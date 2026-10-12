"""Retention: the periodic purge that keeps the database small and honours the privacy promises.

Run from the app lifespan (as a background task: it can touch many rows after a long idle spell). At most
one replica does the work per interval; the rest see the claim and return at once.

What is purged, with the windows from ``settings.retention``:

=====================  ===========================================================================
``profiles``           anonymous profiles idle for ``profile_days`` (contexts, chats and messages cascade);
                       a profile saved to an account (``user_id`` set) is kept
``sample_chats``       chat sessions of sample profiles idle for ``profile_days`` (messages cascade):
                       anyone can chat with the public sample persona, so what they type is user text
``sample_runs``        sample profiles (``is_sample``: "Try a sample profile" runs) idle for
                       ``profile_days``, except the ids in ``keep_profile_ids`` (the bundled persona)
``orphan_drafts``      unpublished portfolios updated > ``profile_days`` whose profile is gone
``state_payloads``     ``decision_states.payload`` (the resume text sent to Jev) after ``payload_days``
``jev_payloads``       ``jev_calls.questions`` and ``response`` emptied after ``payload_days``,
                       ``payload_purged_at`` set so replays skip them
``decision_log``       rows older than ``metadata_days``
``jev_calls``          rows older than ``metadata_days``
``llm_calls``          rows older than ``metadata_days``
``decision_states``    unreferenced states untouched for ``metadata_days``
``rate_limit_hits``    counters older than two days
``auth``               sign-ins (``auth_sessions``) and emailed links (``magic_links``) past their expiry
=====================  ===========================================================================

The permanent sample corpus is exempt from the payload and metadata steps: decision and Jev rows flagged
``is_sample`` that belong to no profile (CLI and eval runs) or to a kept profile, the states they reference,
and ``llm_calls`` of a kept profile. Rows of every other sample run follow the normal windows, and so do
the rows that hold what visitors type, even for the kept persona: the ``chat_gate`` decisions (the message
text is in their state) and ``chat`` LLM calls.

``spend_daily`` is tiny and kept forever.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import ColumnElement, and_, delete, exists, not_, or_, select, text, update

from tayyari.core.ids import SAMPLE_PREFIX
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings

from .db import Database, dialect_insert, rowcount
from .repos import build_repositories
from .tables import (
    ChatSessionRow,
    DecisionLogRow,
    DecisionStateRow,
    JevCallRow,
    KvRow,
    LlmCallRow,
    ProfileRow,
)

log = get_logger(__name__)

LAST_RUN_KEY = "maintenance:last_run"
MIN_INTERVAL = timedelta(hours=6)
HITS_RETENTION = timedelta(days=2)
ADVISORY_LOCK_KEY = 0x5441595941524932
"""``"TAYYARI2"``: the Postgres advisory lock that keeps replicas from purging at the same time."""
USER_TEXT_PACKS: Final = frozenset({"chat_gate"})
"""Decision packs whose state holds text a visitor typed: never part of the permanent sample corpus."""
USER_TEXT_PURPOSE: Final = "chat"
"""The ``llm_calls.purpose`` of a visitor's chat turn: never part of the permanent sample corpus."""


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


def _corpus(table: type[JevCallRow] | type[DecisionLogRow], keep: Collection[str]) -> ColumnElement[bool]:
    """Rows of the permanent sample corpus: flagged ``is_sample``, tied to no profile or to a kept one, and
    not made of visitor text. Never NULL, so ``not_(_corpus(...))`` is safe in a WHERE."""
    return and_(
        table.is_sample.is_(True),
        or_(table.profile_id.is_(None), table.profile_id.in_(keep)),
        table.pack.not_in(USER_TEXT_PACKS),
    )


