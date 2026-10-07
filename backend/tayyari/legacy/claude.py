"""
Thin async wrappers around the official Anthropic Python SDK (the port of server/claude.ts).

Every AI call in Tayyari goes through here. The client authenticates with ANTHROPIC_API_KEY.

    run_structured()  – one-shot call that must return JSON matching a JSON Schema
                        (used by the Haiku readers and the Sonnet synthesizer). Structured outputs
                        (`output_config.format`) are the primary path; models that reject them fall
                        back to a strict `emit` tool. Forced `tool_choice` is deliberately not used:
                        it returns 400 on Sonnet 5.5.
    stream_chat()     – streaming chat turn with the server-side web_search / web_fetch tools
                        (used by the career chat). Conversation history is kept by the caller.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
from collections.abc import Awaitable
from typing import Any, Callable

import anthropic

from . import config

AUTH_MESSAGE = "Claude isn't configured on the server. Set ANTHROPIC_API_KEY (a valid Anthropic API key) and try again."

WEB_TOOLS: list[dict[str, Any]] = [
    {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 5},
    {'type': 'web_fetch_20260209', 'name': 'web_fetch', 'max_uses': 5},
]
EMIT_TOOL = 'emit'
MAX_CONTINUATIONS = 6

Callback = Callable[..., Awaitable[None] | None]


class ClaudeError(Exception):
    """A failed AI call, with a human-readable message (pipeline.humanize_error turns it into UI copy)."""

    def __init__(self, message: str, detail: Any = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    """The shared async client; raises a ClaudeError that names ANTHROPIC_API_KEY when no key is configured."""
    global _client
    key = config.API_KEY or os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        raise ClaudeError(AUTH_MESSAGE)
    if _client is None or _client.api_key != key:
        _client = anthropic.AsyncAnthropic(api_key=key, max_retries=2)
    return _client


# ───────────────────────── errors ─────────────────────────


def _message_of(exc: BaseException) -> str:
    body = getattr(exc, 'body', None)
    if isinstance(body, dict):
        err = body.get('error')
        if isinstance(err, dict) and err.get('message'):
            return str(err['message'])
    return str(getattr(exc, 'message', None) or exc) or type(exc).__name__


def wrap_error(exc: BaseException) -> ClaudeError:
    """Map SDK exceptions to ClaudeErrors whose text the pipeline's humanizer recognises (401, 429, 529…)."""
    if isinstance(exc, ClaudeError):
        return exc
    msg = _message_of(exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return ClaudeError(f'Claude rejected the API key (401 unauthorized): {msg}', exc)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ClaudeError(f'The API key is not permitted to do this (403): {msg}', exc)
    if isinstance(exc, anthropic.RateLimitError):
        return ClaudeError(f'Claude is rate limited (429): {msg}', exc)
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status == 529:
            return ClaudeError(f'Claude is overloaded (529): {msg}', exc)
        return ClaudeError(f'Claude API error {status}: {msg}', exc)
    if isinstance(exc, anthropic.APITimeoutError):
        return ClaudeError(f'The request to Claude timed out: {msg}', exc)
    if isinstance(exc, anthropic.APIConnectionError):
        return ClaudeError(f'Could not connect to Claude (connection error): {msg}', exc)
    return ClaudeError(msg, exc)


def _mentions(exc: BaseException, *needles: str) -> bool:
    text = _message_of(exc).lower()
    return any(n in text for n in needles)


# ───────────────────────── schema check ─────────────────────────

_TYPES: dict[str, tuple[type, ...]] = {
    'object': (dict,),
    'array': (list,),
    'string': (str,),
    'boolean': (bool,),
    'integer': (int,),
    'number': (int, float),
}


def check_schema(data: Any, schema: dict[str, Any], path: str = '$', issues: list[str] | None = None) -> list[str]:
    """
    Lightweight JSON Schema validation (types, required keys, enums, nested objects/arrays) — what the
    SDK's structured output already guarantees, re-checked so callers get a trusted object. Null values
    of optional properties are tolerated. Returns a list of issues (empty = valid), capped at 10.
    """
    if issues is None:
        issues = []
    if len(issues) >= 10:
        return issues
    kind = schema.get('type')
    expected = _TYPES.get(str(kind))
    if expected is not None:
        ok = isinstance(data, expected) and not (kind in ('integer', 'number') and isinstance(data, bool))
        if not ok:
            issues.append(f'{path}: expected {kind}, got {type(data).__name__}')
            return issues
    if 'enum' in schema and data not in schema['enum']:
        issues.append(f'{path}: {data!r} not in {schema["enum"]!r}')
    if kind == 'object':
        required = set(schema.get('required') or [])
        for key in required:
            if key not in data or data[key] is None:
                issues.append(f'{path}.{key}: missing required field')
        for key, sub in (schema.get('properties') or {}).items():
            if key in data and (data[key] is not None or key in required):
                check_schema(data[key], sub, f'{path}.{key}', issues)
    elif kind == 'array' and 'items' in schema:
        for i, item in enumerate(data):
            check_schema(item, schema['items'], f'{path}[{i}]', issues)
    return issues


# ───────────────────────── structured calls ─────────────────────────


async def _stream_message(params: dict[str, Any], on_progress: Callable[[int], None] | None) -> anthropic.types.Message:
    """Run one streamed request, feeding the running count of generated characters to `on_progress`."""
    generated = 0
    async with client().messages.stream(**params) as stream:
        async for event in stream:
            if on_progress is None:
                continue
            if event.type == 'text':
                generated += len(event.text)
            elif event.type == 'input_json':
                generated += len(event.partial_json)
            else:
                continue
            on_progress(generated)
        return await stream.get_final_message()


def _parse_json_object(text: str) -> dict[str, Any]:
    """Parse the whole text as JSON, or pull the first {...} object out of free text (fallback only)."""
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        if start == -1 or end <= start:
            raise ClaudeError('no JSON object in response', text[:400]) from None
        try:
            value = json.loads(text[start : end + 1])
        except ValueError as err:
            raise ClaudeError(f'no JSON object in response ({err})', text[:400]) from None
    if not isinstance(value, dict):
        raise ClaudeError('no JSON object in response', text[:400])
    return value


def _extract(message: anthropic.types.Message) -> dict[str, Any]:
    if message.stop_reason == 'refusal':
        raise ClaudeError('Claude declined this request (refusal)', message.stop_reason)
    if message.stop_reason == 'max_tokens':
        raise ClaudeError('structured output failed validation: the answer was cut off at max_tokens', message.stop_reason)
    for block in message.content:
        if block.type == 'tool_use' and block.name == EMIT_TOOL and isinstance(block.input, dict):
            return dict(block.input)
    text = ''.join(block.text for block in message.content if block.type == 'text')
    return _parse_json_object(text)


def _user_content(prompt: str, pdf: bytes | None) -> str | list[dict[str, Any]]:
    """The user turn: plain text, or the PDF as a document block ahead of the text when one is attached."""
    if pdf is None:
        return prompt
    document = {'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': base64.b64encode(pdf).decode('ascii')}}
    return [document, {'type': 'text', 'text': prompt}]


