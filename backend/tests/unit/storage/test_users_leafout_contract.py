"""Sign in with the Leafout account, in storage: `UserRepo.sign_in_supabase` and the session fields, run against every backend
(the fakes, SQLite file and memory, Postgres when configured) so the fake cannot drift from the SQL repository."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tayyari.storage.ports import EmailTaken
from tayyari.storage.tables import UserRow
from tests.unit.storage.test_users_contract import count, session_record

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

HOUR = timedelta(hours=1)
SUB = "0b8c6f1e-3a4d-4e5f-9a1b-2c3d4e5f6a7b"
OTHER_SUB = "7d2a1c9e-5b4f-4a3e-8c6d-1f0e9d8c7b6a"


async def test_a_leafout_sign_in_creates_then_finds_by_sub(env: Env) -> None:
    users = env.repos.users
    made = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name="Ana Rao", now=env.clock.now()
    )
    env.clock.advance(HOUR)
    again = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name="Other", now=env.clock.now()
    )
    assert again.id == made.id and len(made.id) == 32
    assert (made.email, made.supabase_sub, made.google_sub, made.picture_url) == (
        "ana@example.com",
        SUB,
        None,
        None,
    )
    assert again.name == "Ana Rao" and again.last_login_at == env.clock.now()  # a name is filled in once
    assert (await users.get(made.id)) == again


async def test_a_leafout_sign_in_links_the_account_with_that_verified_email_once(env: Env) -> None:
    users = env.repos.users
    by_link = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    linked = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name="Ana", now=env.clock.now()
    )
    assert linked.id == by_link.id and linked.supabase_sub == SUB and linked.name == "Ana"
    # from then on the sub finds it, and a verified change of address on the site is followed
    again = await users.sign_in_supabase(
        sub=SUB, email="moved@example.com", email_verified=True, name=None, now=env.clock.now()
    )
    assert again.id == by_link.id and again.email == "moved@example.com"


async def test_a_leafout_sign_in_links_a_google_account_too(env: Env) -> None:
    users = env.repos.users
    google = await users.sign_in_google(
        sub="g-1", email="ana@example.com", name=None, picture_url=None, locale=None, now=env.clock.now()
    )
    linked = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name=None, now=env.clock.now()
    )
    assert linked.id == google.id and (linked.google_sub, linked.supabase_sub) == ("g-1", SUB)


async def test_a_verified_email_on_an_account_linked_to_another_sign_in_is_email_taken(env: Env) -> None:
    users = env.repos.users
    holder = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name=None, now=env.clock.now()
    )
    with pytest.raises(EmailTaken) as caught:
        await users.sign_in_supabase(
            sub=OTHER_SUB, email="ana@example.com", email_verified=True, name=None, now=env.clock.now()
        )
    assert caught.value.user_id == holder.id
    assert (await users.get(holder.id)) == holder  # nothing changed
    if env.db is not None:
        assert await count(env, UserRow) == 1  # and no account was made for the refused sign-in


async def test_an_unverified_email_never_links_and_the_new_account_has_none(env: Env) -> None:
    users = env.repos.users
    existing = await users.sign_in_email(email="ana@example.com", now=env.clock.now())
    made = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=False, name=None, now=env.clock.now()
    )
    assert made.id != existing.id and made.email is None and made.supabase_sub == SUB
    assert (await users.get(existing.id)) == existing  # untouched


async def test_a_phone_only_account_has_no_email_and_two_of_them_can_exist(env: Env) -> None:
    users = env.repos.users
    now = env.clock.now()
    first = await users.sign_in_supabase(sub=SUB, email=None, email_verified=False, name=None, now=now)
    second = await users.sign_in_supabase(sub=OTHER_SUB, email=None, email_verified=False, name=None, now=now)
    assert first.email is None and second.email is None and first.id != second.id
    again = await users.sign_in_supabase(sub=SUB, email=None, email_verified=False, name=None, now=now)
    assert again.id == first.id


async def test_the_email_change_waits_while_another_account_holds_the_new_address(env: Env) -> None:
    users = env.repos.users
    mine = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name=None, now=env.clock.now()
    )
    await users.sign_in_email(email="taken@example.com", now=env.clock.now())
    again = await users.sign_in_supabase(
        sub=SUB, email="taken@example.com", email_verified=True, name=None, now=env.clock.now()
    )
    assert again.id == mine.id and again.email == "ana@example.com"


async def test_an_unverified_change_of_email_is_ignored_for_a_known_sub(env: Env) -> None:
    users = env.repos.users
    mine = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name=None, now=env.clock.now()
    )
    again = await users.sign_in_supabase(
        sub=SUB, email="other@example.com", email_verified=False, name=None, now=env.clock.now()
    )
    assert again.id == mine.id and again.email == "ana@example.com"


async def test_a_phone_only_account_gets_an_email_once_the_site_has_a_verified_one(env: Env) -> None:
    users = env.repos.users
    phone = await users.sign_in_supabase(
        sub=SUB, email=None, email_verified=False, name=None, now=env.clock.now()
    )
    later = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name=None, now=env.clock.now()
    )
    assert later.id == phone.id and later.email == "ana@example.com"


async def test_two_first_leafout_sign_ins_at_once_make_one_account(env: Env) -> None:
    users = env.repos.users
    results = await asyncio.gather(
        *(
            users.sign_in_supabase(
                sub=SUB, email="race@example.com", email_verified=True, name=None, now=env.clock.now()
            )
            for _ in range(4)
        )
    )
    assert len({u.id for u in results}) == 1


async def test_deleting_a_phone_only_account_works(env: Env) -> None:
    users = env.repos.users
    user = await users.sign_in_supabase(
        sub=SUB, email=None, email_verified=False, name=None, now=env.clock.now()
    )
    await users.create_session(session_record(env, user.id))
    assert await users.delete(user.id) is True
    assert await users.get(user.id) is None and await users.get_session("a" * 64) is None


async def test_a_session_keeps_where_it_came_from_and_when_the_site_last_vouched_for_it(env: Env) -> None:
    users = env.repos.users
    user = await users.sign_in_supabase(
        sub=SUB, email="ana@example.com", email_verified=True, name=None, now=env.clock.now()
    )
    plain = session_record(env, user.id, "a" * 64)
    bridged = replace(
        session_record(env, user.id, "b" * 64),
        source="supabase",
        supabase_session_id="3f2e1d0c-9b8a-4766-8554-433221100ffe",
        verified_at=env.clock.now(),
    )
    await users.create_session(plain)
    await users.create_session(bridged)

    found_plain = await users.get_session(plain.id)
    found_bridged = await users.get_session(bridged.id)
    assert found_plain is not None and found_bridged is not None
    assert found_plain[0] == plain and found_plain[0].source is None and found_plain[0].verified_at is None
    assert found_bridged[0] == bridged and found_bridged[1].supabase_sub == SUB

    env.clock.advance(25 * HOUR)
    await users.mark_session_verified(bridged.id, env.clock.now())
    slid = await users.get_session(bridged.id)
    assert slid is not None and slid[0].verified_at == env.clock.now() and slid[0].source == "supabase"
    await users.mark_session_verified("f" * 64, env.clock.now())  # an unknown session is a quiet no-op
