"""Pydantic mirror of shared/jobsearch.ts: job search with AI (Haiku scouts, employers' own boards, a checked read).

Same rules as `tayyari.contract.discover`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and `tests/unit/contract/test_jobsearch_parity.py`
fails when the two drift. `Cite`, `Place`, `QuickFit` and `Salary` are Discover's, reused.

What this contract carries (project-soul.md): the trail's sentences are written in code from counts; every quote on a
job (a requirement, a line of evidence, the level) was checked in code against the posting or the profile; the fit is a
word worked out in code, never a model's score and never a percentage; every job links to the employer's own posting,
which the employer's board listed during the search.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/jobsearch.ts)

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field

from tayyari.contract.discover import (
    PLACE_NAME_MAX_CHARS,
    Cite,
    DiscoverWorkMode,
    Place,
    QuickFit,
    Salary,
)
from tayyari.contract.models import _Model

FoundVia = Literal["web", "tayyari"]
AiFitWord = Literal["strong", "good", "stretch"]
LevelWord = Literal["below", "at", "step_up", "unclear"]
TrailStage = Literal["plan", "search", "boards", "narrow", "read", "result"]

PROFILE_ID_MAX_CHARS: Final = 64


class TrailStep(_Model):
    """One line of "how we narrowed it down", written in code."""

    stage: TrailStage
    text: str
    count: int | None = None
    queries: list[str] | None = None


class AskMet(_Model):
    ask: str
    cite: Cite
    partly: bool


class AiJob(_Model):
    id: str
    title: str
    employer: str
    place: Place
    workMode: DiscoverWorkMode
    postedAt: str | None = None
    checkedAt: str
    applyUrl: str
    applyHost: str
    foundVia: FoundVia
    foundBy: str | None = None
    fit: AiFitWord
    level: LevelWord
    levelQuote: str | None = None
    yearsAsked: int | None = None
    met: list[AskMet]
    gaps: list[str]
    quickFit: QuickFit
    salary: Salary | None = None


class FunnelStep(_Model):
    label: str
    count: int


class JobSearchRequest(_Model):
    """``POST /api/jobsearch``. The city is checked against the places the backend knows by the route."""

    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    city: str | None = Field(default=None, max_length=PLACE_NAME_MAX_CHARS)


# ───────────────────────── stream events ─────────────────────────


class JobSearchStepEvent(_Model):
    type: Literal["step"] = "step"
    step: TrailStep


class JobSearchJobEvent(_Model):
    type: Literal["job"] = "job"
    job: AiJob


class JobSearchDoneEvent(_Model):
    type: Literal["done"] = "done"
    jobs: int
    funnel: list[FunnelStep]
    note: str | None = None


class JobSearchErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str


JobSearchEvent = Annotated[
    JobSearchStepEvent | JobSearchJobEvent | JobSearchDoneEvent | JobSearchErrorEvent,
    Field(discriminator="type"),
]
JOBSEARCH_TERMINAL: Final = (JobSearchDoneEvent, JobSearchErrorEvent)


__all__ = [
    "JOBSEARCH_TERMINAL",
    "AiFitWord",
    "AiJob",
    "AskMet",
    "FoundVia",
    "FunnelStep",
    "JobSearchDoneEvent",
    "JobSearchErrorEvent",
    "JobSearchEvent",
    "JobSearchJobEvent",
    "JobSearchRequest",
    "JobSearchStepEvent",
    "LevelWord",
    "TrailStage",
    "TrailStep",
]
