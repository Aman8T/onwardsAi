"""What a radar card draws, and its version: the one shape every drawing (the README card, the LinkedIn image, the page) reads.

Built by `CardService` from the account's newest profile and stored in ``card_snapshots``; read back here with
`content_of`. It holds the areas in the graph's order with their scores, up to three named skills, and an optional role's
title with what it expects per area. Nothing else of the profile: no evidence, no name.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

from tayyari.contract.card import CARD_AREAS_MIN_RADAR
from tayyari.storage.ports import StoredCardSnapshot

VERSION_LEN: Final = 8

Shape = Literal["radar", "bars"]


@dataclass(frozen=True, slots=True)
class DrawnArea:
    name: str
    short: str
    score: int
    role_target: int | None = None


@dataclass(frozen=True, slots=True)
class DrawnSkill:
    name: str
    score: int


@dataclass(frozen=True, slots=True)
class CardContent:
    areas: tuple[DrawnArea, ...]
    skills: tuple[DrawnSkill, ...]
    role_title: str | None
    updated: date
    """When what the card draws last changed: the date printed on it."""

    @property
    def shape(self) -> Shape:
        """A radar on the leaf from three areas up; fewer make no shape, so bars."""
        return "radar" if len(self.areas) >= CARD_AREAS_MIN_RADAR else "bars"

    def strongest(self) -> DrawnArea | None:
        """The highest-scoring area; a tie goes to the first in the graph's order."""
        best: DrawnArea | None = None
        for area in self.areas:
            if best is None or area.score > best.score:
                best = area
        return best


def _score(value: Any) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return 0


def area_dict(area_id: str, area: DrawnArea) -> dict[str, Any]:
    out: dict[str, Any] = {"id": area_id, "name": area.name, "short": area.short, "score": area.score}
    if area.role_target is not None:
        out["roleTarget"] = area.role_target
    return out


def skill_dict(skill_id: str, skill: DrawnSkill) -> dict[str, Any]:
    return {"id": skill_id, "name": skill.name, "score": skill.score}


def content_of(snapshot: StoredCardSnapshot) -> CardContent:
    """The drawn content of a stored card (stored JSON is read defensively: a missing field is empty, a score is clamped)."""
    areas = tuple(
        DrawnArea(
            name=str(a.get("name") or ""),
            short=str(a.get("short") or a.get("name") or ""),
            score=_score(a.get("score")),
            role_target=None if a.get("roleTarget") is None else _score(a.get("roleTarget")),
        )
        for a in snapshot.areas
        if isinstance(a, dict)
    )
    skills = tuple(
        DrawnSkill(name=str(s.get("name") or ""), score=_score(s.get("score")))
        for s in snapshot.skills
        if isinstance(s, dict)
    )
    return CardContent(
        areas=areas,
        skills=skills,
        role_title=snapshot.role_title if snapshot.role_id else None,
        updated=snapshot.updated_at.date(),
    )


def version_of(areas: Sequence[DrawnArea], skills: Sequence[DrawnSkill], role_title: str | None) -> str:
    """The first 8 hex characters of the sha256 of what the card draws (not the theme, not the link, not the date)."""
    drawn = {
        "areas": [[a.name, a.short, a.score, a.role_target] for a in areas],
        "skills": [[s.name, s.score] for s in skills],
        "role": role_title,
    }
    blob = json.dumps(drawn, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:VERSION_LEN]


__all__ = [
    "VERSION_LEN",
    "CardContent",
    "DrawnArea",
    "DrawnSkill",
    "Shape",
    "area_dict",
    "content_of",
    "skill_dict",
    "version_of",
]
