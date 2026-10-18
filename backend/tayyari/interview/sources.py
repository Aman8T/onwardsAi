"""A slot's sources as the prompts and the checks read them."""

from __future__ import annotations

from collections.abc import Sequence

from tayyari.contract.interview import SlotSource
from tayyari.prompts.interview import SourceLine

YOUR_ANSWER = "Your answer"
"""The label of the person's own words among a Say line's sources."""


def source_lines(sources: Sequence[SlotSource]) -> list[SourceLine]:
    return [SourceLine(origin=s.origin, text=s.text, label=s.source) for s in sources]


def evidence_of(sources: Sequence[SlotSource]) -> list[SlotSource]:
    return [s for s in sources if s.origin == "evidence"]


def posting_of(sources: Sequence[SlotSource]) -> list[SlotSource]:
    return [s for s in sources if s.origin == "posting"]


def asks_of(sources: Sequence[SlotSource]) -> list[SlotSource]:
    """What the role asks for: a posting's words, or a graph role's requirement (origin ``role``)."""
    return [s for s in sources if s.origin in ("posting", "role")]


def evidence_labels(sources: Sequence[SlotSource]) -> list[str]:
    """Where the slot's evidence is from ("Resume", "GitHub" ...), once each, in order."""
    return list(dict.fromkeys(s.source for s in evidence_of(sources) if s.source))


def quote_source(sources: Sequence[SlotSource]) -> SlotSource | None:
    """The source a question quotes: what the role asks for when the slot has it (an ask, a gap: the posting's words or a
    graph role's requirement), else the first evidence line."""
    return next(iter(asks_of(sources)), None) or next(iter(evidence_of(sources)), None)


def sources_text(sources: Sequence[SlotSource]) -> str:
    return "\n".join(s.text for s in sources)


__all__ = [
    "YOUR_ANSWER",
    "asks_of",
    "evidence_labels",
    "evidence_of",
    "posting_of",
    "quote_source",
    "source_lines",
    "sources_text",
]
