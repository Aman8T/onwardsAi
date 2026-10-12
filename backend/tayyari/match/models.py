"""The match's data types: the wire contract (re-exported from `contract.match`) and the few internal ones.

`FactBook` is the person's evidence for one match: the facts the models may cite, plus the entry structure
(which bullet facts belong to which job, project or degree) that the resume is composed from. It is plain data and
round-trips through JSON, so the persistence wave can cache it next to the portfolio facts.
"""

# ruff: noqa: N815  (BookEntry mirrors the camelCase naming of the contract it is built from)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tayyari.contract.match import (
    Bullet,
    Change,
    Coverage,
    CoverNote,
    CoverParagraph,
    Decision,
    DecisionChoice,
    EducationEntry,
    ExperienceEntry,
    Fact,
    FactRef,
    Gate,
    GateAnswer,
    LintItem,
    LintReport,
    MatchResult,
    Posting,
    Question,
    Radar,
    RadarAxis,
    Requirement,
    ResumeHeader,
    ResumeVariant,
    SkillGroup,
    SkillItem,
    StatusCounts,
)

EntryKind = Literal["experience", "project", "education"]


class BookEntry(BaseModel):
    """A job, project or degree and the ids of the bullet facts under it."""

    model_config = ConfigDict(extra="ignore")

    factId: str
    kind: EntryKind
    title: str
    org: str | None = None
    location: str | None = None
    start: str | None = None
    end: str | None = None
    current: bool = False
    dates: str | None = None
    """The period as the resume prints it: ``Jul 2025 - Present``, ``Jan 2024 - Apr 2024``, ``2022``."""
    bulletIds: list[str] = Field(default_factory=list)
    recency: int = 0
    """Sort key, higher is more recent (``YYYYMM`` of the end, 999999 when current)."""


class FactBook(BaseModel):
    """Every fact one match may cite, with the structure the resume is built from."""

    model_config = ConfigDict(extra="ignore")

    name: str
    headline: str | None = None
    contact: list[str] = Field(default_factory=list)
    """Email and phone exactly as the source writes them."""
    facts: list[Fact] = Field(default_factory=list)
    entries: list[BookEntry] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    """Why facts or references were refused while the book was built (counts for logs and tests)."""

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def get(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def entry(self, fact_id: str) -> BookEntry | None:
        return next((e for e in self.entries if e.factId == fact_id), None)

    def with_facts(self, extra: list[Fact]) -> FactBook:
        """A copy with ``extra`` facts added (ids already present are kept as they are)."""
        have = {f.id for f in self.facts}
        added = [f for f in extra if f.id not in have]
        return self.model_copy(update={"facts": [*self.facts, *added]})


@dataclass(frozen=True, slots=True)
class Dropped:
    """Something a code check refused: ``kind`` says what (requirement, gate, term, anomaly ...), ``reason`` why."""

    kind: str
    label: str
    reason: str


@dataclass(slots=True)
class Report:
    """The refusals of one stage, in order (never shown to the person; they are for logs and tests)."""

    dropped: list[Dropped] = field(default_factory=list)

    def add(self, kind: str, label: str, reason: str) -> None:
        self.dropped.append(Dropped(kind, label, reason))


__all__ = [
    "BookEntry",
    "Bullet",
    "Change",
    "CoverNote",
    "CoverParagraph",
    "Coverage",
    "Decision",
    "DecisionChoice",
    "Dropped",
    "EducationEntry",
    "ExperienceEntry",
    "Fact",
    "FactBook",
    "FactRef",
    "Gate",
    "GateAnswer",
    "LintItem",
    "LintReport",
    "MatchResult",
    "Posting",
    "Question",
    "Radar",
    "RadarAxis",
    "Report",
    "Requirement",
    "ResumeHeader",
    "ResumeVariant",
    "SkillGroup",
    "SkillItem",
    "StatusCounts",
]
