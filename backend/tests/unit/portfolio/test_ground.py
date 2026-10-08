"""`ground.check_slot` and `verify_slots`: strict, per-slot grounding of generated copy."""

from __future__ import annotations

import re

import pytest

from tayyari.contract.portfolio import SLOT_LIMITS, CopySlot, Fact, PortfolioPerson, SourceRef
from tayyari.decisions.service import (
    CopyClaim,
    CopyVerdict,
    DecisionContext,
    NullDecisionService,
)
from tayyari.portfolio.ground import check_slot, fact_text, slot_corpus, split_sentences, verify_slots
from tests.fakes.decisions import FakeDecisionService
from tests.unit.portfolio._fixtures_a import FIXTURE_NAMES, facts_for, load_fixture, person_for


def fact(fact_id: str, kind: str, title: str, quote: str | None = None, **kw: object) -> Fact:
    return Fact.model_validate(
        {
            "id": fact_id,
            "kind": kind,
            "title": title,
            "highlights": kw.pop("highlights", []),
            "tags": kw.pop("tags", []),
            "sourceRefs": [SourceRef(sourceId="resume", quote=quote or title).model_dump()],
            **kw,
        }
    )


def slot(slot_id: str, text: str, *fact_ids: str, origin: str = "model") -> CopySlot:
    return CopySlot.model_validate({"id": slot_id, "text": text, "origin": origin, "factIds": list(fact_ids)})


KITEBIRD = fact(
    "exp-1",
    "experience",
    "Frontend Engineer II",
    "Kitebird — Frontend Engineer II Bengaluru · Jul 2025 – Present",
    org="Kitebird",
    location="Bengaluru",
    start="2025-07",
    current=True,
    detail="D2C commerce startup (home goods), ~40 engineers, storefront + mobile web.",
    highlights=[
        "Rebuilt the three-step checkout as a single-page flow in React and TypeScript with optimistic cart "
        "updates; checkout conversion rose 9.4% in the A/B test and LCP on the payment step dropped from 4.1s to 1.9s.",
        "Own Kite UI, the shared React component library (42 components, typed tokens, Storybook docs) used by 3 product squads.",
        "Led the storefront migration to React 18 and Vite, removing the legacy CRA build.",
        "Mentored 2 frontend interns through their 6-month internships; both received full-time offers.",
    ],
    tags=["React", "TypeScript"],
)
EARLIER = fact(
    "exp-2",
    "experience",
    "Frontend Engineer",
    "Kitebird — Frontend Engineer Bengaluru · May 2024 – Jun 2025",
    org="Kitebird",
    start="2024-05",
    end="2025-06",
    highlights=[
        "Built the backend-for-frontend (Node.js, Express) for the mobile web app.",
        "Owned API contracts with 2 backend teams (REST and a GraphQL catalog service).",
        "Fixed table rendering for accounts with 10,000+ invoices using virtualised lists.",
    ],
)
VOICE = fact(
    "quote-1",
    "quote",
    "I build interfaces that feel physical: springs, not durations.",
)
SCRIPT = fact(
    "proj-1",
    "project",
    "pixel-forge",
    "pixel-forge — Browser image tool written in JavaScript.",
    detail="Browser image tool written in JavaScript.",
    tags=["JavaScript"],
)
FACTS = {f.id: f for f in (KITEBIRD, EARLIER, VOICE, SCRIPT)}
PERSON = PortfolioPerson(
    name="Riya Kapoor",
    headline="Frontend Engineer · 2.4 yrs · Bengaluru",
    role="Frontend Engineer",
    location="Bengaluru, India",
    yearsExperience=2.4,
    monogram="RK",
)


def problems_of(s: CopySlot, **kw: object) -> list[str]:
    return check_slot(s, FACTS, **kw).problems  # type: ignore[arg-type]


# ───────────────────────── good copy passes ─────────────────────────

CANVAS_COPY = [
    ("tagline", "I build interfaces that feel physical: springs, not durations.", ["quote-1"]),
    (
        "aboutLede",
        "Frontend engineer at Kitebird who owns a 42-component design system and rebuilt checkout for a measured "
        "9.4% conversion lift.",
        ["exp-1"],
    ),
    (
        "aboutBody",
        "Before that, Riya built the Node.js backend-for-frontend for the mobile web app and owned API contracts "
        "with 2 backend teams.",
        ["exp-2"],
    ),
    ("pressHeadline", "Kitebird Engineer Rebuilds Checkout; Conversion Up 9.4%", ["exp-1"]),
    (
        "pressDeck",
        "Frontend Engineer II Riya Kapoor, who also owns a 42-component design system, took payment-step LCP from "
        "4.1s to 1.9s.",
        ["exp-1"],
    ),
    (
        "exp:exp-1",
        "Rebuilt checkout as a single-page flow: conversion rose 9.4% in the A/B test; payment-step LCP from 4.1s "
        "to 1.9s.\nOwns Kite UI, a 42-component React library used by 3 product squads.\nMentored 2 frontend "
        "interns; both received full-time offers.",
        ["exp-1"],
    ),
    (
        "exp:exp-2",
        "Built the Node.js backend-for-frontend for mobile web.\nOwned API contracts with 2 backend teams.",
        ["exp-2"],
    ),
]


