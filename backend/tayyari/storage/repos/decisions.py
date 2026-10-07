"""The Jev decision log: deduplicated states, calls and decision rows, plus the export for evals.

Everything here is written in one transaction per flush, and every insert is idempotent (``ON CONFLICT``),
so the recorder can safely retry a batch. The cost of newly inserted Jev calls is added to the spend
ledger in the same transaction.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import fields
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from tayyari.contract.records import DecisionRecord, JevCallRecord, StateBlob
from tayyari.core.clock import Clock, SystemClock

from ..db import Database, dialect_insert
from ..tables import DecisionLogRow, DecisionStateRow, JevCallRow, as_utc
from .limits import JEV_SCOPE, TOTAL_SCOPE, add_spend, spend_deltas

_CHUNK = 100
"""Rows per INSERT: keeps the bound-parameter count far below SQLite's and asyncpg's limits."""
_EXPORT_PAGE = 500


def _chunks[T](items: Sequence[T]) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), _CHUNK):
        yield items[start : start + _CHUNK]


def _values(rec: JevCallRecord | DecisionRecord) -> dict[str, Any]:
    return {f.name: getattr(rec, f.name) for f in fields(rec)}


def _iso(value: datetime | None) -> str | None:
    return None if value is None else as_utc(value).isoformat()


def _str(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def decode_state(payload: bytes | None) -> Any | None:
    """The state JSON from a ``decision_states.payload`` (zlib-compressed canonical JSON), or None if purged."""
    if payload is None:
        return None
    return json.loads(zlib.decompress(payload))


def _call_export(call: JevCallRow | None) -> dict[str, Any] | None:
    if call is None:
        return None
    return {
        "id": str(call.id),
        "created_at": _iso(call.created_at),
        "pack": call.pack,
        "model_requested": call.model_requested,
        "model_served": call.model_served,
        "state_hash": call.state_hash,
        "questions": call.questions,
        "response": call.response,
        "status": call.status,
        "error_kind": call.error_kind,
        "request_id": call.request_id,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cost_micro_usd": call.cost_micro_usd,
        "latency_ms": call.latency_ms,
        "payload_purged_at": _iso(call.payload_purged_at),
    }


def _row_export(row: DecisionLogRow, call: JevCallRow | None, payload: bytes | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "created_at": _iso(row.created_at),
        "spec_id": row.spec_id,
        "spec_version": row.spec_version,
        "pack": row.pack,
        "mode": row.mode,
        "used": row.used,
        "source_used": row.source_used,
        "escalated": row.escalated,
        "escalation_reason": row.escalation_reason,
        "call_id": _str(row.call_id),
        "claude_call_id": _str(row.claude_call_id),
        "subject_ref": row.subject_ref,
        "jev_answer": row.jev_answer,
        "jev_confidence": row.jev_confidence,
        "threshold": row.threshold,
        "claude_answer": row.claude_answer,
        "incumbent": row.incumbent,
        "latency_ms": row.latency_ms,
        "cost_micro_usd": row.cost_micro_usd,
        "profile_id": row.profile_id,
        "analysis_id": _str(row.analysis_id),
        "is_sample": row.is_sample,
        "extra": row.extra or {},
        "call": _call_export(call),
        "state": decode_state(payload),
    }


class SqlDecisionLogRepo:
    def __init__(self, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def record_batch(
        self,
        states: list[StateBlob],
        calls: list[JevCallRecord],
        rows: list[DecisionRecord],
    ) -> None:
        if not (states or calls or rows):
            return
        dialect = self._db.dialect
        now = self._clock.now()
        unique_states = list({s.hash: s for s in states}.values())  # one row per hash per statement
        async with self._db.session() as s:
            for state_chunk in _chunks(unique_states):
                state_stmt = dialect_insert(dialect, DecisionStateRow).values(
                    [
                        {
                            "hash": b.hash,
                            "payload": b.payload,
                            "raw_bytes": b.raw_bytes,
                            "created_at": now,
                            "last_ref_at": now,
                            "is_sample": b.is_sample,
                        }
                        for b in state_chunk
                    ]
                )
                await s.execute(
                    state_stmt.on_conflict_do_update(
                        index_elements=[DecisionStateRow.hash],
                        set_={
                            "last_ref_at": state_stmt.excluded.last_ref_at,
                            # a payload purged by retention comes back when the same state is seen again
                            "payload": func.coalesce(DecisionStateRow.payload, state_stmt.excluded.payload),
                        },
                    )
                )

            inserted: list[tuple[datetime, int]] = []
            for call_chunk in _chunks(calls):
                call_stmt = (
                    dialect_insert(dialect, JevCallRow)
                    .values([_values(c) for c in call_chunk])
                    .on_conflict_do_nothing(index_elements=[JevCallRow.id])
                    .returning(JevCallRow.created_at, JevCallRow.cost_micro_usd)
                )
                inserted.extend(
                    (r.created_at, int(r.cost_micro_usd)) for r in (await s.execute(call_stmt)).all()
                )

            for row_chunk in _chunks(rows):
                row_stmt = (
                    dialect_insert(dialect, DecisionLogRow)
                    .values([{**_values(r), "extra": r.extra or None} for r in row_chunk])
                    .on_conflict_do_nothing(index_elements=[DecisionLogRow.id])
                )
                await s.execute(row_stmt)

            await add_spend(
                s,
                dialect,
                spend_deltas((created, cost, (TOTAL_SCOPE, JEV_SCOPE)) for created, cost in inserted),
            )

    async def export(
        self, since: datetime, until: datetime | None = None, limit: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Keyset-paginated: no connection is held while the consumer works on a page."""
        after: tuple[datetime, UUID] | None = None
        remaining = limit
        while remaining is None or remaining > 0:
            page = _EXPORT_PAGE if remaining is None else min(_EXPORT_PAGE, remaining)
            stmt = (
                select(DecisionLogRow, JevCallRow, DecisionStateRow.payload)
                .outerjoin(JevCallRow, JevCallRow.id == DecisionLogRow.call_id)
                .outerjoin(DecisionStateRow, DecisionStateRow.hash == JevCallRow.state_hash)
                .where(DecisionLogRow.created_at >= since)
                .order_by(DecisionLogRow.created_at, DecisionLogRow.id)
                .limit(page)
            )
            if until is not None:
                stmt = stmt.where(DecisionLogRow.created_at < until)
            if after is not None:
                stmt = stmt.where(
                    or_(
                        DecisionLogRow.created_at > after[0],
                        and_(DecisionLogRow.created_at == after[0], DecisionLogRow.id > after[1]),
                    )
                )
            async with self._db.session() as s:
                found = (await s.execute(stmt)).all()
            for row, call, payload in found:
                yield _row_export(row, call, payload)
            if len(found) < page:
                return
            last = found[-1][0]
            after = (last.created_at, last.id)
            if remaining is not None:
                remaining -= len(found)


__all__ = ["SqlDecisionLogRepo", "decode_state"]
