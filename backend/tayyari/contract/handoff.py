"""Pydantic mirror of shared/handoff.ts: what the marketing site hands to the app.

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional (``X | None = None``)
exactly when the TypeScript field is ``x?:`` (absent on output, never ``null``), and
`tests/unit/contract/test_handoff_parity.py` fails when the two drift.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/handoff.ts)

from __future__ import annotations

from typing import Final, Literal

from tayyari.contract.models import _Model

HANDOFF_LINKS_MAX: Final = 10
HANDOFF_LINK_MAX_CHARS: Final = 2000
HANDOFF_GOAL_MAX_CHARS: Final = 300
HANDOFF_NOTES_MAX_CHARS: Final = 4000

HandoffErrorCode = Literal["origin", "too_large", "type", "empty", "rate"]


class HandoffResume(_Model):
    name: str
    type: str
    dataBase64: str


class HandoffPayload(_Model):
    links: list[str]
    goal: str | None = None
    notes: str | None = None
    resume: HandoffResume | None = None


__all__ = [
    "HANDOFF_GOAL_MAX_CHARS",
    "HANDOFF_LINKS_MAX",
    "HANDOFF_LINK_MAX_CHARS",
    "HANDOFF_NOTES_MAX_CHARS",
    "HandoffErrorCode",
    "HandoffPayload",
    "HandoffResume",
]
