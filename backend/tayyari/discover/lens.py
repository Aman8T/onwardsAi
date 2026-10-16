"""The lenses: how far, how wide, and in what order. Pure functions over candidates; no state.

**How far** (the near lane only). ``3``, ``5``, ``10`` and ``25`` keep the listings whose straight-line distance from the
person's area is at most that many kilometres; a distance exists only when the area and the listing are both known to a
locality, so without a locality those stops count zero. ``city`` keeps every listing in the area's city and is the default.

**How wide.**

    role      listings that fit the work the person does now (or, with no current role, one of their next roles)
    next      every listing except a stretch, and except one that fits nowhere and cannot be told
    stretch   everything

A stretch listing, or one outside every group that cannot be told, is therefore shown only at "stretch".

**Order.** ``ranked``: the quick-fit word (looks close, worth a look, a stretch, can't tell), then group precedence, then
distance (unknown last), then how recently the employer listed it (newer first), then title. ``posted``: newest first and
nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Final

from tayyari.contract.discover import DiscoverBreadth, DiscoverReach, LensStop

from .groups import Arrangement
from .models import FIT_ORDER, KIND_ORDER, Candidate

REACH_LABELS: Final[dict[str, str]] = {
    "3": "3 km",
    "5": "5 km",
    "10": "10 km",
    "25": "25 km",
    "city": "Whole city",
}
BREADTH_LABELS: Final[dict[str, str]] = {
    "role": "Your role",
    "next": "+ next steps",
    "stretch": "+ stretch",
}
REACH_ORDER: Final[tuple[DiscoverReach, ...]] = ("3", "5", "10", "25", "city")
BREADTH_ORDER: Final[tuple[DiscoverBreadth, ...]] = ("role", "next", "stretch")


def near_pool(city_candidates: Sequence[Candidate], reach: DiscoverReach) -> list[Candidate]:
    """The listings of the area's city within ``reach`` (every one for ``city``)."""
    if reach == "city":
        return list(city_candidates)
    limit = float(reach)
    return [c for c in city_candidates if c.km is not None and c.km <= limit]


def is_visible(
    candidate: Candidate, arrangement: Arrangement, breadth: DiscoverBreadth, *, has_current_role: bool
) -> bool:
    word = candidate.fit.word
    if breadth == "stretch":
        return True
    if word == "stretch":
        return False
    if breadth == "role":
        kinds = arrangement.kinds(candidate.row.id)
        return "current" in kinds if has_current_role else "next_role" in kinds
    home = arrangement.home[candidate.row.id]
    return home.kind != "other" or word != "cant_tell"


def visible(
    pool: Sequence[Candidate], arrangement: Arrangement, breadth: DiscoverBreadth, *, has_current_role: bool
) -> list[Candidate]:
    return [c for c in pool if is_visible(c, arrangement, breadth, has_current_role=has_current_role)]


def stops(order: Sequence[str], labels: dict[str, str], count_of: Callable[[str], int]) -> list[LensStop]:
    """One `LensStop` per value, with the count the person would see there."""
    return [LensStop(value=value, label=labels[value], count=count_of(value)) for value in order]


def _timestamp(moment: datetime | None) -> float:
    return moment.timestamp() if moment is not None else float("-inf")


def ranked_key(
    candidate: Candidate, arrangement: Arrangement
) -> tuple[int, int, int, float, float, str, str]:
    home = arrangement.home[candidate.row.id]
    return (
        FIT_ORDER[candidate.fit.word],
        KIND_ORDER[home.kind],
        home.rank,
        candidate.km if candidate.km is not None else float("inf"),
        -_timestamp(candidate.age_from),
        candidate.row.title.lower(),
        candidate.row.id,
    )


def posted_key(candidate: Candidate) -> tuple[float, str, str]:
    return (-_timestamp(candidate.age_from), candidate.row.title.lower(), candidate.row.id)


def order_candidates(items: Sequence[Candidate], arrangement: Arrangement, order: str) -> list[Candidate]:
    """``items`` in the requested order (see the module docstring)."""
    if order == "posted":
        return sorted(items, key=posted_key)
    return sorted(items, key=lambda c: ranked_key(c, arrangement))


def km_text(reach: str) -> str:
    """How a reach reads in the suggestion line: ``25 km`` or ``the whole city``."""
    return "the whole city" if reach == "city" else f"{reach} km"


__all__ = [
    "BREADTH_LABELS",
    "BREADTH_ORDER",
    "REACH_LABELS",
    "REACH_ORDER",
    "is_visible",
    "km_text",
    "near_pool",
    "order_candidates",
    "posted_key",
    "ranked_key",
    "stops",
    "visible",
]
