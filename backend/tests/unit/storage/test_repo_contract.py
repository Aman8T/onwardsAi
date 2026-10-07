"""Behaviour shared by every repository backend.

Each test runs against the dict-backed fakes, a SQLite file, an in-memory SQLite database and (when
``TEST_DATABASE_URL`` is set) Postgres, so the fakes cannot drift from the real thing.
"""

from __future__ import annotations

import asyncio
import re
import uuid
import zlib
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from tayyari.contract.records import DecisionRecord, JevCallRecord, LlmCallRecord, StateBlob
from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.storage.ports import SessionNotFound, ToolEvent
from tayyari.storage.tables import ProfileRow, SpendDailyRow

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

PROFILE = {"currentRole": "Product designer", "skills": [{"id": "figma", "score": 82}], "note": "naïve café"}
CONTEXT = StoredContext(
    sources=[StoredSource(id="resume", label="Resume", kind="resume", content="Designed a checkout flow.")],
    readers={"resume": {"facts": ["led redesign"], "years": 4}},
)


async def save_profile(
    env: Env, pid: str = "p1", *, is_sample: bool = False, goal: str | None = "design lead"
) -> None:
    await env.repos.profiles.save(
        pid, PROFILE, CONTEXT, goal=goal, owner_hash="ab" * 8, is_sample=is_sample, schema_version=2
    )


async def profile_times(env: Env, pid: str) -> tuple[datetime, datetime]:
    """``(created_at, last_accessed_at)`` straight from the backend."""
    if env.fakes is not None:
        return env.fakes.profiles.created_at(pid), env.fakes.profiles.last_accessed_at(pid)
    assert env.db is not None
    async with env.db.session() as s:
        row = (
            await s.execute(
                select(ProfileRow.created_at, ProfileRow.last_accessed_at).where(ProfileRow.id == pid)
            )
        ).one()
    return row.created_at, row.last_accessed_at


async def spend(env: Env, day: date, scope: str) -> tuple[int, int]:
    """``(micro_usd, calls)`` of a ledger row, ``(0, 0)`` when it does not exist."""
    if env.fakes is not None:
        return env.fakes.limits.spend(day, scope)
    assert env.db is not None
    async with env.db.session() as s:
        row = (
            await s.execute(
                select(SpendDailyRow.micro_usd, SpendDailyRow.calls).where(
                    SpendDailyRow.day == day, SpendDailyRow.scope == scope
                )
            )
        ).one_or_none()
    return (0, 0) if row is None else (int(row.micro_usd), int(row.calls))


# ───────────────────────────── limits ─────────────────────────────


async def test_hit_counts_per_fixed_window(env: Env) -> None:
    limits, now = env.repos.limits, env.clock.now()  # 12:00:00
    assert [await limits.hit("a", 60, now) for _ in range(3)] == [1, 2, 3]
    assert await limits.hit("a", 60, now + timedelta(seconds=59)) == 4  # same minute
    assert await limits.hit("a", 60, now + timedelta(seconds=60)) == 1  # next minute
    assert await limits.hit("b", 60, now) == 1  # another bucket
    # The window size is not part of the key: callers put it in the bucket name ("analyze:min:<ip>").
    assert await limits.hit("a:hour", 3600, now) == 1
    assert await limits.hit("a:hour", 3600, now + timedelta(minutes=59)) == 2


async def test_hit_rejects_a_non_positive_window(env: Env) -> None:
    with pytest.raises(ValueError):
        await env.repos.limits.hit("a", 0, env.clock.now())


async def test_purge_hits_removes_only_old_windows(env: Env) -> None:
    limits, now = env.repos.limits, env.clock.now()
    for minute in range(3):
        await limits.hit("a", 60, now + timedelta(minutes=minute))
    assert await limits.purge_hits(now + timedelta(minutes=2)) == 2
    assert await limits.hit("a", 60, now + timedelta(minutes=2)) == 2  # that window survived
    assert await limits.hit("a", 60, now) == 1  # the purged one starts over


async def test_spent_today_is_zero_without_a_ledger_row(env: Env) -> None:
    assert await env.repos.limits.spent_today(env.clock.now().date()) == 0


# ───────────────────────────── kv ─────────────────────────────


async def test_kv_get_set_overwrite(env: Env) -> None:
    kv = env.repos.kv
    assert await kv.get("missing") is None
    await kv.set("k", {"a": [1, 2], "b": "naïve"})
    assert await kv.get("k") == {"a": [1, 2], "b": "naïve"}
    await kv.set("k", "replaced")
    assert await kv.get("k") == "replaced"
    await kv.set("k", None)
    assert await kv.get("k") is None


