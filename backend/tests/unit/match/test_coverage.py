"""Coverage: downgrades in code, batches run together, and band, counts and confidence computed from the rows."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from tayyari.contract.match import Coverage, Fact, FactRef, GateAnswer, Requirement
from tayyari.llm.errors import ClaudeOverloaded, SpendCeilingReached
from tayyari.match.coverage import (
    assess_coverage,
    band_of,
    confidence_of,
    count_statuses,
    validate_coverage,
    validate_row,
    weighted_score,
)
from tayyari.match.schemas import CoverageRowOut
from tayyari.prompts.match import YEARS_RULE
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.unit.match.support import CTX, HAIKU


def req(rid: str, tier: str = "required", text: str = "Some requirement") -> Requirement:
    return Requirement(
        id=rid,
        text=text,
        quote=text,
        tier=tier,
        kind="skill",
        provenance="stated",
        terms=[],  # type: ignore[arg-type]
    )


def fact(fid: str, source: str = "resume", tier: str | None = "used", kind: str = "accomplishment") -> Fact:
    return Fact(
        id=fid,
        text=f"Fact {fid}",
        kind=kind,  # type: ignore[arg-type]
        refs=[FactRef(source=source, docId=source, lines=[1, 1], quote=f"Fact {fid}")],  # type: ignore[arg-type]
        evidenceTier=tier,  # type: ignore[arg-type]
    )


BOOK = {
    "f1": fact("f1"),
    "f2": fact("f2"),
    "gh": fact("gh", source="github", tier="artifact"),
    "both": Fact(
        id="both",
        text="Fact both",
        kind="accomplishment",
        refs=[
            FactRef(source="resume", docId="resume", lines=[1, 1], quote="Fact both"),
            FactRef(source="github", docId="github", lines=[1, 1], quote="Fact both"),
        ],
        evidenceTier="artifact",
    ),
    "listed": fact("listed", tier="listed", kind="skill"),
}


def row(
    status: str, level: int = 3, fact_ids: list[str] | None = None, risk: str = "", fix: str = ""
) -> CoverageRowOut:
    return CoverageRowOut(
        requirementId="r1",
        factIds=fact_ids if fact_ids is not None else ["f1"],
        status=status,  # type: ignore[arg-type]
        level=level,
        note="n",
        interviewRisk=risk,
        mitigation=fix,
    )


def check(r: CoverageRowOut, requirement: Requirement | None = None) -> Coverage:
    return validate_row(r, requirement or req("r1"), BOOK)


# ───────────────────────── downgrades ─────────────────────────


@pytest.mark.parametrize("status", ["covered", "partial", "synonym_only", "missing_have_it"])
def test_a_non_gap_status_without_a_valid_fact_id_becomes_unknown(status: str) -> None:
    for facts in ([], ["no-such-fact"], ["nope", "nada"]):
        got = check(row(status, 4, facts))
        assert got.status == "unknown"
        assert got.factIds == []
        assert got.level == 0
        assert "no cited fact" in got.note


@pytest.mark.parametrize("status", ["covered", "partial", "synonym_only"])
def test_a_non_gap_status_with_a_valid_fact_id_stands(status: str) -> None:
    got = check(row(status, 3, ["f1", "unknown-id", "f1"]))
    assert got.status == status
    assert got.factIds == ["f1"]


@pytest.mark.parametrize("status", ["missing_gap", "unknown"])
def test_gap_and_unknown_need_no_fact(status: str) -> None:
    got = check(row(status, 4, []))
    assert got.status == status
    assert got.level == 0


def test_missing_have_it_needs_a_fact_that_is_not_the_resume() -> None:
    assert check(row("missing_have_it", 3, ["gh"])).status == "missing_have_it"
    assert check(row("missing_have_it", 3, ["f1", "gh"])).status == "missing_have_it"
    resume_only = check(row("missing_have_it", 3, ["f1"]))
    assert resume_only.status == "unknown"
    assert "no fact outside the resume" in resume_only.note
    # a fact the resume also says is not "something the resume lacks"
    assert check(row("missing_have_it", 3, ["both"])).status == "unknown"


@pytest.mark.parametrize(
    ("status", "given", "expected"),
    [
        ("covered", 0, 3),
        ("covered", 4, 4),
        ("covered", 9, 4),
        ("synonym_only", 4, 3),
        ("synonym_only", 0, 2),
        ("partial", 4, 3),
        ("partial", 0, 1),
        ("missing_have_it", 4, 3),
        ("missing_gap", 4, 0),
        ("unknown", 3, 0),
    ],
)
def test_the_level_is_pulled_into_the_range_of_its_status(status: str, given: int, expected: int) -> None:
    fact_ids = (
        ["gh"]
        if status == "missing_have_it"
        else (["f1"] if status not in ("missing_gap", "unknown") else [])
    )
    assert check(row(status, max(given, 0), fact_ids)).level == expected


def test_a_requirement_only_listed_in_a_skills_line_cannot_be_covered() -> None:
    got = check(row("covered", 4, ["listed"]))
    assert got.status == "partial"
    assert got.level <= 2
    assert "skills list" in got.note
    mixed = check(row("covered", 4, ["listed", "f1"]))  # one fact in use is enough
    assert mixed.status == "covered"
    assert check(row("synonym_only", 3, ["listed"])).level == 2


def test_interview_risk_and_mitigation_are_kept_only_for_unmet_must_and_required_rows() -> None:
    advice = {"risk": "They will probe it.", "fix": "Build a small example."}
    must_gap = check(row("missing_gap", 0, [], **advice), req("r1", "must"))
    assert (must_gap.interviewRisk, must_gap.mitigation) == ("They will probe it.", "Build a small example.")
    assert check(row("covered", 4, ["f1"], **advice), req("r1", "must")).interviewRisk is None
    assert check(row("missing_gap", 0, [], **advice), req("r1", "nice")).interviewRisk is None
    assert (
        check(row("partial", 2, ["f1"], **advice), req("r1", "required")).mitigation
        == "Build a small example."
    )
    assert check(row("missing_gap", 0, [], risk="", fix=""), req("r1", "must")).interviewRisk is None


def test_every_requirement_gets_exactly_one_row_and_a_skipped_one_is_unknown() -> None:
    rows = [
        CoverageRowOut(requirementId="r2", factIds=["f1"], status="covered", level=4, note="a"),
        CoverageRowOut(
            requirementId="r2", factIds=[], status="missing_gap", level=0, note="b"
        ),  # repeat: ignored
        CoverageRowOut(
            requirementId="zzz", factIds=["f1"], status="covered", level=4, note="c"
        ),  # unknown id
    ]
    got = validate_coverage(rows, [req("r1"), req("r2")], BOOK)
    assert [(c.requirementId, c.status) for c in got] == [("r1", "unknown"), ("r2", "covered")]


# ───────────────────────── batches ─────────────────────────


def many(n: int) -> list[Requirement]:
    return [req(f"r{i}", "required", f"Requirement number {i}") for i in range(1, n + 1)]


def echo_gaps(call: StructuredCall) -> dict[str, Any]:
    ids = call.schema["properties"]["rows"]["items"]["properties"]["requirementId"]["enum"]
    return {
        "rows": [
            {
                "requirementId": i,
                "factIds": [],
                "status": "missing_gap",
                "level": 0,
                "note": "-",
                "interviewRisk": "",
                "mitigation": "",
            }
            for i in ids
        ]
    }


async def test_requirements_are_checked_in_concurrent_batches_of_ten_in_order() -> None:
    fake = FakeClaudeClient()
    inflight = peak = 0

    async def respond(call: StructuredCall) -> dict[str, Any]:
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.01)
        inflight -= 1
        return echo_gaps(call)

    fake.script_structured("match.coverage", respond, repeat=True)
    seen: list[int] = []
    rows = await assess_coverage(
        fake,
        model=HAIKU,
        requirements=many(23),
        facts=list(BOOK.values()),
        call_ctx=CTX,
        on_batch=lambda batch: seen.append(len(batch)),
    )
    assert len(fake.structured_calls) == 3
    assert all(c.ctx.purpose == "match.coverage" for c in fake.structured_calls)
    sizes = sorted(
        len(c.schema["properties"]["rows"]["items"]["properties"]["requirementId"]["enum"])
        for c in fake.structured_calls
    )
    assert sizes == [3, 10, 10]
    assert peak >= 2  # the batches ran together
    assert [r.requirementId for r in rows] == [f"r{i}" for i in range(1, 24)]
    assert sorted(seen) == [3, 10, 10]


async def test_a_failed_batch_leaves_its_rows_unknown_and_the_rest_intact() -> None:
    fake = FakeClaudeClient()
    fake.script_structured(
        "match.coverage",
        ClaudeOverloaded(),
        when=lambda c: (
            "r11" in c.schema["properties"]["rows"]["items"]["properties"]["requirementId"]["enum"]
        ),
    )
    fake.script_structured("match.coverage", echo_gaps, repeat=True)
    rows = await assess_coverage(
        fake, model=HAIKU, requirements=many(12), facts=list(BOOK.values()), call_ctx=CTX
    )
    assert [r.status for r in rows[:10]] == ["missing_gap"] * 10
    assert [r.status for r in rows[10:]] == ["unknown", "unknown"]
    assert "did not run" in rows[10].note


async def test_the_spend_ceiling_is_never_swallowed() -> None:
    fake = FakeClaudeClient().script_structured("match.coverage", SpendCeilingReached())
    with pytest.raises(SpendCeilingReached):
        await assess_coverage(
            fake, model=HAIKU, requirements=many(2), facts=list(BOOK.values()), call_ctx=CTX
        )


async def test_the_prompt_lists_the_facts_and_the_schema_cites_before_it_judges() -> None:
    fake = FakeClaudeClient().script_structured("match.coverage", echo_gaps)
    await assess_coverage(fake, model=HAIKU, requirements=many(2), facts=list(BOOK.values()), call_ctx=CTX)
    call = fake.structured_calls[0]
    props = call.schema["properties"]["rows"]["items"]["properties"]
    names = list(props)
    assert names.index("factIds") < names.index("status") < names.index("level")
    assert set(props["factIds"]["items"]["enum"]) == set(BOOK)
    assert "<facts>" in call.user_text
    assert "<requirements>" in call.user_text
    assert call.effort is None


# ───────────────────────── counts, band, confidence ─────────────────────────


def cov(rid: str, status: str, level: int, fact_ids: list[str] | None = None) -> Coverage:
    return Coverage(
        requirementId=rid,
        factIds=fact_ids
        if fact_ids is not None
        else ([] if status in ("missing_gap", "unknown") else ["f1"]),
        status=status,  # type: ignore[arg-type]
        level=level,
        note="",
    )


def test_counts_are_per_status() -> None:
    rows = [
        cov("a", "covered", 4),
        cov("b", "covered", 3),
        cov("c", "partial", 2),
        cov("d", "missing_gap", 0),
        cov("e", "unknown", 0),
        cov("f", "missing_have_it", 3),
        cov("g", "synonym_only", 2),
    ]
    counts = count_statuses(rows)
    assert counts.model_dump() == {
        "covered": 2,
        "partial": 1,
        "synonym_only": 1,
        "missing_have_it": 1,
        "missing_gap": 1,
        "unknown": 1,
    }


def test_the_score_is_the_tier_weighted_mean_of_levels() -> None:
    reqs = [req("a", "must"), req("b", "required"), req("c", "preferred"), req("d", "nice")]
    rows = [
        cov("a", "covered", 4),
        cov("b", "partial", 2),
        cov("c", "covered", 3),
        cov("d", "missing_gap", 0),
    ]
    expected = (1.0 * 1.0 + 0.8 * 0.5 + 0.5 * 0.75 + 0.25 * 0.0) / (1.0 + 0.8 + 0.5 + 0.25)
    assert weighted_score(reqs, rows) == pytest.approx(expected)


def test_a_missing_must_weighs_more_than_a_missing_nice_to_have() -> None:
    must_gap = weighted_score(
        [req("a", "must"), req("b", "nice")], [cov("a", "missing_gap", 0), cov("b", "covered", 4)]
    )
    nice_gap = weighted_score(
        [req("a", "must"), req("b", "nice")], [cov("a", "covered", 4), cov("b", "missing_gap", 0)]
    )
    assert must_gap == pytest.approx(0.25 / 1.25)
    assert nice_gap == pytest.approx(1.0 / 1.25)


def test_unknown_rows_are_left_out_of_the_score() -> None:
    reqs = [req("a", "must"), req("b", "must")]
    assert weighted_score(reqs, [cov("a", "covered", 4), cov("b", "unknown", 0)]) == pytest.approx(1.0)
    assert weighted_score(reqs, [cov("a", "unknown", 0), cov("b", "unknown", 0)]) is None


def sums(levels: list[int], tier: str = "required") -> tuple[list[Requirement], list[Coverage]]:
    reqs = [req(f"r{i}", tier) for i in range(len(levels))]
    rows = [
        cov(f"r{i}", "covered" if lv >= 3 else "partial" if lv else "missing_gap", lv)
        for i, lv in enumerate(levels)
    ]
    return reqs, rows


@pytest.mark.parametrize(
    ("levels", "band"),
    [
        ([4, 4, 4, 4, 4], "strong"),
        ([4, 4, 4, 2, 2], "strong"),  # 16/20 = 0.80: the boundary is inclusive
        ([4, 4, 3, 2, 2], "good"),  # 15/20 = 0.75
        ([3, 3, 2, 2, 2], "good"),  # 12/20 = 0.60
        ([3, 2, 2, 2, 2], "stretch"),  # 11/20 = 0.55
        ([2, 2, 1, 1, 1], "stretch"),  # 7/20 = 0.35
        ([1, 1, 1, 1, 2], "weak"),  # 6/20 = 0.30
        ([0, 0, 0, 0, 0], "weak"),
    ],
)
def test_band_thresholds_with_equal_weights(levels: list[int], band: str) -> None:
    reqs, rows = sums(levels, "required")
    assert band_of(reqs, rows) == band


def test_a_missing_must_caps_the_band_even_when_everything_else_is_perfect() -> None:
    reqs = [req(f"m{i}", "must") for i in range(1, 9)] + [req("x", "must")]
    one_gap = [cov(f"m{i}", "covered", 4) for i in range(1, 9)] + [cov("x", "missing_gap", 0)]
    assert weighted_score(reqs, one_gap) == pytest.approx(8 / 9)  # a raw 0.89 would read "strong"
    assert band_of(reqs, one_gap) == "good"
    reqs2 = [req(f"m{i}", "must") for i in range(1, 11)] + [req("x", "must"), req("y", "must")]
    two_gaps = [cov(f"m{i}", "covered", 4) for i in range(1, 11)] + [
        cov("x", "missing_gap", 0),
        cov("y", "missing_gap", 0),
    ]
    assert weighted_score(reqs2, two_gaps) == pytest.approx(10 / 12)
    assert band_of(reqs2, two_gaps) == "stretch"
    # a missing nice-to-have does not cap
    reqs3 = [req(f"m{i}", "must") for i in range(1, 9)] + [req("n", "nice")]
    assert (
        band_of(reqs3, [cov(f"m{i}", "covered", 4) for i in range(1, 9)] + [cov("n", "missing_gap", 0)])
        == "strong"
    )


def test_nothing_assessed_yet_reads_stretch_not_weak() -> None:
    reqs = [req("a", "must")]
    assert band_of(reqs, [cov("a", "unknown", 0)]) == "stretch"


def answers(*verdicts: str) -> list[GateAnswer]:
    return [GateAnswer(gateId=f"g{i}", factIds=[], verdict=v, note="") for i, v in enumerate(verdicts)]  # type: ignore[arg-type]


def test_confidence_is_high_only_when_every_must_and_required_row_is_covered_or_partial_with_facts() -> None:
    reqs = [req("a", "must"), req("b", "required"), req("c", "nice")]
    rows = [cov("a", "covered", 4), cov("b", "partial", 2), cov("c", "missing_gap", 0)]
    confidence, note = confidence_of(reqs, rows, answers("pass", "pass"), gate_count=2)
    assert confidence == "high"
    assert "backed by facts" in note


def test_confidence_is_low_when_a_must_is_unknown_or_a_gate_is_uncertain() -> None:
    reqs = [req("a", "must"), req("b", "required")]
    ok_rows = [cov("a", "covered", 4), cov("b", "covered", 4)]
    unknown_must = [cov("a", "unknown", 0), cov("b", "covered", 4)]
    assert confidence_of(reqs, unknown_must, [], gate_count=0)[0] == "low"
    assert confidence_of(reqs, ok_rows, answers("pass", "uncertain"), gate_count=2)[0] == "low"
    assert (
        confidence_of(reqs, ok_rows, answers("pass", "fail"), gate_count=2)[0] == "high"
    )  # a fail is decided, not unsure
    note = confidence_of(reqs, unknown_must, answers("uncertain"), gate_count=1)[1]
    assert "1 must-have" in note
    assert "1 hard condition" in note


def test_confidence_is_medium_for_gaps_synonyms_and_unknown_required_rows() -> None:
    reqs = [req("a", "must"), req("b", "required")]
    assert (
        confidence_of(reqs, [cov("a", "missing_gap", 0), cov("b", "covered", 4)], [], gate_count=0)[0]
        == "medium"
    )
    assert (
        confidence_of(reqs, [cov("a", "covered", 4), cov("b", "synonym_only", 2)], [], gate_count=0)[0]
        == "medium"
    )
    assert (
        confidence_of(reqs, [cov("a", "covered", 4), cov("b", "unknown", 0)], [], gate_count=0)[0] == "medium"
    )
    only_nice = [req("a", "nice")]
    assert confidence_of(only_nice, [cov("a", "covered", 4)], [], gate_count=1)[0] == "medium"


def test_zero_gates_is_reported_not_silently_passed() -> None:
    reqs = [req("a", "must")]
    note = confidence_of(reqs, [cov("a", "covered", 4)], [], gate_count=0)[1]
    assert "no hard conditions" in note


async def test_every_batch_starts_with_today_s_date() -> None:
    fake = FakeClaudeClient().script_structured("match.coverage", echo_gaps, repeat=True)
    await assess_coverage(
        fake,
        model=HAIKU,
        requirements=many(12),
        facts=list(BOOK.values()),
        call_ctx=CTX,
        today=date(2026, 11, 5),
    )
    assert len(fake.structured_calls) == 2
    assert all(c.user_text.startswith("Today's date: 2026-11-05.\n\n") for c in fake.structured_calls)
    assert all(YEARS_RULE in c.system_text for c in fake.structured_calls)
