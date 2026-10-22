"""Practice the interview's routes end to end through the real app over a real SQLite file: the profile each may open, the
windows and spend they count, the body limits, the kill switch, the 404s, and `Cache-Control: no-store` on every answer.

The service behind them is a stub handed in through the container override (`ContainerOverrides.interview`), the way the
job search's tests swap theirs: what the service itself does is tested in tests/unit/interview. The stub records what the
routes handed it (the profile, the request, the call context) and how much spend was held while it ran. Nothing here
calls Claude or the network.

The last section is the voice made ahead: the real speech service over `FakeSpeechEngine` behind the stub, and what each route
asks it to get ready (and when: before the judge reads an answer, or after the reply is out).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tayyari.api.routes.interview import SESSION_NOT_FOUND
from tayyari.contract.discover import Cite, Place, QuickFit
from tayyari.contract.interview import (
    AnswerRequest,
    AssistantReply,
    AssistantRequest,
    Debrief,
    FactRequest,
    InterviewLine,
    InterviewPlan,
    InterviewSession,
    PlanRequest,
    PlanSlot,
    PracticeHistory,
    SessionSummary,
    SessionView,
    SlotResult,
    SlotSource,
    StartRequest,
    Turn,
    VerdictCounts,
    WorkSubmit,
)
from tayyari.contract.jobsearch import AiJob, AskCounts, AskMet, JobSearchRun
from tayyari.contract.models import Profile
from tayyari.contract.stored import StoredContext
from tayyari.core.errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from tayyari.core.settings import GuardSettings, Settings, SpeechSettings
from tayyari.interview.errors import (
    AssistantUnavailable,
    BadPlan,
    GraphRoleGone,
    InterviewEnded,
    InterviewMovedOn,
    InterviewNotFound,
    NoTemplate,
    RoleGone,
    WorkFirst,
)
from tayyari.interview.speech import SpeechUpcoming
from tayyari.llm.errors import SPEND_CEILING_MESSAGE, ClaudeOverloaded
from tayyari.llm.types import CallContext
from tayyari.speech.engine import SpeechUnavailable
from tayyari.storage.ports import StoredSearchRun
from tayyari.storage.tables import LlmCallRow, RateLimitHitRow
from tests.fakes.discover import profile_of
from tests.fakes.speech import FakeSpeechEngine
from tests.integration.support import Env, roomy_guard, running

MakeSettings = Callable[..., Settings]
SESSION_COOKIE = "tayyari_session"
FRONTEND_ID = profile_of("frontend").id
UNKNOWN_ID = "f" * 32
RUN = {"runId": "a" * 32, "jobId": "b" * 32}
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
OFF = "Practice is switched off right now."
ESCAPED_E_ACUTE = chr(92) + "u00e9"
"""Six bytes of JSON for one character: how a client that escapes non-ASCII text spells an é."""

SOURCE = SlotSource(origin="evidence", text="Cut the checkout load from 4s to 1.2s", source="Resume")
SLOT = PlanSlot(id="s1", kind="claim", topic="Performance", sources=[SOURCE])


LEAD = "Let's come back to the question."
QUESTION = "Walk me through it."
NEXT_UP = ("Which part of it was yours?", "Who else was involved?", "What did you build next?")
UPCOMING = SpeechUpcoming(now=(LEAD, QUESTION), soon=NEXT_UP, next_turn=NEXT_UP[:1])
"""What the stub says the voice should have ready: the line being said (a lead and its question), then what may follow."""


def line(text: str = QUESTION, kind: str = "question") -> InterviewLine:
    return InterviewLine(kind=kind, text=text, slotId="s1", rung="surface")  # type: ignore[arg-type]


def session_of(
    profile_id: str, session_id: str, *, status: str = "active", kind: str = "resume"
) -> InterviewSession:
    return InterviewSession(
        id=session_id,
        profileId=profile_id,
        kind=kind,  # type: ignore[arg-type]
        title="Defend your resume",
        status=status,  # type: ignore[arg-type]
        createdAt=NOW.isoformat(),
        slots=[SLOT],
        slotIndex=0,
        current=None if status == "done" else line(),
        turns=[],
        minutes=3,
    )


def debrief_of(session_id: str, *, fact_added: bool | None = None) -> Debrief:
    return Debrief(
        sessionId=session_id,
        kind="resume",
        title="Defend your resume",
        createdAt=NOW.isoformat(),
        minutes=3,
        counts=VerdictCounts(defended=1, shaky=0, couldnt=0, notAssessed=0),
        results=[
            SlotResult(
                slotId="s1",
                kind="claim",
                topic="Performance",
                question="Walk me through it.",
                sources=[SOURCE],
                verdict="defended",
                why="You named what you did and what came of it.",
                newFact="Cut the load time by 70%." if fact_added is not None else None,
                factAdded=fact_added,
            )
        ],
        transcript=[Turn(speaker="interviewer", text="Walk me through it.", at=0)],
        keptUntil="2026-11-16",
    )


@dataclass
class Seen:
    """One call the routes made on the service."""

    method: str
    profile_id: str
    args: tuple[Any, ...]
    held_micro: int


@dataclass
class StubInterview:
    """Stands in for `InterviewService`: keeps sessions per profile, answers like the real one would for ids it does not
    know (an `InterviewNotFound`-shaped 404, or None), and records every call."""

    on: bool = True
    calls: list[Seen] = field(default_factory=list)
    sessions: dict[tuple[str, str], InterviewSession] = field(default_factory=dict)
    debriefs: dict[tuple[str, str], Debrief] = field(default_factory=dict)
    contexts: list[CallContext] = field(default_factory=list)
    held: Callable[[], int] = lambda: 0
    fail_with: Exception | None = None
    upcoming: SpeechUpcoming = field(default_factory=lambda: UPCOMING)
    upcoming_error: Exception | None = None
    after_answer: SpeechUpcoming | None = None
    """What the voice should have ready once an answer has been taken (the interviewer has moved on)."""
    unlimited_seen: list[bool] = field(default_factory=list)
    """The ``unlimited`` flag each assistant message came with."""

    @property
    def enabled(self) -> bool:
        return self.on

    def require_enabled(self) -> None:
        if not self.on:
            raise ServiceUnavailable(OFF)

    def _see(self, method: str, profile: Profile, *args: Any) -> None:
        self.calls.append(Seen(method, profile.id, args, self.held()))
        if self.fail_with is not None:
            raise self.fail_with

    def _own(self, profile: Profile, session_id: str) -> InterviewSession:
        found = self.sessions.get((profile.id, session_id))
        if found is None:
            raise NotFound(SESSION_NOT_FOUND)
        return found

    def names(self) -> list[str]:
        return [c.method for c in self.calls]

    async def speech_upcoming(
        self, profile: Profile, session_id: str, *, followups: bool = True
    ) -> SpeechUpcoming | None:
        self.calls.append(Seen("speech_upcoming", profile.id, (session_id, followups), self.held()))
        if self.upcoming_error is not None:
            raise self.upcoming_error
        found = self.sessions.get((profile.id, session_id))
        if found is None or found.status == "done":
            return None
        return self.upcoming

    async def plan(self, profile: Profile, req: PlanRequest) -> InterviewPlan:
        self._see("plan", profile, req)
        return InterviewPlan(kind=req.kind, title="Defend your resume", slots=[SLOT], minutes=3)

    async def start(self, profile: Profile, req: StartRequest, ctx: CallContext) -> InterviewSession:
        self._see("start", profile, req)
        self.contexts.append(ctx)
        made = session_of(profile.id, uuid.uuid4().hex, kind=req.kind)
        self.sessions[(profile.id, made.id)] = made
        return made

    async def answer(
        self, profile: Profile, session_id: str, req: AnswerRequest, ctx: CallContext
    ) -> InterviewSession:
        self._see("answer", profile, session_id, req)
        self.contexts.append(ctx)
        found = self._own(profile, session_id)
        if self.after_answer is not None:
            self.upcoming = self.after_answer
        return found.model_copy(update={"turns": [Turn(speaker="you", text=req.text, at=5, slotId="s1")]})

    async def submit_work(self, profile: Profile, session_id: str, req: WorkSubmit) -> InterviewSession:
        self._see("submit_work", profile, session_id, req)
        return self._own(profile, session_id).model_copy(update={"work": req.work})

    async def assistant(
        self,
        profile: Profile,
        session_id: str,
        req: AssistantRequest,
        ctx: CallContext,
        *,
        unlimited: bool = False,
    ) -> AssistantReply:
        self._see("assistant", profile, session_id, req)
        self.contexts.append(ctx)
        self.unlimited_seen.append(unlimited)
        self._own(profile, session_id)
        return AssistantReply(reply="Try a memo.", asked=1, left=11)

    async def end(self, profile: Profile, session_id: str, ctx: CallContext) -> Debrief:
        self._see("end", profile, session_id)
        self.contexts.append(ctx)
        self._own(profile, session_id)
        made = debrief_of(session_id)
        self.debriefs[(profile.id, session_id)] = made
        return made

    async def view(self, profile: Profile, session_id: str) -> SessionView | None:
        self._see("view", profile, session_id)
        found = self.sessions.get((profile.id, session_id))
        if found is None:
            return None
        return SessionView(session=found, debrief=self.debriefs.get((profile.id, session_id)))

    async def history(self, profile: Profile, *, per_day: int | None = None) -> PracticeHistory:
        self._see("history", profile, per_day)
        mine = [
            SessionSummary(
                id=s.id, kind=s.kind, title=s.title, createdAt=s.createdAt, status=s.status, verdicts=[]
            )
            for (owner, _), s in self.sessions.items()
            if owner == profile.id
        ]
        return PracticeHistory(
            sessions=mine, claims=[], facts=[], leftToday=None if per_day is None else per_day - len(mine)
        )

    async def again(self, profile: Profile, session_id: str, slot_id: str) -> InterviewSession:
        self._see("again", profile, session_id, slot_id)
        self._own(profile, session_id)
        made = session_of(profile.id, uuid.uuid4().hex)
        self.sessions[(profile.id, made.id)] = made
        return made

    async def set_fact(self, profile: Profile, session_id: str, req: FactRequest) -> Debrief | None:
        self._see("set_fact", profile, session_id, req)
        if (profile.id, session_id) not in self.debriefs:
            return None
        return debrief_of(session_id, fact_added=req.add)

    async def delete(self, profile: Profile, session_id: str) -> bool:
        self._see("delete", profile, session_id)
        return self.sessions.pop((profile.id, session_id), None) is not None


@dataclass
class Rig:
    env: Env
    stub: StubInterview

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def profile(self, *, owner: str | None = None) -> str:
        found = profile_of("frontend")
        container = self.env.container
        await container.repos.profiles.save(
            found.id, found.to_dict(), StoredContext(), goal=found.goal, owner_hash=None, is_sample=False
        )
        if owner is not None:
            await container.repos.users.claim_profile(owner, found.id)
        return found.id

    async def account(self, email: str = "ana@example.com") -> tuple[str, dict[str, str]]:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return user.id, {"cookie": f"{SESSION_COOKIE}={token}"}

    async def start(
        self,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        profile_id: str = FRONTEND_ID,
    ) -> httpx.Response:
        return await self.client.post(
            "/api/interview/sessions",
            json=body if body is not None else {"profileId": profile_id, "kind": "resume"},
            headers=headers or {},
        )

    async def started(self, profile_id: str = FRONTEND_ID, headers: dict[str, str] | None = None) -> str:
        response = await self.start(profile_id=profile_id, headers=headers)
        assert response.status_code == 200, response.text
        session_id: str = response.json()["id"]
        return session_id

    def at(self, ip: str) -> dict[str, str]:
        return {"cf-connecting-ip": ip}


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(*, voice: FakeSpeechEngine | None = None, **overrides: Any) -> AsyncIterator[Rig]:
        stub = StubInterview(on=overrides.pop("on", True))
        overrides.setdefault("guard", roomy())
        async with running(build_settings(**overrides), interview=stub, speech_engine=voice) as env:
            stub.held = lambda: env.container.spend_guard.reserved_micro
            yield Rig(env, stub)

    return run


def is_no_store(response: httpx.Response) -> bool:
    return response.headers.get("cache-control") == "no-store"


def roomy(**overrides: Any) -> GuardSettings:
    """Guard settings where nothing trips by accident (the interview windows included); pass what a test wants to hit."""
    values: dict[str, Any] = {
        "interview_per_minute": 1000,
        "interview_per_hour": 1000,
        "interview_per_day": 1000,
        "interview_global_per_hour": 0,
        "interview_again_per_day": 1000,
    }
    values.update(overrides)
    return roomy_guard(**values)


def micro(usd: float) -> int:
    return round(usd * 1_000_000)


# ───────────────────────── the routes, one by one ─────────────────────────


async def test_a_whole_interview_goes_through_the_routes(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        body = {"profileId": FRONTEND_ID}

        plan = await rig.client.post(
            "/api/interview/plan",
            json={**body, "kind": "role", "role": RUN, "length": "short", "focus": "gaps"},
        )
        assert plan.status_code == 200 and is_no_store(plan)
        assert InterviewPlan.model_validate(plan.json()).kind == "role"
        planned = rig.stub.calls[-1].args[0]
        assert (
            isinstance(planned, PlanRequest)
            and planned.role is not None
            and planned.role.runId == RUN["runId"]
        )
        assert (planned.length, planned.focus) == ("short", "gaps")

        started = await rig.client.post(
            "/api/interview/sessions", json={**body, "kind": "role", "role": RUN, "skip": ["s2", "s3"]}
        )
        assert started.status_code == 200 and is_no_store(started)
        session = InterviewSession.model_validate(started.json())
        assert session.profileId == FRONTEND_ID and session.status == "active"
        assert rig.stub.calls[-1].args[0].skip == ["s2", "s3"]
        sid = session.id
        base = f"/api/interview/sessions/{sid}"

        answered = await rig.client.post(
            f"{base}/answer", json={**body, "text": "I cut the load time by 70%.", "mode": "speech"}
        )
        assert answered.status_code == 200 and is_no_store(answered)
        assert InterviewSession.model_validate(answered.json()).turns[0].text == "I cut the load time by 70%."
        asked = rig.stub.calls[-1]
        assert (asked.method, asked.args[0]) == ("answer", sid)
        assert asked.args[1].mode == "speech" and asked.args[1].action is None

        button = await rig.client.post(
            f"{base}/answer", json={**body, "text": "", "mode": "typed", "action": "dont_know"}
        )
        assert button.status_code == 200 and rig.stub.calls[-1].args[1].action == "dont_know"

        work = await rig.client.post(
            f"{base}/work", json={**body, "work": "export const x = 1;", "testNote": "ran it"}
        )
        assert work.status_code == 200 and is_no_store(work)
        assert InterviewSession.model_validate(work.json()).work == "export const x = 1;"
        assert rig.stub.calls[-1].args[1].testNote == "ran it"

        assistant = await rig.client.post(
            f"{base}/assistant", json={**body, "message": "How do I memoise this?", "work": "code"}
        )
        assert assistant.status_code == 200 and is_no_store(assistant)
        assert AssistantReply.model_validate(assistant.json()) == AssistantReply(
            reply="Try a memo.", asked=1, left=11
        )

        ended = await rig.client.post(f"{base}/end", json=body)
        assert ended.status_code == 200 and is_no_store(ended)
        assert Debrief.model_validate(ended.json()).sessionId == sid

        viewed = await rig.client.get(base, params=body)
        assert viewed.status_code == 200 and is_no_store(viewed)
        view = SessionView.model_validate(viewed.json())
        assert view.session.id == sid and view.debrief is not None and view.debrief.sessionId == sid

        again = await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"})
        assert again.status_code == 200 and is_no_store(again)
        repeat = InterviewSession.model_validate(again.json())
        assert repeat.id != sid and rig.stub.calls[-1].args == (sid, "s1")

        fact = await rig.client.post(f"{base}/facts", json={**body, "slotId": "s1", "add": True})
        assert fact.status_code == 200 and is_no_store(fact)
        assert Debrief.model_validate(fact.json()).results[0].factAdded is True
        assert rig.stub.calls[-1].args[1] == FactRequest(profileId=FRONTEND_ID, slotId="s1", add=True)

        history = await rig.client.get("/api/interview/sessions", params=body)
        assert history.status_code == 200 and is_no_store(history)
        assert len(PracticeHistory.model_validate(history.json()).sessions) == 2

        deleted = await rig.client.delete(base, params=body)
        assert deleted.status_code == 204 and deleted.content == b"" and is_no_store(deleted)
        assert (await rig.client.get(base, params=body)).status_code == 404


async def test_a_role_from_the_graph_reaches_the_service_through_plan_and_start(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        body = {"profileId": FRONTEND_ID, "kind": "role", "graphRole": {"roleId": "senior-fe"}}
        plan = await rig.client.post("/api/interview/plan", json={**body, "length": "short"})
        assert plan.status_code == 200 and is_no_store(plan)
        planned = rig.stub.calls[-1].args[0]
        assert isinstance(planned, PlanRequest) and planned.role is None
        assert planned.graphRole is not None and planned.graphRole.roleId == "senior-fe"
        started = await rig.start(body={**body, "skip": ["s2"]})
        assert started.status_code == 200 and is_no_store(started)
        asked = rig.stub.calls[-1].args[0]
        assert isinstance(asked, StartRequest) and asked.graphRole is not None and asked.skip == ["s2"]
        too_long = {**body, "graphRole": {"roleId": "r" * 65}}
        refused = await rig.client.post("/api/interview/plan", json=too_long)
        # refused by the contract, so it never reached the service
        assert refused.status_code == 400 and rig.stub.calls[-1].method == "start"


async def test_the_services_calls_carry_who_asked_and_what_for(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started(headers={"x-request-id": "req-start-1"})
        body = {"profileId": FRONTEND_ID}
        await rig.client.post(
            f"/api/interview/sessions/{sid}/answer", json={**body, "text": "a b c", "mode": "typed"}
        )
        await rig.client.post(f"/api/interview/sessions/{sid}/assistant", json={**body, "message": "hi"})
        await rig.client.post(f"/api/interview/sessions/{sid}/end", json=body)
        purposes = [c.purpose for c in rig.stub.contexts]
        assert purposes == ["interview.phrase", "interview.judge", "interview.assistant", "interview.debrief"]
        assert {c.profile_id for c in rig.stub.contexts} == {FRONTEND_ID}
        assert rig.stub.contexts[0].request_id == "req-start-1"
        assert all(c.ip_hash for c in rig.stub.contexts)


async def test_every_answer_is_no_store(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        base = f"/api/interview/sessions/{sid}"
        body = {"profileId": FRONTEND_ID}
        answers = [
            await rig.client.post("/api/interview/plan", json={**body, "kind": "stories"}),
            await rig.start(),
            await rig.client.get("/api/interview/sessions", params=body),
            await rig.client.get(base, params=body),
            await rig.client.post(f"{base}/answer", json={**body, "text": "x y", "mode": "typed"}),
            await rig.client.post(f"{base}/work", json={**body, "work": "w"}),
            await rig.client.post(f"{base}/assistant", json={**body, "message": "m"}),
            await rig.client.post(f"{base}/end", json=body),
            await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"}),
            await rig.client.post(f"{base}/facts", json={**body, "slotId": "s1", "add": False}),
            await rig.client.delete(base, params=body),
        ]
        assert [r.status_code for r in answers] == [200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 204]
        assert all(is_no_store(r) for r in answers)


def _methods_and_paths(routes: Any) -> set[tuple[str, str]]:
    """Every (method, path); FastAPI keeps an included router as one entry, so look inside it."""
    out: set[tuple[str, str]] = set()
    for route in routes:
        inner = getattr(getattr(route, "original_router", None), "routes", None)
        if inner is not None:
            out |= _methods_and_paths(inner)
        else:
            out |= {(method, route.path) for method in getattr(route, "methods", None) or ()}
    return out


async def test_the_routes_are_registered(start: Any) -> None:
    async with start() as rig:
        routes = {
            (method, path)
            for method, path in _methods_and_paths(rig.env.app.router.routes)
            if path.startswith("/api/interview")
        }
    base = "/api/interview/sessions/{session_id}"
    assert routes == {
        ("POST", "/api/interview/plan"),
        ("POST", "/api/interview/sessions"),
        ("GET", "/api/interview/sessions"),
        ("GET", base),
        ("DELETE", base),
        ("POST", f"{base}/answer"),
        ("POST", f"{base}/work"),
        ("POST", f"{base}/assistant"),
        ("POST", f"{base}/end"),
        ("POST", f"{base}/again"),
        ("POST", f"{base}/facts"),
        ("GET", "/api/interview/redrill"),
        ("POST", "/api/interview/fixes"),
        ("PATCH", "/api/interview/fixes/{fix_id}"),
        ("DELETE", "/api/interview/fixes/{fix_id}"),
    }


# ───────────────────────── sessions that are not there ─────────────────────────


def test_the_routes_404_says_what_the_services_unknown_session_says() -> None:
    assert SESSION_NOT_FOUND == InterviewNotFound.user_message


async def test_a_session_that_is_gone_or_not_yours_is_a_404_with_the_same_sentence(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        body = {"profileId": FRONTEND_ID}
        gone = "c" * 32
        responses = [
            await rig.client.get(f"/api/interview/sessions/{gone}", params=body),
            await rig.client.post(
                f"/api/interview/sessions/{gone}/answer", json={**body, "text": "a b", "mode": "typed"}
            ),
            await rig.client.post(f"/api/interview/sessions/{gone}/work", json={**body, "work": "w"}),
            await rig.client.post(f"/api/interview/sessions/{gone}/assistant", json={**body, "message": "m"}),
            await rig.client.post(f"/api/interview/sessions/{gone}/end", json=body),
            await rig.client.post(f"/api/interview/sessions/{gone}/again", json={**body, "slotId": "s1"}),
            await rig.client.post(
                f"/api/interview/sessions/{gone}/facts", json={**body, "slotId": "s1", "add": True}
            ),
            await rig.client.delete(f"/api/interview/sessions/{gone}", params=body),
        ]
        assert [r.status_code for r in responses] == [404] * 8
        assert {r.json()["error"] for r in responses} == {SESSION_NOT_FOUND}
        assert (await rig.client.get(f"/api/interview/sessions/{sid}", params=body)).status_code == 200


async def test_an_id_of_the_wrong_shape_is_a_404_before_anything_is_read_or_called(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        body = {"profileId": FRONTEND_ID}
        bad_ids = ["not-an-id", "A" * 32, "a" * 31, "a" * 33, "g" * 32, "x" * 200]
        seen_before = len(rig.stub.calls)
        for bad in bad_ids:
            base = f"/api/interview/sessions/{bad}"
            responses = [
                await rig.client.get(base, params=body),
                await rig.client.post(f"{base}/answer", json={**body, "text": "a b", "mode": "typed"}),
                await rig.client.post(f"{base}/end", content=b"not even json"),  # the id is judged first
                await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"}),
                await rig.client.delete(base, params=body),
            ]
            assert {r.status_code for r in responses} == {404}, bad
            assert {r.json()["error"] for r in responses} == {SESSION_NOT_FOUND}
        assert len(rig.stub.calls) == seen_before


async def test_another_profiles_session_reads_as_not_there(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        sample = rig.env.sample_id
        theirs = {"profileId": sample}
        assert (await rig.client.get(f"/api/interview/sessions/{sid}", params=theirs)).status_code == 404
        assert (await rig.client.post(f"/api/interview/sessions/{sid}/end", json=theirs)).status_code == 404
        assert (await rig.client.delete(f"/api/interview/sessions/{sid}", params=theirs)).status_code == 404
        fact = await rig.client.post(
            f"/api/interview/sessions/{sid}/facts", json={**theirs, "slotId": "s1", "add": True}
        )
        assert fact.status_code == 404 and fact.json() == {"error": SESSION_NOT_FOUND}
        mine = {"profileId": FRONTEND_ID}
        assert (await rig.client.get(f"/api/interview/sessions/{sid}", params=mine)).status_code == 200


async def test_deleting_nothing_is_a_404_and_deleting_something_is_a_204(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        mine = {"profileId": FRONTEND_ID}
        first = await rig.client.delete(f"/api/interview/sessions/{sid}", params=mine)
        second = await rig.client.delete(f"/api/interview/sessions/{sid}", params=mine)
        assert (first.status_code, second.status_code) == (204, 404)
        assert second.json() == {"error": SESSION_NOT_FOUND}


async def test_a_fact_on_a_session_without_a_debrief_is_a_404(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        response = await rig.client.post(
            f"/api/interview/sessions/{sid}/facts",
            json={"profileId": FRONTEND_ID, "slotId": "s1", "add": True},
        )
        assert response.status_code == 404 and response.json() == {"error": SESSION_NOT_FOUND}


# ───────────────────────── who may open which profile ─────────────────────────


async def test_an_unknown_or_malformed_profile_is_refused_before_anything_is_counted_or_called(
    start: Any,
) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        calls_before = len(rig.stub.calls)
        for bad in (UNKNOWN_ID, "../etc", "x" * 65):
            responses = [
                await rig.client.post("/api/interview/plan", json={"profileId": bad, "kind": "resume"}),
                await rig.start(profile_id=bad),
                await rig.client.get("/api/interview/sessions", params={"profileId": bad}),
                await rig.client.get(f"/api/interview/sessions/{sid}", params={"profileId": bad}),
                await rig.client.post(f"/api/interview/sessions/{sid}/end", json={"profileId": bad}),
                await rig.client.delete(f"/api/interview/sessions/{sid}", params={"profileId": bad}),
            ]
            statuses = {r.status_code for r in responses}
            assert statuses <= {404, 400}, (bad, statuses)
            for r in responses:
                if r.status_code == 404:
                    assert r.json() == {"error": "Profile not found"}
        missing = await rig.client.get("/api/interview/sessions")
        assert missing.status_code == 404 and missing.json() == {"error": "Profile not found"}
        assert len(rig.stub.calls) == calls_before
        day_hits = [h for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("interview:day:")]
        assert [h.count for h in day_hits] == [1]  # only the one real start above was counted


async def test_a_claimed_profile_is_practised_only_by_its_owner(start: Any) -> None:
    async with start() as rig:
        owner, owner_headers = await rig.account("owner@example.com")
        _, other_headers = await rig.account("someone@example.com")
        await rig.profile(owner=owner)
        body = {"profileId": FRONTEND_ID, "kind": "resume"}
        for headers in (other_headers, {}):
            assert (
                await rig.client.post("/api/interview/plan", json=body, headers=headers)
            ).status_code == 404
            assert (await rig.start(headers=headers)).status_code == 404
            history = await rig.client.get(
                "/api/interview/sessions", params={"profileId": FRONTEND_ID}, headers=headers
            )
            assert history.status_code == 404
        assert rig.stub.calls == []
        mine = await rig.start(headers=owner_headers)
        assert mine.status_code == 200
        sid = mine.json()["id"]
        assert (
            await rig.client.get(
                f"/api/interview/sessions/{sid}", params={"profileId": FRONTEND_ID}, headers=other_headers
            )
        ).status_code == 404
        assert (
            await rig.client.get(
                f"/api/interview/sessions/{sid}", params={"profileId": FRONTEND_ID}, headers=owner_headers
            )
        ).status_code == 200


async def test_the_sample_profile_can_be_practised_without_an_account(start: Any) -> None:
    async with start() as rig:
        sample = rig.env.sample_id
        plan = await rig.client.post("/api/interview/plan", json={"profileId": sample, "kind": "resume"})
        assert plan.status_code == 200
        started = await rig.start(profile_id=sample)
        assert started.status_code == 200 and started.json()["profileId"] == sample
        assert rig.stub.calls[-1].profile_id == sample


async def test_the_shared_sample_profile_never_lists_anyones_practice(start: Any) -> None:
    # Every visitor shares the sample profile: one visitor's transcript and kept facts must not show up for another.
    async with start() as rig:
        sample = rig.env.sample_id
        history = await rig.client.get("/api/interview/sessions", params={"profileId": sample})
        assert history.status_code == 200 and is_no_store(history)
        listed = PracticeHistory.model_validate(history.json())
        assert listed.sessions == [] and listed.claims == [] and listed.facts == []
        assert all(call.method != "history" for call in rig.stub.calls)


# ───────────────────────── bodies ─────────────────────────


async def test_a_bad_body_is_refused_before_the_profile_is_looked_at(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        base = "/api/interview/sessions"
        wrong_type = await rig.client.post(
            base, content=b'{"profileId": "x"}', headers={"content-type": "text/plain"}
        )
        not_json = await rig.client.post(base, content=b"{nope", headers={"content-type": "application/json"})
        no_kind = await rig.client.post(base, json={"profileId": FRONTEND_ID})
        unknown_kind = await rig.client.post(base, json={"profileId": FRONTEND_ID, "kind": "mock"})
        too_many_skips = await rig.client.post(
            base, json={"profileId": FRONTEND_ID, "kind": "resume", "skip": [f"s{i}" for i in range(13)]}
        )
        assert [r.status_code for r in (wrong_type, not_json, no_kind, unknown_kind, too_many_skips)] == [
            415,
            400,
            400,
            400,
            400,
        ]
        assert "kind" in no_kind.json()["error"] and "kind" in unknown_kind.json()["error"]
        assert rig.stub.calls == []


async def test_an_overlong_answer_or_work_is_a_400_and_a_huge_body_a_413(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        base = f"/api/interview/sessions/{sid}"
        body = {"profileId": FRONTEND_ID}
        long_answer = await rig.client.post(
            f"{base}/answer", json={**body, "text": "a" * 6001, "mode": "typed"}
        )
        full_answer = await rig.client.post(
            f"{base}/answer", json={**body, "text": "ab " * 2000, "mode": "typed"}
        )
        escaped = await rig.client.post(  # six thousand characters, each spelled with six bytes of JSON
            f"{base}/answer",
            content=(
                '{"profileId":"' + FRONTEND_ID + '","mode":"speech","text":"' + ESCAPED_E_ACUTE * 6000 + '"}'
            ).encode(),
            headers={"content-type": "application/json"},
        )
        long_work = await rig.client.post(f"{base}/work", json={**body, "work": "w" * 20_001})
        full_work = await rig.client.post(
            f"{base}/work", json={**body, "work": "w" * 20_000, "testNote": "n" * 2000}
        )
        huge = await rig.client.post(f"{base}/answer", json={**body, "text": "x" * 60_000, "mode": "typed"})
        huge_work = await rig.client.post(f"{base}/work", json={**body, "work": "x" * 400_000})
        assert long_answer.status_code == 400 and "text" in long_answer.json()["error"]
        assert (full_answer.status_code, escaped.status_code) == (200, 200)
        assert long_work.status_code == 400 and "work" in long_work.json()["error"]
        assert full_work.status_code == 200
        assert huge.status_code == 413 and huge_work.status_code == 413


async def test_a_missing_profile_id_is_a_400(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        response = await rig.client.post(
            f"/api/interview/sessions/{sid}/answer", json={"text": "a b", "mode": "typed"}
        )
        assert response.status_code == 400 and "profileId" in response.json()["error"]


# ───────────────────────── the kill switch ─────────────────────────


async def test_switched_off_every_route_answers_503_except_taking_a_transcript_away(start: Any) -> None:
    async with start(on=False) as rig:
        await rig.profile()
        sid = "d" * 32
        body = {"profileId": FRONTEND_ID}
        base = f"/api/interview/sessions/{sid}"
        responses = [
            await rig.client.post("/api/interview/plan", json={**body, "kind": "resume"}),
            await rig.start(),
            await rig.client.get("/api/interview/sessions", params=body),
            await rig.client.get(base, params=body),
            await rig.client.post(f"{base}/answer", json={**body, "text": "a b", "mode": "typed"}),
            await rig.client.post(f"{base}/work", json={**body, "work": "w"}),
            await rig.client.post(f"{base}/assistant", json={**body, "message": "m"}),
            await rig.client.post(f"{base}/end", json=body),
            await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"}),
            await rig.client.post(f"{base}/facts", json={**body, "slotId": "s1", "add": True}),
        ]
        assert [r.status_code for r in responses] == [503] * 10
        assert {r.json()["error"] for r in responses} == {OFF}
        assert rig.stub.calls == []
        deleted = await rig.client.delete(base, params=body)
        assert deleted.status_code == 404  # reached the service: there was nothing to delete
        assert rig.stub.names() == ["delete"]
        assert await rig.env.rows(RateLimitHitRow, RateLimitHitRow.bucket.like("interview%")) == []


# ───────────────────────── windows: how many interviews a day ─────────────────────────


async def test_starting_counts_per_address_and_the_minute_window_has_its_own_sentence(start: Any) -> None:
    async with start(guard=roomy(interview_per_minute=1)) as rig:
        await rig.profile()
        assert (await rig.start()).status_code == 200
        second = await rig.start()
        assert second.status_code == 429
        assert second.json()["error"].startswith("A practice interview is already starting. Please wait ")
        assert 1 <= int(second.headers["retry-after"]) <= 60
        elsewhere = await rig.start(headers=rig.at("198.51.100.7"))
        assert elsewhere.status_code == 200
        assert rig.stub.names().count("start") == 2  # the refused one never reached the service


@pytest.mark.parametrize("window", ["hour", "day"])
async def test_the_hour_and_day_windows_say_the_interviews_are_free_while_testing(
    start: Any, window: str
) -> None:
    async with start(guard=roomy(**{f"interview_per_{window}": 2})) as rig:
        await rig.profile()
        for _ in range(2):
            assert (await rig.start()).status_code == 200
        refused = await rig.start()
        assert refused.status_code == 429
        assert refused.json()["error"].startswith(
            "You've used today's practice interviews. They're free while we test them. More in "
        )
        assert int(refused.headers["retry-after"]) > 0
        assert rig.stub.names().count("start") == 2


async def test_everything_after_the_start_is_free_of_the_daily_count(start: Any) -> None:
    async with start(guard=roomy(interview_per_day=1)) as rig:
        await rig.profile()
        sid = await rig.started()
        body = {"profileId": FRONTEND_ID}
        base = f"/api/interview/sessions/{sid}"
        for _ in range(3):
            assert (
                await rig.client.post(f"{base}/answer", json={**body, "text": "a b", "mode": "typed"})
            ).status_code == 200
        assert (
            await rig.client.post("/api/interview/plan", json={**body, "kind": "resume"})
        ).status_code == 200
        assert (await rig.client.post(f"{base}/end", json=body)).status_code == 200
        assert (await rig.client.get("/api/interview/sessions", params=body)).status_code == 200
        assert (await rig.start()).status_code == 429


async def test_a_signed_in_account_is_counted_across_addresses(start: Any) -> None:
    async with start(guard=roomy(interview_per_day=1)) as rig:
        owner, headers = await rig.account("owner@example.com")
        await rig.profile(owner=owner)
        first = await rig.start(headers={**headers, **rig.at("198.51.100.1")})
        second = await rig.start(
            headers={**headers, **rig.at("198.51.100.2")}
        )  # a new address, the same account
        assert first.status_code == 200
        assert second.status_code == 429 and "practice interviews" in second.json()["error"]
        hits = {
            h.bucket: h.count
            for h in await rig.env.rows(RateLimitHitRow)
            if h.bucket.startswith("interview:day")
        }
        assert any(bucket.startswith(f"interview:day:u:{owner}") for bucket in hits)


async def test_without_an_account_only_the_address_is_counted(start: Any) -> None:
    async with start(guard=roomy(interview_per_day=1)) as rig:
        await rig.profile()
        assert (await rig.start()).status_code == 200
        buckets = {h.bucket for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("interview")}
        assert not any(":u:" in bucket for bucket in buckets)
        assert "interview:global:hour" in buckets or not rig.env.settings.guard.interview_global_per_hour


async def test_the_whole_service_has_an_hourly_backstop(start: Any) -> None:
    async with start(guard=roomy(interview_global_per_hour=2)) as rig:
        await rig.profile()
        for n in range(2):
            assert (await rig.start(headers=rig.at(f"198.51.100.{n + 1}"))).status_code == 200
        busy = await rig.start(headers=rig.at("198.51.100.99"))
        assert busy.status_code == 429
        assert busy.json()["error"].startswith("Tayyari is very busy right now. Please try again in ")
        assert rig.stub.names().count("start") == 2
        counted = [
            h.count for h in await rig.env.rows(RateLimitHitRow) if h.bucket == "interview:global:hour"
        ]
        assert counted == [3]


async def test_a_signed_in_start_counts_the_whole_service_once(start: Any) -> None:
    async with start(guard=roomy(interview_global_per_hour=5)) as rig:
        owner, headers = await rig.account("owner@example.com")
        await rig.profile(owner=owner)
        for _ in range(2):
            assert (await rig.start(headers=headers)).status_code == 200
        counted = [
            h.count for h in await rig.env.rows(RateLimitHitRow) if h.bucket == "interview:global:hour"
        ]
        assert counted == [2]


async def test_ask_again_has_a_daily_window_of_its_own(start: Any) -> None:
    async with start(guard=roomy(interview_again_per_day=2, interview_per_day=1)) as rig:
        await rig.profile()
        sid = await rig.started()
        body = {"profileId": FRONTEND_ID, "slotId": "s1"}
        url = f"/api/interview/sessions/{sid}/again"
        assert [(await rig.client.post(url, json=body)).status_code for _ in range(2)] == [200, 200]
        refused = await rig.client.post(url, json=body)
        assert refused.status_code == 429
        assert refused.json()["error"].startswith("That's enough repeats for today. More in ")
        assert int(refused.headers["retry-after"]) > 0
        assert rig.stub.names().count("again") == 2
        elsewhere = await rig.client.post(url, json=body, headers=rig.at("198.51.100.5"))
        assert elsewhere.status_code == 200
        # a repeat is not a new interview: the start's own window is untouched by it
        assert (await rig.start()).status_code == 429  # (that one was spent by `started` above)


async def test_ask_again_is_counted_per_account_too(start: Any) -> None:
    async with start(guard=roomy(interview_again_per_day=1)) as rig:
        owner, headers = await rig.account("owner@example.com")
        await rig.profile(owner=owner)
        sid = await rig.started(headers=headers)
        url = f"/api/interview/sessions/{sid}/again"
        body = {"profileId": FRONTEND_ID, "slotId": "s1"}
        assert (
            await rig.client.post(url, json=body, headers={**headers, **rig.at("198.51.100.1")})
        ).status_code == 200
        second = await rig.client.post(url, json=body, headers={**headers, **rig.at("198.51.100.2")})
        assert second.status_code == 429 and second.json()["error"].startswith("That's enough repeats")


async def test_a_window_of_zero_is_not_enforced(start: Any) -> None:
    async with start(
        guard=roomy(
            interview_per_minute=0, interview_per_hour=0, interview_per_day=0, interview_global_per_hour=0
        )
    ) as rig:
        await rig.profile()
        for _ in range(6):
            assert (await rig.start()).status_code == 200


# ───────────────────────── "left today" ─────────────────────────


async def test_history_is_told_how_many_a_day_are_allowed(start: Any) -> None:
    async with start(guard=roomy(interview_per_day=3)) as rig:
        await rig.profile()
        await rig.started()
        history = await rig.client.get("/api/interview/sessions", params={"profileId": FRONTEND_ID})
        assert PracticeHistory.model_validate(history.json()).leftToday == 2
        assert rig.stub.calls[-1].args == (3,)


async def test_history_is_not_told_a_number_when_the_day_window_is_off(start: Any) -> None:
    async with start(guard=roomy(interview_per_day=0)) as rig:
        await rig.profile()
        history = await rig.client.get("/api/interview/sessions", params={"profileId": FRONTEND_ID})
        assert history.status_code == 200 and "leftToday" not in history.json()
        assert rig.stub.calls[-1].args == (None,)


# ───────────────────────── an account on the unlimited list ─────────────────────────

LISTED = "tester@example.com"
OTHER = "other@example.com"
TINY_WINDOWS = {
    "interview_per_minute": 1,
    "interview_per_hour": 1,
    "interview_per_day": 1,
    "interview_global_per_hour": 1,
    "interview_again_per_day": 1,
}
"""Every window allows one request."""


def listed_guard(**overrides: Any) -> GuardSettings:
    """Roomy limits with an unlimited list that is written the way an owner might (padding, capitals, a second address)."""
    return roomy(unlimited_emails=f" {LISTED.upper()} , second.listed@example.org", **overrides)


async def test_a_listed_account_starts_past_every_window_and_is_counted_nowhere(start: Any) -> None:
    async with start(guard=listed_guard(**TINY_WINDOWS)) as rig:
        listed_id, listed = await rig.account(LISTED)
        _, other = await rig.account(OTHER)
        await rig.profile()
        for n in range(
            6
        ):  # from one address and from several: neither the address nor the account is counted
            headers = {**listed, **rig.at("198.51.100.1" if n % 2 else f"198.51.100.{n + 10}")}
            assert (await rig.start(headers=headers)).status_code == 200
        assert rig.stub.names().count("start") == 6
        assert not [h for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("interview")]

        # the same setup still limits everyone else: per account, per address, and the whole service
        assert (await rig.start(headers={**other, **rig.at("198.51.100.1")})).status_code == 200
        by_account = await rig.start(headers={**other, **rig.at("198.51.100.2")})
        by_address = await rig.start(headers=rig.at("198.51.100.1"))
        assert by_account.status_code == 429 and by_address.status_code == 429
        counted = [h for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("interview")]
        assert counted and not any(listed_id in h.bucket for h in counted)
        assert rig.stub.names().count("start") == 7


async def test_a_listed_account_asks_again_past_the_daily_window_and_others_do_not(start: Any) -> None:
    async with start(guard=listed_guard(interview_again_per_day=1)) as rig:
        _, listed = await rig.account(LISTED)
        _, other = await rig.account(OTHER)
        await rig.profile()
        sid = await rig.started(headers=listed)
        url = f"/api/interview/sessions/{sid}/again"
        body = {"profileId": FRONTEND_ID, "slotId": "s1"}
        mine = [
            (
                await rig.client.post(url, json=body, headers={**listed, **rig.at(f"198.51.100.{n}")})
            ).status_code
            for n in range(1, 6)
        ]
        assert mine == [200] * 5
        first = await rig.client.post(url, json=body, headers={**other, **rig.at("198.51.100.50")})
        second = await rig.client.post(url, json=body, headers={**other, **rig.at("198.51.100.51")})
        assert (first.status_code, second.status_code) == (200, 429)
        assert rig.stub.names().count("again") == 6


async def test_light_reads_never_shed_a_listed_account_but_do_shed_others(start: Any) -> None:
    async with start(guard=listed_guard(chat_per_minute=1)) as rig:
        _, listed = await rig.account(LISTED)
        _, other = await rig.account(OTHER)
        await rig.profile()
        params = {"profileId": FRONTEND_ID}
        url = "/api/interview/sessions"
        address = rig.at("198.51.100.20")
        mine = [
            (await rig.client.get(url, params=params, headers={**listed, **address})).status_code
            for _ in range(12)
        ]
        theirs = [
            (await rig.client.get(url, params=params, headers={**other, **address})).status_code
            for _ in range(12)
        ]
        assert mine == [200] * 12  # the address was never charged for them, so the next account starts fresh
        assert theirs[0] == 200 and 429 in theirs


async def test_history_tells_a_listed_account_no_number_of_interviews_left(start: Any) -> None:
    async with start(guard=listed_guard(interview_per_day=3)) as rig:
        _, listed = await rig.account(LISTED)
        _, other = await rig.account(OTHER)
        await rig.profile()
        params = {"profileId": FRONTEND_ID}
        mine = await rig.client.get("/api/interview/sessions", params=params, headers=listed)
        assert mine.status_code == 200 and "leftToday" not in mine.json()
        assert rig.stub.calls[-1].args == (None,)
        theirs = await rig.client.get("/api/interview/sessions", params=params, headers=other)
        assert PracticeHistory.model_validate(theirs.json()).leftToday == 3
        assert rig.stub.calls[-1].args == (3,)


async def test_the_assistant_is_told_who_is_unlimited(start: Any) -> None:
    async with start(guard=listed_guard()) as rig:
        _, listed = await rig.account(LISTED)
        _, other = await rig.account(OTHER)
        await rig.profile()
        for headers in (listed, other, {}):
            sid = await rig.started(headers=headers)
            reply = await rig.client.post(
                f"/api/interview/sessions/{sid}/assistant",
                json={"profileId": FRONTEND_ID, "message": "How do I start?"},
                headers=headers,
            )
            assert reply.status_code == 200
        assert rig.stub.unlimited_seen == [True, False, False]


async def test_a_listed_account_is_still_refused_at_the_spend_ceiling(start: Any) -> None:
    async with start(guard=listed_guard(daily_ceiling_usd=0.001)) as rig:
        _, listed = await rig.account(LISTED)
        await rig.profile()
        refused = await rig.start(headers=listed)
        assert refused.status_code == 503 and "start" not in rig.stub.names()


async def test_the_voice_made_ahead_leaves_out_the_session_share_for_a_listed_account(start: Any) -> None:
    for who, expected in ((LISTED, [LEAD, QUESTION, *NEXT_UP]), (OTHER, [LEAD])):
        voice = FakeSpeechEngine()
        speech = SpeechSettings(tts_session_chars=40, tts_prefetch_concurrency=1)
        async with start(voice=voice, speech=speech, guard=listed_guard()) as rig:
            _, headers = await rig.account(who)
            await rig.profile()
            assert (await rig.start(headers=headers)).status_code == 200
            await voice_done(rig)
            assert spoken(voice) == expected, who


# ───────────────────────── spend ─────────────────────────


async def test_spend_is_held_while_a_model_call_runs_and_released_after(start: Any) -> None:
    guard = roomy()
    async with start(guard=guard) as rig:
        await rig.profile()
        sid = await rig.started()
        body = {"profileId": FRONTEND_ID}
        base = f"/api/interview/sessions/{sid}"
        await rig.client.post(f"{base}/answer", json={**body, "text": "a b", "mode": "typed"})
        await rig.client.post(f"{base}/assistant", json={**body, "message": "m"})
        await rig.client.post(f"{base}/end", json=body)
        await rig.client.post("/api/interview/plan", json={**body, "kind": "resume"})
        await rig.client.post(f"{base}/work", json={**body, "work": "w"})
        await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"})
        await rig.client.post(f"{base}/facts", json={**body, "slotId": "s1", "add": True})
        await rig.client.get(base, params=body)
        held = {c.method: c.held_micro for c in rig.stub.calls}
        assert held["start"] == micro(guard.est_interview_usd)
        assert held["answer"] == micro(guard.est_interview_turn_usd)
        assert held["assistant"] == micro(guard.est_interview_turn_usd)
        assert held["end"] == micro(guard.est_interview_end_usd)
        for method in ("plan", "submit_work", "again", "set_fact", "view"):
            assert held[method] == 0, method  # no model call, nothing reserved
        assert rig.env.container.spend_guard.reserved_micro == 0
        assert rig.env.container.spend_guard.reservations_in_flight == 0


async def test_spend_is_released_when_the_service_fails(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        rig.stub.fail_with = Conflict("This interview moved on in another tab. Reload to carry on.")
        response = await rig.client.post(
            f"/api/interview/sessions/{sid}/answer",
            json={"profileId": FRONTEND_ID, "text": "a b", "mode": "typed"},
        )
        assert response.status_code == 409
        assert rig.env.container.spend_guard.reserved_micro == 0


async def test_a_spend_ceiling_refuses_before_the_service_is_asked(start: Any) -> None:
    async with start(guard=roomy(daily_ceiling_usd=0.001)) as rig:
        await rig.profile()
        sid = "e" * 32
        body = {"profileId": FRONTEND_ID}
        base = f"/api/interview/sessions/{sid}"
        refused = [
            await rig.start(),
            await rig.client.post(f"{base}/answer", json={**body, "text": "a b", "mode": "typed"}),
            await rig.client.post(f"{base}/assistant", json={**body, "message": "m"}),
            await rig.client.post(f"{base}/end", json=body),
        ]
        assert [r.status_code for r in refused] == [503] * 4
        assert {r.json()["error"] for r in refused} == {SPEND_CEILING_MESSAGE}
        assert rig.stub.calls == []
        # what needs no model still works
        assert (
            await rig.client.post("/api/interview/plan", json={**body, "kind": "resume"})
        ).status_code == 200
        assert (await rig.client.get("/api/interview/sessions", params=body)).status_code == 200
        assert rig.env.claude.calls == []
        assert await rig.env.rows(LlmCallRow) == []


# ───────────────────────── what the service says ─────────────────────────


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (BadRequest("Keep at least one question."), 400),
        (NotFound("That role is not in your searches any more. Search again, then practise it."), 404),
        (Conflict("This interview has ended. Open its debrief, or start a new one."), 409),
        (ServiceUnavailable(OFF), 503),
        (InterviewNotFound(), 404),
        (RoleGone(), 404),
        (GraphRoleGone(), 404),
        (InterviewMovedOn(), 409),
        (InterviewEnded(), 409),
        (NoTemplate(), 400),
        (BadPlan(), 400),
        (WorkFirst(), 400),
        (AssistantUnavailable(), 400),
    ],
)
async def test_the_services_errors_reach_the_person_in_their_own_words(
    start: Any, error: Exception, status: int
) -> None:
    async with start() as rig:
        await rig.profile()
        sid = await rig.started()
        rig.stub.fail_with = error
        body = {"profileId": FRONTEND_ID}
        base = f"/api/interview/sessions/{sid}"
        responses = [
            await rig.client.post("/api/interview/plan", json={**body, "kind": "role", "role": RUN}),
            await rig.start(body={**body, "kind": "role", "role": RUN}),
            await rig.client.post(f"{base}/answer", json={**body, "text": "a b", "mode": "typed"}),
            await rig.client.post(f"{base}/work", json={**body, "work": "w"}),
            await rig.client.post(f"{base}/end", json=body),
            await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"}),
        ]
        assert {r.status_code for r in responses} == {status}
        assert {r.json()["error"] for r in responses} == {str(error)}


# ───────────────────────── the role a practice interview is built on ─────────────────────────


def ai_job(job_id: str) -> AiJob:
    return AiJob(
        id=job_id,
        title="Frontend Engineer",
        employer="Harbour Labs",
        place=Place(label="Bengaluru", city="Bengaluru", precision="city"),
        workMode="onsite",
        checkedAt=NOW.isoformat(),
        applyUrl="https://job-boards.greenhouse.io/harbourlabs/jobs/101",
        applyHost="job-boards.greenhouse.io",
        foundVia="web",
        fit="good",
        level="at",
        met=[
            AskMet(
                ask="React and TypeScript",
                cite=Cite(source="Resume", text="Built the checkout in React"),
                partly=False,
            )
        ],
        gaps=["GraphQL"],
        asks=AskCounts(met=1, partly=0, notShown=1),
        quickFit=QuickFit(word="worth_a_look", named=2, matched=1, notFound=["GraphQL"], reasons=[]),
    )


async def test_the_job_search_hands_a_role_and_the_posting_it_read_to_practice(
    build_settings: MakeSettings,
) -> None:
    async with running(build_settings()) as env:
        run_id, job_id = "1" * 32, "2" * 32
        run = JobSearchRun(
            runId=run_id, createdAt=NOW.isoformat(), remote=True, jobs=[ai_job(job_id)], funnel=[], trail=[]
        )
        await env.container.repos.jobsearch.save_run(
            StoredSearchRun(
                id=run_id,
                profile_id="p1",
                city=None,
                remote=True,
                created_at=NOW,
                result=run.to_dict(),
                postings={
                    job_id: {
                        "title": "Frontend Engineer",
                        "employer": "Harbour Labs",
                        "applyUrl": "u",
                        "text": "We need React.",
                    }
                },
            )
        )
        jobsearch = env.container.jobsearch
        found = await jobsearch.role("p1", run_id, job_id)
        assert found is not None
        job, text = found
        assert (job.id, job.employer, job.gaps, text) == (
            job_id,
            "Harbour Labs",
            ["GraphQL"],
            "We need React.",
        )
        assert await jobsearch.role("p2", run_id, job_id) is None  # another profile's run
        assert await jobsearch.role("p1", run_id, "f" * 32) is None  # a role the run did not show
        assert await jobsearch.role("p1", "3" * 32, job_id) is None  # a run that is not there
        assert (
            await jobsearch.role("p1", run_id, job_id) == found
        )  # and it is the same answer the private reader gives
        assert found == await jobsearch._role("p1", run_id, job_id)


# ───────────────────────── the real service, wired by the container ─────────────────────────


async def test_the_container_builds_the_real_service_over_the_job_search(
    build_settings: MakeSettings,
) -> None:
    service_module = pytest.importorskip("tayyari.interview.service")
    async with running(build_settings(interview={"assistant_messages": 5})) as env:
        service = env.container.interview
        assert isinstance(service, service_module.InterviewService)
        assert service.enabled is True


async def test_a_role_from_the_sample_graph_is_planned_and_started_by_the_real_service(
    build_settings: MakeSettings,
) -> None:
    """The graph-role path end to end: the plan from the sample profile's own Senior Frontend Engineer role (no model call,
    no search read), then a start whose phrasing fails, so the interviewer asks the role's biggest gap from a template."""
    async with running(build_settings(guard=roomy())) as env:
        body = {
            "profileId": env.sample_id,
            "kind": "role",
            "graphRole": {"roleId": "senior-frontend-engineer"},
        }
        planned = await env.client.post("/api/interview/plan", json=body)
        assert planned.status_code == 200, planned.text
        plan = InterviewPlan.model_validate(planned.json())
        assert (plan.title, plan.role, plan.graphRole and plan.graphRole.roleId) == (
            "Senior Frontend Engineer",
            None,
            "senior-frontend-engineer",
        )
        assert [(s.kind, s.topic) for s in plan.slots][:3] == [
            ("gap", "Automated testing"),
            ("gap", "System design"),
            ("claim", "React"),
        ]
        assert plan.slots[0].sources[0] == SlotSource(origin="role", text="Automated testing")
        assert env.claude.calls == []

        env.claude.script_structured("interview.phrase", ClaudeOverloaded())
        started = await env.client.post("/api/interview/sessions", json=body)
        assert started.status_code == 200, started.text
        session = InterviewSession.model_validate(started.json())
        assert session.graphRole is not None and session.graphRole.roleId == "senior-frontend-engineer"
        assert session.current is not None and session.current.text == (
            "The role needs “Automated testing”. How would you handle that in your first month?"
        )

        gone = await env.client.post(
            "/api/interview/plan", json={**body, "graphRole": {"roleId": "no-such-role"}}
        )
        assert gone.status_code == 404 and gone.json() == {"error": GraphRoleGone.user_message}
        both = await env.client.post("/api/interview/plan", json={**body, "role": RUN})
        assert both.status_code == 400 and both.json() == {"error": "Choose one role to practise for."}


