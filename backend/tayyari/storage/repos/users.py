"""Accounts: users, their sign-ins and emailed links, and the profiles they own.

Writes start a transaction with the write itself (an UPDATE that says whether it hit a row), never with a read
that is upgraded later: on SQLite that upgrade can fail at once with "database is locked". Where two requests can
race to create the same account (two first sign-ins at once), the unique constraints decide and the loser retries
through the update path.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from tayyari.core.clock import Clock, SystemClock
from tayyari.core.logging import get_logger

from ..db import Database, rowcount
from ..ports import (
    OwnedProfile,
    ProfileNotClaimable,
    ProfileOwnedByAnother,
    StoredAuthSession,
    StoredMagicLink,
    StoredUser,
)
from ..tables import (
    AuthSessionRow,
    MagicLinkRow,
    MatchRow,
    PortfolioRow,
    ProfileFactsRow,
    ProfileRow,
    UserFactRow,
    UserRow,
    VariantRow,
)

log = get_logger(__name__)

_USER_COLUMNS = (
    UserRow.id,
    UserRow.email,
    UserRow.name,
    UserRow.picture_url,
    UserRow.google_sub,
    UserRow.created_at,
    UserRow.last_login_at,
    UserRow.locale,
    UserRow.currency,
)
_LINK_COLUMNS = (
    MagicLinkRow.token_hash,
    MagicLinkRow.email,
    MagicLinkRow.created_at,
    MagicLinkRow.expires_at,
    MagicLinkRow.used_at,
    MagicLinkRow.ip_hash,
)


def _user(row: Any) -> StoredUser:
    return StoredUser(
        id=row.id,
        email=row.email,
        name=row.name,
        picture_url=row.picture_url,
        google_sub=row.google_sub,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
        locale=row.locale,
        currency=row.currency,
    )


def _link(row: Any) -> StoredMagicLink:
    return StoredMagicLink(
        token_hash=row.token_hash,
        email=row.email,
        created_at=row.created_at,
        expires_at=row.expires_at,
        used_at=row.used_at,
        ip_hash=row.ip_hash,
    )


def new_user_id() -> str:
    """A fresh account id: 128 random bits as 32 hex characters."""
    return secrets.token_hex(16)


class SqlUserRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    # ───────────────────────── accounts ─────────────────────────

    async def get(self, user_id: str) -> StoredUser | None:
        async with self._db.session() as s:
            row = (await s.execute(select(*_USER_COLUMNS).where(UserRow.id == user_id))).one_or_none()
        return None if row is None else _user(row)

    async def get_by_email(self, email: str) -> StoredUser | None:
        async with self._db.session() as s:
            row = (await s.execute(select(*_USER_COLUMNS).where(UserRow.email == email))).one_or_none()
        return None if row is None else _user(row)

    async def sign_in_google(
        self,
        *,
        sub: str,
        email: str,
        name: str | None,
        picture_url: str | None,
        locale: str | None,
        now: datetime,
    ) -> StoredUser:
        for attempt in (1, 2):
            try:
                async with self._db.session() as s:
                    # 1. the same Google account signing in again
                    by_sub = (
                        update(UserRow)
                        .where(UserRow.google_sub == sub)
                        .values(
                            last_login_at=now,
                            picture_url=picture_url,
                            name=func.coalesce(UserRow.name, name),
                            locale=func.coalesce(UserRow.locale, locale),
                        )
                        .returning(*_USER_COLUMNS)
                    )
                    row = (await s.execute(by_sub)).one_or_none()
                    if row is None:
                        # 2. an account made by emailed link: attach the Google identity (never replacing one)
                        by_email = (
                            update(UserRow)
                            .where(UserRow.email == email)
                            .values(
                                google_sub=func.coalesce(UserRow.google_sub, sub),
                                last_login_at=now,
                                picture_url=picture_url,
                                name=func.coalesce(UserRow.name, name),
                                locale=func.coalesce(UserRow.locale, locale),
                            )
                            .returning(*_USER_COLUMNS)
                        )
                        row = (await s.execute(by_email)).one_or_none()
                    if row is None:
                        # 3. a new account
                        new_id = new_user_id()
                        s.add(
                            UserRow(
                                id=new_id,
                                email=email,
                                name=name,
                                picture_url=picture_url,
                                google_sub=sub,
                                created_at=now,
                                last_login_at=now,
                                locale=locale,
                                currency=None,
                            )
                        )
                        await s.flush()
                        row = (await s.execute(select(*_USER_COLUMNS).where(UserRow.id == new_id))).one()
                    return _user(row)
            except IntegrityError:
                # A concurrent first sign-in created the account between our update and our insert.
                if attempt == 2:
                    raise
                log.info("user_create_raced", kind="google")
        raise AssertionError("unreachable")  # pragma: no cover

    async def sign_in_email(self, *, email: str, now: datetime) -> StoredUser:
        for attempt in (1, 2):
            try:
                async with self._db.session() as s:
                    stmt = (
                        update(UserRow)
                        .where(UserRow.email == email)
                        .values(last_login_at=now)
                        .returning(*_USER_COLUMNS)
                    )
                    row = (await s.execute(stmt)).one_or_none()
                    if row is None:
                        new_id = new_user_id()
                        s.add(
                            UserRow(
                                id=new_id,
                                email=email,
                                name=None,
                                picture_url=None,
                                google_sub=None,
                                created_at=now,
                                last_login_at=now,
                                locale=None,
                                currency=None,
                            )
                        )
                        await s.flush()
                        row = (await s.execute(select(*_USER_COLUMNS).where(UserRow.id == new_id))).one()
                    return _user(row)
            except IntegrityError:
                if attempt == 2:
                    raise
                log.info("user_create_raced", kind="email")
        raise AssertionError("unreachable")  # pragma: no cover

    async def update(
        self, user_id: str, *, name: str | None = None, currency: str | None = None
    ) -> StoredUser | None:
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if currency is not None:
            changes["currency"] = currency
        if not changes:
            return await self.get(user_id)
        stmt = update(UserRow).where(UserRow.id == user_id).values(**changes).returning(*_USER_COLUMNS)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _user(row)

    async def delete(self, user_id: str) -> bool:
        async with self._db.session() as s:
            owned = select(ProfileRow.id).where(ProfileRow.user_id == user_id)
            # Portfolios have no foreign key, so they are removed by owner and by profile.
            await s.execute(
                delete(PortfolioRow).where(
                    or_(PortfolioRow.user_id == user_id, PortfolioRow.profile_id.in_(owned))
                )
            )
            # Matches, what the person said in their own words, and the resumes and cover notes built for them: the
            # account's own, and any made on its profiles without an account that the claim did not reach (one that
            # was still running when the profile was claimed). They also follow the account and its profiles by
            # ON DELETE CASCADE; postings are shared and stay.
            their_matches = select(MatchRow.id).where(
                or_(MatchRow.user_id == user_id, MatchRow.profile_id.in_(owned))
            )
            await s.execute(delete(VariantRow).where(VariantRow.match_id.in_(their_matches)))
            await s.execute(
                delete(UserFactRow).where(
                    or_(UserFactRow.user_id == user_id, UserFactRow.profile_id.in_(owned))
                )
            )
            await s.execute(delete(ProfileFactsRow).where(ProfileFactsRow.profile_id.in_(owned)))
            await s.execute(
                delete(MatchRow).where(or_(MatchRow.user_id == user_id, MatchRow.profile_id.in_(owned)))
            )
            # Contexts and chats follow their profile (ON DELETE CASCADE).
            await s.execute(delete(ProfileRow).where(ProfileRow.user_id == user_id))
            email = (await s.execute(select(UserRow.email).where(UserRow.id == user_id))).scalar_one_or_none()
            if email is not None:
                await s.execute(delete(MagicLinkRow).where(MagicLinkRow.email == email))
            await s.execute(delete(AuthSessionRow).where(AuthSessionRow.user_id == user_id))
            return rowcount(await s.execute(delete(UserRow).where(UserRow.id == user_id))) > 0

    # ───────────────────────── sign-ins ─────────────────────────

    async def create_session(self, rec: StoredAuthSession) -> None:
        async with self._db.session() as s:
            s.add(
                AuthSessionRow(
                    id=rec.id,
                    user_id=rec.user_id,
                    created_at=rec.created_at,
                    expires_at=rec.expires_at,
                    last_seen_at=rec.last_seen_at,
                    ip_hash=rec.ip_hash,
                    user_agent=rec.user_agent,
                )
            )

    async def get_session(self, session_id: str) -> tuple[StoredAuthSession, StoredUser] | None:
        stmt = (
            select(
                AuthSessionRow.id.label("session_id"),
                AuthSessionRow.created_at.label("session_created_at"),
                AuthSessionRow.expires_at,
                AuthSessionRow.last_seen_at,
                AuthSessionRow.ip_hash,
                AuthSessionRow.user_agent,
                *_USER_COLUMNS,
            )
            .join(UserRow, UserRow.id == AuthSessionRow.user_id)
            .where(AuthSessionRow.id == session_id)
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        session = StoredAuthSession(
            id=row.session_id,
            user_id=row.id,
            created_at=row.session_created_at,
            expires_at=row.expires_at,
            last_seen_at=row.last_seen_at,
            ip_hash=row.ip_hash,
            user_agent=row.user_agent,
        )
        return session, _user(row)

    async def touch_session(self, session_id: str, *, last_seen_at: datetime, expires_at: datetime) -> None:
        stmt = (
            update(AuthSessionRow)
            .where(AuthSessionRow.id == session_id)
            .values(last_seen_at=last_seen_at, expires_at=expires_at)
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def delete_session(self, session_id: str) -> bool:
        async with self._db.session() as s:
            return (
                rowcount(await s.execute(delete(AuthSessionRow).where(AuthSessionRow.id == session_id))) > 0
            )

    # ───────────────────────── emailed links ─────────────────────────

    async def add_magic_link(self, rec: StoredMagicLink) -> None:
        async with self._db.session() as s:
            s.add(
                MagicLinkRow(
                    token_hash=rec.token_hash,
                    email=rec.email,
                    created_at=rec.created_at,
                    expires_at=rec.expires_at,
                    used_at=rec.used_at,
                    ip_hash=rec.ip_hash,
                )
            )

    async def consume_magic_link(self, token_hash: str, now: datetime) -> StoredMagicLink | None:
        stmt = (
            update(MagicLinkRow)
            .where(
                MagicLinkRow.token_hash == token_hash,
                MagicLinkRow.used_at.is_(None),
                MagicLinkRow.expires_at > now,
            )
            .values(used_at=now)
            .returning(*_LINK_COLUMNS)
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _link(row)

    # ───────────────────────── profiles ─────────────────────────

    async def list_profiles(self, user_id: str, limit: int = 20) -> list[OwnedProfile]:
        stmt = (
            select(ProfileRow.id, ProfileRow.created_at, ProfileRow.goal, ProfileRow.profile)
            .where(ProfileRow.user_id == user_id)
            .order_by(ProfileRow.created_at.desc(), ProfileRow.id)
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        out: list[OwnedProfile] = []
        for row in rows:
            person = row.profile.get("person") if isinstance(row.profile, dict) else None
            name = person.get("name") if isinstance(person, dict) else None
            out.append(
                OwnedProfile(
                    id=row.id,
                    created_at=row.created_at,
                    goal=row.goal,
                    person_name=name if isinstance(name, str) and name.strip() else None,
                )
            )
        return out

    async def claim_profile(self, user_id: str, profile_id: str) -> None:
        async with self._db.session() as s:
            claimed = await s.execute(
                update(ProfileRow)
                .where(
                    ProfileRow.id == profile_id,
                    ProfileRow.user_id.is_(None),
                    ProfileRow.is_sample.is_(False),
                )
                .values(user_id=user_id)
            )
            if rowcount(claimed) == 0:
                row = (
                    await s.execute(
                        select(ProfileRow.user_id, ProfileRow.is_sample).where(ProfileRow.id == profile_id)
                    )
                ).one_or_none()
                if row is None or row.is_sample:
                    raise ProfileNotClaimable(profile_id)
                if row.user_id != user_id:
                    raise ProfileOwnedByAnother(profile_id)
            # Its page (if one was generated) belongs to the same account; a page of another account stays put.
            await s.execute(
                update(PortfolioRow)
                .where(PortfolioRow.profile_id == profile_id, PortfolioRow.user_id.is_(None))
                .values(user_id=user_id)
            )
            # So do the posting matches and the answers made on it without an account (anonymous matching): one UPDATE
            # each. They are the account's from here on, and its history lists them.
            await s.execute(
                update(MatchRow)
                .where(MatchRow.profile_id == profile_id, MatchRow.user_id.is_(None))
                .values(user_id=user_id)
            )
            await s.execute(
                update(UserFactRow)
                .where(UserFactRow.profile_id == profile_id, UserFactRow.user_id.is_(None))
                .values(user_id=user_id)
            )

    async def purge_expired(self, now: datetime) -> int:
        removed = 0
        async with self._db.session() as s:
            removed += rowcount(
                await s.execute(delete(AuthSessionRow).where(AuthSessionRow.expires_at < now))
            )
            removed += rowcount(await s.execute(delete(MagicLinkRow).where(MagicLinkRow.expires_at < now)))
        return removed


__all__ = ["SqlUserRepo", "new_user_id"]
