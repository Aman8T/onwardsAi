"""The resume variant: relevance never rewrites, code selects, gap skills stay out, questions block the export."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.contract.match import Coverage, DecisionChoice, Fact, FactRef
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.match.bullets import all_bullets, export_blockers
from tayyari.match.errors import ExportBlocked
from tayyari.match.models import FactBook, Report
from tayyari.match.posting import parse_posting
from tayyari.match.service import MatchOutcome, MatchService
from tayyari.match.variant import (
    BULLETS_OLDER,
    BULLETS_RECENT,
    Relevance,
    build_skills,
    gap_terms,
    lexical_relevance,
    score_relevance,
    skill_violations,
)
from tests.fakes.claude import FakeClaudeClient
from tests.unit.match.support import (
    CTX,
    HAIKU,
    Persona,
    meera,
    posting_answer,
    relevance_responder,
    riya,
    script_pipeline,
)


async def analysed(
    persona: Persona, fake: FakeClaudeClient | None = None, **phrase: dict[str, Any]
) -> tuple[MatchService, MatchOutcome, FakeClaudeClient]:
    fake = fake or script_pipeline(FakeClaudeClient(), persona, phrase=phrase or None)
    service = MatchService(claude=fake, model=HAIKU)
    outcome = await service.analyse(
        profile=persona.profile,
        context=persona.context,
        posting_text=persona.posting_text,
        call_ctx=CTX,
        portfolio_facts=persona.portfolio_facts,
    )
    return service, outcome, fake


async def composed(persona: Persona, **phrase: dict[str, Any]):  # type: ignore[no-untyped-def]
    service, outcome, fake = await analysed(persona, **phrase)
    result = await service.compose_resume(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    return service, outcome, result, fake


# ───────────────────────── selection ─────────────────────────


async def test_the_most_recent_job_gets_five_bullets_older_ones_three_and_every_bullet_cites_a_fact() -> None:
    _, outcome, result, _ = await composed(riya())
    variant = result.variant
    sizes = [len(e.bullets) for e in variant.experience]
    assert sizes[0] <= BULLETS_RECENT
    assert all(n <= BULLETS_OLDER for n in sizes[1:])
    assert sizes[0] == BULLETS_RECENT  # the first job had six facts to choose from
    assert len(variant.experience) <= 5
    book = outcome.book.by_id()
    for bullet in all_bullets(variant):
        if bullet.status == "dropped":
            continue
        assert bullet.factIds
        assert all(f in book for f in bullet.factIds)
    assert any(c.kind == "left_out" for c in variant.changes)


async def test_bullets_are_ordered_by_relevance_and_an_entry_keeps_its_most_relevant_ones() -> None:
    _, _, result, _ = await composed(meera())
    first = result.variant.experience[0]
    assert first.entryFactId == "exp-1"
    ranked = [b.factIds[0] for b in first.bullets]
    scores = result.relevance.scores
    assert [scores[f] for f in ranked] == sorted((scores[f] for f in ranked), reverse=True)
    assert "exp-1.5" not in ranked or len(ranked) == 5  # the mentoring line is the least relevant of five


async def test_a_fact_only_a_public_page_shows_is_surfaced_into_the_skills_and_the_changes() -> None:
    _, _, result, _ = await composed(meera())
    variant = result.variant
    names = {i.name for g in variant.skills for i in g.items}
    assert "Looker Studio" in names
    surfaced = [c for c in variant.changes if c.kind == "surfaced"]
    assert surfaced
    assert "skill-looker-studio" in surfaced[0].factIds
    assert "your resume did not say it" in surfaced[0].text


def test_the_skills_use_the_postings_spelling_only_when_a_fact_supports_the_skill() -> None:
    from tayyari.contract.match import Requirement

    ab = Fact(
        id="skill-ab",
        text="A/B testing",
        kind="skill",
        refs=[FactRef(source="resume", docId="resume", lines=[3, 3], quote="Ran 38 A/B tests")],
        evidenceTier="used",
    )
    other = Fact(
        id="skill-seo",
        text="SEO",
        kind="skill",
        refs=[FactRef(source="resume", docId="resume", lines=[4, 4], quote="Wrote the SEO brief")],
        evidenceTier="used",
    )
    book = FactBook(name="A B", facts=[ab, other])
    reqs = [
        Requirement(
            id="r1",
            text="AB testing",
            quote="AB testing",
            tier="required",
            kind="skill",
            provenance="stated",
            terms=["AB testing"],
        ),
        Requirement(
            id="r2",
            text="Search engine optimization",
            quote="Search engine optimization",
            tier="preferred",
            kind="skill",
            provenance="stated",
            terms=["Search engine optimization"],
        ),
    ]
    rows = [
        Coverage(requirementId="r1", factIds=["skill-ab"], status="covered", level=4, note=""),
        Coverage(requirementId="r2", factIds=["skill-seo"], status="synonym_only", level=3, note=""),
    ]
    groups = build_skills(book, reqs, rows, Relevance(), Report())
    names = [i.name for g in groups for i in g.items]
    assert names == [
        "AB testing",
        "Search engine optimization",
    ]  # the posting's own spelling of the same thing
    assert [i.factIds for g in groups for i in g.items] == [["skill-ab"], ["skill-seo"]]


async def composed_with(persona: Persona, fake: FakeClaudeClient):  # type: ignore[no-untyped-def]
    service, outcome, _ = await analysed(persona, fake)
    result = await service.compose_resume(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    return service, outcome, result, fake


# ───────────────────────── gap skills never enter ─────────────────────────


def _book_with_sql() -> FactBook:
    sql = Fact(
        id="skill-sql",
        text="SQL",
        kind="skill",
        refs=[FactRef(source="resume", docId="resume", lines=[9, 9], quote="Tools: Excel, SQL, Canva")],
        evidenceTier="used",
    )
    excel = Fact(
        id="skill-excel",
        text="Excel",
        kind="skill",
        refs=[FactRef(source="resume", docId="resume", lines=[9, 9], quote="Tools: Excel, SQL, Canva")],
        evidenceTier="used",
    )
    return FactBook(name="A B", facts=[sql, excel])


def test_a_skill_the_coverage_calls_a_gap_never_enters_the_skills_section() -> None:
    posting = parse_posting(posting_answer(meera()), meera().posting_text).posting
    book = _book_with_sql()
    sql_req = next(r for r in posting.requirements if "SQL" in r.terms)
    excel_req = posting.requirements[0]
    coverage = [
        Coverage(requirementId=sql_req.id, factIds=[], status="missing_gap", level=0, note=""),
        Coverage(requirementId=excel_req.id, factIds=["skill-excel"], status="covered", level=4, note=""),
    ]
    # a model (or anyone) rates SQL highly: it must not matter
    relevance = Relevance(scores={"skill-sql": 99, "skill-excel": 99}, links={"skill-sql": [sql_req.id]})
    report = Report()
    groups = build_skills(book, posting.requirements, coverage, relevance, report)
    names = {i.name for g in groups for i in g.items}
    assert "SQL" not in names
    assert "Excel" in names
    assert ("skill", "SQL") in {(d.kind, d.label) for d in report.dropped}
    assert gap_terms(posting.requirements, coverage) == {"SQL"}


def test_the_skills_check_names_what_must_come_out_of_a_section_it_did_not_build() -> None:
    from tayyari.contract.match import SkillGroup, SkillItem

    posting = parse_posting(posting_answer(meera()), meera().posting_text).posting
    book = _book_with_sql()
    sql_req = next(r for r in posting.requirements if "SQL" in r.terms)
    coverage = [Coverage(requirementId=sql_req.id, factIds=[], status="missing_gap", level=0, note="")]
    groups = [
        SkillGroup(
            group="Tools",
            items=[
                SkillItem(name="SQL", factIds=["skill-sql"]),  # a gap term
                SkillItem(name="Segment", factIds=["no-such-fact"]),  # no fact backs it
                SkillItem(name="Excel", factIds=["skill-excel"]),
            ],
        )
    ]
    assert skill_violations(groups, book, posting.requirements, coverage) == ["SQL", "Segment"]


async def test_no_skill_of_the_final_resume_names_a_gap_requirement() -> None:
    for persona in (meera(), riya()):
        _, outcome, result, _ = await composed(persona)
        gaps = gap_terms(outcome.posting.requirements, outcome.result.coverage)
        assert gaps
        names = {i.name.casefold() for g in result.variant.skills for i in g.items}
        assert not names & {g.casefold() for g in gaps}
        assert (
            skill_violations(
                result.variant.skills, outcome.book, outcome.posting.requirements, outcome.result.coverage
            )
            == []
        )


# ───────────────────────── relevance never rewrites ─────────────────────────


async def test_the_relevance_schema_has_no_text_and_a_rewrite_in_the_answer_changes_nothing() -> None:
    persona = meera()
    _, outcome, _ = await analysed(persona)
    before = outcome.book.model_dump()
    sneaky = relevance_responder(persona)

    def with_rewrites(call):  # type: ignore[no-untyped-def]
        data = sneaky(call)
        for row in data["scores"]:
            row["text"] = "Led a team of 400 and tripled revenue."  # not in the schema; must be ignored
            row["rewrite"] = "Invented."
        return data

    fake2 = FakeClaudeClient().script_structured("match.relevance", with_rewrites)
    scores = await score_relevance(
        fake2, model=HAIKU, requirements=outcome.posting.requirements, facts=outcome.book.facts, call_ctx=CTX
    )
    props = fake2.structured_calls[0].schema["properties"]["scores"]["items"]["properties"]
    assert set(props) == {"factId", "requirementIds", "score"}
    assert all(isinstance(v, int) and 0 <= v <= 100 for v in scores.scores.values())
    assert outcome.book.model_dump() == before  # the facts are untouched
    assert scores.from_model


async def test_scores_are_clamped_and_unknown_ids_ignored() -> None:
    persona = meera()
    _, outcome, _ = await analysed(persona)
    fake = FakeClaudeClient().script_structured(
        "match.relevance",
        {
            "scores": [
                {"factId": "exp-1.1", "requirementIds": ["r1", "r99"], "score": 250},
                {"factId": "exp-1.2", "requirementIds": [], "score": -40},
                {"factId": "nope", "requirementIds": [], "score": 90},
            ]
        },
    )
    got = await score_relevance(
        fake, model=HAIKU, requirements=outcome.posting.requirements, facts=outcome.book.facts, call_ctx=CTX
    )
    assert got.scores["exp-1.1"] == 100
    assert got.scores["exp-1.2"] == 0
    assert "nope" not in got.scores
    assert got.links["exp-1.1"] == ["r1"]


async def test_a_failed_relevance_call_falls_back_to_word_overlap_and_the_resume_still_builds() -> None:
    persona = riya()
    fake = script_pipeline(FakeClaudeClient(), persona)
    fake._queues.structured["match.relevance"] = []
    fake.script_structured("match.relevance", ClaudeOverloaded())
    service, outcome, _ = await analysed(persona, fake)
    result = await service.compose_resume(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    assert not result.relevance.from_model
    assert result.variant.experience
    lexical = lexical_relevance(outcome.posting.requirements, outcome.book.facts)
    assert (
        lexical.scores["exp-1.4"] > lexical.scores["exp-5.1"]
    )  # the accessibility audit beats the student workshops


# ───────────────────────── phrasing: the checks that gate every sentence ─────────────────────────


async def test_the_phrase_calls_are_at_most_three_and_use_the_phrase_purpose() -> None:
    _, _, _, fake = await composed(riya())
    calls = fake.calls_for("match.phrase")
    assert 1 <= len(calls) <= 3
    assert all(c.effort is None for c in calls)
    props = calls[0].schema["properties"]["bullets"]["items"]["properties"]
    assert list(props).index("factIds") < list(props).index("verdict")
    assert "allowed posting terms" in calls[0].user_text


async def test_a_number_that_is_not_in_the_facts_becomes_a_placeholder_and_blocks_the_export() -> None:
    service, outcome, result, _ = await composed(
        meera(),
        **{
            "b-exp-1.3": {
                "factIds": ["exp-1.3"],
                "text": "Ran 38 A/B tests on landing pages and checkout nudges, lifting site conversion by 40%.",
                "verdict": "exact",
            }
        },
    )
    variant = result.variant
    bullet = next(b for b in all_bullets(variant) if b.id == "b-exp-1.3")
    assert bullet.status == "needs_user"
    assert bullet.placeholders == ["[x]%"]
    assert "40" not in bullet.text
    assert "by [x]%" in bullet.text
    assert [d.kind for d in variant.decisions if d.bulletId == "b-exp-1.3"] == ["number"]
    assert export_blockers(variant)
    with pytest.raises(ExportBlocked):
        await service.export_resume(variant, outcome.book)


async def test_the_person_fills_the_figure_and_the_export_opens() -> None:
    service, outcome, result, _ = await composed(
        meera(),
        **{
            "b-exp-1.3": {
                "factIds": ["exp-1.3"],
                "text": "Ran 38 A/B tests on landing pages, lifting site conversion by 40%.",
                "verdict": "exact",
            }
        },
    )
    settled = service.settle_resume(
        result.variant,
        outcome.book,
        outcome.posting,
        outcome.result,
        [DecisionChoice(bulletId="b-exp-1.3", choice="fill", value="6.4%")],
    )
    assert settled.refused == []
    bullet = next(b for b in all_bullets(settled.variant) if b.id == "b-exp-1.3")
    assert bullet.status == "verified"
    assert "by 6.4%" in bullet.text
    assert bullet.placeholders == []
    assert any(
        f.startswith("user-") for f in bullet.factIds
    )  # the figure is a fact in the person's own words
    assert settled.variant.decisions == []
    assert any(f.refs[0].source == "user" for f in settled.book.facts)
    exported = await service.export_resume(settled.variant, settled.book)
    assert exported.files.pdf and exported.files.docx
    assert all(item.ok for item in exported.variant.lint.items)  # type: ignore[union-attr]


async def test_an_unusable_figure_is_refused_and_the_bullet_stays_open() -> None:
    service, outcome, result, _ = await composed(
        meera(),
        **{
            "b-exp-1.3": {
                "factIds": ["exp-1.3"],
                "text": "Ran 38 A/B tests, lifting conversion by 40%.",
                "verdict": "exact",
            }
        },
    )
    settled = service.settle_resume(
        result.variant,
        outcome.book,
        outcome.posting,
        outcome.result,
        [
            DecisionChoice(bulletId="b-exp-1.3", choice="fill", value="a lot"),
            DecisionChoice(bulletId="b-exp-1.3", choice="keep"),  # not an option of a number question
            DecisionChoice(bulletId="b-zzz", choice="drop"),
        ],
    )
    assert len(settled.refused) == 3
    assert export_blockers(settled.variant)


async def test_a_scope_verb_stronger_than_the_facts_is_a_stretch_the_person_must_settle() -> None:
    draft = {
        "b-exp-1.4": {
            "factIds": ["exp-1.4"],
            "text": "Led a team of 3 marketers and 2 freelancers and ran the Monday growth review.",
            "verdict": "exact",
        }
    }
    service, outcome, result, _ = await composed(meera(), **draft)
    bullet = next(b for b in all_bullets(result.variant) if b.id == "b-exp-1.4")
    assert (bullet.verdict, bullet.status) == ("stretch", "needs_user")
    decision = next(d for d in result.variant.decisions if d.bulletId == "b-exp-1.4")
    assert (decision.kind, decision.options) == ("stretch", ["keep", "soften", "drop"])
    with pytest.raises(ExportBlocked):
        await service.export_resume(result.variant, outcome.book)

    def settle(choice: str):  # type: ignore[no-untyped-def]
        return service.settle_resume(
            result.variant,
            outcome.book,
            outcome.posting,
            outcome.result,
            [DecisionChoice(bulletId="b-exp-1.4", choice=choice)],  # type: ignore[arg-type]
        )

    soft = next(b for b in all_bullets(settle("soften").variant) if b.id == "b-exp-1.4")
    assert soft.status == "verified"
    assert soft.verdict == "exact"
    assert soft.text.startswith("Manage a team")  # the person's own words again

    kept = next(b for b in all_bullets(settle("keep").variant) if b.id == "b-exp-1.4")
    assert (kept.status, kept.verdict) == (
        "verified",
        "stretch",
    )  # the person vouched for it, and it stays marked

    dropped = settle("drop").variant
    assert next(b for b in all_bullets(dropped) if b.id == "b-exp-1.4").status == "dropped"
    exported = await service.export_resume(dropped, outcome.book)
    from tayyari.match.lint import read_pdf

    assert "Led a team" not in read_pdf(exported.files.pdf).text


async def test_a_posting_term_no_fact_supports_never_reaches_the_resume() -> None:
    stuffed = {
        "b-exp-1.3": {
            "factIds": ["exp-1.3"],
            "text": "Ran 38 A/B tests on landing pages and checkout nudges using Segment and mParticle.",
            "verdict": "reframed",
        }
    }
    service, outcome, result, _ = await composed(meera(), **stuffed)
    bullet = next(b for b in all_bullets(result.variant) if b.id == "b-exp-1.3")
    assert bullet.status == "verified"
    assert "Segment" not in bullet.text and "mParticle" not in bullet.text
    assert bullet.text.startswith("Ran 38 A/B tests")  # the fact's own wording stands in
    assert any(
        (d.kind == "bullet" and "Segment" in d.reason) or "posting's word" in d.reason
        for d in result.report.dropped
    )
    exported = await service.export_resume(result.variant, outcome.book)
    from tayyari.match.lint import read_docx

    assert "Segment" not in read_docx(exported.files.docx).text


async def test_a_term_coverage_ties_to_the_fact_is_offered_to_the_person_not_slipped_in() -> None:
    reworded = {
        "b-exp-1.1": {
            "factIds": ["exp-1.1"],
            "text": "Led the lifecycle email and WhatsApp programme on Klaviyo for 400,000 subscribers.",
            "verdict": "reframed",
        }
    }
    service, outcome, result, _ = await composed(meera(), **reworded)
    # coverage cites exp-1.1 for the ESP requirement (Klaviyo, CleverTap or MoEngage): the term is the posting's word for it
    bullet = next(b for b in all_bullets(result.variant) if b.id == "b-exp-1.1")
    assert (bullet.verdict, bullet.status) == ("reframed", "needs_user")
    decision = next(d for d in result.variant.decisions if d.bulletId == "b-exp-1.1")
    assert decision.kind == "confirm_term"
    settled = service.settle_resume(
        result.variant,
        outcome.book,
        outcome.posting,
        outcome.result,
        [DecisionChoice(bulletId="b-exp-1.1", choice="keep_my_wording")],
    )
    mine = next(b for b in all_bullets(settled.variant) if b.id == "b-exp-1.1")
    assert "Klaviyo" not in mine.text
    assert mine.status == "verified"


async def test_a_failed_phrase_call_leaves_the_facts_own_wording_and_a_valid_resume() -> None:
    persona = meera()
    fake = script_pipeline(FakeClaudeClient(), persona)
    fake._queues.structured["match.phrase"] = []
    fake.script_structured("match.phrase", ClaudeOverloaded(), repeat=True)
    service, outcome, _ = await analysed(persona, fake)
    result = await service.compose_resume(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    variant = result.variant
    assert variant.summary.status == "dropped"  # nothing to say it from
    assert all(b.status == "verified" and b.verdict == "exact" for e in variant.experience for b in e.bullets)
    exported = await service.export_resume(variant, outcome.book)
    assert all(i.ok for i in exported.variant.lint.items)  # type: ignore[union-attr]


async def test_the_composed_resume_is_json_that_round_trips() -> None:
    from tayyari.contract.match import ResumeVariant

    _, _, result, _ = await composed(riya())
    again = ResumeVariant.from_dict(result.variant.to_dict())
    assert again == result.variant


# ───────────────────────── answers, languages and licences in the skills section ─────────────────────────


def _req(rid: str, kind: str, terms: list[str]) -> Any:
    from tayyari.contract.match import Requirement

    text = " and ".join(terms)
    return Requirement(
        id=rid, text=text, quote=text, tier="required", kind=kind, provenance="stated", terms=terms
    )


def _covered(rid: str, fact_ids: list[str]) -> Coverage:
    return Coverage(requirementId=rid, factIds=fact_ids, status="covered", level=3, note="Shown.")


def test_an_answer_names_the_postings_term_in_the_skills_never_the_answer_itself() -> None:
    from tayyari.match.facts import user_fact

    answer = user_fact(
        "Only basic. I made a small Power BI dashboard for the audit team.", key="m/q-r1", kind="skill"
    )
    book = FactBook(name="A B", facts=[answer])
    reqs = [_req("r1", "tool", ["Power BI"])]
    groups = build_skills(book, reqs, [_covered("r1", [answer.id])], Relevance(), Report())
    names = [item.name for g in groups for item in g.items]
    assert names == ["Power BI"]
    assert skill_violations(groups, book, reqs, [_covered("r1", [answer.id])]) == []


def test_an_answer_that_does_not_name_the_term_adds_nothing_to_the_skills() -> None:
    from tayyari.match.facts import user_fact

    answer = user_fact("I wrote the low level design and the team reviewed it.", key="m/q-r1", kind="skill")
    book = FactBook(name="A B", facts=[answer])
    groups = build_skills(
        book, [_req("r1", "skill", ["HLD"])], [_covered("r1", [answer.id])], Relevance(), Report()
    )
    assert groups == []


def test_a_resume_line_backs_the_languages_and_licences_it_names_word_for_word() -> None:
    languages = Fact(
        id="line-abc-41",
        text="Languages known: Malayalam, English, Hindi (little), Tamil (understand)",
        kind="other",
        refs=[FactRef(source="resume", docId="resume", lines=[41, 41], quote="Languages known: Malayalam")],
        evidenceTier=None,
    )
    licence = Fact(
        id="line-abc-6",
        text="Indian Nursing Council registered",
        kind="cert",
        refs=[
            FactRef(source="resume", docId="resume", lines=[6, 6], quote="Indian Nursing Council registered")
        ],
        evidenceTier=None,
    )
    book = FactBook(name="S K", facts=[languages, licence])
    reqs = [
        _req("r1", "language", ["Kannada", "Hindi", "Malayalam"]),
        _req("r2", "cert", ["Indian Nursing Council", "KNC"]),
    ]
    coverage = [_covered("r1", [languages.id]), _covered("r2", [licence.id])]
    groups = {
        g.group: [i.name for i in g.items] for g in build_skills(book, reqs, coverage, Relevance(), Report())
    }
    assert groups == {"Languages": ["Hindi", "Malayalam"], "Certifications": ["Indian Nursing Council"]}
    built = build_skills(book, reqs, coverage, Relevance(), Report())
    assert skill_violations(built, book, reqs, coverage) == []
    # an item the line does not name is not backed by it
    from tayyari.contract.match import SkillGroup, SkillItem

    forged = [
        SkillGroup(
            group="Languages", items=[SkillItem(name="Kannada", factIds=[languages.id], evidenceTier=None)]
        )
    ]
    assert skill_violations(forged, book, reqs, coverage) == ["Kannada"]


def test_an_answer_never_enters_other_skills_by_its_own_words_however_relevant() -> None:
    from tayyari.match.facts import user_fact

    answer = user_fact("I made a small Power BI dashboard of open audit queries.", key="m/q-r1", kind="skill")
    book = FactBook(name="A B", facts=[answer])
    groups = build_skills(book, [], [], Relevance(scores={answer.id: 100}), Report())
    assert groups == []
