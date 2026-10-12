"""Posting match: the postings read so far, the facts extracted per profile, what people said in their own words,
and the matches with their resume and cover note.

Matches, their facts and their variants are opaque JSON here (the API layer validates them with the contract and
the match models); postings, user facts and extracted facts are typed with the contract, because the match service
reads them through the Protocols in `match.ports` that these classes satisfy.

Every match read and write carries ``user_id``, so another account's match id is answered exactly like an unknown
one. Writes start the transaction with the write (an UPDATE that says whether it hit a row), as in `repos.users`.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from tayyari.contract.match import Fact, Posting
from tayyari.contract.portfolio import Fact as PortfolioFact
from tayyari.core.clock import Clock, SystemClock
from tayyari.core.logging import get_logger

from ..db import Database, dialect_insert
from ..ports import MatchListing, StoredMatch, StoredVariant, VariantKind
from ..tables import MatchRow, PostingRow, ProfileFactsRow, UserFactRow, VariantRow

log = get_logger(__name__)

_MATCH_COLUMNS = (
    MatchRow.id,
    MatchRow.user_id,
    MatchRow.profile_id,
    MatchRow.posting_hash,
    MatchRow.facts,
    MatchRow.result,
    MatchRow.band,
    MatchRow.blocked,
    MatchRow.version,
    MatchRow.created_at,
    MatchRow.updated_at,
)
_VARIANT_COLUMNS = (
    VariantRow.id,
    VariantRow.match_id,
    VariantRow.kind,
    VariantRow.spec,
    VariantRow.status,
    VariantRow.version,
    VariantRow.created_at,
    VariantRow.updated_at,
)


def _new_id() -> str:
    return secrets.token_hex(16)


def _match(row: Any) -> StoredMatch:
    return StoredMatch(
        id=row.id,
        user_id=row.user_id,
        profile_id=row.profile_id,
        posting_hash=row.posting_hash,
        facts=row.facts,
        result=row.result,
        band=row.band,
        blocked=row.blocked,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _variant(row: Any) -> StoredVariant:
    return StoredVariant(
        id=row.id,
        match_id=row.match_id,
        kind=cast("VariantKind", row.kind),
        spec=row.spec,
        status=row.status,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def user_fact_key(profile_id: str, fact_id: str) -> str:
    """The primary key of a user fact: a hash of ``(profile_id, fact id)``, so the same words are one row."""
    return hashlib.sha256(f"{profile_id}\x1f{fact_id}".encode()).hexdigest()[:32]


def posting_listing_fields(parsed: Any) -> tuple[str, str | None]:
    """The title and company of a stored posting's ``parsed`` JSON (a plain title when the document is odd)."""
    doc = parsed if isinstance(parsed, dict) else {}
    title = doc.get("title")
    company = doc.get("company")
    return (
        title if isinstance(title, str) and title.strip() else "Posting",
        company if isinstance(company, str) and company.strip() else None,
    )


class SqlPostingRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def get(self, posting_hash: str) -> Posting | None:
        stmt = select(PostingRow.text, PostingRow.parsed).where(PostingRow.hash == posting_hash)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        if row is None:
            return None
        try:
            return Posting.model_validate({**row.parsed, "rawText": row.text})
        except ValidationError:
            # A reading from an older shape of the contract: read the posting again rather than serve it.
            log.warning("posting_cache_invalid", posting_hash=posting_hash[:12])
            return None

    async def put(self, posting: Posting) -> None:
        stmt = (
            dialect_insert(self._db.dialect, PostingRow)
            .values(
                hash=posting.hash,
                text=posting.rawText,
                parsed=posting.model_dump(mode="json", exclude={"rawText"}, exclude_none=True),
                created_at=self._clock.now(),
            )
            .on_conflict_do_nothing(index_elements=[PostingRow.hash])
        )
        async with self._db.session() as s:
            await s.execute(stmt)


class SqlProfileFactsRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def get(self, profile_id: str, facts_hash: str) -> list[PortfolioFact] | None:
        stmt = select(ProfileFactsRow.facts).where(
            ProfileFactsRow.profile_id == profile_id, ProfileFactsRow.facts_hash == facts_hash
        )
        async with self._db.session() as s:
            raw = (await s.execute(stmt)).scalar_one_or_none()
        if not isinstance(raw, list):
            return None
        try:
            return [PortfolioFact.model_validate(f) for f in raw]
        except ValidationError:
            log.warning("profile_facts_cache_invalid")
            return None

    async def put(self, profile_id: str, facts_hash: str, facts: list[PortfolioFact]) -> None:
        values = {
            "facts_hash": facts_hash,
            "facts": [f.to_dict() for f in facts],
            "created_at": self._clock.now(),
        }
        stmt = dialect_insert(self._db.dialect, ProfileFactsRow).values(profile_id=profile_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[ProfileFactsRow.profile_id], set_=values)
        async with self._db.session() as s:
            await s.execute(stmt)


class SqlUserFactRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def list_for_profile(self, profile_id: str) -> list[Fact]:
        stmt = (
            select(UserFactRow.fact)
            .where(UserFactRow.profile_id == profile_id)
            .order_by(UserFactRow.created_at, UserFactRow.id)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        facts: list[Fact] = []
        for raw in rows:
            try:
                facts.append(Fact.model_validate(raw))
            except ValidationError:
                log.warning("user_fact_invalid")
        return facts

    async def add(
        self, user_id: str, profile_id: str, fact: Fact, *, question: str, answer: str, match_id: str | None
    ) -> bool:
        stmt = (
            dialect_insert(self._db.dialect, UserFactRow)
            .values(
                id=user_fact_key(profile_id, fact.id),
                user_id=user_id,
                profile_id=profile_id,
                match_id=match_id,
                question=question,
                answer=answer,
                fact=fact.model_dump(mode="json", exclude_none=True),
                created_at=self._clock.now(),
            )
            .on_conflict_do_nothing(index_elements=[UserFactRow.id])
            .returning(UserFactRow.id)
        )
        async with self._db.session() as s:
            return (await s.execute(stmt)).scalar_one_or_none() is not None


class SqlMatchRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def create(
        self,
        *,
        match_id: str,
        user_id: str,
        profile_id: str,
        posting_hash: str,
        facts: dict[str, Any],
        result: dict[str, Any],
        band: str,
        blocked: bool,
        version: int,
        now: datetime,
    ) -> StoredMatch:
        row = MatchRow(
            id=match_id,
            user_id=user_id,
            profile_id=profile_id,
            posting_hash=posting_hash,
            facts=facts,
            result=result,
            band=band,
            blocked=blocked,
            version=version,
            created_at=now,
            updated_at=now,
        )
        async with self._db.session() as s:
            s.add(row)
        return _match(row)

    async def get(self, match_id: str, user_id: str) -> StoredMatch | None:
        stmt = select(*_MATCH_COLUMNS).where(MatchRow.id == match_id, MatchRow.user_id == user_id)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _match(row)

    async def list_for_user(self, user_id: str, limit: int = 50) -> list[MatchListing]:
        stmt = (
            select(MatchRow.id, MatchRow.band, MatchRow.created_at, PostingRow.parsed)
            .join(PostingRow, PostingRow.hash == MatchRow.posting_hash)
            .where(MatchRow.user_id == user_id)
            .order_by(MatchRow.created_at.desc(), MatchRow.id)
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        out: list[MatchListing] = []
        for row in rows:
            title, company = posting_listing_fields(row.parsed)
            out.append(MatchListing(row.id, title, company, row.band, row.created_at))
        return out

    async def save_result(
        self,
        match_id: str,
        user_id: str,
        *,
        expected_version: int,
        facts: dict[str, Any],
        result: dict[str, Any],
        band: str,
        blocked: bool,
        version: int,
        now: datetime,
    ) -> bool:
        stmt = (
            update(MatchRow)
            .where(MatchRow.id == match_id, MatchRow.user_id == user_id, MatchRow.version == expected_version)
            .values(facts=facts, result=result, band=band, blocked=blocked, version=version, updated_at=now)
            .returning(MatchRow.id)
        )
        async with self._db.session() as s:
            return (await s.execute(stmt)).scalar_one_or_none() is not None

    async def save_facts(self, match_id: str, user_id: str, facts: dict[str, Any], now: datetime) -> bool:
        stmt = (
            update(MatchRow)
            .where(MatchRow.id == match_id, MatchRow.user_id == user_id)
            .values(facts=facts, updated_at=now)
            .returning(MatchRow.id)
        )
        async with self._db.session() as s:
            return (await s.execute(stmt)).scalar_one_or_none() is not None

    async def get_variant(self, match_id: str, kind: VariantKind) -> StoredVariant | None:
        stmt = select(*_VARIANT_COLUMNS).where(VariantRow.match_id == match_id, VariantRow.kind == kind)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _variant(row)

    async def put_variant(
        self, match_id: str, kind: VariantKind, *, spec: dict[str, Any], status: str, now: datetime
    ) -> StoredVariant:
        for attempt in (1, 2):
            try:
                async with self._db.session() as s:
                    changed = (
                        await s.execute(
                            update(VariantRow)
                            .where(VariantRow.match_id == match_id, VariantRow.kind == kind)
                            .values(spec=spec, status=status, version=VariantRow.version + 1, updated_at=now)
                            .returning(*_VARIANT_COLUMNS)
                        )
                    ).one_or_none()
                    if changed is not None:
                        return _variant(changed)
                    row = VariantRow(
                        id=_new_id(),
                        match_id=match_id,
                        kind=kind,
                        spec=spec,
                        status=status,
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                    s.add(row)
                return _variant(row)
            except IntegrityError:
                # A concurrent first save of the same variant won the unique key: the second attempt updates it.
                if attempt == 2:
                    raise
                log.info("variant_create_raced", kind=kind)
        raise AssertionError("unreachable")  # pragma: no cover


__all__ = [
    "SqlMatchRepo",
    "SqlPostingRepo",
    "SqlProfileFactsRepo",
    "SqlUserFactRepo",
    "posting_listing_fields",
    "user_fact_key",
]