@pytest.mark.parametrize(("slot_id", "text", "ids"), CANVAS_COPY, ids=[c[0] for c in CANVAS_COPY])
def test_the_canvas_copy_is_accepted(slot_id: str, text: str, ids: list[str]) -> None:
    result = check_slot(slot(slot_id, text, *ids), FACTS, person=PERSON)
    assert result.problems == []
    assert result.ok


def test_a_slot_that_names_the_person_needs_the_person() -> None:
    s = slot("pressDeck", "Riya Kapoor took payment-step LCP from 4.1s to 1.9s.", "exp-1")
    assert not check_slot(s, FACTS).ok
    assert check_slot(s, FACTS, person=PERSON).ok


def test_the_sentences_are_returned_for_the_claim_check() -> None:
    s = slot("aboutBody", "Built the BFF for 2 teams. Owned the API contracts.", "exp-2")
    assert check_slot(s, FACTS).sentences == ["Built the BFF for 2 teams.", "Owned the API contracts."]


@pytest.mark.parametrize(
    ("written", "in_facts"),
    [("10000", True), ("10,000", True), ("100,000", False), ("10", False)],
)
def test_numbers_compare_without_separators(written: str, in_facts: bool) -> None:
    s = slot("aboutLede", f"Fixed tables for {written} invoices.", "exp-2")
    assert check_slot(s, FACTS).ok is in_facts


# ───────────────────────── numbers ─────────────────────────


def test_a_planted_number_is_rejected() -> None:
    p = problems_of(
        slot(
            "aboutLede",
            "Frontend engineer at Kitebird whose checkout rebuild lifted conversion 9.7%.",
            "exp-1",
        )
    )
    assert any("9.7" in x for x in p)


def test_a_number_from_another_fact_is_rejected() -> None:
    # 42 components belongs to exp-1; the slot cites only exp-2
    p = problems_of(slot("aboutLede", "Frontend engineer who owns a 42-component design system.", "exp-2"))
    assert any("42" in x for x in p)
    # and cites it correctly
    assert check_slot(
        slot("aboutLede", "Frontend engineer who owns a 42-component design system.", "exp-1"), FACTS
    ).ok


def test_citing_both_facts_allows_both_facts_numbers() -> None:
    s = slot(
        "aboutBody",
        "Mentored 2 interns, fixed tables for 10,000+ invoices and owned 2 API contracts.",
        "exp-1",
        "exp-2",
    )
    assert check_slot(s, FACTS).ok


def test_spelled_out_numbers_are_checked_like_digits() -> None:
    assert any("five" in x for x in problems_of(slot("aboutLede", "Built five apps at Kitebird.", "exp-1")))
    assert check_slot(slot("aboutLede", "Mentored two interns at Kitebird.", "exp-1"), FACTS).ok
    assert check_slot(slot("aboutLede", "Mentored three interns at Kitebird.", "exp-1"), FACTS).ok  # 3 squads
    assert any(
        "three" in x for x in problems_of(slot("aboutLede", "Mentored three interns at Kitebird.", "exp-2"))
    )


def test_a_made_up_duration_is_not_a_fact() -> None:
    assert not check_slot(slot("aboutLede", "Engineer with seven years of experience.", "exp-1"), FACTS).ok


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Joined Kitebird in Jul 2025.", True),
        ("Joined Kitebird in July 2025.", True),
        ("Joined Kitebird in 2025.", True),
        ("Joined Kitebird in March 2025.", False),
        ("Joined Kitebird in Jul 2024.", False),
        ("The team may grow in 2025.", True),
        ("Joined Kitebird in May 2025.", False),
    ],
)
def test_months_and_years_are_date_anchors(text: str, ok: bool) -> None:
    assert check_slot(slot("aboutLede", text, "exp-1"), FACTS).ok is ok


# ───────────────────────── names ─────────────────────────


def test_a_fake_employer_is_rejected() -> None:
    p = problems_of(slot("aboutLede", "Frontend engineer at Google who rebuilt checkout.", "exp-1"))
    assert any("Google" in x for x in p)