async def run_structured(
    model: str,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    max_tokens: int = 8000,
    on_progress: Callable[[int], None] | None = None,
    effort: str | None = None,
    thinking: dict[str, Any] | None = None,
    pdf: bytes | None = None,
) -> dict[str, Any]:
    """
    One-shot structured call: the API constrains the answer to `schema` (structured outputs), and the
    result is re-validated so callers get a trusted dict. `effort` (low/medium/high) is sent as
    `output_config.effort` and `thinking` as the request's thinking config; each is dropped
    automatically if the model rejects it. `pdf` attaches a document (a scanned resume) for the model
    to read alongside the prompt.
    Raises ClaudeError with a readable message; asyncio.CancelledError propagates.
    """
    params: dict[str, Any] = {
        'model': model,
        'max_tokens': max_tokens,
        'system': system,
        'messages': [{'role': 'user', 'content': _user_content(prompt, pdf)}],
        'output_config': {'format': {'type': 'json_schema', 'schema': schema}, **({'effort': effort} if effort else {})},
        **({'thinking': thinking} if thinking else {}),
    }

    # Each 400 that names an optional feature drops that one feature and retries, so a model that lacks
    # several of them still degrades to a plain call; any other error is final.
    while True:
        try:
            message = await _stream_message(params, on_progress)
            break
        except asyncio.CancelledError:
            raise
        except anthropic.BadRequestError as exc:
            output_config: dict[str, Any] = params.get('output_config') or {}
            if 'effort' in output_config and _mentions(exc, 'effort'):
                params['output_config'] = {'format': output_config['format']}
            elif 'thinking' in params and _mentions(exc, 'thinking'):
                params.pop('thinking')
            elif 'format' in output_config and _mentions(exc, 'output_config', 'format', 'structured'):
                # Model without structured outputs: ask for a strict tool call instead (tool_choice stays auto).
                tool = {'name': EMIT_TOOL, 'description': 'Return the structured result.', 'strict': True, 'input_schema': schema}
                steered = _user_content(f'{prompt}\n\nRespond by calling the `{EMIT_TOOL}` tool exactly once with the complete result.', pdf)
                params = {k: v for k, v in params.items() if k != 'output_config'} | {'messages': [{'role': 'user', 'content': steered}], 'tools': [tool]}
            else:
                raise wrap_error(exc) from exc
        except Exception as exc:  # SDK errors → readable ClaudeError
            raise wrap_error(exc) from exc

    data = _extract(message)
    issues = check_schema(data, schema)
    if issues:
        raise ClaudeError(f'structured output failed validation: {"; ".join(issues)[:400]}', data)
    return data


