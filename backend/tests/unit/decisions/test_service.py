"""The decisions facade: its types, the null service and the fake that stands in for the real one in tests."""

from __future__ import annotations

import dataclasses
import inspect
from datetime import date
from uuid import UUID

import pytest

from tayyari.contract import Profile
from tayyari.decisions import service as facade
from tayyari.decisions.service import (
    DecisionContext,
    DecisionService,
    EvidenceClaim,
    EvidenceVerdict,
    FirstReadInput,
    FirstReadResult,
    GateInput,
    GateResult,
    NullDecisionService,
    ShadowMaterials,
    TriageInput,
    TriageResult,
)
from tests.fakes.decisions import FakeDecisionService

CTX = DecisionContext(profile_id="p1", analysis_id=UUID(int=3), is_sample=False, request_id="r1")


def claim(skill: str = "python", source: str = "github") -> EvidenceClaim:
    return EvidenceClaim(skill, source, f"{skill} claim", "source text", None, None)


def test_decision_context_fields_and_default_tag() -> None:
    assert [f.name for f in dataclasses.fields(DecisionContext)] == [
        "profile_id",
        "analysis_id",
        "is_sample",
        "request_id",
        "purpose_tag",
    ]
    assert CTX.purpose_tag == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        CTX.profile_id = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        DecisionContext()  # type: ignore[call-arg]


def test_the_frozen_facade_types_have_the_agreed_fields() -> None:
    def names(cls: type) -> list[str]:
        return [f.name for f in dataclasses.fields(cls)]

    assert names(FirstReadInput) == ["resume_text", "notes_text", "today"]
    assert names(FirstReadResult) == ["lines", "signals", "facts"]
    assert names(TriageInput) == ["source_id", "kind", "host", "title", "text"]
    assert names(TriageResult) == [
        "skip",
        "skip_reason",
        "label",
        "notes",
        "log_lines",
        "text_quality",
        "english",
    ]
    assert names(EvidenceClaim) == ["skill_id", "source_id", "text", "source_text", "reader_output", "facts"]
    assert names(EvidenceVerdict) == ["skill_id", "source_id", "text", "keep", "relation", "reason"]
    assert names(GateInput) == ["message", "thread"]
    assert names(GateResult) == ["allow_tools", "reason", "p_outside"]
    assert names(ShadowMaterials) == ["profile", "reader_outputs", "resume_text"]


def test_results_do_not_share_their_default_lists() -> None:
    a, b = TriageResult(), TriageResult()
    a.notes.append("x")
    assert b.notes == [] and TriageResult().log_lines == []
    assert FirstReadResult().lines == [] and FirstReadResult().facts == {}


def test_the_protocol_lists_the_methods_the_packs_implement() -> None:
    public = {n for n, _ in inspect.getmembers(DecisionService) if not n.startswith("_")}
    assert public == {"first_read", "triage", "check_evidence", "chat_web_gate", "shadow_compare", "drain"}
    assert inspect.iscoroutinefunction(DecisionService.first_read)
    assert not inspect.iscoroutinefunction(DecisionService.shadow_compare)  # returns at once
    assert inspect.iscoroutinefunction(DecisionService.drain)


def test_both_implementations_satisfy_the_protocol() -> None:
    assert isinstance(NullDecisionService(), DecisionService)
    assert isinstance(FakeDecisionService(), DecisionService)


async def test_the_null_service_is_neutral_everywhere(sample_profile: Profile) -> None:
    service = NullDecisionService()
    assert await service.first_read(FirstReadInput("resume", None, date(2026, 10, 7)), CTX) is None

    triage = await service.triage(TriageInput("s1", "link", "example.com", "Title", "text"), CTX)
    assert triage == TriageResult()
    assert not triage.skip and triage.label is None and triage.notes == [] and triage.english is None

    verdicts = await service.check_evidence([claim("python"), claim("sql", "resume")], CTX)
    assert [(v.skill_id, v.source_id, v.keep) for v in verdicts] == [
        ("python", "github", True),
        ("sql", "resume", True),
    ]
    assert verdicts[0].text == "python claim" and verdicts[0].relation is None
    assert await service.check_evidence([], CTX) == []

    gate = await service.chat_web_gate(GateInput("What courses?", "skill: python"), CTX)
    assert gate.allow_tools is True and gate.p_outside is None

    assert service.shadow_compare(ShadowMaterials(sample_profile, {}, None), CTX) is None
    assert await service.drain(1.0) is None


