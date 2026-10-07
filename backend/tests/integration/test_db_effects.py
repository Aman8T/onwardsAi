"""What an analysis leaves in the database: usage rows per purpose, Jev calls per pack, the decision log with its
shadow rows and incumbents, the stored profile and context, and a spend ledger that adds up to all of it.

All of these read one finished resume + link + notes analysis (see `scenarios.run_composite`)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import AsyncIterator, Callable

import pytest

from tayyari.core.limits import JEV_MAX_QUESTIONS_PER_CALL
from tayyari.core.settings import Settings
from tayyari.decisions.packs.shadow import band_of, build_plan
from tayyari.llm.models import jev_cost_micro
from tayyari.storage.repos.decisions import decode_state
from tayyari.storage.tables import (
    DecisionLogRow,
    DecisionStateRow,
    JevCallRow,
    LlmCallRow,
    ProfileContextRow,
    ProfileRow,
)
from tests.fakes.claude import fake_usage
from tests.integration.scenarios import BOGUS_CLAIM, EMAIL, PHONE, Composite, run_composite
from tests.integration.support import FakeWeb

MakeSettings = Callable[..., Settings]


@pytest.fixture
async def composite(build_settings: MakeSettings, web: FakeWeb) -> AsyncIterator[Composite]:
    async with run_composite(build_settings(), web) as c:
        yield c


async def test_llm_calls_are_recorded_per_purpose_with_costs(composite: Composite) -> None:
    c = composite
    rows = await c.env.rows(LlmCallRow)
    settings = c.env.settings

    # three readers (resume, link, notes), one synthesis, and the one Claude answer a low-confidence Jev answer needed
    assert Counter(r.purpose for r in rows) == {"reader": 3, "synth": 1, "decision_fallback": 1}
    assert {r.status for r in rows} == {"ok"}
    models = {r.purpose: r.model for r in rows}
    assert models == {
        "reader": settings.reader_model,
        "synth": settings.synth_model,
        "decision_fallback": settings.reader_model,
    }

    # every row is priced from the real price table for the tokens it holds
    for r in rows:
        priced = fake_usage(
            r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_write_tokens=r.cache_write_tokens,
            cache_read_tokens=r.cache_read_tokens,
            web_searches=r.web_searches,
        )
        assert r.cost_micro_usd == priced.cost_micro_usd > 0, r.purpose

    # one analysis: the rows share its ids
    assert {r.profile_id for r in rows} == {c.profile.id}
    assert len({r.analysis_id for r in rows}) == 1
    assert None not in {r.analysis_id for r in rows}
    assert {r.ip_hash for r in rows if r.purpose != "decision_fallback"} != {None}


async def test_jev_calls_are_recorded_per_pack(composite: Composite) -> None:
    c = composite
    calls = await c.env.rows(JevCallRow)
    plan = build_plan(c.profile)
    shadow_calls = math.ceil(len(plan.bindings) / JEV_MAX_QUESTIONS_PER_CALL)

    assert Counter(r.pack for r in calls) == {
        "first_read": 1,
        "triage": 1,
        "evidence": 1,
        "shadow": shadow_calls,
    }
    assert {r.status for r in calls} == {"ok"}
    for r in calls:
        assert r.model_requested == r.model_served == "jev-1.13.0"
        assert r.profile_id == c.profile.id
        assert r.is_sample is False
        assert r.input_tokens > 0
        assert r.cost_micro_usd == jev_cost_micro(r.model_served or "", r.input_tokens) > 0
        response = r.response or {}
        assert set(response) == {"model", "answers", "usage"}
        assert set(response["answers"]) == set(r.questions)

    by_pack: dict[str, set[str]] = {}
    for r in calls:
        by_pack.setdefault(r.pack, set()).update(r.questions)
    assert {q for q in by_pack["first_read"]} == {
        "first_read.career_family",
        "first_read.career_stage",
        "first_read.depth",
        "first_read.leads_or_mentors",
        "first_read.measured_outcomes",
        "first_read.progression_promoted",
        "first_read.public_proof",
        "first_read.ai_experience",
        "first_read.progression_scope",
        "first_read.progression_switch",
        "first_read.resume_like",
    }
    assert by_pack["triage"] == {
        "triage.text_quality",
        "triage.about_person_work",
        "triage.mainly_english",
        "triage.page_type",
    }
    assert by_pack["evidence"] == {"evidence.support"}
    assert by_pack["shadow"] == set(plan.bindings)


async def test_request_states_are_stored_once_and_without_contact_data(composite: Composite) -> None:
    c = composite
    calls = await c.env.rows(JevCallRow)
    states = {s.hash: s for s in await c.env.rows(DecisionStateRow)}
    assert {r.state_hash for r in calls} == set(states)

    decoded = {r.pack: decode_state(states[r.state_hash].payload) for r in calls}
    assert "Acme Labs" in decoded["first_read"]["resume"]
    assert EMAIL not in decoded["first_read"]["resume"]
    assert PHONE not in decoded["first_read"]["resume"]
    assert decoded["evidence"]["claim"] == BOGUS_CLAIM
    assert decoded["triage"]["source_kind"] == "link"
    assert set(decoded["shadow"]["readers"]) == {"resume", "link-1", "notes"}
    assert all(not s.is_sample for s in states.values())


async def test_decision_log_holds_the_live_decisions_and_the_shadow_rows(composite: Composite) -> None:
    c = composite
    rows = await c.env.rows(DecisionLogRow)
    calls = {r.id: r for r in await c.env.rows(JevCallRow)}
    plan = build_plan(c.profile)

    # shadow: one row per question, never used, each next to Sonnet's own number
    shadow = [r for r in rows if r.mode == "shadow"]
    assert len(shadow) == len(plan.bindings)
    assert {r.subject_ref for r in shadow} == set(plan.bindings)
    for r in shadow:
        assert r.pack == "shadow"
        assert r.used is False and r.source_used == "jev" and r.escalated is False
        assert r.incumbent == plan.incumbent[r.subject_ref or ""]
        assert r.jev_answer is not None
        assert r.profile_id == c.profile.id and r.is_sample is False
        assert r.call_id is not None and calls[r.call_id].pack == "shadow"
        assert r.extra["purpose_tag"] == "analysis"
    react = next(s for s in c.profile.skills if s.name == "React")
    react_row = next(r for r in shadow if r.subject_ref == f"skill.level#{react.id}")
    assert react_row.incumbent == {"score": react.score, "band": band_of(react.score), "skill_id": react.id}

    # first read: the questions Jev answered, the unsure one left neutral
    first_read = {r.spec_id: r for r in rows if r.pack == "first_read"}
    assert len(first_read) == 11
    assert first_read["first_read.career_family"].used is True
    assert first_read["first_read.career_family"].source_used == "jev"
    assert first_read["first_read.ai_experience"].used is False
    assert first_read["first_read.ai_experience"].source_used == "default"

    # triage: Jev was unsure about one question, Claude answered it (logged as an escalation)
    triage = {r.spec_id: r for r in rows if r.pack == "triage"}
    assert set(triage) == {
        "triage.text_quality",
        "triage.about_person_work",
        "triage.mainly_english",
        "triage.page_type",
    }
    about = triage["triage.about_person_work"]
    assert (about.used, about.source_used, about.escalated) == (True, "claude", True)
    assert about.escalation_reason == "low_confidence"
    assert about.claude_answer == {"answer": True, "confidence": "high"}
    assert triage["triage.page_type"].source_used == "jev"
    assert triage["triage.page_type"].extra["value"] == "blog_or_articles"

    # evidence: the one quote the code could not settle, and what Jev said about it
    (evidence,) = [r for r in rows if r.pack == "evidence"]
    assert evidence.spec_id == "evidence.support"
    assert (evidence.mode, evidence.used, evidence.source_used) == ("primary", True, "jev")
    assert evidence.extra["value"] == "says_nothing"
    assert evidence.jev_confidence is not None and evidence.jev_confidence >= 0.8
    assert evidence.call_id is not None and calls[evidence.call_id].pack == "evidence"

    # nothing else was logged: no chat gate in an analysis
    assert {r.pack for r in rows} == {"first_read", "triage", "evidence", "shadow"}


async def test_the_profile_and_its_chat_context_are_stored(composite: Composite) -> None:
    c = composite
    (profile_row,) = await c.env.rows(ProfileRow, ProfileRow.id == c.profile.id)
    assert profile_row.is_sample is False
    assert profile_row.goal == "Become a senior design engineer"
    assert profile_row.owner_hash is not None and len(profile_row.owner_hash) == 16
    assert profile_row.profile["id"] == c.profile.id

    (context_row,) = await c.env.rows(ProfileContextRow, ProfileContextRow.profile_id == c.profile.id)
    sources = {s["id"]: s for s in context_row.context["sources"]}
    assert set(sources) == {"resume", "link-1", "notes"}
    assert "Acme Labs" in sources["resume"]["content"]
    assert "design tokens" in sources["link-1"]["content"]
    assert set(context_row.context["readers"]) == {"resume", "link-1", "notes"}


async def test_the_spend_ledger_adds_up_to_the_call_logs(composite: Composite) -> None:
    c = composite
    llm = await c.env.rows(LlmCallRow)
    jev = await c.env.rows(JevCallRow)
    spend = await c.env.spend()

    anthropic_micro = sum(r.cost_micro_usd for r in llm)
    jev_micro = sum(r.cost_micro_usd for r in jev)
    assert anthropic_micro > 0 and jev_micro > 0
    assert spend["anthropic"] == (anthropic_micro, len(llm))
    assert spend["jev"] == (jev_micro, len(jev))
    assert spend["total"] == (anthropic_micro + jev_micro, len(llm) + len(jev))
    assert spend["total"][0] == spend["anthropic"][0] + spend["jev"][0]

    # the number the spend guard reads (`LimitRepo.spent_today`) is that same ledger, day by day
    limits = c.env.container.repos.limits
    days = {r.created_at.date() for r in [*llm, *jev]}
    assert sum([await limits.spent_today(day) for day in days]) == spend["total"][0]
