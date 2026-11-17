"""Pydantic mirror of shared/report.ts: "Where can I go next", the shareable report (N1, beta).

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional (``X | None = None``)
exactly when the TypeScript field is ``x?:``, and `tests/unit/contract/test_report_parity.py` fails when the two drift.

What the report carries (docs/roadmap.md N1): up to three roles the person can credibly move to, each with the resume lines
that back it (quoted verbatim, then redacted by the owner's choices) and the asks not shown yet, their skill shape, and the
near misses when the owner makes them public. Everything on it is worked out in code from the newest owned graph; no model
is called. Never a percentage, a rank, a percentile or a match score, and never anything that says why the person is looking.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/report.ts)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, field_validator

from tayyari.contract.models import _Model

ReportBlocked = Literal["sample", "anonymous"]

REPORT_ROLES_MAX: Final = 3
REPORT_QUOTES_PER_ROLE: Final = 2
REPORT_MISSING_MAX: Final = 3
REPORT_NEAR_MISSES_MAX: Final = 2
REPORT_AREAS_MAX: Final = 8
REPORT_QUOTE_MAX: Final = 240
"""A quoted resume line is clipped at a word boundary past this many characters."""
REPORT_HIDDEN_MAX: Final = 12
SLUG_LEN: Final = 12
PROFILE_ID_MAX_CHARS: Final = 64
QUOTE_ID_MAX_CHARS: Final = 16


class ReportQuote(_Model):
    id: str
    text: str
    source: str
    hidden: bool


class ReportRole(_Model):
    id: str
    title: str
    have: int
    total: int
    ready: bool
    eta: str
    quotes: list[ReportQuote]
    missing: list[str]


class ReportNearMiss(_Model):
    id: str
    title: str
    missing: list[str]


class ReportArea(_Model):
    name: str
    short: str
    score: int


class ReportPerson(_Model):
    name: str | None = None
    line: str


class ReportContent(_Model):
    person: ReportPerson
    areas: list[ReportArea]
    roles: list[ReportRole]
    nearMisses: list[ReportNearMiss]
    openToWork: bool


def _bounded_quote_ids(value: list[str]) -> list[str]:
    if any(len(v) > QUOTE_ID_MAX_CHARS for v in value):
        raise ValueError(f"a quote id is at most {QUOTE_ID_MAX_CHARS} characters")
    return value


class ReportChoices(_Model):
    showName: bool
    showOrgs: bool
    showQuotes: bool
    openToWork: bool
    showNearMisses: bool
    hiddenQuotes: list[str] = Field(max_length=REPORT_HIDDEN_MAX)

    _quote_ids = field_validator("hiddenQuotes")(_bounded_quote_ids)


class ReportView(_Model):
    choices: ReportChoices
    offered: ReportContent
    public: ReportContent
    orgsFound: int
    blocked: ReportBlocked | None = None
    offerUrl: str | None = None
    slug: str | None = None
    pageUrl: str | None = None
    ogUrl: str | None = None
    updatedAt: str | None = None


class ReportUpdate(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    shared: bool
    choices: ReportChoices


class ReportNewLink(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)


DEFAULT_CHOICES: Final = ReportChoices(
    showName=True,
    showOrgs=False,
    showQuotes=True,
    openToWork=False,
    showNearMisses=False,
    hiddenQuotes=[],
)
"""The choices of a report never saved: name and quotes on; employers, "open to work" and near misses off."""


__all__ = [
    "DEFAULT_CHOICES",
    "PROFILE_ID_MAX_CHARS",
    "QUOTE_ID_MAX_CHARS",
    "REPORT_AREAS_MAX",
    "REPORT_HIDDEN_MAX",
    "REPORT_MISSING_MAX",
    "REPORT_NEAR_MISSES_MAX",
    "REPORT_QUOTES_PER_ROLE",
    "REPORT_QUOTE_MAX",
    "REPORT_ROLES_MAX",
    "SLUG_LEN",
    "ReportArea",
    "ReportBlocked",
    "ReportChoices",
    "ReportContent",
    "ReportNearMiss",
    "ReportNewLink",
    "ReportPerson",
    "ReportQuote",
    "ReportRole",
    "ReportUpdate",
    "ReportView",
]
