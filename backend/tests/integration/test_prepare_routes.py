"""Prepare's routes end to end through the real app over a real SQLite file, with the real `PrepareService` and a scripted
Claude at the edge: the stream and what it stores, the stored plan handed back, reading a plan, saving its state (and the
409 of a stale tab), the repo ranking, the headline, the profile each may open, the body limits, the windows and the spend
each counts, the 404s, and `Cache-Control: no-store` on every answer. Nothing here calls the network.

The plans are made for the bundled sample persona (Riya), whose graph has roles with requirements; a claimed profile is a
copy of hers under another id.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tayyari.contract.prepare import (
    PrepareHeadline,
    PreparePlan,
    PrepareRepos,
    PrepareStateSaved,
)
from tayyari.core.settings import GuardSettings, Settings
from tayyari.llm.errors import SPEND_CEILING_MESSAGE, ClaudeOverloaded
from tayyari.prepare.errors import (
    ALREADY_STRONG_REQUIREMENT,
    NO_MOVES_CODE,
    PLAN_NOT_FOUND,
    REQUIREMENT_NOT_FOUND,
    ROLE_NOT_FOUND,
)
from tayyari.storage.tables import LlmCallRow, RateLimitHitRow
from tests.fakes.claude import StructuredCall
from tests.integration.support import Env, RecordingClaude, ndjson_lines, roomy_guard, running
from tests.unit.prepare.support import headline_answer, moves_answer, riya, standing_answer

MakeSettings = Callable[..., Settings]
SESSION_COOKIE = "tayyari_session"
ROLE = "ai-product-engineer"
COPY_ID = "c" * 32
"""A copy of Riya's graph stored as an ordinary profile (claimable)."""
NOT_PROFILE = "Profile not found"


def events_of(response: httpx.Response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in ndjson_lines(response)]


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def is_no_store(response: httpx.Response) -> bool:
    return response.headers.get("cache-control") == "no-store"


def roomy(**overrides: Any) -> GuardSettings:
    return roomy_guard(**overrides)


def micro(usd: float) -> int:
    return round(usd * 1_000_000)


@dataclass
class Rig:
    env: Env

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    @property
    def sample(self) -> str:
        return self.env.sample_id

    async def copy(self, *, owner: str | None = None) -> str:
        """Riya's graph stored again under `COPY_ID` as a plain profile, claimed by ``owner`` when given."""
        profile, context = riya()
        doc = {**profile.to_dict(), "id": COPY_ID}
        container = self.env.container
        await container.repos.profiles.save(
            COPY_ID, doc, context, goal=profile.goal, owner_hash=None, is_sample=False
        )
        if owner is not None:
            await container.repos.users.claim_profile(owner, COPY_ID)
        return COPY_ID

    async def account(self, email: str = "ana@example.com") -> tuple[str, dict[str, str]]:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return user.id, {"cookie": f"{SESSION_COOKIE}={token}"}

    async def prepare(
        self,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        *,
        profile_id: str | None = None,
        role_id: str = ROLE,
        **extra: Any,
    ) -> httpx.Response:
        payload = (
            body if body is not None else {"profileId": profile_id or self.sample, "roleId": role_id, **extra}
        )
        return await self.client.post("/api/prepare", json=payload, headers=headers or {})

    async def plan(
        self, profile_id: str | None = None, headers: dict[str, str] | None = None, **extra: Any
    ) -> PreparePlan:
        response = await self.prepare(profile_id=profile_id, headers=headers, **extra)
        assert response.status_code == 200, response.text
        events = events_of(response)
        assert events[-1]["type"] == "done", events[-1]
        return PreparePlan.model_validate(events[-1]["plan"])

    def at(self, ip: str) -> dict[str, str]:
        return {"cf-connecting-ip": ip}


def good_claude() -> RecordingClaude:
    claude = RecordingClaude()
    claude.script_structured("prepare.standing", standing_answer, repeat=True)
    claude.script_structured("prepare.moves", moves_answer, repeat=True)
    claude.script_structured("prepare.headline", headline_answer, repeat=True)
    return claude


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(*, claude: RecordingClaude | None = None, **overrides: Any) -> AsyncIterator[Rig]:
        overrides.setdefault("guard", roomy())
        async with running(build_settings(**overrides), claude=claude or good_claude()) as env:
            yield Rig(env)

    return run


