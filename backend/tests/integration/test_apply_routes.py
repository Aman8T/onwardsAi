"""Auto-apply's routes end to end through the real app over a real SQLite file, with the real `ApplyService`, the real
credit ledger and fakes only at the network edge (the employer's page and the ATS read endpoint): signed-in only, the
start checks before any row or hold, one application per posting, the answers, approve, cancel and resolve round trips,
Your details, and the posting match refusing a site auto-apply never opens. Nothing here calls the network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
import pytest

from tayyari.api.routes import apply as apply_routes
from tayyari.contract.match import MatchResult, Posting
from tayyari.contract.stored import StoredContext
from tayyari.core.settings import Settings
from tayyari.storage.ports import StoredApplication
from tests.integration.support import FakeWeb, running
from tests.unit.match.support import js

MakeSettings = Callable[..., Settings]
SESSION_COOKIE = "tayyari_session"
FORM = "https://job-boards.greenhouse.io/lumen/jobs/4012345"
FORM_KEY = "greenhouse:lumen:4012345"
LIVE = "https://boards-api.greenhouse.io/v1/boards/lumen/jobs/4012345"
JOB_PAGE = (
    '<html><script type="application/ld+json">{"@type": "JobPosting", "title": "Senior Growth Marketing Manager", '
    '"hiringOrganization": {"name": "Lumen Retail"}}</script></html>'
)


@dataclass
class Edge:
    """The employer's page and the ATS read endpoint, with every fetch recorded."""

    pages: dict[str, str] = field(default_factory=lambda: {FORM: JOB_PAGE})
    live_status: int = 200
    page_fetches: list[str] = field(default_factory=list)
    live_fetches: list[str] = field(default_factory=list)

    async def fetch(self, url: str) -> tuple[str, str]:
        self.page_fetches.append(url)
        return url, self.pages[url]


class LiveFetcher:
    def __init__(self, edge: Edge) -> None:
        self.edge = edge

    async def fetch(self, url: str, accept: str) -> tuple[int, str, str]:
        self.edge.live_fetches.append(url)
        return self.edge.live_status, url, json.dumps({"id": 4012345})


@pytest.fixture
def edge(monkeypatch: pytest.MonkeyPatch) -> Edge:
    found = Edge()
    monkeypatch.setattr(apply_routes, "IngestFetcher", lambda: found)
    monkeypatch.setattr(apply_routes, "IngestStatusFetcher", lambda: LiveFetcher(found))
    return found


@dataclass
class Rig:
    env: Any
    edge: Edge

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    @property
    def container(self) -> Any:
        return self.env.container

    async def account(
        self, email: str = "riya@example.com", pid: str = "p" * 32, *, consent: bool = True
    ) -> tuple[str, dict[str, str]]:
        c = self.container
        user = await c.repos.users.sign_in_email(email=email, now=c.clock.now())
        await c.repos.profiles.save(
            pid,
            {"person": {"name": "Meera Nair"}},
            StoredContext(),
            goal=None,
            owner_hash=None,
            is_sample=False,
        )
        await c.repos.users.claim_profile(user.id, pid)
        if consent:
            await c.repos.apply.set_consent(user.id, now=c.clock.now())
        token, _ = await c.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return user.id, {"cookie": f"{SESSION_COOKIE}={token}"}

    async def match(
        self, user_id: str, *, match_id: str = "m" * 32, pid: str = "p" * 32, blocked: bool = False
    ) -> str:
        data = js("meera.match.json")
        posting = Posting.model_validate(data["posting"])
        result = MatchResult.model_validate(data["result"])
        if blocked:
            gates = [g.model_copy(update={"verdict": "fail", "factIds": ["quote-1"]}) if g.gateId == "g1" else g
                     for g in result.gates]  # fmt: skip
            result = result.model_copy(update={"gates": gates, "blocked": True})
        c = self.container
        await c.repos.postings.put(posting)
        stored = await c.repos.matches.create(
            match_id=match_id, user_id=user_id, profile_id=pid, posting_hash=posting.hash,
            facts={"name": "Meera Nair", "facts": [], "entries": []}, result=result.to_dict(), band=result.band,
            blocked=result.blocked, version=result.version, now=c.clock.now(),
        )  # fmt: skip
        return str(stored.id)

    async def app(self, user_id: str, app_id: str, stage: str, **extra: Any) -> None:
        now = self.container.clock.now()
        values: dict[str, Any] = {
            "id": app_id, "owner_key": f"u:{user_id}", "user_id": user_id, "source": "match", "job_url": FORM,
            "domain": "job-boards.greenhouse.io", "ats": "greenhouse", "stage": stage, "created_at": now,
            "updated_at": now, "company": f"Co {app_id}",
        }  # fmt: skip
        values.update(extra)
        await self.container.repos.apply.create_application(StoredApplication(**values))

    async def stage(self, app_id: str) -> str:
        found = await self.container.repos.apply.get_application(app_id, None)
        assert found is not None
        return str(found.stage)

    async def hold(self, user_id: str, app_id: str) -> str | None:
        return await self.container.repos.billing.hold_state(user_id, f"apply:{app_id}")  # type: ignore[no-any-return]


