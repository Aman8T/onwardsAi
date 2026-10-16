"""Search with AI end to end through the real app over a real SQLite file: the stream, who may run one for which profile,
the city, the limits, the spend it reserves and records, and the switch that turns it off. Claude (scouts and reads) is
the scripted fake and the employers' boards are `FakeBoards`; nothing touches the network."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tayyari.contract.stored import StoredContext
from tayyari.core.settings import Settings
from tayyari.storage.tables import LlmCallRow
from tests.fakes.claude import search_run
from tests.fakes.discover import DATA, FakeBoards, profile_of, read_json
from tests.integration.support import Env, ndjson_lines, roomy_guard, running
from tests.unit.discover.conftest import BOARD_FILES
from tests.unit.jobsearch.support import WEB_COMPANY, WEB_TOKEN, frontend_answer, reader, web_board

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
FRONTEND_ID = profile_of("frontend").id
UNKNOWN_ID = "f" * 32
HARBOUR_URL = f"https://job-boards.greenhouse.io/{WEB_TOKEN}/jobs/101"
RESULTS = {
    "Frontend Engineer Bengaluru": [(HARBOUR_URL, f"Job Application for Frontend Engineer at {WEB_COMPANY}")]
}


@dataclass
class Rig:
    env: Env
    boards: FakeBoards

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
        return user.id, {"cookie": f"{SESSION}={token}"}

    def script(self) -> None:
        claude = self.env.claude
        claude.script_search(search_run(RESULTS), repeat=True)
        claude.script_structured(
            "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
        )

    async def search(
        self, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return await self.env.client.post(
            "/api/jobsearch",
            json=body if body is not None else {"profileId": FRONTEND_ID},
            headers=headers or {},
        )


def events_of(response: httpx.Response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in ndjson_lines(response)]


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**overrides: Any) -> AsyncIterator[Rig]:
        boards = FakeBoards()
        for token, name in BOARD_FILES.items():
            boards.serve(token, read_json(name))
        boards.serve(WEB_TOKEN, web_board())
        discover = {"registry_path": str(DATA / "registry.json"), **overrides.pop("discover", {})}
        async with running(
            build_settings(discover=discover, **overrides), discover_transport=boards.transport
        ) as env:
            report = await env.container.discover.refresh()
            assert report is not None and report.status == "ok"
            yield Rig(env, boards)

    return run


async def test_a_search_streams_its_trail_and_roles_and_records_what_it_spent(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        rig.script()
        response = await rig.search({"profileId": FRONTEND_ID, "city": "bangalore"})
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/x-ndjson")
        assert response.headers["cache-control"] == "no-store"
        events = events_of(response)
        assert events[0]["type"] == "step" and events[0]["step"]["stage"] == "plan"
        assert events[-1]["type"] == "done" and events[-1]["jobs"] == 1
        (job,) = [e["job"] for e in events if e["type"] == "job"]
        assert job["applyUrl"] == HARBOUR_URL and job["employer"] == WEB_COMPANY and job["fit"] == "good"
        assert "%" not in response.text
        rows = await rig.env.rows(LlmCallRow)
        purposes = sorted({row.purpose for row in rows})
        assert purposes == ["jobsearch.judge", "jobsearch.scout"]
        scouts = [row for row in rows if row.purpose == "jobsearch.scout"]
        assert len(scouts) == 3 and all(
            row.web_searches == 1 and row.profile_id == FRONTEND_ID for row in scouts
        )
        spent, calls = (await rig.env.spend())["total"]
        assert calls == len(rows) and spent == sum(row.cost_micro_usd for row in rows) > 0


async def test_unknown_profiles_and_cities_are_refused_before_anything_is_spent(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        missing = await rig.search({"profileId": UNKNOWN_ID})
        assert (missing.status_code, missing.json()) == (404, {"error": "Profile not found"})
        malformed = await rig.search({"profileId": "../etc"})
        assert malformed.status_code == 404
        city = await rig.search({"profileId": FRONTEND_ID, "city": "Atlantis"})
        assert city.status_code == 400 and "city" in city.json()["error"]
        empty = await rig.search({})
        assert empty.status_code == 400
        assert rig.env.claude.calls == []


async def test_a_claimed_profile_is_searched_only_by_its_owner(start: Any) -> None:
    async with start() as rig:
        owner, owner_headers = await rig.account("owner@example.com")
        _, other_headers = await rig.account("someone@example.com")
        await rig.profile(owner=owner)
        rig.script()
        assert (await rig.search(headers=other_headers)).status_code == 404
        assert (await rig.search()).status_code == 404
        mine = await rig.search(headers=owner_headers)
        assert mine.status_code == 200 and events_of(mine)[-1]["type"] == "done"


async def test_without_an_account_the_daily_allowance_is_the_meter(start: Any) -> None:
    async with start(guard=roomy_guard(anon_jobsearch_per_day=1)) as rig:
        await rig.profile()
        rig.script()
        first = await rig.search()
        assert first.status_code == 200
        second = await rig.search()
        assert second.status_code == 429 and int(second.headers["retry-after"]) > 0
        assert "without an account" in second.json()["error"]
        assert len(rig.env.claude.search_calls) == 3  # the refused run started nothing


async def test_a_spend_ceiling_refuses_before_the_stream_starts(start: Any) -> None:
    async with start(guard=roomy_guard(daily_ceiling_usd=0.05)) as rig:
        await rig.profile()
        rig.script()
        response = await rig.search()
        assert response.status_code == 503 and rig.env.claude.calls == []


async def test_switched_off_it_answers_503(start: Any) -> None:
    async with start(jobsearch={"enabled": False}) as rig:
        await rig.profile()
        response = await rig.search()
        assert response.status_code == 503 and "switched off" in response.json()["error"]