async def test_secret_is_created_once_and_stable(env: Env) -> None:
    kv = env.repos.kv
    first = await kv.get_or_create_secret("ip_hash_key")
    assert len(first) == 32
    assert await kv.get_or_create_secret("ip_hash_key") == first
    assert await kv.get_or_create_secret("ip_hash_key", 64) == first  # keeps its original length
    assert await kv.get_or_create_secret("other", 16) != first
    assert len(await kv.get_or_create_secret("short", 8)) == 8


async def test_concurrent_secret_creation_agrees(env: Env) -> None:
    secrets_seen = await asyncio.gather(*(env.repos.kv.get_or_create_secret("race") for _ in range(12)))
    assert len(set(secrets_seen)) == 1


async def test_secret_rejects_a_value_that_is_not_one(env: Env) -> None:
    await env.repos.kv.set("bad", "not hex!")
    with pytest.raises(ValueError):
        await env.repos.kv.get_or_create_secret("bad")
    with pytest.raises(ValueError):
        await env.repos.kv.get_or_create_secret("zero", 0)


# ───────────────────────────── profiles ─────────────────────────────


async def test_profile_save_and_get_round_trip(env: Env) -> None:
    assert await env.repos.profiles.get_profile("p1") is None
    await save_profile(env)
    assert await env.repos.profiles.get_profile("p1") == PROFILE


async def test_get_for_chat_returns_profile_and_context(env: Env) -> None:
    assert await env.repos.profiles.get_for_chat("p1") is None
    await save_profile(env)
    found = await env.repos.profiles.get_for_chat("p1")
    assert found is not None
    profile, context = found
    assert profile == PROFILE
    assert context == CONTEXT
    assert context.sources[0].content == "Designed a checkout flow."


async def test_resaving_replaces_the_document_and_keeps_created_at(env: Env) -> None:
    await save_profile(env)
    created, _ = await profile_times(env, "p1")
    env.clock.advance(2 * HOUR)
    other = StoredContext(readers={"resume": {"years": 5}})
    await env.repos.profiles.save(
        "p1", {"currentRole": "Lead"}, other, goal=None, owner_hash=None, is_sample=False
    )
    assert await env.repos.profiles.get_profile("p1") == {"currentRole": "Lead"}
    found = await env.repos.profiles.get_for_chat("p1")
    assert found is not None and found[1] == other
    new_created, accessed = await profile_times(env, "p1")
    assert new_created == created
    assert accessed == env.clock.now()


async def test_profile_reads_touch_last_accessed_at_at_most_hourly(env: Env) -> None:
    await save_profile(env)
    saved_at = env.clock.now()

    env.clock.advance(timedelta(minutes=30))
    await env.repos.profiles.get_profile("p1")
    assert (await profile_times(env, "p1"))[1] == saved_at  # too soon

    env.clock.advance(timedelta(minutes=31))  # 61 minutes after the save
    await env.repos.profiles.get_profile("p1")
    touched_at = env.clock.now()
    assert (await profile_times(env, "p1"))[1] == touched_at

    env.clock.advance(timedelta(minutes=29))
    await env.repos.profiles.get_profile("p1")
    assert (await profile_times(env, "p1"))[1] == touched_at  # throttled again

    env.clock.advance(timedelta(minutes=31))  # an hour after the last touch
    await env.repos.profiles.get_for_chat("p1")  # chat reads touch too
    assert (await profile_times(env, "p1"))[1] == env.clock.now()


async def test_purge_expired_keeps_samples_and_recent_profiles(env: Env) -> None:
    await save_profile(env, "old")
    await save_profile(env, "sample", is_sample=True)
    session = await env.repos.sessions.create("old", None, None)
    await env.repos.sessions.append_turn(session.id, user="hi", assistant="hello", tools=[], llm_call_id=None)
    env.clock.advance(100 * DAY)
    await save_profile(env, "fresh")

    assert await env.repos.profiles.purge_expired(env.clock.now() - 60 * DAY) == 1

    assert await env.repos.profiles.get_profile("old") is None
    assert await env.repos.profiles.get_profile("sample") == PROFILE
    assert await env.repos.profiles.get_profile("fresh") == PROFILE
    assert await env.repos.sessions.load(session.id, "old") is None  # its chat went with it
    assert await env.repos.profiles.purge_expired(env.clock.now() - 60 * DAY) == 0


# ───────────────────────────── sessions ─────────────────────────────


