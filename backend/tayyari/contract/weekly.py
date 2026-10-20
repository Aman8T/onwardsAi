"""Pydantic mirror of shared/weekly.ts: the Leafout Weekly, a personal newspaper built every week.

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional (``X | None = None``)
exactly when the TypeScript field is ``x?:`` (absent on output, never ``null``), and
`tests/unit/contract/test_weekly_parity.py` fails when the two drift.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/weekly.ts)

from __future__ import annotations

from typing import Literal

from tayyari.contract.models import _Model

WeeklyDay = Literal["mon", "fri"]
WeeklySection = Literal["jobs", "progress", "news", "tip"]
WeeklyStatus = Literal["active", "paused", "off"]
WeeklyFit = Literal["looks_close", "worth_a_look", "stretch", "cant_tell"]


class WeeklySubscription(_Model):
    status: WeeklyStatus
    email: str | None = None
    day: WeeklyDay
    sections: list[WeeklySection]
    pausedUntil: str | None = None
    unread: int


class WeeklySubscriptionUpdate(_Model):
    status: WeeklyStatus
    day: WeeklyDay
    sections: list[WeeklySection]
    pauseDays: int | None = None


class WeeklyCite(_Model):
    source: str
    text: str


class WeeklyMet(_Model):
    ask: str
    cite: WeeklyCite


class WeeklyJob(_Model):
    listingId: str
    employer: str
    title: str
    place: str
    applyUrl: str
    fit: WeeklyFit
    met: list[WeeklyMet]
    notShown: list[str]
    note: str | None = None


class WeeklyProgress(_Model):
    planRole: str | None = None
    planWeek: int | None = None
    planWeeks: int | None = None
    ticksThisWeek: int | None = None
    ticksPlanned: int | None = None
    closestRole: str | None = None
    closestEta: str | None = None
    lastPractice: str | None = None
    nudge: str | None = None


class WeeklyNews(_Model):
    headline: str
    publication: str
    date: str | None = None
    url: str
    why: str
    skill: str


class WeeklyEdition(_Model):
    id: str
    issue: int
    weekOf: str
    headline: str
    reader: str
    place: str | None = None
    jobs: list[WeeklyJob]
    progress: WeeklyProgress | None = None
    news: list[WeeklyNews]
    tip: str | None = None
    quote: str | None = None
    createdAt: str
    emailedAt: str | None = None
    readAt: str | None = None


class WeeklyEditionSummary(_Model):
    id: str
    issue: int
    weekOf: str
    headline: str
    read: bool


class WeeklyEditionList(_Model):
    editions: list[WeeklyEditionSummary]


__all__ = [
    "WeeklyCite",
    "WeeklyDay",
    "WeeklyEdition",
    "WeeklyEditionList",
    "WeeklyEditionSummary",
    "WeeklyFit",
    "WeeklyJob",
    "WeeklyMet",
    "WeeklyNews",
    "WeeklyProgress",
    "WeeklySection",
    "WeeklyStatus",
    "WeeklySubscription",
    "WeeklySubscriptionUpdate",
]
