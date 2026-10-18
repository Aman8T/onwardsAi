"""The plan's numbers and its evidence, all from the graph and in code: no model is involved.

`assemble` turns one role of a `Profile` into a `PreparePlan` with no moves and no whys yet (the ``gaps`` event):

- gaps are the role's requirements the graph does not meet (``have`` false, or ``current`` under ``target``), ordered by
  ``target - current`` descending; exactly one is ``biggest`` (ties: the first);
- strong are the requirements met (``have`` and ``current`` at or above ``target``);
- scope ``requirement`` keeps only that gap (a requirement already met is `AlreadyStrong`; a role with no gap at all too);
- ``rank`` and ``roleCount`` come from the roles sorted by alignment (1 = closest), never a percentage;
- the evidence list is every `Skill.evidence` line, numbered ``e1``...: the skills the role's requirements point to first
  (by ``skillId``, else by name), then the other skills by score, at most `MAX_EVIDENCE` lines; each line names its
  source's kind and the chip's label ("your resume", "your GitHub").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final

from tayyari.contract.models import Profile, Role, RoleRequirement, Skill
from tayyari.contract.prepare import (
    DEFAULT_HOURS_PER_WEEK,
    PrepareEvidence,
    PrepareGap,
    PreparePlan,
    PrepareState,
    PrepareStrong,
)
from tayyari.contract.stored import StoredContext
from tayyari.prepare.errors import (
    ALREADY_STRONG_REQUIREMENT,
    ALREADY_STRONG_ROLE,
    AlreadyStrong,
    RequirementNotFound,
    RoleNotFound,
)

MAX_EVIDENCE: Final = 40
"""Evidence lines a plan carries (the model's citations draw on these; a graph has about 20 skills with 1 to 3 lines)."""

SOURCE_LABELS: Final[Mapping[str, str]] = {
    "resume": "your resume",
    "github": "your GitHub",
    "lovable": "your Lovable app",
    "linkedin": "your LinkedIn",
    "portfolio": "your portfolio",
}
"""How the evidence chip names a source kind; any other kind shows the source's own label."""
UNKNOWN_SOURCE: Final = ("link", "a page")


def is_met(req: RoleRequirement) -> bool:
    """A requirement the graph meets: marked ``have`` and at or above its target."""
    return req.have and req.current >= req.target


def ranked_roles(profile: Profile) -> list[Role]:
    """The roles by alignment, closest first (ties keep the graph's order)."""
    return sorted(profile.roles, key=lambda r: -r.alignment)


def find_role(profile: Profile, role_id: str) -> Role:
    for role in profile.roles:
        if role.id == role_id:
            return role
    raise RoleNotFound()


def _source_names(profile: Profile, context: StoredContext) -> dict[str, tuple[str, str]]:
    """``source id -> (kind, chip label)`` from the stored context first, then the profile's own source summaries."""
    out: dict[str, tuple[str, str]] = {}
    for summary in profile.sources:
        out[summary.id] = (summary.kind, SOURCE_LABELS.get(summary.kind, summary.label))
    for source in context.sources:
        out[source.id] = (source.kind, SOURCE_LABELS.get(source.kind, source.label))
    return out


def source_labels(profile: Profile, context: StoredContext) -> dict[str, str]:
    """``source id -> chip label`` ("your GitHub"), for the prompts' reader notes."""
    return {sid: label for sid, (_kind, label) in _source_names(profile, context).items()}


def _skills_for(requirements: Sequence[RoleRequirement], profile: Profile) -> list[Skill]:
    """The skills the requirements point to, in requirement order: by ``skillId``, else by name (case-insensitive)."""
    by_id = {s.id: s for s in profile.skills}
    by_name = {s.name.strip().casefold(): s for s in profile.skills}
    found: list[Skill] = []
    for req in requirements:
        skill = by_id.get(req.skillId) if req.skillId else None
        if skill is None:
            skill = by_name.get(req.name.strip().casefold())
        if skill is not None and all(skill.id != f.id for f in found):
            found.append(skill)
    return found


def evidence_lines(
    profile: Profile,
    context: StoredContext,
    *,
    first: Sequence[RoleRequirement],
    limit: int = MAX_EVIDENCE,
) -> list[PrepareEvidence]:
    """The plan's evidence: the lines of the skills ``first`` points to, then the other skills' by score, numbered."""
    names = _source_names(profile, context)
    linked = _skills_for(first, profile)
    linked_ids = {s.id for s in linked}
    rest = sorted((s for s in profile.skills if s.id not in linked_ids), key=lambda s: -s.score)
    seen: set[tuple[str, str]] = set()
    out: list[PrepareEvidence] = []
    for skill in (*linked, *rest):
        for line in skill.evidence:
            text = " ".join(line.text.split())
            key = (line.sourceId, text.casefold())
            if not text or key in seen:
                continue
            seen.add(key)
            kind, label = names.get(line.sourceId, UNKNOWN_SOURCE)
            out.append(
                PrepareEvidence(
                    id=f"e{len(out) + 1}",
                    sourceId=line.sourceId,
                    sourceKind=kind,
                    sourceLabel=label,
                    skill=skill.name,
                    text=text,
                )
            )
            if len(out) >= limit:
                return out
    return out


def _gap(req: RoleRequirement, *, biggest: bool) -> PrepareGap:
    return PrepareGap(
        name=req.name, skillId=req.skillId, current=req.current, target=req.target, biggest=biggest
    )


def default_state() -> PrepareState:
    return PrepareState(hoursPerWeek=DEFAULT_HOURS_PER_WEEK, moveIndex=0, done=[], links=[])


def assemble(
    profile: Profile,
    context: StoredContext,
    *,
    role_id: str,
    requirement: str | None,
    plan_id: str,
    created_at: datetime,
) -> PreparePlan:
    """The plan for ``role_id`` (and one ``requirement`` of it), with no moves and no whys yet.

    Raises `RoleNotFound`, `RequirementNotFound` (a name that is not one of the role's requirements) and
    `AlreadyStrong` (the requirement is met, or the whole role is when no requirement is named).
    """
    role = find_role(profile, role_id)
    ranked = ranked_roles(profile)
    rank = next(i for i, r in enumerate(ranked, start=1) if r.id == role.id)
    met = [r for r in role.requirements if is_met(r)]
    unmet = sorted((r for r in role.requirements if not is_met(r)), key=lambda r: -(r.target - r.current))

    wanted = (requirement or "").strip()
    if wanted:
        chosen = next((r for r in role.requirements if r.name.strip().casefold() == wanted.casefold()), None)
        if chosen is None:
            raise RequirementNotFound()
        if is_met(chosen):
            raise AlreadyStrong(ALREADY_STRONG_REQUIREMENT)
        unmet = [chosen]
    elif not unmet:
        raise AlreadyStrong(ALREADY_STRONG_ROLE)

    gaps = [_gap(r, biggest=(i == 0)) for i, r in enumerate(unmet)]
    strong = [PrepareStrong(name=r.name, current=r.current, target=r.target) for r in met]
    evidence = evidence_lines(profile, context, first=[*unmet, *met])
    return PreparePlan(
        id=plan_id,
        profileId=profile.id,
        roleId=role.id,
        roleTitle=role.title,
        scope="requirement" if wanted else "role",
        requirement=unmet[0].name if wanted else None,
        rank=rank,
        roleCount=len(ranked),
        eta=role.eta,
        createdAt=created_at.isoformat(),
        gaps=gaps,
        strong=strong,
        evidence=evidence,
        moves=[],
        state=default_state(),
        version=1,
    )


__all__ = [
    "MAX_EVIDENCE",
    "SOURCE_LABELS",
    "assemble",
    "default_state",
    "evidence_lines",
    "find_role",
    "is_met",
    "ranked_roles",
    "source_labels",
]