@pytest.fixture
def start(build_settings: MakeSettings, edge: Edge):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**overrides: Any) -> AsyncIterator[Rig]:
        overrides.setdefault("auto_apply_enabled", True)
        async with running(build_settings(**overrides)) as env:
            yield Rig(env, edge)

    return run


def need(field_key: str, key: str, **extra: Any) -> dict[str, Any]:
    return {"key": field_key, "label": field_key, "kind": "text", "required": True,
            "reason": "A question only you can answer.", "questionKey": key, **extra}  # fmt: skip


# ───────────────────────── signed in only ─────────────────────────

ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/apply/check", {"matchId": "m"}),
    ("POST", "/api/apply/applications", {"matchId": "m", "resume": "saved"}),
    ("GET", "/api/apply/applications", None),
    ("GET", "/api/apply/applications/x", None),
    ("GET", "/api/apply/applications/x/screenshot", None),
    ("POST", "/api/apply/applications/x/answers", {"answers": []}),
    ("POST", "/api/apply/applications/x/approve", {"digest": "d"}),
    ("POST", "/api/apply/applications/x/cancel", None),
    ("POST", "/api/apply/applications/x/resolve", {"outcome": "sent"}),
    ("POST", "/api/apply/applications/x/applied-there", None),
    ("POST", "/api/apply/applications/x/stage", {"stage": "viewed"}),
    ("POST", "/api/apply/applications/x/retry", None),
    ("DELETE", "/api/apply/applications/x", None),
    ("GET", "/api/apply/details", None),
    ("PUT", "/api/apply/details/answer", {"questionKey": "bond", "askEachTime": True}),
    ("PUT", "/api/apply/details/answers/bond", {"askEachTime": True}),
    ("DELETE", "/api/apply/details/answers/bond", None),
    ("POST", "/api/apply/details/still-true", {"questionKey": "bond"}),
    ("POST", "/api/apply/details/first-check", {"skipped": True}),
    ("PUT", "/api/apply/details/self-id", {"selfIdOnForms": False}),
    ("POST", "/api/apply/consent", None),
    ("PUT", "/api/apply/profile/resume", None),
    ("POST", "/api/apply/email", {"subject": "s", "sender": "x", "body": "b"}),
    ("GET", "/api/apply/metrics", None),
]


async def test_anonymous_gets_401_on_every_route_even_with_anonymous_matching_on(start: Any) -> None:
    async with start() as rig:
        assert rig.container.match_anonymous is True
        for method, path, body in ROUTES:
            response = await rig.client.request(method, path, json=body)
            assert response.status_code == 401, (method, path, response.status_code)
        status = (await rig.client.get("/api/apply/status")).json()
        assert status["enabled"] is True and status["signedIn"] is False
        assert not {"anonymous", "agents"} & set(status)  # the old branch's fields are gone
        assert rig.edge.page_fetches == [] and rig.edge.live_fetches == []


async def test_everything_but_the_status_is_off_by_default(start: Any) -> None:
    async with start(auto_apply_enabled=False) as rig:
        _, headers = await rig.account()
        assert (await rig.client.get("/api/apply/status")).json()["enabled"] is False
        assert (await rig.client.get("/api/apply/applications", headers=headers)).status_code == 404


# ───────────────────────── the start ─────────────────────────


