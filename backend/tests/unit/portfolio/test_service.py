"""`PortfolioService` over fakes: the generation stream, the build pipeline, the edits and the public reads."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
import structlog.testing

from tayyari.contract.portfolio import (
    GraphSpec,
    PortfolioDoneEvent,
    PortfolioDraftEvent,
    PortfolioErrorEvent,
    PortfolioEvent,
    PortfolioPatch,
    PortfolioPrivacy,
    PortfolioProgressEvent,
    SlotEdit,
    spec_problems,
)
from tayyari.core.errors import BadRequest, NotFound, ServiceUnavailable
from tayyari.core.redact import redact_event_dict
from tayyari.decisions.service import DecisionContext, FirstReadResult
from tayyari.llm.errors import ClaudeOverloaded, ClaudeTimeout, SpendCeilingReached
from tayyari.llm.types import CallContext
from tayyari.portfolio import service as service_module
from tayyari.portfolio.facts import usable_sources, verify_facts
from tayyari.portfolio.ground import SlotCheck
from tayyari.portfolio.service import (
    PAUSED_MESSAGE,
    PORTFOLIO_NOT_FOUND,
    PROFILE_NOT_FOUND,
    STEP_LABELS,
    BuildResult,
    PortfolioVersionConflict,
)
from tayyari.sample.provider import SampleProvider
from tayyari.storage.ports import PortfolioConflict, StoredPortfolio
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.decisions import FakeDecisionService
from tests.unit.portfolio._fixtures_a import scripted_facts
from tests.unit.portfolio._fixtures_c import (
    COPY_CALLS_PER_PAGE,
    REAL_STEPS,
    Rig,
    asked_slots,
    good_copy,
    is_copy_call,
    is_facts_call,
    is_reask,
    make_rig,
    script_copy,
    script_facts,
)


async def run(rig: Rig, *, regenerate: bool = False) -> list[PortfolioEvent]:
    return [e async for e in rig.service.generate(rig.profile_id, regenerate, rig.rc)]


def done_of(events: list[PortfolioEvent]) -> PortfolioDoneEvent:
    last = events[-1]
    assert isinstance(last, PortfolioDoneEvent), events[-1]
    return last


async def build(rig: Rig, *, offline: bool = False, existing: StoredPortfolio | None = None) -> BuildResult:
    return await rig.service.build(
        rig.profile,
        rig.ctx,
        profile_id=rig.profile_id,
        existing=existing,
        call_ctx=CallContext(purpose="portfolio", request_id="r", profile_id=rig.profile_id),
        decision_ctx=DecisionContext(rig.profile_id, None, False, "r", purpose_tag="portfolio"),
        offline=offline,
    )


def slot(portfolio: Any, slot_id: str) -> Any:
    return next((s for s in portfolio.content.slots if s.id == slot_id), None)


# ───────────────────────── the stream ─────────────────────────


async def test_a_generation_streams_four_steps_a_draft_and_the_saved_page(settings: Any) -> None:
    rig = await make_rig(settings)
    events = await run(rig)

    assert [e.type for e in events] == ["progress", "progress", "draft", "progress", "progress", "done"]
    steps = [e for e in events if isinstance(e, PortfolioProgressEvent)]
    assert [e.step for e in steps] == REAL_STEPS
    assert [e.label for e in steps] == [
        "Reading your resume",
        "Designing your page",
        "Writing your copy",
        "Checking every line against your sources",
    ]
    assert [e.label for e in steps] == [STEP_LABELS[s] for s in REAL_STEPS]

    draft = next(e for e in events if isinstance(e, PortfolioDraftEvent)).portfolio
    done = done_of(events).portfolio
    # the draft paints with the verbatim wording; the finished page carries the model's
    assert slot(draft, "aboutLede").origin == "resume"
    assert slot(done, "aboutLede").origin == "model"
    assert {s.origin for s in done.content.slots} == {"model"}

    assert done.profileId == rig.profile_id
    assert done.publicId != done.profileId and len(done.publicId) == 24
    assert (done.version, done.lookIndex, done.graphOutdated) == (1, 0, False)
    assert done.publish.state == "draft" and done.publish.defaultView == "standard"
    assert done.createdAt == "2026-10-07T12:00:00Z" == done.updatedAt
    assert 1 <= len(done.looks) <= 3 and all(spec_problems(look) == [] for look in done.looks)
    assert done.spec == done.looks[0] and done.spec is not done.looks[0]
    assert done.graph.asOf == "2026-10-07"
    assert [d.id for d in done.graph.domains] == [d.id for d in rig.profile.domains]

    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None and stored.version == 1 and not stored.published
    assert stored.public_id == done.publicId
    assert stored.facts_hash is not None
    assert [g["id"] for g in stored.generated] == [s.id for s in done.content.slots]
    assert "graphOutdated" not in stored.doc


async def test_each_source_is_read_once_and_the_copy_is_asked_in_three_parts(settings: Any) -> None:
    rig = await make_rig(settings)
    events = await run(rig)

    facts_calls = [c for c in rig.claude.structured_calls if is_facts_call(c)]
    copy_calls = [c for c in rig.claude.structured_calls if is_copy_call(c)]
    assert len(facts_calls) == len(usable_sources(rig.ctx)) > 1  # one Haiku call per source with text
    assert len(copy_calls) == COPY_CALLS_PER_PAGE == 3
    assert all(c.model == settings.reader_model and c.effort is None for c in facts_calls)
    assert all(c.model == settings.synth_model and c.effort == "low" for c in copy_calls)
    assert {c.ctx.purpose for c in rig.claude.structured_calls} == {"portfolio"}
    assert {c.ctx.profile_id for c in rig.claude.structured_calls} == {rig.profile_id}
    fixed, experience, projects = (asked_slots(c) for c in copy_calls)
    assert fixed == ["tagline", "aboutLede", "aboutBody", "pressHeadline", "pressDeck"]
    assert experience and all(s.startswith("exp:") for s in experience)
    assert projects and all(s.startswith("proj:") for s in projects)
    page = done_of(events).portfolio
    asked = [*fixed, *experience, *projects]
    assert [s.id for s in page.content.slots if s.id in set(asked)] == asked
    assert len(page.content.facts) == 20
    # each call is shown only the facts it needs: the per-fact calls never see the education or the quotes
    assert "edu-1" in copy_calls[0].user_text and "quote-1" in copy_calls[0].user_text
    for call in copy_calls[1:]:
        assert "edu-1" not in call.user_text and "quote-1" not in call.user_text
    assert "exp-1" in copy_calls[1].user_text and "proj-1" not in copy_calls[1].user_text
    assert "proj-1" in copy_calls[2].user_text and "exp-1" not in copy_calls[2].user_text


async def test_the_copy_calls_run_at_the_same_time(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    answer = good_copy("riya")
    arrived = 0
    both = asyncio.Event()

    async def copy(call: Any) -> dict[str, Any]:
        nonlocal arrived
        arrived += 1
        if arrived == COPY_CALLS_PER_PAGE:
            both.set()
        # one after the other, the first call would wait here for ever (and time out)
        await asyncio.wait_for(both.wait(), timeout=1)
        return answer(call)

    script_copy(claude, copy)
    result = await build(rig)

    assert len([c for c in claude.structured_calls if is_copy_call(c)]) == COPY_CALLS_PER_PAGE
    assert not result.report.copy_failed and result.report.fallbacks == 0
    assert result.report.slots_model == len(result.portfolio.content.slots) > 0


async def test_one_copy_call_failing_leaves_the_other_part_and_takes_the_fallback_for_its_slots(
    settings: Any,
) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    claude.script_structured(
        "portfolio", ClaudeOverloaded(), when=lambda c: is_copy_call(c) and "tagline" in asked_slots(c)
    )
    script_copy(claude, good_copy("riya"))

    result = await build(rig)
    report = result.report
    assert report.copy_failed and report.ungrounded == 0 and report.reasked == 0
    fixed = {"tagline", "aboutLede", "aboutBody", "pressHeadline", "pressDeck"}
    origins = {s.id: s.origin for s in result.portfolio.content.slots}
    assert {origins[i] for i in fixed if i in origins} == {"resume"}  # the part that failed
    assert {o for i, o in origins.items() if i not in fixed} == {"model"}  # the part that did not
    assert report.slots_model > 0 and report.fallbacks == len(fixed)


async def test_the_spend_ceiling_in_one_copy_call_stops_the_build(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    claude.script_structured(
        "portfolio",
        SpendCeilingReached(3600.0),
        when=lambda c: is_copy_call(c) and "tagline" in asked_slots(c),
    )
    script_copy(claude, good_copy("riya"))
    with pytest.raises(SpendCeilingReached):
        await build(rig)


async def test_jev_reads_the_resume_while_the_facts_are_extracted(settings: Any) -> None:
    decisions = FakeDecisionService(
        first_read=FirstReadResult(lines=[], signals=[], facts={"career_stage": "career_changer"})
    )
    rig = await make_rig(settings, decisions=decisions)
    await run(rig)

    ((inp, ctx),) = decisions.first_read_calls
    resume = next(s.content for s in rig.ctx.sources if s.kind == "resume")
    assert inp.resume_text == resume and inp.notes_text is None
    assert inp.today.isoformat() == "2026-10-07"
    assert ctx.profile_id == rig.profile_id and ctx.purpose_tag == "portfolio" and not ctx.is_sample
    assert decisions.copy_calls, "every generated sentence goes through the claim check"
    claims, copy_ctx = decisions.copy_calls[0]
    assert copy_ctx.purpose_tag == "portfolio" and all(c.corpus for c in claims)


async def test_a_failing_first_read_does_not_matter(settings: Any) -> None:
    decisions = FakeDecisionService().fail("first_read", RuntimeError("boom"))
    rig = await make_rig(settings, decisions=decisions)
    assert done_of(await run(rig)).portfolio.version == 1


async def test_an_existing_portfolio_is_returned_as_done_without_any_work(settings: Any) -> None:
    rig = await make_rig(settings)
    first = done_of(await run(rig)).portfolio
    calls = len(rig.claude.structured_calls)

    again = await run(rig)
    assert [e.type for e in again] == ["done"]
    page = done_of(again).portfolio
    assert (page.version, page.publicId, page.updatedAt) == (1, first.publicId, first.updatedAt)
    assert len(rig.claude.structured_calls) == calls
    assert page.graphOutdated is False


async def test_regenerating_keeps_the_public_id_and_reuses_the_facts(settings: Any) -> None:
    rig = await make_rig(settings)
    first = done_of(await run(rig)).portfolio
    rig.clock.advance(3600)

    events = await run(rig, regenerate=True)
    page = done_of(events).portfolio
    assert [e.type for e in events] == ["progress", "progress", "draft", "progress", "progress", "done"]
    assert page.version == 2 and page.publicId == first.publicId
    assert page.createdAt == first.createdAt and page.updatedAt != first.updatedAt
    facts_calls = [c for c in rig.claude.structured_calls if is_facts_call(c)]
    copy_calls = [c for c in rig.claude.structured_calls if is_copy_call(c)]
    # the facts came from the stored portfolio: only the first build read the sources
    assert (len(facts_calls), len(copy_calls)) == (len(usable_sources(rig.ctx)), 2 * COPY_CALLS_PER_PAGE)
    assert page.content.facts == first.content.facts


async def test_changed_source_text_means_the_facts_are_extracted_again(settings: Any) -> None:
    rig = await make_rig(settings)
    await run(rig)
    changed = rig.ctx.model_copy(deep=True)
    changed.sources[0].content += "\nOne more line."
    await rig.repos.profiles.save(
        rig.profile_id, rig.profile.to_dict(), changed, goal=None, owner_hash=None, is_sample=False
    )

    await run(rig, regenerate=True)
    # every source is read again (one call each), in both builds
    assert len([c for c in rig.claude.structured_calls if is_facts_call(c)]) == 2 * len(
        usable_sources(rig.ctx)
    )


async def test_regenerating_keeps_the_published_state_and_the_row_ids(settings: Any) -> None:
    rig = await make_rig(settings)
    first = done_of(await run(rig)).portfolio
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None
    await rig.repos.portfolios.put(
        dataclasses.replace(stored, published=True), expected_version=stored.version
    )

    page = done_of(await run(rig, regenerate=True)).portfolio
    assert page.publish.state == "published" and page.publicId == first.publicId and page.version == 3


# ───────────────────────── degrading ─────────────────────────


async def test_a_copy_failure_keeps_the_verbatim_page_and_is_still_done(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    claude.script_structured("portfolio", ClaudeOverloaded(), when=is_copy_call, repeat=True)

    events = await run(rig)
    page = done_of(events).portfolio
    assert [e.type for e in events][-1] == "done" and not any(
        isinstance(e, PortfolioErrorEvent) for e in events
    )
    assert page.content.slots and {s.origin for s in page.content.slots} == {"resume"}
    assert slot(page, "tagline").text.startswith("I care about motion")
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None and [g["origin"] for g in stored.generated] == ["resume"] * len(
        page.content.slots
    )


async def test_a_copy_timeout_is_a_failure_too(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    claude.script_structured("portfolio", ClaudeTimeout(), when=is_copy_call, repeat=True)

    result = await build(rig)
    assert result.report.copy_failed and result.report.slots_model == 0
    assert result.report.slots_resume == len(result.portfolio.content.slots) > 0
    assert result.report.fallbacks == result.report.slots_resume + result.report.slots_missing
    assert result.report.ungrounded == 0


async def test_a_facts_failure_builds_the_page_from_the_profile_alone(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    claude.script_structured("portfolio", ClaudeOverloaded(), when=is_facts_call, repeat=True)

    events = await run(rig)
    page = done_of(events).portfolio
    assert page.content.facts == [] and not any(
        is_copy_call(c) for c in claude.structured_calls
    )  # no copy call
    assert len(claude.structured_calls) == len(usable_sources(rig.ctx))  # one failed call per source
    assert slot(page, "pressHeadline").text == "Riya Kapoor, Frontend Engineer"
    assert page.looks and page.graph.domains
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None and stored.facts_hash is None  # the next regenerate tries the extraction again


async def test_the_spend_ceiling_ends_the_stream_with_a_calm_error_and_saves_nothing(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    claude.script_structured("portfolio", SpendCeilingReached(3600.0), repeat=True)

    events = await run(rig)
    assert [e.type for e in events] == ["progress", "error"]
    error = events[-1]
    assert isinstance(error, PortfolioErrorEvent) and error.message == PAUSED_MESSAGE
    assert error.message.startswith("Portfolio generation is paused for today")
    assert await rig.repos.portfolios.get(rig.profile_id) is None


async def test_an_unknown_profile_is_an_error_event(settings: Any) -> None:
    rig = await make_rig(settings, store=False)
    events = await run(rig)
    assert len(events) == 1 and isinstance(events[0], PortfolioErrorEvent)
    assert events[0].message == PROFILE_NOT_FOUND
    assert rig.claude.structured_calls == []


async def test_the_bundled_sample_persona_works_without_a_stored_profile(settings: Any) -> None:
    sample = SampleProvider()
    sample_id = sample.sample_profile_id()
    assert sample_id is not None
    rig = await make_rig(settings, store=False, profile_id=sample_id, script=False)
    script_facts(rig.claude, sample.sample_context)
    script_copy(rig.claude, good_copy("riya"))

    page = done_of(await run(rig)).portfolio
    assert page.profileId == sample_id and page.content.person.name == "Riya Kapoor"
    assert rig.decisions.first_read_calls[0][1].is_sample


async def test_a_page_whose_document_cannot_be_read_is_built_again(settings: Any) -> None:
    rig = await make_rig(settings)
    first = done_of(await run(rig)).portfolio
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None
    await rig.repos.portfolios.put(dataclasses.replace(stored, doc={"nonsense": True}), expected_version=None)

    page = done_of(await run(rig)).portfolio
    assert page.publicId == first.publicId and page.content.slots


async def test_closing_the_stream_early_cancels_the_work(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)

    async def never(_call: Any) -> Any:
        await asyncio.sleep(60)

    claude.script_structured("portfolio", never, repeat=True)
    stream = rig.service.generate(rig.profile_id, False, rig.rc)
    first = await anext(stream)
    assert isinstance(first, PortfolioProgressEvent) and first.step == "facts"
    await stream.aclose()

    assert rig.service._workers == set()
    assert await rig.repos.portfolios.get(rig.profile_id) is None


async def test_aclose_cancels_a_running_generation(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)

    async def never(_call: Any) -> Any:
        await asyncio.sleep(60)

    claude.script_structured("portfolio", never, repeat=True)
    stream = rig.service.generate(rig.profile_id, False, rig.rc)
    await anext(stream)
    assert len(rig.service._workers) == 1
    await rig.service.aclose()
    events = [e async for e in stream]
    assert rig.service._workers == set() and all(e.type != "done" for e in events)


# ───────────────────────── the pipeline ─────────────────────────


async def test_a_slot_that_fails_the_check_is_asked_again_with_the_notes(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)

    def copy(call: Any) -> dict[str, Any]:
        if is_reask(call):
            return good_copy("riya", {"tagline": "I care about motion and design systems."})(call)
        return good_copy("riya", {"tagline": "Shipped 9000 projects at Google."})(call)

    script_copy(claude, copy)
    result = await build(rig)

    calls = [c for c in claude.structured_calls if is_copy_call(c)]
    page_calls, again = calls[:COPY_CALLS_PER_PAGE], calls[COPY_CALLS_PER_PAGE:]
    assert len(calls) == COPY_CALLS_PER_PAGE + 1 and not any(is_reask(c) for c in page_calls)
    assert len(again) == 1 and is_reask(again[0])
    assert asked_slots(again[0]) == ["tagline"]  # ONE call, for the failed slot only
    assert "9000" in again[0].user_text and "tagline" in again[0].user_text  # the checker's notes
    tagline = slot(result.portfolio, "tagline")
    assert tagline.origin == "model" and tagline.text == "I care about motion and design systems."
    report = result.report
    assert (report.reasked, report.fallbacks, report.ungrounded, report.copy_failed) == (1, 0, 0, False)
    assert report.slots_resume == 0 and report.slots_model == len(result.portfolio.content.slots)
    assert result.generated == result.portfolio.content.slots


async def test_a_slot_that_fails_twice_takes_the_fallback_wording(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    script_copy(claude, good_copy("riya", {"tagline": "Shipped 9000 projects at Google."}))

    result = await build(rig)
    # the page (three concurrent calls), then ONE re-ask
    assert len([c for c in claude.structured_calls if is_copy_call(c)]) == COPY_CALLS_PER_PAGE + 1
    tagline = slot(result.portfolio, "tagline")
    assert tagline.origin == "resume" and tagline.text.startswith("I care about motion")
    assert tagline.factIds == ["quote-1"]
    report = result.report
    assert (report.reasked, report.fallbacks, report.ungrounded) == (1, 1, 0)
    assert report.slots_resume == 1 and report.slots_model == len(result.portfolio.content.slots) - 1


async def test_the_claim_check_of_jev_sends_a_slot_to_the_re_ask_and_then_the_fallback(settings: Any) -> None:
    decisions = FakeDecisionService(copy={"aboutBody": False})
    rig = await make_rig(settings, decisions=decisions)

    result = await build(rig)
    assert slot(result.portfolio, "aboutBody").origin == "resume"
    assert result.report.reasked == 1 and result.report.fallbacks == 1
    reask = [c for c in rig.claude.structured_calls if is_copy_call(c)][COPY_CALLS_PER_PAGE]
    assert is_reask(reask) and "is not supported" in reask.user_text


async def test_empty_and_missing_model_slots_are_filled_from_the_fallback(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)

    def copy(call: Any) -> dict[str, Any]:
        answer = good_copy("riya", {"aboutLede": ""})(call)
        answer["slots"] = [row for row in answer["slots"] if row["id"] != "pressDeck"]
        return answer

    script_copy(claude, copy)
    result = await build(rig)

    # nothing failed: no re-ask
    assert [c for c in claude.structured_calls if is_copy_call(c)][COPY_CALLS_PER_PAGE:] == []
    assert slot(result.portfolio, "aboutLede").origin == "resume"
    assert slot(result.portfolio, "pressDeck").origin == "resume"
    assert result.report.fallbacks == 2 and result.report.reasked == 0


async def test_a_slot_with_no_wording_at_all_is_left_out(settings: Any) -> None:
    rig = await make_rig(settings, "aarav")
    result = await build(rig)
    ids = [s.id for s in result.portfolio.content.slots]
    assert "aboutBody" not in ids and "tagline" in ids
    assert result.report.slots_missing >= 1
    assert all(s.text.strip() for s in result.portfolio.content.slots)


async def test_the_last_check_leaves_off_anything_that_does_not_ground(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_rig(settings)
    real = service_module.check_slot

    def strict(slot_: Any, facts: Any, **kw: Any) -> SlotCheck:
        if slot_.id == "tagline":
            return SlotCheck("tagline", False, ["planted"], [])
        return real(slot_, facts, **kw)

    monkeypatch.setattr(service_module, "check_slot", strict)
    result = await build(rig)
    assert result.report.ungrounded == 1
    assert slot(result.portfolio, "tagline") is None


async def test_the_report_counts_what_the_checks_dropped(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    answer = scripted_facts(rig.ctx)
    baseline = len([d for d in verify_facts(answer, rig.ctx).dropped if d.part == "fact"])
    answer["facts"].append(
        {
            "kind": "experience",
            "sourceId": "resume",
            "line": 9999,  # a line the resume does not have
            "title": "Chief Wizard",
            "org": "Hogwarts",
            "highlightLines": [9999],
            "tags": [],
        }
    )
    claude.script_structured("portfolio", answer, when=is_facts_call, repeat=True)
    script_copy(claude, good_copy("riya"))

    result = await build(rig)
    report = result.report
    assert report.facts_kept == 20 and report.facts_dropped == baseline + 1
    assert any(d.title.startswith("Chief Wizard") for d in report.dropped)
    assert not report.facts_reused and not report.facts_failed
    assert result.facts_hash is not None


async def test_an_offline_build_calls_no_model_at_all(settings: Any) -> None:
    rig = await make_rig(settings)
    result = await build(rig, offline=True)
    assert rig.claude.structured_calls == [] and rig.decisions.calls["first_read"] == 0
    assert result.facts_hash is None and result.portfolio.content.facts == []
    assert {s.origin for s in result.portfolio.content.slots} == {"resume"}
    assert result.report.facts_kept == 0 and result.report.ungrounded == 0
    assert result.portfolio.looks and spec_problems(result.portfolio.spec) == []


async def test_a_build_does_not_touch_the_repository(settings: Any) -> None:
    rig = await make_rig(settings)
    result = await build(rig)
    assert await rig.repos.portfolios.get(rig.profile_id) is None
    assert result.portfolio.version == 1


# ───────────────────────── reads and edits ─────────────────────────


async def test_get_is_none_until_a_page_exists(settings: Any) -> None:
    rig = await make_rig(settings)
    assert await rig.service.get(rig.profile_id) is None
    await run(rig)
    page = await rig.service.get(rig.profile_id)
    assert page is not None and page.graphOutdated is False


async def test_the_graph_is_outdated_when_the_profile_moved_on(settings: Any) -> None:
    rig = await make_rig(settings)
    await run(rig)
    moved = rig.profile.model_copy(deep=True)
    moved.domains[0].score = max(0, moved.domains[0].score - 7)
    await rig.repos.profiles.save(
        rig.profile_id, moved.to_dict(), rig.ctx, goal=None, owner_hash=None, is_sample=False
    )

    page = await rig.service.get(rig.profile_id)
    assert page is not None and page.graphOutdated is True
    assert done_of(await run(rig)).portfolio.graphOutdated is True  # also on the existing-page path


async def test_patch_saves_a_user_edit_and_bumps_the_version(settings: Any) -> None:
    rig = await make_rig(settings)
    first = done_of(await run(rig)).portfolio
    rig.clock.advance(60)

    page = await rig.service.patch(
        rig.profile_id, PortfolioPatch(slots=[SlotEdit(id="tagline", text="Hello, world.")]), rig.rc
    )
    tagline = slot(page, "tagline")
    assert (tagline.text, tagline.origin, tagline.factIds) == ("Hello, world.", "user", [])
    assert page.version == 2 and page.updatedAt > first.updatedAt and page.publicId == first.publicId
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None
    assert next(g for g in stored.generated if g["id"] == "tagline")["origin"] == "model"  # untouched

    back = await rig.service.patch(rig.profile_id, PortfolioPatch(resetSlots=["tagline"]), rig.rc)
    assert slot(back, "tagline").origin == "model" and back.version == 3


async def test_patch_validation_errors_come_through_and_save_nothing(settings: Any) -> None:
    rig = await make_rig(settings)
    await run(rig)
    for bad in (
        PortfolioPatch(slots=[SlotEdit(id="tagline", text="<b>x</b>")]),
        PortfolioPatch(slots=[SlotEdit(id="mystery", text="x")]),
        PortfolioPatch(slots=[SlotEdit(id="tagline", text="x" * 91)]),
    ):
        with pytest.raises(BadRequest):
            await rig.service.patch(rig.profile_id, bad, rig.rc)
    with pytest.raises(NotFound):
        await rig.service.patch("f" * 32, PortfolioPatch(), rig.rc)
    page = await rig.service.get(rig.profile_id)
    assert page is not None and page.version == 1


async def test_a_lost_race_is_a_409(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = await make_rig(settings)
    await run(rig)

    async def lost(rec: StoredPortfolio, *, expected_version: int | None) -> StoredPortfolio:
        raise PortfolioConflict("someone saved first")

    monkeypatch.setattr(rig.repos.portfolios, "put", lost)
    with pytest.raises(PortfolioVersionConflict) as caught:
        await rig.service.patch(rig.profile_id, PortfolioPatch(defaultView="press"), rig.rc)
    assert caught.value.status == 409
    assert "changed somewhere else" in caught.value.user_message


async def test_refreshing_the_graph_takes_a_new_snapshot(settings: Any) -> None:
    rig = await make_rig(settings)
    first = done_of(await run(rig)).portfolio
    hidden = first.graph.domains[0].id
    await rig.service.patch(
        rig.profile_id,
        PortfolioPatch(graph=first.spec.graph.model_copy(update={"hiddenDomainIds": [hidden]})),
        rig.rc,
    )
    moved = rig.profile.model_copy(deep=True)
    moved.domains[1].score = max(0, moved.domains[1].score - 9)
    rig.clock.advance(86_400)
    await rig.repos.profiles.save(
        rig.profile_id, moved.to_dict(), rig.ctx, goal=None, owner_hash=None, is_sample=False
    )

    page = await rig.service.patch(rig.profile_id, PortfolioPatch(refreshGraph=True), rig.rc)
    assert page.graph.domains[1].score == moved.domains[1].score != first.graph.domains[1].score
    assert page.graph.asOf == "2026-10-08" and page.graphOutdated is False
    assert page.spec.graph.hiddenDomainIds == [hidden]  # the owner's choice survives
    assert page.content.slots == first.content.slots


async def test_refreshing_the_graph_needs_the_profile(settings: Any) -> None:
    rig = await make_rig(settings)
    await run(rig)
    rig.repos._state.profiles.clear()
    with pytest.raises(BadRequest, match="no longer available"):
        await rig.service.patch(rig.profile_id, PortfolioPatch(refreshGraph=True), rig.rc)
    page = await rig.service.get(rig.profile_id)
    assert page is not None and page.graphOutdated is None  # unknown without a profile


async def test_rewrite_replaces_one_slot_in_the_page_and_in_the_generated_wording(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    script_copy(claude, good_copy("riya"), repeat=False)
    first = done_of(await run(rig)).portfolio
    await rig.service.patch(
        rig.profile_id, PortfolioPatch(slots=[SlotEdit(id="tagline", text="Edited by hand.")]), rig.rc
    )
    script_copy(claude, good_copy("riya", {"tagline": "I care about motion and design systems."}))

    page = await rig.service.rewrite(rig.profile_id, "tagline", rig.rc)
    tagline = slot(page, "tagline")
    assert tagline.origin == "model" and tagline.text == "I care about motion and design systems."
    assert page.version == 3
    assert slot(page, "aboutLede") == slot(first, "aboutLede")
    last = [c for c in claude.structured_calls if is_copy_call(c)][-1]
    assert asked_slots(last) == ["tagline"] and last.ctx.request_id == "req-1"

    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None
    assert next(g for g in stored.generated if g["id"] == "tagline")["text"] == tagline.text


async def test_rewrite_falls_back_after_one_re_ask(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    script_copy(claude, good_copy("riya"), repeat=False)
    await run(rig)
    script_copy(claude, good_copy("riya", {"aboutLede": "Founded a unicorn in 1999."}))

    page = await rig.service.rewrite(rig.profile_id, "aboutLede", rig.rc)
    assert slot(page, "aboutLede").origin == "resume"
    # the page (three calls), the rewrite, one re-ask
    assert len([c for c in claude.structured_calls if is_copy_call(c)]) == COPY_CALLS_PER_PAGE + 2


async def test_rewrite_reports_a_model_failure_as_unavailable(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    script_copy(claude, good_copy("riya"), repeat=False)
    await run(rig)
    claude.script_structured("portfolio", ClaudeOverloaded(), when=is_copy_call, repeat=True)

    with pytest.raises(ServiceUnavailable) as caught:
        await rig.service.rewrite(rig.profile_id, "tagline", rig.rc)
    assert "busy" in caught.value.user_message
    page = await rig.service.get(rig.profile_id)
    assert page is not None and page.version == 1  # nothing was saved


async def test_rewrite_refuses_what_it_cannot_write(settings: Any) -> None:
    rig = await make_rig(settings)
    await run(rig)
    for bad in ("nonsense", "exp:nope", "proj:exp-1", "exp:"):
        with pytest.raises(BadRequest):
            await rig.service.rewrite(rig.profile_id, bad, rig.rc)
    with pytest.raises(NotFound):
        await rig.service.rewrite("f" * 32, "tagline", rig.rc)


async def test_rewrite_has_nothing_to_write_from_without_facts(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    claude.script_structured("portfolio", ClaudeOverloaded(), repeat=True)
    done_of(await run(rig))  # built from the profile alone
    with pytest.raises(BadRequest, match="no resume details"):
        await rig.service.rewrite(rig.profile_id, "tagline", rig.rc)


# ───────────────────────── the public page ─────────────────────────


async def test_public_is_none_until_the_page_is_published(settings: Any) -> None:
    rig = await make_rig(settings)
    page = done_of(await run(rig)).portfolio
    assert await rig.service.public(page.publicId) is None
    assert await rig.service.public("unknown") is None

    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None
    await rig.repos.portfolios.put(
        dataclasses.replace(stored, published=True), expected_version=stored.version
    )
    public = await rig.service.public(page.publicId)
    assert public is not None and public.publicId == page.publicId
    text = public.model_dump_json()
    assert rig.profile_id not in text and "profileId" not in text
    assert public.graph.domains and all(s.score >= 45 for d in public.graph.domains for s in d.skills)


async def test_the_preview_is_the_public_view_of_an_unpublished_page(settings: Any) -> None:
    rig = await make_rig(settings)
    page = done_of(await run(rig)).portfolio
    preview = await rig.service.public_preview(rig.profile_id)
    assert preview.publicId == page.publicId
    assert rig.profile_id not in preview.model_dump_json()
    with pytest.raises(NotFound, match=PORTFOLIO_NOT_FOUND):
        await rig.service.public_preview("f" * 32)


# ───────────────────────── a regenerate keeps what the owner decided ─────────────────────────


async def choose_privately(rig: Rig, page: Any) -> Any:
    """The owner switches things on and hides things: every choice a rebuild must not undo."""
    blocks = [b.model_copy() for b in page.spec.blocks]
    for b in blocks:
        if b.kind == "projects":
            b.public = False
        if b.kind == "education":
            b.visible = False
    domain = page.graph.domains[0].id
    patch = PortfolioPatch(
        privacy=PortfolioPrivacy(email=True, phone=False, grades=True),
        blocks=blocks,
        graph=GraphSpec(hiddenDomainIds=[domain], stats=False),
        defaultView="press",
    )
    return await rig.service.patch(rig.profile_id, patch, rig.rc)


def choices(page: Any) -> Any:
    return (
        page.spec.privacy,
        [(b.id, b.visible, b.public) for b in page.spec.blocks],
        page.spec.graph.hiddenDomainIds,
        page.publish.defaultView,
    )


async def test_regenerating_keeps_the_owners_privacy_choices(settings: Any) -> None:
    rig = await make_rig(settings)
    first = done_of(await run(rig)).portfolio
    edited = await choose_privately(rig, first)
    assert edited.spec.privacy.email and edited.spec.graph.hiddenDomainIds
    assert any(not (b.visible and b.public) for b in edited.spec.blocks)

    again = done_of(await run(rig, regenerate=True)).portfolio
    assert again.version == edited.version + 1
    assert choices(again) == choices(edited)
    # every look carries them, so switching looks cannot undo them either
    for look in again.looks:
        assert look.privacy == edited.spec.privacy
        assert look.graph.hiddenDomainIds == edited.spec.graph.hiddenDomainIds
        owner = {b.id: (b.visible, b.public) for b in edited.spec.blocks}
        shared = {b.id: (b.visible, b.public) for b in look.blocks if b.id in owner}
        assert shared and all(owner[i] == flags for i, flags in shared.items())
    hero = next(b for b in again.spec.blocks if b.kind == "hero")
    assert hero.visible and hero.public


async def test_the_draft_of_a_regenerate_already_carries_the_choices(settings: Any) -> None:
    rig = await make_rig(settings)
    edited = await choose_privately(rig, done_of(await run(rig)).portfolio)
    events = await run(rig, regenerate=True)
    draft = next(e for e in events if isinstance(e, PortfolioDraftEvent)).portfolio
    assert draft.spec.privacy == edited.spec.privacy
    assert draft.spec.graph.hiddenDomainIds == edited.spec.graph.hiddenDomainIds


async def test_graph_areas_that_no_longer_exist_are_forgotten_on_regenerate(settings: Any) -> None:
    rig = await make_rig(settings)
    first = done_of(await run(rig)).portfolio
    edited = await choose_privately(rig, first)
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None
    doc = dict(stored.doc)
    spec = dict(doc["spec"])
    spec["graph"] = {**spec["graph"], "hiddenDomainIds": [*edited.spec.graph.hiddenDomainIds, "gone-area"]}
    doc["spec"] = spec
    await rig.repos.portfolios.put(dataclasses.replace(stored, doc=doc), expected_version=stored.version)

    again = done_of(await run(rig, regenerate=True)).portfolio
    assert again.spec.graph.hiddenDomainIds == edited.spec.graph.hiddenDomainIds


class Hook:
    """What the owner does while the model works: runs once, during the next copy call."""

    def __init__(self) -> None:
        self.action: Any = None

    async def fire(self) -> None:
        action, self.action = self.action, None
        if action is not None:
            await action()


async def hooked_rig(settings: Any) -> tuple[Rig, Hook]:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    script_facts(claude, rig.ctx)
    answer = good_copy("riya")
    hook = Hook()

    async def copy(call: Any) -> dict[str, Any]:
        await hook.fire()
        return answer(call)

    script_copy(claude, copy)
    return rig, hook


async def set_published(rig: Rig, published: bool) -> StoredPortfolio:
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None
    return await rig.repos.portfolios.put(
        dataclasses.replace(stored, published=published), expected_version=stored.version
    )


async def test_a_page_published_while_it_was_being_rebuilt_stays_published(settings: Any) -> None:
    rig, hook = await hooked_rig(settings)
    first = done_of(await run(rig)).portfolio

    hook.action = lambda: set_published(rig, True)
    events = await run(rig, regenerate=True)
    page = done_of(events).portfolio
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None and stored.published and page.publish.state == "published"
    assert page.publicId == first.publicId
    assert await rig.service.public(first.publicId) is not None


async def test_a_page_unpublished_while_it_was_being_rebuilt_is_not_published_again(settings: Any) -> None:
    rig, hook = await hooked_rig(settings)
    first = done_of(await run(rig)).portfolio
    await set_published(rig, True)

    hook.action = lambda: set_published(rig, False)
    events = await run(rig, regenerate=True)
    page = done_of(events).portfolio
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert (
        stored is not None and not stored.published
    )  # the stale "published" read at the start is not written back
    assert page.publish.state == "draft"
    assert await rig.service.public(first.publicId) is None
    assert stored.doc["publish"]["state"] == "draft"


async def test_a_privacy_switch_flipped_while_it_was_being_rebuilt_survives(settings: Any) -> None:
    rig, hook = await hooked_rig(settings)
    done_of(await run(rig))

    async def owner_edits() -> None:
        await rig.service.patch(
            rig.profile_id,
            PortfolioPatch(privacy=PortfolioPrivacy(email=True, phone=True, grades=False)),
            rig.rc,
        )

    hook.action = owner_edits
    page = done_of(await run(rig, regenerate=True)).portfolio
    assert page.spec.privacy == PortfolioPrivacy(email=True, phone=True, grades=False)


async def test_a_page_deleted_while_it_was_being_rebuilt_is_not_brought_back(settings: Any) -> None:
    rig, hook = await hooked_rig(settings)
    done_of(await run(rig))

    hook.action = lambda: rig.service.delete(rig.profile_id)
    events = await run(rig, regenerate=True)
    last = events[-1]
    assert isinstance(last, PortfolioErrorEvent) and last.message == service_module.DELETED_MEANWHILE
    assert await rig.repos.portfolios.get(rig.profile_id) is None


# ───────────────────────── reading, deleting ─────────────────────────


async def test_has_readable_is_true_only_for_a_page_that_can_be_read_back(settings: Any) -> None:
    rig = await make_rig(settings)
    assert not await rig.service.has_readable(rig.profile_id)
    done_of(await run(rig))
    assert await rig.service.has_readable(rig.profile_id)

    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None
    await rig.repos.portfolios.put(
        dataclasses.replace(stored, doc={"spec": "not a portfolio"}), expected_version=stored.version
    )
    assert not await rig.service.has_readable(rig.profile_id)  # it would be rebuilt, so it is not free
    assert await rig.service.get(rig.profile_id) is None


async def test_delete_removes_the_page_and_the_public_link(settings: Any) -> None:
    rig = await make_rig(settings)
    page = done_of(await run(rig)).portfolio
    await set_published(rig, True)
    assert await rig.service.public(page.publicId) is not None

    assert await rig.service.delete(rig.profile_id) is True
    assert await rig.service.get(rig.profile_id) is None
    assert await rig.service.public(page.publicId) is None
    assert await rig.repos.portfolios.get(rig.profile_id) is None
    assert await rig.service.delete(rig.profile_id) is False  # nothing left to remove


# ───────────────────────── an incomplete extraction is not cached ─────────────────────────


async def test_facts_read_from_only_some_sources_are_not_cached(settings: Any) -> None:
    claude = FakeClaudeClient()
    rig = await make_rig(settings, claude=claude, script=False)
    sources = usable_sources(rig.ctx)
    lost = next(s.id for s in sources if s.kind != "resume")
    claude.script_structured(
        "portfolio",
        ClaudeOverloaded(),
        when=lambda c: is_facts_call(c) and f'<source id="{lost}"' in c.user_text,
        repeat=True,
    )
    script_facts(claude, rig.ctx)
    script_copy(claude, good_copy("riya"))

    result = await build(rig)
    assert result.report.facts_partial and not result.report.facts_failed
    assert result.portfolio.content.facts  # the sources that were read still make a page
    assert result.facts_hash is None

    events = await run(rig)
    assert done_of(events).portfolio.content.facts
    stored = await rig.repos.portfolios.get(rig.profile_id)
    assert stored is not None and stored.facts_hash is None

    # so the next build reads every source again instead of reusing the incomplete result
    before = len([c for c in claude.structured_calls if is_facts_call(c)])
    await run(rig, regenerate=True)
    after = len([c for c in claude.structured_calls if is_facts_call(c)])
    assert after - before == len(sources)


async def test_a_complete_extraction_is_cached(settings: Any) -> None:
    rig = await make_rig(settings)
    result = await build(rig)
    assert not result.report.facts_partial and result.facts_hash is not None


# ───────────────────────── logs ─────────────────────────


async def test_logs_carry_the_profile_id_only_under_the_key_the_redactor_shortens(settings: Any) -> None:
    rig = await make_rig(settings)
    pid = rig.profile_id
    with structlog.testing.capture_logs() as logs:
        done_of(await run(rig))
        await rig.service.patch(pid, PortfolioPatch(defaultView="press"), rig.rc)
        await rig.service.rewrite(pid, "tagline", rig.rc)
        await rig.service.delete(pid)

    assert [e["event"] for e in logs if "profile_id" in e]
    for event in logs:
        assert all(pid not in str(v) for k, v in event.items() if k != "profile_id"), event
        shortened = redact_event_dict(None, "info", dict(event))
        assert pid not in str(shortened), shortened
