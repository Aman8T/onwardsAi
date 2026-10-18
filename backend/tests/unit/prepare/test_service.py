"""`PrepareService` over the fakes: the stream's order (gaps at once, standing and moves as each lands, done with the
stored plan), what a failed call does to it (standing: bare gaps; moves: ``no_moves``), the stored plan handed back instead
of a new call, the state's compare-and-swap, the repo ranking, the headline, and the profile loader that seeds the bundled
sample. Nothing calls the network."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tayyari.contract.prepare import (
    PrepareDoneEvent,
    PrepareErrorEvent,
    PrepareEvent,
    PrepareGapsEvent,
    PrepareLink,
    PrepareMovesEvent,
    PreparePlan,
    PrepareStandingEvent,
    PrepareState,
    PrepareStateUpdate,
)
from tayyari.contract.stored import StoredContext
from tayyari.llm.errors import ClaudeInvalidOutput, ClaudeOverloaded, SpendCeilingReached
from tayyari.prepare.errors import NO_MOVES_CODE, BadState, PlanChanged, PlanNotFound
from tayyari.prepare.service import check_state, new_plan_id, plan_of
from tayyari.prepare.verify import HEADLINE_UNAVAILABLE_NOTE
from tayyari.sample.provider import SampleProvider
from tayyari.storage.ports import StoredPlan
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.fakes.repos import FakeRepositories
from tests.unit.prepare.support import (
    CTX,
    MODEL,
    NOW,
    PLAN_ID,
    ROLE,
    js,
    make_service,
    moves_answer,
    plan_for,
    request_for,
    riya,
    script_good,
    script_riya,
    standing_answer,
)


async def collect(events: Any) -> list[PrepareEvent]:
    return [e async for e in events]


def kinds(events: list[PrepareEvent]) -> list[str]:
    return [e.type for e in events]


async def seeded() -> FakeRepositories:
    """Fakes with Riya's profile stored (a plan's row refers to its profile)."""
    profile, context = riya()
    repos = FakeRepositories()
    await repos.profiles.save(
        profile.id, profile.to_dict(), context, goal=profile.goal, owner_hash=None, is_sample=True
    )
    return repos


# ───────────────────────── the stream ─────────────────────────


async def test_the_stream_is_gaps_then_both_calls_then_done_with_the_stored_plan() -> None:
    fake = script_riya(FakeClaudeClient())
    service, store, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    plan = plan_for()
    events = await collect(service.run(profile, context, plan, CTX))

    assert kinds(events) == ["gaps", "standing", "moves", "done"] or kinds(events) == [
        "gaps",
        "moves",
        "standing",
        "done",
    ]
    first = events[0]
    assert isinstance(first, PrepareGapsEvent)
    assert (
        first.plan.moves == [] and first.plan.headline is None and all(g.why is None for g in first.plan.gaps)
    )
    standing = next(e for e in events if isinstance(e, PrepareStandingEvent))
    assert standing.headline is not None and sum(1 for g in standing.gaps if g.why) == 5
    moves = next(e for e in events if isinstance(e, PrepareMovesEvent))
    assert [m.id for m in moves.moves] == ["m1", "m2", "m3"]
    done = events[-1]
    assert isinstance(done, PrepareDoneEvent)
    final = done.plan
    assert final.id == PLAN_ID and final.headline == standing.headline and final.moves == moves.moves
    assert final.gaps == standing.gaps and final.version == 1
    assert final.state.to_dict() == {"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []}

    stored = store.rows[PLAN_ID]
    assert (stored.profile_id, stored.role_id, stored.requirement, stored.version) == (
        profile.id,
        ROLE,
        None,
        1,
    )
    assert "state" not in stored.content and "version" not in stored.content
    assert plan_of(stored) == final
    assert await service.get(profile.id, PLAN_ID) == final


def _delayed(slow: str, started: list[str]) -> Any:
    """A responder that answers both calls, the ``slow`` one after a pause, recording the order they started in."""

    def answer(call: StructuredCall) -> Any:
        started.append(call.purpose)
        responder = standing_answer if call.purpose == "prepare.standing" else moves_answer

        async def late() -> dict[str, Any]:
            if call.purpose == slow:
                await asyncio.sleep(0.05)
            return responder(call)

        return late()

    return answer


@pytest.mark.parametrize("slow", ["prepare.standing", "prepare.moves"])
async def test_both_calls_run_at_the_same_time_and_each_lands_in_its_own_order(slow: str) -> None:
    profile, context = riya()
    fake = FakeClaudeClient()
    started: list[str] = []
    fake.script_structured("prepare.standing", _delayed(slow, started), repeat=True)
    fake.script_structured("prepare.moves", _delayed(slow, started), repeat=True)
    service, _, _ = make_service(fake, repos=await seeded())
    events = await collect(service.run(profile, context, plan_for(), CTX))
    expected = ["moves", "standing"] if slow == "prepare.standing" else ["standing", "moves"]
    assert kinds(events) == ["gaps", *expected, "done"]
    assert started[:2] == ["prepare.standing", "prepare.moves"]  # both were started before either answered


async def test_the_calls_carry_the_purposes_the_model_and_the_person() -> None:
    fake = script_riya(FakeClaudeClient())
    service, _, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    await collect(service.run(profile, context, plan_for(), CTX))
    purposes = sorted(c.purpose for c in fake.structured_calls)
    assert purposes == ["prepare.moves", "prepare.standing"]
    for call in fake.structured_calls:
        assert call.model == MODEL and call.effort is None
        assert call.ctx.profile_id == CTX.profile_id and call.ctx.request_id == "req-1"
        assert "<evidence>" in call.user_text and "e37" in call.user_text
    moves_call = fake.calls_for("prepare.moves")[0]
    assert (
        "<readers>" in moves_call.user_text
        and "<readers>" not in fake.calls_for("prepare.standing")[0].user_text
    )
    assert moves_call.max_tokens > fake.calls_for("prepare.standing")[0].max_tokens


async def test_the_effort_setting_reaches_the_calls() -> None:
    fake = script_riya(FakeClaudeClient())
    repos = await seeded()
    from tayyari.prepare.service import PrepareService

    service = PrepareService(
        claude=fake,
        model="claude-sonnet-5-5",
        effort="medium",
        repo=repos.prepare,
        profiles=repos.profiles,
        sample=SampleProvider(),
        clock=make_service(fake)[2],
    )
    profile, context = riya()
    await collect(service.run(profile, context, plan_for(), CTX))
    assert {(c.model, c.effort) for c in fake.structured_calls} == {("claude-sonnet-5-5", "medium")}


async def test_a_failed_standing_call_leaves_the_gaps_with_their_numbers_alone() -> None:
    fake = FakeClaudeClient()
    fake.script_structured("prepare.standing", ClaudeOverloaded())
    fake.script_structured("prepare.moves", js("riya.moves.answer.json"))
    service, store, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    events = await collect(service.run(profile, context, plan_for(), CTX))
    assert kinds(events) == ["gaps", "moves", "done"]
    done = events[-1]
    assert isinstance(done, PrepareDoneEvent)
    assert done.plan.headline is None and all(g.why is None for g in done.plan.gaps)
    assert [g.current for g in done.plan.gaps] == [12, 32, 38, 40, 52]
    assert len(done.plan.moves) == 3 and PLAN_ID in store.rows


async def test_a_late_standing_call_is_given_up_on_and_the_plan_still_lands() -> None:
    fake = FakeClaudeClient()

    async def never(call: StructuredCall) -> dict[str, Any]:
        await asyncio.sleep(5)
        return standing_answer(call)

    fake.script_structured("prepare.standing", never)
    fake.script_structured("prepare.moves", moves_answer)
    service, _, _ = make_service(fake, repos=await seeded(), timeout=0.05)
    profile, context = riya()
    events = await collect(service.run(profile, context, plan_for(), CTX))
    assert kinds(events) == ["gaps", "moves", "done"]


async def test_a_failed_moves_call_ends_the_stream_with_no_moves_and_stores_nothing() -> None:
    fake = FakeClaudeClient()
    fake.script_structured("prepare.standing", js("riya.standing.answer.json"))
    fake.script_structured("prepare.moves", ClaudeInvalidOutput())
    service, store, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    events = await collect(service.run(profile, context, plan_for(), CTX))
    assert kinds(events)[0] == "gaps" and kinds(events)[-1] == "error"
    error = events[-1]
    assert isinstance(error, PrepareErrorEvent) and error.code == NO_MOVES_CODE
    assert "Try again" in error.message
    assert store.rows == {}


async def test_moves_that_all_fail_the_checks_are_no_moves_too() -> None:
    fake = FakeClaudeClient()
    fake.script_structured("prepare.standing", js("riya.standing.answer.json"))
    fake.script_structured(
        "prepare.moves",
        {
            "moves": [
                {"have": [], "title": "Win 10 awards", "body": "b", "works": ["LLM evals"], "phases": []}
            ]
        },
    )
    service, store, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    events = await collect(service.run(profile, context, plan_for(), CTX))
    error = events[-1]
    assert isinstance(error, PrepareErrorEvent) and error.code == NO_MOVES_CODE
    assert store.rows == {}


async def test_the_spend_ceiling_ends_the_stream_with_its_own_sentence() -> None:
    fake = FakeClaudeClient()
    fake.script_structured("prepare.standing", SpendCeilingReached(60.0))
    fake.script_structured("prepare.moves", moves_answer)
    service, store, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    events = await collect(service.run(profile, context, plan_for(), CTX))
    error = events[-1]
    assert isinstance(error, PrepareErrorEvent) and error.code == "failed"
    assert error.message == SpendCeilingReached().user_message
    assert store.rows == {}


async def test_a_consumer_that_stops_early_cancels_the_calls_still_running() -> None:
    fake = FakeClaudeClient()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def hang(call: StructuredCall) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return moves_answer(call)

    fake.script_structured("prepare.standing", standing_answer)
    fake.script_structured("prepare.moves", hang)
    service, store, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    stream = service.run(profile, context, plan_for(), CTX)
    first = await anext(stream)
    assert isinstance(first, PrepareGapsEvent)
    waiting = asyncio.create_task(anext(stream))  # the stream now waits on the two calls
    await asyncio.wait_for(started.wait(), 2)
    waiting.cancel()  # the client hung up
    await asyncio.gather(waiting, return_exceptions=True)
    await asyncio.wait_for(cancelled.wait(), 2)
    await stream.aclose()
    assert store.rows == {}


# ───────────────────────── a stored plan ─────────────────────────


async def test_a_stored_plan_is_found_by_role_and_requirement_and_replayed_without_a_call() -> None:
    fake = script_riya(FakeClaudeClient())
    service, _, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    done = (await collect(service.run(profile, context, plan_for(), CTX)))[-1]
    assert isinstance(done, PrepareDoneEvent)
    calls = len(fake.structured_calls)

    found = await service.stored(profile.id, ROLE, None)
    assert found == done.plan
    assert await service.stored(profile.id, ROLE, "LLM evals") is None
    assert await service.stored("someone-else", ROLE, None) is None
    replay = await collect(service.replay(found))
    assert kinds(replay) == ["gaps", "done"]
    assert replay[0].plan == found and replay[1].plan == found  # type: ignore[union-attr]
    assert len(fake.structured_calls) == calls


async def test_a_fresh_plan_is_a_new_row_and_the_newest_is_the_one_found() -> None:
    fake = script_riya(FakeClaudeClient())
    service, store, clock = make_service(fake, repos=await seeded())
    profile, context = riya()
    await collect(service.run(profile, context, plan_for(plan_id="1" * 32), CTX))
    clock.advance(60)
    await collect(service.run(profile, context, plan_for(plan_id="2" * 32), CTX))
    assert set(store.rows) == {"1" * 32, "2" * 32}
    found = await service.stored(profile.id, ROLE, None)
    assert found is not None and found.id == "2" * 32


async def test_a_requirement_plan_is_stored_under_its_requirement() -> None:
    fake = script_good(FakeClaudeClient())
    service, store, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    plan = service.assemble(profile, context, request_for(requirement="rag & retrieval"))
    done = (await collect(service.run(profile, context, plan, CTX)))[-1]
    assert isinstance(done, PrepareDoneEvent)
    assert done.plan.scope == "requirement" and [g.name for g in done.plan.gaps] == ["RAG & retrieval"]
    assert store.rows[plan.id].requirement == "RAG & retrieval"
    assert await service.stored(profile.id, ROLE, "RAG & retrieval") == done.plan
    assert await service.stored(profile.id, ROLE, None) is None


def test_assemble_mints_a_plan_id_and_stamps_the_clock() -> None:
    service, _, _ = make_service(FakeClaudeClient())
    profile, context = riya()
    plan = service.assemble(profile, context, request_for())
    assert len(plan.id) == 32 and int(plan.id, 16) >= 0 and plan.createdAt == NOW.isoformat()
    assert len(new_plan_id()) == 32


# ───────────────────────── the state ─────────────────────────


async def stored_plan(service: Any) -> PreparePlan:
    profile, context = riya()
    done = (await collect(service.run(profile, context, plan_for(), CTX)))[-1]
    assert isinstance(done, PrepareDoneEvent)
    return done.plan


def state(**overrides: Any) -> PrepareState:
    values: dict[str, Any] = {"hoursPerWeek": 10, "moveIndex": 1, "done": ["m1:0:0", "m2:1:2"], "links": []}
    values.update(overrides)
    return PrepareState.model_validate(values)


async def test_the_state_is_saved_and_the_version_goes_up_by_one() -> None:
    service, store, clock = make_service(script_riya(FakeClaudeClient()), repos=await seeded())
    profile, _ = riya()
    plan = await stored_plan(service)
    link = PrepareLink(
        moveId="m1",
        phaseIndex=2,
        url="https://github.com/riyakapoor/notes-ai/pull/1",
        addedAt=NOW.isoformat(),
    )
    saved = await service.save_state(
        profile.id, plan.id, PrepareStateUpdate(profileId=profile.id, version=1, state=state(links=[link]))
    )
    assert saved.version == 2 and saved.state.hoursPerWeek == 10 and saved.state.links == [link]
    again = await service.get(profile.id, plan.id)
    assert again is not None and again.version == 2 and again.state == saved.state
    assert again.moves == plan.moves and store.rows[plan.id].updated_at == clock.now()


async def test_a_stale_version_is_refused_and_nothing_changes() -> None:
    service, _, _ = make_service(script_riya(FakeClaudeClient()), repos=await seeded())
    profile, _ = riya()
    plan = await stored_plan(service)
    await service.save_state(
        profile.id, plan.id, PrepareStateUpdate(profileId=profile.id, version=1, state=state())
    )
    with pytest.raises(PlanChanged) as info:
        await service.save_state(
            profile.id,
            plan.id,
            PrepareStateUpdate(profileId=profile.id, version=1, state=state(hoursPerWeek=3)),
        )
    assert info.value.status == 409 and info.value.code == "plan_changed"
    with pytest.raises(PlanChanged):
        await service.save_state(
            profile.id, plan.id, PrepareStateUpdate(profileId=profile.id, version=5, state=state())
        )
    again = await service.get(profile.id, plan.id)
    assert again is not None and again.version == 2 and again.state.hoursPerWeek == 10


@pytest.mark.parametrize(
    "bad",
    [
        {"done": ["m9:0:0"]},
        {"done": ["m1:7:0"]},
        {"done": ["m1:0:9"]},
        {"done": ["m1:0"]},
        {"done": ["m1:a:0"]},
        {"moveIndex": 2, "done": []},  # the stored plan has three moves: index 2 is fine, see below
    ],
)
async def test_a_done_key_naming_what_the_plan_does_not_have_is_refused(bad: dict[str, Any]) -> None:
    service, _, _ = make_service(script_riya(FakeClaudeClient()), repos=await seeded())
    profile, _ = riya()
    plan = await stored_plan(service)
    update = PrepareStateUpdate(profileId=profile.id, version=1, state=state(**bad))
    if bad.get("moveIndex") == 2:
        assert (await service.save_state(profile.id, plan.id, update)).version == 2
        return
    with pytest.raises(BadState) as info:
        await service.save_state(profile.id, plan.id, update)
    assert info.value.status == 400
    assert (await service.get(profile.id, plan.id)).version == 1  # type: ignore[union-attr]


async def test_a_move_index_past_the_plans_moves_is_refused() -> None:
    fake = FakeClaudeClient()
    fake.script_structured("prepare.standing", js("riya.standing.answer.json"))
    fake.script_structured("prepare.moves", {"moves": js("riya.moves.answer.json")["moves"][:1]})
    service, _, _ = make_service(fake, repos=await seeded())
    profile, _ = riya()
    plan = await stored_plan(service)
    assert len(plan.moves) == 1
    with pytest.raises(BadState):
        await service.save_state(
            profile.id,
            plan.id,
            PrepareStateUpdate(profileId=profile.id, version=1, state=state(moveIndex=1, done=[])),
        )


@pytest.mark.parametrize(
    "url", ["ftp://x.example/a", "javascript:alert(1)", "notes-ai", "https://", "mailto:a@b.c"]
)
async def test_a_link_that_is_not_a_web_address_or_names_no_phase_is_refused(url: str) -> None:
    service, _, _ = make_service(script_riya(FakeClaudeClient()), repos=await seeded())
    profile, _ = riya()
    plan = await stored_plan(service)
    bad = PrepareLink(moveId="m1", phaseIndex=0, url=url, addedAt=NOW.isoformat())
    with pytest.raises(BadState):
        await service.save_state(
            profile.id, plan.id, PrepareStateUpdate(profileId=profile.id, version=1, state=state(links=[bad]))
        )
    off = PrepareLink(
        moveId="m3", phaseIndex=2, url="https://x.example/a", addedAt=NOW.isoformat()
    )  # m3 has 2 phases
    with pytest.raises(BadState):
        await service.save_state(
            profile.id, plan.id, PrepareStateUpdate(profileId=profile.id, version=1, state=state(links=[off]))
        )


def test_check_state_accepts_every_task_the_plan_has() -> None:
    plan = plan_for().model_copy(update={"moves": js("riya.moves.answer.json") and []})
    from tayyari.prepare.verify import check_moves

    plan = plan.model_copy(
        update={
            "moves": check_moves(
                js("riya.moves.answer.json"), gaps=plan.gaps, evidence=plan.evidence, eta=plan.eta
            )
        }
    )
    keys = [
        f"{m.id}:{pi}:{ti}" for m in plan.moves for pi, p in enumerate(m.phases) for ti in range(len(p.tasks))
    ]
    check_state(plan, PrepareState(hoursPerWeek=3, moveIndex=2, done=keys, links=[]))


async def test_an_unknown_plan_or_another_profiles_plan_is_not_found() -> None:
    service, _, _ = make_service(script_riya(FakeClaudeClient()), repos=await seeded())
    profile, context = riya()
    plan = await stored_plan(service)
    assert await service.get("someone-else", plan.id) is None
    assert await service.get(profile.id, "f" * 32) is None
    with pytest.raises(PlanNotFound) as info:
        await service.save_state(
            "someone-else", plan.id, PrepareStateUpdate(profileId="someone-else", version=1, state=state())
        )
    assert info.value.status == 404
    assert await service.repos("someone-else", plan.id, context) is None


async def test_a_stored_row_in_a_shape_this_build_cannot_read_is_not_found() -> None:
    service, store, _ = make_service(FakeClaudeClient(), repos=await seeded())
    await store.create(
        StoredPlan(
            id="b" * 32,
            profile_id="p1",
            role_id=ROLE,
            requirement=None,
            content={"id": "b" * 32, "old": True},
            state={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    assert await service.get("p1", "b" * 32) is None
    assert await service.stored("p1", ROLE, None) is None


# ───────────────────────── repos and the headline ─────────────────────────


async def test_the_repos_are_ranked_from_the_stored_github_digest_with_no_call() -> None:
    fake = script_riya(FakeClaudeClient())
    service, _, _ = make_service(fake, repos=await seeded())
    profile, context = riya()
    plan = await stored_plan(service)
    calls = len(fake.structured_calls)
    repos = await service.repos(profile.id, plan.id, context)
    assert repos is not None and repos.github is True and len(repos.repos) == 10
    assert repos.repos[0].name == "notes-ai" and repos.repos[0].suggested is True
    assert sum(1 for r in repos.repos if r.suggested) == 1
    assert len(fake.structured_calls) == calls
    assert (await service.repos(profile.id, plan.id, StoredContext())) == type(repos)(github=False, repos=[])


async def test_the_headline_is_written_from_the_plans_evidence_and_checked() -> None:
    fake = script_riya(FakeClaudeClient())
    service, _, _ = make_service(fake, repos=await seeded())
    profile, _ = riya()
    plan = await stored_plan(service)
    out = await service.headline(profile, plan, CTX)
    assert out.current == profile.person.headline
    assert out.proposed == "Frontend Engineer · two OpenAI apps, notes-ai and Recipe Genie · Bengaluru"
    assert out.evidenceIds == ["e6", "e7", "e5"] and out.note is None
    call = fake.calls_for("prepare.headline")[0]
    assert call.ctx.purpose == "prepare.headline" and profile.person.headline in call.user_text
    assert "<evidence>" in call.user_text and call.model == MODEL


async def test_a_headline_that_fails_the_check_or_the_call_is_not_proposed() -> None:
    fake = FakeClaudeClient()
    script_riya(fake)
    fake._queues.structured["prepare.headline"] = []  # replace the scripted answer
    fake.script_structured(
        "prepare.headline",
        {
            "evidenceIds": ["e6"],
            "headline": "Frontend Engineer · Prisma pipelines at OpenAI · Bengaluru",
        },  # e6 has no Prisma
    )
    fake.script_structured("prepare.headline", ClaudeOverloaded())
    service, _, _ = make_service(fake, repos=await seeded())
    profile, _ = riya()
    plan = await stored_plan(service)
    failed = await service.headline(profile, plan, CTX)
    assert failed.proposed is None and failed.note is not None and failed.evidenceIds == ["e6"]
    unavailable = await service.headline(profile, plan, CTX)
    assert unavailable.proposed is None and unavailable.note == HEADLINE_UNAVAILABLE_NOTE


# ───────────────────────── the profile ─────────────────────────


async def test_load_profile_reads_a_stored_profile_with_its_context() -> None:
    service, _, _ = make_service(FakeClaudeClient(), repos=await seeded())
    profile, _ = riya()
    loaded = await service.load_profile(profile.id)
    assert loaded is not None
    assert loaded[0] == profile and [s.id for s in loaded[1].sources] == [
        "resume",
        "github",
        "lovable-1",
        "portfolio-1",
    ]
    assert await service.load_profile("f" * 32) is None


async def test_load_profile_seeds_the_bundled_sample_on_first_use() -> None:
    repos = FakeRepositories()
    service, _, _ = make_service(FakeClaudeClient(), repos=repos)
    sample_id = SampleProvider().sample_profile_id()
    assert sample_id is not None
    assert await repos.profiles.get_profile(sample_id) is None
    loaded = await service.load_profile(sample_id)
    assert loaded is not None and loaded[0].id == sample_id and loaded[1].sources
    assert await repos.profiles.get_access(sample_id) is not None  # saved, so a plan's row can refer to it


async def test_a_stored_document_that_is_not_a_profile_is_not_loaded() -> None:
    repos = FakeRepositories()
    await repos.profiles.save(
        "p9", {"id": "p9"}, StoredContext(), goal=None, owner_hash=None, is_sample=False
    )
    service, _, _ = make_service(FakeClaudeClient(), repos=repos)
    assert await service.load_profile("p9") is None
