"""What Discover needs to know about a person, read once per request from their `Profile`.

Nothing here is stored. The profile's skills are turned into the same canonical skill ids the posting vocabulary uses
(``Postgres & Prisma`` is PostgreSQL and Prisma), and the pieces of a skill name that the vocabulary does not know become
extra terms to look for, so a person's own words count. Only a skill with a score of at least 30 is "shown" by the
profile; the cite for a skill is the first line of evidence the profile holds for it, never anything written here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from tayyari.contract.discover import Cite
from tayyari.contract.models import Profile, Skill
from tayyari.core.hashing import canonical_json, sha256_hex
from tayyari.core.text import clip_words, slug

from .normalise import normalise_title, title_tokens
from .vocab import TermMatcher, Vocabulary, prep_term

SHOWN_SCORE: Final = 30
"""A skill counts as shown by the profile at this score or above."""
THIN_PROFILE_SKILLS: Final = 3
"""Fewer skills than this and a quick fit is "Can't tell yet"."""
NEXT_ROLES: Final = 3
CITE_MAX_CHARS: Final = 90
_GENERIC: Final = frozenset(
    {
        "design", "development", "engineering", "management", "testing", "tools", "tooling", "analysis", "skills",
        "basics", "fundamentals", "other", "general", "programming", "software", "web", "data", "systems", "code",
        "coding", "patterns", "architecture", "practices", "frameworks", "framework", "research", "strategy",
        "operations", "support", "writing", "communication", "leadership", "review", "reviews", "planning", "process",
        "quality", "delivery", "integration", "optimization", "optimisation", "mentoring",
    }
)  # fmt: skip
_TRAILING: Final = frozenset(
    {"api", "apis", "development", "skills", "basics", "fundamentals", "tools", "tooling", "frameworks", "framework", "programming"}
)  # fmt: skip
_SPLIT = re.compile(r"\s*(?:&|,|;|\+|\band\b|\bor\b|\s/\s|\s\|\s)\s*", re.IGNORECASE)
_PAREN = re.compile(r"\(([^)]*)\)")
MIN_EXTRA_CHARS: Final = 3


@dataclass(frozen=True, slots=True)
class RoleTarget:
    id: str
    title: str
    tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class Shown:
    """A profile skill that covers a vocabulary skill."""

    skill: Skill
    shown: bool
    """True when the skill's score is at least `SHOWN_SCORE`."""


def _candidates(name: str) -> list[str]:
    outer = _PAREN.sub(" ", name)
    inner = _PAREN.findall(name)
    texts = [name, outer, *inner]
    for text in (outer, *inner):
        texts.extend(_SPLIT.split(text))
    seen: dict[str, None] = {}
    for text in texts:
        words = text.strip().split()
        while words:
            seen.setdefault(" ".join(words), None)
            if words[-1].lower() in _TRAILING and len(words) > 1:
                words = words[:-1]
            else:
                break
    return [c for c in seen if c]


class PersonIndex:
    """A profile read for matching: skills as canonical ids, extra terms, next roles, years and where evidence lives."""

    def __init__(self, profile: Profile, vocab: Vocabulary) -> None:
        person = profile.person
        self.profile_id = profile.id
        self.years: float | None = person.yearsExperience
        self.skill_count = len(profile.skills)
        self.location_text: str | None = person.location
        self._labels = {source.id: source.label for source in profile.sources}
        self._resume_label = next((s.label for s in profile.sources if s.kind == "resume"), None)
        self._current_role = person.currentRole.strip()
        self.current_tokens = (
            title_tokens(normalise_title(self._current_role)) if self._current_role else frozenset()
        )
        self.next_roles: tuple[RoleTarget, ...] = self._roles(profile)

        self.covered: dict[str, Shown] = {}
        """Canonical skill id -> the strongest profile skill that covers it."""
        extras: dict[str, str] = {}
        self.extra_names: dict[str, str] = {}
        for skill in profile.skills:
            for text in _candidates(skill.name):
                term = prep_term(text)
                if len(term) < 2 or term in _GENERIC:
                    continue
                found = vocab.lookup(term)
                if found is not None:
                    self._cover(found, skill)
                elif len(term) >= MIN_EXTRA_CHARS:
                    extra_id = f"x:{term}"
                    extras[term] = extra_id
                    self.extra_names.setdefault(extra_id, text.strip())
                    self._cover(extra_id, skill)
        self.extras = TermMatcher(extras)
        self.fingerprint = sha256_hex(
            canonical_json([sorted((s.name, s.score) for s in profile.skills), self.years, self.skill_count])
        )[:16]

    def _roles(self, profile: Profile) -> tuple[RoleTarget, ...]:
        ranked = sorted(profile.roles, key=lambda role: -role.alignment)
        out: list[RoleTarget] = []
        for role in ranked:
            role_id = slug(role.title)
            tokens = title_tokens(normalise_title(role.title))
            if tokens and all(existing.id != role_id for existing in out):
                out.append(RoleTarget(role_id, role.title.strip(), tokens))
            if len(out) == NEXT_ROLES:
                break
        return tuple(out)

    def _cover(self, skill_id: str, skill: Skill) -> None:
        held = self.covered.get(skill_id)
        if held is None or skill.score > held.skill.score:
            self.covered[skill_id] = Shown(skill, skill.score >= SHOWN_SCORE)

    @property
    def thin(self) -> bool:
        return self.skill_count < THIN_PROFILE_SKILLS

    @property
    def current_role(self) -> str | None:
        return self._current_role or None

    def shows(self, skill_id: str) -> Shown | None:
        """The profile skill that shows ``skill_id`` (score at least `SHOWN_SCORE`), or None."""
        held = self.covered.get(skill_id)
        return held if held is not None and held.shown else None

    def cite_for(self, skill: Skill) -> Cite | None:
        """The first line of evidence the profile holds for ``skill``, as the source's label and the text."""
        for evidence in skill.evidence:
            text = clip_words(evidence.text, CITE_MAX_CHARS)
            if text:
                return Cite(source=self._labels.get(evidence.sourceId, evidence.sourceId), text=text)
        return None

    def resume_cite(self) -> Cite | None:
        """The resume's own statement of the current role, or None when the profile has no resume or no current role."""
        if self._resume_label is None or not self._current_role:
            return None
        return Cite(source=self._resume_label, text=clip_words(self._current_role, CITE_MAX_CHARS))


__all__ = [
    "CITE_MAX_CHARS",
    "NEXT_ROLES",
    "SHOWN_SCORE",
    "THIN_PROFILE_SKILLS",
    "PersonIndex",
    "RoleTarget",
    "Shown",
]
