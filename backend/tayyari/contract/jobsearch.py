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
ReferralPart = Literal["intro", "fit", "draw"]

PROFILE_ID_MAX_CHARS: Final = 64
RUN_ID_MAX_CHARS: Final = 96
AVOID_MAX_ITEMS: Final = 200


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


class AskCounts(_Model):
    """How the read's checked requirements came out (the card's pips): counts, never a score."""

    met: int
    partly: int
    notShown: int


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
    asks: AskCounts
    openLong: bool | None = None
    quickFit: QuickFit
    salary: Salary | None = None


class FunnelStep(_Model):
    label: str
    count: int


class AvoidPrefs(_Model):
    """What "Not for me" taught the search (kept in the browser, sent with each search)."""

    jobIds: list[str] = Field(max_length=AVOID_MAX_ITEMS)
    employers: list[str] = Field(max_length=AVOID_MAX_ITEMS)
    noStepUp: bool


class JobSearchRequest(_Model):
    """``POST /api/jobsearch``. The city is checked against the places the backend knows by the route."""

    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    city: str | None = Field(default=None, max_length=PLACE_NAME_MAX_CHARS)
    remote: bool | None = None
    avoid: AvoidPrefs | None = None


class JobSearchRun(_Model):
    """``GET /api/jobsearch/latest``: the last finished search for a profile, city and remote choice."""

    runId: str
    createdAt: str
    city: str | None = None
    remote: bool
    jobs: list[AiJob]
    funnel: list[FunnelStep]
    trail: list[TrailStep]
    note: str | None = None


class JobPosting(_Model):
    """The posting's text as the search read it, for "See how you fit" (sent to ``POST /api/match`` as ``text``)."""

    jobId: str
    title: str
    employer: str
    applyUrl: str
    postingText: str


class RoleRequest(_Model):
    """``POST /api/jobsearch/prepare`` and ``/referral``. ``fresh`` asks for a new draft."""

    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    runId: str = Field(max_length=RUN_ID_MAX_CHARS)
    jobId: str = Field(max_length=RUN_ID_MAX_CHARS)
    fresh: bool | None = None


class PrepLead(_Model):
    ask: str
    cite: Cite
    partly: bool


class PrepGap(_Model):
    gap: str
    nearest: Cite | None = None
    step: str


class StoryPart(_Model):
    label: str
    cite: Cite | None = None
    placeholder: str | None = None


class PrepQuestion(_Model):
    question: str
    ask: str
    story: list[StoryPart]


class RolePrep(_Model):
    """The prep page for one role: what to lead with, the gaps and a small step for each, likely questions with stories
    built from evidence. Every quote is checked in code; a step is a suggestion, never a claim about the person."""

    jobId: str
    title: str
    employer: str
    lead: list[PrepLead]
    gaps: list[PrepGap]
    questions: list[PrepQuestion]
    createdAt: str


class ReferralLine(_Model):
    """One sentence of the note: who the person is (``intro``), why they fit what the employer needs (``fit``), why the
    employer is where they want to go next (``draw``). ``sources`` say where its words come from, for the key under it."""

    part: ReferralPart
    text: str
    sources: list[str]


class ReferralNote(_Model):
    """A short note asking a connection for a referral: three checked sentences that read as one paragraph, the ask and
    the link. The web app puts the greeting ("Hi [name],") in front."""

    jobId: str
    lines: list[ReferralLine]
    ask: str
    link: str
    createdAt: str


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
    runId: str
    createdAt: str


class JobSearchErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str


JobSearchEvent = Annotated[
    JobSearchStepEvent | JobSearchJobEvent | JobSearchDoneEvent | JobSearchErrorEvent,
    Field(discriminator="type"),
]
JOBSEARCH_TERMINAL: Final = (JobSearchDoneEvent, JobSearchErrorEvent)


__all__ = [
    "AVOID_MAX_ITEMS",
    "JOBSEARCH_TERMINAL",
    "RUN_ID_MAX_CHARS",
    "AiFitWord",
    "AiJob",
    "AskCounts",
    "AskMet",
    "AvoidPrefs",
    "FoundVia",
    "FunnelStep",
    "JobPosting",
    "JobSearchDoneEvent",
    "JobSearchErrorEvent",
    "JobSearchEvent",
    "JobSearchJobEvent",
    "JobSearchRequest",
    "JobSearchRun",
    "JobSearchStepEvent",
    "LevelWord",
    "PrepGap",
    "PrepLead",
    "PrepQuestion",
    "ReferralLine",
    "ReferralNote",
    "ReferralPart",
    "RolePrep",
    "RoleRequest",
    "StoryPart",
    "TrailStage",
    "TrailStep",
]