# ───────────────────────── the whole journey ─────────────────────────


async def test_a_plan_streams_gaps_then_both_calls_then_done_and_is_stored(start: Any) -> None:
    async with start() as rig:
        response = await rig.prepare(headers={"x-request-id": "req-plan-1"})
        assert response.status_code == 200 and is_no_store(response)
        assert response.headers["content-type"] == "application/x-ndjson; charset=utf-8"
        events = events_of(response)
        assert kinds(events)[0] == "gaps" and kinds(events)[-1] == "done"
        assert sorted(kinds(events)[1:3]) == ["moves", "standing"]
        gaps = PreparePlan.model_validate(events[0]["plan"])
        assert gaps.moves == [] and gaps.headline is None and all(g.why is None for g in gaps.gaps)
        assert gaps.gaps[0].name == "LLM evals" and gaps.gaps[0].biggest
        assert (gaps.rank, gaps.roleCount, gaps.eta) == (5, 5, "~9 months")
        done = PreparePlan.model_validate(events[-1]["plan"])
        assert done.id == gaps.id and len(done.id) == 32
        assert done.headline and sum(1 for g in done.gaps if g.why) == 5
        assert [m.id for m in done.moves] == ["m1", "m2", "m3"] and done.version == 1
        assert done.state.to_dict() == {"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []}

        read = await rig.client.get(f"/api/prepare/{done.id}", params={"profileId": rig.sample})
        assert read.status_code == 200 and is_no_store(read)
        assert PreparePlan.model_validate(read.json()) == done

        purposes = sorted(c.purpose for c in rig.env.claude.structured_calls)
        assert purposes == ["prepare.moves", "prepare.standing"]
        assert all(
            c.ctx.request_id == "req-plan-1" and c.ctx.profile_id == rig.sample
            for c in rig.env.claude.structured_calls
        )
        assert all(c.model == rig.env.settings.chat_model for c in rig.env.claude.structured_calls)
        recorded = await rig.env.rows(LlmCallRow)
        assert sorted(r.purpose for r in recorded) == ["prepare.moves", "prepare.standing"]
        assert all(r.cost_micro_usd > 0 for r in recorded)
        assert rig.env.container.spend_guard.reserved_micro == 0


async def test_a_stored_plan_is_streamed_back_at_once_without_a_call_a_counter_or_a_reservation(
    start: Any,
) -> None:
    async with start(guard=roomy(prepare_per_day=1)) as rig:
        first = await rig.plan()
        calls = len(rig.env.claude.structured_calls)
        again = await rig.prepare()
        assert again.status_code == 200 and is_no_store(again)
        events = events_of(again)
        assert kinds(events) == ["gaps", "done"]
        assert PreparePlan.model_validate(events[1]["plan"]) == first
        assert len(rig.env.claude.structured_calls) == calls
        counted = [
            h.count for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("prepare:day:")
        ]
        assert counted == [1]  # the first plan only
        # the day's one plan is spent, so a fresh one is refused, and the stored one still comes back
        fresh = await rig.prepare(fresh=True)
        assert fresh.status_code == 429
        assert kinds(events_of(await rig.prepare())) == ["gaps", "done"]


async def test_fresh_writes_a_new_plan_and_the_newest_is_the_one_handed_back(start: Any) -> None:
    async with start() as rig:
        first = await rig.plan()
        second = await rig.plan(fresh=True)
        assert second.id != first.id
        assert kinds(events_of(await rig.prepare(fresh=True)))[-1] == "done"
        stored = await rig.prepare()
        assert PreparePlan.model_validate(events_of(stored)[-1]["plan"]).id != first.id
        both = [
            (await rig.client.get(f"/api/prepare/{pid}", params={"profileId": rig.sample})).status_code
            for pid in (first.id, second.id)
        ]
        assert both == [200, 200]  # the older plan is still readable by its id


async def test_a_plan_for_one_requirement_is_its_own_plan(start: Any) -> None:
    async with start() as rig:
        whole = await rig.plan()
        one = await rig.plan(requirement="rag & retrieval")
        assert one.id != whole.id and one.scope == "requirement" and one.requirement == "RAG & retrieval"
        assert [g.name for g in one.gaps] == ["RAG & retrieval"]
        again = events_of(await rig.prepare(requirement="RAG & retrieval"))
        assert kinds(again) == ["gaps", "done"] and again[1]["plan"]["id"] == one.id


async def test_the_state_is_saved_with_a_compare_and_swap(start: Any) -> None:
    async with start() as rig:
        plan = await rig.plan()
        url = f"/api/prepare/{plan.id}/state"
        state = {
            "hoursPerWeek": 10,
            "moveIndex": 1,
            "done": ["m1:0:0", "m2:1:1"],
            "links": [
                {
                    "moveId": "m1",
                    "phaseIndex": 1,
                    "url": "https://github.com/x/y/pull/1",
                    "addedAt": "2026-10-18T09:00:00Z",
                }
            ],
        }
        saved = await rig.client.put(url, json={"profileId": rig.sample, "version": 1, "state": state})
        assert saved.status_code == 200 and is_no_store(saved)
        assert PrepareStateSaved.model_validate(saved.json()) == PrepareStateSaved.model_validate(
            {"state": state, "version": 2}
        )
        read = await rig.client.get(f"/api/prepare/{plan.id}", params={"profileId": rig.sample})
        assert read.json()["version"] == 2 and read.json()["state"] == state

        stale = await rig.client.put(
            url, json={"profileId": rig.sample, "version": 1, "state": {**state, "hoursPerWeek": 3}}
        )
        assert stale.status_code == 409 and stale.json() == {
            "error": "This plan changed in another tab. Reload it and try again."
        }
        assert (await rig.client.get(f"/api/prepare/{plan.id}", params={"profileId": rig.sample})).json()[
            "state"
        ] == state

        bad_key = await rig.client.put(
            url, json={"profileId": rig.sample, "version": 2, "state": {**state, "done": ["m9:0:0"]}}
        )
        bad_link = await rig.client.put(
            url,
            json={
                "profileId": rig.sample,
                "version": 2,
                "state": {**state, "links": [{**state["links"][0], "url": "ftp://x"}]},
            },
        )
        too_many = await rig.client.put(
            url, json={"profileId": rig.sample, "version": 2, "state": {**state, "hoursPerWeek": 41}}
        )
        assert [r.status_code for r in (bad_key, bad_link, too_many)] == [400, 400, 400]
        assert "hoursPerWeek" in too_many.json()["error"]
        assert rig.env.claude.calls_for("prepare.headline") == []  # no model was asked by any of this


async def test_the_repos_are_ranked_from_the_stored_github_digest(start: Any) -> None:
    async with start() as rig:
        plan = await rig.plan()
        calls = len(rig.env.claude.structured_calls)
        response = await rig.client.get(f"/api/prepare/{plan.id}/repos", params={"profileId": rig.sample})
        assert response.status_code == 200 and is_no_store(response)
        repos = PrepareRepos.model_validate(response.json())
        assert repos.github is True and len(repos.repos) == 10
        assert sum(1 for r in repos.repos if r.suggested) <= 1
        assert all(
            r.url is not None and r.url.startswith("https://github.com/riyakapoor/") for r in repos.repos
        )
        assert len(rig.env.claude.structured_calls) == calls


async def test_the_headline_is_one_more_call_counted_and_reserved_like_a_plan(start: Any) -> None:
    guard = roomy(prepare_per_day=2)
    async with start(guard=guard) as rig:
        plan = await rig.plan()
        url = f"/api/prepare/{plan.id}/headline"
        response = await rig.client.post(url, json={"profileId": rig.sample})
        assert response.status_code == 200 and is_no_store(response)
        headline = PrepareHeadline.model_validate(response.json())
        assert (
            headline.current == "Frontend Engineer · 2.4 yrs · Bengaluru"
            and headline.proposed == headline.current
        )
        assert headline.evidenceIds == ["e1"] and headline.note is None
        call = rig.env.claude.calls_for("prepare.headline")[0]
        assert call.ctx.profile_id == rig.sample
        counted = [
            h.count for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("prepare:day:")
        ]
        assert counted == [2]
        refused = await rig.client.post(url, json={"profileId": rig.sample})
        assert refused.status_code == 429 and "plans" in refused.json()["error"]
        assert rig.env.container.spend_guard.reserved_micro == 0


async def test_a_headline_that_fails_the_check_is_not_proposed(start: Any) -> None:
    claude = good_claude()
    claude._queues.structured["prepare.headline"] = []
    claude.script_structured(
        "prepare.headline", {"evidenceIds": ["e1"], "headline": "Lead at Google · Bengaluru"}
    )
    async with start(claude=claude) as rig:
        plan = await rig.plan()
        response = await rig.client.post(f"/api/prepare/{plan.id}/headline", json={"profileId": rig.sample})
        assert response.status_code == 200
        body = response.json()
        assert "proposed" not in body and body["note"] and body["evidenceIds"] == ["e1"]


async def test_the_routes_are_registered(start: Any) -> None:
    async with start() as rig:
        routes: set[tuple[str, str]] = set()

        def walk(items: Any) -> None:
            for route in items:
                inner = getattr(getattr(route, "original_router", None), "routes", None)
                if inner is not None:
                    walk(inner)
                else:
                    routes.update((m, route.path) for m in getattr(route, "methods", None) or ())

        walk(rig.env.app.router.routes)
    mine = {(m, p) for m, p in routes if p.startswith("/api/prepare")}
    assert mine == {
        ("POST", "/api/prepare"),
        ("GET", "/api/prepare"),
        ("GET", "/api/prepare/{plan_id}"),
        ("PUT", "/api/prepare/{plan_id}/state"),
        ("GET", "/api/prepare/{plan_id}/repos"),
        ("POST", "/api/prepare/{plan_id}/headline"),
    }


# ───────────────────────── what the graph does not have ─────────────────────────


async def test_a_role_or_requirement_the_graph_lacks_and_a_met_requirement_are_refused_before_any_call(
    start: Any,
) -> None:
    async with start() as rig:
        no_role = await rig.prepare(role_id="staff-engineer")
        no_req = await rig.prepare(requirement="Rust")
        met = await rig.prepare(requirement="React")
        assert (no_role.status_code, no_role.json()) == (404, {"error": ROLE_NOT_FOUND})
        assert (no_req.status_code, no_req.json()) == (404, {"error": REQUIREMENT_NOT_FOUND})
        assert (met.status_code, met.json()) == (400, {"error": ALREADY_STRONG_REQUIREMENT})
        assert rig.env.claude.structured_calls == []
        assert await rig.env.rows(RateLimitHitRow, RateLimitHitRow.bucket.like("prepare%")) == []
        assert rig.env.container.spend_guard.reserved_micro == 0


async def test_when_no_move_survives_the_stream_ends_with_no_moves_and_nothing_is_stored(start: Any) -> None:
    claude = good_claude()
    claude._queues.structured["prepare.moves"] = []
    claude.script_structured("prepare.moves", ClaudeOverloaded())
    async with start(claude=claude) as rig:
        response = await rig.prepare()
        assert response.status_code == 200
        events = events_of(response)
        assert (
            kinds(events)[0] == "gaps"
            and events[-1]["type"] == "error"
            and events[-1]["code"] == NO_MOVES_CODE
        )
        stored = await rig.prepare()  # nothing was stored: this is a new run, not a replay
        assert events_of(stored)[-1]["type"] == "error"
        assert rig.env.container.spend_guard.reserved_micro == 0


# ───────────────────────── plans that are not there ─────────────────────────


async def test_an_unknown_plan_another_profiles_plan_and_a_bad_id_are_the_same_404(start: Any) -> None:
    async with start() as rig:
        plan = await rig.plan()
        copy_id = await rig.copy()
        params = {"profileId": rig.sample}
        gone = "d" * 32
        state = {
            "profileId": rig.sample,
            "version": 1,
            "state": {"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []},
        }
        responses = [
            await rig.client.get(f"/api/prepare/{gone}", params=params),
            await rig.client.put(f"/api/prepare/{gone}/state", json=state),
            await rig.client.get(f"/api/prepare/{gone}/repos", params=params),
            await rig.client.post(f"/api/prepare/{gone}/headline", json={"profileId": rig.sample}),
            await rig.client.get(f"/api/prepare/{plan.id}", params={"profileId": copy_id}),
            await rig.client.put(f"/api/prepare/{plan.id}/state", json={**state, "profileId": copy_id}),
            await rig.client.get(f"/api/prepare/{plan.id}/repos", params={"profileId": copy_id}),
            await rig.client.post(f"/api/prepare/{plan.id}/headline", json={"profileId": copy_id}),
        ]
        assert [r.status_code for r in responses] == [404] * 8
        assert {r.json()["error"] for r in responses} == {PLAN_NOT_FOUND}
        for bad in ("not-an-id", "A" * 32, "a" * 31, "g" * 32):
            calls = len(rig.env.claude.structured_calls)
            short = [
                await rig.client.get(f"/api/prepare/{bad}", params=params),
                await rig.client.put(
                    f"/api/prepare/{bad}/state", content=b"not json"
                ),  # the id is judged first
                await rig.client.post(f"/api/prepare/{bad}/headline", json={"profileId": rig.sample}),
            ]
            assert {r.status_code for r in short} == {404} and {r.json()["error"] for r in short} == {
                PLAN_NOT_FOUND
            }
            assert len(rig.env.claude.structured_calls) == calls
        assert (await rig.client.get(f"/api/prepare/{plan.id}", params=params)).status_code == 200


# ───────────────────────── who may open which profile ─────────────────────────


async def test_an_unknown_or_malformed_profile_is_refused_before_anything_is_counted_or_called(
    start: Any,
) -> None:
    async with start() as rig:
        plan = await rig.plan()
        calls = len(rig.env.claude.structured_calls)
        for bad in ("f" * 32, "../etc", "x" * 65):
            responses = [
                await rig.prepare(profile_id=bad),
                await rig.client.get(f"/api/prepare/{plan.id}", params={"profileId": bad}),
                await rig.client.get(f"/api/prepare/{plan.id}/repos", params={"profileId": bad}),
                await rig.client.post(f"/api/prepare/{plan.id}/headline", json={"profileId": bad}),
            ]
            assert {r.status_code for r in responses} <= {404, 400}, bad
            for r in responses:
                if r.status_code == 404:
                    assert r.json() == {"error": NOT_PROFILE}
        missing = await rig.client.get(f"/api/prepare/{plan.id}")
        assert missing.status_code == 404 and missing.json() == {"error": NOT_PROFILE}
        assert len(rig.env.claude.structured_calls) == calls
        counted = [
            h.count for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("prepare:day:")
        ]
        assert counted == [1]


async def test_a_claimed_profile_is_planned_for_only_by_its_owner(start: Any) -> None:
    async with start() as rig:
        owner, owner_headers = await rig.account("owner@example.com")
        _, other_headers = await rig.account("someone@example.com")
        copy_id = await rig.copy(owner=owner)
        for headers in (other_headers, {}):
            assert (await rig.prepare(profile_id=copy_id, headers=headers)).status_code == 404
        assert rig.env.claude.structured_calls == []
        mine = await rig.plan(profile_id=copy_id, headers=owner_headers)
        assert mine.profileId == copy_id
        theirs = await rig.client.get(
            f"/api/prepare/{mine.id}", params={"profileId": copy_id}, headers=other_headers
        )
        assert theirs.status_code == 404
        own = await rig.client.get(
            f"/api/prepare/{mine.id}", params={"profileId": copy_id}, headers=owner_headers
        )
        assert own.status_code == 200


async def test_the_sample_profile_can_be_planned_for_without_an_account(start: Any) -> None:
    async with start() as rig:
        plan = await rig.plan()
        assert plan.profileId == rig.sample


# ───────────────────────── bodies ─────────────────────────


async def test_a_bad_body_is_refused_before_the_profile_is_looked_at(start: Any) -> None:
    async with start() as rig:
        wrong_type = await rig.client.post(
            "/api/prepare", content=b'{"profileId": "x"}', headers={"content-type": "text/plain"}
        )
        not_json = await rig.client.post(
            "/api/prepare", content=b"{nope", headers={"content-type": "application/json"}
        )
        no_role = await rig.client.post("/api/prepare", json={"profileId": rig.sample})
        long_role = await rig.client.post("/api/prepare", json={"profileId": rig.sample, "roleId": "r" * 121})
        huge = await rig.client.post(
            "/api/prepare", json={"profileId": rig.sample, "roleId": "r", "requirement": "x" * 5000}
        )
        assert [r.status_code for r in (wrong_type, not_json, no_role, long_role, huge)] == [
            415,
            400,
            400,
            400,
            413,
        ]
        assert "roleId" in no_role.json()["error"] and "roleId" in long_role.json()["error"]
        assert rig.env.claude.structured_calls == []


async def test_a_state_body_takes_the_full_state_and_refuses_a_huge_one(start: Any) -> None:
    async with start() as rig:
        plan = await rig.plan()
        url = f"/api/prepare/{plan.id}/state"
        full = {
            "hoursPerWeek": 3,
            "moveIndex": 0,
            "done": [f"m1:{i % 3}:{i % 2}" for i in range(6)],
            "links": [
                {
                    "moveId": "m1",
                    "phaseIndex": 0,
                    "url": "https://x.example/" + "a" * 480,
                    "addedAt": "2026-10-18T09:00:00Z",
                }
                for _ in range(20)
            ],
        }
        ok = await rig.client.put(url, json={"profileId": rig.sample, "version": 1, "state": full})
        assert ok.status_code == 200, ok.text
        huge = await rig.client.put(url, content=b"x" * 60_000, headers={"content-type": "application/json"})
        assert huge.status_code == 413


# ───────────────────────── windows and spend ─────────────────────────


async def test_a_plan_counts_per_address_and_the_minute_window_has_its_own_sentence(start: Any) -> None:
    async with start(guard=roomy(prepare_per_minute=1)) as rig:
        assert (await rig.prepare()).status_code == 200
        second = await rig.prepare(fresh=True)
        assert second.status_code == 429
        assert second.json()["error"].startswith("A plan is already on its way. Please wait ")
        assert 1 <= int(second.headers["retry-after"]) <= 60
        elsewhere = await rig.prepare(fresh=True, headers=rig.at("198.51.100.7"))
        assert elsewhere.status_code == 200


async def test_a_signed_in_account_is_counted_across_addresses(start: Any) -> None:
    async with start(guard=roomy(prepare_per_day=1)) as rig:
        owner, headers = await rig.account("owner@example.com")
        copy_id = await rig.copy(owner=owner)
        first = await rig.prepare(profile_id=copy_id, headers={**headers, **rig.at("198.51.100.1")})
        second = await rig.prepare(
            profile_id=copy_id, fresh=True, headers={**headers, **rig.at("198.51.100.2")}
        )
        assert first.status_code == 200 and second.status_code == 429
        buckets = {
            h.bucket for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("prepare:day")
        }
        assert any(bucket == f"prepare:day:u:{owner}" for bucket in buckets)


async def test_the_whole_service_has_an_hourly_backstop_counted_once_per_request(start: Any) -> None:
    async with start(guard=roomy(prepare_global_per_hour=2)) as rig:
        for n in range(2):
            assert (await rig.prepare(fresh=True, headers=rig.at(f"198.51.100.{n + 1}"))).status_code == 200
        busy = await rig.prepare(fresh=True, headers=rig.at("198.51.100.99"))
        assert busy.status_code == 429 and busy.json()["error"].startswith("Leafout is very busy right now.")
        counted = [h.count for h in await rig.env.rows(RateLimitHitRow) if h.bucket == "prepare:global:hour"]
        assert counted == [3]


async def test_reading_the_state_and_the_repos_are_light_reads_that_count_no_plan(start: Any) -> None:
    async with start(guard=roomy(prepare_per_day=1)) as rig:
        plan = await rig.plan()
        params = {"profileId": rig.sample}
        for _ in range(3):
            assert (await rig.client.get(f"/api/prepare/{plan.id}", params=params)).status_code == 200
            assert (await rig.client.get(f"/api/prepare/{plan.id}/repos", params=params)).status_code == 200
        counted = [
            h.count for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("prepare:day:")
        ]
        assert counted == [1]


async def test_spend_is_reserved_for_a_plan_and_a_headline_and_released_after(start: Any) -> None:
    guard = roomy()
    held: list[int] = []
    claude = good_claude()

    async with start(guard=guard, claude=claude) as rig:
        spend = rig.env.container.spend_guard

        def record(call: StructuredCall) -> dict[str, Any]:
            held.append(spend.reserved_micro)
            return standing_answer(call)

        claude._queues.structured["prepare.standing"] = []
        claude.script_structured("prepare.standing", record, repeat=True)
        plan = await rig.plan()
        assert held == [micro(guard.est_prepare_usd)]
        claude._queues.structured["prepare.headline"] = []
        claude.script_structured(
            "prepare.headline",
            lambda call: (held.append(spend.reserved_micro), headline_answer(call))[1],
            repeat=True,
        )
        assert (
            await rig.client.post(f"/api/prepare/{plan.id}/headline", json={"profileId": rig.sample})
        ).status_code == 200
        assert held[-1] == micro(guard.est_prepare_headline_usd)
        assert spend.reserved_micro == 0 and spend.reservations_in_flight == 0


async def test_a_spend_ceiling_refuses_a_plan_and_a_headline_before_any_call(start: Any) -> None:
    async with start() as rig:
        plan = await rig.plan()
    async with start(
        guard=roomy(daily_ceiling_usd=0.001)
    ) as rig:  # the same data folder: the plan is still there
        refused = await rig.prepare(fresh=True)
        assert refused.status_code == 503 and refused.json() == {"error": SPEND_CEILING_MESSAGE}
        headline = await rig.client.post(f"/api/prepare/{plan.id}/headline", json={"profileId": rig.sample})
        assert headline.status_code == 503 and headline.json() == {"error": SPEND_CEILING_MESSAGE}
        assert rig.env.claude.structured_calls == []
        # what needs no model still works: the stored plan streams back, and is read, past the ceiling
        assert kinds(events_of(await rig.prepare())) == ["gaps", "done"]
        assert (
            await rig.client.get(f"/api/prepare/{plan.id}", params={"profileId": rig.sample})
        ).status_code == 200
        assert rig.env.claude.structured_calls == []


# ───────────────────────── an account on the unlimited list ─────────────────────────

LISTED = "tester@example.com"
OTHER = "other@example.com"


async def test_a_listed_account_plans_past_every_window_and_is_counted_nowhere(start: Any) -> None:
    tiny = {
        "prepare_per_minute": 1,
        "prepare_per_hour": 1,
        "prepare_per_day": 1,
        "prepare_global_per_hour": 1,
    }
    async with start(guard=roomy(unlimited_emails=f" {LISTED.upper()} ", **tiny)) as rig:
        _, listed = await rig.account(LISTED)
        _, other = await rig.account(OTHER)
        for n in range(4):
            assert (
                await rig.prepare(fresh=True, headers={**listed, **rig.at(f"198.51.100.{n + 10}")})
            ).status_code == 200
        assert not [h for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("prepare")]
        assert (await rig.prepare(fresh=True, headers={**other, **rig.at("198.51.100.1")})).status_code == 200
        assert (await rig.prepare(fresh=True, headers={**other, **rig.at("198.51.100.2")})).status_code == 429
        assert [h for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("prepare")]


async def test_a_listed_account_is_still_refused_at_the_spend_ceiling(start: Any) -> None:
    async with start(guard=roomy(unlimited_emails=LISTED, daily_ceiling_usd=0.001)) as rig:
        _, listed = await rig.account(LISTED)
        refused = await rig.prepare(headers=listed)
        assert refused.status_code == 503 and rig.env.claude.structured_calls == []


# ───────────────────────── every answer is no-store ─────────────────────────


async def test_every_answer_is_no_store(start: Any) -> None:
    async with start() as rig:
        plan = await rig.plan()
        params = {"profileId": rig.sample}
        state = {
            "profileId": rig.sample,
            "version": 1,
            "state": {"hoursPerWeek": 6, "moveIndex": 0, "done": [], "links": []},
        }
        answers = [
            await rig.prepare(),
            await rig.client.get(f"/api/prepare/{plan.id}", params=params),
            await rig.client.put(f"/api/prepare/{plan.id}/state", json=state),
            await rig.client.get(f"/api/prepare/{plan.id}/repos", params=params),
            await rig.client.post(f"/api/prepare/{plan.id}/headline", json={"profileId": rig.sample}),
        ]
        assert [r.status_code for r in answers] == [200] * 5
        assert all(is_no_store(r) for r in answers)
