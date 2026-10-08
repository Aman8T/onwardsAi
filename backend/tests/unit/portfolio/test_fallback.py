"""`fallback`: deterministic slot wording taken from the facts, which always passes the grounding check."""

from __future__ import annotations

import pytest

from tayyari.contract.portfolio import SLOT_LIMITS, Fact, SourceRef, slot_limit
from tayyari.portfolio.copy import slot_ids_to_fill
from tayyari.portfolio.fallback import fallback_slots, plain, trim
from tayyari.portfolio.ground import check_slot
from tests.unit.portfolio._fixtures_a import (
    FIXTURE_NAMES,
    PERSONAS,
    facts_for,
    graph_for,
    load_fixture,
    person_for,
)


def build(name: str, slot_ids: list[str] | None = None):  # type: ignore[no-untyped-def]
    fx = load_fixture(name)
    facts = list(facts_for(name))
    person, graph = person_for(fx.profile), graph_for(fx.profile)
    ids = slot_ids if slot_ids is not None else slot_ids_to_fill(facts)
    return facts, person, fallback_slots(facts, person, graph, ids)


def fact(fact_id: str, kind: str, title: str, **kw: object) -> Fact:
    quote = str(kw.pop("quote", title))
    return Fact.model_validate(
        {
            "id": fact_id,
            "kind": kind,
            "title": title,
            "highlights": kw.pop("highlights", []),
            "tags": [],
            "sourceRefs": [SourceRef(sourceId="resume", quote=quote).model_dump()],
            **kw,
        }
    )


# ───────────────────────── every fixture ─────────────────────────


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fallback_slot_of_every_fixture_passes_the_grounding_check(name: str) -> None:
    facts, person, slots = build(name)
    by_id = {f.id: f for f in facts}
    assert [s.id for s in slots] == slot_ids_to_fill(facts)
    for slot in slots:
        result = check_slot(slot, by_id, person=person)
        assert result.ok, (slot.id, slot.text, result.problems)
        assert slot.origin == "resume"
        limit = slot_limit(slot.id)
        assert limit is not None and len(slot.text) <= limit
        # a slot that rests on facts passes on the facts alone, with no help from the person
        if slot.factIds:
            assert check_slot(slot, by_id).ok, (slot.id, slot.text)
        else:
            assert slot.text == "" or slot.id in ("tagline", "aboutLede", "pressHeadline", "pressDeck")


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_the_fixed_slots_are_never_blank_and_nothing_is_blank_but_a_missing_body(name: str) -> None:
    _, _, slots = build(name)
    blank = [s.id for s in slots if not s.text.strip()]
    assert set(blank) <= {"aboutBody"}


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_cited_facts_exist_and_slots_cite_their_own_fact(name: str) -> None:
    facts, _, slots = build(name)
    ids = {f.id for f in facts}
    for slot in slots:
        assert set(slot.factIds) <= ids
        _, _, fact_id = slot.id.partition(":")
        if fact_id and slot.text:
            assert slot.factIds == [fact_id]


@pytest.mark.parametrize("name", PERSONAS)
def test_the_persona_pages_get_their_own_facts_in_the_slots(name: str) -> None:
    facts, person, slots = build(name)
    by_id = {s.id: s for s in slots}
    quotes = [f for f in facts if f.kind == "quote"]
    assert quotes
    tagline = by_id["tagline"]
    assert tagline.factIds == [quotes[0].id]
    assert quotes[0].title.startswith(tagline.text) and len(tagline.text) <= SLOT_LIMITS["tagline"]
    if len(quotes[0].title) <= SLOT_LIMITS["tagline"]:
        assert tagline.text == quotes[0].title
    assert by_id["pressHeadline"].text == f"{person.name}, {person.role}"
    assert by_id["pressDeck"].text == by_id["aboutLede"].text


def test_riya_gets_the_lede_from_her_first_role_and_lines_from_each_role() -> None:
    facts, _, slots = build("riya")
    by_id = {s.id: s for s in slots}
    exp1 = next(f for f in facts if f.title == "Frontend Engineer II")
    lede = by_id["aboutLede"]
    assert lede.factIds == [exp1.id]
    assert lede.text.startswith("Rebuilt the three-step checkout as a single-page flow")
    assert len(lede.text) <= SLOT_LIMITS["aboutLede"]
    assert not lede.text.endswith((",", ";", ":"))
    lines = by_id[f"exp:{exp1.id}"].text.split("\n")
    assert 1 <= len(lines) <= 3
    assert lines[0].startswith("Rebuilt the three-step checkout")
    assert by_id[f"exp:{exp1.id}"].factIds == [exp1.id]


