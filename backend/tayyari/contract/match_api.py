"""The posting-match API's own shapes: request bodies, the NDJSON events of `POST /api/match` and
`POST /api/match/{id}/resume`, and the JSON answers that wrap the match contract.

`contract.match` (mirrored field for field by shared/match.ts) holds what a match IS; this module holds how the API
carries it. The web app reads these shapes through `src/match/api.ts` and `src/match/types.ts`.

    POST /api/match            body `MatchRequest`; NDJSON: `posting`, `gates`, `coverage` (one line per batch),
                               `radar`, `questions`, then `done` (or `error` at any point after the stream began)
    GET  /api/match/{id}       `MatchDetail`
    GET  /api/matches          `MatchList`
    POST /api/match/{id}/answers   body `AnswersRequest` -> `MatchDetail`
    POST /api/match/{id}/resume    NDJSON: `variant`, then `done`
    GET  /api/match/{id}/resume    `VariantResponse`;  PATCH (body `DecisionsRequest`) -> `VariantResponse`
    POST|GET /api/match/{id}/cover `CoverResponse`
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys the web app reads)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from tayyari.contract.match import (
    MAX_QUESTIONS,
    Coverage,
    CoverNote,
    DecisionChoice,
    Fact,
    GateAnswer,
    MatchBand,
    MatchResult,
    Posting,
    Question,
    Radar,
    ResumeVariant,
)
from tayyari.contract.models import _Model

ANSWER_MAX_CHARS: Final = 1500
"""The longest answer to a question (the web app's textarea has the same limit)."""
MAX_DECISIONS: Final = 40
"""The most decisions one PATCH of a resume may carry (a resume has far fewer open lines than this)."""
URL_MAX_CHARS: Final = 2048

MatchErrorCode = Literal[
    "unauthorized", "payment_required", "no_requirements", "export_blocked", "rate_limited", "failed"
]
"""The machine code of an `error` event; the web app decides what to show from it. ``failed`` is everything else."""

# ───────────────────────── requests ─────────────────────────


class MatchRequest(_Model):
    """``POST /api/match``. One of ``text`` (pasted) or ``url`` (a public page); ``text`` wins when both are given."""

    profileId: str
    text: str | None = None
    url: str | None = Field(default=None, max_length=URL_MAX_CHARS)


class AnswerItem(_Model):
    questionId: str = Field(min_length=1, max_length=64)
    text: str = Field(max_length=ANSWER_MAX_CHARS)


class AnswersRequest(_Model):
    """``POST /api/match/{id}/answers``: the person's own words for up to three open questions."""

    answers: list[AnswerItem] = Field(min_length=1, max_length=MAX_QUESTIONS)


class DecisionsRequest(_Model):
    """``PATCH /api/match/{id}/resume``: answers to the open lines of the resume."""

    decisions: list[DecisionChoice] = Field(min_length=1, max_length=MAX_DECISIONS)


# ───────────────────────── stream events ─────────────────────────


class MatchPostingEvent(_Model):
    type: Literal["posting"] = "posting"
    posting: Posting
    facts: list[Fact]
    """The person's evidence, so the rows that follow can show their citations as they arrive."""


class MatchGatesEvent(_Model):
    type: Literal["gates"] = "gates"
    gates: list[GateAnswer]
    blocked: bool


class MatchCoverageEvent(_Model):
    type: Literal["coverage"] = "coverage"
    coverage: list[Coverage]
    """One batch of checked requirements (at most ten); the stream sends several."""


class MatchRadarEvent(_Model):
    type: Literal["radar"] = "radar"
    radar: Radar


class MatchQuestionsEvent(_Model):
    type: Literal["questions"] = "questions"
    questions: list[Question]


class MatchDoneEvent(_Model):
    type: Literal["done"] = "done"
    matchId: str
    createdAt: str


class MatchErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str
    code: MatchErrorCode = "failed"


class ResumeVariantEvent(_Model):
    type: Literal["variant"] = "variant"
    variant: ResumeVariant


class ResumeDoneEvent(_Model):
    type: Literal["done"] = "done"


MatchStreamEvent = (
    MatchPostingEvent
    | MatchGatesEvent
    | MatchCoverageEvent
    | MatchRadarEvent
    | MatchQuestionsEvent
    | MatchDoneEvent
    | MatchErrorEvent
    | ResumeVariantEvent
    | ResumeDoneEvent
)
"""Every line the two match streams write."""

MATCH_TERMINAL: Final = (MatchDoneEvent, MatchErrorEvent, ResumeDoneEvent)
"""The events after which a stream has nothing more to say."""

# ───────────────────────── answers ─────────────────────────


class MatchDetail(_Model):
    """``GET /api/match/{id}`` (and the answer to posting answers): the stored match with the posting it was made
    from and the evidence its rows cite."""

    id: str
    createdAt: str
    posting: Posting
    facts: list[Fact]
    result: MatchResult


class MatchListItem(_Model):
    id: str
    title: str
    company: str | None = None
    band: MatchBand
    createdAt: str


class MatchList(_Model):
    matches: list[MatchListItem]


class VariantResponse(_Model):
    variant: ResumeVariant


class CoverResponse(_Model):
    cover: CoverNote


__all__ = [
    "ANSWER_MAX_CHARS",
    "MATCH_TERMINAL",
    "MAX_DECISIONS",
    "URL_MAX_CHARS",
    "AnswerItem",
    "AnswersRequest",
    "CoverResponse",
    "DecisionsRequest",
    "MatchCoverageEvent",
    "MatchDetail",
    "MatchDoneEvent",
    "MatchErrorCode",
    "MatchErrorEvent",
    "MatchGatesEvent",
    "MatchList",
    "MatchListItem",
    "MatchPostingEvent",
    "MatchQuestionsEvent",
    "MatchRadarEvent",
    "MatchRequest",
    "MatchStreamEvent",
    "ResumeDoneEvent",
    "ResumeVariantEvent",
    "VariantResponse",
]
