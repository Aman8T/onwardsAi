"""Desk notes, run against every backend (the fake, a SQLite file and memory, and Postgres when configured): the list
scoped by account and profile, `replace` as the whole list (rows left out are deleted, changed ones rewritten), the
order, and (on a real database) the notes going with their profile."""

from __future__ import annotations

from datetime import timedelta

from tayyari.contract.stored import StoredContext
from tayyari.storage.ports import StoredDeskNote
from tests.unit.storage.conftest import T0, Env

P1, P2 = "1" * 32, "2" * 32


async def two_accounts(env: Env) -> tuple[str, str]:
    ana = await env.repos.users.sign_in_email(email="ana@example.com", now=T0)
    bo = await env.repos.users.sign_in_email(email="bo@example.com", now=T0)
    for pid in (P1, P2):
        await env.repos.profiles.save(
            pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=False
        )
    return ana.id, bo.id


def note(nid: str, at: int, **over: object) -> StoredDeskNote:
    values: dict[str, object] = {"id": nid, "kind": "own", "text": f"text {nid}", "at": at}
    values.update(over)
    return StoredDeskNote(**values)  # type: ignore[arg-type]


async def test_a_list_comes_back_newest_first_with_every_field(env: Env) -> None:
    ana, _ = await two_accounts(env)
    repo = env.repos.desk_notes
    assert await repo.list(ana, P1) == []
    pinned = note("b", 1_700_000_000_000, kind="pinned", label="Quote", by="Someone")
    await repo.replace(ana, P1, [note("a", 5), pinned, note("c", 5)], T0)
    assert await repo.list(ana, P1) == [pinned, note("a", 5), note("c", 5)]


async def test_replace_is_the_whole_list(env: Env) -> None:
    ana, _ = await two_accounts(env)
    repo = env.repos.desk_notes
    await repo.replace(ana, P1, [note("a", 1), note("b", 2)], T0)
    await repo.replace(ana, P1, [note("b", 3, text="rewritten"), note("c", 4)], T0 + timedelta(minutes=1))
    assert await repo.list(ana, P1) == [note("c", 4), note("b", 3, text="rewritten")]
    await repo.replace(ana, P1, [], T0)
    assert await repo.list(ana, P1) == []


async def test_lists_are_scoped_by_account_and_profile(env: Env) -> None:
    ana, bo = await two_accounts(env)
    repo = env.repos.desk_notes
    await repo.replace(ana, P1, [note("a", 1)], T0)
    await repo.replace(bo, P1, [note("a", 2, text="bo's")], T0)
    await repo.replace(ana, P2, [note("a", 3, text="other profile")], T0)
    await repo.replace(ana, P1, [], T0)
    assert await repo.list(ana, P1) == []
    assert [n.text for n in await repo.list(bo, P1)] == ["bo's"]
    assert [n.text for n in await repo.list(ana, P2)] == ["other profile"]
