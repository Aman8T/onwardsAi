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
``discover_closed``    Discover listings closed more than 30 days ago (`CLOSED_LISTING_DAYS`)
``jobsearch``          searches with AI, prep pages and referral notes older than 30 days (`JOBSEARCH_RETENTION`)
``interview``          practice interviews (transcript, work and debrief) older than ``settings.interview.retention_days``
                       (30 by default, `INTERVIEW_RETENTION`)
``apply_filled``       on finished applications (`ports.FILLED_TERMINAL_STAGES`) untouched for
                       ``settings.apply.screenshot_days``: both screenshots, the review, the needs and the typed values
                       (`ApplyRepo.purge_filled`); the stage, digests, times and history stay
``apply_holds``        not a purge but a reconcile: an application still marked ``credit_state = 'held'`` once it was
                       sent (or moved on from sent) has its hold captured, and one that failed, closed or was cancelled
                       has it released; ``credit_state`` then says what the ledger says. Both calls are idempotent, so
                       this only repairs a capture or release that failed at the time
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
from .repos import Repositories, build_repositories
from .tables import (
    ApplicationRow,
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
CLOSED_LISTING_RETENTION = timedelta(days=30)
"""A Discover listing the employer's board stopped listing is deleted this long after it was closed."""
JOBSEARCH_RETENTION: Final = timedelta(days=30)
"""Searches with AI, and the prep pages and referral notes written for their roles, are deleted after this long."""
INTERVIEW_RETENTION: Final = timedelta(days=30)
"""How long a practice interview (its transcript, work and debrief) is kept: the default of
``settings.interview.retention_days``, which is what the purge reads."""
ADVISORY_LOCK_KEY = 0x5441595941524932
"""``"TAYYARI2"``: the Postgres advisory lock that keeps replicas from purging at the same time."""
USER_TEXT_PACKS: Final = frozenset({"chat_gate"})
"""Decision packs whose state holds text a visitor typed: never part of the permanent sample corpus."""
USER_TEXT_PURPOSE: Final = "chat"
"""The ``llm_calls.purpose`` of a visitor's chat turn: never part of the permanent sample corpus."""
HOLD_CAPTURE_STAGES: Final = frozenset({"sent", "viewed", "interviewing", "offer", "rejected"})
"""An application in one of these stages was sent: a hold still open on it is captured."""
HOLD_RELEASE_STAGES: Final = frozenset({"failed", "closed", "cancelled"})
"""An application in one of these stages was not sent: a hold still open on it is released."""
HOLD_SWEEP_LIMIT: Final = 500
"""Applications reconciled per run (the next run takes the rest)."""


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


async def _reconcile_holds(db: Database, repos: Repositories, now: datetime) -> int:
    """Capture or release the credit holds still open on finished applications; how many applications changed.

    Each application is its own unit: one that fails is logged and left ``held`` for the next run.
    """
    stmt = (
        select(ApplicationRow.id, ApplicationRow.user_id, ApplicationRow.stage, ApplicationRow.credit_ref)
        .where(
            ApplicationRow.credit_state == "held",
            ApplicationRow.user_id.is_not(None),
            ApplicationRow.stage.in_(sorted(HOLD_CAPTURE_STAGES | HOLD_RELEASE_STAGES)),
        )
        .order_by(ApplicationRow.updated_at.asc(), ApplicationRow.id.asc())
        .limit(HOLD_SWEEP_LIMIT)
    )
    async with db.session() as s:
        rows = (await s.execute(stmt)).all()
    changed = 0
    for row in rows:
        user_id: str = row.user_id
        ref = row.credit_ref or f"apply:{row.id}"
        try:
            if row.stage in HOLD_CAPTURE_STAGES:
                await repos.billing.capture(user_id, ref, now=now)
            else:
                await repos.billing.release(user_id, ref, now=now)
            state = await repos.billing.hold_state(user_id, ref)
            if state == "held":
                continue
            if row.stage in HOLD_CAPTURE_STAGES and state != "charged":
                # the application went to the employer but its hold is gone: it will never be charged
                log.error("apply_sent_without_hold", application_id=row.id, state=state)
            async with db.session() as s:
                done = await s.execute(
                    update(ApplicationRow)
                    .where(
                        ApplicationRow.id == row.id,
                        ApplicationRow.credit_state == "held",
                        ApplicationRow.stage
                        == row.stage,  # a row reopened meanwhile keeps its new hold's state
                    )
                    .values(credit_state=state)
                )
            changed += rowcount(done)
        except Exception:
            log.error("apply_hold_reconcile_failed", application_id=row.id, exc_info=True)
    return changed


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
    interview_retention = timedelta(days=settings.interview.retention_days)
    filled_cutoff = now - timedelta(days=settings.apply.screenshot_days)

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
        ("discover_closed", lambda: repos.discover.purge_closed(now - CLOSED_LISTING_RETENTION)),
        ("jobsearch", lambda: repos.jobsearch.purge(now - JOBSEARCH_RETENTION)),
        ("interview", lambda: repos.interview.purge(now - interview_retention)),
        ("apply_filled", lambda: repos.apply.purge_filled(filled_cutoff)),
        ("apply_holds", lambda: _reconcile_holds(db, repos, now)),
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


__all__ = [
    "ADVISORY_LOCK_KEY",
    "CLOSED_LISTING_RETENTION",
    "HITS_RETENTION",
    "HOLD_CAPTURE_STAGES",
    "HOLD_RELEASE_STAGES",
    "HOLD_SWEEP_LIMIT",
    "INTERVIEW_RETENTION",
    "JOBSEARCH_RETENTION",
    "LAST_RUN_KEY",
    "MIN_INTERVAL",
    "run_maintenance",
]
