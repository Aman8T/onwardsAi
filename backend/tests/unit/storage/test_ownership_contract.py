"""What the routes need to decide who may open a profile, run against every backend (the fakes, SQLite file and memory,
and Postgres when configured): the owner and sample flag of a profile (`ProfileRepo.get_access`), and the owner a
portfolio row carries (`StoredPortfolio.user_id`) through a create, a regenerate and an edit."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from tayyari.contract.stored import StoredContext
from tayyari.storage.ports import ProfileAccess, StoredPortfolio
from tayyari.storage.tables import ProfileRow, as_utc

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

HOUR = timedelta(hours=1)


async def add_profile(env: Env, pid: str = "p1", *, sample: bool = False) -> None:
    await env.repos.profiles.save(
        pid, {"person": {"name": "Riya"}}, StoredContext(), goal=None, owner_hash=None, is_sample=sample
    )


async def account(env: Env, email: str = "ana@example.com") -> str:
    return (await env.repos.users.sign_in_email(email=email, now=env.clock.now())).id


def page(env: Env, pid: str = "p1", *, user_id: str | None = None) -> StoredPortfolio:
    now = env.clock.now()
    return StoredPortfolio(
        profile_id=pid,
        public_id=f"pub-{pid}",
        doc={"profileId": pid},
        facts_hash=None,
        generated=[],
        version=0,
        published=False,
        created_at=now,
        updated_at=now,
        user_id=user_id,
    )


# ───────────────────────── who owns a profile ─────────────────────────


async def test_an_unknown_profile_has_no_access_record(env: Env) -> None:
    assert await env.repos.profiles.get_access("missing") is None


async def test_access_tells_anonymous_claimed_and_sample_profiles_apart(env: Env) -> None:
    ana = await account(env)
    await add_profile(env, "anon")
    await add_profile(env, "mine")
    await add_profile(env, "sample-1", sample=True)
    await env.repos.users.claim_profile(ana, "mine")

    profiles = env.repos.profiles
    assert await profiles.get_access("anon") == ProfileAccess(user_id=None, is_sample=False)
    assert await profiles.get_access("mine") == ProfileAccess(user_id=ana, is_sample=False)
    assert await profiles.get_access("sample-1") == ProfileAccess(user_id=None, is_sample=True)


async def test_saving_a_claimed_profile_again_keeps_its_owner_in_the_access_record(env: Env) -> None:
    ana = await account(env)
    await add_profile(env)
    await env.repos.users.claim_profile(ana, "p1")
    await add_profile(env)  # the analysis service saves the same id again
    assert await env.repos.profiles.get_access("p1") == ProfileAccess(user_id=ana, is_sample=False)


async def test_reading_the_access_record_does_not_count_as_opening_the_profile(env: Env) -> None:
    await add_profile(env)
    env.clock.advance(3 * HOUR)  # past the hour a read waits before it records itself
    before = await last_opened(env, "p1")
    await env.repos.profiles.get_access("p1")
    assert await last_opened(env, "p1") == before
    await env.repos.profiles.get_profile("p1")  # an actual read does
    assert await last_opened(env, "p1") > before


# ───────────────────────── whose page it is ─────────────────────────


async def test_a_page_written_with_an_owner_carries_it_on_every_read(env: Env) -> None:
    ana = await account(env)
    saved = await env.repos.portfolios.put(page(env, user_id=ana), expected_version=None)

    assert saved.user_id == ana
    got = await env.repos.portfolios.get("p1")
    assert got is not None and got.user_id == ana
    published = await env.repos.portfolios.put(
        dataclasses.replace(got, published=True), expected_version=got.version
    )
    via_public_id = await env.repos.portfolios.get_published(published.public_id)
    assert via_public_id is not None and via_public_id.user_id == ana


async def test_a_page_written_without_an_owner_has_none(env: Env) -> None:
    saved = await env.repos.portfolios.put(page(env), expected_version=None)
    assert saved.user_id is None
    got = await env.repos.portfolios.get("p1")
    assert got is not None and got.user_id is None


async def test_a_regenerate_that_names_no_owner_keeps_the_one_the_page_has(env: Env) -> None:
    ana = await account(env)
    await env.repos.portfolios.put(page(env, user_id=ana), expected_version=None)

    again = await env.repos.portfolios.put(page(env), expected_version=None)  # replaces the row outright

    assert again.version == 2 and again.user_id == ana


async def test_a_regenerate_that_names_the_owner_gives_an_unowned_page_one(env: Env) -> None:
    ana = await account(env)
    await env.repos.portfolios.put(page(env), expected_version=None)

    again = await env.repos.portfolios.put(page(env, user_id=ana), expected_version=None)

    assert again.version == 2 and again.user_id == ana


async def test_an_edit_keeps_the_owner_and_can_give_one_to_a_page_that_had_none(env: Env) -> None:
    ana = await account(env)
    owned = await env.repos.portfolios.put(page(env, "owned", user_id=ana), expected_version=None)
    bare = await env.repos.portfolios.put(page(env, "bare"), expected_version=None)

    edited_owned = await env.repos.portfolios.put(
        dataclasses.replace(owned, user_id=None, doc={"edited": True}), expected_version=owned.version
    )
    edited_bare = await env.repos.portfolios.put(
        dataclasses.replace(bare, user_id=ana, doc={"edited": True}), expected_version=bare.version
    )

    assert edited_owned.user_id == ana and edited_owned.doc == {"edited": True}
    assert edited_bare.user_id == ana


# ───────────────────────── helpers ─────────────────────────


async def last_opened(env: Env, pid: str) -> datetime:
    if env.fakes is not None:
        return env.fakes.profiles.last_accessed_at(pid)
    assert env.db is not None
    async with env.db.session() as s:
        stamp = (
            await s.execute(select(ProfileRow.last_accessed_at).where(ProfileRow.id == pid))
        ).scalar_one()
    return as_utc(stamp)
