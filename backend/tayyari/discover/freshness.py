"""How recent a listing is, in the words the card uses.

``ageFrom`` is the date the employer gave (Lever ``createdAt``, Greenhouse ``first_published``) when there is one, else
the day we first saw the listing, but only when that was after the very first refresh: everything the first refresh found
was first "seen" in that run, which says nothing about its age. With neither, the age is unknown and the card says only
that the listing is on the employer's board now.

The words (no percentages, and never a word that accuses an employer of anything):

    up to 14 days   Posted today · Posted yesterday · Posted N days ago
    15 to 45 days   Open N weeks
    46 to 90 days   Worth checking it's still hiring
    over 90 days    Open over 3 months
    unknown         On the employer's board now
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

RECENT_DAYS: Final = 14
OPEN_DAYS: Final = 45
CHECK_DAYS: Final = 90
UNKNOWN_FRESHNESS: Final = "On the employer's board now"
WORTH_CHECKING: Final = "Worth checking it's still hiring"
OVER_THREE_MONTHS: Final = "Open over 3 months"


def age_from(
    posted_at: datetime | None, first_seen_at: datetime, first_run_ends_at: datetime | None
) -> datetime | None:
    """The moment freshness counts from, or None when it cannot be told.

    ``first_run_ends_at`` is when the very first refresh finished (None while there has never been one, which makes
    every first-seen date unusable). A first-seen date counts only when it is later than that.
    """
    if posted_at is not None:
        return posted_at
    if first_run_ends_at is not None and first_seen_at > first_run_ends_at:
        return first_seen_at
    return None


def age_days(now: datetime, since: datetime) -> int:
    """Whole days from ``since`` to ``now`` (0 for anything in the future)."""
    return max(0, (now - since).days)


def freshness_label(now: datetime, since: datetime | None) -> str:
    """The card's freshness line for a listing whose age counts from ``since`` (see the module docstring)."""
    if since is None:
        return UNKNOWN_FRESHNESS
    days = age_days(now, since)
    if days <= RECENT_DAYS:
        if days == 0:
            return "Posted today"
        if days == 1:
            return "Posted yesterday"
        return f"Posted {days} days ago"
    if days <= OPEN_DAYS:
        return f"Open {days // 7} weeks"
    if days <= CHECK_DAYS:
        return WORTH_CHECKING
    return OVER_THREE_MONTHS


def checked_label(now: datetime, checked_at: datetime) -> str:
    """``Employer's own listing, checked today`` · ``... yesterday`` · ``... 3 days ago``."""
    days = age_days(now, checked_at)
    if days == 0:
        when = "today"
    elif days == 1:
        when = "yesterday"
    else:
        when = f"{days} days ago"
    return f"Employer's own listing, checked {when}"


def first_run_end(started_at: datetime, finished_at: datetime | None, stale_after: timedelta) -> datetime:
    """When the first refresh can be called over: when it finished, else when it would count as abandoned."""
    return finished_at if finished_at is not None else started_at + stale_after


__all__ = [
    "CHECK_DAYS",
    "OPEN_DAYS",
    "OVER_THREE_MONTHS",
    "RECENT_DAYS",
    "UNKNOWN_FRESHNESS",
    "WORTH_CHECKING",
    "age_days",
    "age_from",
    "checked_label",
    "first_run_end",
    "freshness_label",
]
