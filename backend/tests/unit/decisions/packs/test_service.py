"""`JevDecisionService` as a whole: the registry and `defaults.toml` agree, nothing raises when Jev fails or is slow,
cancellation propagates, and a full session through every method works end to end.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from tayyari.contract import Profile
from tayyari.decisions.config import DecisionConfigStore, load_defaults
from tayyari.decisions.packs.service_impl import ALL_PACKS, JevDecisionService, build_registry
from tayyari.decisions.packs.shadow import REQUIREMENT_HAVE_ID, ROLE_FIT_ID, SKILL_LEVEL_ID
from tayyari.decisions.service import (
    DecisionService,
    EvidenceClaim,
    GateInput,
    NullDecisionService,
    ShadowMaterials,
    TriageResult,
)
from tayyari.llm.errors import JevError
from tests.fakes.jev import choice_answer, noul_answer, score_answer
from tests.unit.decisions.packs.support import (
    CTX,
    PackHarness,
    first_read_answers,
    page_input,
    relation_answer,
    resume_input,
    triage_answers,
)
from tests.unit.decisions.support import SpendTrip

Harness = Callable[..., PackHarness]

JEV_ERROR_KINDS = ["timeout", "connection", "server", "rate_limit", "auth", "bad_request", "invalid_response"]
EXPECTED_SPEC_IDS = {
    "first_read.career_family",
    "first_read.career_stage",
    "first_read.depth",
    "first_read.leads_or_mentors",
    "first_read.public_proof",
    "first_read.measured_outcomes",
    "first_read.ai_experience",
    "first_read.progression_promoted",
    "first_read.progression_scope",
    "first_read.progression_switch",
    "first_read.resume_like",
    "triage.text_quality",
    "triage.about_person_work",
    "triage.mainly_english",
    "triage.page_type",
    "evidence.support",
    "chat.web_gate",
    "skill.level",
    "requirement.have",
    "role.closest",
    "role.fit",
}


# ───────────────────────── the registry and defaults.toml ─────────────────────────


def test_the_registry_holds_every_pack_and_spec() -> None:
    registry = build_registry()
    assert [p.id for p in registry.packs()] == ["first_read", "triage", "evidence", "chat_gate", "shadow"]
    assert {s.id for s in registry.specs()} == EXPECTED_SPEC_IDS
    assert len(registry.specs()) == len(EXPECTED_SPEC_IDS)  # no duplicates
    assert [p.id for p in ALL_PACKS] == [p.id for p in registry.packs()]


def test_every_spec_id_has_a_defaults_table_and_the_other_way_round() -> None:
    registry = build_registry()
    defaults = load_defaults()
    assert set(defaults) == EXPECTED_SPEC_IDS
    store = DecisionConfigStore(defaults)
    assert registry.unknown_config_ids(store) == []
    assert all(spec.id in defaults for spec in registry.specs())


def test_the_specs_own_defaults_agree_with_defaults_toml() -> None:
    store = DecisionConfigStore(load_defaults())
    for spec in build_registry().specs():
        assert store.for_spec(spec) == spec.default, spec.id


def test_a_config_id_that_matches_no_spec_is_reported() -> None:
    store = DecisionConfigStore({"triage.text_qualty": {"mode": "off"}}, {"nope.spec": {"mode": "off"}})
    assert build_registry().unknown_config_ids(store) == ["nope.spec", "triage.text_qualty"]


def test_the_live_specs_are_primary_and_the_shadow_ones_are_shadow() -> None:
    store = DecisionConfigStore(load_defaults())
    modes = {s.id: store.for_spec(s).mode for s in build_registry().specs()}
    assert {i for i, m in modes.items() if m == "shadow"} == {
        "skill.level",
        "requirement.have",
        "role.closest",
        "role.fit",
    }
    assert {
        m
        for i, m in modes.items()
        if i not in {"skill.level", "requirement.have", "role.closest", "role.fit"}
    } == {"primary"}


def test_every_question_is_a_valid_wire_shape_and_every_version_is_set() -> None:
    for spec in build_registry().specs():
        wire = spec.question.to_wire()
        assert wire["type"] in {"noul", "choice", "score"}, spec.id
        assert spec.version.strip(), spec.id
        if wire["type"] == "choice":
            assert 1 <= len(wire["criteria"]) <= 255
            assert all(len(str(d)) <= 255 for d in wire["criteria"].values() if d is not None), spec.id
        if wire["type"] == "score":
            assert 2 <= len(wire["criteria"]) <= 10, spec.id


def test_the_service_satisfies_the_decision_service_protocol(harness: Harness) -> None:
    assert isinstance(harness({}).service, DecisionService)
    assert isinstance(harness({}).service, JevDecisionService)


def test_the_budgets_come_from_the_settings(harness: Harness) -> None:
    service = harness({}).service
    assert service._first_read_budget_s == pytest.approx(3.0)
    assert service._triage_budget_s == pytest.approx(5.0)
    assert service._gate_budget_s == pytest.approx(3.0)
    assert service._evidence_budget_s == pytest.approx(4.0)
    tight = harness({}, jev_settings={"timeout_display_s": 1.0, "timeout_evidence_s": 2.0}).service
    assert tight._first_read_budget_s == pytest.approx(1.5)
    assert tight._triage_budget_s == pytest.approx(2.0)
    assert tight._evidence_budget_s == pytest.approx(2.0)


# ───────────────────────── nothing raises ─────────────────────────


def one_of_each(profile: Profile, h: PackHarness) -> dict[str, Callable[[], Awaitable[Any]]]:
    claim = EvidenceClaim(
        "react", "resume", "Led the Stripe migration for 9000 stores", "I used React. " * 40, None, None
    )

    async def shadow() -> None:
        h.service.shadow_compare(ShadowMaterials(profile, {"resume": {"sub": "x"}}, "resume"), CTX)
        await h.service.drain(2.0)

    return {
        "first_read": lambda: h.service.first_read(resume_input(), CTX),
        "triage": lambda: h.service.triage(page_input("A page of text. " * 60), CTX),
        "check_evidence": lambda: h.service.check_evidence([claim], CTX),
        "chat_web_gate": lambda: h.service.chat_web_gate(GateInput("Any open roles?", "free: Chat"), CTX),
        "shadow": shadow,
    }


@pytest.mark.parametrize("kind", JEV_ERROR_KINDS)
async def test_no_method_raises_when_jev_errors(harness: Harness, sample_profile: Profile, kind: str) -> None:
    h = harness({}, with_claude=False)
    h.jev.fail_with(JevError(kind))  # type: ignore[arg-type]
    calls = one_of_each(sample_profile, h)

    assert await calls["first_read"]() is None
    assert await calls["triage"]() == TriageResult()
    assert [v.keep for v in await calls["check_evidence"]()] == [True]
    assert (await calls["chat_web_gate"]()).allow_tools is True
    assert await calls["shadow"]() is None


@pytest.mark.parametrize("kind", JEV_ERROR_KINDS)
async def test_no_method_raises_when_jev_and_claude_both_fail(
    harness: Harness, sample_profile: Profile, kind: str
) -> None:
    h = harness({})  # the fake Claude has nothing scripted, so every fallback call raises
    h.jev.fail_with(JevError(kind))  # type: ignore[arg-type]
    calls = one_of_each(sample_profile, h)

    assert await calls["first_read"]() is None
    assert await calls["triage"]() == TriageResult()
    assert [v.keep for v in await calls["check_evidence"]()] == [True]
    assert (await calls["chat_web_gate"]()).allow_tools is True
    assert await calls["shadow"]() is None


async def test_no_method_raises_when_the_spend_guard_trips(harness: Harness, sample_profile: Profile) -> None:
    h = harness({}, spend=SpendTrip())
    calls = one_of_each(sample_profile, h)
    assert await calls["first_read"]() is None
    assert await calls["triage"]() == TriageResult()
    assert [v.keep for v in await calls["check_evidence"]()] == [True]
    assert (await calls["chat_web_gate"]()).allow_tools is True
    assert await calls["shadow"]() is None
    assert h.jev.calls == []


async def test_no_method_raises_when_jev_is_slow_past_every_budget(
    harness: Harness, sample_profile: Profile
) -> None:
    h = harness({}, slow_jev_s=5.0, with_claude=False)
    s = h.service
    s._first_read_budget_s = s._triage_budget_s = s._gate_budget_s = s._evidence_budget_s = 0.05
    calls = one_of_each(sample_profile, h)

    assert await calls["first_read"]() is None
    assert await calls["triage"]() == TriageResult()
    assert [v.keep for v in await calls["check_evidence"]()] == [True]
    assert (await calls["chat_web_gate"]()).allow_tools is True


async def test_a_disabled_jev_without_claude_is_neutral_everywhere(
    harness: Harness, sample_profile: Profile
) -> None:
    h = harness(first_read_answers(), jev_enabled=False, with_claude=False)
    calls = one_of_each(sample_profile, h)
    assert await calls["first_read"]() is None
    assert await calls["triage"]() == TriageResult()
    assert [v.keep for v in await calls["check_evidence"]()] == [True]
    assert (await calls["chat_web_gate"]()).allow_tools is True
    assert await calls["shadow"]() is None
    assert h.jev.calls == []


async def test_it_matches_the_null_service_when_nothing_can_be_asked(harness: Harness) -> None:
    null = NullDecisionService()
    h = harness({}, jev_enabled=False, with_claude=False)
    inp = page_input("A page of text. " * 60)
    assert await h.service.triage(inp, CTX) == await null.triage(inp, CTX)
    assert await h.service.first_read(resume_input(), CTX) == await null.first_read(resume_input(), CTX)
    gate = GateInput("Any open roles?", "free: Chat")
    assert (await h.service.chat_web_gate(gate, CTX)).allow_tools == (
        await null.chat_web_gate(gate, CTX)
    ).allow_tools


# ───────────────────────── cancellation ─────────────────────────


@pytest.mark.parametrize("method", ["first_read", "triage", "check_evidence", "chat_web_gate"])
async def test_cancellation_propagates(harness: Harness, sample_profile: Profile, method: str) -> None:
    h = harness({}, slow_jev_s=30.0, with_claude=False)
    task = asyncio.ensure_future(one_of_each(sample_profile, h)[method]())
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ───────────────────────── drain ─────────────────────────


async def test_drain_delegates_to_the_runner(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness({})
    seen: list[float] = []

    async def fake_drain(timeout_s: float, *, cancel_remaining: bool = False) -> None:
        seen.append(timeout_s)

    monkeypatch.setattr(h.runner, "drain", fake_drain)
    await h.service.drain(3.5)
    assert seen == [3.5]


async def test_drain_never_raises(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness({})

    async def broken(timeout_s: float, *, cancel_remaining: bool = False) -> None:
        raise RuntimeError("log write failed")

    monkeypatch.setattr(h.runner, "drain", broken)
    await h.service.drain(1.0)


async def test_drain_flushes_the_decision_log(harness: Harness) -> None:
    h = harness(first_read_answers())
    await h.service.first_read(resume_input(), CTX)
    assert h.repos.decisions.rows == {}  # buffered, not yet written
    await h.service.drain(1.0)
    assert len(h.repos.decisions.rows) == 11 and len(h.repos.decisions.calls) == 1


# ───────────────────────── a whole session ─────────────────────────


async def test_a_full_session_through_every_method(harness: Harness, sample_profile: Profile) -> None:
    answers: dict[str, Any] = {
        **first_read_answers(),
        **triage_answers("login_or_block_wall", quality_p=0.99, page_type="blog_or_articles"),
        "evidence.support": relation_answer("says_nothing", 0.97),
        "chat.web_gate": noul_answer(0.02),
        SKILL_LEVEL_ID: score_answer(3, n_levels=6),
        ROLE_FIT_ID: score_answer(4, n_levels=6),
        REQUIREMENT_HAVE_ID: noul_answer(0.9),
    }
    h = harness(answers)

    first = await h.service.first_read(resume_input(), CTX)
    assert first is not None and first.lines[0] == "First read: Design and UX, early career"

    skipped = await h.service.triage(page_input("Please sign in. " * 4, source_id="link-1"), CTX)
    assert skipped.skip is True

    h.jev.set_answer(
        "triage.text_quality", choice_answer("readable", options=["readable", "empty_or_placeholder"], p=0.95)
    )
    read = await h.service.triage(page_input("Plain prose about my work. " * 80, source_id="link-2"), CTX)
    assert read.skip is False and read.label == "Blog"

    verdicts = await h.service.check_evidence(
        [
            EvidenceClaim(
                "react",
                "resume",
                "Led the Stripe migration for 9000 stores",
                "I used React. " * 40,
                None,
                None,
            )
        ],
        CTX,
    )
    assert [v.keep for v in verdicts] == [False]

    gate = await h.service.chat_web_gate(GateInput("What is my strongest skill?", "you: Your profile"), CTX)
    assert gate.allow_tools is False

    h.service.shadow_compare(ShadowMaterials(sample_profile, {"resume": {"sub": "x"}}, "resume"), CTX)
    await h.service.drain(5.0)

    packs = {r.pack for r in h.rows}
    assert packs == {"first_read", "triage", "evidence", "chat_gate", "shadow"}
    assert {c.pack for c in h.calls} == packs
    assert all(r.profile_id == "profile-1" for r in h.rows)


# ───────────────────────── a bug in a pack ─────────────────────────


def _bug(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("bug in the pack")


async def test_a_bug_in_a_pack_is_contained(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from tayyari.decisions.packs import service_impl

    h = harness({**first_read_answers(), **triage_answers(), "chat.web_gate": noul_answer(0.01)})
    monkeypatch.setattr(service_impl, "interpret_first_read", _bug)
    monkeypatch.setattr(service_impl, "interpret_triage", _bug)
    monkeypatch.setattr(service_impl, "interpret_gate", _bug)

    assert await h.service.first_read(resume_input(), CTX) is None
    assert await h.service.triage(page_input("A page of text. " * 60), CTX) == TriageResult()
    gate = await h.service.chat_web_gate(GateInput("What is my strongest skill?", "you: Profile"), CTX)
    assert gate.allow_tools is True and "failed" in gate.reason


async def test_a_cancelled_drain_propagates(harness: Harness, sample_profile: Profile) -> None:
    h = harness({}, slow_jev_s=30.0)
    h.service.shadow_compare(ShadowMaterials(sample_profile, {}, "resume"), CTX)
    task = asyncio.ensure_future(h.service.drain(30.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await h.runner.drain(0.05, cancel_remaining=True)
