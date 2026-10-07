"""What is specific to the dict-backed fakes (the shared behaviour lives in test_repo_contract)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from tayyari.contract.stored import StoredContext
from tayyari.storage.ports import (
    DecisionLogRepo,
    KvRepo,
    LimitRepo,
    LlmCallRepo,
    ProfileRepo,
    SessionConflict,
    SessionRepo,
)
from tayyari.storage.repos import Repositories
from tests.fakes.repos import FakeRepositories

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


async def seeded() -> tuple[FakeRepositories, str]:
    fakes = FakeRepositories()
    await fakes.profiles.save("p1", {}, StoredContext(), goal=None, owner_hash=None, is_sample=False)
    return fakes, (await fakes.sessions.create("p1", None, None)).id


def test_the_bundle_is_a_repositories_of_the_fakes() -> None:
    fakes = FakeRepositories()
    assert isinstance(fakes.bundle, Repositories)
    assert fakes.bundle.profiles is fakes.profiles
    assert fakes.bundle.limits is fakes.limits
    protocols: list[tuple[object, type]] = [
        (fakes.profiles, ProfileRepo),
        (fakes.sessions, SessionRepo),
        (fakes.llm_calls, LlmCallRepo),
        (fakes.decisions, DecisionLogRepo),
        (fakes.limits, LimitRepo),
        (fakes.kv, KvRepo),
    ]
    for fake, protocol in protocols:
        for name in (n for n in vars(protocol) if not n.startswith("_")):
            assert callable(getattr(fake, name)), f"{type(fake).__name__} lacks {name}"


async def test_one_injected_conflict_is_retried_and_two_raise() -> None:
    fakes, sid = await seeded()
    fakes.sessions.conflicts = 1
    await fakes.sessions.append_turn(sid, user="q", assistant="a", tools=[], llm_call_id=None)
    assert fakes.sessions.conflicts == 0
    loaded = await fakes.sessions.load(sid, "p1")
    assert loaded is not None and [m.seq for m in loaded.messages] == [0, 1]

    fakes.sessions.conflicts = 2
    with pytest.raises(SessionConflict):
        await fakes.sessions.append_turn(sid, user="q2", assistant="a2", tools=[], llm_call_id=None)
    loaded = await fakes.sessions.load(sid, "p1")
    assert loaded is not None and loaded.turn_count == 1  # the lost turn left no trace


async def test_documents_are_copied_in_and_out() -> None:
    fakes = FakeRepositories()
    profile = {"skills": [{"id": "a"}]}
    await fakes.profiles.save("p", profile, StoredContext(), goal=None, owner_hash=None, is_sample=False)
    profile["skills"].append({"id": "mutated afterwards"})
    stored = await fakes.profiles.get_profile("p")
    assert stored == {"skills": [{"id": "a"}]}
    assert stored is not None
    stored["skills"].clear()
    assert await fakes.profiles.get_profile("p") == {"skills": [{"id": "a"}]}


async def test_values_json_cannot_hold_are_rejected_like_a_json_column_would() -> None:
    fakes = FakeRepositories()
    with pytest.raises(TypeError):
        await fakes.kv.set("k", {"when": NOW})
    with pytest.raises(TypeError):
        await fakes.profiles.save(
            "p", {"id": uuid.uuid4()}, StoredContext(), goal=None, owner_hash=None, is_sample=False
        )


async def test_every_method_yields_to_the_event_loop() -> None:
    """So concurrent tasks interleave in service tests the way they do against a database."""
    fakes = FakeRepositories()
    order: list[str] = []

    async def hit(name: str) -> None:
        await fakes.limits.hit("b", 60, NOW)
        order.append(name)

    async def marker() -> None:
        order.append("marker")

    await asyncio.gather(hit("hit"), marker())
    assert order == ["marker", "hit"]
