"""Portfolio copy checks through the service: every sentence is asked, the drop rule, the budget and fail-open."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from tayyari.core.limits import JEV_EVIDENCE_WINDOW_CHARS
from tayyari.decisions.packs import evidence as evidence_pack
from tayyari.decisions.service import CopyClaim, DecisionContext
from tayyari.llm.errors import JevError
from tayyari.llm.jev import JevQuestion, JsonContent
from tests.fakes.jev import choice_answer
from tests.unit.decisions.packs.support import CTX, PackHarness, relation_answer

Harness = Callable[..., PackHarness]

SUPPORT = "evidence.support"
CORPUS = (
    "Rebuilt the checkout with React and TypeScript at Kitebird. "
    "Owns the Kite UI design system: 42 components used by 6 teams."
)
SUPPORTED = "Rebuilt the checkout with React and TypeScript at Kitebird."
UNSUPPORTED = "Led the Stripe payments migration for 9000 stores."


def claim(sentence: str = UNSUPPORTED, *, slot: str = "aboutLede", corpus: str = CORPUS) -> CopyClaim:
    return CopyClaim(slot, sentence, corpus)


def relation(label: str, confidence: float) -> dict[str, Any]:
    return choice_answer(
        label, options=["supports", "contradicts", "says_nothing"], p=0.9, confidence=confidence
    )


def by_sentence(answers: Mapping[str, dict[str, Any]], default: dict[str, Any]) -> Any:
    """An answer script that answers each question by the sentence in its state."""

    def script(state: JsonContent, questions: Mapping[str, JevQuestion]) -> dict[str, dict[str, Any]]:
        assert isinstance(state, dict)
        return {SUPPORT: answers.get(str(state["claim"]), default)}

    return script


# ───────────────────────── the verdicts ─────────────────────────


async def test_a_supported_sentence_is_kept(harness: Harness) -> None:
    h = harness({SUPPORT: relation("supports", 0.97)})
    (verdict,) = await h.service.check_copy([claim(SUPPORTED, slot="exp:exp-1")], CTX)

    assert verdict.keep is True and verdict.relation == "supports"
    assert verdict.confidence == pytest.approx(0.97)
    assert (verdict.slot_id, verdict.sentence) == ("exp:exp-1", SUPPORTED)


async def test_a_contradicted_sentence_is_dropped(harness: Harness) -> None:
    h = harness({SUPPORT: relation("contradicts", 0.97)})
    (verdict,) = await h.service.check_copy([claim()], CTX)

    assert verdict.keep is False and verdict.relation == "contradicts"
    assert verdict.confidence == pytest.approx(0.97)
    assert "contradict" in verdict.reason
    assert (verdict.slot_id, verdict.sentence) == ("aboutLede", UNSUPPORTED)


async def test_a_sentence_the_corpus_says_nothing_about_is_dropped_when_sure(harness: Harness) -> None:
    h = harness({SUPPORT: relation("says_nothing", 0.97)})
    (verdict,) = await h.service.check_copy([claim()], CTX)

    assert verdict.keep is False and verdict.relation == "says_nothing"
    assert "say nothing" in verdict.reason


@pytest.mark.parametrize(
    ("label", "confidence", "keep"),
    [
        ("says_nothing", 1.0, False),
        ("says_nothing", 0.8, False),  # the bar is inclusive
        ("contradicts", 0.8, False),
        ("contradicts", 0.95, False),
        ("supports", 0.95, True),
        ("supports", 0.8, True),
    ],
)
async def test_the_drop_rule_table(harness: Harness, label: str, confidence: float, keep: bool) -> None:
    h = harness({SUPPORT: relation(label, confidence)})
    (verdict,) = await h.service.check_copy([claim()], CTX)
    assert verdict.keep is keep


@pytest.mark.parametrize("label", ["says_nothing", "contradicts"])
@pytest.mark.parametrize("confidence", [0.79, 0.5, 0.2])
async def test_an_unsure_jev_never_drops_a_sentence_without_a_fallback(
    harness: Harness, label: str, confidence: float
) -> None:
    h = harness({SUPPORT: relation(label, confidence)}, with_claude=False)
    (verdict,) = await h.service.check_copy([claim()], CTX)
    assert verdict.keep is True and verdict.relation is None
    assert "gave no answer" in verdict.reason


async def test_an_unsure_jev_asks_claude_and_a_sure_claude_can_drop(harness: Harness) -> None:
    h = harness({SUPPORT: relation("says_nothing", 0.4)})
    h.claude.script_structured("decision_fallback", {"choice": "says_nothing"})  # type: ignore[union-attr]
    (verdict,) = await h.service.check_copy([claim()], CTX)

    assert verdict.keep is False and verdict.relation == "says_nothing"
    assert len(h.fallback_calls) == 1
    prompt = h.fallback_calls[0].user_text
    assert UNSUPPORTED in prompt and "says_nothing" in prompt


async def test_a_claude_fallback_that_says_supports_keeps_the_sentence(harness: Harness) -> None:
    h = harness({SUPPORT: relation("says_nothing", 0.4)})
    h.claude.script_structured("decision_fallback", {"choice": "supports"})  # type: ignore[union-attr]
    (verdict,) = await h.service.check_copy([claim()], CTX)
    assert verdict.keep is True and verdict.relation == "supports"


# ───────────────────────── every sentence is asked ─────────────────────────


async def test_every_sentence_is_asked_even_one_taken_word_for_word_from_the_corpus(harness: Harness) -> None:
    """No anchor short-cut (unlike evidence): a verbatim sentence, one without names and one with all its names
    in the corpus each still go to Jev, and a sure `contradicts` drops them."""
    h = harness({SUPPORT: relation("contradicts", 0.97)})
    sentences = [SUPPORTED, "worked on interesting problems", "Kitebird used React."]
    verdicts = await h.service.check_copy([claim(s) for s in sentences], CTX)

    assert len(h.jev.calls) == 3
    assert {c.state["claim"] for c in h.jev.calls} == set(sentences)  # type: ignore[index]
    assert [v.keep for v in verdicts] == [False, False, False]


async def test_the_question_is_the_evidence_question_over_the_best_window_of_the_corpus(
    harness: Harness,
) -> None:
    filler = "Quarterly planning notes about budgets and hiring. " * 400
    corpus = filler + "At Stripe I led the payments migration for 9000 stores. " + filler
    h = harness({SUPPORT: relation_answer("supports")})
    await h.service.check_copy([claim(UNSUPPORTED, corpus=corpus)], CTX)

    call = h.jev.calls[0]
    assert call.question_ids == [SUPPORT]
    state = call.state
    assert isinstance(state, dict) and set(state) == {"claim", "section"}
    assert state["claim"] == UNSUPPORTED
    assert len(state["section"]) <= JEV_EVIDENCE_WINDOW_CHARS
    assert "Stripe" in state["section"] and "9000" in state["section"]
    wire = call.questions[SUPPORT].to_wire()
    assert wire["type"] == "choice" and list(wire["criteria"]) == ["supports", "contradicts", "says_nothing"]
    assert call.timeout_s == 4.0


async def test_a_short_corpus_is_sent_whole(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("supports")})
    await h.service.check_copy([claim(SUPPORTED)], CTX)
    assert h.jev.calls[0].state["section"] == CORPUS  # type: ignore[index]


# ───────────────────────── batching ─────────────────────────


async def test_many_sentences_are_batched_and_the_verdicts_keep_their_order(harness: Harness) -> None:
    sentences = [f"Sentence number {n} about the Project{n} migration." for n in range(30)]
    dropped = {sentences[n] for n in (0, 7, 8, 9, 17, 29)}  # spans the waves of CONCURRENCY
    h = harness(
        by_sentence({s: relation("says_nothing", 0.97) for s in dropped}, default=relation("supports", 0.97))
    )
    claims = [claim(s, slot=f"proj:p{n % 6}") for n, s in enumerate(sentences)]
    verdicts = await h.service.check_copy(claims, CTX)

    assert len(h.jev.calls) == 30
    assert [v.sentence for v in verdicts] == sentences
    assert [v.slot_id for v in verdicts] == [c.slot_id for c in claims]
    assert {v.sentence for v in verdicts if not v.keep} == dropped
    assert all(v.relation == "supports" for v in verdicts if v.keep)


async def test_the_same_sentence_on_the_same_corpus_is_asked_once(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing", 0.97)})
    claims = [
        claim(UNSUPPORTED, slot="aboutLede"),
        claim(UNSUPPORTED, slot="tagline"),
        claim(UNSUPPORTED, slot="tagline", corpus=CORPUS + " More text about something else."),
    ]
    verdicts = await h.service.check_copy(claims, CTX)

    assert len(h.jev.calls) == 2  # (sentence, corpus) once, (sentence, other corpus) once
    assert [v.keep for v in verdicts] == [False, False, False]
    assert [v.slot_id for v in verdicts] == ["aboutLede", "tagline", "tagline"]


async def test_no_sentences_is_no_call(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    assert await h.service.check_copy([], CTX) == []
    assert h.jev.calls == []


@pytest.mark.parametrize(("sentence", "corpus"), [("   ", CORPUS), (UNSUPPORTED, ""), (UNSUPPORTED, "  \n ")])
async def test_an_empty_sentence_or_corpus_is_kept_without_asking(
    harness: Harness, sentence: str, corpus: str
) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    verdicts = await h.service.check_copy([claim(sentence, corpus=corpus), claim(UNSUPPORTED)], CTX)

    assert [v.keep for v in verdicts] == [True, False]
    assert verdicts[0].relation is None and verdicts[0].reason.endswith("kept")
    assert len(h.jev.calls) == 1


# ───────────────────────── the log ─────────────────────────


async def test_the_decision_log_gets_a_row_per_question_tagged_portfolio(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing", 0.97)})
    await h.service.check_copy([claim(), claim("Ran the Oracle rollout.")], CTX)
    await h.drain()

    assert len(h.rows) == 2
    for row in h.rows:
        assert row.spec_id == SUPPORT and row.pack == "evidence" and row.mode == "primary"
        assert row.used is True and row.threshold == 0.8
        assert row.extra["value"] == "says_nothing"
        assert row.extra["purpose_tag"] == "portfolio"


async def test_the_callers_context_is_kept_apart_from_the_purpose_tag(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("supports")})
    ctx = DecisionContext("profile-9", None, True, "req-9", purpose_tag="somethingelse")
    await h.service.check_copy([claim()], ctx)
    await h.drain()

    (row,) = h.rows
    assert row.extra["purpose_tag"] == "portfolio"
    assert (row.profile_id, row.is_sample) == ("profile-9", True)
    assert ctx.purpose_tag == "somethingelse"  # the caller's object is untouched


# ───────────────────────── fail open ─────────────────────────


@pytest.mark.parametrize(
    "kind", ["timeout", "connection", "server", "rate_limit", "auth", "invalid_response"]
)
async def test_a_jev_error_keeps_every_sentence(harness: Harness, kind: str) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")}, with_claude=False)
    h.jev.fail_with(JevError(kind))  # type: ignore[arg-type]
    verdicts = await h.service.check_copy([claim(), claim("Ran the Oracle rollout.")], CTX)
    assert [v.keep for v in verdicts] == [True, True]
    assert all("gave no answer" in v.reason for v in verdicts)


async def test_a_jev_error_and_a_failing_claude_keep_every_sentence(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})  # the fake Claude has nothing scripted: it raises
    h.jev.fail_with(JevError("server"))
    assert [v.keep for v in await h.service.check_copy([claim()], CTX)] == [True]


async def test_an_open_breaker_keeps_every_sentence(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")}, with_claude=False, breaker_failures=1)
    h.jev.fail_with(JevError("server"))
    assert [v.keep for v in await h.service.check_copy([claim()], CTX)] == [True]
    assert h.breaker.state == "open"

    h.jev.fail_with(None)
    calls_before = len(h.jev.calls)
    assert [v.keep for v in await h.service.check_copy([claim()], CTX)] == [True]
    assert len(h.jev.calls) == calls_before  # the open breaker sent nothing


async def test_a_spend_trip_keeps_every_sentence(harness: Harness) -> None:
    from tests.unit.decisions.support import SpendTrip

    h = harness({SUPPORT: relation_answer("says_nothing")}, spend=SpendTrip())
    assert [v.keep for v in await h.service.check_copy([claim()], CTX)] == [True]
    assert h.jev.calls == []


async def test_jev_disabled_without_claude_keeps_the_sentences(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")}, jev_enabled=False, with_claude=False)
    assert [v.keep for v in await h.service.check_copy([claim()], CTX)] == [True]


async def test_the_budget_is_the_evidence_timeout_setting(harness: Harness) -> None:
    assert harness({}).service._copy_budget_s == pytest.approx(4.0)
    tight = harness({}, jev_settings={"timeout_evidence_s": 2.0}).service
    assert tight._copy_budget_s == pytest.approx(2.0)


async def test_a_slow_jev_is_cut_off_by_the_overall_budget_and_everything_is_kept(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")}, slow_jev_s=5.0)
    h.service._copy_budget_s = 0.1
    started = time.perf_counter()
    verdicts = await h.service.check_copy([claim(), claim("Ran the Oracle rollout.")], CTX)

    assert time.perf_counter() - started < 2.0
    assert [v.keep for v in verdicts] == [True, True]


async def test_the_copy_budget_is_independent_of_the_evidence_budget(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")}, slow_jev_s=0.3)
    h.service._evidence_budget_s = 0.01
    verdicts = await h.service.check_copy([claim()], CTX)
    assert [v.keep for v in verdicts] == [False]


async def test_what_finished_before_the_budget_counts_and_the_rest_is_kept(harness: Harness) -> None:
    """Ten questions are asked in waves of eight: the first wave finishes in time, the second is cut off."""
    h = harness({SUPPORT: relation_answer("says_nothing")}, slow_jev_s=0.3)
    h.service._copy_budget_s = 0.5
    claims = [claim(f"Led the Project{n} migration.", slot=f"proj:p{n}") for n in range(10)]
    verdicts = await h.service.check_copy(claims, CTX)

    assert [v.keep for v in verdicts] == [False] * 8 + [True] * 2
    assert verdicts[9].reason.endswith("kept")


async def test_a_crash_building_one_question_keeps_only_that_sentence(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    real = evidence_pack.copy_question

    def flaky(c: CopyClaim) -> Any:
        if c.sentence == "Ran the Oracle rollout.":
            raise RuntimeError("bug")
        return real(c)

    monkeypatch.setattr(evidence_pack, "copy_question", flaky)
    verdicts = await h.service.check_copy(
        [claim(), claim("Ran the Oracle rollout."), claim("Wrote Haskell.")], CTX
    )
    assert [v.keep for v in verdicts] == [False, True, False]
    assert verdicts[1].reason == "the check did not finish, kept"


async def test_a_crash_while_asking_keeps_every_sentence(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(h.runner, "run_many", boom)
    assert [v.keep for v in await h.service.check_copy([claim()], CTX)] == [True]


async def test_cancellation_propagates(harness: Harness) -> None:
    h = harness({}, slow_jev_s=30.0, with_claude=False)
    task = asyncio.ensure_future(h.service.check_copy([claim()], CTX))
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ───────────────────────── odd inputs ─────────────────────────


async def test_hostile_sentences_are_checked_in_linear_time(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("supports")})
    hostile = ["a-" * 40_000, "1," * 40_000, "A" * 80_000, "x.y." * 20_000, "Ab" * 40_000, "-" * 80_000]
    started = time.perf_counter()
    verdicts = await h.service.check_copy([claim(t, slot=str(i)) for i, t in enumerate(hostile)], CTX)
    assert time.perf_counter() - started < 10.0
    assert len(verdicts) == len(hostile)


async def test_a_sentence_in_another_script_is_still_asked(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("supports")})
    (verdict,) = await h.service.check_copy([claim("負責公司的結帳系統重構", corpus="結帳系統重構")], CTX)
    assert verdict.keep is True and verdict.relation == "supports"
    assert len(h.jev.calls) == 1
