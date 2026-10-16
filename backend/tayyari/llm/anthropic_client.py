"""The real `ClaudeClient`: structured answers and streamed chat turns over the Anthropic SDK.

One `AsyncAnthropic` per process (created lazily, closed by `aclose`). Every request is built from the model's
`ModelSpec` (no `effort` for Haiku, no thinking knobs for models that reject them), every API request writes
one `LlmCallRecord` through the usage sink, and every failure is a typed `ClaudeError`.

    structured()   json_schema output through ``output_config.format`` (the strict ``emit`` tool is the learned,
                   per-model fallback), validated with `schema_check`. A content failure (unusable JSON or a
                   ``max_tokens`` stop) gets one more attempt: a repair note, one effort step lower and
                   more tokens. Transport errors are not retried here: the SDK already retries twice.
    stream_chat()  streamed turn with the web search and web fetch tools always declared (so the cache prefix
                   never changes), ``tool_choice`` ``none`` to withhold them, ``pause_turn`` continuation and
                   one `TurnEnd` at the end.
    search_web()   one turn with only the basic web search tool (domain-restricted), read back query by query from
                   the response blocks, with ``pause_turn`` continuation.

Prompt caching: `TextBlock.cache` becomes an explicit ephemeral breakpoint; ``cache_tail`` on a chat turn adds the
API's top-level (automatic) breakpoint, which follows the end of the conversation. Request bodies are
deterministic (no timestamps, no ids), because the cache is a prefix match.

Usage rows: one per request (a retry, a rejected-and-resent request and each ``pause_turn`` continuation each
get their own). Status is ``ok`` or ``error`` (``error_kind`` says why, and a refusal or truncation keeps its
tokens), or ``cancelled`` when the caller went away mid-stream (input tokens as of ``message_start``, output
estimated as characters / 4). A failing usage sink is logged and never breaks the call. A request the SDK
retried internally is one row (the retried attempts that failed carried no tokens).
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final

import anthropic
import httpx2

from ..contract.records import CallStatus, LlmCallRecord
from ..core.clock import Clock, SystemClock
from ..core.ids import uuid7
from ..core.limits import (
    CHAT_MAX_PAUSE_CONTINUATIONS,
    CHAT_TOOL_DETAIL_MAX_CHARS,
    SYNTH_REPAIR_REASON_CLIP_CHARS,
    WEB_FETCH_MAX_USES,
    WEB_SEARCH_MAX_USES,
)
from ..core.logging import get_logger
from ..core.settings import Settings
from ..core.text import clip, tidy
from .errors import (
    SERVER_MESSAGE,
    ClaudeConnectionError,
    ClaudeError,
    ClaudeInvalidOutput,
    ClaudeNotConfigured,
    ClaudeRefusal,
    ClaudeTimeout,
    ClaudeTruncated,
    from_anthropic,
)
from .models import ModelSpec, RawTokens, claude_cost_micro, spec_for
from .schema_check import check_schema
from .types import (
    CallContext,
    ChatStreamEvent,
    Effort,
    MessageParam,
    SearchCall,
    SearchHit,
    StructuredResult,
    TextBlock,
    TextDelta,
    ToolMode,
    ToolUse,
    TurnEnd,
    Usage,
    WebSearchRun,
)
from .usage import UsageSink, usage_from_message

if TYPE_CHECKING:
    from .claude import ClaudeClient

log = get_logger(__name__)

EMIT_TOOL: Final = "emit"
"""Name of the strict tool used when a model does not accept ``output_config.format``."""
WEB_FETCH_MAX_CONTENT_TOKENS: Final = 20_000
"""Approximate cap on the text one ``web_fetch`` puts into the context (the API parameter ``max_content_tokens``;
binary content such as PDFs is not capped by it). Without it a single fetched page can add far more input
tokens than the chat estimate allows for, which makes the cost of a turn unbounded."""
WEB_TOOLS: Final[tuple[dict[str, Any], ...]] = (
    {"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES},
    {
        "type": "web_fetch_20260209",
        "name": "web_fetch",
        "max_uses": WEB_FETCH_MAX_USES,
        "max_content_tokens": WEB_FETCH_MAX_CONTENT_TOKENS,
    },
)
"""Declared on every chat request, even when `tool_choice` is ``none``, so the cached prefix is identical."""
WEB_TOOLS_BASIC: Final[tuple[dict[str, Any], ...]] = (
    {"type": "web_search_20250305", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES},
    {
        "type": "web_fetch_20250910",
        "name": "web_fetch",
        "max_uses": WEB_FETCH_MAX_USES,
        "max_content_tokens": WEB_FETCH_MAX_CONTENT_TOKENS,
    },
)
"""The same two tools in the versions without dynamic filtering, for models that do not take ``_20260209``."""


def web_tools_for(spec: ModelSpec) -> tuple[dict[str, Any], ...]:
    """The web tool declarations a model accepts (`ModelSpec.web_tools`)."""
    return WEB_TOOLS if spec.web_tools == "dynamic" else WEB_TOOLS_BASIC


WEB_SEARCH_BASIC_TYPE: Final = "web_search_20250305"
"""`search_web` declares the basic search tool on every model: its results come back as ``web_search_tool_result`` blocks
the caller reads. The dynamic-filtering version may hand results to code execution instead, out of the caller's sight."""


