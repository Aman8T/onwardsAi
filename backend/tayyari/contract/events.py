"""Typed NDJSON stream events: `AnalysisEvent` (POST /api/analyze) and `ChatEvent` (POST /api/chat).

Both are discriminated unions on ``"type"`` and mirror the unions of the same names in shared/types.ts.
Each member carries its own ``type`` literal as a default, so ``LogEvent(who="Jev", text="...")`` is
already complete; parsing a dict that lacks ``type`` is an error.

`encode_event` is the single place an event becomes bytes on the wire: one compact JSON line ending in
``"\\n"``, optional fields that are ``None`` omitted. The ``ev_*`` builders mirror the legacy helpers in
`tayyari.legacy.types` (same arguments, same JSON), but return typed events.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/types.ts)

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final, Literal

from pydantic import Field, TypeAdapter
from pydantic_core import PydanticSerializationError

from tayyari.contract.capabilities import Destination
from tayyari.contract.jobsearch import JobSearchEvent
from tayyari.contract.match_api import MatchStreamEvent
from tayyari.contract.models import Phase, Profile, ReaderState, SourceKind, _Model
from tayyari.contract.portfolio import PortfolioEvent
from tayyari.contract.prepare import PrepareEvent
from tayyari.core.limits import CHAT_TOOL_DETAIL_MAX_CHARS

LogKind = SourceKind | Literal["sonnet"]
"""What a log line is about: a source kind, or "sonnet" for the synthesis step."""
SynthStep = Literal[0, 1, 2, 3]

HEARTBEAT: Final[bytes] = b"\n"
"""A blank line written after a quiet spell so idle proxies keep the stream open; clients skip it."""

# ───────────────────────── analysis events ─────────────────────────


class ReadersEvent(_Model):
    type: Literal["readers"] = "readers"
    readers: list[ReaderState]


class ReaderEvent(_Model):
    type: Literal["reader"] = "reader"
    reader: ReaderState


class SignalEvent(_Model):
    type: Literal["signal"] = "signal"
    label: str
    src: str
    kind: SourceKind


class LogEvent(_Model):
    type: Literal["log"] = "log"
    who: str
    text: str
    kind: LogKind | None = None


class PhaseEvent(_Model):
    type: Literal["phase"] = "phase"
    phase: Phase
    label: str


class SynthEvent(_Model):
    type: Literal["synth"] = "synth"
    step: SynthStep
    chars: int | None = None


class DoneEvent(_Model):
    type: Literal["done"] = "done"
    profile: Profile


class ErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str


AnalysisEvent = Annotated[
    ReadersEvent | ReaderEvent | SignalEvent | LogEvent | PhaseEvent | SynthEvent | DoneEvent | ErrorEvent,
    Field(discriminator="type"),
]

# ───────────────────────── chat events ─────────────────────────


class DeltaEvent(_Model):
    type: Literal["delta"] = "delta"
    text: str


class ToolEvent(_Model):
    """The model used a server tool; ``name`` is the UI's name (WebSearch, WebFetch), ``detail`` the query or URL."""

    type: Literal["tool"] = "tool"
    name: str
    detail: str


class ActionEvent(_Model):
    """The coach proposes one button after its text (`shared/capabilities.ts`). No model prose rides on it: ``label`` is
    the capability's button text and ``title`` the target's own title."""

    type: Literal["action"] = "action"
    destination: Destination
    target: str | None = None
    label: str
    title: str | None = None


class ChatDoneEvent(_Model):
    type: Literal["done"] = "done"
    sessionId: str


class ChatErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str


ChatEvent = Annotated[
    DeltaEvent | ToolEvent | ActionEvent | ChatDoneEvent | ChatErrorEvent,
    Field(discriminator="type"),
]

AnalysisEventAdapter: TypeAdapter[AnalysisEvent] = TypeAdapter(AnalysisEvent)
ChatEventAdapter: TypeAdapter[ChatEvent] = TypeAdapter(ChatEvent)

# ───────────────────────── encoding ─────────────────────────

# `str.splitlines()` also breaks on these; JSON allows them raw inside a string, so escape them and a line
# of the stream can never be split by a consumer that uses it (NEL, line separator, paragraph separator).
_LINE_BREAKS: Final[dict[int, str]] = {code: f"\\u{code:04x}" for code in (0x85, 0x2028, 0x2029)}


