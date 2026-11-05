"""Retention: what `run_maintenance` purges, what it keeps, and how often it runs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

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
    ChatMessageRow,
    ChatSessionRow,
    DecisionLogRow,
    DecisionStateRow,
    JevCallRow,
    LlmCallRow,
    PostingRow,
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
    await seed_profile(repos, "sample-bundled", sample=True)
    clock.now_value = NOW - 10 * DAY
    await seed_profile(repos, "recent")

    counts = await run_maintenance(db, settings, NOW, keep_profile_ids={"sample-bundled"})

    assert counts["profiles"] == 1 and counts["errors"] == 0
    assert await ids(db, ProfileRow.id) == {"sample-bundled", "recent"}  # the kept persona stays
    assert await count(db, ProfileContextRow) == 2
    assert old_session not in await ids(db, ChatSessionRow.id)
    assert await count(db, ChatSessionRow) == 1  # ... but what visitors typed to it does not (see below)
    assert counts["sample_chats"] == 1 and counts["sample_runs"] == 0


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
    kept_llm = make_llm_call(created_at=old, profile_id="sample-bundled", purpose="synth")
    recent_llm = make_llm_call(created_at=NOW - DAY, profile_id="real-profile")
    for rec in (gone_llm, anonymous_llm, kept_llm, recent_llm):
        await repos.llm_calls.record(rec)

    counts = await run_maintenance(db, settings, NOW, keep_profile_ids={"sample-bundled"})

    assert counts["decision_log"] == 1
    assert counts["jev_calls"] == 1
    assert counts["llm_calls"] == 2
    assert counts["decision_states"] == 1
    assert await ids(db, JevCallRow.id) == {sample_call.id}  # corpus rows (no profile) are permanent
    assert await ids(db, DecisionStateRow.hash) == {kept_state.hash}
    assert await ids(db, LlmCallRow.id) == {kept_llm.id, recent_llm.id}
    async with db.session() as s:
        assert (await s.execute(select(func.count()).select_from(DecisionLogRow))).scalar_one() == 1
        # The spend ledger is history, not metadata: it is never purged.
        assert (await s.execute(select(func.count()).select_from(SpendDailyRow))).scalar_one() >= 2


# ───────────────────────── sample profiles, their chats and their logs (#9) ─────────────────────────

BUNDLED = "sample-bundled-persona"
"""Stands in for the id of the bundled sample persona, which the container passes as ``keep_profile_ids``."""


async def seed_sample_chat(repos: Repositories, pid: str, text: str = "q") -> str:
    """A chat session on an existing profile (the clock of ``repos`` decides when it was last used)."""
    session = await repos.sessions.create(pid, None, None)
    await repos.sessions.append_turn(session.id, user=text, assistant="a", tools=[], llm_call_id=None)
    return session.id


async def test_sample_runs_are_purged_after_the_profile_window_except_the_kept_persona(
    any_real_db: Database, settings: Settings
) -> None:
    db = any_real_db
    clock = _Clock(NOW - 70 * DAY)
    repos = build_repositories(db, clock)
    await seed_profile(repos, BUNDLED, sample=True)
    await seed_profile(repos, "sample-old-run", sample=True)
    await seed_profile(repos, "real-old", sample=False)
    clock.now_value = NOW - 10 * DAY
    await seed_profile(repos, "sample-recent-run", sample=True)

    counts = await run_maintenance(db, settings, NOW, keep_profile_ids={BUNDLED})

    assert counts["errors"] == 0
    assert counts["sample_runs"] == 1  # only the idle run that is not the bundled persona
    assert counts["profiles"] == 1  # the ordinary rule is unchanged
    assert await ids(db, ProfileRow.id) == {BUNDLED, "sample-recent-run"}
    # the purged run took its context with it
    async with db.session() as s:
        contexts = set((await s.execute(select(ProfileContextRow.profile_id))).scalars().all())
    assert contexts == {BUNDLED, "sample-recent-run"}


async def test_without_a_keep_set_every_idle_sample_profile_goes(
    any_real_db: Database, settings: Settings
) -> None:
    repos = build_repositories(any_real_db, _Clock(NOW - 70 * DAY))
    await seed_profile(repos, BUNDLED, sample=True)

    counts = await run_maintenance(any_real_db, settings, NOW)  # the default: keep nothing

    assert counts["sample_runs"] == 1
    assert await count(any_real_db, ProfileRow) == 0


async def test_a_single_id_string_is_one_kept_id_not_a_set_of_characters(
    any_real_db: Database, settings: Settings
) -> None:
    repos = build_repositories(any_real_db, _Clock(NOW - 70 * DAY))
    await seed_profile(repos, BUNDLED, sample=True)
    await seed_profile(repos, "sample-other", sample=True)

    counts = await run_maintenance(any_real_db, settings, NOW, keep_profile_ids=BUNDLED)

    assert counts["sample_runs"] == 1
    assert await ids(any_real_db, ProfileRow.id) == {BUNDLED}


async def test_chats_with_sample_profiles_are_purged_after_the_window_even_for_the_kept_persona(
    any_real_db: Database, settings: Settings
) -> None:
    """The public sample persona is chat-able by anyone: what they typed must not live forever."""
    db = any_real_db
    clock = _Clock(NOW - 90 * DAY)
    repos = build_repositories(db, clock)
    stale_bundled = await seed_profile(repos, BUNDLED, sample=True)
    stale_real = await seed_profile(repos, "real", sample=False)
    clock.now_value = NOW - 30 * DAY
    fresh_bundled = await seed_sample_chat(repos, BUNDLED, "recent question")

    counts = await run_maintenance(db, settings, NOW, keep_profile_ids={BUNDLED})

    assert counts["sample_chats"] == 1 and counts["errors"] == 0
    # the stale chat with the kept persona is gone; its profile and the recent chat stay
    assert await ids(db, ChatSessionRow.id) == {fresh_bundled}
    assert stale_bundled not in await ids(db, ChatSessionRow.id)
    # the real profile (idle 90 days) went by the ordinary rule, with its chat
    assert stale_real not in await ids(db, ChatSessionRow.id)
    assert await ids(db, ProfileRow.id) == {BUNDLED}
    # and the messages of the purged sessions went with them
    async with db.session() as s:
        sessions_with_messages = set((await s.execute(select(ChatMessageRow.session_id))).scalars().all())
    assert sessions_with_messages == {fresh_bundled}


async def test_the_chats_of_a_purged_sample_run_cascade_away(
    any_real_db: Database, settings: Settings
) -> None:
    db = any_real_db
    repos = build_repositories(db, _Clock(NOW - 70 * DAY))
    await seed_profile(repos, "sample-run", sample=True)
    assert await count(db, ChatSessionRow) == 1 and await count(db, ChatMessageRow) == 2

    counts = await run_maintenance(db, settings, NOW)

    assert counts["sample_chats"] == 1 and counts["sample_runs"] == 1
    assert await count(db, ChatSessionRow) == 0 and await count(db, ChatMessageRow) == 0


async def test_sample_chat_retention_follows_the_profile_days_setting(
    any_real_db: Database, make_settings: Callable[..., Settings]
) -> None:
    tight = make_settings(retention=RetentionSettings(profile_days=5, payload_days=1, metadata_days=2))
    repos = build_repositories(any_real_db, _Clock(NOW - 6 * DAY))
    await seed_profile(repos, BUNDLED, sample=True)
    repos_recent = build_repositories(any_real_db, _Clock(NOW - 4 * DAY))
    recent = await seed_sample_chat(repos_recent, BUNDLED)

    counts = await run_maintenance(any_real_db, tight, NOW, keep_profile_ids={BUNDLED})

    assert counts["sample_chats"] == 1  # the six-day-old session of seed_profile, not the four-day-old one
    assert await ids(any_real_db, ChatSessionRow.id) == {recent}


async def test_a_sample_profile_used_recently_is_not_purged_as_idle(
    any_real_db: Database, settings: Settings
) -> None:
    db = any_real_db
    clock = _Clock(NOW - 70 * DAY)
    repos = build_repositories(db, clock)
    await seed_profile(repos, "sample-active", sample=True)
    clock.now_value = NOW - DAY
    await repos.profiles.save(
        "sample-active", {"id": "x"}, StoredContext(), goal=None, owner_hash=None, is_sample=True
    )

    counts = await run_maintenance(db, settings, NOW)

    assert counts["sample_runs"] == 0
    assert await ids(db, ProfileRow.id) == {"sample-active"}


async def _seed_sample_logs(
    db: Database,
    created_at: datetime,
    make_state: Callable[..., StateBlob],
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_llm_call: Callable[..., LlmCallRecord],
) -> dict[str, Any]:
    """The logs of: a sample run, the permanent corpus (no profile), the kept persona's analysis, its chat
    gate (visitor text) and an ordinary user. Returns the ids by name."""
    repos = build_repositories(db, _Clock(created_at))
    seeded: dict[str, Any] = {}
    states: list[StateBlob] = []
    calls: list[JevCallRecord] = []
    decisions: list[DecisionRecord] = []
    cases: dict[str, dict[str, Any]] = {
        "run": {"profile_id": "sample-run", "is_sample": True, "pack": "first_read"},
        "corpus": {"profile_id": None, "is_sample": True, "pack": "first_read"},
        "kept": {"profile_id": BUNDLED, "is_sample": True, "pack": "first_read"},
        "chat_gate": {"profile_id": BUNDLED, "is_sample": True, "pack": "chat_gate"},
        "user": {"profile_id": "real-profile", "is_sample": False, "pack": "first_read"},
    }
    for name, attrs in cases.items():
        state = make_state({"who": name}, is_sample=attrs["is_sample"])
        call = make_jev_call(created_at=created_at, state_hash=state.hash, **attrs)
        decision = make_decision(created_at=created_at, call_id=call.id, **attrs)
        states.append(state)
        calls.append(call)
        decisions.append(decision)
        seeded[name] = {"state": state.hash, "call": call.id, "decision": decision.id}
    await repos.decisions.record_batch(states, calls, decisions)
    llm_cases = {
        "run": ("sample-run", "reader"),
        "kept": (BUNDLED, "synth"),
        "chat_gate": (BUNDLED, "chat"),
        "user": ("real-profile", "chat"),
        "anonymous": (None, "reader"),
    }
    for name, (profile_id, purpose) in llm_cases.items():
        rec = make_llm_call(created_at=created_at, profile_id=profile_id, purpose=purpose)
        await repos.llm_calls.record(rec)
        seeded.setdefault(name, {})["llm"] = rec.id
    return seeded


async def test_logs_of_purged_sample_runs_follow_the_normal_metadata_window(
    any_real_db: Database,
    settings: Settings,
    make_state: Callable[..., StateBlob],
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_llm_call: Callable[..., LlmCallRecord],
) -> None:
    db = any_real_db
    seeded = await _seed_sample_logs(
        db, NOW - 200 * DAY, make_state, make_jev_call, make_decision, make_llm_call
    )

    counts = await run_maintenance(db, settings, NOW, keep_profile_ids={BUNDLED})

    assert counts["errors"] == 0
    # permanent: the corpus (no profile) and the kept persona's analysis; gone: everything else
    assert await ids(db, JevCallRow.id) == {seeded["corpus"]["call"], seeded["kept"]["call"]}
    assert await ids(db, DecisionLogRow.id) == {seeded["corpus"]["decision"], seeded["kept"]["decision"]}
    assert await ids(db, DecisionStateRow.hash) == {seeded["corpus"]["state"], seeded["kept"]["state"]}
    assert await ids(db, LlmCallRow.id) == {seeded["kept"]["llm"]}
    assert counts["jev_calls"] == 3 and counts["decision_log"] == 3 and counts["llm_calls"] == 4
    assert counts["decision_states"] == 3


async def test_logs_younger_than_the_metadata_window_stay(
    any_real_db: Database,
    settings: Settings,
    make_state: Callable[..., StateBlob],
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_llm_call: Callable[..., LlmCallRecord],
) -> None:
    db = any_real_db
    await _seed_sample_logs(db, NOW - 100 * DAY, make_state, make_jev_call, make_decision, make_llm_call)

    counts = await run_maintenance(db, settings, NOW, keep_profile_ids={BUNDLED})

    assert (
        counts["jev_calls"] == counts["decision_log"] == counts["llm_calls"] == counts["decision_states"] == 0
    )
    assert await count(db, JevCallRow) == 5 and await count(db, LlmCallRow) == 5


async def test_payloads_of_sample_runs_and_visitor_text_follow_the_normal_payload_window(
    any_real_db: Database,
    settings: Settings,
    make_state: Callable[..., StateBlob],
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_llm_call: Callable[..., LlmCallRecord],
) -> None:
    db = any_real_db
    seeded = await _seed_sample_logs(
        db, NOW - 40 * DAY, make_state, make_jev_call, make_decision, make_llm_call
    )

    counts = await run_maintenance(db, settings, NOW, keep_profile_ids={BUNDLED})

    assert counts["errors"] == 0 and counts["jev_calls"] == 0  # 40 days: payloads go, metadata stays
    async with db.session() as s:
        calls = {r.id: r for r in (await s.execute(select(JevCallRow))).scalars()}
        states = {r.hash: r for r in (await s.execute(select(DecisionStateRow))).scalars()}
    purged = {"run", "chat_gate", "user"}
    for name in ("run", "corpus", "kept", "chat_gate", "user"):
        call = calls[seeded[name]["call"]]
        state = states[seeded[name]["state"]]
        if name in purged:
            assert call.payload_purged_at is not None and call.questions == {} and call.response is None, name
            assert state.payload is None, name
        else:
            assert call.payload_purged_at is None and call.questions != {}, name
            assert state.payload is not None, name
    assert counts["jev_payloads"] == 3 and counts["state_payloads"] == 3


async def test_a_sample_state_shared_with_a_corpus_call_keeps_its_payload(
    any_real_db: Database,
    settings: Settings,
    make_state: Callable[..., StateBlob],
    make_jev_call: Callable[..., JevCallRecord],
) -> None:
    """States are stored once per content: the corpus call that still references it keeps it whole."""
    db = any_real_db
    repos = build_repositories(db, _Clock(NOW - 40 * DAY))
    shared = make_state({"resume": "the bundled resume"}, is_sample=True)
    run_call = make_jev_call(
        created_at=NOW - 40 * DAY, state_hash=shared.hash, profile_id="sample-run", is_sample=True
    )
    corpus_call = make_jev_call(
        created_at=NOW - 40 * DAY, state_hash=shared.hash, profile_id=None, is_sample=True
    )
    await repos.decisions.record_batch([shared], [run_call, corpus_call], [])

    counts = await run_maintenance(db, settings, NOW)

    assert counts["state_payloads"] == 0
    async with db.session() as s:
        state = (await s.execute(select(DecisionStateRow))).scalar_one()
    assert state.payload is not None


async def test_the_sample_steps_report_zero_on_a_bare_database(
    any_real_db: Database, settings: Settings
) -> None:
    counts = await run_maintenance(any_real_db, settings, NOW, keep_profile_ids={BUNDLED})
    assert counts["sample_chats"] == 0 and counts["sample_runs"] == 0 and counts["errors"] == 0


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


async def test_postings_no_match_points_at_are_purged_after_thirty_days(
    any_real_db: Database, settings: Settings
) -> None:
    from tayyari.contract.match import Posting
    from tests.unit.match.support import js

    data = js("meera.match.json")["posting"]
    old = Posting.model_validate({**data, "hash": "a" * 64})
    recent = Posting.model_validate({**data, "hash": "b" * 64})
    await build_repositories(any_real_db, _Clock(NOW - 31 * DAY)).postings.put(old)
    await build_repositories(any_real_db, _Clock(NOW - 29 * DAY)).postings.put(recent)

    counts = await run_maintenance(any_real_db, settings, NOW)

    assert counts["postings"] == 1
    assert await ids(any_real_db, PostingRow.hash) == {"b" * 64}


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
