"""Chat sessions and messages.

Appends are optimistic on the ``(session_id, seq)`` primary key: a turn reads the next ``seq``, inserts
its messages and, if another turn got there first, reloads and tries once more before raising
`SessionConflict`. Each attempt starts with the ``UPDATE`` of the session row, which takes the write lock
on SQLite and a row lock on Postgres, so in practice concurrent turns on one session queue up behind each
other instead of colliding; the conflict path is the safety net for everything that gets past that.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tayyari.core.clock import Clock, SystemClock
from tayyari.core.ids import new_session_id
from tayyari.core.logging import get_logger

from ..db import Database, rowcount
from ..ports import ChatMessage, ChatSession, SessionConflict, SessionNotFound, ToolEvent
from ..tables import ChatMessageRow, ChatSessionRow

log = get_logger(__name__)


def _tools_to_json(tools: list[ToolEvent]) -> list[dict[str, str]] | None:
    return [{"name": t.name, "detail": t.detail} for t in tools] or None


def _tools_from_json(raw: list[dict[str, str]] | None) -> list[ToolEvent]:
    return [ToolEvent(name=str(t.get("name", "")), detail=str(t.get("detail", ""))) for t in raw or []]


class SqlSessionRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def load(self, sid: str, profile_id: str, limit: int = 40) -> ChatSession | None:
        session_stmt = select(
            ChatSessionRow.id, ChatSessionRow.profile_id, ChatSessionRow.origin, ChatSessionRow.turn_count
        ).where(ChatSessionRow.id == sid, ChatSessionRow.profile_id == profile_id)
        message_stmt = (
            select(ChatMessageRow.seq, ChatMessageRow.role, ChatMessageRow.content, ChatMessageRow.tools)
            .where(ChatMessageRow.session_id == sid)
            .order_by(ChatMessageRow.seq.desc())
            .limit(max(limit, 0))
        )
        async with self._db.session() as s:
            head = (await s.execute(session_stmt)).one_or_none()
            if head is None:
                return None
            rows = (await s.execute(message_stmt)).all()
        messages = [
            ChatMessage(seq=r.seq, role=r.role, content=r.content, tools=_tools_from_json(r.tools))
            for r in reversed(rows)
        ]
        return ChatSession(
            id=head.id,
            profile_id=head.profile_id,
            origin=head.origin,
            turn_count=head.turn_count,
            messages=messages,
        )

    async def create(
        self, profile_id: str, origin: dict[str, Any] | None, ip_hash: str | None
    ) -> ChatSession:
        sid = new_session_id()
        now = self._clock.now()
        async with self._db.session() as s:
            await s.execute(
                insert(ChatSessionRow).values(
                    id=sid,
                    profile_id=profile_id,
                    origin=origin,
                    created_at=now,
                    updated_at=now,
                    turn_count=0,
                    ip_hash=ip_hash,
                )
            )
        return ChatSession(id=sid, profile_id=profile_id, origin=origin, turn_count=0, messages=[])

    async def append_turn(
        self,
        sid: str,
        *,
        user: str,
        assistant: str | None,
        tools: list[ToolEvent],
        llm_call_id: UUID | None,
    ) -> None:
        for attempt in (1, 2):
            try:
                await self._append_once(
                    sid, user=user, assistant=assistant, tools=tools, llm_call_id=llm_call_id
                )
            except IntegrityError:
                log.warning("chat_append_conflict", attempt=attempt, session_id=sid)
                continue
            return
        raise SessionConflict(f"could not append to session {sid}: lost the (session_id, seq) race twice")

    async def _append_once(
        self,
        sid: str,
        *,
        user: str,
        assistant: str | None,
        tools: list[ToolEvent],
        llm_call_id: UUID | None,
    ) -> None:
        now = self._clock.now()
        async with self._db.session() as s:
            # Write first (see the module docstring): this is also where an unknown session shows up.
            bumped = await s.execute(
                update(ChatSessionRow)
                .where(ChatSessionRow.id == sid)
                .values(turn_count=ChatSessionRow.turn_count + 1, updated_at=now)
            )
            if rowcount(bumped) == 0:
                raise SessionNotFound(sid)
            seq = await self._next_seq(s, sid)
            user_row: dict[str, Any] = {
                "session_id": sid,
                "seq": seq,
                "role": "user",
                "content": user,
                "tools": None,
                "llm_call_id": None,
                "created_at": now,
            }
            rows = [user_row]
            payload = {"tools": _tools_to_json(tools), "llm_call_id": llm_call_id}
            if assistant is None:
                user_row.update(payload)
            else:
                rows.append(
                    {
                        "session_id": sid,
                        "seq": seq + 1,
                        "role": "assistant",
                        "content": assistant,
                        "created_at": now,
                        **payload,
                    }
                )
            await s.execute(insert(ChatMessageRow).values(rows))

    async def _next_seq(self, session: AsyncSession, sid: str) -> int:
        """One past the highest ``seq`` of the session (0 for an empty one)."""
        top = (
            await session.execute(
                select(func.max(ChatMessageRow.seq)).where(ChatMessageRow.session_id == sid)
            )
        ).scalar_one()
        return 0 if top is None else int(top) + 1


__all__ = ["SqlSessionRepo"]