async def test_switched_off_by_setting_the_real_service_answers_503(build_settings: MakeSettings) -> None:
    pytest.importorskip("tayyari.interview.service")
    async with running(build_settings(interview={"enabled": False})) as env:
        found = profile_of("frontend")
        await env.container.repos.profiles.save(
            found.id, found.to_dict(), StoredContext(), goal=found.goal, owner_hash=None, is_sample=False
        )
        response = await env.client.post(
            "/api/interview/sessions", json={"profileId": found.id, "kind": "resume"}
        )
        assert response.status_code == 503 and response.json() == {"error": OFF}
        assert env.claude.calls == []


# ───────────────────────── the voice made ahead ─────────────────────────

NOTHING = SpeechUpcoming(now=(), soon=(), next_turn=())
MOVED_ON = SpeechUpcoming(
    now=("That's fine. I'll note it and we'll move on.", "How did you decide?"),
    soon=("What would you change?", "Who reviewed it?"),
    next_turn=("What would you change?",),
)
ONE_AT_A_TIME = SpeechSettings(tts_prefetch_concurrency=1)
"""One line at a time, so the engine sees the lines in the order they were asked for."""


def spoken(voice: FakeSpeechEngine) -> list[str]:
    return [made.text for made in voice.spoken]


async def voice_done(rig: Rig) -> None:
    """Wait for every line the routes asked the speech service to make."""
    await rig.env.container.speech.drain()