async def test_create_then_load_an_empty_session(env: Env) -> None:
    await save_profile(env)
    origin = {"kind": "skill", "id": "figma", "title": "Figma"}
    created = await env.repos.sessions.create("p1", origin, "cd" * 8)
    assert re.fullmatch(r"[0-9a-f]{32}", created.id)
    assert (created.profile_id, created.origin, created.turn_count, created.messages) == ("p1", origin, 0, [])

    loaded = await env.repos.sessions.load(created.id, "p1")
    assert loaded is not None
    assert (loaded.id, loaded.profile_id, loaded.origin, loaded.turn_count) == (created.id, "p1", origin, 0)
    assert loaded.messages == []


async def test_a_session_of_another_profile_or_an_unknown_one_is_none(env: Env) -> None:
    await save_profile(env, "p1")
    await save_profile(env, "p2")
    session = await env.repos.sessions.create("p1", None, None)
    assert await env.repos.sessions.load(session.id, "p2") is None
    assert await env.repos.sessions.load("0" * 32, "p1") is None
    assert await env.repos.sessions.load(session.id, "p1") is not None


async def test_append_turn_assigns_consecutive_seq_numbers(env: Env) -> None:
    await save_profile(env)
    session = await env.repos.sessions.create("p1", None, None)
    tools = [ToolEvent("WebSearch", "figma jobs"), ToolEvent("WebFetch", "https://example.com/a")]
    await env.repos.sessions.append_turn(
        session.id, user="q1", assistant="a1", tools=tools, llm_call_id=uuid.uuid4()
    )
    await env.repos.sessions.append_turn(session.id, user="q2", assistant="a2", tools=[], llm_call_id=None)

    loaded = await env.repos.sessions.load(session.id, "p1")
    assert loaded is not None
    assert loaded.turn_count == 2
    assert [(m.seq, m.role, m.content) for m in loaded.messages] == [
        (0, "user", "q1"),
        (1, "assistant", "a1"),
        (2, "user", "q2"),
        (3, "assistant", "a2"),
    ]
    assert loaded.messages[0].tools == []
    assert loaded.messages[1].tools == tools
    assert loaded.messages[3].tools == []


async def test_a_turn_without_visible_text_stores_only_the_user_message(env: Env) -> None:
    await save_profile(env)
    session = await env.repos.sessions.create("p1", None, None)
    tools = [ToolEvent("WebSearch", "salaries")]
    await env.repos.sessions.append_turn(session.id, user="q", assistant=None, tools=tools, llm_call_id=None)
    await env.repos.sessions.append_turn(session.id, user="q2", assistant="a2", tools=[], llm_call_id=None)

    loaded = await env.repos.sessions.load(session.id, "p1")
    assert loaded is not None
    assert loaded.turn_count == 2
    assert [(m.seq, m.role) for m in loaded.messages] == [(0, "user"), (1, "user"), (2, "assistant")]
    assert loaded.messages[0].tools == tools


async def test_load_returns_the_last_messages_oldest_first(env: Env) -> None:
    await save_profile(env)
    session = await env.repos.sessions.create("p1", None, None)
    for i in range(5):
        await env.repos.sessions.append_turn(
            session.id, user=f"q{i}", assistant=f"a{i}", tools=[], llm_call_id=None
        )
    last4 = await env.repos.sessions.load(session.id, "p1", limit=4)
    assert last4 is not None
    assert [m.seq for m in last4.messages] == [6, 7, 8, 9]
    assert [m.content for m in last4.messages] == ["q3", "a3", "q4", "a4"]
    assert last4.turn_count == 5  # the count is the session's, not the window's
    default = await env.repos.sessions.load(session.id, "p1")
    assert default is not None and len(default.messages) == 10
    none = await env.repos.sessions.load(session.id, "p1", limit=0)
    assert none is not None and none.messages == []


async def test_appending_to_an_unknown_session_raises(env: Env) -> None:
    with pytest.raises(SessionNotFound):
        await env.repos.sessions.append_turn("0" * 32, user="q", assistant="a", tools=[], llm_call_id=None)


async def test_concurrent_turns_on_one_session_all_land(env: Env) -> None:
    await save_profile(env)
    session = await env.repos.sessions.create("p1", None, None)
    await asyncio.gather(
        *(
            env.repos.sessions.append_turn(
                session.id, user=f"q{i}", assistant=f"a{i}", tools=[], llm_call_id=None
            )
            for i in range(8)
        )
    )
    loaded = await env.repos.sessions.load(session.id, "p1", limit=100)
    assert loaded is not None
    assert [m.seq for m in loaded.messages] == list(range(16))
    assert loaded.turn_count == 8
    for first, second in zip(loaded.messages[::2], loaded.messages[1::2], strict=True):
        assert first.role == "user" and second.role == "assistant"
        assert first.content[1:] == second.content[1:]  # q{i} is followed by its own a{i}


