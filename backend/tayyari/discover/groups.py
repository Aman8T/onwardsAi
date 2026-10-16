"""Grouping listings by why each fits, in a fixed order of precedence. The first group a listing fits is its home; the others
go to ``alsoFits`` and are never counted twice.

    1  next_role:{slug}   the person's top three next roles by alignment, when the listing's title is close to the role's
    2  current            close to the role they hold now
    3  skill_bridge:{id}  the profile shows 3 or more of the skills named and not 1 or 2 others; grouped by the most common
                          missing skill, and only when at least 2 listings share it
    4  first_steps        entry-level roles, only for someone with under 2 years or none stated
    5  other              the rest

"Close" is token-set similarity of the normalised titles (seniority words kept, filler and level numerals dropped) of at
least one half. Everything here is plain arithmetic over titles and skill names: no model, nothing stored.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from tayyari.contract.discover import Reason
from tayyari.contract.models import Skill

from .models import KIND_ORDER, Candidate, GroupDef
from .normalise import sentence_case, title_similarity
from .person import PersonIndex

CLOSE_TITLE: Final = 0.5
BRIDGE_MIN_MATCHED: Final = 3
BRIDGE_MAX_MISSING: Final = 2
BRIDGE_MIN_LISTINGS: Final = 2
FIRST_STEPS_BELOW_YEARS: Final = 2
OTHER: Final = GroupDef("other", "other", "Other roles that look close")
FIRST_STEPS: Final = GroupDef("first_steps", "first_steps", "First steps")
CURRENT: Final = GroupDef("current", "current", "Roles like yours")


def role_groups(person: PersonIndex) -> list[GroupDef]:
    return [
        GroupDef(f"next_role:{role.id}", "next_role", sentence_case(role.title), rank=index)
        for index, role in enumerate(person.next_roles)
    ]


def static_claims(
    candidate: Candidate, person: PersonIndex, roles: Sequence[GroupDef]
) -> tuple[GroupDef, ...]:
    """The groups a listing fits by its title and level alone (they do not depend on which other listings are shown)."""
    claims: list[GroupDef] = []
    for role, group in zip(person.next_roles, roles, strict=True):
        if title_similarity(candidate.tokens, role.tokens) >= CLOSE_TITLE:
            claims.append(group)
    if person.current_tokens and title_similarity(candidate.tokens, person.current_tokens) >= CLOSE_TITLE:
        claims.append(CURRENT)
    return tuple(claims)


def _first_steps(candidate: Candidate, person: PersonIndex) -> bool:
    new_to_work = person.years is None or person.years < FIRST_STEPS_BELOW_YEARS
    return new_to_work and candidate.row.entry_level


@dataclass(slots=True)
class Arrangement:
    """Where each listing of a pool lives."""

    home: dict[str, GroupDef] = field(default_factory=dict)
    claims: dict[str, tuple[GroupDef, ...]] = field(default_factory=dict)
    """Every group a listing fits, in precedence order (``other`` is nobody's claim)."""

    def kinds(self, listing_id: str) -> frozenset[str]:
        return frozenset(group.kind for group in self.claims.get(listing_id, ()))


def arrange(
    pool: Sequence[Candidate],
    person: PersonIndex,
    static: dict[str, tuple[GroupDef, ...]],
) -> Arrangement:
    """Give every listing of ``pool`` a home group and the other groups it fits.

    ``static`` holds the title-based claims by listing id (`static_claims`), computed once for all listings.
    """
    qualifying = [
        c
        for c in pool
        if c.fit.matched >= BRIDGE_MIN_MATCHED and 1 <= len(c.fit.missing) <= BRIDGE_MAX_MISSING
    ]
    frequency = Counter(skill_id for c in qualifying for skill_id, _ in c.fit.missing)
    chosen: dict[str, tuple[str, str]] = {}
    for c in qualifying:
        options = [
            (index, skill_id, name)
            for index, (skill_id, name) in enumerate(c.fit.missing)
            if frequency[skill_id] >= 2
        ]
        if options:
            _, skill_id, name = max(options, key=lambda o: (frequency[o[1]], -o[0], o[2]))
            chosen[c.row.id] = (skill_id, name)
    sizes = Counter(skill_id for skill_id, _ in chosen.values())
    bridges: dict[str, GroupDef] = {
        skill_id: GroupDef(
            f"skill_bridge:{skill_id}",
            "skill_bridge",
            f"One skill away: {name}",
            rank=-sizes[skill_id],
            skill_id=skill_id,
            skill_name=name,
        )
        for skill_id, name in chosen.values()
        if sizes[skill_id] >= BRIDGE_MIN_LISTINGS
    }
    arrangement = Arrangement()
    for c in pool:
        claims = list(static.get(c.row.id, ()))
        picked = chosen.get(c.row.id)
        if picked is not None and picked[0] in bridges:
            claims.append(bridges[picked[0]])
        if _first_steps(c, person):
            claims.append(FIRST_STEPS)
        claims.sort(key=lambda g: (KIND_ORDER[g.kind], g.rank, g.id))
        arrangement.claims[c.row.id] = tuple(claims)
        arrangement.home[c.row.id] = claims[0] if claims else OTHER
    return arrangement


def group_reason(group: GroupDef, members: Sequence[Candidate], person: PersonIndex) -> Reason:
    """The one sentence that says why a group matters to this person, citing their evidence where it states a fact."""
    if group.kind == "next_role":
        counts = Counter(skill_id for c in members for skill_id in c.fit.matched_ids)
        ranked = sorted(counts, key=lambda s: (-counts[s], -(person.covered[s].skill.score), s))
        skills: list[Skill] = []
        for skill_id in ranked:
            shown = person.shows(skill_id)
            if shown is not None and all(shown.skill.id != s.id for s in skills):
                skills.append(shown.skill)
            if len(skills) == 2:
                break
        if not skills:
            return Reason(text="Your next step.")
        names = " and ".join(skill.name for skill in skills)
        verb = "show" if len(skills) > 1 else "shows"
        return Reason(
            text=f"Your next step. {names} {verb} up in your evidence.", cite=person.cite_for(skills[0])
        )
    if group.kind == "current":
        return Reason(text="The work you do now.", cite=person.resume_cite())
    if group.kind == "skill_bridge":
        return Reason(
            text=f"{group.skill_name} shows up in {len(members)} of these. Your evidence doesn't show it yet."
        )
    if group.kind == "first_steps":
        return Reason(text="Entry roles that ask for what your evidence shows.")
    return Reason(
        text="Their titles differ from yours, but the skills they name are ones your evidence shows."
    )


__all__ = [
    "BRIDGE_MIN_LISTINGS",
    "CLOSE_TITLE",
    "CURRENT",
    "FIRST_STEPS",
    "OTHER",
    "Arrangement",
    "arrange",
    "group_reason",
    "role_groups",
    "static_claims",
]