# ───────────────────────── the fake ─────────────────────────


async def test_the_fake_is_neutral_by_default_and_records_every_call(sample_profile: Profile) -> None:
    fake = FakeDecisionService()
    inp = FirstReadInput("resume", "notes", date(2026, 10, 7))
    assert await fake.first_read(inp, CTX) is None
    assert (await fake.triage(TriageInput("s", "link", None, None, "t"), CTX)) == TriageResult()
    assert [v.keep for v in await fake.check_evidence([claim(), claim("sql")], CTX)] == [True, True]
    assert (await fake.chat_web_gate(GateInput("hi", "chat"), CTX)).allow_tools is True
    fake.shadow_compare(ShadowMaterials(sample_profile, {"s": {}}, "resume"), CTX)
    await fake.drain(2.5)

    assert fake.calls == {
        "first_read": 1,
        "triage": 1,
        "check_evidence": 1,
        "chat_web_gate": 1,
        "shadow_compare": 1,
        "drain": 1,
    }
    assert fake.first_read_calls[0] == (inp, CTX)
    assert fake.drain_calls == [2.5]
    assert fake.snapshot()["chat_web_gate"] == [GateInput("hi", "chat")]


async def test_the_fake_returns_scripted_results_or_calls_scripts() -> None:
    fake = FakeDecisionService(
        first_read=FirstReadResult(["First read: Design"], ["Leads others"], {"career_family": "design_ux"}),
        triage=lambda inp: (
            TriageResult(skip=True, skip_reason="login wall") if "login" in inp.text else TriageResult()
        ),
        evidence={("python", "github"): False},
        gate=lambda inp, ctx: GateResult(allow_tools="http" in inp.message, reason="url", p_outside=0.5),
    )
    first = await fake.first_read(FirstReadInput("r", None, date(2026, 10, 7)), CTX)
    assert first is not None and first.lines == ["First read: Design"]
    assert (await fake.triage(TriageInput("s", "link", None, None, "please login"), CTX)).skip
    assert not (await fake.triage(TriageInput("s", "link", None, None, "hello"), CTX)).skip

    verdicts = await fake.check_evidence([claim("python", "github"), claim("python", "resume")], CTX)
    assert [v.keep for v in verdicts] == [False, True]
    assert verdicts[0].relation == "says_nothing" and verdicts[1].relation is None

    assert (await fake.chat_web_gate(GateInput("see http://x.y", "t"), CTX)).allow_tools
    assert not (await fake.chat_web_gate(GateInput("plain", "t"), CTX)).allow_tools


async def test_the_fake_evidence_script_can_be_a_callable_returning_a_verdict() -> None:
    def script(c: EvidenceClaim) -> bool | EvidenceVerdict:
        if c.skill_id == "sql":
            return EvidenceVerdict(
                c.skill_id, c.source_id, c.text, keep=False, relation="contradicts", reason="x"
            )
        return True

    fake = FakeDecisionService(evidence=script)
    verdicts = await fake.check_evidence([claim("python"), claim("sql")], CTX)
    assert [(v.keep, v.relation) for v in verdicts] == [(True, None), (False, "contradicts")]


async def test_the_fake_can_be_told_to_fail() -> None:
    fake = FakeDecisionService().fail("triage", RuntimeError("boom"), times=2).fail("drain", TimeoutError())
    for _ in range(2):
        with pytest.raises(RuntimeError, match="boom"):
            await fake.triage(TriageInput("s", "link", None, None, "t"), CTX)
    assert await fake.triage(TriageInput("s", "link", None, None, "t"), CTX) == TriageResult()
    with pytest.raises(TimeoutError):
        await fake.drain(1.0)
    assert fake.calls["triage"] == 3  # failed calls are recorded too
    with pytest.raises(ValueError, match="unknown"):
        fake.fail("nope", RuntimeError())


def test_the_facade_module_exports_exactly_its_public_names() -> None:
    assert set(facade.__all__) == {
        "DecisionContext",
        "DecisionService",
        "EvidenceClaim",
        "EvidenceVerdict",
        "FirstReadInput",
        "FirstReadResult",
        "GateInput",
        "GateResult",
        "NullDecisionService",
        "ShadowMaterials",
        "TriageInput",
        "TriageResult",
    }
