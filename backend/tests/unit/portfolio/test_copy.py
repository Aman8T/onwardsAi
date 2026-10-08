"""`copy`: which slots the Sonnet call fills, the prompt it sees, and how its answer is read."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.contract.portfolio import FIXED_SLOTS, SLOT_LIMITS, Fact
from tayyari.decisions.service import DecisionContext, NullDecisionService
from tayyari.llm.errors import ClaudeRefusal
from tayyari.llm.schema_check import check_schema
from tayyari.llm.types import CallContext
from tayyari.portfolio.copy import (
    COPY_MAX_TOKENS,
    COPY_SCHEMA,
    COPY_SYSTEM,
    build_copy_prompt,
    build_copy_schema,
    parse_copy,
    slot_ids_to_fill,
    write_copy,
)
from tayyari.portfolio.fallback import fallback_slots
from tayyari.portfolio.ground import check_slot, verify_slots
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.unit.portfolio._fixtures_a import facts_for, graph_for, load_fixture, person_for

SONNET = "claude-sonnet-5-5"
CALL_CTX = CallContext(purpose="portfolio", profile_id="p1")
DECISION_CTX = DecisionContext(profile_id="p1", analysis_id=None, is_sample=False, request_id="r1")


def riya() -> tuple[list[Fact], Any, Any]:
    fx = load_fixture("riya")
    return list(facts_for("riya")), person_for(fx.profile), graph_for(fx.profile)


def fact_of(facts: list[Fact], kind: str, title: str) -> Fact:
    return next(f for f in facts if f.kind == kind and f.title.startswith(title))


# ───────────────────────── which slots ─────────────────────────


def test_the_five_fixed_slots_come_first_then_one_per_role_and_project() -> None:
    facts, _, _ = riya()
    ids = slot_ids_to_fill(facts)
    assert ids[:5] == list(FIXED_SLOTS)
    roles = [f for f in facts if f.kind == "experience" and f.highlights]
    projects = [f for f in facts if f.kind == "project"]
    assert ids[5:] == [f"exp:{f.id}" for f in roles] + [f"proj:{f.id}" for f in projects]


def test_a_role_without_highlights_gets_no_slot() -> None:
    facts = [
        Fact(id="exp-1", kind="experience", title="Engineer", highlights=[], tags=[], sourceRefs=[]),
        Fact(
            id="exp-2",
            kind="experience",
            title="Engineer",
            highlights=["Built a thing."],
            tags=[],
            sourceRefs=[],
        ),
    ]
    assert slot_ids_to_fill(facts) == [*FIXED_SLOTS, "exp:exp-2"]


def test_roles_and_projects_are_capped() -> None:
    many = [
        Fact(
            id=f"exp-{i}", kind="experience", title="Engineer", highlights=["Built."], tags=[], sourceRefs=[]
        )
        for i in range(1, 10)
    ] + [
        Fact(id=f"proj-{i}", kind="project", title="p", highlights=[], tags=[], sourceRefs=[])
        for i in range(1, 15)
    ]
    ids = slot_ids_to_fill(many)
    assert sum(i.startswith("exp:") for i in ids) == 6
    assert sum(i.startswith("proj:") for i in ids) == 8
    fixture_ids = slot_ids_to_fill(list(facts_for("stress-many-projects")))
    assert sum(i.startswith("proj:") for i in fixture_ids) == 8


def test_no_facts_still_asks_for_the_fixed_slots() -> None:
    assert slot_ids_to_fill([]) == list(FIXED_SLOTS)


# ───────────────────────── schema ─────────────────────────


def test_the_schema_enumerates_the_slot_and_fact_ids() -> None:
    schema = build_copy_schema(["tagline", "exp:exp-1"], ["exp-1", "quote-1"])
    item = schema["properties"]["slots"]["items"]
    assert item["properties"]["id"]["enum"] == ["tagline", "exp:exp-1"]
    assert item["properties"]["factIds"]["items"]["enum"] == ["exp-1", "quote-1"]
    assert item["required"] == ["id", "text", "factIds"]
    assert (
        "enum"
        not in build_copy_schema(["tagline"])["properties"]["slots"]["items"]["properties"]["factIds"][
            "items"
        ]
    )


def test_the_schema_carries_limits_in_descriptions_only() -> None:
    def walk(node: Any):  # type: ignore[no-untyped-def]
        if isinstance(node, dict):
            yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    for node in walk(COPY_SCHEMA):
        assert (
            not {"maxLength", "minLength", "maxItems", "minItems", "maximum", "minimum", "pattern"}
            & node.keys()
        )
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
    text = COPY_SCHEMA["properties"]["slots"]["items"]["properties"]["text"]["description"]
    for name, limit in SLOT_LIMITS.items():
        assert f"{name} {limit}" in text


def test_a_good_answer_validates_and_an_unknown_slot_does_not() -> None:
    schema = build_copy_schema(["tagline"], ["quote-1"])
    assert check_schema({"slots": [{"id": "tagline", "text": "Hi.", "factIds": ["quote-1"]}]}, schema) == []
    assert check_schema({"slots": [{"id": "hero", "text": "Hi.", "factIds": []}]}, schema)
    assert check_schema({"slots": [{"id": "tagline", "text": "Hi.", "factIds": ["nope"]}]}, schema)


# ───────────────────────── prompt ─────────────────────────


def test_the_system_prompt_carries_the_voice_and_the_hard_rules() -> None:
    for needle in (
        "Warm, specific and calm",
        "No hype and no puffery",
        "Never use she, he, her or his",
        "Only the tagline may speak in the person's own voice",
        "Use only the facts given",
        "digits exactly as the fact has them",
        "Claim no seniority the facts do not state",
        "Never mention grades",
        "Stay inside each slot's character limit",
        "Respond ONLY",
    ):
        assert needle in COPY_SYSTEM
    for word in (
        "passionate",
        "world-class",
        "rockstar",
        "ninja",
        "guru",
        "visionary",
        "cutting-edge",
        "synergy",
        "results-driven",
        "dynamic",
        "seasoned",
        "expert in",
    ):
        assert f'"{word}"' in COPY_SYSTEM
    for slot_id in (
        "tagline",
        "aboutLede",
        "aboutBody",
        "pressHeadline",
        "pressDeck",
        "exp:<id>",
        "proj:<id>",
    ):
        assert f"- {slot_id}:" in COPY_SYSTEM


def test_the_prompt_shows_the_person_the_facts_and_the_slots() -> None:
    facts, person, graph = riya()
    ids = slot_ids_to_fill(facts)
    prompt = build_copy_prompt(facts, person, graph, ids)
    assert "- Name: Riya Kapoor" in prompt
    assert "- Years of experience: 2.4" in prompt
    assert "exp-1 | experience | Frontend Engineer II at Kitebird, Bengaluru | Jul 2025 - now" in prompt
    assert "checkout conversion rose 9.4%" in prompt
    assert "proj-1 | project | motion-kit" in prompt
    assert "quote-1 | quote (the person's own words) |" in prompt
    assert "- tagline (at most 90 characters;" in prompt
    assert "- exp:exp-1 (at most 300 characters;" in prompt
    assert "- proj:proj-1 (at most 200 characters;" in prompt
    assert prompt.rstrip().endswith("Write the slots now.")


def test_private_details_never_reach_the_model() -> None:
    facts, person, graph = riya()
    assert any(f.kind == "contact" for f in facts) and any(f.grade for f in facts)
    prompt = build_copy_prompt(facts, person, graph, slot_ids_to_fill(facts))
    for secret in ("riya.kapoor.dev@example.com", "mailto", "CGPA", "https://", "github.com/riyakapoor"):
        assert secret not in prompt
    assert "link-1" not in prompt and "contact-1" not in prompt


def test_the_strongest_areas_are_named_for_emphasis_only() -> None:
    facts, person, graph = riya()
    prompt = build_copy_prompt(facts, person, graph, list(FIXED_SLOTS))
    top = ", ".join(d.name for d in sorted(graph.domains, key=lambda d: -d.score)[:3])
    line = next(ln for ln in prompt.splitlines() if ln.startswith("Their strongest areas"))
    assert line == f"Their strongest areas, for emphasis only (do not name areas or quote scores): {top}."
    assert not any(str(d.score) in line for d in graph.domains)


def test_only_the_requested_slots_are_listed_and_notes_are_appended() -> None:
    facts, person, graph = riya()
    prompt = build_copy_prompt(
        facts,
        person,
        graph,
        ["aboutLede"],
        notes={"aboutLede": '"Google" is not in the facts this slot cites'},
    )
    slots = prompt.split("Slots to write")[1]
    assert "aboutLede" in slots and "tagline" not in slots.split("These slots were written")[0]
    assert "rejected by the checker" in prompt
    assert '- aboutLede: "Google" is not in the facts this slot cites' in prompt
    assert "rejected" not in build_copy_prompt(facts, person, graph, ["aboutLede"])


def test_a_prompt_without_facts_says_so() -> None:
    _, person, graph = riya()
    assert "(no facts were found)" in build_copy_prompt([], person, graph, list(FIXED_SLOTS))


# ───────────────────────── the call ─────────────────────────


def answer(*rows: tuple[str, str, list[str]]) -> dict[str, Any]:
    return {"slots": [{"id": i, "text": t, "factIds": f} for i, t, f in rows]}


async def test_write_copy_makes_one_sonnet_call_and_returns_model_slots() -> None:
    facts, person, graph = riya()
    exp1 = fact_of(facts, "experience", "Frontend Engineer II")
    quote = fact_of(facts, "quote", "I build")
    ids = ["tagline", "aboutLede", f"exp:{exp1.id}"]
    claude = FakeClaudeClient().script_structured(
        "portfolio",
        answer(
            (f"exp:{exp1.id}", "Rebuilt checkout.", [exp1.id]),
            ("tagline", quote.title, [quote.id]),
            ("aboutLede", "Frontend engineer at Kitebird.", [exp1.id]),
        ),
    )
    slots = await write_copy(
        claude, model=SONNET, facts=facts, person=person, graph=graph, slot_ids=ids, call_ctx=CALL_CTX
    )
    (call,) = claude.structured_calls
    assert isinstance(call, StructuredCall)
    assert (call.model, call.effort, call.max_tokens, call.ctx) == (SONNET, "low", COPY_MAX_TOKENS, CALL_CTX)
    assert call.pdf is None
    assert call.system_text == COPY_SYSTEM
    assert call.schema["properties"]["slots"]["items"]["properties"]["id"]["enum"] == ids
    cited_enum = call.schema["properties"]["slots"]["items"]["properties"]["factIds"]["items"]["enum"]
    assert exp1.id in cited_enum and quote.id in cited_enum
    assert not any(i.startswith(("link-", "contact-")) for i in cited_enum)
    # in the order asked, whatever order the model answered in
    assert [(s.id, s.origin) for s in slots] == [
        ("tagline", "model"),
        ("aboutLede", "model"),
        (f"exp:{exp1.id}", "model"),
    ]
    assert slots[0].factIds == [quote.id]


async def test_write_copy_with_nothing_to_write_makes_no_call() -> None:
    facts, person, graph = riya()
    claude = FakeClaudeClient()
    assert (
        await write_copy(
            claude, model=SONNET, facts=facts, person=person, graph=graph, slot_ids=[], call_ctx=CALL_CTX
        )
        == []
    )
    assert claude.structured_calls == []


async def test_write_copy_lets_model_errors_propagate() -> None:
    facts, person, graph = riya()
    claude = FakeClaudeClient().script_structured("portfolio", ClaudeRefusal())
    with pytest.raises(ClaudeRefusal):
        await write_copy(
            claude,
            model=SONNET,
            facts=facts,
            person=person,
            graph=graph,
            slot_ids=["tagline"],
            call_ctx=CALL_CTX,
        )


def test_parse_skips_unknown_repeated_and_malformed_entries() -> None:
    data: dict[str, Any] = {
        "slots": [
            {"id": "tagline", "text": "First.", "factIds": ["quote-1", 7, "quote-1"]},
            {"id": "tagline", "text": "Second.", "factIds": []},
            {"id": "hero", "text": "Not asked.", "factIds": []},
            {"id": "aboutLede", "text": None, "factIds": []},
            {"id": "aboutBody", "text": "Body.", "factIds": "exp-1"},
            "junk",
            {"text": "no id"},
        ]
    }
    slots = parse_copy(data, ["aboutBody", "tagline", "aboutLede"])
    assert [(s.id, s.text, s.factIds) for s in slots] == [
        ("aboutBody", "Body.", []),
        ("tagline", "First.", ["quote-1"]),
    ]
    assert parse_copy({"slots": "no"}, ["tagline"]) == []
    assert parse_copy({}, ["tagline"]) == []


def test_parse_cleans_the_text() -> None:
    data = answer(
        ("tagline", '"I build things."', ["quote-1"]),
        ("aboutLede", "  Frontend   engineer\nat Kitebird.  ", ["exp-1"]),
        ("exp:exp-1", "- Rebuilt checkout.\n\n• Owns Kite UI.\r\n2. Mentored 2 interns.", ["exp-1"]),
        ("pressHeadline", "“Kitebird Engineer Rebuilds Checkout”", ["exp-1"]),
    )
    slots = {s.id: s.text for s in parse_copy(data, ["tagline", "aboutLede", "exp:exp-1", "pressHeadline"])}
    assert slots["tagline"] == "I build things."
    assert slots["aboutLede"] == "Frontend engineer at Kitebird."
    assert slots["exp:exp-1"] == "Rebuilt checkout.\nOwns Kite UI.\nMentored 2 interns."
    assert slots["pressHeadline"] == "Kitebird Engineer Rebuilds Checkout"


# ───────────────────────── the whole flow, as the service will run it ─────────────────────────


async def test_generate_verify_reask_and_fall_back() -> None:
    """Sonnet answers; the grounder rejects one slot; a re-ask fixes it; a slot the model skipped comes from facts."""
    facts, person, graph = riya()
    exp1 = fact_of(facts, "experience", "Frontend Engineer II")
    quote = fact_of(facts, "quote", "I build")
    ids = slot_ids_to_fill(facts)

    def reask(call: StructuredCall) -> dict[str, Any]:
        assert "rejected by the checker" in call.user_text
        return answer(("aboutLede", "Frontend engineer at Kitebird who rebuilt checkout.", [exp1.id]))

    claude = FakeClaudeClient().script_structured(
        "portfolio",
        answer(
            ("tagline", quote.title, [quote.id]),
            ("aboutLede", "Frontend engineer at Google who rebuilt checkout.", [exp1.id]),
            ("pressHeadline", "Kitebird Engineer Rebuilds Checkout; Conversion Up 9.4%", [exp1.id]),
            ("pressDeck", "Riya Kapoor took payment-step LCP from 4.1s to 1.9s.", [exp1.id]),
            (
                f"exp:{exp1.id}",
                "Rebuilt checkout as a single-page flow.\nOwns Kite UI, used by 3 product squads.",
                [exp1.id],
            ),
        ),
        reask,
    )
    first = await write_copy(
        claude, model=SONNET, facts=facts, person=person, graph=graph, slot_ids=ids, call_ctx=CALL_CTX
    )
    kept, problems = await verify_slots(first, facts, NullDecisionService(), DECISION_CTX, person=person)
    assert set(problems) == {"aboutLede"}
    assert any("Google" in p for p in problems["aboutLede"])
    assert {s.id for s in kept} == {"tagline", "pressHeadline", "pressDeck", f"exp:{exp1.id}"}

    notes = {slot_id: "; ".join(p) for slot_id, p in problems.items()}
    second = await write_copy(
        claude,
        model=SONNET,
        facts=facts,
        person=person,
        graph=graph,
        slot_ids=list(problems),
        call_ctx=CALL_CTX,
        notes=notes,
    )
    kept2, problems2 = await verify_slots(second, facts, NullDecisionService(), DECISION_CTX, person=person)
    assert problems2 == {}
    retry_call = claude.structured_calls[1]
    assert retry_call.schema["properties"]["slots"]["items"]["properties"]["id"]["enum"] == ["aboutLede"]
    assert '"Google" is not in the facts this slot cites' in retry_call.user_text

    final = {s.id: s for s in [*kept, *kept2]}
    missing = [i for i in ids if i not in final]
    assert missing  # the model skipped the other roles and projects
    for slot in fallback_slots(facts, person, graph, missing):
        final[slot.id] = slot
    assert set(final) == set(ids)
    by_id = {f.id: f for f in facts}
    for slot_id in ids:
        slot = final[slot_id]
        if slot.text:
            assert check_slot(slot, by_id, person=person).ok, (slot_id, slot.text)
    assert {final[i].origin for i in ids} == {"model", "resume"}