# ───────────────────────── chat ─────────────────────────


async def _call(cb: Callback | None, *args: Any) -> None:
    if cb is None:
        return
    result = cb(*args)
    if inspect.isawaitable(result):
        await result


def _tool_detail(block: Any) -> str:
    inp = block.input if isinstance(getattr(block, 'input', None), dict) else {}
    return str(inp.get('query') or inp.get('url') or inp.get('prompt') or '')


async def _chat_turn(params: dict[str, Any], text: str, on_text: Callback, on_tool: Callback | None) -> tuple[anthropic.types.Message, str]:
    """One streamed assistant turn; returns the final message and the accumulated visible text."""
    async with client().messages.stream(**params) as stream:
        async for event in stream:
            if event.type == 'text':
                text += event.text
                await _call(on_text, event.text)
            elif event.type == 'content_block_start':
                if event.content_block.type == 'text' and text and not text.endswith('\n'):
                    # a new text block after a tool call: separate it visually
                    text += '\n\n'
                    await _call(on_text, '\n\n')
            elif event.type == 'content_block_stop':
                block = stream.current_message_snapshot.content[event.index]
                if block.type in ('server_tool_use', 'tool_use'):
                    await _call(on_tool, block.name, _tool_detail(block))
        return await stream.get_final_message(), text


async def stream_chat(
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    *,
    on_text: Callback,
    on_tool: Callback | None = None,
    max_tokens: int = 8000,
    effort: str | None = 'medium',
) -> str:
    """
    Streaming chat turn over `messages` (the thread so far, ending with the user's message; not mutated).
    Text deltas go to `on_text`, server web-tool calls to `on_tool(name, detail)`; both may be sync or
    async. Server tools can pause a turn (`pause_turn`); it is resumed up to MAX_CONTINUATIONS times.
    Returns the full assistant text. Raises ClaudeError; asyncio.CancelledError propagates.
    """
    convo = list(messages)
    text = ''
    continuations = 0
    while True:
        params: dict[str, Any] = {'model': model, 'max_tokens': max_tokens, 'system': system, 'messages': convo, 'tools': WEB_TOOLS}
        if effort:
            params['output_config'] = {'effort': effort}
        try:
            message, text = await _chat_turn(params, text, on_text, on_tool)
        except asyncio.CancelledError:
            raise
        except anthropic.BadRequestError as exc:
            if effort and _mentions(exc, 'effort'):
                effort = None
                continue
            raise wrap_error(exc) from exc
        except Exception as exc:
            raise wrap_error(exc) from exc
        if message.stop_reason != 'pause_turn':
            return text
        continuations += 1
        if continuations > MAX_CONTINUATIONS:
            raise ClaudeError('Claude session ended without a result (too many paused turns)')
        convo.append({'role': 'assistant', 'content': message.content})
