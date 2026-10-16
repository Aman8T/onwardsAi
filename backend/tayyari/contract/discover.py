"""Pydantic mirror of shared/discover.ts: personal, location-aware job discovery.

Same rules as `tayyari.contract.billing`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and
`tests/unit/contract/test_discover_parity.py` fails when the two drift.

The rules this contract carries (project-soul.md): no percentages anywhere; the quick fit is a word and counts
computed in code from the skill names a posting uses, never a model's score; a salary appears only when the employer
states it; every listing carries an apply link to the employer's own posting; distances are straight lines; the
person's area is chosen in the browser and is not stored by the server.

`DiscoverQuery` exists only here: it is the query string of ``GET /api/discover`` (the TypeScript file documents it in
the comment above `DiscoverResponse`), so it has no interface to mirror. The names of cities and localities are checked
against the places the backend knows by the route, because the list of places lives in `tayyari.discover`, above this
package in the layering.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/discover.ts)

from __future__ import annotations

from typing import Final, Literal, get_args

from pydantic import Field, field_validator
from pydantic_core import PydanticUseDefault

from tayyari.contract.models import _Model

DiscoverSource = Literal["greenhouse", "lever"]
DiscoverWorkMode = Literal["onsite", "hybrid", "remote", "unknown"]
PlacePrecision = Literal["locality", "city", "country"]
QuickFitWord = Literal["looks_close", "worth_a_look", "stretch", "cant_tell"]
DiscoverLane = Literal["near", "remote", "city"]
DiscoverReach = Literal["3", "5", "10", "25", "city"]
DiscoverBreadth = Literal["role", "next", "stretch"]
DiscoverOrder = Literal["ranked", "posted"]
GroupKind = Literal["next_role", "current", "skill_bridge", "first_steps", "other"]
SourceConfidence = Literal["employer"]
CityOrigin = Literal["resume", "you", "none"]

REACHES: Final[tuple[DiscoverReach, ...]] = get_args(DiscoverReach)
"""Every `DiscoverReach`, narrowest first (the order the lens shows them)."""
BREADTHS: Final[tuple[DiscoverBreadth, ...]] = get_args(DiscoverBreadth)
"""Every `DiscoverBreadth`, narrowest first."""
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


class ListingCard(_Model):
    id: str
    title: str
    employer: str
    employerId: str
    source: DiscoverSource
    place: Place
    workMode: DiscoverWorkMode
    postedAt: str | None = None
    firstSeenAt: str
    checkedAt: str
    freshness: str
    applyUrl: str
    applyHost: str
    confidence: SourceConfidence
    sourceLabel: str
    salary: Salary | None = None
    fit: QuickFit
    groupId: str
    alsoFits: list[str]


class Group(_Model):
    id: str
    kind: GroupKind
    title: str
    count: int
    reason: Reason
    listingIds: list[str]


class LensStop(_Model):
    value: str
    label: str
    count: int


class DiscoverArea(_Model):
    city: str | None = None
    cityFrom: CityOrigin
    resumeText: str | None = None
    locality: str | None = None


class CompanyLead(_Model):
    employerId: str
    name: str
    city: str | None = None
    reason: str
    careersUrl: str


class DiscoverCoverage(_Model):
    employers: int
    listings: int
    note: str | None = None


class LaneCounts(_Model):
    """The inline ``{ near; remote; city }`` object of `DiscoverResponse.laneCounts`."""

    near: int
    remote: int
    city: int


class DiscoverResponse(_Model):
    """``GET /api/discover``."""

    area: DiscoverArea
    lane: DiscoverLane
    reach: DiscoverReach
    breadth: DiscoverBreadth
    order: DiscoverOrder
    total: int
    reachStops: list[LensStop]
    breadthStops: list[LensStop]
    laneCounts: LaneCounts
    suggestion: str | None = None
    groups: list[Group]
    listings: list[ListingCard]
    companies: list[CompanyLead]
    coverage: DiscoverCoverage
    signals: list[str]
    refreshedAt: str | None = None


class DiscoverCity(_Model):
    name: str
    localities: list[str]


class DiscoverCitiesResponse(_Model):
    """``GET /api/discover/cities``."""

    cities: list[DiscoverCity]


class ListingDetail(_Model):
    """``GET /api/discover/listings/{id}``. ``postingText`` is what the web app sends to ``POST /api/match``."""

    card: ListingCard
    postingText: str


class DiscoverQuery(_Model):
    """The query string of ``GET /api/discover`` (Python only).

    ``profileId`` is required. ``city``, ``locality`` and ``otherCity`` are optional names from
    ``GET /api/discover/cities``: the person's area lives in the browser and is sent with each request, used for that
    response and never stored. The route checks the names against the places the backend knows. A blank value counts
    as not given.
    """

    profileId: str = Field(min_length=1, max_length=96)
    city: str | None = Field(default=None, max_length=PLACE_NAME_MAX_CHARS)
    locality: str | None = Field(default=None, max_length=PLACE_NAME_MAX_CHARS)
    otherCity: str | None = Field(default=None, max_length=PLACE_NAME_MAX_CHARS)
    reach: DiscoverReach = "city"
    breadth: DiscoverBreadth = "next"
    lane: DiscoverLane = "near"
    order: DiscoverOrder = "ranked"

    @field_validator("city", "locality", "otherCity", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("reach", "breadth", "lane", "order", mode="before")
    @classmethod
    def _blank_keeps_the_default(cls, v: object) -> object:
        """``?reach=`` (an empty value) means "not given", like every other optional parameter."""
        if isinstance(v, str) and not v.strip():
            raise PydanticUseDefault()
        return v


__all__ = [
    "BREADTHS",
    "PLACE_NAME_MAX_CHARS",
    "REACHES",
    "Cite",
    "CityOrigin",
    "CompanyLead",
    "DiscoverArea",
    "DiscoverBreadth",
    "DiscoverCitiesResponse",
    "DiscoverCity",
    "DiscoverCoverage",
    "DiscoverLane",
    "DiscoverOrder",
    "DiscoverQuery",
    "DiscoverReach",
    "DiscoverResponse",
    "DiscoverSource",
    "DiscoverWorkMode",
    "Group",
    "GroupKind",
    "LaneCounts",
    "LensStop",
    "ListingCard",
    "ListingDetail",
    "Place",
    "PlacePrecision",
    "QuickFit",
    "QuickFitWord",
    "Reason",
    "Salary",
    "SourceConfidence",
]
