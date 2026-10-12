"""Retention for accounts: `run_maintenance` removes expired sign-ins and links, and keeps profiles that belong to an
account while it still deletes idle anonymous ones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from tayyari.contract.stored import StoredContext
from tayyari.core.settings import Settings
from tayyari.storage.db import Database
from tayyari.storage.maintenance import run_maintenance
from tayyari.storage.ports import StoredAuthSession, StoredMagicLink
from tayyari.storage.repos import build_repositories
from tayyari.storage.tables import AuthSessionRow, MagicLinkRow, ProfileRow, UserRow

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
DAY = timedelta(days=1)


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return self.at.timestamp()

    async def sleep(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


async def rows(db: Database, table: type) -> int:
    async with db.session() as s:
        return int((await s.execute(select(func.count()).select_from(table))).scalar_one())


async def test_the_purge_removes_expired_sign_ins_and_links_and_nothing_else(
    any_real_db: Database, settings: Settings
) -> None:
    db = any_real_db
    clock = _Clock(NOW - 40 * DAY)
    repos = build_repositories(db, clock)
    user = await repos.users.sign_in_email(email="ana@example.com", now=clock.now())
    old, fresh = NOW - 40 * DAY, NOW
    await repos.users.create_session(
        StoredAuthSession("a" * 64, user.id, old, old + 30 * DAY, old, None, None)  # expired 10 days ago
    )
    await repos.users.create_session(
        StoredAuthSession("b" * 64, user.id, fresh, fresh + 30 * DAY, fresh, None, None)
    )
    await repos.users.add_magic_link(
        StoredMagicLink("c" * 64, "ana@example.com", old, old + timedelta(minutes=15), None, None)
    )
    await repos.users.add_magic_link(
        StoredMagicLink("d" * 64, "ana@example.com", fresh, fresh + timedelta(minutes=15), None, None)
    )

    counts = await run_maintenance(db, settings, NOW)

    assert counts["auth"] == 2 and counts["errors"] == 0
    assert await rows(db, AuthSessionRow) == 1 and await rows(db, MagicLinkRow) == 1
    assert await rows(db, UserRow) == 1  # the account itself is never purged


async def test_an_account_keeps_its_profile_through_the_idle_purge(
    any_real_db: Database, settings: Settings
) -> None:
    db = any_real_db
    clock = _Clock(NOW - 70 * DAY)  # saved 70 days ago, idle since: past the 60-day window
    repos = build_repositories(db, clock)
    user = await repos.users.sign_in_email(email="ana@example.com", now=clock.now())
    for pid in ("mine", "anonymous"):
        await repos.profiles.save(
            pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
        )
    await repos.users.claim_profile(user.id, "mine")

    counts = await run_maintenance(db, settings, NOW)

    assert counts["profiles"] == 1
    async with db.session() as s:
        left = set((await s.execute(select(ProfileRow.id))).scalars().all())
    assert left == {"mine"}
