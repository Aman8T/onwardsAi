"""The application inbox (`inbox_addresses`, `inbound_emails`, `verification_codes`).

Inserts that may clash are ``INSERT ... ON CONFLICT DO NOTHING`` on both databases: an address whose account already has
one or whose local part is taken writes nothing (the service draws again), and a received email whose Resend id is stored
already writes nothing (a repeated webhook). A pending email is claimed by one compare-and-set, so two replicas never
process the same email at once.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update

from ..db import Database, dialect_insert, rowcount
from ..ports import (
    FETCHED_STATUSES,
    INBOUND_EMAIL_COLUMNS,
    StoredInboundEmail,
    StoredInboxAddress,
    StoredVerificationCode,
)
from ..tables import InboundEmailRow, InboxAddressRow, VerificationCodeRow


def _address(row: Any) -> StoredInboxAddress:
    return StoredInboxAddress(
        id=row.id,
        user_id=row.user_id,
        local_part=row.local_part,
        status=row.status,
        copies=bool(row.copies),
        consent_at=row.consent_at,
        consent_version=row.consent_version,
        created_at=row.created_at,
    )


def _email(row: Any) -> StoredInboundEmail:
    return StoredInboundEmail(
        id=row.id,
        provider_email_id=row.provider_email_id,
        received_at=row.received_at,
        status=row.status,
        sender=row.sender,
        sender_domain=row.sender_domain,
        subject=row.subject,
        created_at=row.created_at,
        recipient=row.recipient,
        address_id=row.address_id,
        user_id=row.user_id,
        message_id=row.message_id,
        stage=row.stage,
        application_id=row.application_id,
        quote=row.quote,
        copy_status=row.copy_status,
        attempts=int(row.attempts or 0),
        claimed_until=row.claimed_until,
        processed_at=row.processed_at,
    )


def _code(row: Any) -> StoredVerificationCode:
    return StoredVerificationCode(
        id=row.id,
        user_id=row.user_id,
        sender_domain=row.sender_domain,
        received_at=row.received_at,
        expires_at=row.expires_at,
        code=row.code,
        link=row.link,
        used_at=row.used_at,
    )


class SqlInboxRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ── addresses ──

    async def _one_address(self, *where: Any) -> StoredInboxAddress | None:
        async with self._db.session() as s:
            row = (await s.execute(select(InboxAddressRow).where(*where))).scalars().one_or_none()
        return None if row is None else _address(row)

    async def get_address_for_user(self, user_id: str) -> StoredInboxAddress | None:
        return await self._one_address(InboxAddressRow.user_id == user_id)

    async def get_address_by_local_part(self, local_part: str) -> StoredInboxAddress | None:
        return await self._one_address(InboxAddressRow.local_part == local_part.lower())

    async def get_address(self, address_id: str) -> StoredInboxAddress | None:
        return await self._one_address(InboxAddressRow.id == address_id)

    async def create_address(self, item: StoredInboxAddress) -> StoredInboxAddress | None:
        stmt = (
            dialect_insert(self._db.dialect, InboxAddressRow)
            .values(
                id=item.id,
                user_id=item.user_id,
                local_part=item.local_part.lower(),
                status=item.status,
                copies=item.copies,
                consent_at=item.consent_at,
                consent_version=item.consent_version,
                created_at=item.created_at,
            )
            .on_conflict_do_nothing()
        )
        async with self._db.session() as s:
            written = rowcount(await s.execute(stmt))
        return await self.get_address(item.id) if written else None

    async def update_address(
        self, address_id: str, *, status: str | None = None, copies: bool | None = None
    ) -> StoredInboxAddress | None:
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status
        if copies is not None:
            values["copies"] = copies
        if values:
            async with self._db.session() as s:
                await s.execute(
                    update(InboxAddressRow).where(InboxAddressRow.id == address_id).values(**values)
                )
        return await self.get_address(address_id)

    # ── received emails ──

    async def insert_email(self, item: StoredInboundEmail) -> bool:
        stmt = (
            dialect_insert(self._db.dialect, InboundEmailRow)
            .values(
                id=item.id,
                provider_email_id=item.provider_email_id,
                message_id=item.message_id,
                address_id=item.address_id,
                user_id=item.user_id,
                recipient=item.recipient,
                sender=item.sender,
                sender_domain=item.sender_domain,
                subject=item.subject,
                received_at=item.received_at,
                status=item.status,
                stage=item.stage,
                application_id=item.application_id,
                quote=item.quote,
                copy_status=item.copy_status,
                attempts=item.attempts,
                claimed_until=item.claimed_until,
                processed_at=item.processed_at,
                created_at=item.created_at,
            )
            .on_conflict_do_nothing(index_elements=[InboundEmailRow.provider_email_id])
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt)) > 0

    async def get_email(self, email_id: str) -> StoredInboundEmail | None:
        async with self._db.session() as s:
            row = (
                (await s.execute(select(InboundEmailRow).where(InboundEmailRow.id == email_id)))
                .scalars()
                .one_or_none()
            )
        return None if row is None else _email(row)

    async def known_provider_ids(self, provider_ids: Collection[str]) -> set[str]:
        wanted = list(dict.fromkeys(provider_ids))
        if not wanted:
            return set()
        stmt = select(InboundEmailRow.provider_email_id).where(InboundEmailRow.provider_email_id.in_(wanted))
        async with self._db.session() as s:
            return {str(v) for v in (await s.execute(stmt)).scalars()}

    async def claim_email(
        self, email_id: str, *, now: datetime, until: datetime
    ) -> StoredInboundEmail | None:
        stmt = (
            update(InboundEmailRow)
            .where(
                InboundEmailRow.id == email_id,
                InboundEmailRow.status == "pending",
                or_(InboundEmailRow.claimed_until.is_(None), InboundEmailRow.claimed_until < now),
            )
            .values(claimed_until=until, attempts=InboundEmailRow.attempts + 1)
        )
        async with self._db.session() as s:
            claimed = rowcount(await s.execute(stmt)) > 0
        return await self.get_email(email_id) if claimed else None

    async def update_email(self, email_id: str, changes: Mapping[str, Any]) -> None:
        unknown = set(changes) - INBOUND_EMAIL_COLUMNS
        if unknown:
            raise ValueError(f"not an inbound email column: {sorted(unknown)}")
        if not changes:
            return
        async with self._db.session() as s:
            await s.execute(
                update(InboundEmailRow).where(InboundEmailRow.id == email_id).values(**dict(changes))
            )

    async def list_emails(self, user_id: str, limit: int) -> list[StoredInboundEmail]:
        stmt = (
            select(InboundEmailRow)
            .where(InboundEmailRow.user_id == user_id)
            .order_by(InboundEmailRow.received_at.desc(), InboundEmailRow.id.desc())
            .limit(max(limit, 0))
        )
        async with self._db.session() as s:
            return [_email(row) for row in (await s.execute(stmt)).scalars()]

    async def list_pending(self, *, before: datetime, now: datetime, limit: int) -> list[StoredInboundEmail]:
        stmt = (
            select(InboundEmailRow)
            .where(
                InboundEmailRow.status == "pending",
                InboundEmailRow.received_at < before,
                or_(InboundEmailRow.claimed_until.is_(None), InboundEmailRow.claimed_until < now),
            )
            .order_by(InboundEmailRow.received_at, InboundEmailRow.id)
            .limit(max(limit, 0))
        )
        async with self._db.session() as s:
            return [_email(row) for row in (await s.execute(stmt)).scalars()]

    async def count_fetched_since(
        self, address_id: str, since: datetime, *, exclude_id: str | None = None
    ) -> int:
        stmt = select(func.count()).where(
            InboundEmailRow.address_id == address_id,
            InboundEmailRow.received_at >= since,
            or_(
                InboundEmailRow.status.in_(sorted(FETCHED_STATUSES)),
                and_(InboundEmailRow.status == "pending", InboundEmailRow.attempts > 0),
            ),
        )
        if exclude_id is not None:
            stmt = stmt.where(InboundEmailRow.id != exclude_id)
        async with self._db.session() as s:
            return int((await s.execute(stmt)).scalar_one())

    # ── verification codes ──

    async def add_code(self, item: StoredVerificationCode) -> None:
        async with self._db.session() as s:
            s.add(
                VerificationCodeRow(
                    id=item.id,
                    user_id=item.user_id,
                    sender_domain=item.sender_domain,
                    code=item.code,
                    link=item.link,
                    received_at=item.received_at,
                    expires_at=item.expires_at,
                    used_at=item.used_at,
                )
            )

    async def list_codes(
        self, user_id: str, *, since: datetime, now: datetime, limit: int = 20
    ) -> list[StoredVerificationCode]:
        stmt = (
            select(VerificationCodeRow)
            .where(
                VerificationCodeRow.user_id == user_id,
                VerificationCodeRow.received_at >= since,
                VerificationCodeRow.expires_at > now,
                VerificationCodeRow.used_at.is_(None),
            )
            .order_by(VerificationCodeRow.received_at.desc(), VerificationCodeRow.id.desc())
            .limit(max(limit, 0))
        )
        async with self._db.session() as s:
            return [_code(row) for row in (await s.execute(stmt)).scalars()]

    async def use_code(self, code_id: str, *, now: datetime) -> bool:
        stmt = (
            update(VerificationCodeRow)
            .where(VerificationCodeRow.id == code_id, VerificationCodeRow.used_at.is_(None))
            .values(used_at=now)
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt)) > 0

    async def purge_codes(self, now: datetime) -> int:
        stmt = delete(VerificationCodeRow).where(
            or_(VerificationCodeRow.used_at.is_not(None), VerificationCodeRow.expires_at <= now)
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))


__all__ = ["SqlInboxRepo"]
