"""Retention: what `run_maintenance` purges, what it keeps, and how often it runs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from tayyari.contract.records import DecisionRecord, JevCallRecord, LlmCallRecord, StateBlob
from tayyari.contract.stored import StoredContext
from tayyari.core.settings import RetentionSettings, Settings
from tayyari.storage import maintenance
from tayyari.storage.db import Database
from tayyari.storage.maintenance import LAST_RUN_KEY, run_maintenance
from tayyari.storage.repos import Repositories, build_repositories
from tayyari.storage.tables import (
    ChatSessionRow,
    DecisionLogRow,
    DecisionStateRow,
    JevCallRow,
    LlmCallRow,
    ProfileContextRow,
    ProfileRow,
    RateLimitHitRow,
    SpendDailyRow,
)

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
DAY = timedelta(days=1)


async def count(db: Database, row: type) -> int:
    async with db.session() as s:
        return (await s.execute(select(func.count()).select_from(row))).scalar_one()


async def ids(db: Database, column: object) -> set[object]:
    async with db.session() as s:
        return set((await s.execute(select(column))).scalars().all())  # type: ignore[call-overload]


async def seed_profile(repos: Repositories, pid: str, *, sample: bool = False) -> str:
    await repos.profiles.save(pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=sample)
    session = await repos.sessions.create(pid, None, None)
    await repos.sessions.append_turn(session.id, user="q", assistant="a", tools=[], llm_call_id=None)
    return session.id


async def test_profiles_idle_past_the_window_are_deleted_with_their_chats(
    any_real_db: Database, settings: Settings
) -> None:
    db = any_real_db
    clock = _Clock(NOW - 70 * DAY)
    repos = build_repositories(db, clock)
    old_session = await seed_profile(repos, "old")
    await seed_profile(repos, "old-sample", sample=True)
    clock.now_value = NOW - 10 * DAY
    await seed_profile(repos, "recent")

    counts = await run_maintenance(db, settings, NOW)

    assert counts["profiles"] == 1 and counts["errors"] == 0
    assert await ids(db, ProfileRow.id) == {"old-sample", "recent"}
    assert await count(db, ProfileContextRow) == 2
    assert old_session not in await ids(db, ChatSessionRow.id)
    assert await count(db, ChatSessionRow) == 2


async def test_payloads_are_purged_after_thirty_days_but_metadata_stays(
    any_real_db: Database,
    settings: Settings,
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_state: Callable[..., StateBlob],
) -> None:
    db = any_real_db
    repos = build_repositories(db, _Clock(NOW - 40 * DAY))
    old_state = make_state({"resume": "old resume"})
    sample_state = make_state({"resume": "sample resume"}, is_sample=True)
    old_call = make_jev_call(created_at=NOW - 40 * DAY, state_hash=old_state.hash, is_sample=False)
    sample_call = make_jev_call(created_at=NOW - 40 * DAY, state_hash=sample_state.hash, is_sample=True)
    await repos.decisions.record_batch(
        [old_state, sample_state],
        [old_call, sample_call],
        [make_decision(created_at=NOW - 40 * DAY, call_id=old_call.id)],
    )
    fresh_repos = build_repositories(db, _Clock(NOW - 2 * DAY))
    fresh_state = make_state({"resume": "fresh resume"})
    fresh_call = make_jev_call(created_at=NOW - 2 * DAY, state_hash=fresh_state.hash)
    await fresh_repos.decisions.record_batch([fresh_state], [fresh_call], [])

    counts = await run_maintenance(db, settings, NOW)

    assert counts["state_payloads"] == 1
    assert counts["jev_payloads"] == 1
    assert counts["jev_calls"] == 0 and counts["decision_log"] == 0  # metadata is kept for 180 days
    async with db.session() as s:
        calls = {r.id: r for r in (await s.execute(select(JevCallRow))).scalars()}
        states = {r.hash: r for r in (await s.execute(select(DecisionStateRow))).scalars()}
    purged = calls[old_call.id]
    assert purged.questions == {} and purged.response is None
    assert purged.payload_purged_at is not None
    assert purged.pack == "first_read" and purged.cost_micro_usd == old_call.cost_micro_usd  # metadata intact
    assert calls[sample_call.id].questions == sample_call.questions  # sample rows are never purged
    assert calls[sample_call.id].payload_purged_at is None
    assert calls[fresh_call.id].questions == fresh_call.questions
    assert states[old_state.hash].payload is None
    assert states[sample_state.hash].payload is not None
    assert states[fresh_state.hash].payload is not None

    exported = [r async for r in repos.decisions.export(NOW - 100 * DAY)]
    assert exported[0]["state"] is None  # a purged state is reported as gone, not as an error
    assert exported[0]["call"]["payload_purged_at"] is not None


async def test_metadata_older_than_180_days_is_deleted_except_samples(
    any_real_db: Database,
    settings: Settings,
    make_llm_call: Callable[..., LlmCallRecord],
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_state: Callable[..., StateBlob],
) -> None:
    db = any_real_db
    old = NOW - 200 * DAY
    repos = build_repositories(db, _Clock(old))
    kept_state = make_state({"resume": "referenced by a sample call"})
    gone_state = make_state({"resume": "unreferenced and old"})
    gone_call = make_jev_call(created_at=old, state_hash=gone_state.hash)
    sample_call = make_jev_call(created_at=old, state_hash=kept_state.hash, is_sample=True)
    await repos.decisions.record_batch(
        [gone_state, kept_state],
        [gone_call, sample_call],
        [
            make_decision(created_at=old, call_id=gone_call.id),
            make_decision(created_at=old, call_id=sample_call.id, is_sample=True),
        ],
    )
    gone_llm = make_llm_call(created_at=old, profile_id="real-profile")
    anonymous_llm = make_llm_call(created_at=old, profile_id=None)
    sample_llm = make_llm_call(created_at=old, profile_id="sample-0123456789abcdef")
    recent_llm = make_llm_call(created_at=NOW - DAY, profile_id="real-profile")
    for rec in (gone_llm, anonymous_llm, sample_llm, recent_llm):
        await repos.llm_calls.record(rec)

    counts = await run_maintenance(db, settings, NOW)

    assert counts["decision_log"] == 1
    assert counts["jev_calls"] == 1
    assert counts["llm_calls"] == 2
    assert counts["decision_states"] == 1
    assert await ids(db, JevCallRow.id) == {sample_call.id}
    assert await ids(db, DecisionStateRow.hash) == {kept_state.hash}
    assert await ids(db, LlmCallRow.id) == {sample_llm.id, recent_llm.id}
    async with db.session() as s:
        assert (await s.execute(select(func.count()).select_from(DecisionLogRow))).scalar_one() == 1
        # The spend ledger is history, not metadata: it is never purged.
        assert (await s.execute(select(func.count()).select_from(SpendDailyRow))).scalar_one() >= 2


async def test_rate_limit_counters_older_than_two_days_are_deleted(
    any_real_db: Database, settings: Settings
) -> None:
    limits = build_repositories(any_real_db).limits
    await limits.hit("a", 60, NOW - 3 * DAY)
    await limits.hit("a", 60, NOW - timedelta(days=1, hours=23))
    await limits.hit("a", 60, NOW)

    counts = await run_maintenance(any_real_db, settings, NOW)

    assert counts["rate_limit_hits"] == 1
    assert await count(any_real_db, RateLimitHitRow) == 2


async def test_it_runs_at_most_every_six_hours(any_real_db: Database, settings: Settings) -> None:
    first = await run_maintenance(any_real_db, settings, NOW)
    assert set(first) >= {"profiles", "llm_calls", "rate_limit_hits", "errors"}
    assert await run_maintenance(any_real_db, settings, NOW + timedelta(hours=5, minutes=59)) == {}
    assert await run_maintenance(any_real_db, settings, NOW + timedelta(hours=6)) != {}
    # The interval counts from the last run that did the work.
    assert await run_maintenance(any_real_db, settings, NOW + timedelta(hours=11)) == {}
    kv = build_repositories(any_real_db).kv
    assert await kv.get(LAST_RUN_KEY) == (NOW + timedelta(hours=6)).isoformat()


async def test_concurrent_callers_do_the_work_once(any_real_db: Database, settings: Settings) -> None:
    results = await asyncio.gather(*(run_maintenance(any_real_db, settings, NOW) for _ in range(5)))
    assert sum(1 for r in results if r) == 1


async def test_a_failing_step_is_reported_and_the_others_still_run(
    any_real_db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object) -> int:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(maintenance, "_delete_llm_calls", broken)
    limits = build_repositories(any_real_db).limits
    await limits.hit("a", 60, NOW - 3 * DAY)

    counts = await run_maintenance(any_real_db, settings, NOW)

    assert counts["errors"] == 1
    assert counts["llm_calls"] == 0
    assert counts["rate_limit_hits"] == 1  # a later step still ran


async def test_retention_windows_come_from_settings(
    any_real_db: Database, make_settings: Callable[..., Settings]
) -> None:
    tight = make_settings(retention=RetentionSettings(profile_days=5, payload_days=1, metadata_days=2))
    clock = _Clock(NOW - 6 * DAY)
    repos = build_repositories(any_real_db, clock)
    await seed_profile(repos, "six-days-idle")

    counts = await run_maintenance(any_real_db, tight, NOW)

    assert counts["profiles"] == 1


async def test_a_bare_database_is_fine(any_real_db: Database, settings: Settings) -> None:
    counts = await run_maintenance(any_real_db, settings, NOW)
    assert all(v == 0 for v in counts.values())


class _Clock:
    """A settable clock for seeding rows at chosen times."""

    def __init__(self, moment: datetime) -> None:
        self.now_value = moment

    def now(self) -> datetime:
        return self.now_value

    def monotonic(self) -> float:
        return self.now_value.timestamp()

    async def sleep(self, seconds: float) -> None:
        self.now_value += timedelta(seconds=seconds)
