"""The decisions facade: what analysis and chat call, as a Protocol with plain input and result types.

The packs (Wave 2) implement `DecisionService` on top of the runner; the analysis and chat services depend only
on this module, so they are tested with `tests.fakes.decisions.FakeDecisionService` and run unchanged when
decisions are off (`NullDecisionService`).

Contract for every implementation:
  - Nothing here raises for a Jev, Claude or spend failure: a failed decision returns its neutral result
    (no first read, no skip, keep the evidence, allow tools). Only cancellation propagates.
  - ``shadow_compare`` returns at once; the work runs in a tracked background task and never raises.
  - ``drain`` waits (up to ``timeout_s``) for the background work and flushes the decision log.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from ..contract import Profile


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Who a decision is for: ids for the log rows and the sample flag (sample rows are kept forever as an eval corpus)."""

    profile_id: str | None
    analysis_id: UUID | None
    is_sample: bool
    request_id: str | None
    purpose_tag: str = ""
    """A free label stored with each log row (for example ``"analysis"`` or ``"chat"``)."""


# ───────────────────────── first read ─────────────────────────


@dataclass(frozen=True, slots=True)
class FirstReadInput:
    """The text ingested for the person's resume and notes. ``today`` is for the pack's own date logic: Jev never counts."""

    resume_text: str | None
    notes_text: str | None
    today: date


@dataclass(frozen=True, slots=True)
class FirstReadResult:
    lines: list[str] = field(default_factory=list)
    """Short display log lines (3 to 5), in order."""
    signals: list[str] = field(default_factory=list)
    """Chip labels of at most 32 characters."""
    facts: dict[str, Any] = field(default_factory=dict)
    """The interpreted answers, for logging and comparison with the Sonnet profile."""


# ───────────────────────── triage ─────────────────────────


@dataclass(frozen=True, slots=True)
class TriageInput:
    source_id: str
    kind: str
    host: str | None
    title: str | None
    text: str


@dataclass(frozen=True, slots=True)
class TriageResult:
    skip: bool = False
    """True only for a login wall or an empty page that Jev and the code both agree on."""
    skip_reason: str | None = None
    label: str | None = None
    """A better ``kindLabel`` for generic links (``"Blog"``, ``"Publication"``), when confident."""
    notes: list[str] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    text_quality: str | None = None
    english: bool | None = None


# ───────────────────────── evidence ─────────────────────────


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    skill_id: str
    source_id: str
    text: str
    source_text: str
    reader_output: dict[str, Any] | None = None
    facts: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class EvidenceVerdict:
    skill_id: str
    source_id: str
    text: str
    keep: bool
    relation: str | None = None
    """``supports`` / ``contradicts`` / ``says_nothing`` when Jev or Claude was asked, else None."""
    reason: str = ""


# ───────────────────────── chat gate ─────────────────────────


@dataclass(frozen=True, slots=True)
class GateInput:
    message: str
    thread: str
    """``"<origin kind>: <title>"``."""


@dataclass(frozen=True, slots=True)
class GateResult:
    allow_tools: bool
    reason: str = ""
    p_outside: float | None = None
    """Jev's probability that the message needs outside information, when it answered."""


# ───────────────────────── portfolio copy ─────────────────────────


@dataclass(frozen=True, slots=True)
class CopyClaim:
    """One sentence of generated portfolio copy and the only text it may rest on."""

    slot_id: str
    sentence: str
    corpus: str
    """The verbatim quotes of the facts the slot cites, joined (never the whole resume)."""


@dataclass(frozen=True, slots=True)
class CopyVerdict:
    slot_id: str
    sentence: str
    keep: bool
    relation: str | None = None
    """``supports`` / ``contradicts`` / ``says_nothing`` when Jev or Claude answered, else None."""
    confidence: float | None = None
    reason: str = ""


# ───────────────────────── shadow ─────────────────────────


@dataclass(frozen=True, slots=True)
class ShadowMaterials:
    profile: Profile
    reader_outputs: dict[str, dict[str, Any]]
    """Reader output per source id."""
    resume_text: str | None


# ───────────────────────── the facade ─────────────────────────


@runtime_checkable
class DecisionService(Protocol):
    async def first_read(self, inp: FirstReadInput, ctx: DecisionContext) -> FirstReadResult | None:
        """The instant read of the resume for the analysis screen; None when there is nothing to show."""
        ...

    async def triage(self, inp: TriageInput, ctx: DecisionContext) -> TriageResult:
        """Judge one ingested source: skip a wall or an empty page, otherwise label it."""
        ...

    async def check_evidence(
        self, claims: Sequence[EvidenceClaim], ctx: DecisionContext
    ) -> list[EvidenceVerdict]:
        """One verdict per claim, in the same order; unfinished or failed checks keep the claim."""
        ...

    async def chat_web_gate(self, inp: GateInput, ctx: DecisionContext) -> GateResult:
        """May the coach use web tools for this message? Defaults to yes."""
        ...

    def shadow_compare(self, materials: ShadowMaterials, ctx: DecisionContext) -> None:
        """Queue the shadow decisions (skill levels, requirements, roles) as a tracked background task; never raises."""
        ...

    async def drain(self, timeout_s: float) -> None:
        """Wait for background work and flush the log (end of an analysis, shutdown)."""
        ...


class NullDecisionService:
    """Decisions off: every method returns its neutral result and nothing is called or logged."""

    async def first_read(self, inp: FirstReadInput, ctx: DecisionContext) -> FirstReadResult | None:
        return None

    async def triage(self, inp: TriageInput, ctx: DecisionContext) -> TriageResult:
        return TriageResult()

    async def check_evidence(
        self, claims: Sequence[EvidenceClaim], ctx: DecisionContext
    ) -> list[EvidenceVerdict]:
        return [
            EvidenceVerdict(c.skill_id, c.source_id, c.text, keep=True, relation=None, reason="decisions off")
            for c in claims
        ]

    async def chat_web_gate(self, inp: GateInput, ctx: DecisionContext) -> GateResult:
        return GateResult(allow_tools=True, reason="decisions off")

    def shadow_compare(self, materials: ShadowMaterials, ctx: DecisionContext) -> None:
        return None

    async def drain(self, timeout_s: float) -> None:
        return None


__all__ = [
    "CopyClaim",
    "CopyVerdict",
    "DecisionContext",
    "DecisionService",
    "EvidenceClaim",
    "EvidenceVerdict",
    "FirstReadInput",
    "FirstReadResult",
    "GateInput",
    "GateResult",
    "NullDecisionService",
    "ShadowMaterials",
    "TriageInput",
    "TriageResult",
]
