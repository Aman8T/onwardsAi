"""Pydantic mirror of shared/discover.ts: the small vocabulary Discover shares with Search with AI.

Discover is the board store behind Search with AI (`tayyari.jobsearch`): it reads employers' own public job boards and
knows the cities. What it puts on the wire is the city list (``GET /api/discover/cities``) and the pieces a found job
reuses: a place, a salary the employer states, a work mode, the quick fit and the evidence a reason cites.
`contract/jobsearch.py` imports them from here.

Same rules as `tayyari.contract.billing`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and
`tests/unit/contract/test_discover_parity.py` fails when the two drift.

The rules this contract carries (project-soul.md): no percentages anywhere; the quick fit is a word and counts
computed in code from the skill names a posting uses, never a model's score; a salary appears only when the employer
states it; distances are straight lines; the city a person looks in is chosen in the browser and is not stored by the
server. The names of cities and localities are checked against the places the backend knows by the routes that take
them, because the list of places lives in `tayyari.discover`, above this package in the layering.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/discover.ts)

from __future__ import annotations

from typing import Final, Literal

from tayyari.contract.models import _Model

DiscoverWorkMode = Literal["onsite", "hybrid", "remote", "unknown"]
PlacePrecision = Literal["locality", "city", "country"]
QuickFitWord = Literal["looks_close", "worth_a_look", "stretch", "cant_tell"]

PLACE_NAME_MAX_CHARS: Final = 80
"""The longest city or locality name a client may send (the longest of ours is 28 characters)."""


class Cite(_Model):
    """A fact a reason rests on: which source (``Resume``, ``GitHub``, a link label) and the evidence text."""

    source: str
    text: str


class Reason(_Model):
    text: str
    cite: Cite | None = None


class QuickFit(_Model):
    """The quick fit, in words: ``word`` and counts from the skill names the posting uses. Never a score."""

    word: QuickFitWord
    named: int
    matched: int
    notFound: list[str]
    yearsAsked: int | None = None
    yearsYou: float | None = None
    reasons: list[Reason]


class Place(_Model):
    """Where a listing is. ``km`` only when both ends are known to a locality."""

    label: str
    city: str | None = None
    locality: str | None = None
    precision: PlacePrecision
    km: float | None = None


class Salary(_Model):
    text: str


class DiscoverCity(_Model):
    name: str
    localities: list[str]


class DiscoverCitiesResponse(_Model):
    """``GET /api/discover/cities``."""

    cities: list[DiscoverCity]


__all__ = [
    "PLACE_NAME_MAX_CHARS",
    "Cite",
    "DiscoverCitiesResponse",
    "DiscoverCity",
    "DiscoverWorkMode",
    "Place",
    "PlacePrecision",
    "QuickFit",
    "QuickFitWord",
    "Reason",
    "Salary",
]
