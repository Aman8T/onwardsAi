"""Internal value types shared by the service's modules (the response models live in `contract.discover`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tayyari.contract.discover import GroupKind
from tayyari.storage.ports import DiscoverListing

from .quickfit import Fit


@dataclass(frozen=True, slots=True)
class Candidate:
    """A stored listing worked out for one person: its quick fit, the straight-line distance from their area (only when
    both ends are known to a locality), and the date its freshness counts from."""

    row: DiscoverListing
    tokens: frozenset[str]
    fit: Fit
    km: float | None
    age_from: datetime | None


@dataclass(frozen=True, slots=True)
class GroupDef:
    """A group a listing can live in. ``order`` sorts groups by precedence (kind, then rank within the kind)."""

    id: str
    kind: GroupKind
    title: str
    rank: int = 0
    skill_id: str | None = None
    """For a ``skill_bridge`` group: the skill the listings name and the profile does not show."""
    skill_name: str | None = None


KIND_ORDER: dict[GroupKind, int] = {
    "next_role": 0,
    "current": 1,
    "skill_bridge": 2,
    "first_steps": 3,
    "other": 4,
}
"""Precedence of the group kinds: the first kind that fits is a listing's home."""

FIT_ORDER = {"looks_close": 0, "worth_a_look": 1, "stretch": 2, "cant_tell": 3}
"""The order of the quick-fit words in the ranked list."""


__all__ = ["FIT_ORDER", "KIND_ORDER", "Candidate", "GroupDef"]
