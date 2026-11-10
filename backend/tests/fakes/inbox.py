"""A dict-backed `InboxRepo` that behaves like the SQL one (the repo contract tests run both).

What it keeps exact: one address per account and one holder per local part (a clash writes nothing), a repeated Resend id
writing nothing, the claim's compare-and-set (pending and a free lease) counting the attempt, newest-first lists, the daily
cap counting fetched (or claimed pending) rows, codes listed unused and unexpired newest first, used once, and purged once used or
expired. Every method yields to the event loop once, so concurrent tasks interleave as against a database.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tayyari.storage.ports import (
    FETCHED_STATUSES,
    INBOUND_EMAIL_COLUMNS,
    StoredInboundEmail,
    StoredInboxAddress,
    StoredVerificationCode,
)


def _at(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else _at(value)


class FakeInboxRepo:
    def __init__(self) -> None:
        self.addresses: dict[str, StoredInboxAddress] = {}
        self.emails: dict[str, StoredInboundEmail] = {}
        self.codes: dict[str, StoredVerificationCode] = {}
        self.fail_inserts: Exception | None = None
        """Set to make `insert_email` raise (a database that is down)."""

    # ── addresses ──

    async def get_address_for_user(self, user_id: str) -> StoredInboxAddress | None:
        await asyncio.sleep(0)
        return next((a for a in self.addresses.values() if a.user_id == user_id), None)

    async def get_address_by_local_part(self, local_part: str) -> StoredInboxAddress | None:
        await asyncio.sleep(0)
        wanted = local_part.lower()
        return next((a for a in self.addresses.values() if a.local_part == wanted), None)

    async def get_address(self, address_id: str) -> StoredInboxAddress | None:
        await asyncio.sleep(0)
        return self.addresses.get(address_id)

    async def create_address(self, item: StoredInboxAddress) -> StoredInboxAddress | None:
        await asyncio.sleep(0)
        local = item.local_part.lower()
        for row in self.addresses.values():
            if row.local_part == local or (item.user_id is not None and row.user_id == item.user_id):
                return None
        if item.id in self.addresses:
            return None
        stored = replace(
            item, local_part=local, consent_at=_at(item.consent_at), created_at=_at(item.created_at)
        )
        self.addresses[item.id] = stored
        return stored

    async def update_address(
        self, address_id: str, *, status: str | None = None, copies: bool | None = None
    ) -> StoredInboxAddress | None:
        await asyncio.sleep(0)
        row = self.addresses.get(address_id)
        if row is None:
            return None
        if status is not None:
            row = replace(row, status=status)
        if copies is not None:
            row = replace(row, copies=copies)
        self.addresses[address_id] = row
        return row

    def forget_user(self, user_id: str) -> None:
        """What deleting the account does in SQL: the address stays with no account, its emails and codes go."""
        for key, row in list(self.addresses.items()):
            if row.user_id == user_id:
                self.addresses[key] = replace(row, user_id=None)
        self.emails = {k: v for k, v in self.emails.items() if v.user_id != user_id}
        self.codes = {k: v for k, v in self.codes.items() if v.user_id != user_id}

    # ── received emails ──

    async def insert_email(self, item: StoredInboundEmail) -> bool:
        await asyncio.sleep(0)
        if self.fail_inserts is not None:
            raise self.fail_inserts
        if item.id in self.emails or any(
            e.provider_email_id == item.provider_email_id for e in self.emails.values()
        ):
            return False
        self.emails[item.id] = replace(
            item,
            received_at=_at(item.received_at),
            created_at=_at(item.created_at),
            claimed_until=_utc(item.claimed_until),
            processed_at=_utc(item.processed_at),
        )
        return True

    async def get_email(self, email_id: str) -> StoredInboundEmail | None:
        await asyncio.sleep(0)
        return self.emails.get(email_id)

    async def known_provider_ids(self, provider_ids: Collection[str]) -> set[str]:
        await asyncio.sleep(0)
        wanted = set(provider_ids)
        return {e.provider_email_id for e in self.emails.values() if e.provider_email_id in wanted}

    async def claim_email(
        self, email_id: str, *, now: datetime, until: datetime
    ) -> StoredInboundEmail | None:
        await asyncio.sleep(0)
        row = self.emails.get(email_id)
        if row is None or row.status != "pending":
            return None
        if row.claimed_until is not None and not row.claimed_until < _at(now):
            return None
        row = replace(row, claimed_until=_utc(until), attempts=row.attempts + 1)
        self.emails[email_id] = row
        return row

    async def update_email(self, email_id: str, changes: Mapping[str, Any]) -> None:
        await asyncio.sleep(0)
        unknown = set(changes) - INBOUND_EMAIL_COLUMNS
        if unknown:
            raise ValueError(f"not an inbound email column: {sorted(unknown)}")
        row = self.emails.get(email_id)
        if row is None or not changes:
            return
        values: dict[str, Any] = {k: (_at(v) if isinstance(v, datetime) else v) for k, v in changes.items()}
        self.emails[email_id] = replace(row, **values)

    async def list_emails(self, user_id: str, limit: int) -> list[StoredInboundEmail]:
        await asyncio.sleep(0)
        rows = [e for e in self.emails.values() if e.user_id == user_id]
        rows.sort(key=lambda e: (e.received_at, e.id), reverse=True)
        return rows[: max(limit, 0)]

    async def list_pending(self, *, before: datetime, now: datetime, limit: int) -> list[StoredInboundEmail]:
        await asyncio.sleep(0)
        at, cut = _at(now), _at(before)
        rows = [
            e
            for e in self.emails.values()
            if e.status == "pending"
            and e.received_at < cut
            and (e.claimed_until is None or e.claimed_until < at)
        ]
        rows.sort(key=lambda e: (e.received_at, e.id))
        return rows[: max(limit, 0)]

    async def count_fetched_since(
        self, address_id: str, since: datetime, *, exclude_id: str | None = None
    ) -> int:
        await asyncio.sleep(0)
        start = _at(since)
        return sum(
            1
            for e in self.emails.values()
            if e.address_id == address_id
            and e.received_at >= start
            and e.id != exclude_id
            and (e.status in FETCHED_STATUSES or (e.status == "pending" and e.attempts > 0))
        )

    # ── verification codes ──

    async def add_code(self, item: StoredVerificationCode) -> None:
        await asyncio.sleep(0)
        if item.id in self.codes:
            raise ValueError(f"code {item.id!r} exists")
        self.codes[item.id] = replace(
            item,
            received_at=_at(item.received_at),
            expires_at=_at(item.expires_at),
            used_at=_utc(item.used_at),
        )

    async def list_codes(
        self, user_id: str, *, since: datetime, now: datetime, limit: int = 20
    ) -> list[StoredVerificationCode]:
        await asyncio.sleep(0)
        start, at = _at(since), _at(now)
        rows = [
            c
            for c in self.codes.values()
            if c.user_id == user_id and c.received_at >= start and c.expires_at > at and c.used_at is None
        ]
        rows.sort(key=lambda c: (c.received_at, c.id), reverse=True)
        return rows[: max(limit, 0)]

    async def use_code(self, code_id: str, *, now: datetime) -> bool:
        await asyncio.sleep(0)
        row = self.codes.get(code_id)
        if row is None or row.used_at is not None:
            return False
        self.codes[code_id] = replace(row, used_at=_utc(now))
        return True

    async def purge_codes(self, now: datetime) -> int:
        await asyncio.sleep(0)
        at = _at(now)
        gone = [k for k, c in self.codes.items() if c.used_at is not None or c.expires_at <= at]
        for key in gone:
            del self.codes[key]
        return len(gone)


__all__ = ["FakeInboxRepo"]
