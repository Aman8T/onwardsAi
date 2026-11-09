"""Pydantic mirror of shared/card.ts: the radar card (R1), a person's skill shape shared as an image.

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional (``X | None = None``)
exactly when the TypeScript field is ``x?:``, and `tests/unit/contract/test_card_parity.py` fails when the two drift.

What the card carries (project-soul.md, "Lets them share proof"): the graph's areas in their own order with their 0-100
scores, up to three skills the person picks (name and score), and one role of the graph as a lighter outline of what it
expects. Never evidence text, a name, an email, a username, a rank, a percentile or a match percentage. The link is a random
slug made when sharing is turned on; the owner's account is the only one that can change it.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/card.ts)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, field_validator

from tayyari.contract.models import _Model

CardTheme = Literal["light", "dark", "transparent", "match"]
"""``match`` is a setting only: the README snippet becomes a ``<picture>`` with the dark card for dark visitors; the image
route itself draws ``light``, ``dark`` or ``transparent``."""
CardSize = Literal["md", "sm"]
CardBlocked = Literal["sample", "anonymous"]

CARD_SKILLS_MAX: Final = 3
CARD_SKILLS_OFFERED: Final = 8
CARD_AREAS_MAX: Final = 8
CARD_AREAS_MIN_RADAR: Final = 3
"""Fewer areas than this make no shape: the card draws bars and no leaf."""
SLUG_LEN: Final = 12
CARD_LINE_MAX: Final = 160
"""The one line the owner may put on the card: a sentence of their read, or their own words."""
PROFILE_ID_MAX_CHARS: Final = 64
SKILL_ID_MAX_CHARS: Final = 64
ROLE_ID_MAX_CHARS: Final = 120


def _bounded_ids(value: list[str]) -> list[str]:
    if any(len(v) > SKILL_ID_MAX_CHARS for v in value):
        raise ValueError(f"a skill id is at most {SKILL_ID_MAX_CHARS} characters")
    return value


class CardArea(_Model):
    id: str
    name: str
    short: str
    score: int
    roleTarget: int | None = None


class CardSkill(_Model):
    id: str
    name: str
    score: int


class CardRole(_Model):
    id: str
    title: str


class CardSettings(_Model):
    shared: bool
    skills: list[str] = Field(max_length=CARD_SKILLS_MAX)
    roleId: str | None = None
    theme: CardTheme
    line: str | None = Field(default=None, max_length=CARD_LINE_MAX)

    _skill_ids = field_validator("skills")(_bounded_ids)


class CardView(_Model):
    settings: CardSettings
    areas: list[CardArea]
    skillsOffered: list[CardSkill]
    rolesOffered: list[CardRole]
    linesOffered: list[str]
    blocked: CardBlocked | None = None
    slug: str | None = None
    pageUrl: str | None = None
    svgUrl: str | None = None
    ogUrl: str | None = None
    markdown: str | None = None
    updatedAt: str | None = None


class CardUpdate(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    shared: bool
    skills: list[str] = Field(max_length=CARD_SKILLS_MAX)
    roleId: str | None = Field(default=None, max_length=ROLE_ID_MAX_CHARS)
    theme: CardTheme
    line: str | None = Field(default=None, max_length=CARD_LINE_MAX)

    _skill_ids = field_validator("skills")(_bounded_ids)


class CardNewLink(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)


__all__ = [
    "CARD_AREAS_MAX",
    "CARD_AREAS_MIN_RADAR",
    "CARD_LINE_MAX",
    "CARD_SKILLS_MAX",
    "CARD_SKILLS_OFFERED",
    "PROFILE_ID_MAX_CHARS",
    "ROLE_ID_MAX_CHARS",
    "SKILL_ID_MAX_CHARS",
    "SLUG_LEN",
    "CardArea",
    "CardBlocked",
    "CardNewLink",
    "CardRole",
    "CardSettings",
    "CardSize",
    "CardSkill",
    "CardTheme",
    "CardUpdate",
    "CardView",
]
