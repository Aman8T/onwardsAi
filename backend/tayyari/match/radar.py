"""The radar: what the role expects, domain by domain, against what the person's graph shows.

Each requirement is mapped to one of the profile's existing domains:

  1. its ``domainHint`` against the domain names (case, plural and a small synonym table: `aliases.domain_score`);
  2. else its named ``terms`` against the profile's skills, and that skill's domain;
  3. else it is unmapped. Unmapped requirements form an ``Other`` axis only when there are two or more of them.

``role`` of an axis is the weight (tier weights, never a model's number) of the requirements mapped to that domain,
scaled to 0..100 against the busiest axis. ``you`` is the profile's mastery of the domain; for ``Other`` it is the
mean coverage of those requirements, since the graph has no domain for them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from tayyari.contract.match import Coverage, Radar, RadarAxis, Requirement, tier_weight
from tayyari.contract.models import Domain, Profile
from tayyari.match.aliases import domain_score, same_term

OTHER_AXIS: Final = "Other"
MIN_OTHER: Final = 2
MIN_DOMAIN_SCORE: Final = 2
"""`domain_score` from which a hint names a domain (the same words, one containing the other, or an alias group)."""


def _best_domain(hint: str, domains: Sequence[Domain]) -> Domain | None:
    best: tuple[int, Domain] | None = None
    for domain in domains:
        score = max(domain_score(hint, domain.name), domain_score(hint, domain.short))
        if score >= MIN_DOMAIN_SCORE and (best is None or score > best[0]):
            best = (score, domain)
    return best[1] if best else None


def map_requirement(req: Requirement, profile: Profile) -> Domain | None:
    """The profile domain a requirement belongs to, or None."""
    if req.domainHint:
        found = _best_domain(req.domainHint, profile.domains)
        if found is not None:
            return found
    by_id = {d.id: d for d in profile.domains}
    for term in req.terms:
        for skill in profile.skills:
            if same_term(term, skill.name) and skill.domainId in by_id:
                return by_id[skill.domainId]
    return None


def _credit(requirement_ids: set[str], coverage: Sequence[Coverage]) -> int:
    rows = [c for c in coverage if c.requirementId in requirement_ids and c.status != "unknown"]
    return round(100 * sum(c.level for c in rows) / (4 * len(rows))) if rows else 0


def build_radar(
    profile: Profile, requirements: Sequence[Requirement], coverage: Sequence[Coverage] = ()
) -> Radar:
    """The `Radar` of a posting against a profile (see the module docstring)."""
    weights: dict[str, float] = dict.fromkeys((d.id for d in profile.domains), 0.0)
    other: list[Requirement] = []
    for req in requirements:
        domain = map_requirement(req, profile)
        if domain is None:
            other.append(req)
        else:
            weights[domain.id] += tier_weight(req.tier)
    other_weight = sum(tier_weight(r.tier) for r in other) if len(other) >= MIN_OTHER else 0.0
    peak = max([*weights.values(), other_weight], default=0.0)

    def scaled(weight: float) -> int:
        return round(100 * weight / peak) if peak > 0 else 0

    axes = [
        RadarAxis(domain=d.short or d.name, you=min(100, max(0, d.score)), role=scaled(weights[d.id]))
        for d in profile.domains
    ]
    if other_weight > 0:
        axes.append(
            RadarAxis(
                domain=OTHER_AXIS, you=_credit({r.id for r in other}, coverage), role=scaled(other_weight)
            )
        )
    return Radar(axes=axes)


__all__ = ["MIN_OTHER", "OTHER_AXIS", "build_radar", "map_requirement"]