async def test_the_check_is_uncharged_and_a_start_is_queued_with_a_hold(start: Any) -> None:
    async with start() as rig:
        user_id, headers = await rig.account()
        match_id = await rig.match(user_id)
        checked = await rig.client.post(
            "/api/apply/check", json={"matchId": match_id, "applyUrl": FORM}, headers=headers
        )
        assert checked.status_code == 200, checked.text
        assert checked.json() == {
            "offered": True, "host": "job-boards.greenhouse.io", "ats": "greenhouse", "applyUrl": FORM,
        }  # fmt: skip
        assert (await rig.client.get("/api/apply/applications", headers=headers)).json() == {
            "applications": []
        }
        assert rig.edge.live_fetches == [LIVE]

        no_link = await rig.client.post("/api/apply/check", json={"matchId": match_id}, headers=headers)
        assert no_link.json()["reason"] == "not_fillable" and "host" not in no_link.json()

        made = await rig.client.post(
            "/api/apply/applications",
            json={"matchId": match_id, "resume": "saved", "applyUrl": FORM},
            headers=headers,
        )
        assert made.status_code == 201, made.text
        app = made.json()
        assert (app["stage"], app["creditState"], app["company"], app["matchId"]) == (
            "queued", "held", "Lumen Retail", match_id,
        )  # fmt: skip
        assert await rig.hold(user_id, app["id"]) == "held"

        again = await rig.client.post(
            "/api/apply/applications",
            json={"matchId": match_id, "resume": "saved", "applyUrl": FORM},
            headers=headers,
        )
        assert again.status_code == 409 and again.json()["existingApplicationId"] == app["id"]

        listed = (await rig.client.get("/api/apply/applications", headers=headers)).json()["applications"]
        assert [a["id"] for a in listed] == [app["id"]]


async def test_nothing_starts_before_the_consent(start: Any) -> None:
    async with start() as rig:
        user_id, headers = await rig.account(consent=False)
        match_id = await rig.match(user_id)
        body = {"matchId": match_id, "resume": "saved", "applyUrl": FORM}
        refused = await rig.client.post("/api/apply/applications", json=body, headers=headers)
        assert refused.status_code == 409 and "Agree" in refused.text
        assert (await rig.client.get("/api/apply/applications", headers=headers)).json()["applications"] == []
        assert (await rig.client.post("/api/apply/consent", headers=headers)).status_code == 200
        made = await rig.client.post("/api/apply/applications", json=body, headers=headers)
        assert made.status_code == 201, made.text


async def test_a_tailored_resume_must_be_ready(start: Any) -> None:
    async with start() as rig:
        user_id, headers = await rig.account()
        match_id = await rig.match(user_id)
        body = {"matchId": match_id, "resume": "tailored", "applyUrl": FORM}
        refused = await rig.client.post("/api/apply/applications", json=body, headers=headers)
        assert refused.status_code == 409 and await rig.hold(user_id, "x") is None
        assert (await rig.client.get("/api/apply/applications", headers=headers)).json()["applications"] == []
        await rig.container.repos.matches.put_variant(
            match_id, "resume", spec={}, status="ready", now=rig.container.clock.now()
        )
        made = await rig.client.post("/api/apply/applications", json=body, headers=headers)
        assert made.status_code == 201 and made.json()["resumeChoice"] == "tailored"


async def test_a_blocked_match_gives_409_with_its_reasons(start: Any) -> None:
    async with start() as rig:
        user_id, headers = await rig.account()
        match_id = await rig.match(user_id, blocked=True)
        refused = await rig.client.post(
            "/api/apply/applications",
            json={"matchId": match_id, "resume": "saved", "applyUrl": FORM},
            headers=headers,
        )
        assert refused.status_code == 409
        body = refused.json()
        assert body["reason"] == "blocked_by_requirement"
        assert body["gateReasons"] == ["Are you authorised to work in India?"]
        assert (await rig.client.get("/api/apply/applications", headers=headers)).json()["applications"] == []


async def test_someone_elses_match_is_not_found(start: Any) -> None:
    async with start() as rig:
        other_id, _ = await rig.account("ben@example.com", pid="b" * 32)
        match_id = await rig.match(other_id, pid="b" * 32)
        _, headers = await rig.account()
        for path, body in (
            ("/api/apply/applications", {"matchId": match_id, "resume": "saved", "applyUrl": FORM}),
            ("/api/apply/check", {"matchId": match_id, "applyUrl": FORM}),
        ):
            response = await rig.client.post(path, json=body, headers=headers)
            assert response.status_code == 404, path
        assert rig.edge.page_fetches == [] and rig.edge.live_fetches == []


