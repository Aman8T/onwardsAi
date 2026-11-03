"""The cover note: 250 to 400 words, no generic openers, a plain sentence for every gap, logistics only from the person."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from tayyari.contract.match import COVER_WORDS_MAX, COVER_WORDS_MIN, Fact, Requirement
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.match.cover import (
    BANNED_OPENERS,
    GENERIC_CLOSE,
    LENGTH_LABEL,
    REASK_MAX_TOKENS,
    REFUSED_MARK,
    CoverResult,
    banned_opener,
    banned_phrase,
    fact_paragraph,
    first_person,
    gap_requirements,
    gap_sentence,
    is_logistics,
    pick_pairs,
    refusal_suffix,
    unfinished,
    voice_problem,
    write_cover,
)
from tayyari.match.errors import CoverUnavailable
from tayyari.match.facts import user_fact
from tayyari.match.lint import LABELS, failing, read_docx
from tayyari.match.service import MatchOutcome, MatchService
from tests.fakes.claude import FakeClaudeClient
from tests.unit.match.support import CTX, HAIKU, Persona, js, meera, riya, script_pipeline


def para(
    kind: str, text: str, facts: list[str] | None = None, reqs: list[str] | None = None
) -> dict[str, Any]:
    return {"factIds": facts or [], "requirementIds": reqs or [], "kind": kind, "text": text}


async def cover(
    persona: Persona,
    *answers: dict[str, Any],
    user_facts: tuple[Fact, ...] = (),
) -> tuple[CoverResult, FakeClaudeClient, MatchOutcome, MatchService]:
    fake = script_pipeline(FakeClaudeClient(), persona)
    service = MatchService(claude=fake, model=HAIKU)
    outcome = await service.analyse(
        profile=persona.profile,
        context=persona.context,
        posting_text=persona.posting_text,
        call_ctx=CTX,
        portfolio_facts=persona.portfolio_facts,
        user_facts=user_facts,
    )
    fake.script_structured("match.cover", *answers)
    result = await service.compose_cover(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    return result, fake, outcome, service


def kinds(result: CoverResult) -> list[str]:
    return [p.kind for p in result.note.paragraphs]


# ───────────────────────── the fixtures, end to end ─────────────────────────


@pytest.mark.parametrize("persona", [meera(), riya()], ids=["meera", "riya"])
async def test_a_good_note_is_in_range_cites_facts_and_has_no_generic_opener(persona: Persona) -> None:
    result, fake, outcome, service = await cover(persona, js(f"{persona.name}.cover.answer.json"))
    note = result.note
    assert COVER_WORDS_MIN <= note.wordCount <= COVER_WORDS_MAX
    assert result.calls == 1
    assert fake.calls_for("match.cover")[0].ctx.purpose == "match.cover"
    assert kinds(result)[-1] == "close"
    for p in note.paragraphs:
        assert banned_opener(p.text) is None
        assert banned_phrase(p.text) is None
        if p.kind in ("match", "logistics"):
            assert p.factIds
            assert all(outcome.book.get(f) is not None for f in p.factIds)
    exported = await service.export_cover(note, outcome.book, outcome.posting)
    assert exported.note.lint is not None
    assert failing(exported.note.lint) == []


async def test_the_greeting_and_signoff_are_added_by_the_exporter_not_counted_or_written_by_a_model() -> None:
    persona = meera()
    result, _, outcome, service = await cover(persona, js("meera.cover.answer.json"))
    exported = await service.export_cover(result.note, outcome.book, outcome.posting)
    text = read_docx(exported.files.docx).text
    assert text.splitlines()[0] == "Meera Nair"
    assert "Dear Lumen Retail hiring team," in text
    assert text.rstrip().endswith("Sincerely,\nMeera Nair")
    assert all(not p.text.startswith("Dear") for p in result.note.paragraphs)


# ───────────────────────── banned openers ─────────────────────────


@pytest.mark.parametrize(
    "opener",
    [
        "I am writing to apply for the Senior Growth Marketing Manager role at Lumen Retail.",
        "I am the perfect candidate for this role.",
        "To whom it may concern, I have a lot to offer.",
        "I saw your posting for a growth manager and wanted to reach out.",
        "I am writing to express my interest in the role.",
    ],
)
async def test_a_paragraph_with_a_generic_opener_is_never_kept(opener: str) -> None:
    good = js("meera.cover.answer.json")
    bad = {
        "paragraphs": [
            para("match", opener + " " + good["paragraphs"][0]["text"], ["exp-1", "exp-2"], ["r1"]),
            *good["paragraphs"][1:],
        ]
    }
    result, _, _, _ = await cover(meera(), bad, bad)
    assert all(banned_opener(p.text) is None for p in result.note.paragraphs)
    assert not any(p.text.startswith(opener) for p in result.note.paragraphs)
    assert ("paragraph", "generic opener or puffery") in {
        (d.kind, d.reason) for d in result.report.dropped
    } or any(d.reason == "generic opener or puffery" for d in result.report.dropped)


@pytest.mark.parametrize("opener", BANNED_OPENERS)
def test_every_banned_opener_is_recognised_whatever_the_case_or_quote_marks(opener: str) -> None:
    assert banned_opener(opener.upper() + " for the role.") == opener
    assert banned_opener("  “" + opener.capitalize() + "”") == opener


def test_ordinary_openers_and_puffery() -> None:
    assert banned_opener("At Brightwell Foods I led the lifecycle programme.") is None
    assert banned_phrase("I am a passionate, results-driven marketer.") is not None
    assert banned_phrase("I am the perfect fit.") is not None
    assert banned_phrase("I ran 38 A/B tests.") is None


# ───────────────────────── gaps ─────────────────────────


def with_coverage(persona: Persona, **changes: tuple[str, int, list[str]]) -> Persona:
    table = dict(persona.coverage)
    for prefix, row in changes.items():
        key = next(k for k in table if k.startswith(prefix.replace("_", " ")))
        table[key] = row
    return dataclasses.replace(persona, coverage=table)


async def test_gaps_stay_on_the_match_page_and_never_reach_the_note_even_when_the_model_writes_one() -> None:
    persona = with_coverage(meera(), **{"Lifecycle_marketing_on_email": ("missing_gap", 0, [])})
    good = js("meera.cover.answer.json")
    plain = para("gap", "I have not yet worked with a customer data platform such as Segment.", [], ["r7"])
    boastful = para(
        "gap", "I have extensive experience with customer data platforms such as Segment.", [], ["r7"]
    )
    answer = {
        "paragraphs": [*[p for p in good["paragraphs"] if "CleverTap" not in p["text"]], plain, boastful]
    }
    result, fake, outcome, _ = await cover(persona, answer, answer)
    # the gaps are real, and the match page shows them ...
    assert {g.id for g in gap_requirements(outcome.posting, outcome.result.coverage)} == {"r3", "r7"}
    # ... but the note is what the person can show: no gap paragraph, no "I have not", no gap term
    assert "gap" not in kinds(result)
    text = " ".join(p.text for p in result.note.paragraphs)
    assert "not yet" not in text and "extensive" not in text and "Segment" not in text
    assert "Not met" in fake.calls_for("match.cover")[0].user_text
    assert "(none: write no gap paragraph)" in fake.calls_for("match.cover")[0].user_text


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            "Meera holds a lifecycle programme. The resume records it as an accomplishment.",
            "talks about the resume, not the work",
        ),
        ("Meera led the lifecycle programme at Brightwell Foods.", "speaks of the person by name, not as I"),
        ("I led the lifecycle programme. I have not yet used Segment.", "says what the person has not done"),
        (
            "I write Java daily. My Go knowledge is limited and I am still learning it.",
            "says what the person has not done",
        ),
        (
            "I use SQL every week, so I would not claim it as a strength yet.",
            "says what the person has not done",
        ),
        ("At Brightwell Foods, handled annual budget of.", "a sentence is not finished"),
        ("I led the lifecycle programme and.", "a sentence is not finished"),
        ("I led the lifecycle email programme at Brightwell Foods for 400,000 subscribers.", None),
    ],
)
def test_a_paragraph_speaks_as_the_person_about_the_work_in_whole_sentences(
    text: str, reason: str | None
) -> None:
    assert voice_problem(text, "Meera Nair") == reason


def test_a_surname_that_is_also_the_employers_name_is_not_the_third_person() -> None:
    known = "Systems Engineer at Tata Consultancy Services"
    text = "At Tata Consultancy Services I moved six SOAP services to REST."
    assert voice_problem(text, "Ravi Tata", known) is None
    assert voice_problem("Ravi moved six SOAP services to REST.", "Ravi Tata", known) is not None


async def test_a_match_paragraph_in_the_third_person_is_refused_and_asked_for_again() -> None:
    good = js("meera.cover.answer.json")
    first = good["paragraphs"][0]
    third = dict(
        first, text=first["text"].replace("I am a", "Meera Nair is a", 1).replace(" I was ", " she was ")
    )
    result, fake, _, _ = await cover(
        meera(), {"paragraphs": [third, *good["paragraphs"][1:]]}, {"paragraphs": [first]}
    )
    assert result.calls == 2 and len(fake.calls_for("match.cover")) == 2
    assert [p.text for p in result.note.paragraphs] == [p["text"] for p in good["paragraphs"]]
    assert not any("Meera" in p.text for p in result.note.paragraphs)


async def test_there_is_no_gap_paragraph_when_nothing_must_or_required_is_missing() -> None:
    result, _, outcome, _ = await cover(riya(), js("riya.cover.answer.json"))
    assert gap_requirements(outcome.posting, outcome.result.coverage) == []
    assert "gap" not in kinds(result)


# ───────────────────────── logistics ─────────────────────────


LOGISTICS = {"paragraphs": []}  # filled per test


def logistic_answer(fact_ids: list[str]) -> dict[str, Any]:
    good = js("meera.cover.answer.json")
    keep = [p for p in good["paragraphs"] if p["kind"] != "close"]
    return {
        "paragraphs": [
            *keep,
            para("logistics", "I can relocate to Mumbai and join with a 60-day notice period.", fact_ids),
            *[p for p in good["paragraphs"] if p["kind"] == "close"],
        ]
    }


async def test_logistics_come_only_from_facts_the_person_typed() -> None:
    typed = user_fact(
        "I can relocate to Mumbai and join with a 60-day notice period.", key="q-g2", kind="other"
    )
    assert is_logistics(typed)
    result, fake, _, _ = await cover(meera(), logistic_answer([typed.id]), user_facts=(typed,))
    para_ = next(p for p in result.note.paragraphs if p.kind == "logistics")
    assert para_.factIds == [typed.id]
    assert "Logistics facts" in fake.calls_for("match.cover")[0].user_text
    assert typed.text in fake.calls_for("match.cover")[0].user_text
    assert kinds(result)[-2:] == ["logistics", "close"]


async def test_a_logistics_paragraph_without_a_user_fact_is_dropped() -> None:
    # nothing typed: the model must not invent a notice period or a willingness to relocate
    result, _, _, _ = await cover(meera(), logistic_answer(["exp-1"]), logistic_answer(["exp-1"]))
    assert "logistics" not in kinds(result)
    assert not any("relocate" in p.text for p in result.note.paragraphs)


async def test_a_logistics_paragraph_may_not_lean_on_a_document_fact_even_when_a_user_fact_exists() -> None:
    typed = user_fact("I can relocate to Mumbai and join with a 60-day notice period.", key="q-g2")
    leaning = logistic_answer([typed.id, "exp-1"])
    invented = {
        "paragraphs": [
            para(
                "logistics",
                "I can relocate to Mumbai and join with a 30-day notice period.",
                [typed.id, "exp-1"],
            )
        ]
    }
    result, fake, _, _ = await cover(meera(), leaning, invented, user_facts=(typed,))
    assert "logistics" not in kinds(
        result
    )  # refused, asked for again, refused again: the invented 30 days never stand
    repair = fake.calls_for("match.cover")[1].user_text
    assert (
        f"fact {typed.id} |" in repair and "fact exp-1 |" not in repair
    )  # the re-ask offers the typed fact only


async def test_a_refused_logistics_paragraph_is_asked_for_again_from_the_typed_fact_alone() -> None:
    typed = user_fact("I can relocate to Mumbai and join with a 60-day notice period.", key="q-g2")
    fixed = {
        "paragraphs": [
            para(
                "logistics",
                "I can relocate to Mumbai and join with a 60-day notice period.",
                [typed.id, "exp-1"],
            )
        ]
    }
    result, fake, _, _ = await cover(
        meera(), logistic_answer([typed.id, "exp-1"]), fixed, user_facts=(typed,)
    )
    logistics = next(p for p in result.note.paragraphs if p.kind == "logistics")
    assert logistics.factIds == [typed.id]  # the document fact it also named is not one it may cite
    assert kinds(result)[-2:] == ["logistics", "close"]
    assert len(fake.calls_for("match.cover")) == 2


async def test_a_user_fact_about_anything_else_is_not_logistics() -> None:
    other = user_fact("I led a pricing project at a previous job.", key="q-r5")
    assert not is_logistics(other)
    result, fake, _, _ = await cover(meera(), js("meera.cover.answer.json"), user_facts=(other,))
    assert "(none: write no logistics paragraph)" in fake.calls_for("match.cover")[0].user_text
    assert result.note.paragraphs


# ───────────────────────── what is kept and what is not ─────────────────────────


async def test_a_match_paragraph_needs_a_real_cited_fact_and_the_facts_numbers_and_names() -> None:
    good = js("meera.cover.answer.json")
    rows = [
        para(
            "match",
            "I tripled revenue to 5M dollars at Brightwell Foods and led a team of 40.",
            ["exp-1"],
            ["r1"],
        ),  # not in the facts
        para(
            "match", "I run lifecycle for Globex and Initech with Klaviyo.", ["exp-1.1"], ["r3"]
        ),  # employers and a term no fact has
        para(
            "match", "I led the lifecycle email programme for 400,000 subscribers.", [], ["r3"]
        ),  # cites nothing
        para(
            "match", "I led the lifecycle email programme for 400,000 subscribers.", ["made-up"], ["r3"]
        ),  # cites nothing real
        para(
            "match", "I owned the lifecycle email programme for 400,000 subscribers.", ["exp-2"], ["r3"]
        ),  # scope the fact does not state
    ]
    answer = {"paragraphs": [*rows, *good["paragraphs"]]}
    result, _, _, _ = await cover(meera(), answer, answer)
    texts = " ".join(p.text for p in result.note.paragraphs)
    for bad in ("tripled", "Globex", "Initech", "owned the lifecycle"):
        assert bad not in texts
    assert sum(1 for p in result.note.paragraphs if p.kind == "match") == 4
    assert result.calls == 1  # every place had a good paragraph: nothing to ask for again
    assert (
        len(
            [
                d
                for d in result.report.dropped
                if d.reason in ("not supported by the facts it cites", "a match paragraph cites no fact")
            ]
        )
        >= 5
    )


async def test_a_note_over_400_words_loses_its_last_match_paragraphs() -> None:
    filler = " ".join(["I would bring the same care to every channel and every report."] * 5)
    good = js("meera.cover.answer.json")
    long = {
        "paragraphs": [
            dict(p, text=p["text"] + " " + filler) if p["kind"] == "match" else p for p in good["paragraphs"]
        ]
    }
    result, _, _, _ = await cover(meera(), long)
    assert result.note.wordCount <= COVER_WORDS_MAX
    assert sum(1 for p in result.note.paragraphs if p.kind == "match") < 4
    assert kinds(result)[-1] == "close"
    assert any("word limit" in d.reason for d in result.report.dropped)


async def test_a_short_note_is_asked_for_once_more_and_then_returned_as_it_is() -> None:
    good = js("meera.cover.answer.json")
    short = {"paragraphs": [good["paragraphs"][1], good["paragraphs"][4]]}  # one match and the close
    result, fake, _, _ = await cover(meera(), short, good)
    assert result.calls == 2
    assert result.note.wordCount >= COVER_WORDS_MIN
    second = fake.calls_for("match.cover")[1].user_text
    assert "must be 250 to 400 words" in second

    still_short, _, outcome2, service2 = await cover(meera(), short, short)
    assert still_short.calls == 2
    assert still_short.note.wordCount < COVER_WORDS_MIN
    exported = await service2.export_cover(still_short.note, outcome2.book, outcome2.posting)
    flagged = [i for i in exported.note.lint.items if not i.ok]  # type: ignore[union-attr]
    assert [i.id for i in flagged] == ["length"]  # a short true note beats a padded one, but it is flagged


async def test_the_note_never_invents_a_closing_or_a_gap_it_was_not_given() -> None:
    good = js("meera.cover.answer.json")
    answer = {"paragraphs": [p for p in good["paragraphs"] if p["kind"] == "match"]}
    result, _, _, _ = await cover(meera(), answer, answer)
    last = result.note.paragraphs[-1]
    assert last.kind == "close"
    assert last.text == GENERIC_CLOSE
    assert last.factIds == []


async def test_no_supported_pair_means_no_note() -> None:
    persona = dataclasses.replace(meera(), coverage={})  # every row unknown
    fake = script_pipeline(FakeClaudeClient(), persona)
    service = MatchService(claude=fake, model=HAIKU)
    outcome = await service.analyse(
        profile=persona.profile,
        context=persona.context,
        posting_text=persona.posting_text,
        call_ctx=CTX,
        portfolio_facts=persona.portfolio_facts,
    )
    assert pick_pairs(outcome.posting, outcome.result.coverage, outcome.book) == []
    with pytest.raises(CoverUnavailable):
        await write_cover(
            fake,
            model=HAIKU,
            book=outcome.book,
            posting=outcome.posting,
            coverage=outcome.result.coverage,
            call_ctx=CTX,
        )
    assert fake.calls_for("match.cover") == []


def test_pairs_bring_the_job_a_bullet_happened_at() -> None:
    persona = meera()
    book = persona.book()
    from tayyari.match.posting import parse_posting
    from tests.unit.match.support import posting_answer

    posting = parse_posting(posting_answer(persona), persona.posting_text).posting
    coverage_rows = []
    from tayyari.contract.match import Coverage

    for r in posting.requirements:
        status, level, ids = next(
            (v for k, v in persona.coverage.items() if k in r.text or r.text.startswith(k)),
            ("unknown", 0, []),
        )
        coverage_rows.append(Coverage(requirementId=r.id, factIds=ids, status=status, level=level, note=""))  # type: ignore[arg-type]
    pairs = pick_pairs(posting, coverage_rows, book)
    assert 1 <= len(pairs) <= 4
    lifecycle = next(p for p in pairs if p.requirement.id == "r3")
    assert lifecycle.facts[0].id == "exp-1.1"
    assert "exp-1" in [f.id for f in lifecycle.facts]  # Brightwell Foods comes with the bullet


@pytest.mark.parametrize(
    ("resume_voice", "spoken"),
    [
        ("Led the lifecycle programme.", "I led the lifecycle programme."),
        ("Own the paid budget", "I own the paid budget."),
        ("Mentored 2 associates.", "I mentored 2 associates."),
        ("Built the weekly report", "I built the weekly report."),
        ("Weekly report for the founders.", None),
        ("motion-kit: spring physics", None),
    ],
)
def test_resume_voice_becomes_first_person_only_when_it_starts_with_a_verb(
    resume_voice: str, spoken: str | None
) -> None:
    assert first_person(resume_voice) == spoken


# ───────────────────────── one re-ask, then the fact's own words ─────────────────────────

UNSUPPORTED_R4 = "On experimentation, I ran 90 A/B tests and doubled site conversion."
FALLBACK_R4 = (
    "At Brightwell Foods, I ran 38 A/B tests on landing pages and checkout nudges; "
    "five winners shipped and added 6.4% to site conversion."
)


def with_unsupported(*indexes: int) -> dict[str, Any]:
    """The good note with the match paragraph(s) at ``indexes`` replaced by text its facts do not carry."""
    good = js("meera.cover.answer.json")
    return {
        "paragraphs": [
            dict(p, text=UNSUPPORTED_R4) if i in indexes else p for i, p in enumerate(good["paragraphs"])
        ]
    }


async def test_a_refused_paragraph_is_asked_for_once_more_alone_with_its_reason_and_its_facts() -> None:
    good = js("meera.cover.answer.json")
    fixed = {"paragraphs": [good["paragraphs"][3]]}
    result, fake, _, _ = await cover(meera(), with_unsupported(3), fixed)

    sent = fake.calls_for("match.cover")
    assert result.calls == 2 and len(sent) == 2  # the whole note, then ONE re-ask
    assert all(c.ctx.purpose == "match.cover" for c in sent)
    ask = sent[1].user_text
    assert "Paragraph 1 (match)" in ask and "Paragraph 2" not in ask  # only the refused one
    assert "Refused because: not supported by the facts it cites" in ask
    assert "requirement r4: A proven A/B testing" in ask
    for offered in (
        "exp-1.3",
        "skill-a-b-testing",
        "exp-1",
    ):  # the facts of that pair, and the job it happened at
        assert f"fact {offered} |" in ask
    for other in ("exp-1.1", "exp-1.2", "exp-2", "skill-clevertap", "skill-meta-ads"):
        assert f"fact {other} |" not in ask
    assert sent[1].max_tokens == REASK_MAX_TOKENS < sent[0].max_tokens
    assert "Facts you may cite (the only ones" in ask

    # the new paragraph sits where the refused one was; the other paragraphs are the model's first answer
    assert [p.text for p in result.note.paragraphs] == [p["text"] for p in good["paragraphs"]]
    assert COVER_WORDS_MIN <= result.note.wordCount <= COVER_WORDS_MAX
    assert result.note.lint is None
    assert any(d.reason == "not supported by the facts it cites" for d in result.report.dropped)


async def test_a_second_refusal_falls_back_to_the_facts_own_words_and_there_is_no_third_call() -> None:
    result, fake, _, _ = await cover(meera(), with_unsupported(3), with_unsupported(3))
    assert result.calls == 2 and len(fake.calls_for("match.cover")) == 2
    r4 = result.note.paragraphs[3]
    assert r4.text == FALLBACK_R4
    assert (r4.kind, r4.requirementIds, r4.factIds) == (
        "match",
        ["r4"],
        ["exp-1.3", "exp-1"],
    )  # cites what it says
    assert "90" not in " ".join(p.text for p in result.note.paragraphs)
    assert [p.kind for p in result.note.paragraphs] == ["match"] * 4 + ["close"]
    assert any(d.reason == "written from the fact itself" for d in result.report.dropped)


async def test_a_failed_reask_is_only_another_refusal() -> None:
    result, fake, _, _ = await cover(meera(), with_unsupported(3), ClaudeOverloaded())
    assert result.calls == 2 and len(fake.calls_for("match.cover")) == 2
    assert result.note.paragraphs[3].text == FALLBACK_R4


async def test_the_reask_answer_may_cite_only_the_facts_it_was_offered() -> None:
    # it cites a fact outside the pair and names another requirement: code decides both
    wrong = {
        "paragraphs": [
            para(
                "match",
                "At Brightwell Foods I led the lifecycle email and WhatsApp programme for 400,000 subscribers.",
                ["exp-1.1", "exp-1"],
                ["r2"],
            )
        ]
    }
    result, _, _, _ = await cover(meera(), with_unsupported(3), wrong)
    r4 = result.note.paragraphs[3]
    assert r4.text == FALLBACK_R4  # exp-1.1 was not on offer, so the lifecycle claim has no fact to rest on
    assert r4.requirementIds == ["r4"]


async def test_only_the_refused_paragraphs_are_asked_for_again_in_their_order() -> None:
    good = js("meera.cover.answer.json")
    fixed = {"paragraphs": [good["paragraphs"][1], good["paragraphs"][3]]}
    result, fake, _, _ = await cover(meera(), with_unsupported(1, 3), fixed)
    ask = fake.calls_for("match.cover")[1].user_text
    assert "Paragraph 1 (match)" in ask and "Paragraph 2 (match)" in ask and "Paragraph 3" not in ask
    assert ask.index("requirement r3") < ask.index("requirement r4")
    assert (
        ask.count("fact exp-1 |") == 2 and "fact exp-1.2 |" not in ask
    )  # the facts of each, not of the others
    assert [p.text for p in result.note.paragraphs] == [p["text"] for p in good["paragraphs"]]
    assert result.calls == 2


async def test_a_refusal_whose_place_is_filled_elsewhere_costs_no_second_call() -> None:
    good = js("meera.cover.answer.json")
    extra = para("match", UNSUPPORTED_R4, ["exp-1.3"], ["r4"])  # r4 also has its good paragraph below
    result, fake, _, _ = await cover(meera(), {"paragraphs": [extra, *good["paragraphs"]]})
    assert result.calls == 1 and len(fake.calls_for("match.cover")) == 1
    assert UNSUPPORTED_R4 not in " ".join(p.text for p in result.note.paragraphs)


async def test_a_short_note_with_nothing_to_repair_gets_the_whole_note_asked_once_more() -> None:
    good = js("meera.cover.answer.json")
    short = {"paragraphs": [good["paragraphs"][1], good["paragraphs"][4]]}  # one match and the close
    result, fake, _, _ = await cover(
        meera(), short, ClaudeOverloaded()
    )  # a failed retry keeps the first note
    assert result.calls == 2 and len(fake.calls_for("match.cover")) == 2
    assert [p.text for p in result.note.paragraphs] == [good["paragraphs"][i]["text"] for i in (1, 4)]
    assert "aim for 280 to 360" in fake.calls_for("match.cover")[1].user_text


# ───────────────────────── gap sentences ─────────────────────────


def requirement(rid: str, text: str, terms: list[str]) -> Requirement:
    return Requirement(
        id=rid, text=text, quote=text, tier="must", kind="skill", provenance="stated", terms=terms
    )


def test_a_gap_sentence_says_it_for_one_term_and_them_for_several_in_the_requirements_own_wording() -> None:
    cdp = requirement("r1", "Has worked with a customer data platform", ["customer data platform", "Segment"])
    sql = requirement("r2", "Familiarity with SQL", ["SQL."])
    tool = requirement("r3", "Experience with CleverTap", ["CleverTap"])
    assert gap_sentence([cdp]) == "The role lists customer data platform. I haven't used it yet."
    assert (
        gap_sentence([cdp, sql]) == "The role lists customer data platform and SQL. I haven't used them yet."
    )
    assert (
        gap_sentence([cdp, sql, tool])
        == "The role lists customer data platform, SQL and CleverTap. I haven't used them yet."
    )
    assert gap_sentence([cdp, cdp]) == gap_sentence([cdp])  # the same wording twice is one thing


def test_a_requirement_without_a_term_is_named_by_its_own_text_without_an_added_article_or_period() -> None:
    years = requirement("r1", "5+ years of growth marketing at a consumer brand.", [])
    fluent = requirement("r2", "Fluent in English and Hindi", [])
    acronym = requirement("r3", "SQL fluency", [])
    assert (
        gap_sentence([years])
        == "The role lists 5+ years of growth marketing at a consumer brand. I haven't used it yet."
    )
    assert gap_sentence([fluent]).startswith("The role lists fluent in English and Hindi. ")
    assert gap_sentence([acronym]).startswith("The role lists SQL fluency. ")  # an acronym keeps its capitals
    long = requirement("r4", "Owns " + "a very long requirement " * 8, [])
    assert len(gap_sentence([long])) < 140 and ".." not in gap_sentence([long])


# ───────────────────────── the fact's own words ─────────────────────────


def test_a_fact_paragraph_is_at_the_org_in_first_person_and_cites_the_fact_and_the_job() -> None:
    book = meera().book()
    r4 = requirement("r4", "A proven A/B testing practice", ["A/B testing"])
    facts = [book.get("exp-1.3"), book.get("skill-a-b-testing")]
    made = fact_paragraph([r4], [f for f in facts if f is not None], book)
    assert made is not None
    assert made.text == FALLBACK_R4
    assert made.factIds == ["exp-1.3", "exp-1"] and made.requirementIds == ["r4"] and made.kind == "match"


def test_a_fact_that_is_not_a_sentence_and_a_skill_name_give_no_paragraph() -> None:
    book = riya().book()
    ask = requirement("r1", "Owns a component library", ["component library"])
    react, motion = book.get("skill-react"), book.get("proj-1")
    assert react is not None and motion is not None
    # "motion-kit: Spring-physics animation primitives ..." is a label, not something the person did: no fragment
    assert fact_paragraph([ask], [react, motion], book) is None
    assert fact_paragraph([ask], [react], book) is None
    assert fact_paragraph([ask], [], book) is None


@pytest.mark.parametrize(
    "bullet",
    [
        "Handling patients on mechanical ventilator, BiPAP, CPAP and monitoring ABG reports.",
        "Redesigned the dispute status API and",
        "Handled annual budget of",
    ],
)
def test_a_bullet_that_does_not_make_a_whole_first_person_sentence_gives_no_paragraph(bullet: str) -> None:
    book = meera().book()
    exp = book.get("exp-1")
    assert exp is not None
    fact = Fact(id="exp-1.9", text=bullet, kind="accomplishment", refs=exp.refs, evidenceTier="used")
    ask = requirement("r1", "Critical care", ["critical care"])
    made = fact_paragraph([ask], [fact], book)
    assert made is None or unfinished(made.text) is None


def test_a_fact_paragraph_from_job_lines_alone_says_the_role_in_the_first_person() -> None:
    book = meera().book()
    years = requirement("r1", "5+ years of growth marketing", [])
    jobs = [book.get("exp-1"), book.get("exp-2"), book.get("exp-3")]
    made = fact_paragraph([years], [j for j in jobs if j is not None], book)
    assert made is not None
    assert made.text == (
        "I work as Growth Marketing Manager at Brightwell Foods. "
        "I worked as Performance Marketing Specialist at Pinecrest Media."
    )  # two jobs at most, a whole sentence each
    assert made.factIds == ["exp-1", "exp-2"]


# ───────────────────────── a note that stays short says why ─────────────────────────


async def test_a_short_note_names_the_paragraphs_that_were_refused_and_why_on_its_length_item() -> None:
    everything = with_unsupported(0, 1, 2, 3)
    result, fake, outcome, service = await cover(meera(), everything, everything)
    note = result.note
    assert result.calls == 2 and len(fake.calls_for("match.cover")) == 2
    assert note.wordCount < COVER_WORDS_MIN
    assert kinds(result)[-1] == "close" and "gap" not in kinds(result)  # fact-text paragraphs, then the close
    assert all(p.factIds and p.text != UNSUPPORTED_R4 for p in note.paragraphs if p.kind == "match")

    assert note.lint is not None
    [item] = note.lint.items
    assert (item.id, item.ok, item.label) == ("length", False, LENGTH_LABEL)
    assert item.detail.startswith(
        f"{note.wordCount} words; the note should be {COVER_WORDS_MIN} to {COVER_WORDS_MAX}."
    )
    assert REFUSED_MARK in item.detail and "not supported by the facts it cites" in item.detail
    assert item.detail.count("match (") == 1  # four paragraphs, one reason: named together
    assert len(item.detail) <= 240
    assert refusal_suffix(note) == item.detail[item.detail.index(REFUSED_MARK) :]

    # the exporter still flags the length (it checks the words again)
    exported = await service.export_cover(note, outcome.book, outcome.posting)
    assert exported.note.lint is not None
    assert [i.id for i in failing(exported.note.lint)] == ["length"]


async def test_a_note_that_is_long_enough_after_the_reask_carries_no_lint_note() -> None:
    result, _, _, _ = await cover(meera(), with_unsupported(3), with_unsupported(3))
    assert result.note.wordCount >= COVER_WORDS_MIN
    assert result.note.lint is None
    assert refusal_suffix(result.note) == ""


def test_the_length_label_is_the_one_the_exporters_lint_uses() -> None:
    assert LABELS["length"] == LENGTH_LABEL


# ───────────────────────── a paragraph that another already says ─────────────────────────


def test_a_paragraph_that_another_already_says_is_left_out_and_the_first_stays() -> None:
    from tayyari.contract.match import CoverParagraph
    from tayyari.match.cover import assemble
    from tayyari.match.models import Report

    full = CoverParagraph(
        text="I ran 38 A/B tests on landing pages and checkout nudges. Five winners shipped. That is my practice.",
        requirementIds=["r4"],
        factIds=["exp-1.3", "skill-a-b-testing"],
        kind="match",
    )
    again = CoverParagraph(
        text="At Brightwell Foods, I ran 38 A/B tests on landing pages and checkout nudges.",
        requirementIds=["r4"],
        factIds=["exp-1.3", "exp-1"],
        kind="match",
    )
    role = CoverParagraph(
        text="I work as Growth Marketing Manager at Brightwell Foods.",
        requirementIds=["r1"],
        factIds=["exp-1"],
        kind="match",
    )
    longer = CoverParagraph(
        text="As Growth Marketing Manager at Brightwell Foods I own the paid acquisition budget and the growth review.",
        requirementIds=["r1"],
        factIds=["exp-1", "exp-1.2"],
        kind="match",
    )
    report = Report()
    out = assemble([full, again, role, longer], pairs=[], gaps=[], book=meera().book(), report=report)
    assert [p.text for p in out if p.kind == "match"] == [full.text, longer.text]
    assert sum(d.reason == "says what another paragraph already says" for d in report.dropped) == 2
    twins = assemble([full, full], pairs=[], gaps=[], book=meera().book(), report=Report())
    assert [p.text for p in twins if p.kind == "match"] == [full.text]
