"""`CoachChatService`: one streamed coach turn, grounded in the stored profile.

A turn goes through these steps; only a missing profile, a Claude failure or the spend ceiling end it with
an error event, everything else degrades:

 1. Load the profile and its context. The bundled sample persona is seeded into the repository on its first
    chat, so the session's foreign key holds. An unknown id is the legacy "Profile not found" error.
 2. Open the session. A known ``sessionId`` of this profile continues it (the last 40 messages are replayed;
    a stored answer that used web tools carries a short bracketed note of them, so the model still knows
    where its earlier claims came from); an unknown one, or one that belongs to another profile, starts a
    new session and the user text gets the legacy ``RESET_NOTE``. A session store that fails only costs the
    history, never the answer.
 3. Decide whether the model may use the web tools: a link in the message always allows them, otherwise the
    decision gate answers, and any failure of it allows them (tools are only ever withheld on a confident "no").
 4. Stream through the Claude client as three system blocks (rules, profile with the cache breakpoint,
    thread origin), mapping text and tool events to `ChatEvent`s.
 5. At the end of the turn, store the user and assistant messages together and emit ``done``. A failure to
    store is logged and does not fail the turn. A cancelled turn (the client went away) stores nothing.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from tayyari.contract.events import (
    ChatEvent,
    ev_chat_done,
    ev_chat_error,
    ev_delta,
    ev_tool,
)
from tayyari.contract.requests import ChatRequestBody, RequestContext
from tayyari.contract.stored import StoredContext
from tayyari.core.clock import Clock, SystemClock
from tayyari.core.errors import AppError
from tayyari.core.ids import is_sample_id, new_session_id
from tayyari.core.limits import CHAT_HISTORY_MESSAGES
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.decisions.service import DecisionContext, DecisionService, GateInput
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError
from tayyari.llm.types import CallContext, MessageParam, TextDelta, ToolMode, ToolUse, TurnEnd, Usage
from tayyari.sample.provider import SampleProvider
from tayyari.storage.ports import ChatMessage, ProfileRepo, SessionRepo
from tayyari.storage.ports import ToolEvent as StoredToolEvent

from .context import ChatPromptBlocks, ChatPromptRenderer
from .tools import ui_tool_name, url_in_message

log = get_logger(__name__)

PROFILE_NOT_FOUND_MESSAGE: Final[str] = "Profile not found — run the analysis again"
"""Exact text of the legacy error event: the web app shows it as is."""

RESET_NOTE: Final[str] = (
    "(Note: the earlier messages of this conversation are no longer available — answer this on its own, "
    "using the profile.)\n\n"
)
"""Prefixed to the user's text when a ``sessionId`` could not be resumed (legacy wording)."""

CUT_OFF_NOTE: Final[str] = "\n\n_(The answer was cut off — ask me to continue.)_"
"""Sent as one last delta when the model stopped at ``max_tokens`` (not stored with the answer)."""

_TOOL_NOTE_PREFIX: Final[str] = "\n\n[Tools used for this answer: "
_TOOL_NOTE_SUFFIX: Final[str] = "]"
_TOOL_NOTE_MAX_ENTRIES: Final[int] = 5
_TOOL_NOTE_MAX_CHARS: Final[int] = 300
"""The whole appended note, separator and brackets included."""
_TOOL_NOTE_DETAIL_CHARS: Final[int] = 80
_URL_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


@dataclass(slots=True)
class _Thread:
    """The session of this turn: its id, the replayed history and whether turns are stored."""

    id: str
    history: list[MessageParam] = field(default_factory=list)
    reset: bool = False
    """The ``sessionId`` of the request could not be resumed."""
    persist: bool = True
    """False when the session store failed: the turn still runs, it just is not saved."""
    origin: dict[str, Any] | None = None
    """The origin stored with a resumed session (used when the request carries none)."""


def _thread_label(origin: Mapping[str, Any] | None) -> str:
    """``"<origin kind>: <title>"`` for the gate; a thread with no origin is a free-form one."""
    kind = str((origin or {}).get("kind") or "free")
    title = str((origin or {}).get("title") or "")
    return f"{kind}: {title}" if title else kind