def test_a_fake_employer_opening_the_sentence_is_rejected() -> None:
    assert any(
        "Google" in x
        for x in problems_of(slot("aboutLede", "Google engineer who rebuilt checkout.", "exp-1"))
    )
    assert any(
        "Google" in x
        for x in problems_of(slot("pressHeadline", "Google Engineer Rebuilds Checkout", "exp-1"))
    )


def test_java_is_not_javascript() -> None:
    assert not check_slot(slot("proj:proj-1", "A browser image tool written in Java.", "proj-1"), FACTS).ok
    assert check_slot(slot("proj:proj-1", "A browser image tool written in JavaScript.", "proj-1"), FACTS).ok
    # and the other way round
    only_java = fact("proj-2", "project", "jvm-lab", "jvm-lab — Experiments in Java.")
    assert not check_slot(
        slot("proj:proj-2", "Experiments in JavaScript.", "proj-2"), {**FACTS, "proj-2": only_java}
    ).ok


def test_names_match_whole_words_only() -> None:
    assert not check_slot(
        slot("aboutLede", "Frontend engineer at Kitebir who rebuilt checkout.", "exp-1"), FACTS
    ).ok
    assert not check_slot(slot("aboutLede", "Frontend engineer at Birdy.", "exp-1"), FACTS).ok
    assert check_slot(slot("aboutLede", "Frontend engineer at Kitebird.", "exp-1"), FACTS).ok


def test_matching_ignores_case_and_accents() -> None:
    cafe = fact("exp-9", "experience", "Engineer", "Café Délice — Engineer", org="Café Délice")
    assert check_slot(slot("aboutLede", "Engineer at Cafe Delice.", "exp-9"), {"exp-9": cafe}).ok


def test_identifiers_may_be_hyphenated_or_spaced_or_joined() -> None:
    assert check_slot(slot("aboutLede", "Built the backend-for-frontend.", "exp-2"), FACTS).ok
    assert check_slot(slot("aboutLede", "Built the backend for frontend.", "exp-2"), FACTS).ok
    assert not check_slot(slot("aboutLede", "Built the Next.js app at Kitebird.", "exp-1"), FACTS).ok


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Engineer at Kitebird with a React-based design system.", True),
        ("Engineer at Kitebird with a Google-scale design system.", False),
        ("Engineer at Kitebird who builds single-page flows.", True),
        ("Engineer at Kitebird who builds swift-kit flows.", False),
        ("Engineer at Kitebird who ships a well-known library.", False),
    ],
)
def test_hyphenated_compounds_need_their_named_or_content_words(text: str, ok: bool) -> None:
    assert check_slot(slot("aboutLede", text, "exp-1"), FACTS).ok is ok


# ───────────────────────── seniority and puffery ─────────────────────────


@pytest.mark.parametrize(
    "verb",
    [
        "led",
        "headed",
        "founded",
        "managed",
        "directed",
        "spearheaded",
        "owned",
        "architected",
        "leads",
        "manager",
    ],
)
def test_seniority_verbs_must_appear_in_the_cited_facts(verb: str) -> None:
    bare = fact(
        "exp-9", "experience", "Engineer", "Acme — Engineer", org="Acme", highlights=["Wrote 3 reports."]
    )
    s = slot("aboutLede", f"Engineer at Acme who {verb} the reports.", "exp-9")
    result = check_slot(s, {"exp-9": bare})
    assert not result.ok
    assert any(verb in x for x in result.problems)


def test_a_seniority_verb_in_the_facts_is_fine_in_any_form() -> None:
    assert check_slot(
        slot("aboutLede", "Engineer at Kitebird who leads the storefront migration.", "exp-1"), FACTS
    ).ok
    assert check_slot(
        slot("aboutLede", "Engineer at Kitebird who owns the component library.", "exp-1"), FACTS
    ).ok
    # "Led" is in exp-1 but not in exp-2
    assert not check_slot(slot("aboutLede", "Engineer at Kitebird who led the migration.", "exp-2"), FACTS).ok


def test_their_own_is_not_a_claim_of_ownership() -> None:
    assert check_slot(
        slot("aboutLede", "Engineer at Kitebird with their own approach to motion.", "exp-2"), FACTS
    ).ok


@pytest.mark.parametrize(
    "word",
    [
        "passionate",
        "world-class",
        "world class",
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
    ],
)
def test_puffery_is_rejected(word: str) -> None:
    result = check_slot(slot("aboutLede", f"A {word} engineer at Kitebird.", "exp-1"), FACTS)
    assert any("puffery" in x for x in result.problems)


