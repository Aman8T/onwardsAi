"""Auto-apply end to end on fakes (docs/auto-apply.md 3.1): the real app over a real SQLite file, the real `ApplyService`,
the real credit ledger and the real worker and engine, with fakes only at the edges: the employer's job page and the
ATS read endpoint (`Edge`), and the browser (`FakeOpener` handing out `FakeDriver` pages with a Browser Use cost, in
place of `BrowserUseOpener`). Nothing calls the network or a model.

1. A signed-in person starts from a match (the start check, the credit hold), the worker fills the form in the guarded
   preview browser (nothing is sent) and the row waits for the person with one question; the answer queues it again,
   the next fill gives a review with nothing open, the approval signs its digest, and the worker's submit pass sends it
   with exactly one click: ``sent``, the hold captured, the confirmation stored, ``sentAt`` set.
2. The same, but nothing clear comes back after the click: ``check_this_one`` keeps the hold, never a second click, and
   the person's "it did not go through" (resolve ``not_sent``) fails the row and gives the hold back.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from tayyari.api.routes import apply as apply_routes
from tayyari.apply.worker import ApplyWorker
from tayyari.container import build_apply_worker
from tayyari.contract.match import MatchResult, Posting
from tayyari.contract.stored import StoredContext
from tayyari.core.settings import ApplySettings, Settings
from tayyari.storage.ports import StoredAnswer
from tests.fakes.apply_browser import FakeOpener
from tests.integration.support import running
from tests.unit.match.support import js

MakeSettings = Callable[..., Settings]
SESSION_COOKIE = "tayyari_session"
FORM = "https://job-boards.greenhouse.io/lumen/jobs/4012345"
LIVE = "https://boards-api.greenhouse.io/v1/boards/lumen/jobs/4012345"
JOB_PAGE = (
    '<html><script type="application/ld+json">{"@type": "JobPosting", "title": "Senior Growth Marketing Manager", '
    '"hiringOrganization": {"name": "Lumen Retail"}}</script></html>'
)
FORM_PAGE = (Path(__file__).resolve().parents[1] / "data" / "apply" / "greenhouse_job.html").read_text(
    encoding="utf-8"
)
QUIET = "<html><body><p>Working on it</p></body></html>"
"""What the page shows after the click when nothing clear comes back."""
FAKE_KEY = SecretStr("bu-test-not-a-key")
BROWSER_COST = 4_000
SETTINGS = ApplySettings(worker_in_app=False, domain_gap_seconds=0, max_per_domain_per_hour=0)


@dataclass
class Edge:
    """The employer's job page and the ATS read endpoint, with every fetch recorded."""

    pages: dict[str, str] = field(default_factory=lambda: {FORM: JOB_PAGE})
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
        return 200, url, json.dumps({"id": 4012345})


@pytest.fixture
def edge(monkeypatch: pytest.MonkeyPatch) -> Edge:
    found = Edge()
    monkeypatch.setattr(apply_routes, "IngestFetcher", lambda: found)
    monkeypatch.setattr(apply_routes, "IngestStatusFetcher", lambda: LiveFetcher(found))
    return found


