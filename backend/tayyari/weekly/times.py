"""India time and the week an edition belongs to. India has no daylight saving, so a fixed +05:30 offset is exact (and needs
no tz database on a machine that has none)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Final

IST: Final = timezone(timedelta(hours=5, minutes=30), "IST")
SEND_HOUR: Final = 6
"""Editions are built from 06:00 India time on the reader's day."""
WEEKDAY: Final[dict[str, int]] = {"mon": 0, "fri": 4}
DAYS: Final = tuple(WEEKDAY)


def in_india(moment: datetime) -> datetime:
    """``moment`` on India's clock (a naive value is taken to be UTC)."""
    return (moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)).astimezone(IST)


def week_of(moment: datetime, day: str) -> date:
    """The Monday (``mon``) or Friday (``fri``) of the India week that contains ``moment``."""
    local = in_india(moment).date()
    monday = local - timedelta(days=local.weekday())
    return monday + timedelta(days=WEEKDAY[day])


def days_due(moment: datetime) -> list[str]:
    """The reader days that are being sent at ``moment``: today's weekday, once it is 06:00 in India."""
    local = in_india(moment)
    if local.hour < SEND_HOUR:
        return []
    return [day for day, weekday in WEEKDAY.items() if local.weekday() == weekday]


def iso_date(moment: datetime) -> str:
    """The India calendar date of ``moment`` as ``YYYY-MM-DD``."""
    return in_india(moment).date().isoformat()


__all__ = ["DAYS", "IST", "SEND_HOUR", "WEEKDAY", "days_due", "in_india", "iso_date", "week_of"]