async def _purge_sample_chats(db: Database, cutoff: datetime) -> int:
    """Delete chat sessions of sample profiles that saw no turn since ``cutoff`` (messages cascade).

    The kept sample persona is included: its profile is permanent, what visitors typed to it is not.
    """
    sample_profiles = select(ProfileRow.id).where(ProfileRow.is_sample.is_(True))
    stmt = delete(ChatSessionRow).where(
        ChatSessionRow.updated_at < cutoff,
        or_(
            ChatSessionRow.profile_id.in_(sample_profiles),
            ChatSessionRow.profile_id.startswith(SAMPLE_PREFIX),
        ),
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _purge_sample_runs(db: Database, cutoff: datetime, keep: Collection[str]) -> int:
    """Delete sample profiles ("Try a sample profile" runs) idle since ``cutoff``, except the ``keep`` ids."""
    stmt = delete(ProfileRow).where(
        ProfileRow.is_sample.is_(True), ProfileRow.last_accessed_at < cutoff, ProfileRow.id.not_in(keep)
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _purge_state_payloads(db: Database, cutoff: datetime, keep: Collection[str]) -> int:
    # A sample state is permanent only while a corpus call references it.
    corpus_ref = exists(
        select(1).where(JevCallRow.state_hash == DecisionStateRow.hash, _corpus(JevCallRow, keep))
    )
    stmt = (
        update(DecisionStateRow)
        .where(
            DecisionStateRow.payload.is_not(None),
            DecisionStateRow.last_ref_at < cutoff,
            not_(and_(DecisionStateRow.is_sample.is_(True), corpus_ref)),
        )
        .values(payload=None)
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _purge_jev_payloads(db: Database, cutoff: datetime, now: datetime, keep: Collection[str]) -> int:
    stmt = (
        update(JevCallRow)
        .where(
            JevCallRow.payload_purged_at.is_(None),
            JevCallRow.created_at < cutoff,
            not_(_corpus(JevCallRow, keep)),
        )
        .values(questions={}, response=None, payload_purged_at=now)
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _delete_decision_log(db: Database, cutoff: datetime, keep: Collection[str]) -> int:
    stmt = delete(DecisionLogRow).where(
        DecisionLogRow.created_at < cutoff, not_(_corpus(DecisionLogRow, keep))
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _delete_jev_calls(db: Database, cutoff: datetime, keep: Collection[str]) -> int:
    stmt = delete(JevCallRow).where(JevCallRow.created_at < cutoff, not_(_corpus(JevCallRow, keep)))
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _delete_llm_calls(db: Database, cutoff: datetime, keep: Collection[str]) -> int:
    # llm_calls has no is_sample column: only the calls of a kept profile are exempt (and not its chat turns).
    # Written as an OR of three non-null tests, because NOT over a NULL profile_id would match nothing.
    stmt = delete(LlmCallRow).where(
        LlmCallRow.created_at < cutoff,
        or_(
            LlmCallRow.profile_id.is_(None),
            LlmCallRow.profile_id.not_in(keep),
            LlmCallRow.purpose == USER_TEXT_PURPOSE,
        ),
    )
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def _delete_unreferenced_states(db: Database, cutoff: datetime) -> int:
    # Sample states too: once the calls of a purged sample run are gone nothing references their state, and
    # a state a corpus call still uses is referenced, so it stays.
    referenced = exists(select(1).where(JevCallRow.state_hash == DecisionStateRow.hash))
    stmt = delete(DecisionStateRow).where(DecisionStateRow.last_ref_at < cutoff, not_(referenced))
    async with db.session() as s:
        return rowcount(await s.execute(stmt))


async def run_maintenance(
    db: Database, settings: Settings, now: datetime, keep_profile_ids: Collection[str] = ()
) -> dict[str, int]:
    """Purge what has outlived its retention window; returns rows affected per step.

    ``keep_profile_ids`` are the profiles that are never purged even though they are samples (the bundled
    sample persona that "Try a sample profile" serves); what visitors typed to them is still purged.

    Returns ``{}`` when it did not run (the last run was under six hours ago, or another replica holds the
    lock). A step that fails is logged and counted in ``"errors"``; the other steps still run.
    """
    if not await _claim(db, now):
        log.debug("maintenance_skipped")
        return {}

    keep = frozenset({keep_profile_ids} if isinstance(keep_profile_ids, str) else keep_profile_ids)
    retention = settings.retention
    repos = build_repositories(db)
    profile_cutoff = now - timedelta(days=retention.profile_days)
    payload_cutoff = now - timedelta(days=retention.payload_days)
    metadata_cutoff = now - timedelta(days=retention.metadata_days)

    steps: list[tuple[str, Callable[[], Awaitable[int]]]] = [
        ("profiles", lambda: repos.profiles.purge_expired(profile_cutoff)),
        ("sample_chats", lambda: _purge_sample_chats(db, profile_cutoff)),
        ("sample_runs", lambda: _purge_sample_runs(db, profile_cutoff, keep)),
        ("orphan_drafts", lambda: repos.portfolios.purge_orphan_drafts(profile_cutoff)),
        ("state_payloads", lambda: _purge_state_payloads(db, payload_cutoff, keep)),
        ("jev_payloads", lambda: _purge_jev_payloads(db, payload_cutoff, now, keep)),
        ("decision_log", lambda: _delete_decision_log(db, metadata_cutoff, keep)),
        ("jev_calls", lambda: _delete_jev_calls(db, metadata_cutoff, keep)),
        ("llm_calls", lambda: _delete_llm_calls(db, metadata_cutoff, keep)),
        ("decision_states", lambda: _delete_unreferenced_states(db, metadata_cutoff)),
        ("rate_limit_hits", lambda: repos.limits.purge_hits(now - HITS_RETENTION)),
        ("auth", lambda: repos.users.purge_expired(now)),
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