async def test_starting_has_the_current_line_and_what_follows_made_once_the_answer_is_out(start: Any) -> None:
    voice = FakeSpeechEngine()
    async with start(voice=voice, speech=ONE_AT_A_TIME) as rig:
        await rig.profile()
        response = await rig.start()
        assert response.status_code == 200 and InterviewSession.model_validate(response.json())
        sid = response.json()["id"]
        await voice_done(rig)
        assert spoken(voice) == [
            LEAD,
            QUESTION,
            *NEXT_UP,
        ]  # the line first (its lead, then its text), then what follows
        assert rig.stub.names() == ["start", "speech_upcoming"]
        assert rig.stub.calls[-1].args == (sid, True)
        for text in NEXT_UP:
            assert text not in response.text  # what may come next never goes to the browser


async def test_an_answer_has_the_next_turns_lines_made_while_it_is_judged_and_the_new_line_after(
    start: Any,
) -> None:
    voice = FakeSpeechEngine()
    async with start(voice=voice, speech=ONE_AT_A_TIME) as rig:
        await rig.profile()
        rig.stub.upcoming = NOTHING
        sid = await rig.started()
        rig.stub.upcoming, rig.stub.after_answer = UPCOMING, MOVED_ON
        rig.stub.calls.clear()
        answered = await rig.client.post(
            f"/api/interview/sessions/{sid}/answer",
            json={"profileId": FRONTEND_ID, "text": "I cut the load time by 70%.", "mode": "speech"},
        )
        assert answered.status_code == 200
        await voice_done(rig)
        # before the judge reads the answer, then after the reply: the line and what follows it
        assert rig.stub.names() == ["speech_upcoming", "answer", "speech_upcoming"]
        assert [c.args[0] for c in rig.stub.calls] == [sid, sid, sid]
        assert spoken(voice) == [NEXT_UP[0], *MOVED_ON.now, *MOVED_ON.soon]
        for text in (*MOVED_ON.now[:1], *MOVED_ON.soon, NEXT_UP[0]):
            assert text not in answered.text