def search_tool(
    max_searches: int,
    allowed_domains: Sequence[str],
    blocked_domains: Sequence[str],
    user_location: Mapping[str, str] | None,
) -> dict[str, Any]:
    """The web search tool declaration of `search_web` (the API takes allowed or blocked domains, never both)."""
    if allowed_domains and blocked_domains:
        raise ValueError("web search takes allowed_domains or blocked_domains, not both")
    tool: dict[str, Any] = {
        "type": WEB_SEARCH_BASIC_TYPE,
        "name": "web_search",
        "max_uses": max(1, max_searches),
    }
    if allowed_domains:
        tool["allowed_domains"] = list(allowed_domains)
    elif blocked_domains:
        tool["blocked_domains"] = list(blocked_domains)
    if user_location:
        tool["user_location"] = {"type": "approximate", **user_location}
    return tool


def searches_of(content: Iterable[Any]) -> list[tuple[str, SearchCall]]:
    """``(tool_use id, SearchCall)`` for every web search in a response's content, in the order the model ran them.

    The query is the ``server_tool_use`` input; the hits are the ``web_search_tool_result`` with the same id (a URL, a
    title and the page age each), or its error code. A search with no result block reads as the error ``no_result``.
    """
    queries: dict[str, str] = {}
    results: dict[str, tuple[tuple[SearchHit, ...], str | None]] = {}
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "server_tool_use" and getattr(block, "name", None) == "web_search":
            raw = getattr(block, "input", None)
            query = raw.get("query") if isinstance(raw, dict) else None
            queries[str(getattr(block, "id", ""))] = tidy(query) if isinstance(query, str) else ""
        elif kind == "web_search_tool_result":
            tool_use_id = str(getattr(block, "tool_use_id", ""))
            body = getattr(block, "content", None)
            if isinstance(body, list):
                hits = tuple(
                    SearchHit(
                        url=str(getattr(item, "url", "") or ""),
                        title=tidy(getattr(item, "title", "") or ""),
                        page_age=getattr(item, "page_age", None) or None,
                    )
                    for item in body
                    if getattr(item, "type", None) == "web_search_result" and getattr(item, "url", None)
                )
                results[tool_use_id] = (hits, None)
            else:
                code = getattr(body, "error_code", None)
                results[tool_use_id] = ((), str(code) if code else "error")
    found: list[tuple[str, SearchCall]] = []
    for tool_use_id, query in queries.items():
        hits, error = results.get(tool_use_id, ((), "no_result"))
        found.append((tool_use_id, SearchCall(query=query, hits=hits, error=error)))
    return found


WEB_TOOL_UI_NAMES: Final[dict[str, str]] = {"web_search": "WebSearch", "web_fetch": "WebFetch"}
"""API tool name -> the name the UI renders (the single mapping; other server tools are not shown)."""

MAX_STRUCTURED_ATTEMPTS: Final = 2
RETRY_SLEEP_RANGE_S: Final = (1.0, 2.0)
RETRY_TOKEN_FACTOR: Final = 1.5
MAX_RETRY_OUTPUT_TOKENS: Final = 64_000
"""Cap of the raised ``max_tokens`` on the second attempt: the smallest output limit of the known models (Haiku 4.5)."""
THINKING_BUDGET_TOKENS: Final = 4096
_MIN_THINKING_BUDGET: Final = 1024
_MAX_EXPLICIT_BREAKPOINTS: Final = 4
_MAX_JSON_PROBES: Final = 20
_CHARS_PER_TOKEN: Final = 4

_EFFORTS: Final[tuple[Effort, ...]] = ("low", "medium", "high", "xhigh", "max")
_FORMAT_REJECTION_NEEDLES: Final = ("output_config", "format", "structured", "json_schema")
_EMIT_INSTRUCTION: Final = (
    f"Respond by calling the `{EMIT_TOOL}` tool exactly once with the complete result. "
    "Do not write any other text."
)
_NO_JSON_ISSUE: Final = "no JSON object in the response"


# ───────────────────────────── small pure helpers ─────────────────────────────


def _marker(ttl: str = "5m") -> dict[str, str]:
    """An ephemeral cache breakpoint. The default 5-minute lifetime is the API default, so it is not spelled out."""
    return {"type": "ephemeral"} if ttl == "5m" else {"type": "ephemeral", "ttl": ttl}


def _system_blocks(system: Sequence[TextBlock], ttl: str = "5m") -> list[dict[str, Any]]:
    """System text blocks in order, ``cache_control`` where `TextBlock.cache` is set (empty blocks are dropped)."""
    return [
        {"type": "text", "text": block.text, **({"cache_control": _marker(ttl)} if block.cache else {})}
        for block in system
        if block.text.strip()
    ]


def _user_message(
    user: Sequence[TextBlock], document: dict[str, Any] | None, extra_texts: Sequence[str]
) -> dict[str, Any]:
    """One user turn: the PDF document block (when given) first, then the text blocks, then any extra notes."""
    content: list[dict[str, Any]] = [] if document is None else [document]
    content.extend(
        {"type": "text", "text": block.text, **({"cache_control": _marker()} if block.cache else {})}
        for block in user
        if block.text.strip()
    )
    content.extend({"type": "text", "text": text} for text in extra_texts)
    if not content:
        raise ValueError("structured() needs at least one non-empty user block or a PDF")
    return {"role": "user", "content": content}