async def test_a_manual_only_link_is_refused_without_a_fetch(start: Any, web: FakeWeb) -> None:
    async with start() as rig:
        user_id, headers = await rig.account()
        match_id = await rig.match(user_id)
        link = "https://www.timesjobs.com/job/123"
        refused = await rig.client.post(
            "/api/apply/applications",
            json={"matchId": match_id, "resume": "saved", "applyUrl": link},
            headers=headers,
        )
        assert refused.status_code == 409 and refused.json()["reason"] == "manual_only"
        assert rig.edge.page_fetches == [] and rig.edge.live_fetches == []
        # the posting match never reads one either (a board auto-apply refuses is refused before any fetch)
        read = await rig.client.post("/api/match", json={"profileId": rig.env.sample_id, "url": link})
        assert read.status_code == 422 and web.web_calls == []


# ───────────────────────── answers, approve, cancel, resolve ─────────────────────────


async def test_answers_approve_cancel_and_resolve_round_trips(start: Any) -> None:
    async with start() as rig:
        user_id, headers = await rig.account()
        ask = need("notice", "noticePeriod")
        now = rig.container.clock.now()
        await rig.app(user_id, "ap1", "waiting_for_you", needs=[ask], created_at=now)
        await rig.app(user_id, "ap2", "waiting_for_you", needs=[ask], created_at=now + timedelta(seconds=1))
        await rig.app(user_id, "ap3", "waiting_for_you", needs=[ask], created_at=now + timedelta(seconds=2))
        refused = await rig.client.post("/api/apply/applications/ap1/answers", headers=headers, json={
            "answers": [{"questionKey": "noticePeriod", "value": "ABCDE1234F", "save": True}]})  # fmt: skip
        assert refused.status_code == 400 and await rig.stage("ap1") == "waiting_for_you"
        unasked = await rig.client.post("/api/apply/applications/ap1/answers", headers=headers, json={
            "answers": [{"questionKey": "q:not-on-this-form", "value": "x", "save": False}]})  # fmt: skip
        assert unasked.status_code == 400 and await rig.stage("ap1") == "waiting_for_you"
        answered = await rig.client.post("/api/apply/applications/ap1/answers", headers=headers, json={
            "answers": [{"questionKey": "noticePeriod", "value": "30 days", "save": True}]})  # fmt: skip
        assert answered.status_code == 200, answered.text
        assert answered.json()["requeued"] == 2 and answered.json()["application"]["stage"] == "queued"
        assert [await rig.stage(a) for a in ("ap1", "ap2", "ap3")] == ["queued"] * 3

        digest = "d" * 64
        await rig.app(user_id, "ap4", "waiting_for_you", review_digest=digest)
        wrong = await rig.client.post(
            "/api/apply/applications/ap4/approve", json={"digest": "0" * 64}, headers=headers
        )
        assert wrong.status_code == 409
        ok = await rig.client.post(
            "/api/apply/applications/ap4/approve", json={"digest": digest}, headers=headers
        )
        assert ok.status_code == 200 and ok.json()["stage"] == "approved"

        await rig.container.entitlements.hold(user_id, "apply:ap5")
        await rig.app(user_id, "ap5", "approved", credit_state="held", credit_ref="apply:ap5")
        cancelled = await rig.client.post("/api/apply/applications/ap5/cancel", headers=headers)
        assert cancelled.json()["stage"] == "cancelled" and cancelled.json()["creditState"] == "released"
        assert await rig.hold(user_id, "ap5") == "released"

        for app_id in ("ap6", "ap7"):
            await rig.container.entitlements.hold(user_id, f"apply:{app_id}")
            await rig.app(
                user_id, app_id, "check_this_one", credit_state="held", credit_ref=f"apply:{app_id}"
            )
        sent = await rig.client.post(
            "/api/apply/applications/ap6/resolve", json={"outcome": "sent"}, headers=headers
        )
        assert sent.json()["stage"] == "sent" and await rig.hold(user_id, "ap6") == "charged"
        not_sent = await rig.client.post(
            "/api/apply/applications/ap7/resolve", json={"outcome": "not_sent"}, headers=headers
        )
        assert not_sent.json()["stage"] == "failed" and await rig.hold(user_id, "ap7") == "released"

        await rig.app(user_id, "ap8", "needs_signin")
        there = await rig.client.post("/api/apply/applications/ap8/applied-there", headers=headers)
        assert there.json()["stage"] == "sent"
        tracked = await rig.client.post(
            "/api/apply/applications/ap8/stage", json={"stage": "interviewing"}, headers=headers
        )
        assert tracked.json()["stage"] == "interviewing"
        sneak = await rig.client.post(
            "/api/apply/applications/ap1/stage", json={"stage": "approved"}, headers=headers
        )
        assert sneak.status_code == 400

        shot = await rig.client.get(
            "/api/apply/applications/ap6/screenshot?kind=confirmation", headers=headers
        )
        assert shot.status_code == 404
        await rig.container.repos.apply.put_confirmation("ap6", b"\xff\xd8jpeg")
        shot = await rig.client.get(
            "/api/apply/applications/ap6/screenshot?kind=confirmation", headers=headers
        )
        assert shot.status_code == 200 and shot.content == b"\xff\xd8jpeg"


