"""The Leafout Weekly's repository, run against every backend (the fake, a SQLite file and memory, and Postgres when configured),
so `FakeWeeklyRepo` cannot drift from `SqlWeeklyRepo`: a subscription per account with a unique token, who is due on a day, an
edition per reader and week, scoping by reader, first-time-wins stamps, the claims two replicas race for, and the rows going with
their account."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import pytest

from tayyari.storage.ports import StoredWeeklyEdition, StoredWeeklySubscription
from tests.unit.storage.conftest import T0, Env

MONDAY = date(2026, 10, 19)
NEXT_MONDAY = date(2026, 10, 26)
HOLD = timedelta(minutes=10)


async def account(env: Env, email: str = "ana@example.com") -> str:
    user = await env.repos.users.sign_in_email(email=email, now=T0)
    return user.id


def sub(user_id: str, **over: Any) -> StoredWeeklySubscription:
    values: dict[str, Any] = {
        "user_id": user_id,
        "email": "ana@example.com",
        "day": "mon",
        "sections": ["jobs", "progress", "news", "tip"],
        "status": "active",
        "paused_until": None,
        "unsubscribe_token": f"tok-{user_id[:12]}",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(over)
    return StoredWeeklySubscription(**values)


def edition(user_id: str, edition_id: str = "e" * 32, **over: Any) -> StoredWeeklyEdition:
    values: dict[str, Any] = {
        "id": edition_id,
        "user_id": user_id,
        "issue": 1,
        "week_of": MONDAY,
        "payload": {"headline": "A quiet week on the boards", "jobs": [], "news": [], "reader": "Ana"},
        "created_at": T0,
    }
    values.update(over)
    return StoredWeeklyEdition(**values)


# ───────────────────────── subscriptions ─────────────────────────


async def test_a_subscription_comes_back_whole_and_by_its_token(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.weekly
    assert await repo.get_subscription(uid) is None
    await repo.save_subscription(sub(uid, paused_until=T0 + timedelta(days=30), status="paused"))
    found = await repo.get_subscription(uid)
    assert found == sub(uid, paused_until=T0 + timedelta(days=30), status="paused")
    assert await repo.get_subscription_by_token(found.unsubscribe_token) == found
    assert await repo.get_subscription_by_token("nope") is None


async def test_saving_again_replaces_the_row_and_keeps_when_it_began(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.weekly
    await repo.save_subscription(sub(uid))
    later = T0 + timedelta(days=3)
    await repo.save_subscription(
        sub(uid, day="fri", sections=["tip"], unsubscribe_token="fresh", created_at=later, updated_at=later)
    )
    found = await repo.get_subscription(uid)
    assert found is not None
    assert (found.day, found.sections, found.unsubscribe_token) == ("fri", ["tip"], "fresh")
    assert found.created_at == T0 and found.updated_at == later
    assert await repo.get_subscription_by_token(sub(uid).unsubscribe_token) is None


async def test_unsubscribe_by_token_turns_it_off_and_clears_the_pause(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.weekly
    await repo.save_subscription(sub(uid, status="paused", paused_until=T0 + timedelta(days=9)))
    assert await repo.unsubscribe_by_token("nope", T0) is False
    assert await repo.unsubscribe_by_token(sub(uid).unsubscribe_token, T0 + timedelta(hours=1)) is True
    assert await repo.unsubscribe_by_token(sub(uid).unsubscribe_token, T0 + timedelta(hours=2)) is True
    found = await repo.get_subscription(uid)
    assert found is not None and found.status == "off" and found.paused_until is None


async def test_who_is_due_on_a_day(env: Env) -> None:
    repo = env.repos.weekly
    now = T0 + timedelta(days=30)
    active = await account(env, "a@example.com")
    friday = await account(env, "f@example.com")
    off = await account(env, "o@example.com")
    paused_over = await account(env, "p1@example.com")
    paused_on = await account(env, "p2@example.com")
    built = await account(env, "b@example.com")
    await repo.save_subscription(sub(active))
    await repo.save_subscription(sub(friday, day="fri"))
    await repo.save_subscription(sub(off, status="off"))
    await repo.save_subscription(sub(paused_over, status="paused", paused_until=now - timedelta(days=1)))
    await repo.save_subscription(sub(paused_on, status="paused", paused_until=now + timedelta(days=1)))
    await repo.save_subscription(sub(built))
    assert await repo.create_edition(edition(built))

    due = await repo.due_subscriptions(day="mon", week_of=MONDAY, now=now, limit=10)
    assert {s.user_id for s in due} == {active, paused_over}
    assert [s.user_id for s in due] == sorted(s.user_id for s in due)
    assert {
        s.user_id for s in await repo.due_subscriptions(day="fri", week_of=MONDAY, now=now, limit=10)
    } == {friday}
    assert len(await repo.due_subscriptions(day="mon", week_of=MONDAY, now=now, limit=1)) == 1
    # the next week nobody has an edition yet
    next_week = await repo.due_subscriptions(day="mon", week_of=NEXT_MONDAY, now=now, limit=10)
    assert {s.user_id for s in next_week} == {active, paused_over, built}


async def test_built_but_not_emailed_editions_with_an_address(env: Env) -> None:
    repo = env.repos.weekly
    waiting = await account(env, "w@example.com")
    phone_only = await account(env, "ph@example.com")
    done = await account(env, "d@example.com")
    await repo.save_subscription(sub(waiting))
    await repo.save_subscription(sub(phone_only, email=None))
    await repo.save_subscription(sub(done))
    for uid, eid in ((waiting, "1" * 32), (phone_only, "2" * 32), (done, "3" * 32)):
        assert await repo.create_edition(edition(uid, eid))
    await repo.mark_emailed(done, "3" * 32, T0)
    rows = await repo.unsent_editions(day="mon", week_of=MONDAY, now=T0, limit=10)
    assert [(s.user_id, e.id) for s, e in rows] == [(waiting, "1" * 32)]


# ───────────────────────── editions ─────────────────────────


async def test_an_edition_per_reader_and_week(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.weekly
    assert await repo.create_edition(edition(uid, "1" * 32)) is True
    assert await repo.create_edition(edition(uid, "2" * 32)) is False  # the same week
    assert await repo.create_edition(edition(uid, "3" * 32, week_of=NEXT_MONDAY, issue=2)) is True
    found = await repo.edition_for_week(uid, MONDAY)
    assert found == edition(uid, "1" * 32)
    assert await repo.get_edition(uid, "2" * 32) is None
    assert await repo.edition_for_week(uid, date(2026, 11, 2)) is None


async def test_an_edition_is_only_its_readers(env: Env) -> None:
    ana, ben = await account(env), await account(env, "ben@example.com")
    repo = env.repos.weekly
    await repo.create_edition(edition(ana))
    assert await repo.get_edition(ben, "e" * 32) is None
    await repo.mark_read(ben, "e" * 32, T0)
    await repo.mark_emailed(ben, "e" * 32, T0)
    found = await repo.get_edition(ana, "e" * 32)
    assert found is not None and found.read_at is None and found.emailed_at is None
    assert await repo.list_editions(ben, 10) == []
    assert await repo.unread_count(ben) == 0


async def test_editions_list_newest_first_and_issues_count_up(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.weekly
    assert await repo.next_issue(uid) == 1
    await repo.create_edition(edition(uid, "1" * 32, issue=1, week_of=MONDAY))
    await repo.create_edition(edition(uid, "2" * 32, issue=2, week_of=NEXT_MONDAY))
    await repo.create_edition(edition(uid, "3" * 32, issue=3, week_of=date(2026, 11, 2)))
    assert [e.issue for e in await repo.list_editions(uid, 10)] == [3, 2, 1]
    assert [e.issue for e in await repo.list_editions(uid, 2)] == [3, 2]
    assert await repo.next_issue(uid) == 4


async def test_the_first_time_stamped_wins_and_unread_is_counted(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.weekly
    await repo.create_edition(edition(uid, "1" * 32))
    await repo.create_edition(edition(uid, "2" * 32, week_of=NEXT_MONDAY, issue=2))
    assert await repo.unread_count(uid) == 2
    await repo.mark_read(uid, "1" * 32, T0 + timedelta(hours=1))
    await repo.mark_read(uid, "1" * 32, T0 + timedelta(hours=5))
    await repo.mark_emailed(uid, "1" * 32, T0 + timedelta(hours=2))
    await repo.mark_emailed(uid, "1" * 32, T0 + timedelta(hours=6))
    found = await repo.get_edition(uid, "1" * 32)
    assert found is not None
    assert found.read_at == T0 + timedelta(hours=1) and found.emailed_at == T0 + timedelta(hours=2)
    assert await repo.unread_count(uid) == 1


async def test_the_payload_round_trips_whole(env: Env) -> None:
    uid = await account(env)
    payload = {
        "headline": "Three openings fit your graph this week",
        "jobs": [{"title": "Frontend Engineer"}],
        "n": 3,
    }
    await env.repos.weekly.create_edition(edition(uid, payload=payload))
    found = await env.repos.weekly.get_edition(uid, "e" * 32)
    assert found is not None and found.payload == payload


# ───────────────────────── claims and the paid counter ─────────────────────────


async def test_a_claim_is_held_for_its_hold_and_then_free(env: Env) -> None:
    repo = env.repos.weekly
    assert await repo.claim("compose:u1", MONDAY, now=T0, hold=HOLD, max_tries=3) == 1
    assert (
        await repo.claim("compose:u1", MONDAY, now=T0 + timedelta(minutes=5), hold=HOLD, max_tries=3) is None
    )
    assert await repo.claim("compose:u1", MONDAY, now=T0 + HOLD, hold=HOLD, max_tries=3) == 2
    assert await repo.claim("compose:u2", MONDAY, now=T0, hold=HOLD, max_tries=3) == 1  # another key


async def test_a_claim_stops_after_its_tries_and_a_new_week_starts_over(env: Env) -> None:
    repo = env.repos.weekly
    for attempt in (1, 2, 3):
        assert await repo.claim("send:u1", MONDAY, now=T0 + HOLD * attempt, hold=HOLD, max_tries=3) == attempt
    assert await repo.claim("send:u1", MONDAY, now=T0 + HOLD * 9, hold=HOLD, max_tries=3) is None
    assert await repo.claim("send:u1", NEXT_MONDAY, now=T0 + timedelta(days=7), hold=HOLD, max_tries=3) == 1


async def test_two_claims_at_once_have_exactly_one_winner(env: Env) -> None:
    repo = env.repos.weekly
    results = await asyncio.gather(
        *(repo.claim("compose:race", MONDAY, now=T0, hold=HOLD, max_tries=3) for _ in range(6))
    )
    assert sorted(r for r in results if r is not None) == [1]


async def test_the_paid_counter_counts_per_week(env: Env) -> None:
    repo = env.repos.weekly
    assert await repo.paid_this_week(MONDAY) == 0
    await repo.add_paid(MONDAY, T0)
    await repo.add_paid(MONDAY, T0)
    await repo.add_paid(NEXT_MONDAY, T0)
    assert (await repo.paid_this_week(MONDAY), await repo.paid_this_week(NEXT_MONDAY)) == (2, 1)


# ───────────────────────── the account ─────────────────────────


async def test_unique_token_per_subscription(env: Env) -> None:
    ana, ben = await account(env), await account(env, "ben@example.com")
    await env.repos.weekly.save_subscription(sub(ana, unsubscribe_token="same"))
    with pytest.raises(Exception):  # noqa: B017  (the fake raises ValueError, SQL an IntegrityError)
        await env.repos.weekly.save_subscription(sub(ben, unsubscribe_token="same"))


async def test_deleting_the_account_takes_its_paper_with_it(env: Env) -> None:
    if env.kind == "fake":
        pytest.skip("the foreign key cascade is the database's")
    uid = await account(env)
    await env.repos.weekly.save_subscription(sub(uid))
    await env.repos.weekly.create_edition(edition(uid))
    assert await env.repos.users.delete(uid)
    assert await env.repos.weekly.get_subscription(uid) is None
    assert await env.repos.weekly.list_editions(uid, 10) == []
