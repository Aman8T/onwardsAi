"""The triage pack: questions, state, the skip rules (Jev confidence versus code agreement), labels, notes and failure."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tayyari.core.limits import JEV_TRIAGE_HEAD_CHARS, JEV_TRIAGE_TAIL_CHARS
from tayyari.decisions.packs.triage import (
    ABOUT_PERSON_ID,
    MAINLY_ENGLISH_ID,
    NOT_PERSON_NOTE,
    PAGE_LABELS,
    PAGE_TYPE_ID,
    PAGE_TYPE_OPTIONS,
    SHORT_TEXT_CHARS,
    SKIP_REASONS,
    TEXT_QUALITY_ID,
    TRIAGE_PACK,
    WALL_SCAN_MAX_CHARS,
    build_state,
    code_agrees_unreadable,
    spec_ids_to_ask,
    split_text,
)
from tayyari.decisions.service import TriageResult
from tayyari.llm.errors import JevError
from tests.fakes.jev import choice_answer
from tests.unit.decisions.packs.support import (
    CTX,
    PackHarness,
    long_text,
    page_input,
    triage_answers,
)

Harness = Callable[..., PackHarness]

QUALITIES = ["readable", "garbled_or_code_dominated", "login_or_block_wall", "empty_or_placeholder"]
WALL_TEXT = "Sign in to continue. " + long_text(900)
CLEAN = long_text(2000)


def quality(verdict: str, confidence: float) -> dict[str, Any]:
    """A text_quality answer with an exact normalised confidence."""
    return choice_answer(verdict, options=QUALITIES, p=0.9, confidence=confidence)


def answers_with(verdict: str, confidence: float, **kwargs: Any) -> dict[str, Any]:
    script = triage_answers(**kwargs)
    script[TEXT_QUALITY_ID] = quality(verdict, confidence)
    return script


def claude_says(**by_key: Any) -> Callable[[Any], dict[str, Any]]:
    """A scripted Claude fallback: ``choice``-shaped questions answer with ``choice``, nouls with ``noul``."""

    def answer(call: Any) -> dict[str, Any]:
        props = call.schema["properties"]
        if "choice" in props:
            return {"choice": by_key["choice"]}
        if "answer" in props:
            return {"answer": by_key["noul"], "confidence": "high"}
        return {"level": 0}

    return answer


# ───────────────────────── question set and state ─────────────────────────


def test_the_pack_asks_the_agreed_questions() -> None:
    assert [s.id for s in TRIAGE_PACK.specs] == [
        TEXT_QUALITY_ID,
        ABOUT_PERSON_ID,
        MAINLY_ENGLISH_ID,
        PAGE_TYPE_ID,
    ]
    kinds = {s.id: s.question.kind for s in TRIAGE_PACK.specs}
    assert kinds == {
        TEXT_QUALITY_ID: "choice",
        ABOUT_PERSON_ID: "noul",
        MAINLY_ENGLISH_ID: "noul",
        PAGE_TYPE_ID: "choice",
    }
    assert TRIAGE_PACK.spec(TEXT_QUALITY_ID).question.option_labels == tuple(QUALITIES)
    assert len(TRIAGE_PACK.spec(PAGE_TYPE_ID).question.option_labels) == 9
    assert set(PAGE_LABELS) == set(PAGE_TYPE_OPTIONS) - {"other"}
    assert {s.version for s in TRIAGE_PACK.specs} == {"1"}


def test_which_questions_are_asked_depends_on_the_kind() -> None:
    assert spec_ids_to_ask("link") == [TEXT_QUALITY_ID, ABOUT_PERSON_ID, MAINLY_ENGLISH_ID, PAGE_TYPE_ID]
    assert spec_ids_to_ask("portfolio") == spec_ids_to_ask("link")
    for kind in ("github", "linkedin", "lovable"):
        assert PAGE_TYPE_ID not in spec_ids_to_ask(kind)
        assert TEXT_QUALITY_ID in spec_ids_to_ask(kind)
    assert spec_ids_to_ask("resume") == [MAINLY_ENGLISH_ID]
    assert spec_ids_to_ask("notes") == [MAINLY_ENGLISH_ID]


def test_the_state_is_the_kind_the_host_the_title_and_the_head_and_tail() -> None:
    state = build_state(page_input("Hello world", kind="portfolio", host="me.dev", title="Me"))
    assert state == {
        "source_kind": "portfolio",
        "host": "me.dev",
        "title": "Me",
        "text_head": "Hello world",
        "text_tail": "",
    }
    bare = build_state(page_input("x", host=None, title=None))
    assert bare["host"] == "" and bare["title"] == ""


def test_head_and_tail_never_overlap() -> None:
    short = "a" * 100
    assert split_text(short) == (short, "")

    mid = "a" * JEV_TRIAGE_HEAD_CHARS + "b" * 300
    first, last = split_text(mid)
    assert first == "a" * JEV_TRIAGE_HEAD_CHARS and last == "b" * 300

    long = "h" * JEV_TRIAGE_HEAD_CHARS + "m" * 9000 + "t" * JEV_TRIAGE_TAIL_CHARS
    first, last = split_text(long)
    assert len(first) == JEV_TRIAGE_HEAD_CHARS and set(first) == {"h"}
    assert len(last) == JEV_TRIAGE_TAIL_CHARS and set(last) == {"t"}


def test_a_very_long_title_is_clipped() -> None:
    assert len(build_state(page_input("x", title="T" * 5000))["title"]) == 300


# ───────────────────────── the code's opinion ─────────────────────────


@pytest.mark.parametrize(
    ("text", "agrees"),
    [
        ("", True),
        ("Coming soon", True),
        ("x" * (SHORT_TEXT_CHARS - 1), True),
        (long_text(SHORT_TEXT_CHARS), False),
        ("Please log in to view this profile. " + long_text(900), True),
        ("Verify you are human " + long_text(900), True),
        ("Just a moment... " + long_text(900), True),
        ("Access denied. " + long_text(900), True),
        ("Create a free account to continue. " + long_text(900), True),
        (long_text(900) + " Page not found", True),
        (long_text(2000) + " you can sign in on my site too", False),
        ("Sign in to continue " + long_text(WALL_SCAN_MAX_CHARS + 500), False),
        (long_text(3000), False),
    ],
)
def test_the_code_agrees_with_short_pages_and_wall_patterns(text: str, agrees: bool) -> None:
    assert code_agrees_unreadable(text) is agrees


# ───────────────────────── the skip rules ─────────────────────────


@pytest.mark.parametrize("verdict", ["login_or_block_wall", "empty_or_placeholder"])
async def test_a_very_sure_verdict_skips_even_when_the_code_sees_nothing(
    harness: Harness, verdict: str
) -> None:
    h = harness(answers_with(verdict, 0.95))
    result = await h.service.triage(page_input(CLEAN), CTX)

    assert result.skip is True
    assert result.text_quality == verdict
    assert result.skip_reason == SKIP_REASONS[verdict]
    assert result.label is None


@pytest.mark.parametrize("verdict", ["login_or_block_wall", "empty_or_placeholder"])
async def test_a_less_sure_verdict_skips_only_when_the_code_agrees(harness: Harness, verdict: str) -> None:
    clean = await harness(answers_with(verdict, 0.7)).service.triage(page_input(CLEAN), CTX)
    assert clean.skip is False and clean.skip_reason is None
    assert clean.text_quality == verdict  # still reported: the label is usable at 0.6

    short = await harness(answers_with(verdict, 0.7)).service.triage(page_input("Coming soon. " * 5), CTX)
    assert short.skip is True

    walled = await harness(answers_with(verdict, 0.7)).service.triage(page_input(WALL_TEXT), CTX)
    assert walled.skip is True


async def test_the_ninety_percent_bar_is_inclusive(harness: Harness) -> None:
    assert (
        await harness(answers_with("login_or_block_wall", 0.9)).service.triage(page_input(CLEAN), CTX)
    ).skip
    assert not (
        await harness(answers_with("login_or_block_wall", 0.89)).service.triage(page_input(CLEAN), CTX)
    ).skip


@pytest.mark.parametrize("verdict", ["readable", "garbled_or_code_dominated"])
async def test_other_verdicts_never_skip(harness: Harness, verdict: str) -> None:
    result = await harness(answers_with(verdict, 0.99)).service.triage(page_input("short"), CTX)
    assert result.skip is False
    assert result.text_quality == verdict


async def test_a_long_page_with_a_sign_in_link_is_not_a_wall_for_the_code(harness: Harness) -> None:
    text = "Sign in to continue " + long_text(WALL_SCAN_MAX_CHARS + 500)
    result = await harness(answers_with("login_or_block_wall", 0.75)).service.triage(page_input(text), CTX)
    assert result.skip is False


@pytest.mark.parametrize("kind", ["resume", "notes"])
async def test_the_persons_own_text_is_never_skipped(harness: Harness, kind: str) -> None:
    h = harness(answers_with("login_or_block_wall", 0.99))
    result = await h.service.triage(page_input("short text", kind=kind, host=None), CTX)

    assert result.skip is False and result.label is None
    assert h.jev.calls[0].question_ids == [MAINLY_ENGLISH_ID]  # only the language is looked at


@pytest.mark.parametrize("kind", ["resume", "link", "portfolio", "github", "linkedin"])
async def test_a_source_with_no_text_asks_nothing(harness: Harness, kind: str) -> None:
    """A scanned resume arrives with empty text (and a PDF): there is nothing to judge and nothing to skip."""
    h = harness(answers_with("empty_or_placeholder", 0.99))
    assert await h.service.triage(page_input("", kind=kind), CTX) == TriageResult()
    assert await h.service.triage(page_input("  \n ", kind=kind), CTX) == TriageResult()
    assert h.jev.calls == []


async def test_the_claude_fallback_counts_as_a_confident_label_but_not_as_ninety_percent(
    harness: Harness,
) -> None:
    """Jev is unsure (0.4 < 0.6), so Claude answers at 0.85: usable for a label, not enough to skip alone."""
    answers = answers_with("login_or_block_wall", 0.4)

    h = harness(answers)
    h.claude.script_structured("decision_fallback", claude_says(choice="login_or_block_wall"), repeat=True)  # type: ignore[union-attr]
    on_clean = await h.service.triage(page_input(CLEAN), CTX)
    assert on_clean.skip is False and on_clean.text_quality == "login_or_block_wall"
    assert len(h.fallback_calls) == 1

    h = harness(answers)
    h.claude.script_structured("decision_fallback", claude_says(choice="login_or_block_wall"), repeat=True)  # type: ignore[union-attr]
    on_short = await h.service.triage(page_input("Log in to see this. " * 5), CTX)
    assert on_short.skip is True


async def test_jev_error_with_a_claude_wall_verdict_follows_the_same_rules(harness: Harness) -> None:
    h = harness(answers_with("readable", 0.9))
    h.jev.fail_next(JevError("server"))
    h.claude.script_structured(
        "decision_fallback", claude_says(choice="empty_or_placeholder", noul=True), repeat=True
    )  # type: ignore[union-attr]
    assert (await h.service.triage(page_input("Nothing here yet. " * 5), CTX)).skip is True

    h = harness(answers_with("readable", 0.9))
    h.jev.fail_next(JevError("server"))
    h.claude.script_structured(
        "decision_fallback", claude_says(choice="empty_or_placeholder", noul=True), repeat=True
    )  # type: ignore[union-attr]
    assert (await h.service.triage(page_input(CLEAN), CTX)).skip is False


# ───────────────────────── labels and notes ─────────────────────────


@pytest.mark.parametrize(("page_type", "label"), sorted(PAGE_LABELS.items()))
async def test_page_type_relabels_a_link(harness: Harness, page_type: str, label: str) -> None:
    result = await harness(triage_answers(page_type=page_type)).service.triage(page_input(CLEAN), CTX)
    assert result.label == label and result.skip is False


async def test_the_labels_match_the_agreed_vocabulary() -> None:
    assert {
        PAGE_LABELS[k] for k in ("blog_or_articles", "publication_or_talk", "portfolio_or_case_studies")
    } == {
        "Blog",
        "Publication",
        "Portfolio",
    }
    assert PAGE_LABELS["product_or_app_page"] == "Product" and PAGE_LABELS["profile_page"] == "Profile"
    assert PAGE_LABELS["code_or_package_page"] == "Code"


async def test_other_and_unsure_page_types_leave_the_label_alone(harness: Harness) -> None:
    other = await harness(triage_answers(page_type="other")).service.triage(page_input(CLEAN), CTX)
    assert other.label is None

    # Jev unsure (0.2 < the 0.5 label bar) and no Claude to ask: no label, no error
    unsure = await harness(triage_answers(page_type_p=0.3), with_claude=False).service.triage(
        page_input(CLEAN), CTX
    )
    assert unsure.label is None


async def test_page_type_is_only_asked_for_links_and_portfolios(harness: Harness) -> None:
    for kind, asked in (("link", True), ("portfolio", True), ("github", False), ("linkedin", False)):
        h = harness(triage_answers())
        result = await h.service.triage(page_input(CLEAN, kind=kind), CTX)
        assert (PAGE_TYPE_ID in h.jev.calls[0].question_ids) is asked
        assert (result.label is not None) is asked


async def test_not_about_a_person_adds_a_note_and_a_log_line_but_never_skips(harness: Harness) -> None:
    h = harness(triage_answers(about_person=0.03), with_claude=False)
    result = await h.service.triage(page_input(CLEAN, host="docs.example.org"), CTX)

    assert result.skip is False
    assert result.notes == [NOT_PERSON_NOTE]
    assert result.log_lines == ["docs.example.org does not look like personal work, reading it anyway"]


async def test_a_person_page_has_no_note(harness: Harness) -> None:
    result = await harness(triage_answers(about_person=0.97)).service.triage(page_input(CLEAN), CTX)
    assert result.notes == [] and result.log_lines == []


async def test_not_english_is_a_log_line_only(harness: Harness) -> None:
    result = await harness(triage_answers(english=0.03)).service.triage(
        page_input(CLEAN, host="blog.example.fr"), CTX
    )

    assert result.english is False and result.skip is False
    assert result.notes == []
    assert result.log_lines == ["blog.example.fr is mostly not in English, reading it anyway"]


async def test_english_is_reported_when_sure(harness: Harness) -> None:
    assert (
        await harness(triage_answers(english=0.97)).service.triage(page_input(CLEAN), CTX)
    ).english is True
    unsure = await harness(triage_answers(english=0.6), with_claude=False).service.triage(
        page_input(CLEAN), CTX
    )
    assert unsure.english is None


async def test_a_skip_has_one_log_line_and_no_notes(harness: Harness) -> None:
    result = await harness(answers_with("login_or_block_wall", 0.95)).service.triage(page_input(CLEAN), CTX)
    assert result.log_lines == ["example.com sits behind a login or bot check, skipping it"]
    assert result.notes == []
    empty = await harness(answers_with("empty_or_placeholder", 0.95)).service.triage(page_input(CLEAN), CTX)
    assert empty.log_lines == ["example.com has no real content, skipping it"]


async def test_the_log_line_names_the_kind_when_there_is_no_host(harness: Harness) -> None:
    result = await harness(answers_with("login_or_block_wall", 0.95)).service.triage(
        page_input(CLEAN, host=None), CTX
    )
    assert result.log_lines == ["This link sits behind a login or bot check, skipping it"]


# ───────────────────────── logging and failure ─────────────────────────


async def test_the_decision_log_records_the_text_quality_row(harness: Harness) -> None:
    h = harness(answers_with("login_or_block_wall", 0.95))
    await h.service.triage(page_input(CLEAN), CTX)
    await h.drain()

    rows = {r.spec_id: r for r in h.rows}
    assert set(rows) == {TEXT_QUALITY_ID, ABOUT_PERSON_ID, MAINLY_ENGLISH_ID, PAGE_TYPE_ID}
    row = rows[TEXT_QUALITY_ID]
    assert row.pack == "triage" and row.mode == "primary" and row.used is True
    assert row.threshold == 0.6 and row.jev_confidence == pytest.approx(0.95)


async def test_the_state_sent_to_jev_has_the_head_and_the_tail(harness: Harness) -> None:
    h = harness(triage_answers())
    text = "H" * JEV_TRIAGE_HEAD_CHARS + "M" * 5000 + "T" * 200
    await h.service.triage(page_input(text), CTX)
    state = h.jev.calls[0].state
    assert state["text_head"] == "H" * JEV_TRIAGE_HEAD_CHARS  # type: ignore[index]
    assert state["text_tail"] == "M" * (JEV_TRIAGE_TAIL_CHARS - 200) + "T" * 200  # type: ignore[index]


@pytest.mark.parametrize("kind", ["timeout", "connection", "server", "rate_limit", "invalid_response"])
async def test_a_jev_error_without_claude_leaves_the_source_alone(harness: Harness, kind: str) -> None:
    h = harness(answers_with("login_or_block_wall", 0.99), with_claude=False)
    h.jev.fail_next(JevError(kind))  # type: ignore[arg-type]
    assert await h.service.triage(page_input("short"), CTX) == TriageResult()


async def test_the_overall_budget_cuts_a_slow_jev_off(harness: Harness) -> None:
    h = harness(answers_with("login_or_block_wall", 0.99), slow_jev_s=5.0)
    h.service._triage_budget_s = 0.05
    assert await h.service.triage(page_input("short"), CTX) == TriageResult()


async def test_a_breaker_that_is_open_leaves_the_source_alone(harness: Harness) -> None:
    h = harness(answers_with("login_or_block_wall", 0.99), with_claude=False, breaker_failures=1)
    h.jev.fail_next(JevError("server"))
    await h.service.triage(page_input("short"), CTX)  # opens the breaker
    calls_before = len(h.jev.calls)
    assert await h.service.triage(page_input("short"), CTX) == TriageResult()
    assert len(h.jev.calls) == calls_before  # not asked again while open