# ───────────────────────────── llm calls and spend ─────────────────────────────


async def test_recording_a_call_adds_to_total_and_anthropic(
    env: Env, make_llm_call: Callable[..., LlmCallRecord]
) -> None:
    day = env.clock.now().date()
    await env.repos.llm_calls.record(make_llm_call(cost_micro_usd=1_500_000))
    await env.repos.llm_calls.record(
        make_llm_call(cost_micro_usd=250_000, status="error", error_kind="timeout")
    )
    assert await env.repos.limits.spent_today(day) == 1_750_000
    assert await spend(env, day, "total") == (1_750_000, 2)
    assert await spend(env, day, "anthropic") == (1_750_000, 2)
    assert await spend(env, day, "jev") == (0, 0)


async def test_recording_the_same_call_twice_counts_it_once(
    env: Env, make_llm_call: Callable[..., LlmCallRecord]
) -> None:
    rec = make_llm_call(cost_micro_usd=777)
    await env.repos.llm_calls.record(rec)
    await env.repos.llm_calls.record(rec)
    assert await spend(env, env.clock.now().date(), "total") == (777, 1)


async def test_spend_is_kept_per_utc_day(env: Env, make_llm_call: Callable[..., LlmCallRecord]) -> None:
    now = env.clock.now()
    await env.repos.llm_calls.record(make_llm_call(created_at=now, cost_micro_usd=10))
    await env.repos.llm_calls.record(make_llm_call(created_at=now + DAY, cost_micro_usd=32))
    assert await env.repos.limits.spent_today(now.date()) == 10
    assert await env.repos.limits.spent_today((now + DAY).date()) == 32


async def test_concurrent_calls_sum_exactly(env: Env, make_llm_call: Callable[..., LlmCallRecord]) -> None:
    await asyncio.gather(
        *(env.repos.llm_calls.record(make_llm_call(cost_micro_usd=100 + i)) for i in range(20))
    )
    expected = sum(100 + i for i in range(20))
    assert await spend(env, env.clock.now().date(), "total") == (expected, 20)


async def test_llm_and_jev_spend_sum_into_the_total(
    env: Env,
    make_llm_call: Callable[..., LlmCallRecord],
    make_jev_call: Callable[..., JevCallRecord],
    make_state: Callable[..., StateBlob],
) -> None:
    day = env.clock.now().date()
    state = make_state()
    await env.repos.llm_calls.record(make_llm_call(cost_micro_usd=1_500_000))
    await env.repos.decisions.record_batch(
        [state],
        [
            make_jev_call(state_hash=state.hash, cost_micro_usd=100),
            make_jev_call(state_hash=state.hash, cost_micro_usd=250),
        ],
        [],
    )
    assert await spend(env, day, "total") == (1_500_350, 3)
    assert await spend(env, day, "anthropic") == (1_500_000, 1)
    assert await spend(env, day, "jev") == (350, 2)
    assert await env.repos.limits.spent_today(day) == 1_500_350


# ───────────────────────────── decision log ─────────────────────────────


async def test_record_batch_is_idempotent(
    env: Env,
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_state: Callable[..., StateBlob],
) -> None:
    state = make_state()
    call = make_jev_call(state_hash=state.hash, cost_micro_usd=500)
    row = make_decision(call_id=call.id)
    await env.repos.decisions.record_batch([state, state], [call], [row])
    await env.repos.decisions.record_batch([state], [call], [row])
    exported = [r async for r in env.repos.decisions.export(env.clock.now() - HOUR)]
    assert len(exported) == 1
    assert await spend(env, env.clock.now().date(), "jev") == (500, 1)
    await env.repos.decisions.record_batch([], [], [])  # empty flush: nothing to do