def _pdf_block(pdf: bytes) -> dict[str, Any]:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(pdf).decode("ascii"),
        },
    }


def _count_markers(items: Iterable[Any]) -> int:
    """Explicit ``cache_control`` markers on system blocks or on the content blocks of messages."""
    total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if "cache_control" in item:
            total += 1
        content = item.get("content")
        if isinstance(content, list):
            total += sum(1 for block in content if isinstance(block, dict) and "cache_control" in block)
    return total


def _with_tail_marker(messages: list[dict[str, Any]], marker: dict[str, str]) -> list[dict[str, Any]]:
    """Copy of ``messages`` with ``cache_control`` on the last content block of the last message.

    The explicit form of the tail breakpoint, used when the top-level (automatic) one is rejected. A last
    message that is not plain text or a list of block dicts (SDK objects from a ``pause_turn`` continuation)
    is left alone.
    """
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        if not content.strip():
            return messages
        new_content: list[Any] = [{"type": "text", "text": content, "cache_control": dict(marker)}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        new_content = [*content[:-1], {**content[-1], "cache_control": dict(marker)}]
    else:
        return messages
    return [*messages[:-1], {**last, "content": new_content}]


def _prefix_tokens(blocks: Sequence[TextBlock]) -> int | None:
    """Estimated tokens up to the last block that carries a cache breakpoint; None when none does."""
    last = max((i for i, block in enumerate(blocks) if block.cache), default=-1)
    if last < 0:
        return None
    return sum(len(block.text) for block in blocks[: last + 1]) // _CHARS_PER_TOKEN


def _lower_effort(effort: Effort | None, spec: ModelSpec) -> Effort | None:
    """One step down the effort ladder (``low`` stays ``low``); unset or unsupported effort is unchanged."""
    if effort is None or not spec.supports_effort:
        return effort
    return _EFFORTS[max(_EFFORTS.index(effort) - 1, 0)]


def _more_tokens(max_tokens: int) -> int:
    """``max_tokens`` x 1.5 up to `MAX_RETRY_OUTPUT_TOKENS`, never below the value already used."""
    return max(max_tokens, min(int(max_tokens * RETRY_TOKEN_FACTOR), MAX_RETRY_OUTPUT_TOKENS))


def _repair_note(failure: ClaudeError) -> str:
    """The extra user block of the second attempt (adapted from the legacy synthesis repair note, generic)."""
    if isinstance(failure, ClaudeTruncated):
        reason = "the answer was cut off before it was complete, so keep every field concise"
    elif isinstance(failure, ClaudeInvalidOutput) and failure.issues:
        reason = clip("; ".join(failure.issues), SYNTH_REPAIR_REASON_CLIP_CHARS)
    else:
        reason = "the answer was not usable"
    return (
        f"IMPORTANT: your previous attempt failed ({reason}). Return ONLY the structured output, valid against "
        "the schema: every required field present, correct types, only allowed values, nothing outside the JSON object."
    )


def parse_json_object(text: str) -> dict[str, Any] | None:
    """The JSON object in ``text``: the whole text, or the first complete ``{...}`` span (trailing prose is tolerated)."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        whole = json.loads(stripped)
    except ValueError:
        whole = None
    if isinstance(whole, dict):
        return whole
    decoder = json.JSONDecoder()
    start = 0
    for _ in range(_MAX_JSON_PROBES):
        start = stripped.find("{", start)
        if start == -1:
            return None
        try:
            value, _end = decoder.raw_decode(stripped, start)
        except ValueError:
            start += 1
            continue
        if isinstance(value, dict):
            return value
        start += 1
    return None


def _extract_output(message: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """The structured answer of a finished message: the ``emit`` tool call, else the JSON in the text blocks.

    Raises `ClaudeInvalidOutput` (with the issues) when there is no JSON object or it fails the schema.
    """
    data: dict[str, Any] | None = None
    for block in message.content:
        if block.type == "tool_use" and block.name == EMIT_TOOL and isinstance(block.input, dict):
            data = dict(block.input)
            break
    if data is None:
        text = "".join(block.text for block in message.content if block.type == "text")
        data = parse_json_object(text)
    if data is None:
        raise ClaudeInvalidOutput([_NO_JSON_ISSUE])
    issues = check_schema(data, schema)
    if issues:
        raise ClaudeInvalidOutput(issues)
    return data


def _tool_event(block: Any) -> ToolUse | None:
    """The UI event for a finished ``server_tool_use`` block of a web tool; other blocks and tools give None."""
    if getattr(block, "type", None) != "server_tool_use":
        return None
    name = WEB_TOOL_UI_NAMES.get(str(getattr(block, "name", "")))
    if name is None:
        return None
    raw = getattr(block, "input", None)
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
    detail = tidy(data.get("query")) or tidy(data.get("url"))
    return ToolUse(name=name, detail=detail[:CHAT_TOOL_DETAIL_MAX_CHARS])


def _error_text(exc: BaseException) -> str:
    """Lower-cased provider message of an SDK error (for matching, never shown)."""
    parts: list[str] = [str(getattr(exc, "message", "") or "")]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            parts.append(str(error.get("message", "")))
    return " ".join(parts).lower()


def _mentions(exc: BaseException, needles: Iterable[str]) -> bool:
    text = _error_text(exc)
    return any(needle in text for needle in needles)


def _is_format_rejection(exc: BaseException) -> bool:
    """A 400 that names structured output: the model does not accept ``output_config.format``."""
    return isinstance(exc, anthropic.BadRequestError) and _mentions(exc, _FORMAT_REJECTION_NEEDLES)


def _is_cache_control_rejection(exc: BaseException) -> bool:
    return isinstance(exc, anthropic.BadRequestError) and _mentions(exc, ("cache_control",))


def _is_operator_problem(error: ClaudeError) -> bool:
    """A failure that only the operator can fix (key, billing, permission, model access)."""
    return error.kind in ("auth", "not_configured") or (error.kind == "bad_request" and error.status == 404)


def _map_error(exc: Exception) -> ClaudeError:
    """`from_anthropic`, plus the raw ``httpx2`` errors the SDK does not wrap once a stream has started.

    The public message of an operator-state failure is one generic sentence (see `llm.errors`), so the real
    reason is logged here, where the provider's answer is still at hand.
    """
    if isinstance(exc, httpx2.TimeoutException):
        return ClaudeTimeout(detail=type(exc).__name__)
    if isinstance(exc, httpx2.RequestError):
        return ClaudeConnectionError(detail=type(exc).__name__)
    mapped = from_anthropic(exc)
    if _is_operator_problem(mapped):
        log.error(
            "claude_unavailable",
            kind=mapped.kind,
            status=mapped.status,
            detail=mapped.detail,
            anthropic_request_id=mapped.request_id,
        )
    return mapped


def _int_attr(obj: Any, name: str) -> int:
    value = getattr(obj, name, None)
    return max(int(value), 0) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _record_status_of(failure: ClaudeError | None) -> CallStatus:
    return "ok" if failure is None else "error"


# ───────────────────────────── bookkeeping ─────────────────────────────


@dataclass(slots=True)
class _Flight:
    """What is known about one API request while it is in flight (enough to write its usage row, even on cancel)."""

    model: str
    spec: ModelSpec
    ctx: CallContext
    attempt: int
    started: float
    cache_prefix_tokens: int | None = None
    """Estimated tokens up to the last explicit breakpoint, or None when no breakpoint was sent."""
    request_id: str | None = None
    start_usage: Any = None
    """``message_start``'s usage (input and cache counts are final there)."""
    ttft_ms: int | None = None
    chars: int = 0
    """Characters generated so far (text and tool-input deltas)."""


@dataclass(slots=True)
class _Attempt:
    """The outcome of one structured attempt that did not raise: usable data, or a content failure to retry."""

    usage: Usage
    stop_reason: str
    data: dict[str, Any] | None = None
    failure: ClaudeError | None = None


class _Progress:
    """A character counter that stays monotonic across attempts and shields the call from a failing callback."""

    def __init__(self, callback: Callable[[int], None] | None) -> None:
        self._callback = callback
        self._broken = False
        self.total = 0

    def add(self, chars: int) -> None:
        if chars <= 0:
            return
        self.total += chars
        if self._callback is None or self._broken:
            return
        try:
            self._callback(self.total)
        except Exception:  # progress is cosmetic: it must never fail an analysis
            self._broken = True
            log.warning("progress_callback_failed")


# ───────────────────────────── the client ─────────────────────────────


class AnthropicClaudeClient:
    """`ClaudeClient` over `anthropic.AsyncAnthropic` (see the module docstring)."""

    def __init__(
        self,
        *,
        settings: Settings,
        usage_sink: UsageSink,
        clock: Clock | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._sink = usage_sink
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._http_client = http_client
        self._client: anthropic.AsyncAnthropic | None = None
        self._emit_only: set[str] = set()
        """Model ids that rejected ``output_config.format``: they use the ``emit`` tool from then on."""
        self._auto_cache_ok = True
        self._writes: set[asyncio.Task[None]] = set()

    # ── lifecycle ──

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """The shared SDK client, created on first use. Raises `ClaudeNotConfigured` without an API key."""
        if self._client is None:
            key = self._settings.anthropic_api_key
            if key is None:
                raise ClaudeNotConfigured()
            self._client = anthropic.AsyncAnthropic(
                api_key=key.get_secret_value(),
                max_retries=2,
                timeout=anthropic.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0),
                http_client=self._http_client,
            )
        return self._client

    async def aclose(self) -> None:
        """Finish pending usage writes and close the SDK client (and the HTTP client it owns)."""
        if self._writes:
            await asyncio.gather(*self._writes, return_exceptions=True)
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    # ── structured ──

    async def structured(
        self,
        *,
        model: str,
        system: Sequence[TextBlock],
        user: Sequence[TextBlock],
        schema: dict[str, Any],
        max_tokens: int,
        effort: Effort | None,
        pdf: bytes | None = None,
        on_progress: Callable[[int], None] | None = None,
        ctx: CallContext,
    ) -> StructuredResult:
        """One structured call; see `ClaudeClient.structured`.

        ``on_progress`` receives the running character count, which keeps growing across the second attempt.
        `StructuredResult.usage` is the sum over the attempts (the cost of everything that was paid for).
        """
        client = self._get_client()
        spec = spec_for(model)
        system_blocks = _system_blocks(system)
        document = _pdf_block(pdf) if pdf is not None else None
        progress = _Progress(on_progress)
        cache_prefix_tokens = _prefix_tokens([*system, *user])  # system renders first, then the user turn

        attempt = 1
        attempt_tokens, attempt_effort = max_tokens, effort
        repair: str | None = None
        total: Usage | None = None
        while True:
            outcome = await self._structured_attempt(
                client=client,
                model=model,
                spec=spec,
                system_blocks=system_blocks,
                user=user,
                document=document,
                repair=repair,
                schema=schema,
                max_tokens=attempt_tokens,
                effort=attempt_effort,
                attempt=attempt,
                progress=progress,
                cache_prefix_tokens=cache_prefix_tokens,
                ctx=ctx,
            )
            total = outcome.usage if total is None else total + outcome.usage
            if outcome.data is not None:
                return StructuredResult(
                    data=outcome.data, usage=total, stop_reason=outcome.stop_reason, attempts=attempt
                )
            failure = outcome.failure or ClaudeInvalidOutput()
            if attempt >= MAX_STRUCTURED_ATTEMPTS:
                raise failure
            attempt += 1
            log.warning(
                "structured_retry",
                purpose=ctx.purpose,
                model=model,
                reason=failure.kind,
                attempt=attempt,
            )
            await self._clock.sleep(random.uniform(*RETRY_SLEEP_RANGE_S))
            repair = _repair_note(failure)
            attempt_effort = _lower_effort(attempt_effort, spec)
            attempt_tokens = _more_tokens(attempt_tokens)

    def _thinking_param(self, spec: ModelSpec, purpose: str, max_tokens: int) -> dict[str, Any] | None:
        """The ``thinking`` parameter for one request, or None to send none.

        Budget-style models (Haiku 4.5) think only as readers and only when ``reader_thinking`` is on; the budget
        must stay below ``max_tokens``. Adaptive models think by default: those that can switch it off (Haiku 5.5)
        get ``{"type": "disabled"}`` unless the purpose wants thinking (synthesis and chat, per settings); those that
        cannot (Sonnet 5.5, where "disabled" is a 400) get no parameter.
        """
        s = self._settings
        if spec.thinking == "budget":
            if purpose == "reader" and s.reader_thinking:
                budget = min(THINKING_BUDGET_TOKENS, max_tokens - _MIN_THINKING_BUDGET)
                if budget >= _MIN_THINKING_BUDGET:
                    return {"type": "enabled", "budget_tokens": budget}
            return None
        if spec.thinking == "adaptive" and spec.thinking_can_disable:
            wants = {"reader": s.reader_thinking, "synth": s.synth_thinking, "chat": s.chat_thinking}
            if not wants.get(purpose, False):
                return {"type": "disabled"}
        return None

    def _structured_params(
        self,
        *,
        model: str,
        spec: ModelSpec,
        system_blocks: list[dict[str, Any]],
        user_message: dict[str, Any],
        schema: dict[str, Any],
        max_tokens: int,
        effort: Effort | None,
        emit: bool,
        purpose: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": [user_message]}
        if system_blocks:
            params["system"] = system_blocks
        output_config: dict[str, Any] = {}
        if not emit:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if effort is not None and spec.supports_effort:
            output_config["effort"] = effort
        if output_config:
            params["output_config"] = output_config
        thinking = self._thinking_param(spec, purpose, max_tokens)
        if thinking is not None:
            params["thinking"] = thinking
        if emit:
            # tool_choice stays auto: forcing a tool is a 400 on the newest models. The instruction steers instead.
            params["tools"] = [
                {
                    "name": EMIT_TOOL,
                    "description": "Return the structured result.",
                    "strict": True,
                    "input_schema": schema,
                }
            ]
        return params

    async def _structured_attempt(
        self,
        *,
        client: anthropic.AsyncAnthropic,
        model: str,
        spec: ModelSpec,
        system_blocks: list[dict[str, Any]],
        user: Sequence[TextBlock],
        document: dict[str, Any] | None,
        repair: str | None,
        schema: dict[str, Any],
        max_tokens: int,
        effort: Effort | None,
        attempt: int,
        progress: _Progress,
        cache_prefix_tokens: int | None,
        ctx: CallContext,
    ) -> _Attempt:
        """One API round (resent once in ``emit`` mode when the model rejects structured output)."""
        emit = spec.id in self._emit_only or not spec.structured_output
        probing = False
        while True:
            extras = [text for text in (repair, _EMIT_INSTRUCTION if emit else None) if text]
            params = self._structured_params(
                model=model,
                spec=spec,
                system_blocks=system_blocks,
                user_message=_user_message(user, document, extras),
                schema=schema,
                max_tokens=max_tokens,
                effort=effort,
                emit=emit,
                purpose=ctx.purpose,
            )
            flight = _Flight(
                model=model,
                spec=spec,
                ctx=ctx,
                attempt=attempt,
                started=self._clock.monotonic(),
                cache_prefix_tokens=cache_prefix_tokens,
            )
            try:
                message = await self._run_structured(client, params, flight, progress)
            except asyncio.CancelledError:
                await self._record_unfinished(flight, "cancelled", None)
                raise
            except Exception as exc:
                mapped = _map_error(exc)
                await self._record_unfinished(flight, "error", mapped)
                if not emit and _is_format_rejection(exc):
                    log.warning("structured_output_rejected", model=model, detail=mapped.detail)
                    emit, probing = True, True
                    continue
                if mapped is exc:
                    raise
                raise mapped from exc
            break

        if probing:
            # The same request was accepted once the format was dropped: remember it for the process.
            self._emit_only.add(spec.id)
            log.warning("structured_output_unsupported_using_emit_tool", model=model)

        usage = self._usage_of(message, flight)
        stop_reason = str(message.stop_reason or "end_turn")
        failure: ClaudeError | None = None
        data: dict[str, Any] | None = None
        if stop_reason == "refusal":
            category = getattr(getattr(message, "stop_details", None), "category", None)
            refusal = ClaudeRefusal(
                category if isinstance(category, str) else None, request_id=usage.request_id
            )
            await self._record_finished(flight, usage, stop_reason, "error", refusal.kind)
            raise refusal
        if stop_reason == "max_tokens":
            failure = ClaudeTruncated(max_tokens, request_id=usage.request_id)
        else:
            try:
                data = _extract_output(message, schema)
            except ClaudeInvalidOutput as invalid:
                invalid.request_id = usage.request_id
                failure = invalid
        await self._record_finished(
            flight, usage, stop_reason, _record_status_of(failure), failure.kind if failure else None
        )
        self._check_cache(flight, usage)
        return _Attempt(usage=usage, stop_reason=stop_reason, data=data, failure=failure)

    async def _run_structured(
        self,
        client: anthropic.AsyncAnthropic,
        params: dict[str, Any],
        flight: _Flight,
        progress: _Progress,
    ) -> Any:
        async with client.messages.stream(**params) as stream:
            flight.request_id = stream.request_id
            async for event in stream:
                ev: Any = event
                delta = self._note(flight, ev)
                if delta:
                    progress.add(delta)
            return await stream.get_final_message()

    # ── chat ──

    async def stream_chat(
        self,
        *,
        model: str,
        system: Sequence[TextBlock],
        messages: Sequence[MessageParam],
        tools: ToolMode,
        max_tokens: int,
        effort: Effort | None,
        cache_tail: bool = True,
        ctx: CallContext,
    ) -> AsyncIterator[ChatStreamEvent]:
        """One chat turn; see `ClaudeClient.stream_chat`.

        The generator ends with exactly one `TurnEnd` whose usage is summed over the turn's requests. An
        exhausted ``pause_turn`` budget ends the turn with ``stop_reason="pause_turn"`` when some text
        arrived, and raises a retryable ``ClaudeError`` when none did.
        """
        client = self._get_client()
        spec = spec_for(model)
        ttl = self._settings.chat_cache_ttl
        system_blocks = _system_blocks(system, ttl)
        cache_prefix_tokens = _prefix_tokens(system)

        convo: list[dict[str, Any]] = list(messages)
        text = ""
        separator_due = False
        total: Usage | None = None
        pauses = 0
        while True:
            params = self._chat_params(
                model=model,
                spec=spec,
                system_blocks=system_blocks,
                convo=convo,
                tools=tools,
                max_tokens=max_tokens,
                effort=effort,
                cache_tail=cache_tail,
                ttl=ttl,
            )
            flight = _Flight(
                model=model,
                spec=spec,
                ctx=ctx,
                attempt=1,
                started=self._clock.monotonic(),
                cache_prefix_tokens=cache_prefix_tokens,
            )
            try:
                async with client.messages.stream(**params) as stream:
                    flight.request_id = stream.request_id
                    async for event in stream:
                        ev: Any = event
                        self._note(flight, ev)
                        kind = ev.type
                        if kind == "content_block_start":
                            # A text block after earlier text (a tool call came between): set it apart, but
                            # only once it actually has text, so an empty block adds nothing.
                            if ev.content_block.type == "text" and text and not text.endswith("\n"):
                                separator_due = True
                        elif kind == "text":
                            if separator_due:
                                separator_due = False
                                text += "\n\n"
                                yield TextDelta("\n\n")
                            text += ev.text
                            yield TextDelta(ev.text)
                        elif kind == "content_block_stop":
                            tool_event = _tool_event(ev.content_block)
                            if tool_event is not None:
                                yield tool_event
                    final = await stream.get_final_message()
            except (asyncio.CancelledError, GeneratorExit):
                await self._record_unfinished(flight, "cancelled", None)
                raise
            except Exception as exc:
                mapped = _map_error(exc)
                await self._record_unfinished(flight, "error", mapped)
                if "cache_control" in params and self._auto_cache_ok and _is_cache_control_rejection(exc):
                    self._auto_cache_ok = False
                    log.warning("automatic_cache_control_rejected", model=model)
                    continue
                if mapped is exc:
                    raise
                raise mapped from exc

            usage = self._usage_of(final, flight)
            stop_reason = str(final.stop_reason or "end_turn")
            if stop_reason == "refusal":
                category = getattr(getattr(final, "stop_details", None), "category", None)
                refusal = ClaudeRefusal(
                    category if isinstance(category, str) else None, request_id=usage.request_id
                )
                await self._record_finished(flight, usage, stop_reason, "error", refusal.kind)
                raise refusal
            await self._record_finished(flight, usage, stop_reason, "ok", None)
            self._check_cache(flight, usage)
            total = usage if total is None else total + usage

            if stop_reason == "pause_turn":
                if pauses >= CHAT_MAX_PAUSE_CONTINUATIONS:
                    log.warning("chat_pause_turn_budget_exhausted", model=model, continuations=pauses)
                    if not text:
                        raise ClaudeError(
                            "server",
                            retryable=True,
                            user_message=SERVER_MESSAGE,
                            detail="too many paused turns",
                            request_id=usage.request_id,
                        )
                    yield TurnEnd(text=text, usage=total, stop_reason="pause_turn")
                    return
                pauses += 1
                # Re-send with the paused assistant turn appended; the API resumes where it stopped.
                convo = [*convo, {"role": "assistant", "content": final.content}]
                continue
            yield TurnEnd(text=text, usage=total, stop_reason=stop_reason)
            return

    # ── web search ──

    async def search_web(
        self,
        *,
        model: str,
        system: Sequence[TextBlock],
        user: Sequence[TextBlock],
        max_searches: int,
        allowed_domains: Sequence[str] = (),
        blocked_domains: Sequence[str] = (),
        user_location: Mapping[str, str] | None = None,
        max_tokens: int,
        effort: Effort | None,
        ctx: CallContext,
    ) -> WebSearchRun:
        """One search-only turn; see `ClaudeClient.search_web`. `WebSearchRun.usage` sums the turn's requests."""
        client = self._get_client()
        spec = spec_for(model)
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "tools": [search_tool(max_searches, allowed_domains, blocked_domains, user_location)],
        }
        system_blocks = _system_blocks(system)
        if system_blocks:
            params["system"] = system_blocks
        if effort is not None and spec.supports_effort:
            params["output_config"] = {"effort": effort}
        thinking = self._thinking_param(spec, ctx.purpose, max_tokens)
        if thinking is not None:
            params["thinking"] = thinking
        convo: list[dict[str, Any]] = [_user_message(user, None, ())]
        seen: dict[str, SearchCall] = {}
        texts: list[str] = []
        total: Usage | None = None
        pauses = 0
        while True:
            flight = _Flight(model=model, spec=spec, ctx=ctx, attempt=1, started=self._clock.monotonic())
            try:
                final = await self._run_structured(
                    client, {**params, "messages": convo}, flight, _Progress(None)
                )
            except asyncio.CancelledError:
                await self._record_unfinished(flight, "cancelled", None)
                raise
            except Exception as exc:
                mapped = _map_error(exc)
                await self._record_unfinished(flight, "error", mapped)
                if mapped is exc:
                    raise
                raise mapped from exc
            usage = self._usage_of(final, flight)
            stop_reason = str(final.stop_reason or "end_turn")
            if stop_reason == "refusal":
                category = getattr(getattr(final, "stop_details", None), "category", None)
                refusal = ClaudeRefusal(
                    category if isinstance(category, str) else None, request_id=usage.request_id
                )
                await self._record_finished(flight, usage, stop_reason, "error", refusal.kind)
                raise refusal
            await self._record_finished(flight, usage, stop_reason, "ok", None)
            total = usage if total is None else total + usage
            for tool_use_id, call in searches_of(final.content):
                seen.setdefault(tool_use_id, call)
            texts.extend(block.text for block in final.content if getattr(block, "type", None) == "text")
            if stop_reason == "pause_turn" and pauses < CHAT_MAX_PAUSE_CONTINUATIONS:
                pauses += 1
                convo = [*convo, {"role": "assistant", "content": final.content}]
                continue
            return WebSearchRun(
                searches=tuple(seen.values()),
                usage=total,
                stop_reason=stop_reason,
                text="".join(texts).strip(),
            )

    def _chat_params(
        self,
        *,
        model: str,
        spec: ModelSpec,
        system_blocks: list[dict[str, Any]],
        convo: list[dict[str, Any]],
        tools: ToolMode,
        max_tokens: int,
        effort: Effort | None,
        cache_tail: bool,
        ttl: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": convo,
            "tools": [dict(tool) for tool in web_tools_for(spec)],
        }
        if system_blocks:
            params["system"] = system_blocks
        if tools == "none":
            # Tools stay declared, so the cached prefix is byte-identical to a turn that may search.
            params["tool_choice"] = {"type": "none"}
        if effort is not None and spec.supports_effort:
            params["output_config"] = {"effort": effort}
        thinking = self._thinking_param(spec, "chat", max_tokens)
        if thinking is not None:
            params["thinking"] = thinking
        if cache_tail and _count_markers([*system_blocks, *convo]) < _MAX_EXPLICIT_BREAKPOINTS:
            if self._auto_cache_ok:
                params["cache_control"] = _marker(ttl)
            else:
                params["messages"] = _with_tail_marker(convo, _marker(ttl))
        return params

    # ── shared request plumbing ──

    def _note(self, flight: _Flight, event: Any) -> int:
        """Update the flight from one stream event; returns the characters the event generated (0 for most)."""
        kind = event.type
        if kind == "message_start":
            flight.start_usage = event.message.usage
            return 0
        chars = 0
        if kind == "text":
            chars = len(event.text)
        elif kind == "input_json":
            chars = len(event.partial_json)
        elif kind != "thinking":
            return 0
        if flight.ttft_ms is None:
            flight.ttft_ms = self._elapsed_ms(flight)
        flight.chars += chars
        return chars

    def _elapsed_ms(self, flight: _Flight) -> int:
        return max(int((self._clock.monotonic() - flight.started) * 1000), 0)

    def _usage_of(self, message: Any, flight: _Flight) -> Usage:
        usage = usage_from_message(message, flight.model)
        request_id = flight.request_id or usage.request_id
        return replace(usage, request_id=request_id) if request_id != usage.request_id else usage

    def _partial_usage(self, flight: _Flight) -> Usage:
        """Usage of a request that did not finish: input as of ``message_start``, output estimated from characters."""
        start = flight.start_usage
        if start is None:
            return Usage(model=flight.model, request_id=flight.request_id)
        tokens = RawTokens(
            input_tokens=_int_attr(start, "input_tokens"),
            output_tokens=flight.chars // _CHARS_PER_TOKEN,
            cache_write_tokens=_int_attr(start, "cache_creation_input_tokens"),
            cache_read_tokens=_int_attr(start, "cache_read_input_tokens"),
        )
        return Usage(
            model=flight.model,
            input_tokens=tokens.input_tokens,
            output_tokens=tokens.output_tokens,
            cache_write_tokens=tokens.cache_write_tokens,
            cache_read_tokens=tokens.cache_read_tokens,
            request_id=flight.request_id,
            cost_micro_usd=claude_cost_micro(flight.spec, tokens),
        )

    def _check_cache(self, flight: _Flight, usage: Usage) -> None:
        """Log ``cache_unexpected_miss`` when a breakpoint was sent over a cacheable prefix that nothing wrote or read."""
        prefix = flight.cache_prefix_tokens
        if prefix is None or usage.cache_write_tokens + usage.cache_read_tokens > 0:
            return
        if prefix <= flight.spec.min_cache_tokens:
            return
        log.warning(
            "cache_unexpected_miss",
            purpose=flight.ctx.purpose,
            model=flight.model,
            est_prefix_tokens=prefix,
            min_cache_tokens=flight.spec.min_cache_tokens,
            input_tokens=usage.input_tokens,
            anthropic_request_id=usage.request_id,
        )

    # ── usage rows ──

    async def _record_finished(
        self, flight: _Flight, usage: Usage, stop_reason: str, status: CallStatus, error_kind: str | None
    ) -> None:
        await self._write(
            self._row(flight, usage, status=status, stop_reason=stop_reason, error_kind=error_kind)
        )

    async def _record_unfinished(
        self, flight: _Flight, status: CallStatus, error: ClaudeError | None
    ) -> None:
        """A request that failed or was cancelled before a final message (tokens from what was seen)."""
        usage = self._partial_usage(flight)
        if error is not None and error.request_id and not usage.request_id:
            usage = replace(usage, request_id=error.request_id)
        await self._write(
            self._row(
                flight, usage, status=status, stop_reason=None, error_kind=error.kind if error else None
            )
        )

    def _row(
        self,
        flight: _Flight,
        usage: Usage,
        *,
        status: CallStatus,
        stop_reason: str | None,
        error_kind: str | None,
    ) -> LlmCallRecord:
        ctx = flight.ctx
        return LlmCallRecord(
            id=uuid7(),
            created_at=self._clock.now(),
            purpose=ctx.purpose,
            model=flight.model,
            status=status,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            web_searches=usage.web_searches,
            cost_micro_usd=usage.cost_micro_usd,
            latency_ms=self._elapsed_ms(flight),
            ttft_ms=flight.ttft_ms,
            attempt=flight.attempt,
            request_id=usage.request_id or flight.request_id,
            stop_reason=stop_reason,
            error_kind=error_kind,
            profile_id=ctx.profile_id,
            session_id=ctx.session_id,
            analysis_id=ctx.analysis_id,
            ip_hash=ctx.ip_hash,
        )

    async def _write(self, rec: LlmCallRecord) -> None:
        """Write a row in a task of its own, shielded: a cancellation of the caller cannot lose the ledger entry."""
        task = asyncio.ensure_future(self._deliver(rec))
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)
        await asyncio.shield(task)

    async def _deliver(self, rec: LlmCallRecord) -> None:
        log.info(
            "llm_call",
            purpose=rec.purpose,
            model=rec.model,
            status=rec.status,
            attempt=rec.attempt,
            stop_reason=rec.stop_reason,
            error_kind=rec.error_kind,
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            cache_write_tokens=rec.cache_write_tokens,
            cache_read_tokens=rec.cache_read_tokens,
            web_searches=rec.web_searches,
            cost_micro_usd=rec.cost_micro_usd,
            latency_ms=rec.latency_ms,
            ttft_ms=rec.ttft_ms,
            anthropic_request_id=rec.request_id,
        )
        try:
            await self._sink.record(rec)
        except Exception as exc:  # the ledger must never break a call
            log.error(
                "llm_usage_record_failed",
                error=type(exc).__name__,
                purpose=rec.purpose,
                model=rec.model,
                cost_micro_usd=rec.cost_micro_usd,
            )


if TYPE_CHECKING:

    def _implements_protocol(client: AnthropicClaudeClient) -> ClaudeClient:
        return client


__all__ = [
    "EMIT_TOOL",
    "WEB_FETCH_MAX_CONTENT_TOKENS",
    "WEB_SEARCH_BASIC_TYPE",
    "WEB_TOOLS",
    "WEB_TOOLS_BASIC",
    "WEB_TOOL_UI_NAMES",
    "AnthropicClaudeClient",
    "parse_json_object",
    "search_tool",
    "searches_of",
    "web_tools_for",
]