async def test_the_lines_before_the_judge_are_asked_for_before_the_spend_is_held(start: Any) -> None:
    voice = FakeSpeechEngine()
    async with start(voice=voice) as rig:
        await rig.profile()
        sid = await rig.started()
        rig.stub.calls.clear()
        await rig.client.post(
            f"/api/interview/sessions/{sid}/answer",
            json={"profileId": FRONTEND_ID, "text": "I cut the load time by 70%.", "mode": "speech"},
        )
        before, judging, after = rig.stub.calls
        assert (before.method, judging.method, after.method) == (
            "speech_upcoming",
            "answer",
            "speech_upcoming",
        )
        assert before.held_micro == 0 and judging.held_micro > 0 and after.held_micro == 0


async def test_handing_in_work_asking_again_and_a_reload_each_get_their_lines_made(start: Any) -> None:
    voice = FakeSpeechEngine()
    async with start(voice=voice) as rig:
        await rig.profile()
        sid = await rig.started()
        base = f"/api/interview/sessions/{sid}"
        body = {"profileId": FRONTEND_ID}
        rig.stub.calls.clear()

        assert (
            await rig.client.post(f"{base}/work", json={**body, "work": "export const x = 1;"})
        ).status_code == 200
        assert [(c.method, c.args[0]) for c in rig.stub.calls] == [
            ("submit_work", sid),
            ("speech_upcoming", sid),
        ]

        rig.stub.calls.clear()
        again = await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"})
        new_id = again.json()["id"]
        assert new_id != sid
        assert [(c.method, c.args[0]) for c in rig.stub.calls] == [
            ("again", sid),
            ("speech_upcoming", new_id),
        ]

        rig.stub.calls.clear()
        assert (await rig.client.get(base, params=body)).status_code == 200
        assert [(c.method, c.args[0]) for c in rig.stub.calls] == [("view", sid), ("speech_upcoming", sid)]
        await voice_done(rig)
        assert set(spoken(voice)) == {LEAD, QUESTION, *NEXT_UP}


