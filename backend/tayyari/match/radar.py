"""The radar: what the role expects, area by area, against what the person's graph shows.

Every axis is one of the profile's own domains, in the profile's order. Each requirement is placed on one of them:

  1. where the areas call put it (`areas.place_requirements`): a domain of the profile, or none (outside the graph);
  2. else, by code (`map_requirement`): its ``domainHint`` against the domain names (the same words, one inside the
     other, a distinctive word in common, an alias group), then its named ``terms`` against the profile's skills;
  3. else it is outside the graph.

``role`` of an axis is the weight (tier weights, never a model's number) of the requirements placed on it, scaled to
0..100 against the busiest axis; ``asks`` names those requirements. ``you`` is the profile's mastery of the domain.
Requirements outside the graph are never an axis: summed into one, they outweighed every real area and pulled the
role's shape towards a corner that means nothing. They are listed in ``outside`` and named beside the chart.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from tayyari.contract.match import Radar, RadarAxis, Requirement, tier_weight
from tayyari.contract.models import Domain, Profile, Skill
from tayyari.match.aliases import domain_score, name_words, same_term
from tayyari.match.areas import Placement

MIN_DOMAIN_SCORE: Final = 2
"""`domain_score` from which a hint names a domain (the same words, one containing the other, or an alias group)."""

GENERIC_WORDS: Final[frozenset[str]] = frozenset(
    [
        "engineering",
        "engineer",
        "development",
        "developer",
        "system",
        "skill",
        "professional",
        "technical",
        "foundation",
        "general",
        "experience",
        "knowledge",
        "work",
        "application",
        "tool",
        "core",
        "practice",
        "advanced",
        "basic",
        "modern",
        "applied",
        "public",
        "proof",
        "other",
    ]
)
"""Words a hint and a domain name may share without naming the same area ("Software engineering" and "Frontend
Engineering"). Folded and singular, as `aliases.name_words` returns them."""

MIN_PREFIX: Final = 5
"""Two words this long where one begins the other count as one word ("agentic" and "agent")."""


def _shares_a_word(a: Sequence[str], b: Sequence[str]) -> bool:
    for x in a:
        if x in GENERIC_WORDS:
            continue
        for y in b:
            if y in GENERIC_WORDS:
                continue
            short, long_ = sorted((x, y), key=len)
            if x == y or (len(short) >= MIN_PREFIX and long_.startswith(short)):
                return True
    return False


def _term_fits_skill(term: str, skill: Skill) -> bool:
    """The same term, or every word of the shorter one inside the longer ("RAG" and "RAG & retrieval")."""
    if same_term(term, skill.name):
        return True
    t, s = name_words(term), name_words(skill.name)
    if not t or not s:
        return False
    short, long_ = (t, s) if len(t) <= len(s) else (s, t)
    return all(w in long_ for w in short) and not all(w in GENERIC_WORDS for w in short)


def _by_terms(req: Requirement, profile: Profile) -> dict[str, int]:
    """How many of the requirement's named terms (and its hint) fit a skill of each domain."""
    hits: dict[str, int] = {}
    named = [*req.terms, *([req.domainHint] if req.domainHint else [])]
    for skill in profile.skills:
        if any(_term_fits_skill(term, skill) for term in named):
            hits[skill.domainId] = hits.get(skill.domainId, 0) + 1
    return hits


SAME_WORDS_SCORE: Final = 4
"""`domain_score` of a hint with the very same words as a domain name."""

SHARED_WORD_SCORE: Final = 2.5
"""A distinctive word the hint and a domain name share ("Cloud infrastructure" and "Backend & Cloud"): above an alias
group (2: "AI" and "LLM" are one group, which would put "Agentic AI" on any AI area), below the same words (4) and one
name inside the other (3)."""


def _hint_score(hint: str, domain: Domain) -> float:
    score = max(domain_score(hint, domain.name), domain_score(hint, domain.short))
    words = name_words(hint)
    if all(w in GENERIC_WORDS for w in words):
        # "Systems" names no area, though "Design Systems & Motion" contains it; only the very same name counts
        return score if score == SAME_WORDS_SCORE else 0
    if score > SHARED_WORD_SCORE:
        return score
    if _shares_a_word(words, name_words(domain.name)) or _shares_a_word(words, name_words(domain.short)):
        return SHARED_WORD_SCORE
    return score if score >= MIN_DOMAIN_SCORE else 0


def map_requirement(req: Requirement, profile: Profile) -> Domain | None:
    """The profile domain a requirement belongs to by code alone, or None.

    The domain its hint names best (`_hint_score`), ties going to the domain whose skills the requirement's terms fit;
    else the domain whose skills most of its terms (or its hint) fit.
    """
    terms = _by_terms(req, profile)
    if req.domainHint:
        scored = [(_hint_score(req.domainHint, d), d) for d in profile.domains]
        top = max((s for s, _ in scored), default=0)
        if top > 0:
            tied = [d for s, d in scored if s == top]
            return max(tied, key=lambda d: terms.get(d.id, 0))  # max keeps the first of equals
    if terms:
        best = max(profile.domains, key=lambda d: terms.get(d.id, 0))
        if terms.get(best.id, 0) > 0:
            return best
    return None


def build_radar(
    profile: Profile, requirements: Sequence[Requirement], placed: Placement | None = None
) -> Radar:
    """The `Radar` of a posting against a profile (see the module docstring)."""
    by_id = {d.id: d for d in profile.domains}
    weights: dict[str, float] = dict.fromkeys(by_id, 0.0)
    asks: dict[str, list[str]] = {d: [] for d in by_id}
    outside: list[str] = []
    for req in requirements:
        if placed is not None and req.id in placed:
            target = placed[req.id]
            domain = by_id.get(target) if target is not None else None
        else:
            domain = map_requirement(req, profile)
        if domain is None:
            outside.append(req.id)
            continue
        weights[domain.id] += tier_weight(req.tier)
        asks[domain.id].append(req.id)
    peak = max(weights.values(), default=0.0)

    def scaled(weight: float) -> int:
        return round(100 * weight / peak) if peak > 0 else 0

    return Radar(
        axes=[
            RadarAxis(
                domain=d.short or d.name,
                you=min(100, max(0, d.score)),
                role=scaled(weights[d.id]),
                asks=asks[d.id],
            )
            for d in profile.domains
        ],
        outside=outside,
    )


__all__ = ["GENERIC_WORDS", "build_radar", "map_requirement"]