def _clip(text: str, limit: int) -> str:
    """One line of at most ``limit`` characters (whitespace collapsed, ``...`` marks a cut)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


def _tool_entry(tool: StoredToolEvent) -> str | None:
    """``searched the web for "<query>"`` or ``read <url>``; None for a tool the note does not know."""
    name = ui_tool_name(tool.name)
    if name == "WebSearch":
        query = _clip(tool.detail.replace('"', "'"), _TOOL_NOTE_DETAIL_CHARS)
        return f'searched the web for "{query}"' if query else "searched the web"
    if name == "WebFetch":
        url = _clip(_URL_SCHEME.sub("", tool.detail.strip()), _TOOL_NOTE_DETAIL_CHARS)
        return f"read {url}" if url else "read a web page"
    return None


def _tool_note(tools: Sequence[StoredToolEvent]) -> str:
    """The note appended to a replayed answer that used tools, or ``""`` when there is nothing to say.

    Entries keep their stored order, identical ones count once, at most five are listed and the whole
    note (separator included) stays within 300 characters: trailing entries are dropped to fit. The note
    is a pure function of the stored rows, so the replayed prefix is the same on every turn (cacheable).
    """
    entries: list[str] = []
    for tool in tools:
        entry = _tool_entry(tool)
        if entry is not None and entry not in entries:
            entries.append(entry)
    del entries[_TOOL_NOTE_MAX_ENTRIES:]
    while entries:
        note = _TOOL_NOTE_PREFIX + "; ".join(entries) + _TOOL_NOTE_SUFFIX
        if len(note) <= _TOOL_NOTE_MAX_CHARS:
            return note
        entries.pop()
    return ""


def _replay_history(messages: Sequence[ChatMessage]) -> list[MessageParam]:
    """The stored messages as Messages-API turns: the last 40, starting on a user message.

    A stored answer that used web tools gets `_tool_note` appended here, in the copy sent to Claude; the
    stored row keeps its text. The note is only ever built for stored messages, so the turn being answered
    (not stored yet) never carries one.
    """
    history: list[MessageParam] = []
    for m in messages[-CHAT_HISTORY_MESSAGES:]:
        content = m.content
        if m.role == "assistant" and m.tools:
            content += _tool_note(m.tools)
        history.append({"role": m.role, "content": content})
    while history and history[0]["role"] != "user":
        history.pop(0)  # the Messages API wants the first message from the user
    return history


async def _aclose(stream: AsyncIterator[Any]) -> None:
    """Close an async generator promptly, so the client records a cancelled call before we return."""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


class CoachChatService:
    """Implements `tayyari.chat.ports.ChatService`."""

    def __init__(
        self,
        *,
        settings: Settings,
        claude: ClaudeClient,
        decisions: DecisionService,
        profiles: ProfileRepo,
        sessions: SessionRepo,
        sample: SampleProvider,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._claude = claude
        self._decisions = decisions
        self._profiles = profiles
        self._sessions = sessions
        self._sample = sample
        self._clock: Clock = clock if clock is not None else SystemClock()
        self.renderer = ChatPromptRenderer()
        """Public so tests and the container can read its hit counters."""

    # ───────────────────────── the turn ─────────────────────────

    async def stream_turn(self, req: ChatRequestBody, rc: RequestContext) -> AsyncIterator[ChatEvent]:
        """Stream the events of one turn, ending with one ``done`` (with the ``sessionId``) or one ``error``."""
        started = self._clock.monotonic()
        pid = req.profileId

        loaded = await self._load_profile(pid)
        if loaded is None:
            log.info("chat_profile_not_found", request_id=rc.request_id)
            yield ev_chat_error(PROFILE_NOT_FOUND_MESSAGE)
            return
        profile, context = loaded
        is_sample = is_sample_id(pid) or self._sample.is_sample_profile(pid)

        origin = req.origin.to_dict() if req.origin is not None else None
        thread = await self._open_thread(req, rc, origin)
        if origin is None:
            origin = thread.origin
        user_text = RESET_NOTE + req.message if thread.reset else req.message

        mode, gate_reason = await self._tool_mode(req, rc, origin, is_sample)
        blocks = self.renderer.render(profile, context, origin)
        stream = self._claude.stream_chat(
            model=self._settings.chat_model,
            system=blocks.system_blocks(),
            messages=[*thread.history, {"role": "user", "content": user_text}],
            tools=mode,
            max_tokens=self._settings.chat_max_tokens,
            effort=self._settings.chat_effort,
            cache_tail=True,
            ctx=CallContext(
                purpose="chat",
                request_id=rc.request_id,
                profile_id=pid,
                session_id=thread.id,
                ip_hash=rc.ip_hash,
            ),
        )

        tools: list[StoredToolEvent] = []
        parts: list[str] = []
        end: TurnEnd | None = None
        try:
            async for event in stream:
                if isinstance(event, TextDelta):
                    if event.text:
                        parts.append(event.text)
                        yield ev_delta(event.text)
                elif isinstance(event, ToolUse):
                    shown = ev_tool(ui_tool_name(event.name), event.detail)
                    tools.append(StoredToolEvent(shown.name, shown.detail))
                    yield shown
                elif isinstance(event, TurnEnd):
                    end = event
        except (ClaudeError, AppError) as exc:
            log.warning(
                "chat_turn_failed",
                request_id=rc.request_id,
                profile_id=pid,
                session_id=thread.id,
                error=type(exc).__name__,
                kind=getattr(exc, "kind", None) or getattr(exc, "code", None),
            )
            yield ev_chat_error(exc.user_message)
            return
        finally:
            await _aclose(stream)

        text = end.text if end is not None else "".join(parts)
        if end is not None and end.stop_reason == "max_tokens":
            yield ev_delta(CUT_OFF_NOTE)
        await self._store_turn(thread, user_text, text if text.strip() else None, tools, rc)
        self._log_turn(rc, pid, thread, blocks, mode, gate_reason, end, started)
        yield ev_chat_done(thread.id)

    # ───────────────────────── steps ─────────────────────────

    async def _load_profile(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        """The stored profile and context; the sample persona is saved on first use. None when unknown."""
        found = await self._profiles.get_for_chat(pid)
        if found is None and self._sample.is_sample_profile(pid):
            found = await self._seed_sample(pid)
        return found

    async def _seed_sample(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        """Save the bundled sample profile so sessions can reference it, then read it back like any profile.

        The read-back matters: a document that went through the store can order its keys differently
        (Postgres JSONB sorts them), and the prompt must be byte-identical on every turn to stay cached.
        """
        sample = self._sample.sample_profile
        if sample is None:
            return None
        document = sample.to_dict()
        context = self._sample.sample_context
        try:
            await self._profiles.save(
                pid, document, context, goal=sample.goal, owner_hash=None, is_sample=True
            )
            stored = await self._profiles.get_for_chat(pid)
            if stored is not None:
                return stored
        except Exception:
            log.warning("chat_sample_seed_failed", profile_id=pid, exc_info=True)
        return document, context

    async def _open_thread(
        self, req: ChatRequestBody, rc: RequestContext, origin: dict[str, Any] | None
    ) -> _Thread:
        """Resume the request's session, or start a new one (the legacy reset path)."""
        pid = req.profileId
        resumed = None
        if req.sessionId:
            try:
                resumed = await self._sessions.load(req.sessionId, pid, CHAT_HISTORY_MESSAGES)
            except Exception:
                log.warning("chat_session_load_failed", profile_id=pid, exc_info=True)
            if resumed is None:
                log.info("chat_session_not_resumed", profile_id=pid, request_id=rc.request_id)
        if resumed is not None:
            return _Thread(id=resumed.id, history=_replay_history(resumed.messages), origin=resumed.origin)

        reset = bool(req.sessionId)
        try:
            created = await self._sessions.create(pid, origin, rc.ip_hash)
        except Exception:
            log.warning("chat_session_create_failed", profile_id=pid, exc_info=True)
            return _Thread(id=new_session_id(), reset=reset, persist=False)
        return _Thread(id=created.id, reset=reset)

    async def _tool_mode(
        self, req: ChatRequestBody, rc: RequestContext, origin: Mapping[str, Any] | None, is_sample: bool
    ) -> tuple[ToolMode, str]:
        """``"web"`` or ``"none"`` (tools stay declared either way, so the cached prefix is the same)."""
        if url_in_message(req.message):
            return "web", "url in message"
        try:
            gate = await self._decisions.chat_web_gate(
                GateInput(message=req.message, thread=_thread_label(origin)),
                DecisionContext(req.profileId, None, is_sample, rc.request_id, purpose_tag="chat"),
            )
        except Exception:
            log.warning("chat_gate_failed", profile_id=req.profileId, exc_info=True)
            return "web", "gate failed"
        return ("web" if gate.allow_tools else "none"), gate.reason

    async def _store_turn(
        self,
        thread: _Thread,
        user_text: str,
        assistant: str | None,
        tools: list[StoredToolEvent],
        rc: RequestContext,
    ) -> None:
        """Save the exchange; a failure is logged and the turn still completes."""
        if not thread.persist:
            return
        try:
            await self._sessions.append_turn(
                thread.id, user=user_text, assistant=assistant, tools=tools, llm_call_id=None
            )
        except Exception:
            log.warning("chat_turn_not_saved", session_id=thread.id, request_id=rc.request_id, exc_info=True)

    def _log_turn(
        self,
        rc: RequestContext,
        pid: str,
        thread: _Thread,
        blocks: ChatPromptBlocks,
        mode: ToolMode,
        gate_reason: str,
        end: TurnEnd | None,
        started: float,
    ) -> None:
        """One line per finished turn: sizes and cache numbers only, never message or profile text."""
        usage: Usage | None = end.usage if end is not None else None
        log.info(
            "chat_turn",
            request_id=rc.request_id,
            profile_id=pid,
            session_id=thread.id,
            resumed=bool(thread.history) and not thread.reset,
            history_messages=len(thread.history),
            tools=mode,
            gate=gate_reason,
            profile_chars=len(blocks.profile),
            stop_reason=end.stop_reason if end is not None else None,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cache_write_tokens=usage.cache_write_tokens if usage else None,
            cache_read_tokens=usage.cache_read_tokens if usage else None,
            web_searches=usage.web_searches if usage else None,
            seconds=round(self._clock.monotonic() - started, 3),
        )


__all__ = [
    "CUT_OFF_NOTE",
    "PROFILE_NOT_FOUND_MESSAGE",
    "RESET_NOTE",
    "CoachChatService",
]
