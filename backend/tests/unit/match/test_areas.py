"""The areas call: each requirement placed in one of the person's own areas, re-checked by code."""

from __future__ import annotations

from typing import Any

from tayyari.contract.match import Requirement
from tayyari.contract.models import Profile
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.match.areas import area_lines, place_requirements, validate_areas
from tayyari.match.schemas import AreaRowOut
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.unit.match.support import CTX, HAIKU, meera


def req(rid: str, hint: str = "") -> Requirement:
    return Requirement(
        id=rid,
        text=f"Requirement {rid}",
        quote=f"Requirement {rid}",
        tier="required",
        kind="skill",
        provenance="stated",
        domainHint=hint or None,
        terms=[],
    )


def profile() -> Profile:
    base = meera().profile.to_dict()
    base["domains"] = [
        {"id": "agents", "name": "Agent & RAG Systems", "short": "Agents & RAG", "score": 76, "summary": ""},
        {"id": "backend", "name": "Backend & Cloud", "short": "Backend & Cloud", "score": 58, "summary": ""},
    ]
    base["skills"] = [
        {
            "id": "s1",
            "name": "Vector databases",
            "domainId": "agents",
            "score": 50,
            "evidence": [],
            "unlocks": [],
        },
        {"id": "s2", "name": "RAG", "domainId": "agents", "score": 80, "evidence": [], "unlocks": []},
        {
            "id": "s3",
            "name": "Cloud & containers",
            "domainId": "backend",
            "score": 60,
            "evidence": [],
            "unlocks": [],
        },
    ]
    return Profile.from_dict(base)


def row(rid: str, area: str, skill: str = "") -> AreaRowOut:
    return AreaRowOut(requirementId=rid, skill=skill, area=area)


def test_the_cited_skill_decides_the_area_then_a_known_area_then_none() -> None:
    reqs = [req("r1"), req("r2"), req("r3"), req("r4"), req("r5")]
    placed = validate_areas(
        [
            row("r1", "agents", skill="Cloud & containers"),  # the skill's own area wins over the one named
            row("r2", "backend"),
            row("r3", "none"),
            row("r4", "frontend"),  # not an area of this graph: left for code
            row("r5", "None", skill="Kubernetes"),  # a skill the graph lacks cites nothing; none still holds
            row("r9", "agents"),  # not a requirement of this posting
        ],
        reqs,
        profile(),
    )
    assert placed == {"r1": "backend", "r2": "backend", "r3": None, "r5": None}


def test_the_first_row_of_a_requirement_counts() -> None:
    placed = validate_areas([row("r1", "agents"), row("r1", "backend")], [req("r1")], profile())
    assert placed == {"r1": "agents"}


def test_the_prompt_lists_each_area_with_its_strongest_skills_first() -> None:
    lines = area_lines(profile())
    assert [(a.id, list(a.skills)) for a in lines] == [
        ("agents", ["RAG", "Vector databases"]),
        ("backend", ["Cloud & containers"]),
    ]


async def test_the_call_offers_only_the_graphs_areas_and_skills_and_its_answer_is_checked() -> None:
    fake = FakeClaudeClient()
    seen: list[StructuredCall] = []

    def respond(call: StructuredCall) -> dict[str, Any]:
        seen.append(call)
        return {
            "rows": [
                {"requirementId": "r1", "skill": "RAG", "area": "agents"},
                {"requirementId": "r2", "skill": "", "area": "none"},
            ]
        }

    fake.script_structured("match.areas", respond)
    placed = await place_requirements(
        fake,
        model=HAIKU,
        profile=profile(),
        requirements=[req("r1", "Agentic AI"), req("r2", "Education")],
        call_ctx=CTX,
    )
    assert placed == {"r1": "agents", "r2": None}
    call = seen[0]
    assert call.ctx.purpose == "match.areas" and call.model == HAIKU
    props = call.schema["properties"]["rows"]["items"]["properties"]
    assert props["area"]["enum"] == ["agents", "backend", "none"]
    assert props["skill"]["enum"] == ["", "RAG", "Vector databases", "Cloud & containers"]
    assert props["requirementId"]["enum"] == ["r1", "r2"]
    assert "<areas>" in call.user_text and "field: Agentic AI" in call.user_text
    assert "<facts>" not in call.user_text  # it sorts the asks; it never sees the evidence


async def test_a_failed_call_places_nothing_so_the_radar_maps_by_code() -> None:
    fake = FakeClaudeClient()
    fake.script_structured("match.areas", ClaudeOverloaded())
    placed = await place_requirements(
        fake, model=HAIKU, profile=profile(), requirements=[req("r1")], call_ctx=CTX
    )
    assert placed == {}


async def test_nothing_to_place_makes_no_call() -> None:
    fake = FakeClaudeClient()
    assert await place_requirements(fake, model=HAIKU, profile=profile(), requirements=[], call_ctx=CTX) == {}
    assert fake.structured_calls == []
