"""Pydantic mirror of shared/match.ts: the posting-match contract.

Same rules as `tayyari.contract.portfolio`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and
`tests/unit/match/test_contract_parity.py` fails when the two drift. The constants (`TIER_WEIGHTS`, the limits,
the band thresholds) are checked against the TypeScript consts.

Reading order of a match: a `Posting` (read on its own, never beside a person) holds `Requirement`s and `Gate`s;
every requirement is checked against the person's `Fact`s into a `Coverage` row; the `MatchResult` carries the
band, counts and confidence computed from those rows by code. A `ResumeVariant` and a `CoverNote` are composed
from the same facts and carry the ids of the facts every sentence rests on.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/match.ts)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, field_validator, model_validator

from tayyari.contract.models import _Model

# ───────────────────────── enums ─────────────────────────

FactSource = Literal["resume", "github", "lovable", "link", "user"]
MatchFactKind = Literal["experience", "accomplishment", "skill", "education", "project", "cert", "other"]
EvidenceTier = Literal["artifact", "built", "used", "listed"]
RequirementTier = Literal["must", "required", "preferred", "nice"]
RequirementKind = Literal[
    "skill", "years", "degree", "cert", "tool", "domain", "location", "auth", "language", "other"
]
Provenance = Literal["stated", "structural", "inferred"]
WorkMode = Literal["onsite", "hybrid", "remote", "unspecified"]
GateVerdict = Literal["pass", "fail", "uncertain"]
CoverageStatus = Literal["covered", "partial", "synonym_only", "missing_have_it", "missing_gap", "unknown"]
MatchBand = Literal["strong", "good", "stretch", "weak"]
Confidence = Literal["high", "medium", "low"]
BulletVerdict = Literal["exact", "reframed", "stretch"]
BulletStatus = Literal["verified", "needs_user", "dropped"]
DecisionKind = Literal["stretch", "number", "confirm_term"]
DecisionChoiceKey = Literal["keep", "soften", "drop", "fill", "use_posting_term", "keep_my_wording"]
ChangeKind = Literal["surfaced", "reordered", "reworded", "left_out"]
CoverKind = Literal["match", "gap", "logistics", "close"]

# ───────────────────────── constants ─────────────────────────

MATCH_VERSION: Final = 1
"""Bumped when the shape of a stored `MatchResult` changes."""

TIER_WEIGHTS: Final[dict[str, float]] = {"must": 1.0, "required": 0.8, "preferred": 0.5, "nice": 0.25}
"""How much a requirement counts in the band, by how strongly the posting asks for it."""

MAX_REQUIREMENTS: Final = 30
MAX_GATES: Final = 6
MAX_QUESTIONS: Final = 3
GATE_BLOCK_SEVERITY: Final = 7
"""Only a `fail` on a gate of at least this severity blocks. `uncertain` never blocks."""

BAND_STRONG: Final = 0.80
BAND_GOOD: Final = 0.60
BAND_STRETCH: Final = 0.35
"""Lower bounds of the weighted coverage (0..1) for each band; below `BAND_STRETCH` is `weak`."""

COVER_WORDS_MIN: Final = 250
COVER_WORDS_MAX: Final = 400

# ───────────────────────── facts ─────────────────────────


class FactRef(_Model):
    """Where a fact comes from: a source kind, a document id and (when known) the lines the quote sits on."""

    source: FactSource
    docId: str
    lines: list[int] | None = None
    quote: str

    @field_validator("lines")
    @classmethod
    def _two_line_numbers(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and not (len(value) == 2 and 1 <= value[0] <= value[1]):
            raise ValueError("lines is [first, last] with 1 <= first <= last")
        return value


class Fact(_Model):
    """Something true about the person, with the words of its source. ``quote`` is a passage of the source."""

    id: str
    text: str
    kind: MatchFactKind
    refs: list[FactRef]
    evidenceTier: EvidenceTier | None = None


# ───────────────────────── posting ─────────────────────────


class Requirement(_Model):
    id: str
    text: str
    quote: str
    tier: RequirementTier
    kind: RequirementKind
    provenance: Provenance
    domainHint: str | None = None
    terms: list[str]
    """The named skills, tools or credentials of the requirement, each a passage of ``quote``."""

    @model_validator(mode="after")
    def _inferred_is_never_a_must(self) -> Requirement:
        if self.provenance == "inferred" and self.tier in ("must", "required"):
            raise ValueError("an inferred requirement can never be must or required")
        return self


class Gate(_Model):
    id: str
    question: str
    quote: str
    severity: int = Field(ge=1, le=10)


class Posting(_Model):
    hash: str
    title: str
    company: str | None = None
    location: str | None = None
    workMode: WorkMode
    rawText: str
    requirements: list[Requirement] = Field(max_length=MAX_REQUIREMENTS)
    gates: list[Gate] = Field(max_length=MAX_GATES)
    anomalies: list[str]
    """Text in the posting that tries to instruct a model. Reported, never followed."""
    links: list[str]
    """Web addresses written in the posting. Listed, never opened."""


# ───────────────────────── match ─────────────────────────


class GateAnswer(_Model):
    gateId: str
    factIds: list[str]
    verdict: GateVerdict
    note: str


class Coverage(_Model):
    requirementId: str
    factIds: list[str]
    status: CoverageStatus
    level: int = Field(ge=0, le=4)
    note: str
    interviewRisk: str | None = None
    mitigation: str | None = None


class StatusCounts(_Model):
    covered: int
    partial: int
    synonym_only: int
    missing_have_it: int
    missing_gap: int
    unknown: int


class RadarAxis(_Model):
    domain: str
    you: int
    role: int


class Radar(_Model):
    axes: list[RadarAxis]


class Question(_Model):
    id: str
    requirementId: str | None = None
    gateId: str | None = None
    text: str
    why: str


class MatchResult(_Model):
    postingHash: str
    band: MatchBand
    counts: StatusCounts
    confidence: Confidence
    confidenceNote: str
    gates: list[GateAnswer]
    blocked: bool
    coverage: list[Coverage]
    radar: Radar
    questions: list[Question] = Field(max_length=MAX_QUESTIONS)
    version: int


# ───────────────────────── resume variant ─────────────────────────


class LintItem(_Model):
    id: str
    ok: bool
    label: str
    detail: str
    location: str | None = None


class LintReport(_Model):
    items: list[LintItem]


class Bullet(_Model):
    id: str
    text: str
    factIds: list[str]
    verdict: BulletVerdict
    placeholders: list[str]
    status: BulletStatus


class SkillItem(_Model):
    name: str
    factIds: list[str]
    evidenceTier: EvidenceTier | None = None


class SkillGroup(_Model):
    group: str
    items: list[SkillItem]


class ResumeHeader(_Model):
    name: str
    headline: str | None = None
    contact: list[str]
    """Email and phone exactly as the resume writes them, shown as plain text."""


class ExperienceEntry(_Model):
    entryFactId: str
    title: str
    org: str | None = None
    dates: str | None = None
    bullets: list[Bullet]


class EducationEntry(_Model):
    factId: str
    title: str
    org: str | None = None
    dates: str | None = None


class Decision(_Model):
    bulletId: str
    kind: DecisionKind
    prompt: str
    options: list[DecisionChoiceKey]
    """The choices the person has: stretch keep/soften/drop, number fill/soften/drop,
    confirm_term use_posting_term/keep_my_wording/drop."""


class Change(_Model):
    kind: ChangeKind
    text: str
    factIds: list[str]


class ResumeVariant(_Model):
    header: ResumeHeader
    summary: Bullet
    skills: list[SkillGroup]
    experience: list[ExperienceEntry]
    projects: list[ExperienceEntry]
    education: list[EducationEntry]
    decisions: list[Decision]
    changes: list[Change]
    lint: LintReport | None = None


class DecisionChoice(_Model):
    """The person's answer to one `Decision` (the body item of the PATCH that settles a resume)."""

    bulletId: str
    choice: DecisionChoiceKey
    value: str | None = None
    """The figure for ``fill``."""


