"""The Anthropic call log and its share of the spend ledger."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from tayyari.contract.records import LlmCallRecord

from ..db import Database, dialect_insert
from ..tables import LlmCallRow
from .limits import ANTHROPIC_SCOPE, TOTAL_SCOPE, add_spend, spend_deltas


def llm_call_values(rec: LlmCallRecord) -> dict[str, Any]:
    """Column values for an ``llm_calls`` row (the record's fields are the column names)."""
    return {f.name: getattr(rec, f.name) for f in fields(rec)}


class SqlLlmCallRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, rec: LlmCallRecord) -> None:
        """Insert the call and add its cost to ``total`` and ``anthropic`` in one transaction.

        ``ON CONFLICT DO NOTHING`` makes a repeated ``rec.id`` a no-op, and the ledger is only touched when
        a row was really inserted, so a retry can never count a call twice.
        """
        dialect = self._db.dialect
        insert_stmt = (
            dialect_insert(dialect, LlmCallRow)
            .values(**llm_call_values(rec))
            .on_conflict_do_nothing(index_elements=[LlmCallRow.id])
            .returning(LlmCallRow.id)
        )
        async with self._db.session() as s:
            inserted = (await s.execute(insert_stmt)).scalar_one_or_none()
            if inserted is None:
                return
            deltas = spend_deltas([(rec.created_at, rec.cost_micro_usd, (TOTAL_SCOPE, ANTHROPIC_SCOPE))])
            await add_spend(s, dialect, deltas)


__all__ = ["SqlLlmCallRepo", "llm_call_values"]