def encode_event(
    ev: AnalysisEvent | ChatEvent | PortfolioEvent | MatchStreamEvent | JobSearchEvent | PrepareEvent,
) -> bytes:
    """One compact JSON line (UTF-8, not ASCII-escaped, ``None`` fields omitted) ending in ``"\\n"``.

    It also encodes the portfolio stream's events (`contract.portfolio.PortfolioEvent`), the posting match's
    (`contract.match_api.MatchStreamEvent`), the search's and the plan's (`contract.prepare.PrepareEvent`), which share
    the wire form.
    """
    try:
        text = ev.model_dump_json(exclude_none=True)
    except PydanticSerializationError:
        # Text that holds a lone surrogate cannot be written as UTF-8; ASCII-escape the line instead.
        text = json.dumps(ev.model_dump(mode="json", exclude_none=True), separators=(",", ":"))
    return text.translate(_LINE_BREAKS).encode("utf-8") + b"\n"


# ───────────────────────── analysis builders ─────────────────────────


def _reader_state(state: ReaderState | Mapping[str, Any]) -> ReaderState:
    # A snapshot: callers keep updating their ReaderState after emitting it.
    return state.model_copy() if isinstance(state, ReaderState) else ReaderState.from_dict(state)


def ev_readers(readers: Sequence[ReaderState | Mapping[str, Any]]) -> ReadersEvent:
    return ReadersEvent(readers=[_reader_state(r) for r in readers])


def ev_reader(state: ReaderState | Mapping[str, Any]) -> ReaderEvent:
    return ReaderEvent(reader=_reader_state(state))


def ev_signal(label: str, src: str, kind: SourceKind) -> SignalEvent:
    return SignalEvent(label=label, src=src, kind=kind)


def ev_log(who: str, text: str, kind: LogKind | None = None) -> LogEvent:
    return LogEvent(who=who, text=text, kind=kind)


def ev_phase(phase: Phase, label: str) -> PhaseEvent:
    return PhaseEvent(phase=phase, label=label)


def ev_synth(step: SynthStep, chars: int | None = None) -> SynthEvent:
    return SynthEvent(step=step, chars=chars)


def ev_done(profile: Profile | Mapping[str, Any]) -> DoneEvent:
    snapshot = profile.model_copy(deep=True) if isinstance(profile, Profile) else Profile.from_dict(profile)
    return DoneEvent(profile=snapshot)


def ev_error(message: str) -> ErrorEvent:
    return ErrorEvent(message=message)


# ───────────────────────── chat builders ─────────────────────────


def ev_delta(text: str) -> DeltaEvent:
    return DeltaEvent(text=text)


def ev_tool(name: str, detail: str) -> ToolEvent:
    """A tool use; ``detail`` is cut to `CHAT_TOOL_DETAIL_MAX_CHARS`."""
    return ToolEvent(name=name, detail=detail[:CHAT_TOOL_DETAIL_MAX_CHARS])


def ev_action(destination: Destination, target: str | None, label: str, title: str | None) -> ActionEvent:
    return ActionEvent(destination=destination, target=target, label=label, title=title)


def ev_chat_done(session_id: str) -> ChatDoneEvent:
    return ChatDoneEvent(sessionId=session_id)


def ev_chat_error(message: str) -> ChatErrorEvent:
    return ChatErrorEvent(message=message)


__all__ = [
    "HEARTBEAT",
    "ActionEvent",
    "AnalysisEvent",
    "AnalysisEventAdapter",
    "ChatDoneEvent",
    "ChatErrorEvent",
    "ChatEvent",
    "ChatEventAdapter",
    "DeltaEvent",
    "DoneEvent",
    "ErrorEvent",
    "LogEvent",
    "LogKind",
    "PhaseEvent",
    "ReaderEvent",
    "ReadersEvent",
    "SignalEvent",
    "SynthEvent",
    "SynthStep",
    "ToolEvent",
    "encode_event",
    "ev_action",
    "ev_chat_done",
    "ev_chat_error",
    "ev_delta",
    "ev_done",
    "ev_error",
    "ev_log",
    "ev_phase",
    "ev_reader",
    "ev_readers",
    "ev_signal",
    "ev_synth",
    "ev_tool",
]