@dataclass
class Flow:
    env: Any
    opener: FakeOpener
    worker: ApplyWorker
    user_id: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client  # type: ignore[no-any-return]

    async def sign_in(self) -> None:
        c = self.env.container
        pid = "p" * 32
        user = await c.repos.users.sign_in_email(email="meera@example.com", now=c.clock.now())
        await c.repos.profiles.save(
            pid,
            {"person": {"name": "Meera Nair"}},
            StoredContext(),
            goal=None,
            owner_hash=None,
            is_sample=False,
        )
        await c.repos.users.claim_profile(user.id, pid)
        token, _ = await c.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        self.user_id, self.headers = user.id, {"cookie": f"{SESSION_COOKIE}={token}"}
        # Your details: the answers the form's personal fields and its India question are filled from
        for key, value, source in (
            ("fullName", "Meera Nair", "resume"),
            ("email", "meera@example.com", "resume"),
            ("needsSponsorship:IN", "No", "first_check"),
        ):
            await c.repos.apply.put_answer(
                StoredAnswer(user_id=user.id, question_key=key, question=key, source=source, ask_each_time=False,
                             answered_at=c.clock.now(), created_at=c.clock.now(), updated_at=c.clock.now(),
                             value=value)
            )  # fmt: skip

    async def match(self) -> str:
        data = js("meera.match.json")
        posting = Posting.model_validate(data["posting"])
        result = MatchResult.model_validate(data["result"])
        c = self.env.container
        await c.repos.postings.put(posting)
        stored = await c.repos.matches.create(
            match_id="m" * 32, user_id=self.user_id, profile_id="p" * 32, posting_hash=posting.hash,
            facts={"name": "Meera Nair", "facts": [], "entries": []}, result=result.to_dict(), band=result.band,
            blocked=result.blocked, version=result.version, now=c.clock.now(),
        )  # fmt: skip
        return str(stored.id)

    async def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.client.post(path, json=body, headers=self.headers)
        assert response.status_code in (200, 201), (path, response.status_code, response.text)
        return response.json()  # type: ignore[no-any-return]

    async def get(self, app_id: str) -> dict[str, Any]:
        response = await self.client.get(f"/api/apply/applications/{app_id}", headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    async def hold(self, app_id: str) -> str | None:
        billing = self.env.container.repos.billing
        return await billing.hold_state(self.user_id, f"apply:{app_id}")  # type: ignore[no-any-return]

    def clicks(self) -> int:
        return sum(1 for d in self.opener.drivers for a in d.actions if a[0] == "click_submit")

    async def to_review(self) -> str:
        """Sign in, consent, upload the resume, check and start, then fill, answer and fill again: the id of an
        application waiting for its approval with nothing left to answer."""
        await self.sign_in()
        assert (await self.post("/api/apply/consent"))["consentAt"]
        uploaded = await self.client.put(
            "/api/apply/profile/resume", content=b"%PDF-1.7 Meera's resume",
            headers={**self.headers, "content-type": "application/pdf", "x-file-name": "Meera-Nair-CV.pdf"},
        )  # fmt: skip
        assert uploaded.status_code == 200 and uploaded.json()["hasResume"] is True
        match_id = await self.match()
        check = await self.post("/api/apply/check", {"matchId": match_id, "applyUrl": FORM})
        assert check["offered"] is True and check["applyUrl"] == FORM
        assert self.opener.opened == []  # the check opens no browser
        started = await self.post(
            "/api/apply/applications", {"matchId": match_id, "resume": "saved", "applyUrl": FORM}
        )
        app_id = str(started["id"])
        assert (started["stage"], started["creditState"]) == ("queued", "held")
        assert await self.hold(app_id) == "held"

        # the worker's fill: the guarded preview browser, nothing sent, one question for the person
        assert await self.worker.run_once() is True
        waiting = await self.get(app_id)
        assert (waiting["stage"], waiting["stopReason"]) == ("waiting_for_you", "questions"), waiting
        (need,) = waiting["needs"]
        assert need["key"] == "question_102" and need["questionKey"].startswith("q:") and need["requiredBy"]
        preview = self.opener.drivers[0]
        assert self.opener.opened[0] == ("preview", frozenset()) and preview.closed
        assert preview.sent_writes() == [] and not preview.submitted and preview.allow_calls == []

        # the answer queues it again; the next fill leaves nothing open
        answered = await self.post(
            f"/api/apply/applications/{app_id}/answers",
            {"answers": [{"questionKey": need["questionKey"], "value": "I want to grow Lumen's stores.", "save": False}]},
        )  # fmt: skip
        assert answered["application"]["stage"] == "queued" and answered["requeued"] == 0
        assert await self.worker.run_once() is True
        review = await self.get(app_id)
        assert review["stage"] == "waiting_for_you" and review["needs"] == [] and review["review"]["digest"]
        assert review["review"]["resume"]["name"] == "Meera-Nair-CV.pdf"
        assert all(row["selector"] for row in review["review"]["fields"])  # every row names its field
        assert self.clicks() == 0 and all(d.sent_writes() == [] for d in self.opener.drivers)
        # the resume goes on only in the submit pass, never in a guarded preview
        assert all(a[0] != "upload" for d in self.opener.drivers for a in d.actions)

        approved = await self.post(
            f"/api/apply/applications/{app_id}/approve", {"digest": review["review"]["digest"]}
        )
        assert approved["stage"] == "approved"
        return app_id


@pytest.fixture
def start(build_settings: MakeSettings, edge: Edge):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**opener_kw: Any) -> AsyncIterator[Flow]:
        settings = build_settings(auto_apply_enabled=True, browser_use_api_key=FAKE_KEY, apply=SETTINGS)
        opener = FakeOpener(FORM_PAGE, url=FORM, cost_micro_usd=BROWSER_COST, **opener_kw)
        async with running(settings, apply_opener=opener) as env:
            assert env.app.state.apply_worker is None  # driven by hand here, one pass at a time
            yield Flow(env, opener, build_apply_worker(env.container))

    return run


