"""Rules the contract models enforce on their own, so no code path can build a value that breaks them."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from tayyari.contract.match import (
    MAX_GATES,
    MAX_QUESTIONS,
    MAX_REQUIREMENTS,
    Coverage,
    Gate,
    MatchResult,
    Posting,
    Question,
    Radar,
    Requirement,
    StatusCounts,
)
from tayyari.contract.models import Profile
from tayyari.match.errors import CoverUnavailable
from tayyari.match.service import MatchService
from tests.fakes.claude import FakeClaudeClient
from tests.unit.match.support import CTX, HAIKU, meera, script_pipeline


def requirement(rid: str = "r1", tier: str = "must", provenance: str = "stated") -> Requirement:
    return Requirement(
        id=rid,
        text="t",
        quote="q",
        tier=tier,  # type: ignore[arg-type]
        kind="skill",
        provenance=provenance,  # type: ignore[arg-type]
        terms=[],
    )


@pytest.mark.parametrize("tier", ["must", "required"])
def test_an_inferred_requirement_cannot_be_built_as_must_or_required(tier: str) -> None:
    with pytest.raises(ValidationError, match="inferred"):
        requirement(tier=tier, provenance="inferred")


@pytest.mark.parametrize("tier", ["preferred", "nice"])
def test_an_inferred_requirement_may_be_preferred_or_nice(tier: str) -> None:
    assert requirement(tier=tier, provenance="inferred").tier == tier


def posting(requirements: int = 1, gates: int = 0) -> Posting:
    return Posting(
        hash="h",
        title="t",
        workMode="unspecified",
        rawText="x",
        requirements=[requirement(f"r{i}") for i in range(requirements)],
        gates=[Gate(id=f"g{i}", question="q?", quote="q", severity=5) for i in range(gates)],
        anomalies=[],
        links=[],
    )


def test_a_posting_holds_at_most_thirty_requirements_and_six_gates() -> None:
    assert len(posting(MAX_REQUIREMENTS, MAX_GATES).requirements) == 30
    with pytest.raises(ValidationError):
        posting(MAX_REQUIREMENTS + 1)
    with pytest.raises(ValidationError):
        posting(1, MAX_GATES + 1)


def result_with(questions: int) -> MatchResult:
    return MatchResult(
        postingHash="h",
        band="good",
        counts=StatusCounts(
            covered=0, partial=0, synonym_only=0, missing_have_it=0, missing_gap=0, unknown=0
        ),
        confidence="low",
        confidenceNote="",
        gates=[],
        blocked=False,
        coverage=[],
        radar=Radar(axes=[]),
        questions=[Question(id=f"q{i}", text="t", why="w") for i in range(questions)],
        version=1,
    )


def test_a_match_result_asks_at_most_three_questions() -> None:
    assert len(result_with(MAX_QUESTIONS).questions) == 3
    with pytest.raises(ValidationError):
        result_with(MAX_QUESTIONS + 1)


@pytest.mark.parametrize("severity", [0, 11, -3])
def test_gate_severity_is_one_to_ten(severity: int) -> None:
    with pytest.raises(ValidationError):
        Gate(id="g", question="q?", quote="q", severity=severity)


@pytest.mark.parametrize("level", [-1, 5])
def test_coverage_level_is_zero_to_four(level: int) -> None:
    with pytest.raises(ValidationError):
        Coverage(requirementId="r", factIds=[], status="unknown", level=level, note="")


# ───────────────────────── a person with nothing to show ─────────────────────────


async def test_a_person_with_no_evidence_gets_honest_unknowns_and_no_invented_resume() -> None:
    persona = meera()
    bare_profile = Profile.from_dict({**persona.profile.to_dict(), "skills": []})
    bare = dataclasses.replace(persona, profile=bare_profile, portfolio_facts=[], coverage={}, gates={})
    fake = script_pipeline(FakeClaudeClient(), bare)
    service = MatchService(claude=fake, model=HAIKU)
    outcome = await service.analyse(
        profile=bare_profile,
        context=bare.context.model_copy(update={"sources": []}),
        posting_text=persona.posting_text,
        call_ctx=CTX,
        portfolio_facts=[],
    )
    assert outcome.book.facts == []
    result = outcome.result
    assert result.counts.unknown == len(
        outcome.posting.requirements
    )  # nothing is a fail: it simply cannot be told
    assert result.band == "stretch"
    assert result.confidence == "low"
    assert len(result.questions) == 3
    assert not result.blocked

    composed = await service.compose_resume(
        book=outcome.book, posting=outcome.posting, result=result, call_ctx=CTX
    )
    variant = composed.variant
    assert variant.experience == [] and variant.projects == [] and variant.skills == []
    assert variant.summary.status == "dropped"
    with pytest.raises(CoverUnavailable):
        await service.compose_cover(book=outcome.book, posting=outcome.posting, result=result, call_ctx=CTX)
    assert fake.calls_for("match.cover") == []
