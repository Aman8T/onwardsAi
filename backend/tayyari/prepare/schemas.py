"""What the three calls are asked to return, as Pydantic models, and the JSON Schemas built from them.

The same two rules as the posting match's schemas (`match.schemas`, whose `schema_of` and `rows_of` are reused rather
than forked): citations come first in every object (``evidenceIds`` before ``why`` and ``text``, ``have`` before a move's
``title``), and nothing a model returns is trusted: every row is validated on its own, and `prepare.verify` then re-checks
every id, name and number in code.

The schema restricts ``evidenceIds`` to the plan's evidence ids and the gap names (``name`` in standing, ``works`` in
moves) to the plan's gap names, so a model cannot cite a line that does not exist or work on a gap the plan does not have.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys the models are asked for)

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tayyari.match.schemas import rows_of, schema_of


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ───────────────────────── standing ─────────────────────────


class StandingGapOut(_Out):
    name: str = Field(description="The gap's name, copied exactly from the Gaps list.")
    evidenceIds: list[str] = Field(
        description="The evidence lines nearest to this gap, closest first, 1 to 3."
    )
    why: str = Field(
        description="One sentence, at most 180 characters: what the cited lines show and what the requirement still asks for."
    )


class StandingOut(_Out):
    headline: str = Field(
        description="One sentence, at most 160 characters, over the gaps: where the person stands for this role."
    )
    gaps: list[StandingGapOut]


# ───────────────────────── moves ─────────────────────────


class HaveOut(_Out):
    evidenceIds: list[str] = Field(description="The evidence lines this item rests on, 1 to 3.")
    text: str = Field(description="One sentence, at most 160 characters, restating what those lines show.")


class PhaseOut(_Out):
    title: str = Field(description="At most 90 characters.")
    hours: int = Field(description="Focused hours for the phase, a whole number from 1 to 40.")
    tasks: list[str] = Field(
        description="2 to 4 concrete tasks, at most 160 characters each, each starting with a verb."
    )
    deliverable: str = Field(description="What exists at the end of the phase, at most 120 characters.")


class MoveOut(_Out):
    have: list[HaveOut] = Field(
        description="What the person already has that this move builds on, 1 to 3 items."
    )
    title: str = Field(description="A concrete verb phrase naming the thing shipped, at most 90 characters.")
    body: str = Field(description="What the move is and why it closes these gaps, at most 200 characters.")
    works: list[str] = Field(
        description="The gaps this move works on, names copied exactly from the Gaps list."
    )
    phases: list[PhaseOut] = Field(description="2 to 5 phases in order.")


class MovesOut(_Out):
    moves: list[MoveOut] = Field(description="1 to 3 moves, best first, distinct in size.")


# ───────────────────────── headline ─────────────────────────


class HeadlineOut(_Out):
    evidenceIds: list[str] = Field(description="The 1 to 4 evidence lines the headline rests on.")
    headline: str = Field(description="At most 120 characters, in the shape of the current headline.")


# ───────────────────────── schemas ─────────────────────────


def standing_schema(evidence_ids: Sequence[str], gap_names: Sequence[str]) -> dict[str, Any]:
    return schema_of(StandingOut, evidenceIds=list(evidence_ids), name=list(gap_names))


def moves_schema(evidence_ids: Sequence[str], gap_names: Sequence[str]) -> dict[str, Any]:
    return schema_of(MovesOut, evidenceIds=list(evidence_ids), works=list(gap_names))


def headline_schema(evidence_ids: Sequence[str]) -> dict[str, Any]:
    return schema_of(HeadlineOut, evidenceIds=list(evidence_ids))


__all__ = [
    "HaveOut",
    "HeadlineOut",
    "MoveOut",
    "MovesOut",
    "PhaseOut",
    "StandingGapOut",
    "StandingOut",
    "headline_schema",
    "moves_schema",
    "rows_of",
    "standing_schema",
]