async def test_start_fill_answer_approve_and_send_once_with_the_hold_captured(start: Any, edge: Edge) -> None:
    async with start() as flow:
        app_id = await flow.to_review()
        assert await flow.worker.run_once() is True  # the submit pass
        sent = await flow.get(app_id)
        assert (sent["stage"], sent["creditState"], sent["hasConfirmation"]) == ("sent", "charged", True)
        assert sent["submittedAt"] and sent["sentAt"] == sent["submittedAt"]
        assert await flow.hold(app_id) == "charged"
        assert flow.clicks() == 1
        send = flow.opener.drivers[-1]
        assert flow.opener.opened[-1][0] == "submit" and send.submitted and send.closed
        kinds_sent = [a[0] for a in send.actions]
        assert kinds_sent.count("upload") == 1  # the resume goes on once, in the submit pass
        assert kinds_sent.index("upload") < kinds_sent.index("click_submit")
        assert send.allow_calls and set(send.allow_calls[-1]) <= {
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
        }
        listed = (await flow.client.get("/api/apply/applications", headers=flow.headers)).json()[
            "applications"
        ]
        assert [(a["id"], a["stage"], a["sentAt"]) for a in listed] == [(app_id, "sent", sent["sentAt"])]
        shot = await flow.client.get(
            f"/api/apply/applications/{app_id}/screenshot?kind=confirmation", headers=flow.headers
        )
        assert shot.status_code == 200 and shot.content
        assert await flow.worker.run_once() is False  # nothing left to run: never a second send
        assert flow.clicks() == 1
        kinds = [e["toStage"] for e in sent["events"] if e.get("toStage")]
        assert kinds == ["filling", "waiting_for_you", "queued", "filling", "waiting_for_you", "approved",
                         "submitting", "sent"]  # fmt: skip
        spend = await flow.env.container.repos.apply.list_run_metrics(sent_since(sent))
        assert [(m.run_kind, m.outcome) for m in reversed(spend)] == [
            ("fill", "questions"), ("fill", "success"), ("submit", "success"),
        ]  # fmt: skip
        assert all(
            m.cost_micro_usd >= BROWSER_COST for m in spend
        )  # each Browser Use session's cost is on its run
    assert edge.live_fetches and edge.page_fetches  # the start checks read the job page and the ATS endpoint


async def test_an_unclear_outcome_is_check_this_one_and_not_sent_releases_the_hold(start: Any) -> None:
    async with start(after_submit=QUIET, after_submit_url=FORM) as flow:
        app_id = await flow.to_review()
        assert await flow.worker.run_once() is True  # the submit pass: clicked, then nothing clear
        unclear = await flow.get(app_id)
        assert (unclear["stage"], unclear["creditState"]) == ("check_this_one", "held")
        assert unclear["manualUrl"] == FORM and "sentAt" not in unclear
        assert await flow.hold(app_id) == "held" and flow.clicks() == 1
        assert await flow.worker.run_once() is False  # never retried
        assert flow.clicks() == 1

        settled = await flow.post(f"/api/apply/applications/{app_id}/resolve", {"outcome": "not_sent"})
        assert (settled["stage"], settled["creditState"]) == ("failed", "released")
        assert await flow.hold(app_id) == "released"
        assert settled["events"][-1]["source"] == "user" and settled["events"][-1]["toStage"] == "failed"
        assert (
            await flow.worker.run_once() is False and flow.clicks() == 1
        )  # a failed row is never sent again


def sent_since(detail: dict[str, Any]) -> Any:
    from datetime import datetime, timedelta

    return datetime.fromisoformat(detail["createdAt"]) - timedelta(minutes=1)
