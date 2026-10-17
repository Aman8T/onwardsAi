"""Value types shared by every LLM call: who is asking, what was sent, what it cost and what came back.

These are plain frozen dataclasses with no behaviour beyond arithmetic helpers, so any layer can build
and compare them in tests. Money is always integer micro-USD (1 USD = 1_000_000) so sums never drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

Purpose = Literal[
    "reader",
    "synth",
    "chat",
    "portfolio",
    "decision_fallback",
    "smoke",
    "eval",
    "label",
    "match.facts",
    "match.posting",
    "match.gates",
    "match.coverage",
    "match.relevance",
    "match.phrase",
    "match.cover",
    "jobsearch.scout",
    "jobsearch.judge",
    "jobsearch.prepare",
    "jobsearch.referral",
    "interview.phrase",
    "interview.judge",
    "interview.checks",
    "interview.debrief",
    "interview.assistant",
]
"""Why a Claude call is made. Stored on every `llm_calls` row and used to script fakes."""

Effort = Literal["low", "medium", "high", "xhigh", "max"]
"""`output_config.effort` values the API documents (Claude docs, "Effort"). Haiku 4.5 accepts none of them."""

ToolMode = Literal["web", "none"]
"""Chat tool policy. ``"none"`` keeps the web tool definitions in the request (so the cached prefix is
unchanged) and sets ``tool_choice={"type": "none"}``."""

MessageParam = dict[str, Any]
"""One Messages-API turn (``{"role": ..., "content": ...}``), passed through to the SDK unchanged."""


@dataclass(frozen=True, slots=True)
class CallContext:
    """Correlation data attached to every Claude or Jev call (logged and written to the usage tables)."""

    purpose: Purpose
    request_id: str | None = None
    profile_id: str | None = None
    session_id: str | None = None
    analysis_id: UUID | None = None
    ip_hash: str | None = None


@dataclass(frozen=True, slots=True)
class TextBlock:
    """One text block of a system or user prompt. ``cache=True`` puts an ephemeral cache breakpoint on it."""

    text: str
    cache: bool = False


@dataclass(frozen=True, slots=True)
class Usage:
    """Token and cost accounting of one Claude request (or, for a chat turn, of all its continuations).

    The token counters follow the API: ``input_tokens`` is only the uncached input after the last cache
    breakpoint, so the whole prompt is ``input_tokens + cache_write_tokens + cache_read_tokens``.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    web_searches: int = 0
    request_id: str | None = None
    cost_micro_usd: int = 0

    @property
    def prompt_tokens(self) -> int:
        """Every input token the model saw, cached or not."""
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens

    def __add__(self, other: Usage) -> Usage:
        """Sum two usages of the same model (a turn and its ``pause_turn`` continuation); keeps the last request id."""
        if not isinstance(other, Usage):
            return NotImplemented
        if other.model != self.model:
            raise ValueError(f"cannot add usages of different models ({self.model!r} and {other.model!r})")
        return Usage(
            model=self.model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            web_searches=self.web_searches + other.web_searches,
            request_id=other.request_id or self.request_id,
            cost_micro_usd=self.cost_micro_usd + other.cost_micro_usd,
        )


@dataclass(frozen=True, slots=True)
class StructuredResult:
    """A validated structured answer: the JSON object, what it cost and how many attempts it took."""

    data: dict[str, Any]
    usage: Usage
    stop_reason: str = "end_turn"
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A piece of the assistant's visible text."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolUse:
    """A server-side web tool call the model made, with the UI's tool name (``WebSearch`` / ``WebFetch``)."""

    name: Literal["WebSearch", "WebFetch"] | str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TurnEnd:
    """The last event of a chat stream: the whole visible text, the turn's usage and why it stopped."""

    text: str
    usage: Usage
    stop_reason: str = "end_turn"


ChatStreamEvent = TextDelta | ToolUse | TurnEnd


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One result of a web search, as the search tool returned it (never the model's retelling of it)."""

    url: str
    title: str
    page_age: str | None = None


@dataclass(frozen=True, slots=True)
class SearchCall:
    """One search the model ran: its query and the results, or the tool's error code when it failed."""

    query: str
    hits: tuple[SearchHit, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WebSearchRun:
    """What one model turn with only the web search tool did: every search, in order, with its results.

    ``text`` is the model's own closing words; callers that must not show a model's claims ignore it.
    """

    searches: tuple[SearchCall, ...]
    usage: Usage
    stop_reason: str = "end_turn"
    text: str = ""


__all__ = [
    "CallContext",
    "ChatStreamEvent",
    "Effort",
    "MessageParam",
    "Purpose",
    "SearchCall",
    "SearchHit",
    "StructuredResult",
    "TextBlock",
    "TextDelta",
    "ToolMode",
    "ToolUse",
    "TurnEnd",
    "Usage",
    "WebSearchRun",
]
