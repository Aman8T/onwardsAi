"""Retention: the periodic purge that keeps the database small and honours the privacy promises.

Run from the app lifespan (as a background task: it can touch many rows after a long idle spell). At most
one replica does the work per interval; the rest see the claim and return at once.

What is purged, with the windows from ``settings.retention`` (sample rows are never touched: they are
fictional and form the permanent eval corpus):

=====================  ===========================================================================
``profiles``           profiles idle for ``profile_days`` (contexts, chats and messages cascade)
``state_payloads``     ``decision_states.payload`` (the resume text sent to Jev) after ``payload_days``
``jev_payloads``       ``jev_calls.questions`` and ``response`` emptied after ``payload_days``,
                       ``payload_purged_at`` set so replays skip them
``decision_log``       rows older than ``metadata_days``
``jev_calls``          rows older than ``metadata_days``
``llm_calls``          rows older than ``metadata_days`` (calls of ``sample-`` profiles are kept)
``decision_states``    unreferenced states untouched for ``metadata_days``
``rate_limit_hits``    counters older than two days
=====================  ===========================================================================

``spend_daily`` is tiny and kept forever.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, not_, or_, select, text, update

from tayyari.core.ids import SAMPLE_PREFIX
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings

from .db import Database, dialect_insert, rowcount
from .repos import build_repositories
from .tables import DecisionLogRow, DecisionStateRow, JevCallRow, KvRow, LlmCallRow

log = get_logger(__name__)

LAST_RUN_KEY = "maintenance:last_run"
MIN_INTERVAL = timedelta(hours=6)
HITS_RETENTION = timedelta(days=2)
ADVISORY_LOCK_KEY = 0x5441595941524932
"""``"TAYYARI2"``: the Postgres advisory lock that keeps replicas from purging at the same time."""


async def _claim(db: Database, now: datetime) -> bool:
    """Win the right to run: true for exactly one caller per interval, across replicas.

    The claim is a conditional upsert of ``kv["maintenance:last_run"]``: it only writes (and so only
    returns a row) when the stored time is at least ``MIN_INTERVAL`` old. On Postgres a try-lock taken in the
    same transaction makes concurrent claimers back off without even waiting for the row.
    """
    async with db.session() as s:
        if db.dialect == "postgresql":
            locked = (
                await s.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
            ).scalar_one()
            if not locked:
                return False
        insert_stmt = dialect_insert(db.dialect, KvRow).values(
            key=LAST_RUN_KEY, value=now.isoformat(), updated_at=now
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[KvRow.key],
            set_={"value": insert_stmt.excluded.value, "updated_at": insert_stmt.excluded.updated_at},
            where=KvRow.updated_at <= now - MIN_INTERVAL,
        ).returning(KvRow.key)
        return (await s.execute(stmt)).scalar_one_or_none() is not None


async def _purge_state_payloads(db: Database, cutoff: datetime) -> int:
    stmt = (
        update(DecisionStateRow)
        .where(
            DecisionStateRow.payload.is_not(None),
            DecisionStateRow.last_ref_at < cutoff,
            DecisionStateRow.is_sample.is_(False),
        )
        .values(payload=None)
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _purge_jev_payloads(db: Database, cutoff: datetime, now: datetime) -> int:
    stmt = (
        update(JevCallRow)
        .where(
            JevCallRow.payload_purged_at.is_(None),
            JevCallRow.created_at < cutoff,
            JevCallRow.is_sample.is_(False),
        )
        .values(questions={}, response=None, payload_purged_at=now)
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _delete_decision_log(db: Database, cutoff: datetime) -> int:
    stmt = delete(DecisionLogRow).where(
        DecisionLogRow.created_at < cutoff, DecisionLogRow.is_sample.is_(False)
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _delete_jev_calls(db: Database, cutoff: datetime) -> int:
    stmt = delete(JevCallRow).where(JevCallRow.created_at < cutoff, JevCallRow.is_sample.is_(False))
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _delete_llm_calls(db: Database, cutoff: datetime) -> int:
    # llm_calls has no is_sample column: calls made for sample profiles are recognised by the id prefix.
    stmt = delete(LlmCallRow).where(
        LlmCallRow.created_at < cutoff,
        or_(LlmCallRow.profile_id.is_(None), not_(LlmCallRow.profile_id.startswith(SAMPLE_PREFIX))),
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _delete_unreferenced_states(db: Database, cutoff: datetime) -> int:
    referenced = exists(select(1).where(JevCallRow.state_hash == DecisionStateRow.hash))
    stmt = delete(DecisionStateRow).where(
        DecisionStateRow.last_ref_at < cutoff, DecisionStateRow.is_sample.is_(False), not_(referenced)
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def run_maintenance(db: Database, settings: Settings, now: datetime) -> dict[str, int]:
    """Purge what has outlived its retention window; returns rows affected per step.

    Returns ``{}`` when it did not run (the last run was under six hours ago, or another replica holds the
    lock). A step that fails is logged and counted in ``"errors"``; the other steps still run.
    """
    if not await _claim(db, now):
        log.debug("maintenance_skipped")
        return {}

    retention = settings.retention
    repos = build_repositories(db)
    profile_cutoff = now - timedelta(days=retention.profile_days)
    payload_cutoff = now - timedelta(days=retention.payload_days)
    metadata_cutoff = now - timedelta(days=retention.metadata_days)

    steps: list[tuple[str, Callable[[], Awaitable[int]]]] = [
        ("profiles", lambda: repos.profiles.purge_expired(profile_cutoff)),
        ("state_payloads", lambda: _purge_state_payloads(db, payload_cutoff)),
        ("jev_payloads", lambda: _purge_jev_payloads(db, payload_cutoff, now)),
        ("decision_log", lambda: _delete_decision_log(db, metadata_cutoff)),
        ("jev_calls", lambda: _delete_jev_calls(db, metadata_cutoff)),
        ("llm_calls", lambda: _delete_llm_calls(db, metadata_cutoff)),
        ("decision_states", lambda: _delete_unreferenced_states(db, metadata_cutoff)),
        ("rate_limit_hits", lambda: repos.limits.purge_hits(now - HITS_RETENTION)),
    ]
    counts: dict[str, int] = {}
    errors = 0
    for name, step in steps:
        try:
            counts[name] = await step()
        except Exception:
            errors += 1
            counts[name] = 0
            log.error("maintenance_step_failed", step=name, exc_info=True)
    counts["errors"] = errors
    log.info("maintenance_done", **counts)
    return counts


__all__ = ["ADVISORY_LOCK_KEY", "HITS_RETENTION", "LAST_RUN_KEY", "MIN_INTERVAL", "run_maintenance"]
