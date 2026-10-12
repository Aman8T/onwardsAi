"""Posting-match storage, run against every backend (the fakes, SQLite file and memory, and Postgres when configured), so
the fake cannot drift from the SQL repositories: postings shared by hash, the facts cache, user facts, matches that only
their owner can read, the resume and cover note of a match, and what deleting an account takes along."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from tayyari.contract.match import Fact, Posting
from tayyari.contract.portfolio import Fact as PortfolioFact
from tayyari.contract.portfolio import SourceRef
from tayyari.contract.stored import StoredContext
from tayyari.storage.tables import (
    MatchRow,
    PostingRow,
    ProfileFactsRow,
    ProfileRow,
    UserFactRow,
    VariantRow,
)
from tests.unit.match.support import js

if TYPE_CHECKING:
    from tests.unit.storage.conftest import Env

HOUR = timedelta(hours=1)


def meera_posting(**changes: Any) -> Posting:
    data = js("meera.match.json")["posting"]
    return Posting.model_validate({**data, **changes})


def a_fact(fact_id: str = "user-aaaaaaaa", text: str = "I am authorised to work in India.") -> Fact:
    return Fact.model_validate(
        {
            "id": fact_id,
            "text": text,
            "kind": "other",
            "refs": [{"source": "user", "docId": "m1/q-g1", "quote": text}],
            "evidenceTier": "used",
        }
    )


async def an_account(env: Env, email: str = "ana@example.com", pid: str = "p1") -> str:
    """A user who owns the profile ``pid``; returns the user id."""
    user = await env.repos.users.sign_in_email(email=email, now=env.clock.now())
    await env.repos.profiles.save(
        pid,
        {"person": {"name": "Meera Nair"}},
        StoredContext(),
        goal="growth roles",
        owner_hash=None,
        is_sample=False,
    )
    await env.repos.users.claim_profile(user.id, pid)
    return user.id


async def a_match(
    env: Env,
    user_id: str,
    *,
    match_id: str = "m1",
    pid: str = "p1",
    posting: Posting | None = None,
    **extra: Any,
) -> str:
    posting = posting or meera_posting()
    await env.repos.postings.put(posting)
    values: dict[str, Any] = {
        "match_id": match_id,
        "user_id": user_id,
        "profile_id": pid,
        "posting_hash": posting.hash,
        "facts": {"name": "Meera Nair", "facts": [], "entries": []},
        "result": {"band": "good", "version": 1, "coverage": []},
        "band": "good",
        "blocked": False,
        "version": 1,
        "now": env.clock.now(),
    }
    values.update(extra)
    stored = await env.repos.matches.create(**values)
    return stored.id


async def count(env: Env, table: type) -> int:
    assert env.db is not None
    async with env.db.session() as s:
        return int((await s.execute(select(func.count()).select_from(table))).scalar_one())


# ───────────────────────── postings ─────────────────────────


async def test_a_posting_comes_back_exactly_as_it_was_stored_with_its_text(env: Env) -> None:
    posting = meera_posting()
    assert await env.repos.postings.get(posting.hash) is None
    await env.repos.postings.put(posting)
    assert await env.repos.postings.get(posting.hash) == posting


async def test_the_first_reading_of_a_posting_stays(env: Env) -> None:
    posting = meera_posting()
    await env.repos.postings.put(posting)
    await env.repos.postings.put(meera_posting(title="A different reading"))
    stored = await env.repos.postings.get(posting.hash)
    assert stored is not None and stored.title == posting.title


async def test_a_posting_holds_the_full_text_once_and_the_reading_without_it(env: Env) -> None:
    posting = meera_posting()
    await env.repos.postings.put(posting)
    if env.db is None:
        return
    async with env.db.session() as s:
        row = (await s.execute(select(PostingRow))).scalar_one()
    assert row.text == posting.rawText and "rawText" not in row.parsed
    assert row.parsed["title"] == posting.title


# ───────────────────────── extracted facts ─────────────────────────


def portfolio_facts() -> list[PortfolioFact]:
    return [
        PortfolioFact(
            id="f1",
            kind="experience",
            title="Growth lead",
            org="Lumen Retail",
            highlights=["Led the lifecycle team"],
            tags=["lifecycle"],
            sourceRefs=[SourceRef(sourceId="resume", quote="Led the lifecycle team")],
        )
    ]


async def test_extracted_facts_are_found_by_profile_and_hash_and_replaced_when_the_sources_change(
    env: Env,
) -> None:
    await an_account(env)
    cache = env.repos.profile_facts
    assert await cache.get("p1", "h1") is None
    facts = portfolio_facts()
    await cache.put("p1", "h1", facts)
    assert await cache.get("p1", "h1") == facts
    assert await cache.get("p1", "other-hash") is None
    assert await cache.get("p2", "h1") is None
    await cache.put("p1", "h2", [])
    assert await cache.get("p1", "h2") == []
    assert await cache.get("p1", "h1") is None  # one extraction per profile: the new one replaced it


# ───────────────────────── user facts ─────────────────────────


async def test_a_user_fact_is_one_row_per_profile_however_often_it_is_added(env: Env) -> None:
    user_id = await an_account(env)
    facts = env.repos.user_facts
    fact = a_fact()
    assert await facts.add(user_id, "p1", fact, question="Are you authorised?", answer="Yes.", match_id=None)
    assert not await facts.add(user_id, "p1", fact, question="Again?", answer="Yes!", match_id=None)
    assert await facts.list_for_profile("p1") == [fact]
    assert await facts.list_for_profile("p2") == []


async def test_user_facts_come_oldest_first_and_belong_to_their_profile(env: Env) -> None:
    user_id = await an_account(env)
    await env.repos.profiles.save(
        "p2", {"person": {"name": "Other"}}, StoredContext(), goal=None, owner_hash=None, is_sample=False
    )
    await env.repos.users.claim_profile(user_id, "p2")
    first, second, other = (
        a_fact("user-1", "First."),
        a_fact("user-2", "Second."),
        a_fact("user-1", "Elsewhere."),
    )
    await env.repos.user_facts.add(user_id, "p1", first, question="q", answer="a", match_id=None)
    env.clock.advance(HOUR)
    await env.repos.user_facts.add(user_id, "p1", second, question="q", answer="a", match_id=None)
    await env.repos.user_facts.add(
        user_id, "p2", other, question="q", answer="a", match_id=None
    )  # same id, other profile
    assert [f.text for f in await env.repos.user_facts.list_for_profile("p1")] == ["First.", "Second."]
    assert [f.text for f in await env.repos.user_facts.list_for_profile("p2")] == ["Elsewhere."]


# ───────────────────────── matches ─────────────────────────


async def test_a_match_is_read_back_by_its_owner_only(env: Env) -> None:
    ana = await an_account(env)
    bob = await an_account(env, "bob@example.com", "p2")
    await a_match(env, ana)
    mine = await env.repos.matches.get("m1", ana)
    assert mine is not None
    assert (mine.user_id, mine.profile_id, mine.band, mine.blocked, mine.version) == (
        ana,
        "p1",
        "good",
        False,
        1,
    )
    assert mine.facts == {"name": "Meera Nair", "facts": [], "entries": []}
    assert mine.result == {"band": "good", "version": 1, "coverage": []}
    assert mine.created_at == mine.updated_at == env.clock.now()
    assert await env.repos.matches.get("m1", bob) is None  # someone else's: exactly like an unknown id
    assert await env.repos.matches.get("nope", ana) is None


async def test_the_list_is_the_users_own_newest_first_with_the_postings_title_and_company(env: Env) -> None:
    ana = await an_account(env)
    bob = await an_account(env, "bob@example.com", "p2")
    await a_match(env, ana, match_id="old")
    env.clock.advance(HOUR)
    other = meera_posting(hash="b" * 64, title="Lifecycle Lead", company=None)
    await a_match(env, ana, match_id="new", posting=other, band="strong")
    await a_match(env, bob, match_id="theirs", pid="p2")
    listing = await env.repos.matches.list_for_user(ana)
    assert [(m.id, m.title, m.company, m.band) for m in listing] == [
        ("new", "Lifecycle Lead", None, "strong"),
        ("old", meera_posting().title, meera_posting().company, "good"),
    ]
    assert [m.id for m in await env.repos.matches.list_for_user(ana, limit=1)] == ["new"]
    assert [m.id for m in await env.repos.matches.list_for_user(bob)] == ["theirs"]


async def test_a_result_is_replaced_only_when_nothing_else_changed_it_first(env: Env) -> None:
    ana = await an_account(env)
    await a_match(env, ana)
    env.clock.advance(HOUR)
    update: dict[str, Any] = {
        "facts": {"name": "Meera Nair", "facts": [{"id": "x"}], "entries": []},
        "result": {"band": "strong", "version": 2},
        "band": "strong",
        "blocked": True,
        "version": 2,
        "now": env.clock.now(),
    }
    assert await env.repos.matches.save_result("m1", ana, expected_version=1, **update)
    saved = await env.repos.matches.get("m1", ana)
    assert saved is not None
    assert (saved.band, saved.blocked, saved.version, saved.result["version"]) == ("strong", True, 2, 2)
    assert saved.facts["facts"] == [{"id": "x"}] and saved.updated_at > saved.created_at
    # the same save again is a lost race: the stored version is no longer the expected one
    assert not await env.repos.matches.save_result("m1", ana, expected_version=1, **update)
    bob = await an_account(env, "bob@example.com", "p2")
    assert not await env.repos.matches.save_result("m1", bob, expected_version=2, **update)
    assert not await env.repos.matches.save_result("nope", ana, expected_version=1, **update)


async def test_facts_alone_can_be_replaced_without_touching_the_result(env: Env) -> None:
    ana = await an_account(env)
    await a_match(env, ana)
    env.clock.advance(HOUR)
    book = {"name": "Meera Nair", "facts": [{"id": "user-1"}], "entries": []}
    assert await env.repos.matches.save_facts("m1", ana, book, env.clock.now())
    saved = await env.repos.matches.get("m1", ana)
    assert saved is not None and saved.facts == book and saved.version == 1 and saved.band == "good"
    bob = await an_account(env, "bob@example.com", "p2")
    assert not await env.repos.matches.save_facts("m1", bob, {}, env.clock.now())


# ───────────────────────── variants ─────────────────────────


async def test_a_match_has_one_resume_and_one_cover_note_and_saving_again_counts_a_version(env: Env) -> None:
    ana = await an_account(env)
    await a_match(env, ana)
    store = env.repos.matches
    assert await store.get_variant("m1", "resume") is None
    first = await store.put_variant(
        "m1", "resume", spec={"header": {"name": "A"}}, status="needs_user", now=env.clock.now()
    )
    assert (first.kind, first.status, first.version) == ("resume", "needs_user", 1)
    env.clock.advance(HOUR)
    again = await store.put_variant(
        "m1", "resume", spec={"header": {"name": "B"}}, status="ready", now=env.clock.now()
    )
    assert (again.id, again.version, again.status, again.spec) == (
        first.id,
        2,
        "ready",
        {"header": {"name": "B"}},
    )
    assert again.created_at == first.created_at and again.updated_at > first.updated_at
    cover = await store.put_variant(
        "m1", "cover", spec={"paragraphs": []}, status="ready", now=env.clock.now()
    )
    assert cover.id != first.id and cover.version == 1
    assert (await store.get_variant("m1", "resume")) == again
    assert (await store.get_variant("m1", "cover")) == cover
    assert await store.get_variant("m2", "resume") is None


async def test_a_variant_needs_a_match(env: Env) -> None:
    await an_account(env)
    with pytest.raises((IntegrityError, ValueError)):
        await env.repos.matches.put_variant("ghost", "resume", spec={}, status="ready", now=env.clock.now())


# ───────────────────────── who owns a profile ─────────────────────────


async def test_a_profile_is_handed_over_only_to_its_owner(env: Env) -> None:
    ana = await an_account(env)
    bob = await an_account(env, "bob@example.com", "p2")
    profiles = env.repos.profiles
    owned = await profiles.get_owned_for_chat(ana, "p1")
    assert owned is not None and owned[0]["person"]["name"] == "Meera Nair"
    assert await profiles.get_owned_for_chat(bob, "p1") is None  # another account's
    assert await profiles.get_owned_for_chat(ana, "nope") is None
    await profiles.save("anon", {}, StoredContext(), goal=None, owner_hash="h", is_sample=False)
    assert await profiles.get_owned_for_chat(ana, "anon") is None  # nobody's yet
    await profiles.save("sample-1", {}, StoredContext(), goal=None, owner_hash=None, is_sample=True)
    assert await profiles.get_owned_for_chat(ana, "sample-1") is None


# ───────────────────────── deleting an account ─────────────────────────


async def test_deleting_an_account_removes_its_matches_resumes_and_facts_but_not_the_shared_postings(
    env: Env,
) -> None:
    ana = await an_account(env)
    bob = await an_account(env, "bob@example.com", "p2")
    await a_match(env, ana, match_id="a1")
    await a_match(env, bob, match_id="b1", pid="p2")  # the same posting, another account
    for owner, pid, mid in ((ana, "p1", "a1"), (bob, "p2", "b1")):
        await env.repos.matches.put_variant(mid, "resume", spec={"n": 1}, status="ready", now=env.clock.now())
        await env.repos.matches.put_variant(mid, "cover", spec={"n": 2}, status="ready", now=env.clock.now())
        await env.repos.user_facts.add(owner, pid, a_fact(), question="q", answer="a", match_id=mid)
        await env.repos.profile_facts.put(pid, "h", portfolio_facts())

    assert await env.repos.users.delete(ana)

    assert await env.repos.matches.get("a1", ana) is None
    assert await env.repos.matches.get_variant("a1", "resume") is None
    assert await env.repos.matches.get_variant("a1", "cover") is None
    assert await env.repos.user_facts.list_for_profile("p1") == []
    assert await env.repos.profile_facts.get("p1", "h") is None
    # the other account keeps everything, and the posting they share stays
    assert await env.repos.matches.get("b1", bob) is not None
    assert await env.repos.matches.get_variant("b1", "resume") is not None
    assert len(await env.repos.user_facts.list_for_profile("p2")) == 1
    assert await env.repos.profile_facts.get("p2", "h") is not None
    assert await env.repos.postings.get(meera_posting().hash) is not None


# ───────────────────────── the real database ─────────────────────────


async def test_the_database_cascades_when_a_profile_goes_and_refuses_a_match_without_a_posting(
    env: Env,
) -> None:
    if env.db is None:
        pytest.skip("foreign keys exist only in the real database")
    ana = await an_account(env)
    await a_match(env, ana)
    await env.repos.matches.put_variant("m1", "resume", spec={}, status="ready", now=env.clock.now())
    await env.repos.user_facts.add(ana, "p1", a_fact(), question="q", answer="a", match_id="m1")
    await env.repos.profile_facts.put("p1", "h", portfolio_facts())

    with pytest.raises(IntegrityError):
        await env.repos.matches.create(
            match_id="m2",
            user_id=ana,
            profile_id="p1",
            posting_hash="f" * 64,
            facts={},
            result={},
            band="good",
            blocked=False,
            version=1,
            now=env.clock.now(),
        )

    async with env.db.session() as s:  # the profile goes (a purge, a manual delete): everything on it follows
        await s.execute(delete(ProfileRow).where(ProfileRow.id == "p1"))
    for table in (MatchRow, VariantRow, UserFactRow, ProfileFactsRow):
        assert await count(env, table) == 0, table.__name__
    assert await count(env, PostingRow) == 1
