"""Areas: which of the person's own areas (the graph's domains) each requirement of a posting belongs to, for the radar.

The posting is read alone, so its ``domainHint`` is written without knowing the person's areas ("Cloud
infrastructure" for a graph that says "Backend & Cloud", "Agentic AI" for one that says "Agent & RAG Systems"), and
the small alias table of `aliases` cannot cover every profession. One Haiku call places every requirement in one of
the person's areas, or in none, naming first the person's skill it is closest to; code then re-reads every row:

  - a ``skill`` that is a skill of the graph decides the area (its own domain), whatever area the row names;
  - otherwise an ``area`` that is one of the profile's domains is kept, and ``none`` is kept as "outside the graph";
  - anything else (an unknown id, a missing row) places nothing, and the radar maps that requirement by code.

A failed or late call places nothing at all: the radar then maps every requirement by code (`radar.map_requirement`).
The placement is a classification; how much the role weighs on each area is still computed in code from the tiers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from tayyari.contract.match import Requirement
from tayyari.contract.models import Profile
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.aliases import same_term
from tayyari.match.calls import ask_or_none
from tayyari.match.schemas import AreaRowOut, AreasOut, rows_of, schema_of
from tayyari.prompts.match import AREA_SKILLS, AREAS_SYSTEM, AreaLine, build_areas_prompt

AREAS_MAX_TOKENS: Final = 2048
NONE: Final = "none"

Placement = Mapping[str, str | None]
"""requirement id → the profile domain id it belongs to, or None for "outside the person's graph". A requirement
missing from the mapping was not placed (the radar maps it by code)."""


def area_lines(profile: Profile) -> list[AreaLine]:
    """The person's areas as the prompt shows them: each domain with its strongest skills."""
    lines = []
    for domain in profile.domains:
        skills = sorted((s for s in profile.skills if s.domainId == domain.id), key=lambda s: -s.score)
        lines.append(AreaLine(id=domain.id, name=domain.name, skills=[s.name for s in skills[:AREA_SKILLS]]))
    return lines


def validate_areas(
    rows: Sequence[AreaRowOut], requirements: Sequence[Requirement], profile: Profile
) -> dict[str, str | None]:
    """The checked placement (see the module docstring); the first row of a requirement counts."""
    domains = {d.id for d in profile.domains}
    wanted = {r.id for r in requirements}
    placed: dict[str, str | None] = {}
    for row in rows:
        if row.requirementId not in wanted or row.requirementId in placed:
            continue
        by_skill = next(
            (
                s.domainId
                for s in profile.skills
                if row.skill and same_term(row.skill, s.name) and s.domainId in domains
            ),
            None,
        )
        if by_skill is not None:
            placed[row.requirementId] = by_skill
        elif row.area in domains:
            placed[row.requirementId] = row.area
        elif row.area.strip().casefold() == NONE:
            placed[row.requirementId] = None
    return placed


async def place_requirements(
    claude: ClaudeClient,
    *,
    model: str,
    profile: Profile,
    requirements: Sequence[Requirement],
    call_ctx: CallContext,
) -> dict[str, str | None]:
    """Each requirement's area among the person's own, or {} when the call failed or there is nothing to place."""
    if not requirements or not profile.domains:
        return {}
    areas = area_lines(profile)
    data = await ask_or_none(
        claude,
        model=model,
        purpose="match.areas",
        system=AREAS_SYSTEM,
        user=build_areas_prompt(requirements, areas),
        schema=schema_of(
            AreasOut,
            requirementId=[r.id for r in requirements],
            skill=["", *(n for a in areas for n in a.skills)],
            area=[*(a.id for a in areas), NONE],
        ),
        max_tokens=AREAS_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    if data is None:
        return {}
    return validate_areas(rows_of(data, "rows", AreaRowOut), requirements, profile)


__all__ = ["AREAS_MAX_TOKENS", "NONE", "Placement", "area_lines", "place_requirements", "validate_areas"]
