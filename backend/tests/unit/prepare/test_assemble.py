"""The plan's numbers and evidence come from the graph, in code: the gaps and their order, exactly one biggest, what is
already strong, the rank among the roles, the scope over one requirement, and the numbered evidence list with its source
chips. What the graph does not have is refused before any model is asked."""

from __future__ import annotations

import pytest

from tayyari.contract.models import Evidence, Profile, Skill, SourceSummary
from tayyari.contract.prepare import MAX_MOVES, PreparePlan
from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.prepare.assemble import (
    MAX_EVIDENCE,
    assemble,
    evidence_lines,
    is_met,
    ranked_roles,
)
from tayyari.prepare.errors import (
    ALREADY_STRONG_REQUIREMENT,
    ALREADY_STRONG_ROLE,
    AlreadyStrong,
    RequirementNotFound,
    RoleNotFound,
)
from tests.unit.prepare.support import NOW, PLAN_ID, ROLE, plan_for, riya


def test_the_gaps_are_the_unmet_requirements_biggest_first_and_exactly_one_is_marked() -> None:
    plan = plan_for()
    assert [g.name for g in plan.gaps] == [
        "LLM evals",
        "AI UX patterns",
        "Prompt design",
        "RAG & retrieval",
        "LLM API integration",
    ]
    assert [g.target - g.current for g in plan.gaps] == [53, 38, 32, 25, 23]
    assert [g.biggest for g in plan.gaps] == [True, False, False, False, False]
    assert (
        plan.gaps[0].skillId == "llm-evals" and plan.gaps[0].why is None and plan.gaps[0].evidenceIds is None
    )


def test_a_requirement_marked_have_but_under_its_target_is_still_a_gap() -> None:
    plan = plan_for("design-engineer")
    names = {g.name: (g.current, g.target) for g in plan.gaps}
    assert names == {"AI UX patterns": (32, 55), "Figma-to-code": (74, 75)}
    assert [s.name for s in plan.strong] == ["Motion & animation", "Design systems", "React"]


def test_strong_is_what_the_graph_meets_with_its_numbers() -> None:
    plan = plan_for()
    assert [(s.name, s.current, s.target) for s in plan.strong] == [("React", 90, 85), ("TypeScript", 86, 80)]


def test_rank_and_count_come_from_the_roles_by_alignment_never_a_percentage() -> None:
    profile, _ = riya()
    assert [r.id for r in ranked_roles(profile)][:2] == ["design-engineer", "design-systems-engineer"]
    assert (plan_for("design-engineer").rank, plan_for("design-engineer").roleCount) == (1, 5)
    assert (plan_for().rank, plan_for().roleCount) == (5, 5)
    dumped = plan_for().to_dict()
    assert "alignment" not in dumped and "%" not in str(dumped["rank"])