async def test_a_finished_interview_that_is_reloaded_gets_nothing_made(start: Any) -> None:
    voice = FakeSpeechEngine()
    async with start(voice=voice) as rig:
        await rig.profile()
        sid = await rig.started()
        await voice_done(rig)
        voice.spoken.clear()
        rig.stub.sessions[(FRONTEND_ID, sid)] = session_of(FRONTEND_ID, sid, status="done")
        rig.stub.calls.clear()
        response = await rig.client.get(f"/api/interview/sessions/{sid}", params={"profileId": FRONTEND_ID})
        assert response.status_code == 200
        assert rig.stub.names() == ["view"]  # not even asked what to make
        assert voice.calls == 0


async def test_nothing_is_made_for_a_session_the_interview_does_not_know(start: Any) -> None:
    voice = FakeSpeechEngine()
    async with start(voice=voice) as rig:
        await rig.profile()
        response = await rig.client.post(
            f"/api/interview/sessions/{'f' * 32}/answer",
            json={"profileId": FRONTEND_ID, "text": "Something I did.", "mode": "typed"},
        )
        assert response.status_code == 404 and response.json() == {"error": SESSION_NOT_FOUND}
        await voice_done(rig)
        assert voice.calls == 0  # the stub says there is nothing to get ready for a session it does not know


