"""The evidence check: which quotes are checked, what is dropped, and every way it can fail open."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from tayyari.analysis import evidence
from tayyari.analysis.evidence import EVIDENCE_MIN_SCORE, EvidenceOutcome, build_claims, check_evidence
from tayyari.analysis.normalise import ReadResult, to_profile
from tayyari.contract import AnalysisEvent, LogEvent, ModelsUsed, Profile
from tayyari.core.settings import Settings
from tayyari.decisions.service import DecisionContext, EvidenceClaim, EvidenceVerdict
from tayyari.ingest import PlannedSource, SourceDoc
from tests.fakes.decisions import FakeDecisionService
from tests.unit.analysis.support import make_run, synth_output


def _result(
    sid: str,
    kind: str,
    text: str,
    *,
    reader: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> ReadResult:
    planned = PlannedSource(sid, kind, sid.title(), f"{kind} page", sid, None, None)  # type: ignore[arg-type]
    raw = SourceDoc(
        id=sid, kind=kind, label=sid.title(), src=sid, kind_label=kind, text=text, meta=meta or {}
    )
    return ReadResult(planned=planned, raw=raw, reader=reader or {"sub": "s", "summary": "x", "skills": []})


def _profile() -> tuple[Profile, list[ReadResult]]:
    results = [
        _result(
            "resume",
            "resume",
            "RESUME RAW TEXT",
            reader={"sub": "resume reader", "summary": "r"},
            meta={"pages": 2},
        ),
        _result("link-1", "link", "LINK RAW TEXT"),
    ]
    profile = to_profile(
        synth_output(["resume", "link-1"]),
        results,
        None,
        False,
        models=ModelsUsed(reader="r", synth="s"),
        profile_id="p1",
    )
    return profile, results


def _texts(profile: Profile) -> list[str]:
    return [e.text for s in profile.skills for e in s.evidence]


def _log_lines(events: Sequence[AnalysisEvent]) -> list[LogEvent]:
    return [e for e in events if isinstance(e, LogEvent)]


# ───────────────────────── building the claims ─────────────────────────


def test_only_skills_at_the_minimum_score_are_checked() -> None:
    profile, results = _profile()
    claims, where = build_claims(profile, results)
    checked = {c.skill_id for c in claims}
    scored = {s.id: s.score for s in profile.skills}
    assert EVIDENCE_MIN_SCORE == 45
    assert checked == {
        sid
        for sid, score in scored.items()
        if score >= 45 and any(s.evidence for s in profile.skills if s.id == sid)
    }
    assert "skill-7" not in checked and "skill-8" not in checked  # gaps state an observed absence
    assert len(claims) == len(where)


def test_a_claim_carries_the_source_text_reader_output_and_facts() -> None:
    profile, results = _profile()
    claims, where = build_claims(profile, results)
    first = claims[0]
    assert (first.skill_id, first.source_id) == ("skill-1", "resume")
    assert first.text == "Evidence for skill 1 in resume"
    assert first.source_text == "RESUME RAW TEXT"
    assert first.reader_output == {"sub": "resume reader", "summary": "r"}
    assert first.facts == {"pages": 2}
    assert where[0] == (0, 0)
    link_claim = next(c for c in claims if c.source_id == "link-1")
    assert link_claim.source_text == "LINK RAW TEXT"
    assert link_claim.facts == {}


def test_the_claim_positions_point_into_the_profile() -> None:
    profile, results = _profile()
    claims, where = build_claims(profile, results)
    for claim, (si, ei) in zip(claims, where, strict=True):
        skill = profile.skills[si]
        assert (skill.id, skill.evidence[ei].text) == (claim.skill_id, claim.text)


def test_a_source_without_text_gives_an_empty_source_text() -> None:
    profile, results = _profile()
    results[0].raw = None  # e.g. a reader that failed before its source was fetched
    claims, _ = build_claims(profile, results)
    resume_claim = next(c for c in claims if c.source_id == "resume")
    assert (resume_claim.source_text, resume_claim.facts) == ("", None)


# ───────────────────────── applying the verdicts ─────────────────────────


async def test_everything_is_kept_when_every_verdict_keeps(settings: Settings) -> None:
    profile, results = _profile()
    before = _texts(profile)
    decisions = FakeDecisionService()
    run, events = make_run(settings, decisions=decisions)
    outcome = await check_evidence(run, profile, results)
    assert outcome is not None and outcome.dropped == 0
    assert _texts(profile) == before
    (line,) = _log_lines(events)
    assert (line.who, line.kind) == ("Jev", None)
    assert line.text == f"Checked {outcome.checked} evidence quotes, dropped 0"
    assert outcome.checked == len(decisions.evidence_calls[0][0])


async def test_quotes_ruled_out_are_removed_and_counted(settings: Settings) -> None:
    profile, results = _profile()
    decisions = FakeDecisionService(evidence={("skill-1", "resume"): False, ("skill-3", "resume"): False})
    run, events = make_run(settings, decisions=decisions)
    outcome = await check_evidence(run, profile, results)
    assert outcome is not None and outcome.dropped == 2
    skills = {s.id: s for s in profile.skills}
    assert skills["skill-1"].evidence == []
    assert skills["skill-3"].evidence == []
    assert [e.text for e in skills["skill-2"].evidence] == ["Evidence for skill 2 in link-1"]
    assert _log_lines(events)[-1].text == f"Checked {outcome.checked} evidence quotes, dropped 2"


async def test_only_the_dropped_item_of_a_skill_goes(settings: Settings) -> None:
    profile, results = _profile()
    skill = profile.skills[0]
    skill.evidence = [
        *skill.evidence,
        skill.evidence[0].model_copy(update={"text": "Second quote", "sourceId": "link-1"}),
    ]
    decisions = FakeDecisionService(evidence=lambda claim: claim.text != "Second quote")
    run, _ = make_run(settings, decisions=decisions)
    outcome = await check_evidence(run, profile, results)
    assert outcome is not None and outcome.dropped == 1
    assert [e.text for e in profile.skills[0].evidence] == ["Evidence for skill 1 in resume"]


async def test_a_skill_that_loses_all_its_evidence_stays_with_its_score(settings: Settings) -> None:
    profile, results = _profile()
    scores = [s.score for s in profile.skills]
    decisions = FakeDecisionService(evidence=lambda claim: False)
    run, _ = make_run(settings, decisions=decisions)
    await check_evidence(run, profile, results)
    assert [s.score for s in profile.skills] == scores
    assert all(s.evidence == [] for s in profile.skills if s.score >= EVIDENCE_MIN_SCORE)
    assert [e.text for e in profile.skills[6].evidence] == [
        "Evidence for skill 7 in resume"
    ]  # a gap is exempt


async def test_the_decision_context_names_the_analysis(settings: Settings) -> None:
    profile, results = _profile()
    decisions = FakeDecisionService()
    run, _ = make_run(settings, decisions=decisions)
    await check_evidence(run, profile, results)
    ctx: DecisionContext = decisions.evidence_calls[0][1]
    assert (ctx.profile_id, ctx.is_sample, ctx.request_id, ctx.purpose_tag) == (
        "profile-test",
        False,
        "req-test",
        "analysis",
    )
    assert ctx.analysis_id == run.analysis_id


async def test_a_single_quote_reads_as_a_singular(settings: Settings) -> None:
    profile, results = _profile()
    for skill in profile.skills[1:]:
        skill.evidence = []
    profile.skills[0].evidence = profile.skills[0].evidence[:1]
    run, events = make_run(settings)
    outcome = await check_evidence(run, profile, results)
    assert outcome == EvidenceOutcome(checked=1, dropped=0)
    assert _log_lines(events)[-1].text == "Checked 1 evidence quote, dropped 0"


# ───────────────────────── no check, no line ─────────────────────────


async def test_with_nothing_to_check_the_service_is_not_called(settings: Settings) -> None:
    profile, results = _profile()
    for skill in profile.skills:
        skill.evidence = []
    decisions = FakeDecisionService()
    run, events = make_run(settings, decisions=decisions)
    assert await check_evidence(run, profile, results) is None
    assert decisions.evidence_calls == []
    assert events == []


async def test_with_decisions_off_nothing_is_checked_and_nothing_is_shown(settings: Settings) -> None:
    from tayyari.decisions.service import NullDecisionService

    profile, results = _profile()
    before = _texts(profile)
    run, events = make_run(settings)
    object.__setattr__(run, "decisions", NullDecisionService())
    assert await check_evidence(run, profile, results) is None
    assert _texts(profile) == before
    assert events == []


async def test_a_failing_service_changes_nothing_and_shows_nothing(settings: Settings) -> None:
    profile, results = _profile()
    before = _texts(profile)
    decisions = FakeDecisionService().fail("check_evidence", RuntimeError("jev is down"))
    run, events = make_run(settings, decisions=decisions)
    assert await check_evidence(run, profile, results) is None
    assert _texts(profile) == before
    assert events == []


async def test_a_timeout_keeps_everything(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, results = _profile()
    before = _texts(profile)
    monkeypatch.setattr(evidence, "EVIDENCE_TIMEOUT_S", 0.05)

    class Slow(FakeDecisionService):
        async def check_evidence(
            self, claims: Sequence[EvidenceClaim], ctx: DecisionContext
        ) -> list[EvidenceVerdict]:
            await asyncio.sleep(5)
            return await super().check_evidence(claims, ctx)

    run, events = make_run(settings, decisions=Slow(evidence=lambda claim: False))
    assert await check_evidence(run, profile, results) is None
    assert _texts(profile) == before
    assert events == []


async def test_a_verdict_list_of_the_wrong_length_is_ignored(settings: Settings) -> None:
    profile, results = _profile()
    before = _texts(profile)

    class Short(FakeDecisionService):
        async def check_evidence(
            self, claims: Sequence[EvidenceClaim], ctx: DecisionContext
        ) -> list[EvidenceVerdict]:
            verdicts = await super().check_evidence(claims, ctx)
            return verdicts[:-1]

    run, events = make_run(settings, decisions=Short(evidence=lambda claim: False))
    assert await check_evidence(run, profile, results) is None
    assert _texts(profile) == before
    assert events == []


async def test_cancellation_is_not_swallowed(settings: Settings) -> None:
    profile, results = _profile()
    started = asyncio.Event()

    class Blocked(FakeDecisionService):
        async def check_evidence(
            self, claims: Sequence[EvidenceClaim], ctx: DecisionContext
        ) -> list[EvidenceVerdict]:
            started.set()
            await asyncio.sleep(60)
            raise AssertionError("not reached")

    run, _ = make_run(settings, decisions=Blocked())
    task = asyncio.create_task(check_evidence(run, profile, results))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
