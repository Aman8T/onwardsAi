"""Accounts storage, run against every backend (the fakes, SQLite file and memory, and Postgres when configured), so
the fake cannot drift from the SQL repository: sign-in lookups, sessions, single-use links, claims, deletion, purge."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from tayyari.contract.stored import StoredContext
from tayyari.storage.ports import (
    ProfileNotClaimable,
    ProfileOwnedByAnother,
    StoredAuthSession,
    StoredMagicLink,
    StoredPortfolio,
)
from tayyari.storage.tables import (
    AuthSessionRow,
    MagicLinkRow,
    PortfolioRow,
    ProfileContextRow,
    ProfileRow,
    UserRow,
)

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


async def add_profile(
    env: Env, pid: str = "p1", *, sample: bool = False, name: str | None = "Riya Kapoor"
) -> None:
    doc = {"person": {"name": name}} if name is not None else {}
    await env.repos.profiles.save(
        pid, doc, StoredContext(), goal="backend roles", owner_hash=None, is_sample=sample
    )


async def add_portfolio(env: Env, pid: str = "p1") -> None:
    await env.repos.portfolios.put(
        StoredPortfolio(
            profile_id=pid,
            public_id=f"pub-{pid}",
            doc={"profileId": pid},
            facts_hash=None,
            generated=[],
            version=1,
            published=False,
            created_at=env.clock.now(),
            updated_at=env.clock.now(),
        ),
        expected_version=None,
    )


async def count(env: Env, table: type) -> int:
    """Rows in a table of the real database (the fakes have none: callers check ``env.db`` first)."""
    assert env.db is not None
    async with env.db.session() as s:
        return int((await s.execute(select(func.count()).select_from(table))).scalar_one())


def session_record(
    env: Env, user_id: str, sid: str = "a" * 64, *, expires_in: timedelta = 30 * DAY
) -> StoredAuthSession:
    now = env.clock.now()
    return StoredAuthSession(
        id=sid,
        user_id=user_id,
        created_at=now,
        expires_at=now + expires_in,
        last_seen_at=now,
        ip_hash="ip",
        user_agent="UA",
    )


def link_record(
    env: Env, token_hash: str = "b" * 64, *, email: str = "ana@example.com", minutes: int = 15
) -> StoredMagicLink:
    now = env.clock.now()
    return StoredMagicLink(token_hash, email, now, now + timedelta(minutes=minutes), None, "ip")


# ───────────────────────── sign in ─────────────────────────


async def test_an_email_sign_in_creates_the_account_once(env: Env) -> None:
    users = env.repos.users
    first = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    env.clock.advance(HOUR)
    again = await users.sign_in_email(email="ana@example.com", now=env.clock.now())

    assert again.id == first.id and len(first.id) == 32
    assert first.created_at == again.created_at and again.last_login_at == env.clock.now()
    assert (first.name, first.google_sub, first.currency) == (None, None, None)
    assert (await users.get(first.id)) == again
    assert (await users.get_by_email("ana@example.com")) == again
    assert await users.get("nope") is None and await users.get_by_email("nobody@example.com") is None


async def test_two_first_sign_ins_at_once_make_one_account(env: Env) -> None:
    users = env.repos.users
    results = await asyncio.gather(
        *(users.sign_in_email(email="race@example.com", now=env.clock.now()) for _ in range(4))
    )
    assert len({u.id for u in results}) == 1


async def test_a_google_sign_in_creates_then_finds_by_sub(env: Env) -> None:
    users = env.repos.users
    now = env.clock.now()
    made = await users.sign_in_google(
        sub="g-1",
        email="ana@example.com",
        name="Ana Rao",
        picture_url="https://x/1.png",
        locale="en-GB",
        now=now,
    )
    env.clock.advance(HOUR)
    again = await users.sign_in_google(
        sub="g-1",
        email="ana@example.com",
        name="Other Name",
        picture_url="https://x/2.png",
        locale="hi-IN",
        now=env.clock.now(),
    )

    assert again.id == made.id
    assert (again.name, again.locale) == ("Ana Rao", "en-GB")  # filled in once, never overwritten
    assert again.picture_url == "https://x/2.png" and again.last_login_at == env.clock.now()


async def test_a_google_sign_in_attaches_to_the_account_with_that_email(env: Env) -> None:
    users = env.repos.users
    by_link = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    by_google = await users.sign_in_google(
        sub="g-1", email="ana@example.com", name="Ana", picture_url=None, locale=None, now=env.clock.now()
    )
    assert by_google.id == by_link.id and by_google.google_sub == "g-1" and by_google.name == "Ana"
    # ... and from then on the sub finds it, whatever the email says
    third = await users.sign_in_google(
        sub="g-1", email="moved@example.com", name=None, picture_url=None, locale=None, now=env.clock.now()
    )
    assert third.id == by_link.id and third.email == "ana@example.com"


async def test_an_email_account_never_loses_its_google_identity_to_another(env: Env) -> None:
    users = env.repos.users
    await users.sign_in_google(
        sub="g-1", email="ana@example.com", name=None, picture_url=None, locale=None, now=env.clock.now()
    )
    other = await users.sign_in_google(
        sub="g-2", email="ana@example.com", name=None, picture_url=None, locale=None, now=env.clock.now()
    )
    assert other.google_sub == "g-1"  # the first identity stays; a second Google account is not attached


async def test_update_changes_only_what_it_is_given(env: Env) -> None:
    users = env.repos.users
    user = await users.sign_in_email(email="ana@example.com", now=env.clock.now())

    named = await users.update(user.id, name="Ana Rao")
    assert named is not None and (named.name, named.currency) == ("Ana Rao", None)
    priced = await users.update(user.id, currency="INR")
    assert priced is not None and (priced.name, priced.currency) == ("Ana Rao", "INR")
    unchanged = await users.update(user.id)
    assert unchanged == priced
    assert await users.update("missing", name="x") is None


# ───────────────────────── sessions ─────────────────────────


async def test_a_session_is_stored_and_read_back_with_its_user(env: Env) -> None:
    users = env.repos.users
    user = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    record = session_record(env, user.id)
    await users.create_session(record)

    found = await users.get_session(record.id)
    assert found is not None
    session, owner = found
    assert session == record and owner.id == user.id and owner.email == "ana@example.com"
    assert await users.get_session("f" * 64) is None


async def test_touching_a_session_moves_its_times(env: Env) -> None:
    users = env.repos.users
    user = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    record = session_record(env, user.id)
    await users.create_session(record)

    later = env.clock.now() + 2 * HOUR
    await users.touch_session(record.id, last_seen_at=later, expires_at=later + 30 * DAY)
    found = await users.get_session(record.id)
    assert found is not None
    assert (found[0].last_seen_at, found[0].expires_at) == (later, later + 30 * DAY)
    assert found[0].created_at == record.created_at


async def test_deleting_a_session_signs_out_only_that_one(env: Env) -> None:
    users = env.repos.users
    user = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    await users.create_session(session_record(env, user.id, "a" * 64))
    await users.create_session(session_record(env, user.id, "c" * 64))

    assert await users.delete_session("a" * 64) is True
    assert await users.delete_session("a" * 64) is False
    assert await users.get_session("a" * 64) is None
    assert await users.get_session("c" * 64) is not None


# ───────────────────────── emailed links ─────────────────────────


async def test_a_link_can_be_spent_exactly_once(env: Env) -> None:
    users = env.repos.users
    await users.add_magic_link(link_record(env))

    first = await users.consume_magic_link("b" * 64, env.clock.now())
    assert first is not None and first.email == "ana@example.com" and first.used_at == env.clock.now()
    assert await users.consume_magic_link("b" * 64, env.clock.now()) is None
    assert await users.consume_magic_link("c" * 64, env.clock.now()) is None  # never issued


async def test_two_clicks_at_once_sign_in_once(env: Env) -> None:
    users = env.repos.users
    await users.add_magic_link(link_record(env))
    results = await asyncio.gather(*(users.consume_magic_link("b" * 64, env.clock.now()) for _ in range(5)))
    assert sum(r is not None for r in results) == 1


async def test_a_link_is_valid_up_to_but_not_at_its_expiry(env: Env) -> None:
    users = env.repos.users
    await users.add_magic_link(link_record(env, "d" * 64))
    await users.add_magic_link(link_record(env, "e" * 64))
    just_before = env.clock.now() + timedelta(minutes=15) - timedelta(seconds=1)
    at_expiry = env.clock.now() + timedelta(minutes=15)
    assert await users.consume_magic_link("d" * 64, just_before) is not None
    assert await users.consume_magic_link("e" * 64, at_expiry) is None


async def test_an_expired_link_is_not_marked_used(env: Env) -> None:
    users = env.repos.users
    await users.add_magic_link(link_record(env))
    assert await users.consume_magic_link("b" * 64, env.clock.now() + DAY) is None
    # still unspent, so a (wrong-clock) retry within its window works
    assert await users.consume_magic_link("b" * 64, env.clock.now()) is not None


# ───────────────────────── profiles ─────────────────────────


async def test_claiming_attaches_the_profile_and_its_portfolio(env: Env) -> None:
    users = env.repos.users
    owner = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    await add_profile(env)
    await add_portfolio(env)

    await users.claim_profile(owner.id, "p1")

    listed = await users.list_profiles(owner.id)
    assert [(p.id, p.person_name, p.goal) for p in listed] == [("p1", "Riya Kapoor", "backend roles")]
    if env.fakes is not None:
        assert env.fakes.users.profile_owner("p1") == owner.id
        assert env.fakes.users.portfolio_owner("p1") == owner.id
    else:
        assert env.db is not None
        async with env.db.session() as s:
            assert (
                await s.execute(select(ProfileRow.user_id).where(ProfileRow.id == "p1"))
            ).scalar_one() == owner.id
            assert (
                await s.execute(select(PortfolioRow.user_id).where(PortfolioRow.profile_id == "p1"))
            ).scalar_one() == owner.id


async def test_claiming_again_is_a_no_op_for_the_owner_and_a_conflict_for_anyone_else(env: Env) -> None:
    users = env.repos.users
    ana = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    raj = await users.sign_in_email(email="raj@example.com", now=env.clock.now())
    await add_profile(env)

    await users.claim_profile(ana.id, "p1")
    await users.claim_profile(ana.id, "p1")  # idempotent
    with pytest.raises(ProfileOwnedByAnother):
        await users.claim_profile(raj.id, "p1")

    assert [p.id for p in await users.list_profiles(ana.id)] == ["p1"]
    assert await users.list_profiles(raj.id) == []


async def test_an_unknown_profile_and_a_sample_run_cannot_be_claimed(env: Env) -> None:
    users = env.repos.users
    ana = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    await add_profile(env, "sample-1", sample=True)
    with pytest.raises(ProfileNotClaimable):
        await users.claim_profile(ana.id, "missing")
    with pytest.raises(ProfileNotClaimable):
        await users.claim_profile(ana.id, "sample-1")
    assert await users.list_profiles(ana.id) == []


async def test_profiles_are_listed_newest_first_and_capped(env: Env) -> None:
    users = env.repos.users
    ana = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    for i in range(3):
        await add_profile(env, f"p{i}", name=f"Person {i}")
        await users.claim_profile(ana.id, f"p{i}")
        env.clock.advance(HOUR)

    assert [p.id for p in await users.list_profiles(ana.id)] == ["p2", "p1", "p0"]
    assert [p.id for p in await users.list_profiles(ana.id, limit=2)] == ["p2", "p1"]


async def test_a_profile_without_a_name_is_listed_with_none(env: Env) -> None:
    users = env.repos.users
    ana = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    await add_profile(env, name=None)
    await users.claim_profile(ana.id, "p1")
    (listed,) = await users.list_profiles(ana.id)
    assert listed.person_name is None


async def test_re_saving_a_claimed_profile_keeps_its_owner(env: Env) -> None:
    users = env.repos.users
    ana = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    await add_profile(env)
    await users.claim_profile(ana.id, "p1")
    await add_profile(env)  # the analysis service saves the same id again
    assert [p.id for p in await users.list_profiles(ana.id)] == ["p1"]


async def test_the_retention_purge_keeps_a_profile_that_belongs_to_an_account(env: Env) -> None:
    users = env.repos.users
    ana = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    await add_profile(env, "mine")
    await add_profile(env, "anonymous")
    await users.claim_profile(ana.id, "mine")

    env.clock.advance(90 * DAY)
    purged = await env.repos.profiles.purge_expired(env.clock.now() - 60 * DAY)

    assert purged == 1
    assert await env.repos.profiles.get_profile("mine") is not None
    assert await env.repos.profiles.get_profile("anonymous") is None


# ───────────────────────── delete and purge ─────────────────────────


async def test_deleting_an_account_removes_what_it_owns_and_nothing_else(env: Env) -> None:
    users = env.repos.users
    ana = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    raj = await users.sign_in_email(email="raj@example.com", now=env.clock.now())
    for pid in ("ana-1", "raj-1"):
        await add_profile(env, pid)
        await add_portfolio(env, pid)
    await users.claim_profile(ana.id, "ana-1")
    await users.claim_profile(raj.id, "raj-1")
    await users.create_session(session_record(env, ana.id, "a" * 64))
    await users.create_session(session_record(env, raj.id, "c" * 64))
    await users.add_magic_link(link_record(env, "d" * 64, email="ana@example.com"))
    await add_profile(env, "stranger")

    assert await users.delete(ana.id) is True

    assert await users.get(ana.id) is None and await users.get_session("a" * 64) is None
    assert await users.consume_magic_link("d" * 64, env.clock.now()) is None
    assert await env.repos.profiles.get_profile("ana-1") is None
    assert await env.repos.portfolios.get("ana-1") is None
    # the others are untouched
    assert await users.get(raj.id) is not None and await users.get_session("c" * 64) is not None
    assert await env.repos.profiles.get_profile("raj-1") is not None
    assert await env.repos.portfolios.get("raj-1") is not None
    assert await env.repos.profiles.get_profile("stranger") is not None
    assert await users.delete(ana.id) is False


async def test_deleting_an_account_takes_the_profiles_context_and_chats_with_it(env: Env) -> None:
    users = env.repos.users
    ana = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    await add_profile(env)
    await users.claim_profile(ana.id, "p1")
    chat = await env.repos.sessions.create("p1", None, None)

    await users.delete(ana.id)

    assert await env.repos.profiles.get_for_chat("p1") is None
    assert await env.repos.sessions.load(chat.id, "p1") is None
    if env.db is not None:
        assert await count(env, ProfileContextRow) == 0
        assert await count(env, UserRow) == 0


async def test_the_purge_removes_expired_sessions_and_links_only(env: Env) -> None:
    users = env.repos.users
    user = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    await users.create_session(session_record(env, user.id, "a" * 64, expires_in=HOUR))
    await users.create_session(session_record(env, user.id, "c" * 64, expires_in=30 * DAY))
    await users.add_magic_link(link_record(env, "d" * 64, minutes=15))
    await users.add_magic_link(link_record(env, "e" * 64, minutes=24 * 60))

    env.clock.advance(2 * HOUR)
    assert await users.purge_expired(env.clock.now()) == 2  # one session, one link

    assert await users.get_session("a" * 64) is None and await users.get_session("c" * 64) is not None
    assert await users.consume_magic_link("d" * 64, env.clock.now()) is None
    assert await users.consume_magic_link("e" * 64, env.clock.now()) is not None
    if env.db is not None:
        assert await count(env, AuthSessionRow) == 1 and await count(env, MagicLinkRow) == 1
    assert await users.purge_expired(env.clock.now()) == 0


# ───────────────────────── the create race (SQL only) ─────────────────────────


async def test_a_lost_create_race_is_retried_through_the_update_path(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two first sign-ins at once: the loser's INSERT hits a unique constraint. The id collision below stands in for
    it (on Postgres the second INSERT really fails after both UPDATEs found nothing); the retry must then succeed."""
    if env.db is None:
        pytest.skip("only the SQL repository has a create race")
    from tayyari.storage.repos import users as users_module

    users = env.repos.users
    taken = await users.sign_in_email(email="first@example.com", now=env.clock.now())
    ids = iter([taken.id, "f" * 32])  # the first attempt draws an id that is already taken
    monkeypatch.setattr(users_module, "new_user_id", lambda: next(ids))

    made = await users.sign_in_email(email="second@example.com", now=env.clock.now())

    assert made.id == "f" * 32 and made.email == "second@example.com"
    assert (await users.get(taken.id)) == taken  # the other account is untouched


async def test_a_create_that_keeps_failing_raises_after_one_retry(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    if env.db is None:
        pytest.skip("only the SQL repository has a create race")
    from sqlalchemy.exc import IntegrityError

    from tayyari.storage.repos import users as users_module

    users = env.repos.users
    taken = await users.sign_in_email(email="first@example.com", now=env.clock.now())
    monkeypatch.setattr(users_module, "new_user_id", lambda: taken.id)

    with pytest.raises(IntegrityError):
        await users.sign_in_google(
            sub="g-9",
            email="other@example.com",
            name=None,
            picture_url=None,
            locale=None,
            now=env.clock.now(),
        )
