"""Evidence checks through the service: code first, Jev only for missing anchors, the drop rule, the budget and fail-open."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

from tayyari.core.limits import JEV_EVIDENCE_WINDOW_CHARS
from tayyari.decisions.packs import evidence as evidence_pack
from tayyari.decisions.service import EvidenceClaim
from tayyari.llm.errors import JevError
from tests.fakes.jev import choice_answer
from tests.unit.decisions.packs.support import CTX, PackHarness, relation_answer

Harness = Callable[..., PackHarness]

SUPPORT = "evidence.support"
SOURCE = (
    "Frontend Engineer, Kitebird (2022-2024). I rebuilt the checkout with React and TypeScript, "
    "and I own the Kite UI design system: 42 components used by 6 teams. "
    + "Other duties included planning. "
    * 30
)
SUPPORTED = "Rebuilt Kitebird checkout in React; owns 42-component Kite UI"
UNSUPPORTED = "Led the Stripe payments migration for 9000 stores"


def claim(
    text: str = UNSUPPORTED,
    *,
    skill: str = "react",
    source: str = "resume",
    source_text: str = SOURCE,
    reader: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
) -> EvidenceClaim:
    return EvidenceClaim(skill, source, text, source_text, reader, facts)


def relation(label: str, confidence: float) -> dict[str, Any]:
    return choice_answer(
        label, options=["supports", "contradicts", "says_nothing"], p=0.9, confidence=confidence
    )


# ───────────────────────── code settles it: no Jev call ─────────────────────────


async def test_claims_the_code_settles_never_reach_jev(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    claims = [
        claim(SUPPORTED),  # all anchors found
        claim("Rebuilt the checkout with React and TypeScript"),  # verbatim-ish: anchors found
        claim("worked on interesting problems"),  # nothing to check
        claim(UNSUPPORTED, source="scan", source_text=""),  # a scanned resume: no raw text
        claim(UNSUPPORTED, source_text="   "),
    ]
    verdicts = await h.service.check_evidence(claims, CTX)

    assert h.jev.calls == []
    assert [v.keep for v in verdicts] == [True] * 5
    assert [v.relation for v in verdicts] == [None] * 5
    assert verdicts[3].reason == "no source text to check against, kept"


async def test_the_verdicts_keep_the_order_and_identity_of_the_claims(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    claims = [
        claim(SUPPORTED, skill="react", source="resume"),
        claim(UNSUPPORTED, skill="stripe", source="github"),
        claim("worked on interesting problems", skill="x", source="notes"),
    ]
    verdicts = await h.service.check_evidence(claims, CTX)

    assert [(v.skill_id, v.source_id, v.text) for v in verdicts] == [
        ("react", "resume", SUPPORTED),
        ("stripe", "github", UNSUPPORTED),
        ("x", "notes", "worked on interesting problems"),
    ]
    assert [v.keep for v in verdicts] == [True, False, True]


async def test_no_claims_no_work(harness: Harness) -> None:
    h = harness({})
    assert await h.service.check_evidence([], CTX) == []
    assert h.jev.calls == []


async def test_the_reader_output_and_facts_rescue_a_claim_before_jev(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    reader = {"sub": "9000 stores", "skills": [{"name": "Stripe", "evidence": "Led the Stripe migration"}]}
    verdicts = await h.service.check_evidence([claim(UNSUPPORTED, reader=reader)], CTX)
    assert verdicts[0].keep and h.jev.calls == []


# ───────────────────────── the Jev stage and the drop rule ─────────────────────────


async def test_missing_anchors_plus_says_nothing_drops_the_claim(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing", 0.97)})
    (verdict,) = await h.service.check_evidence([claim()], CTX)

    assert verdict.keep is False
    assert verdict.relation == "says_nothing"
    assert "not found in the source" in verdict.reason and "says nothing" in verdict.reason
    assert (verdict.skill_id, verdict.source_id, verdict.text) == ("react", "resume", UNSUPPORTED)


async def test_missing_anchors_plus_contradicts_drops_the_claim(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("contradicts", 0.97)})
    (verdict,) = await h.service.check_evidence([claim()], CTX)
    assert verdict.keep is False and verdict.relation == "contradicts"


async def test_missing_anchors_but_jev_says_supports_keeps_the_claim(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("supports", 0.97)})
    (verdict,) = await h.service.check_evidence([claim()], CTX)
    assert verdict.keep is True and verdict.relation == "supports"


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
    (verdict,) = await h.service.check_evidence([claim()], CTX)
    assert verdict.keep is keep


@pytest.mark.parametrize("label", ["says_nothing", "contradicts"])
@pytest.mark.parametrize("confidence", [0.79, 0.5, 0.2])
async def test_an_unsure_jev_never_drops_a_claim_without_a_fallback(
    harness: Harness, label: str, confidence: float
) -> None:
    h = harness({SUPPORT: relation(label, confidence)}, with_claude=False)
    (verdict,) = await h.service.check_evidence([claim()], CTX)
    assert verdict.keep is True
    assert "gave no answer" in verdict.reason


async def test_an_unsure_jev_asks_claude_and_a_sure_claude_can_drop(harness: Harness) -> None:
    h = harness({SUPPORT: relation("says_nothing", 0.4)})
    h.claude.script_structured("decision_fallback", {"choice": "says_nothing"})  # type: ignore[union-attr]
    (verdict,) = await h.service.check_evidence([claim()], CTX)

    assert verdict.keep is False and verdict.relation == "says_nothing"
    assert len(h.fallback_calls) == 1
    prompt = h.fallback_calls[0].user_text
    assert UNSUPPORTED in prompt and "says_nothing" in prompt


async def test_a_claude_fallback_that_says_supports_keeps_the_claim(harness: Harness) -> None:
    h = harness({SUPPORT: relation("says_nothing", 0.4)})
    h.claude.script_structured("decision_fallback", {"choice": "supports"})  # type: ignore[union-attr]
    (verdict,) = await h.service.check_evidence([claim()], CTX)
    assert verdict.keep is True and verdict.relation == "supports"


async def test_the_question_carries_the_claim_and_the_best_window(harness: Harness) -> None:
    filler = "Quarterly planning notes about budgets and hiring. " * 400
    source = filler + "At Stripe I led the payments migration for 9000 stores. " + filler
    h = harness({SUPPORT: relation_answer("supports")})
    text = f"{UNSUPPORTED} at Initech"  # Initech is nowhere in the source, so the code cannot settle it
    await h.service.check_evidence([claim(text, source_text=source)], CTX)

    call = h.jev.calls[0]
    assert call.question_ids == [SUPPORT]
    state = call.state
    assert isinstance(state, dict) and set(state) == {"claim", "section"}
    assert state["claim"] == text
    assert len(state["section"]) <= JEV_EVIDENCE_WINDOW_CHARS
    assert "Stripe" in state["section"] and "9000" in state["section"]
    wire = call.questions[SUPPORT].to_wire()
    assert wire["type"] == "choice" and list(wire["criteria"]) == ["supports", "contradicts", "says_nothing"]
    assert call.timeout_s == 4.0


async def test_each_unsettled_claim_is_its_own_question_and_settled_ones_are_not(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    texts = ["Led the Stripe migration", "Ran the Oracle rollout", "Wrote Haskell compilers"]
    claims = [claim(t, skill=f"s{i}") for i, t in enumerate(texts)] + [claim(SUPPORTED, skill="ok")]
    verdicts = await h.service.check_evidence(claims, CTX)

    assert len(h.jev.calls) == 3
    assert {c.state["claim"] for c in h.jev.calls} == set(texts)  # type: ignore[index]
    assert [v.keep for v in verdicts] == [False, False, False, True]


async def test_the_same_claim_on_the_same_source_is_asked_once(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    claims = [
        claim(UNSUPPORTED, skill="a"),
        claim(UNSUPPORTED, skill="b"),
        claim(UNSUPPORTED, skill="c", source="github"),
    ]
    verdicts = await h.service.check_evidence(claims, CTX)

    assert len(h.jev.calls) == 2  # (resume, claim) once, (github, claim) once
    assert [v.keep for v in verdicts] == [False, False, False]
    assert [v.skill_id for v in verdicts] == ["a", "b", "c"]


async def test_the_decision_log_gets_a_row_per_question(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing", 0.97)})
    await h.service.check_evidence([claim()], CTX)
    await h.drain()

    (row,) = h.rows
    assert row.spec_id == SUPPORT and row.pack == "evidence" and row.mode == "primary"
    assert row.used is True and row.threshold == 0.8
    assert row.extra["value"] == "says_nothing"


# ───────────────────────── fail open ─────────────────────────


@pytest.mark.parametrize(
    "kind", ["timeout", "connection", "server", "rate_limit", "auth", "invalid_response"]
)
async def test_a_jev_error_keeps_every_claim(harness: Harness, kind: str) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")}, with_claude=False)
    h.jev.fail_with(JevError(kind))  # type: ignore[arg-type]
    verdicts = await h.service.check_evidence([claim(), claim("Ran the Oracle rollout")], CTX)
    assert [v.keep for v in verdicts] == [True, True]
    assert all("gave no answer" in v.reason for v in verdicts)


async def test_a_spend_trip_keeps_every_claim(harness: Harness) -> None:
    from tests.unit.decisions.support import SpendTrip

    h = harness({SUPPORT: relation_answer("says_nothing")}, spend=SpendTrip())
    assert [v.keep for v in await h.service.check_evidence([claim()], CTX)] == [True]
    assert h.jev.calls == []


async def test_jev_disabled_without_claude_keeps_the_claims(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")}, jev_enabled=False, with_claude=False)
    assert [v.keep for v in await h.service.check_evidence([claim()], CTX)] == [True]


async def test_a_slow_jev_is_cut_off_by_the_overall_budget_and_everything_is_kept(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")}, slow_jev_s=5.0)
    h.service._evidence_budget_s = 0.1
    started = time.perf_counter()
    verdicts = await h.service.check_evidence([claim(), claim("Ran the Oracle rollout")], CTX)

    assert time.perf_counter() - started < 2.0
    assert [v.keep for v in verdicts] == [True, True]


async def test_what_finished_before_the_budget_counts_and_the_rest_is_kept(harness: Harness) -> None:
    """Ten questions are asked in waves of eight: the first wave finishes in time, the second is cut off."""
    h = harness({SUPPORT: relation_answer("says_nothing")}, slow_jev_s=0.3)
    h.service._evidence_budget_s = 0.5
    claims = [claim(f"Led the Project{n} migration", skill=f"s{n}") for n in range(10)]
    verdicts = await h.service.check_evidence(claims, CTX)

    assert [v.keep for v in verdicts] == [False] * 8 + [True] * 2
    assert verdicts[9].reason.endswith("kept")


async def test_a_crash_in_the_code_stage_keeps_the_claim(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(evidence_pack, "code_check", boom)
    verdicts = await h.service.check_evidence([claim(), claim(SUPPORTED)], CTX)
    assert [v.keep for v in verdicts] == [True, True]
    assert verdicts[0].reason == "the check did not finish, kept"


async def test_a_crash_while_asking_keeps_every_claim(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(h.runner, "run_many", boom)
    assert [v.keep for v in await h.service.check_evidence([claim()], CTX)] == [True]


# ───────────────────────── odd inputs ─────────────────────────


async def test_hostile_claim_text_is_checked_in_linear_time(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("supports")})
    hostile = ["a-" * 40_000, "1," * 40_000, "A" * 80_000, "x.y." * 20_000, "Ab" * 40_000, "-" * 80_000]
    started = time.perf_counter()
    verdicts = await h.service.check_evidence([claim(t, skill=str(i)) for i, t in enumerate(hostile)], CTX)
    assert time.perf_counter() - started < 10.0
    assert len(verdicts) == len(hostile)


async def test_a_claim_in_another_script_is_kept_without_anchors(harness: Harness) -> None:
    h = harness({SUPPORT: relation_answer("says_nothing")})
    verdicts = await h.service.check_evidence([claim("負責公司的結帳系統重構")], CTX)
    assert verdicts[0].keep is True and h.jev.calls == []


async def test_the_sample_profile_claims_are_checked_against_a_matching_source(
    harness: Harness, sample_profile: Any
) -> None:
    resume = (
        "Kitebird: rebuilt the checkout in React. Owns Kite UI, 42 components. "
        "motion-kit: 212 stars, 31 test files. Built design-tokens. " * 3
    )
    h = harness({SUPPORT: relation_answer("says_nothing")})
    claims = [
        EvidenceClaim(skill.id, ev.sourceId, ev.text, resume, None, None)
        for skill in sample_profile.skills
        for ev in skill.evidence
        if ev.sourceId == "resume"
    ]
    assert claims
    verdicts = await h.service.check_evidence(claims, CTX)
    assert len(verdicts) == len(claims)
    kept = [v for v in verdicts if v.keep]
    assert any("Kitebird" in v.text for v in kept)
