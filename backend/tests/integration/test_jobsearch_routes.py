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

from tayyari.contract.jobsearch import JobPosting, JobSearchRun, ReferralNote, RolePrep
from tayyari.contract.stored import StoredContext
from tayyari.core.settings import Settings
from tayyari.jobsearch import service as jobsearch_service
from tayyari.storage.tables import LlmCallRow, RateLimitHitRow
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


# ───────────────────────── an account on the unlimited list ─────────────────────────

LISTED = "tester@example.com"
OTHER = "other@example.com"
UNLIMITED_LIST = f" {LISTED.upper()} , second.listed@example.org"
"""Written the way an owner might: padding, capitals, a second address."""
ONE_EACH = {
    "jobsearch_per_minute": 1,
    "jobsearch_per_hour": 1,
    "jobsearch_per_day": 1,
    "jobsearch_global_per_hour": 1,
    "anon_jobsearch_per_day": 1,
}


async def test_a_listed_account_searches_past_every_window_and_others_do_not(start: Any) -> None:
    guard = roomy_guard(unlimited_emails=UNLIMITED_LIST, **ONE_EACH)
    async with start(guard=guard) as rig:
        await rig.profile()
        rig.script()
        listed_id, listed = await rig.account(LISTED)
        _, other = await rig.account(OTHER)
        address = {"cf-connecting-ip": "203.0.113.8"}
        for _ in range(3):  # per account, per address and for the whole service: none of it is counted
            run = await rig.search(headers={**listed, **address})
            assert run.status_code == 200 and events_of(run)[-1]["type"] == "done"
        assert not [h for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("jobsearch")]

        # the same setup, the same address: the whole service had nothing counted, so one more is welcome
        first = await rig.search(headers={**other, **address})
        second = await rig.search(headers={**other, **address})
        anonymous = await rig.search(headers=address)
        counted = [h.bucket for h in await rig.env.rows(RateLimitHitRow) if h.bucket.startswith("jobsearch")]
    assert first.status_code == 200
    assert second.status_code == 429 and int(second.headers["retry-after"]) > 0
    assert anonymous.status_code == 429
    assert counted and not any(listed_id in bucket for bucket in counted)
    assert (
        len(rig.env.claude.search_calls) == 12
    )  # four searches ran, three scouts each; the refused ones started nothing


async def test_the_light_job_search_reads_never_shed_a_listed_account(start: Any) -> None:
    async with start(guard=roomy_guard(unlimited_emails=UNLIMITED_LIST, chat_per_minute=1)) as rig:
        await rig.profile()
        _, listed = await rig.account(LISTED)
        params = {"profileId": FRONTEND_ID}
        mine = [
            (await rig.env.client.get("/api/jobsearch/latest", params=params, headers=listed)).status_code
            for _ in range(10)
        ]
        theirs = [
            (await rig.env.client.get("/api/jobsearch/latest", params=params)).status_code for _ in range(6)
        ]
    assert 429 not in mine  # a 404 each: there is no search yet
    assert theirs[:2] == [404, 404] and 429 in theirs


async def test_a_listed_account_still_meets_the_spend_ceiling_on_a_search(start: Any) -> None:
    async with start(guard=roomy_guard(unlimited_emails=UNLIMITED_LIST, daily_ceiling_usd=0.05)) as rig:
        await rig.profile()
        rig.script()
        _, listed = await rig.account(LISTED)
        response = await rig.search(headers=listed)
        assert response.status_code == 503 and rig.env.claude.calls == []


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


# ───────────────────────── reading back, and a role's prep page and referral note ─────────────────────────


async def test_the_last_results_and_a_roles_posting_are_read_back_for_the_runs_own_profile(
    start: Any,
) -> None:
    async with start() as rig:
        await rig.profile()
        missing = await rig.env.client.get("/api/jobsearch/latest", params={"profileId": FRONTEND_ID})
        assert missing.status_code == 404 and missing.json() == {"error": "No search yet"}
        rig.script()
        events = events_of(await rig.search())
        run_id = events[-1]["runId"]
        latest = await rig.env.client.get(
            "/api/jobsearch/latest", params={"profileId": FRONTEND_ID, "city": "Bengaluru", "remote": "1"}
        )
        assert latest.status_code == 200 and latest.headers["cache-control"] == "no-store"
        run = JobSearchRun.model_validate(latest.json())
        assert run.runId == run_id and [j.id for j in run.jobs] == [
            e["job"]["id"] for e in events if e["type"] == "job"
        ]
        no_remote = await rig.env.client.get(
            "/api/jobsearch/latest", params={"profileId": FRONTEND_ID, "remote": "0"}
        )
        assert no_remote.status_code == 404
        (job,) = [j for j in run.jobs if j.applyUrl == HARBOUR_URL]
        posting = await rig.env.client.get(
            f"/api/jobsearch/runs/{run_id}/jobs/{job.id}/posting", params={"profileId": FRONTEND_ID}
        )
        assert posting.status_code == 200
        assert (
            "Strong experience with React and TypeScript"
            in JobPosting.model_validate(posting.json()).postingText
        )
        for bad in (
            f"/api/jobsearch/runs/{run_id}/jobs/{'f' * 32}/posting",
            f"/api/jobsearch/runs/x/jobs/{job.id}/posting",
        ):
            assert (await rig.env.client.get(bad, params={"profileId": FRONTEND_ID})).status_code == 404


async def test_a_roles_prep_page_and_referral_note_come_from_the_stored_run(
    start: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_prepare(**kwargs: Any) -> RolePrep:
        job = kwargs["job"]
        assert "Strong experience with React" in kwargs["posting_text"]
        return RolePrep(
            jobId=job.id,
            title=job.title,
            employer=job.employer,
            lead=[],
            gaps=[],
            questions=[],
            createdAt="t",
        )

    async def fake_referral(**kwargs: Any) -> ReferralNote:
        job = kwargs["job"]
        return ReferralNote(
            jobId=job.id, lines=[], ask="Would you be open to referring me?", link=job.applyUrl, createdAt="t"
        )

    monkeypatch.setattr(jobsearch_service, "prepare_role", fake_prepare)
    monkeypatch.setattr(jobsearch_service, "write_referral", fake_referral)
    async with start() as rig:
        await rig.profile()
        rig.script()
        events = events_of(await rig.search())
        run_id = events[-1]["runId"]
        job_id = next(
            e["job"]["id"] for e in events if e["type"] == "job" and e["job"]["applyUrl"] == HARBOUR_URL
        )
        body = {"profileId": FRONTEND_ID, "runId": run_id, "jobId": job_id}
        prep = await rig.env.client.post("/api/jobsearch/prepare", json=body)
        assert prep.status_code == 200 and RolePrep.model_validate(prep.json()).employer == WEB_COMPANY
        note = await rig.env.client.post("/api/jobsearch/referral", json={**body, "fresh": True})
        assert note.status_code == 200 and ReferralNote.model_validate(note.json()).link == HARBOUR_URL
        gone = await rig.env.client.post("/api/jobsearch/prepare", json={**body, "jobId": "f" * 32})
        assert gone.status_code == 404
        bad = await rig.env.client.post("/api/jobsearch/referral", json={**body, "runId": "not-a-run"})
        assert bad.status_code == 404