# ───────────────────────── Your details ─────────────────────────


async def test_your_details_crud(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.account()
        empty = (await rig.client.get("/api/apply/details", headers=headers)).json()
        # layer 1 (spec 2.2): the resume's name and the sign-in email, for the first check to show for Edit
        assert {a["questionKey"]: (a["value"], a["source"]) for a in empty["answers"]} == {
            "fullName": ("Meera Nair", "resume"), "email": ("riya@example.com", "resume"),
        }  # fmt: skip
        assert empty["firstCheckDone"] is False and "PAN" in empty["neverStored"]

        checked = await rig.client.post("/api/apply/details/first-check", headers=headers, json={
            "skipped": False, "noticePeriod": "30 days",
            "workAuthorization": {"country": "IN", "authorised": "Yes", "needsSponsorship": "No"}})  # fmt: skip
        assert checked.status_code == 200 and checked.json()["firstCheckDone"] is True
        assert checked.json()["workAuthorization"] == [
            {"country": "IN", "authorised": "Yes", "needsSponsorship": "No"}
        ]

        saved = await rig.client.put(
            "/api/apply/details/answer", json={"questionKey": "languages", "askEachTime": False, "value": "English, Hindi"},
            headers=headers,
        )  # fmt: skip
        assert {a["questionKey"]: a.get("value") for a in saved.json()["answers"]}[
            "languages"
        ] == "English, Hindi"
        by_path = await rig.client.put("/api/apply/details/answers/shifts", json={"askEachTime": False, "value": "Day"},
                                       headers=headers)  # fmt: skip
        assert by_path.status_code == 200
        refused = await rig.client.put("/api/apply/details/answer", headers=headers,
                                       json={"questionKey": "pan", "askEachTime": False, "value": "x"})  # fmt: skip
        assert refused.status_code == 400
        assert (await rig.client.post("/api/apply/details/still-true", json={"questionKey": "noticePeriod"},
                                      headers=headers)).status_code == 200  # fmt: skip
        gone = await rig.client.delete("/api/apply/details/answers/shifts", headers=headers)
        assert "shifts" not in {a["questionKey"] for a in gone.json()["answers"]}
        self_id = await rig.client.put(
            "/api/apply/details/self-id", json={"selfIdOnForms": True}, headers=headers
        )
        assert self_id.json()["selfIdOnForms"] is True

        bad = await rig.client.put("/api/apply/profile/resume", content=b"<html/>", headers={
            **headers, "content-type": "application/pdf", "x-file-name": "cv.pdf"})  # fmt: skip
        assert bad.status_code == 400
        good = await rig.client.put("/api/apply/profile/resume", content=b"%PDF-1.7 a resume", headers={
            **headers, "content-type": "application/pdf", "x-file-name": "Riya%20CV"})  # fmt: skip
        assert good.status_code == 200 and good.json()["resumeName"] == "Riya CV.pdf"  # `YourDetails`
        assert good.json()["hasResume"] is True and "details" not in good.json()
        old = await rig.client.get("/api/apply/profile", headers=headers)
        assert old.status_code in (404, 405)  # the old branch's details form is gone
        assert (await rig.client.get("/api/apply/details", headers=headers)).json()["hasResume"] is True

        agreed = await rig.client.post("/api/apply/consent", headers=headers)
        assert agreed.status_code == 200 and agreed.json()["consentAt"]
        status = (await rig.client.get("/api/apply/status", headers=headers)).json()
        assert status["signedIn"] is True and status["firstCheckDone"] is True and status["consentAt"]


# ───────────────────────── the listing proof ─────────────────────────


async def test_a_discover_listing_gives_its_link_only_when_it_is_the_matched_posting(start: Any) -> None:
    from types import SimpleNamespace

    from tayyari.api.matching import listing_url, open_match
    from tayyari.contract.jobsearch import JobPosting

    async with start() as rig:
        user_id, _ = await rig.account()
        ws = await open_match(rig.container, user_id, await rig.match(user_id))
        text = ws.posting.rawText

        class Search:
            def __init__(self, posting_text: str) -> None:
                self.text = posting_text

            async def posting(self, profile_id: str, run_id: str, job_id: str) -> JobPosting:
                return JobPosting(jobId=job_id, title="t", employer="e", applyUrl=FORM, postingText=self.text)

        class Listings:
            async def get(self, listing_id: str) -> None:
                return None

        same = SimpleNamespace(jobsearch=Search(text), repos=SimpleNamespace(discover=Listings()))
        assert await listing_url(same, ws, "l1", "run1") == FORM  # type: ignore[arg-type]
        other = SimpleNamespace(jobsearch=Search("A different posting entirely. " * 20),
                                repos=SimpleNamespace(discover=Listings()))  # fmt: skip
        assert await listing_url(other, ws, "l1", "run1") is None  # type: ignore[arg-type]


async def test_the_check_reads_a_web_found_role_from_the_persons_search_run(start: Any) -> None:
    """A role a web search found is never stored as a listing: the check reads its form link from the run."""
    from tayyari.contract.jobsearch import JobPosting

    async with start() as rig:
        _, headers = await rig.account()
        asked: list[tuple[str, str, str]] = []

        class Search:
            async def posting(self, profile_id: str, run_id: str, job_id: str) -> JobPosting | None:
                asked.append((profile_id, run_id, job_id))
                return JobPosting(jobId=job_id, title="t", employer="e", applyUrl=FORM, postingText="x")

        rig.container.jobsearch = Search()
        checked = await rig.client.post(
            "/api/apply/check", json={"listingId": "web-1", "runId": "run1"}, headers=headers
        )
        assert checked.status_code == 200, checked.text
        assert checked.json()["offered"] is True and checked.json()["applyUrl"] == FORM
        assert asked == [("p" * 32, "run1", "web-1")]


# ───────────────────────── rate limits (I#10) ─────────────────────────


async def test_the_check_and_the_email_are_shed_per_account(start: Any) -> None:
    from tests.integration.support import roomy_guard

    async with start(guard=roomy_guard(chat_per_minute=1)) as rig:
        user_id, headers = await rig.account()
        match_id = await rig.match(user_id)
        body = {"matchId": match_id, "applyUrl": FORM}
        checks = [
            (await rig.client.post("/api/apply/check", json=body, headers=headers)).status_code
            for _ in range(4)
        ]
        assert checks[0] == 200 and checks[-1] == 429
        email = {
            "subject": "Your application",
            "sender": "jobs@lumen.example",
            "body": "Thanks for applying.",
        }
        refused = await rig.client.post("/api/apply/email", json=email, headers=headers)
        assert refused.status_code == 429  # the same account's bucket
        fetched = len(rig.edge.live_fetches)
        _, other = await rig.account("ben@example.com", pid="b" * 32)
        assert (
            await rig.client.post("/api/apply/check", json={"applyUrl": FORM}, headers=other)
        ).status_code == 200
        assert len(rig.edge.live_fetches) == fetched + 1  # another account is not held back


async def test_the_start_and_the_answers_are_shed_per_account(start: Any) -> None:
    """A start reads the employer's page and the ATS endpoint, and an answer queues a fill: both are shed per account
    before anything is fetched."""
    from tests.integration.support import roomy_guard

    async with start(guard=roomy_guard(chat_per_minute=1)) as rig:
        user_id, headers = await rig.account()
        match_id = await rig.match(user_id)
        body = {"matchId": match_id, "resume": "saved", "applyUrl": FORM}
        codes = [
            (await rig.client.post("/api/apply/applications", json=body, headers=headers)).status_code
            for _ in range(4)
        ]
        assert codes[-1] == 429, codes
        fetched = len(rig.edge.page_fetches)
        assert (
            await rig.client.post("/api/apply/applications", json=body, headers=headers)
        ).status_code == 429
        assert len(rig.edge.page_fetches) == fetched  # a shed start fetches nothing
        await rig.app(user_id, "ap1", "waiting_for_you", needs=[need("notice", "noticePeriod")])
        answered = await rig.client.post("/api/apply/applications/ap1/answers", headers=headers, json={
            "answers": [{"questionKey": "noticePeriod", "value": "30 days", "save": False}]})  # fmt: skip
        assert answered.status_code == 429 and await rig.stage("ap1") == "waiting_for_you"
