"""The retention run deletes the application inbox's verification codes once used or expired, and leaves fresh ones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tayyari.core.settings import Settings
from tayyari.storage.db import Database
from tayyari.storage.maintenance import run_maintenance
from tayyari.storage.ports import StoredVerificationCode
from tayyari.storage.repos import build_repositories

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


async def test_used_and_expired_codes_are_purged(any_real_db: Database, settings: Settings) -> None:
    repos = build_repositories(any_real_db)
    user = await repos.users.sign_in_email(email="riya@example.com", now=NOW)

    def code(code_id: str, received: datetime) -> StoredVerificationCode:
        return StoredVerificationCode(
            id=code_id, user_id=user.id, sender_domain="greenhouse.io", received_at=received,
            expires_at=received + timedelta(minutes=15), code="482913",
        )  # fmt: skip

    await repos.inbox.add_code(code("expired", NOW - timedelta(hours=1)))
    await repos.inbox.add_code(code("used", NOW - timedelta(minutes=1)))
    await repos.inbox.add_code(code("fresh", NOW - timedelta(minutes=1)))
    assert await repos.inbox.use_code("used", now=NOW)

    counts = await run_maintenance(any_real_db, settings, NOW)

    assert counts["inbox_codes"] == 2 and counts["errors"] == 0
    left = await repos.inbox.list_codes(user.id, since=NOW - timedelta(days=1), now=NOW)
    assert [c.id for c in left] == ["fresh"]