# ───────────────────────── cover note ─────────────────────────


class CoverParagraph(_Model):
    text: str
    requirementIds: list[str]
    factIds: list[str]
    kind: CoverKind


class CoverNote(_Model):
    paragraphs: list[CoverParagraph]
    wordCount: int
    lint: LintReport | None = None


# ───────────────────────── helpers ─────────────────────────


def tier_weight(tier: str) -> float:
    """The weight of a requirement tier (``nice`` for anything unknown: the smallest weight)."""
    return TIER_WEIGHTS.get(tier, TIER_WEIGHTS["nice"])


__all__ = [
    "BAND_GOOD",
    "BAND_STRETCH",
    "BAND_STRONG",
    "COVER_WORDS_MAX",
    "COVER_WORDS_MIN",
    "GATE_BLOCK_SEVERITY",
    "MATCH_VERSION",
    "MAX_GATES",
    "MAX_QUESTIONS",
    "MAX_REQUIREMENTS",
    "TIER_WEIGHTS",
    "Bullet",
    "BulletStatus",
    "BulletVerdict",
    "Change",
    "ChangeKind",
    "Confidence",
    "CoverKind",
    "CoverNote",
    "CoverParagraph",
    "Coverage",
    "CoverageStatus",
    "Decision",
    "DecisionChoice",
    "DecisionChoiceKey",
    "DecisionKind",
    "EducationEntry",
    "EvidenceTier",
    "ExperienceEntry",
    "Fact",
    "FactRef",
    "FactSource",
    "Gate",
    "GateAnswer",
    "GateVerdict",
    "LintItem",
    "LintReport",
    "MatchBand",
    "MatchFactKind",
    "MatchResult",
    "Posting",
    "Provenance",
    "Question",
    "Radar",
    "RadarAxis",
    "Requirement",
    "RequirementKind",
    "RequirementTier",
    "ResumeHeader",
    "ResumeVariant",
    "SkillGroup",
    "SkillItem",
    "StatusCounts",
    "WorkMode",
    "tier_weight",
]
