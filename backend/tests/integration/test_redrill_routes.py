"""Re-drill and Fix steps end to end (I2) through the real app over a real SQLite file: the real interview and prepare
services, a profile stored as a plain one, claim marks written straight to their table (as a finished session leaves them),
and a Prepare plan stored from the golden fixture. Nothing here calls Claude or the network, except the one scripted
phrasing call of a re-drill start.

    GET    /api/interview/redrill        the claims in code's order; empty for the shared sample; 404 for an unknown profile
    POST   /api/interview/plan           kind redrill: slots opening where they wobbled; a gone claim is a 400
    POST   /api/interview/sessions       kind redrill: counted in the interview window like any interview
    POST   /api/interview/fixes          into a plan of this profile, or kept; another profile's session or plan is a 404
    PATCH  /api/interview/fixes/{id}     ticked: the claim comes first
    DELETE /api/interview/fixes/{id}     204, then 404
    GET    /api/prepare/{id}             carries the plan's Fix steps; GET /api/prepare?profileId= lists the plans
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from tayyari.contract.models import Profile
from tayyari.contract.stored import StoredContext
from tayyari.core.paths import REPO_ROOT, SAMPLE_DIR
from tayyari.core.settings import Settings
from tayyari.interview.marks import claim_key
from tayyari.jobsearch.brief import evidence_lines
from tayyari.storage.ports import StoredClaimMark, StoredPlan
from tests.integration.support import Env, RecordingClaude, roomy_guard, running

MakeSettings = Callable[..., Settings]
PID = "1" * 32
OTHER = "2" * 32
SESSION = "a" * 32
PLAN = "c" * 32
OTHER_PLAN = "d" * 32
T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
PERSONA: Profile = Profile.from_dict(
    json.loads((SAMPLE_DIR / "sample-profile.json").read_text(encoding="utf-8"))
)
LINES = [e.cite.text for e in evidence_lines(PERSONA)]
PLAN_JSON = REPO_ROOT / "backend" / "tests" / "data" / "prepare" / "riya.prepare.json"


def mark(mark_id: str, slot: str, text: str, verdict: str, *, profile_id: str = PID) -> StoredClaimMark:
    return StoredClaimMark(
        id=mark_id,
        profile_id=profile_id,
        claim_key=claim_key(text),
        claim_text=text,
        source="Resume",
        session_id=SESSION,
        slot_id=slot,
        session_kind="resume",
        slot_kind="claim",
        verdict=verdict,
        rung="push" if verdict == "shaky" else "surface",
        question=f"Walk me through this: {text[:60]}?",
        marked_on=date(2026, 10, 16),
        created_at=T0,
        quote="we did a lot of things there",
        fix_text=None
        if verdict == "defended"
        else "Write down one real example: what you did and what changed.",
        fix_minutes=None if verdict == "defended" else 15,
    )


def plan_row(plan_id: str, profile_id: str) -> StoredPlan:
    content = json.loads(PLAN_JSON.read_text(encoding="utf-8"))
    state = content.pop("state")
    content.pop("version")
    content.update(id=plan_id, profileId=profile_id)
    return StoredPlan(
        id=plan_id,
        profile_id=profile_id,
        role_id=content["roleId"],
        requirement=None,
        content=content,
        state=state,
        version=1,
        created_at=T0,
        updated_at=T0,
    )


@dataclass
class Rig:
    env: Env

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def redrill(self, profile_id: str = PID) -> httpx.Response:
        return await self.client.get("/api/interview/redrill", params={"profileId": profile_id})

    async def fix(
        self, slot: str, plan_id: str | None = None, *, profile_id: str = PID, session: str = SESSION
    ) -> httpx.Response:
        body: dict[str, Any] = {"profileId": profile_id, "sessionId": session, "slotId": slot}
        if plan_id is not None:
            body["planId"] = plan_id
        return await self.client.post("/api/interview/fixes", json=body)


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**overrides: Any) -> AsyncIterator[Rig]:
        overrides.setdefault("guard", roomy_guard())
        claude = RecordingClaude()
        claude.script_structured("interview.phrase", {"slots": []}, repeat=True)  # the templates stand in
        async with running(build_settings(**overrides), claude=claude) as env:
            repos = env.container.repos
            for pid in (PID, OTHER):
                doc = {**PERSONA.to_dict(), "id": pid}
                await repos.profiles.save(
                    pid, doc, StoredContext(), goal=None, owner_hash=None, is_sample=False
                )
            await repos.claim_marks.add(
                [
                    mark("e" + "0" * 31, "s1", LINES[0], "shaky"),
                    mark("e" + "1" * 31, "s2", LINES[1], "couldnt"),
                    mark("e" + "2" * 31, "s3", LINES[2], "defended"),
                ]
            )
            await repos.prepare.create(plan_row(PLAN, PID))
            await repos.prepare.create(plan_row(OTHER_PLAN, OTHER))
            yield Rig(env)

    return run


# ───────────────────────── the view and the plan ─────────────────────────


async def test_the_view_lists_what_wobbled_couldnt_first_and_nothing_for_the_sample(start: Any) -> None:
    async with start() as rig:
        response = await rig.redrill()
        sample = await rig.redrill(rig.env.sample_id)
        unknown = await rig.redrill("f" * 32)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    view = response.json()
    assert [c["text"] for c in view["claims"]] == [LINES[1], LINES[0]]
    assert [(c["best"], c["start"]) for c in view["claims"]] == [("couldnt", "surface"), ("shaky", "push")]
    assert view["kept"] == [] and view["leftToday"] == 1000
    assert sample.status_code == 200 and sample.json() == {"claims": [], "kept": []}
    assert unknown.status_code == 404


async def test_a_redrill_plan_opens_where_it_wobbled_and_a_gone_claim_is_refused(start: Any) -> None:
    async with start() as rig:
        plan = await rig.client.post("/api/interview/plan", json={"profileId": PID, "kind": "redrill"})
        gone = await rig.client.post(
            "/api/interview/plan", json={"profileId": PID, "kind": "redrill", "claims": ["not a claim"]}
        )
    assert plan.status_code == 200
    slots = plan.json()["slots"]
    assert [(s["start"], s["last"]["verdict"]) for s in slots] == [("surface", "couldnt"), ("push", "shaky")]
    assert gone.status_code == 400


async def test_starting_a_redrill_counts_as_an_interview_of_the_day(start: Any) -> None:
    async with start(guard=roomy_guard(interview_per_day=1)) as rig:
        first = await rig.client.post("/api/interview/sessions", json={"profileId": PID, "kind": "redrill"})
        second = await rig.client.post("/api/interview/sessions", json={"profileId": PID, "kind": "redrill"})
    assert first.status_code == 200, first.text
    session = first.json()
    assert session["kind"] == "redrill" and session["current"]["rung"] == "surface"
    assert second.status_code == 429


# ───────────────────────── Fix steps ─────────────────────────


async def test_a_fix_step_goes_into_a_plan_shows_on_the_plan_is_ticked_and_taken_out(start: Any) -> None:
    async with start() as rig:
        added = await rig.fix("s1", PLAN)
        task = added.json()
        plan = await rig.client.get(f"/api/prepare/{PLAN}", params={"profileId": PID})
        listed = await rig.client.get("/api/prepare", params={"profileId": PID})
        ticked = await rig.client.patch(
            f"/api/interview/fixes/{task['id']}", json={"profileId": PID, "done": True}
        )
        view = await rig.redrill()
        removed = await rig.client.delete(f"/api/interview/fixes/{task['id']}", params={"profileId": PID})
        again = await rig.client.delete(f"/api/interview/fixes/{task['id']}", params={"profileId": PID})
        after = await rig.client.get(f"/api/prepare/{PLAN}", params={"profileId": PID})
    assert added.status_code == 200 and added.headers["cache-control"] == "no-store"
    assert (task["where"], task["planId"], task["claimText"], task["minutes"]) == ("plan", PLAN, LINES[0], 15)
    assert plan.status_code == 200 and [t["id"] for t in plan.json()["practice"]] == [task["id"]]
    assert listed.status_code == 200 and [p["id"] for p in listed.json()] == [PLAN]
    assert ticked.status_code == 200 and ticked.json()["doneAt"]
    assert view.json()["claims"][0]["text"] == LINES[0]  # its Fix step is ticked: it comes first now
    assert removed.status_code == 204 and again.status_code == 404
    assert "practice" not in after.json()


async def test_a_fix_step_kept_in_practice_is_listed_with_the_redrill(start: Any) -> None:
    async with start() as rig:
        kept = await rig.fix("s2")
        view = await rig.redrill()
    assert kept.status_code == 200 and kept.json()["where"] == "practice"
    assert [t["id"] for t in view.json()["kept"]] == [kept.json()["id"]]


async def test_fix_steps_refuse_what_is_not_this_profiles(start: Any) -> None:
    async with start() as rig:
        defended = await rig.fix("s3")
        their_plan = await rig.fix("s1", OTHER_PLAN)
        their_session = await rig.fix("s1", profile_id=OTHER)
        bad_session = await rig.fix("s1", session="not-hex")
        bad_plan = await rig.fix("s1", "not-hex")
        task = (await rig.fix("s1")).json()
        their_tick = await rig.client.patch(
            f"/api/interview/fixes/{task['id']}", json={"profileId": OTHER, "done": True}
        )
        bad_id = await rig.client.patch("/api/interview/fixes/nope", json={"profileId": PID, "done": True})
        their_delete = await rig.client.delete(
            f"/api/interview/fixes/{task['id']}", params={"profileId": OTHER}
        )
    for response in (
        defended,
        their_plan,
        their_session,
        bad_session,
        bad_plan,
        their_tick,
        bad_id,
        their_delete,
    ):
        assert response.status_code == 404, response.text
