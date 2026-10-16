"""The profile read in code for one search: what the scouts look for, and the evidence the reads may cite.

Nothing here calls a model. The scouts' angles come from the person's current role and next roles (their own words,
from the profile), the place from the city Discover is showing. The evidence lines are the profile's own evidence
for its skills, each already a quote of a resume line or a page, numbered ``E1``, ``E2`` ... so a read can cite them and
code can check the citation. Only skills at the match's evidence bar (score 45 and over) give evidence lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tayyari.contract.discover import Cite
from tayyari.contract.models import Profile
from tayyari.core.text import clip_words, tidy
from tayyari.discover.normalise import normalise_title, title_tokens
from tayyari.discover.places import City
from tayyari.prompts.jobsearch import EvidenceLine, ScoutBrief

EVIDENCE_MIN_SCORE: Final = 45
"""A skill below this score is a gap in the graph, not evidence (the posting match uses the same bar)."""
MAX_EVIDENCE: Final = 40
EVIDENCE_MAX_CHARS: Final = 240
MAX_SKILLS: Final = 6
"""Skill names a scout may put in its searches (the strongest first)."""
NEXT_ROLES: Final = 2
REMOTE_PLACE: Final = "remote, open to people in India"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One line a read may cite: ``E3`` is the third, ``skill`` the skill it backs, ``cite`` its source and text."""

    id: str
    skill: str
    cite: Cite

    def line(self) -> EvidenceLine:
        return EvidenceLine(id=self.id, skill=self.skill, text=self.cite.text)


@dataclass(frozen=True, slots=True)
class Angle:
    """One scout's job: the titles it searches for and where."""

    id: str
    titles: tuple[str, ...]
    remote: bool
    place: str

    @property
    def label(self) -> str:
        return f"{' or '.join(self.titles)}, {'remote in India' if self.remote else 'in ' + self.place}"


@dataclass(frozen=True, slots=True)
class Brief:
    role: str
    years: float | None
    city: City | None
    next_roles: tuple[str, ...]
    skills: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    angles: tuple[Angle, ...]

    def scout_brief(self, angle: Angle) -> ScoutBrief:
        return ScoutBrief(titles=angle.titles, place=angle.place, years=self.years, skills=self.skills)

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}


def _same_kind(a: str, b: str) -> bool:
    return title_tokens(normalise_title(a)) == title_tokens(normalise_title(b))


def _evidence(profile: Profile) -> tuple[Evidence, ...]:
    labels = {source.id: source.label for source in profile.sources}
    seen: set[str] = set()
    out: list[Evidence] = []
    for skill in sorted(profile.skills, key=lambda s: -s.score):
        if skill.score < EVIDENCE_MIN_SCORE:
            continue
        for item in skill.evidence:
            text = clip_words(item.text, EVIDENCE_MAX_CHARS)
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            cite = Cite(source=labels.get(item.sourceId, item.sourceId), text=text)
            out.append(Evidence(id=f"E{len(out) + 1}", skill=skill.name, cite=cite))
            if len(out) == MAX_EVIDENCE:
                return tuple(out)
    return tuple(out)


def build_brief(profile: Profile, city: City | None, *, scouts: int) -> Brief:
    """The brief of one search. ``city`` is the city Discover shows (None: remote roles only)."""
    person = profile.person
    role = tidy(person.currentRole)
    ranked = sorted(profile.roles, key=lambda r: -r.alignment)
    next_roles: list[str] = []
    for item in ranked:
        title = tidy(item.title)
        if title and not any(_same_kind(title, seen) for seen in [role, *next_roles] if seen):
            next_roles.append(title)
        if len(next_roles) == NEXT_ROLES:
            break
    skills = tuple(
        tidy(s.name) for s in sorted(profile.skills, key=lambda s: -s.score)[:MAX_SKILLS] if tidy(s.name)
    )

    main = role or (next_roles[0] if next_roles else "")
    step = next((title for title in next_roles if title != main), None)
    angles: list[Angle] = []
    if main:
        if city is not None:
            angles.append(Angle("role", (main,), False, city.name))
        if step is not None:
            angles.append(Angle("next", (step,), city is None, city.name if city else REMOTE_PLACE))
        angles.append(Angle("remote", (main,), True, REMOTE_PLACE))
    return Brief(
        role=role,
        years=person.yearsExperience,
        city=city,
        next_roles=tuple(next_roles),
        skills=skills,
        evidence=_evidence(profile),
        angles=tuple(angles[: max(scouts, 1)]),
    )


__all__ = ["EVIDENCE_MIN_SCORE", "REMOTE_PLACE", "Angle", "Brief", "Evidence", "build_brief"]
