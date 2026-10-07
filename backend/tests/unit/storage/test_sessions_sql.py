"""SQL-specific session behaviour: the optimistic append and what ends up in the tables."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select

from tayyari.contract.stored import StoredContext
from tayyari.storage.db import Database
from tayyari.storage.ports import SessionConflict, ToolEvent
from tayyari.storage.repos import build_repositories
from tayyari.storage.repos.sessions import SqlSessionRepo
from tayyari.storage.tables import ChatMessageRow, ChatSessionRow


async def make_session(db: Database) -> tuple[SqlSessionRepo, str]:
    repos = build_repositories(db)
    await repos.profiles.save("p1", {}, StoredContext(), goal=None, owner_hash=None, is_sample=False)
    session = await repos.sessions.create("p1", {"kind": "skill"}, "ab" * 8)
    assert isinstance(repos.sessions, SqlSessionRepo)
    return repos.sessions, session.id


def stale_once(repo: SqlSessionRepo, stale_seq: int, calls: list[int]) -> Callable[..., Awaitable[int]]:
    """A ``_next_seq`` that reports an out-of-date number the first time, as if another turn had just won."""
    real = repo._next_seq

    async def next_seq(session: Any, sid: str) -> int:
        calls.append(len(calls))
        return stale_seq if len(calls) == 1 else await real(session, sid)

    return next_seq


async def test_a_lost_race_reloads_and_retries_once(
    file_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sid = await make_session(file_db)
    await repo.append_turn(sid, user="q0", assistant="a0", tools=[], llm_call_id=None)  # seq 0 and 1 exist

    calls: list[int] = []
    monkeypatch.setattr(repo, "_next_seq", stale_once(repo, 0, calls))  # the first attempt tries seq 0 again
    await repo.append_turn(sid, user="q1", assistant="a1", tools=[], llm_call_id=None)

    assert len(calls) == 2  # one conflict, one successful retry
    loaded = await repo.load(sid, "p1")
    assert loaded is not None
    assert [(m.seq, m.content) for m in loaded.messages] == [(0, "q0"), (1, "a0"), (2, "q1"), (3, "a1")]
    assert loaded.turn_count == 2  # the failed attempt rolled its counter bump back


async def test_a_second_lost_race_raises_session_conflict(
    file_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sid = await make_session(file_db)
    await repo.append_turn(sid, user="q0", assistant="a0", tools=[], llm_call_id=None)

    attempts: list[int] = []

    async def always_stale(_session: Any, _sid: str) -> int:
        attempts.append(1)
        return 0

    monkeypatch.setattr(repo, "_next_seq", always_stale)
    with pytest.raises(SessionConflict):
        await repo.append_turn(sid, user="q1", assistant="a1", tools=[], llm_call_id=None)

    assert len(attempts) == 2  # exactly one retry
    monkeypatch.undo()
    loaded = await repo.load(sid, "p1")
    assert loaded is not None
    assert [m.seq for m in loaded.messages] == [0, 1]  # nothing half-written
    assert loaded.turn_count == 1


async def test_rows_carry_tools_call_ids_and_timestamps(file_db: Database) -> None:
    repo, sid = await make_session(file_db)
    call_id = uuid.uuid4()
    tools = [ToolEvent("WebSearch", "figma jobs")]
    await repo.append_turn(sid, user="q", assistant="a", tools=tools, llm_call_id=call_id)
    await repo.append_turn(sid, user="only a question", assistant=None, tools=[], llm_call_id=None)

    async with file_db.session() as s:
        messages = (await s.execute(select(ChatMessageRow).order_by(ChatMessageRow.seq))).scalars().all()
        session_row = (await s.execute(select(ChatSessionRow).where(ChatSessionRow.id == sid))).scalar_one()
    assert [(m.role, m.tools, m.llm_call_id) for m in messages] == [
        ("user", None, None),
        ("assistant", [{"name": "WebSearch", "detail": "figma jobs"}], call_id),
        ("user", None, None),
    ]
    assert all(m.created_at.tzinfo is not None for m in messages)
    assert session_row.updated_at >= session_row.created_at
    assert (session_row.turn_count, session_row.ip_hash, session_row.origin) == (
        2,
        "ab" * 8,
        {"kind": "skill"},
    )