def test_puffery_the_facts_themselves_say_is_not_added_by_the_writer() -> None:
    own = fact("quote-2", "quote", "I am passionate about quiet interfaces.")
    s = slot("tagline", "I am passionate about quiet interfaces.", "quote-2")
    assert check_slot(s, {"quote-2": own}).ok


def test_gendered_pronouns_are_not_assumed() -> None:
    result = check_slot(slot("aboutLede", "Engineer at Kitebird; her work spans checkout.", "exp-1"), FACTS)
    assert any("pronoun" in x for x in result.problems)


# ───────────────────────── corpus ─────────────────────────


def test_an_empty_corpus_is_a_failure() -> None:
    result = check_slot(
        slot(
            "aboutLede",
            "Engineer at Kitebird.",
        ),
        FACTS,
    )
    assert not result.ok
    assert any("cites no facts" in x for x in result.problems)


def test_unknown_fact_ids_are_a_failure() -> None:
    result = check_slot(slot("aboutLede", "Engineer at Kitebird.", "exp-99"), FACTS)
    assert not result.ok
    assert any("exp-99" in x for x in result.problems)
    # one good id does not excuse a bad one
    assert not check_slot(slot("aboutLede", "Engineer at Kitebird.", "exp-1", "exp-99"), FACTS).ok


def test_a_slot_cannot_borrow_the_whole_resume() -> None:
    text = "Engineer at Kitebird who fixed tables for 10,000+ invoices."
    assert not check_slot(slot("aboutLede", text, "exp-1"), FACTS).ok
    assert check_slot(slot("aboutLede", text, "exp-1", "exp-2"), FACTS).ok


def test_the_grade_is_not_part_of_the_corpus() -> None:
    edu = fact("edu-1", "education", "B.Tech", "B.Tech — Nandi", org="Nandi", grade="CGPA 8.6 / 10")
    assert "8.6" not in fact_text(edu)
    assert not check_slot(
        slot("aboutLede", "Studied at Nandi with a CGPA of 8.6.", "edu-1"), {"edu-1": edu}
    ).ok


def test_fact_text_covers_quotes_fields_and_highlights() -> None:
    text = fact_text(KITEBIRD)
    for needle in ("Kitebird", "Bengaluru", "Jul 2025", "July 2025", "9.4%", "D2C", "React"):
        assert needle in text


def test_slot_corpus_is_only_the_cited_facts() -> None:
    corpus = slot_corpus(slot("aboutLede", "x", "exp-2"), FACTS)
    assert "10,000+" in corpus
    assert "9.4%" not in corpus


# ───────────────────────── limits and format ─────────────────────────