async def test_export_joins_the_call_and_the_decompressed_state(
    env: Env,
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_state: Callable[..., StateBlob],
) -> None:
    now = env.clock.now()
    state_doc = {"resume": "Designed a checkout flow.", "notes": "naïve café"}
    state = make_state(state_doc)
    call = make_jev_call(state_hash=state.hash, created_at=now)
    analysis = uuid.uuid4()
    linked = make_decision(
        created_at=now + timedelta(seconds=1),
        call_id=call.id,
        claude_call_id=uuid.uuid4(),
        subject_ref="skill:figma",
        jev_answer={"choice": "design_ux", "confidence": 0.8},
        jev_confidence=0.8,
        threshold=0.45,
        claude_answer={"label": "design_ux"},
        incumbent={"score": 62},
        latency_ms=1100,
        cost_micro_usd=126,
        profile_id="p1",
        analysis_id=analysis,
        extra={"order": ["a", "b"]},
    )
    bare = make_decision(created_at=now + timedelta(seconds=2), mode="off", used=False, source_used="default")
    await env.repos.decisions.record_batch([state], [call], [bare, linked])

    first, second = [r async for r in env.repos.decisions.export(now - HOUR)]
    assert first["id"] == str(linked.id)  # oldest first
    assert second["id"] == str(bare.id)

    assert first["created_at"] == (now + timedelta(seconds=1)).isoformat()
    assert first["spec_id"] == "first_read.career_family"
    assert first["mode"] == "primary" and first["used"] is True and first["source_used"] == "jev"
    assert first["escalated"] is False and first["escalation_reason"] is None
    assert first["subject_ref"] == "skill:figma"
    assert first["jev_answer"] == {"choice": "design_ux", "confidence": 0.8}
    assert first["jev_confidence"] == 0.8 and first["threshold"] == 0.45
    assert first["claude_answer"] == {"label": "design_ux"} and first["incumbent"] == {"score": 62}
    assert first["claude_call_id"] == str(linked.claude_call_id)
    assert first["profile_id"] == "p1" and first["analysis_id"] == str(analysis)
    assert first["extra"] == {"order": ["a", "b"]}
    assert first["call_id"] == str(call.id)
    assert first["call"]["id"] == str(call.id)
    assert first["call"]["pack"] == "first_read" and first["call"]["model_served"] == "jev-1.13.0"
    assert first["call"]["questions"] == call.questions and first["call"]["response"] == call.response
    assert first["call"]["state_hash"] == state.hash and first["call"]["payload_purged_at"] is None
    assert first["state"] == state_doc

    assert second["call"] is None and second["state"] is None and second["extra"] == {}


async def test_export_filters_and_limits(
    env: Env, make_decision: Callable[..., DecisionRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tayyari.storage.repos import decisions as decisions_module

    monkeypatch.setattr(decisions_module, "_EXPORT_PAGE", 2)  # several pages on the SQL repository
    now = env.clock.now()
    rows = [make_decision(created_at=now + timedelta(minutes=i), subject_ref=f"r{i}") for i in range(5)]
    await env.repos.decisions.record_batch([], [], rows[::-1])

    async def refs(**kwargs: Any) -> list[str | None]:
        return [r["subject_ref"] async for r in env.repos.decisions.export(**kwargs)]

    assert await refs(since=now) == ["r0", "r1", "r2", "r3", "r4"]
    assert await refs(since=now + timedelta(minutes=1), until=now + timedelta(minutes=4)) == [
        "r1",
        "r2",
        "r3",
    ]
    assert await refs(since=now, limit=3) == ["r0", "r1", "r2"]
    assert await refs(since=now, limit=0) == []
    assert await refs(since=now + timedelta(hours=1)) == []


async def test_export_orders_rows_with_equal_timestamps_stably(
    env: Env, make_decision: Callable[..., DecisionRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tayyari.storage.repos import decisions as decisions_module

    monkeypatch.setattr(decisions_module, "_EXPORT_PAGE", 2)
    now = env.clock.now()
    rows = [make_decision(created_at=now) for _ in range(5)]
    await env.repos.decisions.record_batch([], [], rows)
    ids = [r["id"] async for r in env.repos.decisions.export(now)]
    assert len(ids) == 5
    assert len(set(ids)) == 5  # no row repeated or skipped across page boundaries
    assert ids == sorted(ids)


async def test_a_state_seen_again_is_stored_once(
    env: Env,
    make_jev_call: Callable[..., JevCallRecord],
    make_decision: Callable[..., DecisionRecord],
    make_state: Callable[..., StateBlob],
) -> None:
    state = make_state()
    for minute in range(3):
        call = make_jev_call(state_hash=state.hash, created_at=env.clock.now())
        await env.repos.decisions.record_batch(
            [state], [call], [make_decision(call_id=call.id, created_at=env.clock.now())]
        )
        env.clock.advance(timedelta(minutes=minute + 1))
    exported = [r async for r in env.repos.decisions.export(env.clock.now() - DAY)]
    assert len(exported) == 3
    assert {r["state"]["resume"] for r in exported} == {"Designed a checkout flow."}
    assert zlib.decompress(state.payload)  # the blob handed in is untouched