def test_a_project_slot_is_its_description_and_the_facts_numbers_survive() -> None:
    facts, _, slots = build("riya")
    motion = next(f for f in facts if f.title == "motion-kit")
    text = next(s for s in slots if s.id == f"proj:{motion.id}").text
    assert text.startswith("Spring-physics animation primitives for React")


def test_the_scanned_fixture_is_built_from_github_alone() -> None:
    facts, person, slots = build("stress-scanned")
    assert {r.sourceId for f in facts for r in f.sourceRefs} == {"github"}
    by_id = {s.id: s for s in slots}
    assert by_id["tagline"].text == person.headline  # no quote fact: the headline
    assert by_id["tagline"].factIds == []
    assert by_id["aboutLede"].text == "Python Developer based in Jaipur, India."
    assert by_id["pressHeadline"].text == "Ishaan Verma, Python Developer"
    assert all(by_id[f"proj:{f.id}"].factIds == [f.id] for f in facts if f.kind == "project")


def test_a_resume_with_nothing_in_it_still_fills_every_fixed_slot_from_the_person() -> None:
    facts, person, slots = build("stress-missing")
    assert facts == []
    by_id = {s.id: s for s in slots}
    assert by_id["tagline"].text == "Aspiring Developer"
    assert by_id["aboutLede"].text == "Aspiring Developer."
    assert by_id["aboutBody"].text == ""
    assert by_id["pressHeadline"].text == "Rohan Desai, Aspiring Developer"
    assert all(not s.factIds for s in slots)
    assert all(check_slot(s, {}, person=person).ok for s in slots)


def test_a_non_latin_name_makes_a_headline_all_the_same() -> None:
    _, person, slots = build("stress-unicode")
    assert next(s for s in slots if s.id == "pressHeadline").text == f"{person.name}, {person.role}"


def test_long_titles_and_fourteen_projects_stay_within_the_limits() -> None:
    for name in ("stress-long-titles", "stress-many-projects"):
        _, _, slots = build(name)
        for slot in slots:
            assert len(slot.text) <= (slot_limit(slot.id) or 0)
    assert sum(s.id.startswith("proj:") for s in build("stress-many-projects")[2]) == 8


# ───────────────────────── choices ─────────────────────────


def test_the_tagline_is_trimmed_at_a_word_boundary() -> None:
    long_quote = "I build interfaces that feel physical, with springs and gestures and motion that respects people who prefer less."
    facts = [fact("quote-1", "quote", long_quote)]
    person = person_for(load_fixture("riya").profile)
    (slot,) = fallback_slots(facts, person, graph_for(load_fixture("riya").profile), ["tagline"])
    assert len(slot.text) <= 90
    assert long_quote.startswith(slot.text)
    assert long_quote[len(slot.text)] in " ,"
    assert slot.factIds == ["quote-1"]


def test_the_first_quote_wins_and_the_headline_is_the_last_resort() -> None:
    person = person_for(load_fixture("riya").profile)
    graph = graph_for(load_fixture("riya").profile)
    two = [fact("quote-1", "quote", "I like small things."), fact("quote-2", "quote", "I like big things.")]
    assert fallback_slots(two, person, graph, ["tagline"])[0].text == "I like small things."
    assert fallback_slots([], person, graph, ["tagline"])[0].text == person.headline


def test_the_lede_skips_roles_without_highlights_and_the_body_continues_it() -> None:
    person = person_for(load_fixture("riya").profile)
    graph = graph_for(load_fixture("riya").profile)
    facts = [
        fact("exp-1", "experience", "Advisor", highlights=[]),
        fact(
            "exp-2", "experience", "Engineer", highlights=["Built the billing service.", "Cut costs by 20%."]
        ),
        fact("exp-3", "experience", "Intern", highlights=["Wrote 14 tests"]),
    ]
    slots = {s.id: s for s in fallback_slots(facts, person, graph, ["aboutLede", "aboutBody"])}
    assert slots["aboutLede"].text == "Built the billing service."
    assert slots["aboutLede"].factIds == ["exp-2"]
    assert (
        slots["aboutBody"].text == "Cut costs by 20%. Wrote 14 tests."
    )  # a full stop is added where the line has none
    assert slots["aboutBody"].factIds == ["exp-2", "exp-3"]