@pytest.mark.parametrize("slot_id", ["tagline", "aboutLede", "aboutBody", "pressHeadline", "pressDeck"])
def test_the_character_limit_is_enforced(slot_id: str) -> None:
    limit = SLOT_LIMITS[slot_id]
    text = "Kitebird " * (limit // 9 + 2)
    s = slot(slot_id, text[: limit + 1], "exp-1")
    assert any("limit" in x for x in check_slot(s, FACTS).problems)
    assert not any(
        "limit" in x for x in check_slot(slot(slot_id, text[:limit].strip(), "exp-1"), FACTS).problems
    )


def test_per_fact_slots_use_their_prefix_limit() -> None:
    long_line = ("Mentored 2 interns. " * 20)[:301]
    assert any("limit" in x for x in check_slot(slot("exp:exp-1", long_line, "exp-1"), FACTS).problems)
    assert any(
        "limit" in x for x in check_slot(slot("proj:proj-1", "pixel-forge " * 20, "proj-1"), FACTS).problems
    )


def test_an_unknown_slot_id_fails() -> None:
    assert any("known slot" in x for x in problems_of(slot("hero", "Engineer at Kitebird.", "exp-1")))


def test_exp_slots_have_at_most_three_lines_and_cite_their_own_fact() -> None:
    four = "\n".join(["Mentored 2 interns."] * 4)
    assert any("4 lines" in x for x in problems_of(slot("exp:exp-1", four, "exp-1")))
    assert any("must cite" in x for x in problems_of(slot("exp:exp-1", "Mentored 2 interns.", "exp-2")))
    assert any("does not exist" in x for x in problems_of(slot("exp:exp-77", "Mentored 2 interns.", "exp-1")))
    assert any(
        "experience fact" in x for x in problems_of(slot("exp:proj-1", "Written in JavaScript.", "proj-1"))
    )


@pytest.mark.parametrize(
    "text",
    [
        "<b>Frontend</b> engineer at Kitebird.",
        "Frontend engineer at <Kitebird>",
        "Frontend engineer at Kitebird &amp; friends.",
        "**Frontend** engineer at Kitebird.",
        "*Frontend* engineer at Kitebird.",
        "_Frontend_ engineer at Kitebird.",
        "`Frontend` engineer at Kitebird.",
        "# Frontend engineer at Kitebird.",
        "- Frontend engineer at Kitebird.",
        "1. Frontend engineer at Kitebird.",
        "> Frontend engineer at Kitebird.",
        "[Kitebird](https://kitebird.example) engineer.",
        "~~Frontend~~ engineer at Kitebird.",
    ],
)
def test_html_and_markdown_are_rejected(text: str) -> None:
    result = check_slot(slot("aboutLede", text, "exp-1"), FACTS)
    assert not result.ok
    assert any("HTML" in x or "markdown" in x for x in result.problems)


@pytest.mark.parametrize(
    "text",
    [
        "Mentored 2 interns, 6-month terms.",
        "A/B test of the checkout: conversion rose 9.4%.",
        "Fixed tables for 10,000+ invoices (virtualised lists).",
        "Led the migration; kept the build green_ever.",
    ],
)
def test_plain_text_with_ordinary_punctuation_is_not_markdown(text: str) -> None:
    result = check_slot(slot("aboutBody", text, "exp-1", "exp-2"), FACTS)
    assert not any("markdown" in x or "HTML" in x for x in result.problems)


def test_private_details_are_rejected() -> None:
    assert any("email" in x for x in problems_of(slot("aboutLede", "Write to riya@example.com.", "exp-1")))
    assert any("emoji" in x for x in problems_of(slot("aboutLede", "Engineer at Kitebird 🚀", "exp-1")))
    assert any(
        "web address" in x
        for x in problems_of(slot("aboutLede", "Engineer at https://kitebird.example", "exp-1"))
    )
    assert any(
        "line break" in x
        for x in problems_of(slot("aboutLede", "Engineer at Kitebird.\nSecond line.", "exp-1"))
    )


def test_an_empty_slot_makes_no_claim() -> None:
    result = check_slot(slot("aboutBody", ""), FACTS)
    assert result.ok
    assert result.sentences == []


def test_resume_slots_without_facts_need_the_person() -> None:
    own = slot("pressHeadline", "Riya Kapoor, Frontend Engineer", origin="resume")
    assert not check_slot(own, FACTS).ok
    assert check_slot(own, FACTS, person=PERSON).ok
    # the same text from the model is never accepted without a cited fact
    assert not check_slot(slot("pressHeadline", "Riya Kapoor, Frontend Engineer"), FACTS, person=PERSON).ok
    # and the person does not license other names
    assert not check_slot(
        slot("pressHeadline", "Riya Kapoor, Google Engineer", origin="resume"), FACTS, person=PERSON
    ).ok


# ───────────────────────── sentences ─────────────────────────


def test_sentences_split_on_terminators_not_on_abbreviations_or_decimals() -> None:
    text = "Studied at St. Xavier's College, Mumbai. Conversion rose 9.4% in Q3. It went well! Dr. Rao agreed? Yes."
    assert split_sentences(text) == [
        "Studied at St. Xavier's College, Mumbai.",
        "Conversion rose 9.4% in Q3.",
        "It went well!",
        "Dr. Rao agreed?",
        "Yes.",
    ]


def test_every_line_of_an_exp_slot_is_its_own_sentence() -> None:
    assert split_sentences("- Built a thing\n• Shipped it\n\nRan it.") == [
        "Built a thing",
        "Shipped it",
        "Ran it.",
    ]


# ───────────────────────── fixtures: real extracted facts ─────────────────────────


def test_riya_canvas_lede_is_grounded_in_the_extracted_riya_facts() -> None:
    fx = load_fixture("riya")
    facts = {f.id: f for f in facts_for("riya")}
    exp1 = next(f for f in facts.values() if f.kind == "experience" and f.title == "Frontend Engineer II")
    s = slot(
        "aboutLede",
        "Frontend engineer at Kitebird who owns a 42-component design system and rebuilt checkout for a measured "
        "9.4% conversion lift.",
        exp1.id,
    )
    assert check_slot(s, facts, person=person_for(fx.profile)).ok


def _wordings(name: str) -> list[tuple[Fact, str]]:
    """Every highlight and detail of every extracted fact of a fixture, as (fact, text within the body limit)."""
    out: list[tuple[Fact, str]] = []
    for f in facts_for(name):
        out += [(f, text[:380]) for text in [*f.highlights, f.detail or ""] if text]
    return out


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_the_facts_own_wording_is_accepted(name: str) -> None:
    fx = load_fixture(name)
    by_id = {f.id: f for f in facts_for(name)}
    for f, text in _wordings(name):
        result = check_slot(slot("aboutBody", text, f.id), by_id, person=person_for(fx.profile))
        assert result.ok, (f.id, text, result.problems)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_a_changed_number_or_an_added_employer_is_always_caught(name: str) -> None:
    by_id = {f.id: f for f in facts_for(name)}
    for f, text in _wordings(name):
        if re.search(r"\d", text):
            changed = re.sub(r"\d+", "7777", text, count=1)
            result = check_slot(slot("aboutBody", changed, f.id), by_id)
            assert not result.ok and any("7777" in x for x in result.problems), (f.id, changed)
        added = f"{text.rstrip('.')} at Globex Corporation."
        result = check_slot(slot("aboutBody", added, f.id), by_id)
        assert any("Globex" in x for x in result.problems), (f.id, added)


# ───────────────────────── verify_slots ─────────────────────────

DECISION_CTX = DecisionContext(
    profile_id="p1", analysis_id=None, is_sample=False, request_id="r1", purpose_tag="portfolio"
)
GOOD = slot("aboutLede", "Frontend engineer at Kitebird who rebuilt checkout.", "exp-1")
BAD = slot("aboutBody", "Mentored 9 interns at Kitebird.", "exp-1")
FACT_LIST = list(FACTS.values())


async def test_verify_slots_with_decisions_off_is_the_code_check() -> None:
    kept, problems = await verify_slots([GOOD, BAD], FACT_LIST, NullDecisionService(), DECISION_CTX)
    assert [s.id for s in kept] == ["aboutLede"]
    assert list(problems) == ["aboutBody"]
    assert any("9" in x for x in problems["aboutBody"])


async def test_verify_slots_asks_about_each_sentence_of_the_slots_that_passed() -> None:
    decisions = FakeDecisionService()
    body = slot("aboutBody", "Built the backend-for-frontend in Node.js. Owned 2 API contracts.", "exp-2")
    bad = slot("pressDeck", "Mentored 9 interns at Kitebird.", "exp-1")
    kept, problems = await verify_slots([GOOD, bad, body], FACT_LIST, decisions, DECISION_CTX)
    assert [x.id for x in kept] == ["aboutLede", "aboutBody"]
    assert list(problems) == ["pressDeck"]
    ((claims, ctx),) = decisions.copy_calls
    assert ctx is DECISION_CTX
    assert [(c.slot_id, c.sentence) for c in claims] == [
        ("aboutLede", "Frontend engineer at Kitebird who rebuilt checkout."),
        ("aboutBody", "Built the backend-for-frontend in Node.js."),
        ("aboutBody", "Owned 2 API contracts."),
    ]  # the slot that failed the code check never reached the decision service


async def test_the_claim_corpus_is_only_the_cited_facts() -> None:
    decisions = FakeDecisionService()
    s = slot("aboutBody", "Built the backend-for-frontend in Node.js.", "exp-2")
    await verify_slots([s], FACT_LIST, decisions, DECISION_CTX)
    ((claims, _),) = decisions.copy_calls
    corpus = claims[0].corpus
    assert "Kitebird — Frontend Engineer Bengaluru · May 2024 – Jun 2025" in corpus
    assert "backend-for-frontend" in corpus
    assert "9.4%" not in corpus
    assert "pixel-forge" not in corpus


async def test_a_dropped_sentence_fails_its_slot_with_the_reason() -> None:
    decisions = FakeDecisionService(copy={"Owned 2 API contracts.": False})
    s = slot("aboutBody", "Built the backend-for-frontend in Node.js. Owned 2 API contracts.", "exp-2")
    kept, problems = await verify_slots([GOOD, s], FACT_LIST, decisions, DECISION_CTX)
    assert [x.id for x in kept] == ["aboutLede"]
    assert "Owned 2 API contracts." in problems["aboutBody"][0]
    assert "says_nothing" in problems["aboutBody"][0] or "fake" in problems["aboutBody"][0]


async def test_a_failing_decision_service_keeps_what_the_code_check_passed() -> None:
    decisions = FakeDecisionService().fail("check_copy", RuntimeError("boom"))
    kept, problems = await verify_slots([GOOD], FACT_LIST, decisions, DECISION_CTX)
    assert [x.id for x in kept] == ["aboutLede"]
    assert problems == {}


async def test_a_service_without_check_copy_is_the_code_check() -> None:
    class Old:
        pass

    kept, problems = await verify_slots([GOOD, BAD], FACT_LIST, Old(), DECISION_CTX)  # type: ignore[arg-type]
    assert [x.id for x in kept] == ["aboutLede"]
    assert list(problems) == ["aboutBody"]


async def test_a_service_that_returns_fewer_verdicts_is_matched_by_sentence() -> None:
    class Partial(NullDecisionService):
        async def check_copy(self, claims, ctx):  # type: ignore[no-untyped-def]
            return [CopyVerdict(claims[1].slot_id, claims[1].sentence, keep=False, relation="contradicts")]

    s = slot("aboutBody", "Built the backend-for-frontend in Node.js. Owned 2 API contracts.", "exp-2")
    kept, problems = await verify_slots([GOOD, s], FACT_LIST, Partial(), DECISION_CTX)
    assert [x.id for x in kept] == ["aboutLede"]
    assert "aboutBody" in problems


async def test_empty_slots_are_neither_kept_nor_reported() -> None:
    empty = slot("aboutBody", "")
    kept, problems = await verify_slots([empty, GOOD], FACT_LIST, FakeDecisionService(), DECISION_CTX)
    assert [x.id for x in kept] == ["aboutLede"]
    assert problems == {}


async def test_verify_slots_with_the_person_allows_their_name() -> None:
    s = slot("pressDeck", "Riya Kapoor rebuilt checkout at Kitebird.", "exp-1")
    kept, _ = await verify_slots([s], FACT_LIST, NullDecisionService(), DECISION_CTX)
    assert kept == []
    kept, problems = await verify_slots([s], FACT_LIST, NullDecisionService(), DECISION_CTX, person=PERSON)
    assert [x.id for x in kept] == ["pressDeck"]
    assert problems == {}


def test_copy_claim_is_the_decision_services_shape() -> None:
    claim = CopyClaim("tagline", "A sentence.", "A corpus.")
    assert (claim.slot_id, claim.sentence, claim.corpus) == ("tagline", "A sentence.", "A corpus.")


# ───────────────────────── numbers carry their unit ─────────────────────────


def sized(*highlights: str, grade: str | None = None) -> dict[str, Fact]:
    one = fact(
        "exp-9",
        "experience",
        "Engineer",
        "Acme — Engineer",
        org="Acme",
        highlights=list(highlights),
        grade=grade,
    )
    return {"exp-9": one}


def ok_against(text: str, *highlights: str, grade: str | None = None) -> bool:
    return check_slot(slot("aboutLede", text, "exp-9"), sized(*highlights, grade=grade)).ok


@pytest.mark.parametrize(
    ("text", "corpus", "ok"),
    [
        # a bare number is not the same number with a scale
        ("Engineer at Acme serving 42M users.", "Served 42 users.", False),
        ("Engineer at Acme serving 42 million users.", "Served 42 users.", False),
        ("Engineer at Acme serving 42 users.", "Served 42M users.", False),
        ("Engineer at Acme serving 42M users.", "Served 42M users.", True),
        ("Engineer at Acme serving 42 million users.", "Served 42M users.", True),
        ("Engineer at Acme serving 10K users.", "Served 10,000 users.", True),
        ("Engineer at Acme serving 10,000 users.", "Served 10K users.", True),
        ("Engineer at Acme serving 10K users.", "Served 10 users.", False),
        ("Engineer at Acme with a 2 lakh budget.", "Budget of 2 lakh.", True),
        ("Engineer at Acme with a 2 crore budget.", "Budget of 2 lakh.", False),
        # percent and multiplier are different units
        ("Engineer at Acme: 9.4x faster.", "Conversion rose 9.4%.", False),
        ("Engineer at Acme: 9.4% faster.", "Conversion rose 9.4x.", False),
        ("Engineer at Acme: conversion up 9.4 percent.", "Conversion rose 9.4%.", True),
        ("Engineer at Acme: 10x faster.", "Builds are 10x faster.", True),
        # money: the amount, the scale and the currency
        ("Engineer at Acme who raised $2M.", "Raised 2M.", False),
        ("Engineer at Acme who raised 2M.", "Raised $2M.", True),
        ("Engineer at Acme who raised $2M.", "Raised $2M.", True),
        ("Engineer at Acme who raised ₹2M.", "Raised $2M.", False),
        ("Engineer at Acme who raised $20M.", "Raised $2M.", False),
        ("Engineer at Acme who raised ₹1,200 crore.", "Raised ₹1,200 crore.", True),
        ("Engineer at Acme who raised ₹1,200 crore.", "Raised 1,200 crore.", False),
        ("Engineer at Acme who raised ₹1,200 crore.", "Raised Rs. 1200 crore.", True),
        ("Engineer at Acme who raised ₹1,200 lakh.", "Raised ₹1,200 crore.", False),
        # ordinary quantities still compare bare, and so do durations written with an attached unit
        ("Engineer at Acme with 10+ years.", "Ten-plus: 10+ years.", True),
        ("Engineer at Acme: LCP from 4.1s to 1.9s.", "LCP fell from 4.1s to 1.9s.", True),
        ("Engineer at Acme: LCP from 4.1s to 1.9s.", "LCP fell from 4.1 to 1.9.", True),
    ],
)
def test_a_number_is_compared_with_its_unit(text: str, corpus: str, ok: bool) -> None:
    assert ok_against(text, corpus) is ok


@pytest.mark.parametrize(
    ("text", "corpus", "ok"),
    [
        ("Engineer at Acme serving millions of users.", "Served 2 million users.", False),
        ("Engineer at Acme serving millions of users.", "Served millions of users.", True),
        ("Engineer at Acme reaching hundreds of thousands of users.", "Reached 300 thousand users.", False),
        ("Engineer at Acme reaching hundreds of thousands of users.", "Reached hundreds of thousands.", True),
        ("Engineer at Acme reaching thousands of users.", "Reached hundreds of users.", False),
        ("Engineer at Acme reaching thousands of users.", "Reached thousands of users.", True),
        ("Engineer at Acme with billions of events.", "Processed 3 billion events.", False),
        ("Engineer at Acme with a million users.", "Served 2 million users.", True),
        ("Engineer at Acme with a million users.", "Served 2 users.", False),
    ],
)
def test_vague_multipliers_must_be_in_the_facts(text: str, corpus: str, ok: bool) -> None:
    assert ok_against(text, corpus) is ok


def test_a_number_inside_a_name_is_not_a_claim() -> None:
    assert ok_against("Engineer at Acme running k8s clusters.", "Ran k8s clusters.")
    assert ok_against("Engineer at Acme on React 18 and Vite 5.", "Moved to React 18 and Vite 5.")


@pytest.mark.parametrize(
    ("text", "leaks"),
    [
        ("Engineer at Acme with a score of 8.6.", True),
        ("Engineer at Acme with an 8.60 average.", True),
        ("Engineer at Acme, 8.6/10 overall.", True),
        ("Engineer at Acme who led 10 interns.", False),  # the 10 of "8.6 / 10" is the scale, not the score
        ("Engineer at Acme who led 3 interns.", False),
    ],
)
def test_the_grade_of_a_cited_fact_cannot_be_written_as_a_number(text: str, leaks: bool) -> None:
    corpus = sized(
        "Led 10 interns.", "Scored 8.6 on the bench.", "Mentored 3 interns.", grade="CGPA 8.6 / 10"
    )
    result = check_slot(slot("aboutLede", text, "exp-9"), corpus)
    assert any("grade" in x for x in result.problems) is leaks


def test_a_grade_number_is_only_private_for_the_facts_the_slot_cites() -> None:
    edu = fact("edu-1", "education", "B.Tech", "B.Tech — Nandi", org="Nandi", grade="8.6")
    work = sized("Reduced p95 latency from 8.6s to 2s.")["exp-9"]
    text = "Engineer at Acme who cut p95 latency from 8.6s to 2s."
    assert check_slot(slot("aboutLede", text, "exp-9"), {"exp-9": work, "edu-1": edu}).ok
    assert not check_slot(slot("aboutLede", text, "exp-9", "edu-1"), {"exp-9": work, "edu-1": edu}).ok


# ───────────────────────── truncated quotes ─────────────────────────


def test_a_quote_cut_off_mid_phrase_is_rejected() -> None:
    quote = Fact(
        id="quote-1",
        kind="quote",
        title="I like boring, correct systems: idempotent writes, sharding without downtime and a quiet pager.",
        highlights=[],
        tags=[],
        sourceRefs=[
            SourceRef(
                sourceId="resume",
                quote="I like boring, correct systems: idempotent writes, sharding without downtime and a quiet pager.",
            )
        ],
    )
    cut = CopySlot(
        id="tagline",
        text="I like boring, correct systems: idempotent writes, sharding without downtime and a quiet",
        origin="model",
        factIds=["quote-1"],
    )
    assert any("mid-phrase" in p for p in check_slot(cut, {"quote-1": quote}).problems)
    clause = CopySlot(
        id="tagline", text="I like boring, correct systems.", origin="model", factIds=["quote-1"]
    )
    assert not any("mid-phrase" in p for p in check_slot(clause, {"quote-1": quote}).problems)
