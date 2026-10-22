"""Radar cards (R1), run against every backend (the fake, a SQLite file and memory, and Postgres when configured), so
`FakeCardRepo` cannot drift from `SqlCardRepo`: one card per account that keeps when it began, a slug found only while
shared, a slug never held by two cards, pages in user id order, copies in and out, and (on a real database) the card going
with its account."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tayyari.contract.stored import StoredContext
from tayyari.storage.ports import StoredCardSnapshot
from tests.unit.storage.conftest import T0, Env

P1, P2 = "1" * 32, "2" * 32
AREAS = [
    {"id": "frontend", "name": "Frontend engineering", "short": "Frontend", "score": 72},
    {"id": "data", "name": "Data", "short": "Data", "score": 41, "roleTarget": 60},
]
SKILLS = [{"id": "react", "name": "React", "score": 81}]


async def account(env: Env, email: str = "ana@example.com", pid: str = P1) -> str:
    user = await env.repos.users.sign_in_email(email=email, now=T0)
    await env.repos.profiles.save(
        pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
    )
    return user.id


def card(user_id: str, profile_id: str = P1, **over: Any) -> StoredCardSnapshot:
    values: dict[str, Any] = {
        "user_id": user_id,
        "profile_id": profile_id,
        "slug": "abcdefgh1234",
        "shared": True,
        "theme": "match",
        "areas": AREAS,
        "skills": SKILLS,
        "role_id": "senior-frontend",
        "role_title": "Senior frontend engineer",
        "version": "0a1b2c3d",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(over)
    return StoredCardSnapshot(**values)


async def test_a_card_comes_back_whole_by_account_and_by_slug(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.card
    assert await repo.get(uid) is None
    await repo.save(card(uid))
    assert await repo.get(uid) == card(uid)
    assert await repo.get_by_slug("abcdefgh1234") == card(uid)
    assert await repo.get_by_slug("zzzzzzzzzzzz") is None


async def test_a_card_not_shared_has_no_link(env: Env) -> None:
    uid = await account(env)
    await env.repos.card.save(card(uid, slug=None, shared=False, role_id=None, role_title=None, skills=[]))
    found = await env.repos.card.get(uid)
    assert found is not None and found.slug is None and found.role_id is None and found.skills == []
    assert await env.repos.card.get_by_slug("abcdefgh1234") is None


async def test_a_slug_on_a_card_turned_off_is_not_found(env: Env) -> None:
    """Belt and braces: the service clears the slug when sharing stops, and the lookup also asks for ``shared``."""
    uid = await account(env)
    await env.repos.card.save(card(uid, shared=False))
    assert await env.repos.card.get_by_slug("abcdefgh1234") is None


async def test_saving_again_replaces_the_card_and_keeps_when_it_began(env: Env) -> None:
    uid = await account(env)
    repo = env.repos.card
    await repo.save(card(uid))
    later = T0 + timedelta(days=2)
    await repo.save(
        card(uid, slug="newslug98765", theme="dark", version="ffffffff", created_at=later, updated_at=later)
    )
    found = await repo.get(uid)
    assert found == card(uid, slug="newslug98765", theme="dark", version="ffffffff", updated_at=later)
    assert await repo.get_by_slug("abcdefgh1234") is None
    assert await repo.get_by_slug("newslug98765") == found


async def test_two_cards_never_share_a_slug(env: Env) -> None:
    ana, ben = await account(env), await account(env, "ben@example.com", P2)
    await env.repos.card.save(card(ana))
    with pytest.raises(Exception):  # noqa: B017  (the fake raises ValueError, SQL an IntegrityError)
        await env.repos.card.save(card(ben, P2))
    await env.repos.card.save(card(ben, P2, slug=None, shared=False))
    await env.repos.card.save(card(ana, slug=None, shared=False))
    assert [c.user_id for c in await env.repos.card.list_page(None, 10)] == sorted([ana, ben])


async def test_pages_walk_the_cards_in_user_id_order(env: Env) -> None:
    ids = []
    for n, email in enumerate(("a@example.com", "b@example.com", "c@example.com")):
        pid = str(n + 1) * 32
        uid = await account(env, email, pid)
        await env.repos.card.save(card(uid, pid, slug=f"slug{n:08d}"))
        ids.append(uid)
    ids.sort()
    first = await env.repos.card.list_page(None, 2)
    assert [c.user_id for c in first] == ids[:2]
    rest = await env.repos.card.list_page(first[-1].user_id, 2)
    assert [c.user_id for c in rest] == ids[2:]
    assert await env.repos.card.list_page(ids[-1], 2) == []


async def test_reads_are_copies(env: Env) -> None:
    uid = await account(env)
    await env.repos.card.save(card(uid))
    got = await env.repos.card.get(uid)
    assert got is not None
    got.areas[0]["score"] = 1
    got.skills.clear()
    assert await env.repos.card.get(uid) == card(uid)


async def test_deleting_the_account_takes_its_card_with_it(env: Env) -> None:
    if env.kind == "fake":
        pytest.skip("the foreign key cascade is the database's")
    uid = await account(env)
    await env.repos.card.save(card(uid))
    assert await env.repos.users.delete(uid)
    assert await env.repos.card.get(uid) is None
    assert await env.repos.card.get_by_slug("abcdefgh1234") is None