def test_the_plan_carries_the_roles_title_eta_and_the_default_state() -> None:
    plan = plan_for()
    assert (plan.id, plan.profileId, plan.roleId, plan.roleTitle) == (
        PLAN_ID,
        riya()[0].id,
        ROLE,
        "AI Product Engineer",
    )
    assert plan.eta == "~9 months" and plan.createdAt == NOW.isoformat()
    assert plan.scope == "role" and plan.requirement is None and plan.headline is None
    assert plan.moves == [] and plan.version == 1
    assert plan.state.to_dict() == {"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []}
    assert plan.state.moveIndex < MAX_MOVES


# ───────────────────────── scope: one requirement ─────────────────────────


def test_a_requirement_scope_keeps_only_that_gap_matched_without_case() -> None:
    plan = plan_for(requirement="  rag & RETRIEVAL ")
    assert plan.scope == "requirement" and plan.requirement == "RAG & retrieval"
    assert [(g.name, g.biggest) for g in plan.gaps] == [("RAG & retrieval", True)]
    assert [s.name for s in plan.strong] == ["React", "TypeScript"]


def test_a_requirement_already_met_is_refused_as_already_strong() -> None:
    with pytest.raises(AlreadyStrong) as info:
        plan_for(requirement="React")
    assert info.value.code == "already_strong" and info.value.status == 400
    assert info.value.user_message == ALREADY_STRONG_REQUIREMENT


def test_a_requirement_the_role_does_not_have_is_a_404() -> None:
    with pytest.raises(RequirementNotFound) as info:
        plan_for(requirement="Rust")
    assert info.value.status == 404


def test_a_role_the_graph_does_not_have_is_a_404() -> None:
    with pytest.raises(RoleNotFound) as info:
        plan_for("staff-engineer")
    assert info.value.status == 404 and info.value.code == "role_not_found"


def test_a_role_with_no_gap_at_all_is_already_strong() -> None:
    profile, context = riya()
    role = next(r for r in profile.roles if r.id == "design-engineer")
    met = role.model_copy(
        update={
            "requirements": [r.model_copy(update={"have": True, "current": 99}) for r in role.requirements]
        }
    )
    strong_profile = profile.model_copy(
        update={"roles": [met, *[r for r in profile.roles if r.id != role.id]]}
    )
    with pytest.raises(AlreadyStrong) as info:
        assemble(strong_profile, context, role_id=role.id, requirement=None, plan_id=PLAN_ID, created_at=NOW)
    assert info.value.user_message == ALREADY_STRONG_ROLE


def test_is_met_needs_both_the_flag_and_the_level() -> None:
    profile, _ = riya()
    reqs = {r.name: r for role in profile.roles for r in role.requirements}
    assert is_met(reqs["React"]) is True
    assert is_met(reqs["Automated testing"]) is False  # have False
    assert is_met(reqs["Figma-to-code"]) is False  # have True, 74 under 75 (the design engineer's)


# ───────────────────────── evidence ─────────────────────────


def test_the_evidence_is_numbered_the_gap_skills_first_then_the_rest_by_score() -> None:
    plan = plan_for()
    assert [e.id for e in plan.evidence] == [f"e{i}" for i in range(1, len(plan.evidence) + 1)]
    first_skills = [e.skill for e in plan.evidence[:7]]
    assert first_skills == [
        "LLM evals",
        "LLM evals",
        "AI UX patterns",
        "Prompt design",
        "RAG & retrieval",
        "LLM API integration",
        "LLM API integration",
    ]
    assert [e.skill for e in plan.evidence[7:11]] == [
        "React",
        "React",
        "TypeScript",
        "TypeScript",
    ]  # then strong
    rest = [e.skill for e in plan.evidence[11:]]
    assert rest[0] == "Motion & animation"  # the highest-scoring skill the role does not name
    assert len(plan.evidence) == 37 <= MAX_EVIDENCE


def test_each_line_names_its_source_kind_and_the_chip_label() -> None:
    plan = plan_for()
    by_kind = {(e.sourceKind, e.sourceLabel) for e in plan.evidence}
    assert by_kind == {
        ("github", "your GitHub"),
        ("lovable", "your Lovable app"),
        ("resume", "your resume"),
        ("portfolio", "your portfolio"),
    }
    assert {e.sourceId for e in plan.evidence} == {"resume", "github", "lovable-1", "portfolio-1"}


def test_an_unknown_source_kind_shows_the_sources_own_label_and_an_unknown_id_a_page() -> None:
    profile, _ = riya()
    context = StoredContext(
        sources=[StoredSource(id="link-9", label="Growth notes", kind="link", url="https://x.example")]
    )
    skills = [
        Skill(
            id="s1",
            name="Growth",
            domainId="d",
            score=50,
            evidence=[
                Evidence(sourceId="link-9", text="Wrote notes"),
                Evidence(sourceId="ghost", text="Lost"),
            ],
            unlocks=[],
        )
    ]
    odd = profile.model_copy(update={"skills": skills, "sources": []})
    lines = evidence_lines(odd, context, first=[])
    assert [(e.sourceKind, e.sourceLabel, e.text) for e in lines] == [
        ("link", "Growth notes", "Wrote notes"),
        ("link", "a page", "Lost"),
    ]


def test_the_profiles_own_source_summaries_name_a_source_the_context_lacks() -> None:
    profile, _ = riya()
    only_profile_sources = profile.model_copy(
        update={
            "sources": [
                SourceSummary.model_validate({**s.to_dict(), "kind": s.kind}) for s in profile.sources
            ]
        }
    )
    lines = evidence_lines(only_profile_sources, StoredContext(), first=[])
    assert {(e.sourceId, e.sourceLabel) for e in lines if e.sourceId == "github"} == {
        ("github", "your GitHub")
    }


def test_duplicate_lines_are_kept_once_and_the_list_is_capped() -> None:
    profile, context = riya()
    doubled = profile.model_copy(
        update={
            "skills": [s.model_copy(update={"evidence": [*s.evidence, *s.evidence]}) for s in profile.skills]
        }
    )
    lines = evidence_lines(doubled, context, first=[])
    assert len(lines) == 37
    assert len(evidence_lines(doubled, context, first=[], limit=5)) == 5


def test_a_role_requirement_without_a_skill_id_is_linked_by_name() -> None:
    profile, context = riya()
    role = next(r for r in profile.roles if r.id == "senior-frontend-engineer")
    plan = assemble(profile, context, role_id=role.id, requirement=None, plan_id=PLAN_ID, created_at=NOW)
    assert "System design" in [g.name for g in plan.gaps]  # a gap with no skillId
    unnamed = profile.model_copy(
        update={
            "roles": [
                r.model_copy(
                    update={"requirements": [q.model_copy(update={"skillId": None}) for q in r.requirements]}
                )
                for r in profile.roles
            ]
        }
    )
    by_name = assemble(unnamed, context, role_id=ROLE, requirement=None, plan_id=PLAN_ID, created_at=NOW)
    assert [e.skill for e in by_name.evidence[:2]] == ["LLM evals", "LLM evals"]


def test_the_plan_is_valid_contract_json_round_trip() -> None:
    plan = plan_for()
    assert PreparePlan.from_dict(plan.to_dict()) == plan


def test_the_profile_model_is_the_graphs() -> None:
    profile = riya()[0]
    assert isinstance(profile, Profile) and profile.roles
