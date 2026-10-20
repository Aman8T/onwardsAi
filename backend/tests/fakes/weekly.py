"""A dict-backed `WeeklyRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: an edition is unique per reader and week (`create_edition` answers False on a clash), another reader's
edition id reads as nothing, `due_subscriptions` and `unsent_editions` apply the same eligibility (the reader's day, active or
paused-and-past), `mark_emailed` and `mark_read` keep the first time, and `claim` hands the right out once per ``hold`` and
counts the attempts of a week. Reads hand out copies.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tayyari.storage.ports import StoredWeeklyEdition, StoredWeeklySubscription


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class FakeWeeklyRepo:
    def __init__(self) -> None:
        self.subscriptions: dict[str, StoredWeeklySubscription] = {}
        self.editions: dict[str, StoredWeeklyEdition] = {}
        self.claims: dict[str, tuple[str, int, datetime]] = {}
        self.paid: dict[date, int] = {}

    # subscriptions

    async def get_subscription(self, user_id: str) -> StoredWeeklySubscription | None:
        found = self.subscriptions.get(user_id)
        return None if found is None else copy.deepcopy(found)

    async def get_subscription_by_token(self, token: str) -> StoredWeeklySubscription | None:
        for item in self.subscriptions.values():
            if item.unsubscribe_token == token:
                return copy.deepcopy(item)
        return None

    async def save_subscription(self, item: StoredWeeklySubscription) -> None:
        for other in self.subscriptions.values():
            if other.unsubscribe_token == item.unsubscribe_token and other.user_id != item.user_id:
                raise ValueError("duplicate unsubscribe token")
        before = self.subscriptions.get(item.user_id)
        created = _utc(before.created_at) if before is not None else _utc(item.created_at)
        self.subscriptions[item.user_id] = copy.deepcopy(
            replace(
                item,
                created_at=created,
                updated_at=_utc(item.updated_at),
                paused_until=None if item.paused_until is None else _utc(item.paused_until),
            )
        )

    async def unsubscribe_by_token(self, token: str, now: datetime) -> bool:
        for user_id, item in self.subscriptions.items():
            if item.unsubscribe_token == token:
                self.subscriptions[user_id] = replace(
                    item, status="off", paused_until=None, updated_at=_utc(now)
                )
                return True
        return False

    def _running(self, item: StoredWeeklySubscription, day: str, now: datetime) -> bool:
        if item.day != day:
            return False
        if item.status == "active":
            return True
        return (
            item.status == "paused" and item.paused_until is not None and _utc(item.paused_until) <= _utc(now)
        )

    def _edition_of(self, user_id: str, week_of: date) -> StoredWeeklyEdition | None:
        return next(
            (e for e in self.editions.values() if e.user_id == user_id and e.week_of == week_of), None
        )

    async def due_subscriptions(
        self, *, day: str, week_of: date, now: datetime, limit: int
    ) -> list[StoredWeeklySubscription]:
        out = [
            item
            for item in sorted(self.subscriptions.values(), key=lambda i: i.user_id)
            if self._running(item, day, now) and self._edition_of(item.user_id, week_of) is None
        ]
        return [copy.deepcopy(item) for item in out[:limit]]

    async def unsent_editions(
        self, *, day: str, week_of: date, now: datetime, limit: int
    ) -> list[tuple[StoredWeeklySubscription, StoredWeeklyEdition]]:
        out: list[tuple[StoredWeeklySubscription, StoredWeeklyEdition]] = []
        for item in sorted(self.subscriptions.values(), key=lambda i: i.user_id):
            edition = self._edition_of(item.user_id, week_of)
            if (
                self._running(item, day, now)
                and item.email is not None
                and edition is not None
                and edition.emailed_at is None
            ):
                out.append((copy.deepcopy(item), copy.deepcopy(edition)))
        return out[:limit]

    # editions

    async def create_edition(self, item: StoredWeeklyEdition) -> bool:
        if self._edition_of(item.user_id, item.week_of) is not None or item.id in self.editions:
            return False
        self.editions[item.id] = copy.deepcopy(replace(item, created_at=_utc(item.created_at)))
        return True

    async def get_edition(self, user_id: str, edition_id: str) -> StoredWeeklyEdition | None:
        found = self.editions.get(edition_id)
        return copy.deepcopy(found) if found is not None and found.user_id == user_id else None

    async def edition_for_week(self, user_id: str, week_of: date) -> StoredWeeklyEdition | None:
        found = self._edition_of(user_id, week_of)
        return None if found is None else copy.deepcopy(found)

    async def list_editions(self, user_id: str, limit: int) -> list[StoredWeeklyEdition]:
        mine = [e for e in self.editions.values() if e.user_id == user_id]
        mine.sort(key=lambda e: (e.week_of, e.issue), reverse=True)
        return [copy.deepcopy(e) for e in mine[:limit]]

    async def next_issue(self, user_id: str) -> int:
        return max((e.issue for e in self.editions.values() if e.user_id == user_id), default=0) + 1

    async def mark_emailed(self, user_id: str, edition_id: str, now: datetime) -> None:
        found = self.editions.get(edition_id)
        if found is not None and found.user_id == user_id and found.emailed_at is None:
            self.editions[edition_id] = replace(found, emailed_at=_utc(now))

    async def mark_read(self, user_id: str, edition_id: str, now: datetime) -> None:
        found = self.editions.get(edition_id)
        if found is not None and found.user_id == user_id and found.read_at is None:
            self.editions[edition_id] = replace(found, read_at=_utc(now))

    async def unread_count(self, user_id: str) -> int:
        return sum(1 for e in self.editions.values() if e.user_id == user_id and e.read_at is None)

    # claims

    async def claim(
        self, key: str, week_of: date, *, now: datetime, hold: timedelta, max_tries: int
    ) -> int | None:
        week = week_of.isoformat()
        held: Any = self.claims.get(key)
        tries = held[1] if held is not None and held[0] == week else 0
        if tries >= max_tries:
            return None
        if held is not None and _utc(held[2]) > _utc(now) - hold:
            return None
        self.claims[key] = (week, tries + 1, _utc(now))
        return tries + 1

    async def paid_this_week(self, week_of: date) -> int:
        return self.paid.get(week_of, 0)

    async def add_paid(self, week_of: date, now: datetime) -> None:
        self.paid[week_of] = self.paid.get(week_of, 0) + 1


__all__ = ["FakeWeeklyRepo"]