@pytest.mark.parametrize(
    ("with_engine", "speech"),
    [
        (False, SpeechSettings()),
        (True, SpeechSettings(enabled=False)),
        (True, SpeechSettings(tts_prefetch=False)),
        (True, SpeechSettings(tts_cache_items=0)),
    ],
    ids=["no key", "speech switched off", "prefetch switched off", "no cache to keep them in"],
)
async def test_with_speech_off_no_route_makes_or_asks_for_anything(
    start: Any, with_engine: bool, speech: SpeechSettings
) -> None:
    voice = FakeSpeechEngine() if with_engine else None
    async with start(voice=voice, speech=speech) as rig:
        await rig.profile()
        sid = await rig.started()
        base = f"/api/interview/sessions/{sid}"
        body = {"profileId": FRONTEND_ID}
        answered = await rig.client.post(
            f"{base}/answer", json={**body, "text": "Cut it by 70%.", "mode": "typed"}
        )
        assert answered.status_code == 200
        assert (await rig.client.post(f"{base}/work", json={**body, "work": "x"})).status_code == 200
        assert (await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"})).status_code == 200
        assert (await rig.client.get(base, params=body)).status_code == 200
        await voice_done(rig)
        assert "speech_upcoming" not in rig.stub.names()
        assert voice is None or voice.calls == 0


async def test_the_followups_setting_is_passed_on_to_the_interview(start: Any) -> None:
    for followups in (True, False):
        async with start(
            voice=FakeSpeechEngine(), speech=SpeechSettings(tts_prefetch_followups=followups)
        ) as rig:
            await rig.profile()
            sid = await rig.started()
            await voice_done(rig)
            assert rig.stub.calls[-1].method == "speech_upcoming"
            assert rig.stub.calls[-1].args == (sid, followups)


@pytest.mark.parametrize("failure", ["interview", "engine"])
async def test_a_problem_making_lines_ahead_never_changes_what_a_route_answers(
    start: Any, failure: str
) -> None:
    voice = FakeSpeechEngine(speak_error=SpeechUnavailable() if failure == "engine" else None)
    async with start(voice=voice) as rig:
        await rig.profile()
        if failure == "interview":
            rig.stub.upcoming_error = RuntimeError("the session store hiccuped")
        started = await rig.start()
        assert started.status_code == 200
        sid = started.json()["id"]
        base = f"/api/interview/sessions/{sid}"
        body = {"profileId": FRONTEND_ID}
        answered = await rig.client.post(
            f"{base}/answer", json={**body, "text": "Cut it by 70%.", "mode": "typed"}
        )
        assert answered.status_code == 200
        assert InterviewSession.model_validate(answered.json()).turns[0].text == "Cut it by 70%."
        for response in (
            await rig.client.post(f"{base}/work", json={**body, "work": "x"}),
            await rig.client.post(f"{base}/again", json={**body, "slotId": "s1"}),
            await rig.client.get(base, params=body),
        ):
            assert response.status_code == 200 and is_no_store(response)
        await voice_done(rig)
        assert "speech_upcoming" in rig.stub.names()  # it was tried, and failed quietly


async def test_lines_the_budget_would_not_cover_are_left_unmade_without_a_word(start: Any) -> None:
    voice = FakeSpeechEngine()
    # the day has room for the lead and the question (51 characters) and for nothing after them
    async with start(
        voice=voice, speech=SpeechSettings(tts_daily_chars=60, tts_prefetch_concurrency=1)
    ) as rig:
        await rig.profile()
        response = await rig.start()
        assert response.status_code == 200
        await voice_done(rig)
        assert spoken(voice) == [LEAD, QUESTION]
    voice.spoken.clear()
    async with start(
        voice=voice, speech=SpeechSettings(tts_session_chars=40, tts_prefetch_concurrency=1)
    ) as rig:
        await rig.profile()
        assert (await rig.start()).status_code == 200
        await voice_done(rig)
        assert spoken(voice) == [LEAD]  # this interview's share: 32 used, 8 left, and the question needs 19


async def test_nothing_is_made_once_the_days_or_the_sessions_speech_is_used_up(start: Any) -> None:
    for settings in (SpeechSettings(tts_daily_chars=60), SpeechSettings(tts_session_chars=60)):
        voice = FakeSpeechEngine()
        async with start(voice=voice, speech=settings) as rig:
            await rig.profile()
            sid = await rig.started()
            await voice_done(rig)
            assert voice.spoken  # the first lines were made
            speech = rig.env.container.speech
            await speech.meter.charge(sid, tts_chars=1000)  # the day, and this interview, are spent now
            assert (await speech.status(sid)).tts is False
            voice.spoken.clear()
            rig.stub.upcoming = SpeechUpcoming(
                now=("A new line.",), soon=("Another new line.",), next_turn=()
            )
            body = {"profileId": FRONTEND_ID}
            assert (await rig.client.get(f"/api/interview/sessions/{sid}", params=body)).status_code == 200
            await voice_done(rig)
            assert voice.calls == 0


async def test_only_lines_that_can_be_spoken_are_made_and_each_once(start: Any) -> None:
    voice = FakeSpeechEngine()
    async with start(voice=voice) as rig:
        await rig.profile()
        rig.stub.upcoming = SpeechUpcoming(
            now=("  Only this.  ", ""), soon=("Only this.", "x" * 5000), next_turn=()
        )
        await rig.started()
        await voice_done(rig)
        assert spoken(voice) == [
            "Only this."
        ]  # tidied, once, and a line too long to be spoken is passed over