def test_a_second_sentence_is_needed_for_a_body() -> None:
    person = person_for(load_fixture("riya").profile)
    graph = graph_for(load_fixture("riya").profile)
    one = [fact("exp-1", "experience", "Engineer", highlights=["Built the billing service."])]
    assert fallback_slots(one, person, graph, ["aboutBody"])[0].text == ""


def test_experience_lines_are_the_first_three_highlights_within_the_limit() -> None:
    person = person_for(load_fixture("riya").profile)
    graph = graph_for(load_fixture("riya").profile)
    highlights = [f"Shipped release number {n} with 4 reviewers." for n in range(1, 8)]
    facts = [fact("exp-1", "experience", "Engineer", highlights=highlights)]
    (slot,) = fallback_slots(facts, person, graph, ["exp:exp-1"])
    assert slot.text.split("\n") == highlights[:3]
    long_line = (
        "Rebuilt the ordering platform "
        + "with careful rollouts and 3 staged migrations " * 8
        + "for 4 teams."
    )
    (slot,) = fallback_slots(
        [fact("exp-1", "experience", "Engineer", highlights=[long_line])], person, graph, ["exp:exp-1"]
    )
    assert 0 < len(slot.text) <= 300
    assert "\n" not in slot.text


def test_unknown_slots_and_missing_facts_are_left_out() -> None:
    person = person_for(load_fixture("riya").profile)
    graph = graph_for(load_fixture("riya").profile)
    facts = [fact("exp-1", "experience", "Engineer", highlights=["Built it."])]
    slots = fallback_slots(
        facts, person, graph, ["hero", "exp:exp-9", "proj:proj-1", "exp:exp-1", "exp:exp-1"]
    )
    assert [s.id for s in slots] == ["exp:exp-1"]


# ───────────────────────── plain text ─────────────────────────


@pytest.mark.parametrize(
    ("dirty", "clean"),
    [
        (
            "`useSpring` / `useSprings` with velocity-aware retargeting",
            "useSpring / useSprings with velocity-aware retargeting",
        ),
        ("**Shipped** the [docs](https://x.example/docs) site", "Shipped the docs site"),
        ("<b>Built</b> a <i>thing</i>", "Built a thing"),
        ("- Wrote 14 tests", "Wrote 14 tests"),
        ("> quoted __line__", "quoted line"),
        ("  spaced \n out  ", "spaced out"),
    ],
)
def test_plain_strips_markup(dirty: str, clean: str) -> None:
    assert plain(dirty) == clean


def test_markup_in_a_fact_never_reaches_the_slot() -> None:
    person = person_for(load_fixture("riya").profile)
    graph = graph_for(load_fixture("riya").profile)
    facts = [
        fact(
            "proj-1",
            "project",
            "kit",
            detail="**Springs** for React via `useSpring` and [docs](https://x.example).",
        ),
        fact(
            "exp-1",
            "experience",
            "Engineer",
            highlights=["<b>Built</b> a thing for 3 teams.", "`Ran` 2 audits."],
        ),
    ]
    slots = {s.id: s for s in fallback_slots(facts, person, graph, ["proj:proj-1", "exp:exp-1"])}
    assert slots["proj:proj-1"].text == "Springs for React via useSpring and docs."
    assert slots["exp:exp-1"].text == "Built a thing for 3 teams.\nRan 2 audits."
    by_id = {f.id: f for f in facts}
    assert all(check_slot(s, by_id, person=person).ok for s in slots.values())


# ───────────────────────── trim ─────────────────────────


@pytest.mark.parametrize("limit", [20, 40, 60, 90, 160])
def test_trim_never_cuts_through_a_word_or_a_number(limit: int) -> None:
    text = "Rebuilt the three-step checkout; conversion rose 9.4% in the A/B test and LCP dropped from 4.1s to 1.9s (payment step)."
    out = trim(text, limit)
    assert len(out) <= limit
    assert text.startswith(out)
    assert text[len(out) : len(out) + 1] in ("", " ", ";", ",", ".", ":")
    assert out == out.strip() and not out.endswith((",", ";", ":", "-"))
    assert out.count("(") == out.count(")")


def test_trim_prefers_a_sentence_end_and_leaves_short_text_alone() -> None:
    assert trim("Short text.", 50) == "Short text."
    text = "Built the billing service. Then did a great many other things over time."
    assert trim(text, 45) == "Built the billing service."


def test_trim_survives_one_enormous_word() -> None:
    assert trim("x" * 500, 40) == "x" * 40
