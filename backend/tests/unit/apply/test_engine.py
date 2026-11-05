"""The engine end to end on fakes: a queued Greenhouse application is filled, reviewed and screenshotted without a submit;
an approval of the exact digest lets the worker submit it; a run on a site with no playbook and no AI fails honestly. The last
test drives a real Chromium against the saved page (offline, requests answered from memory) when TAYYARI_TEST_BROWSER=1."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tayyari.apply.engine import ApplyEngine
from tayyari.apply.worker import ApplyWorker
from tayyari.core.settings import ApplySettings
from tayyari.storage.ports import StoredApplication
from tests.fakes.apply import FakeApplyRepo
from tests.fakes.apply_browser import FakeOpener

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"
GH = (DATA / "greenhouse_job.html").read_text(encoding="utf-8")
T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
OWNER = "a:" + "3" * 40
URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        self.t += timedelta(seconds=30)
        return self.t

    def monotonic(self) -> float:
        return 0.0


def application(ats: str = "greenhouse", typed: dict[str, str] | None = None) -> StoredApplication:
    return StoredApplication(
        id="ap1", owner_key=OWNER, source="link", job_url=URL, apply_url=URL, domain="job-boards.greenhouse.io", ats=ats,
        stage="queued", created_at=T0, updated_at=T0, title="Backend Engineer", company="Acme",
        job={"typed": typed or {"question_102": "I want to build Acme's APIs."}},
    )  # fmt: skip


async def setup(page: str = GH, ats: str = "greenhouse") -> tuple[FakeApplyRepo, ApplyEngine, FakeOpener]:
    repo = FakeApplyRepo()
    await repo.put_details(OWNER, user_id=None, details={"firstName": "Riya", "lastName": "Kapoor", "email": "riya@example.com",
                                                        "needsSponsorship": "No"}, now=T0)  # fmt: skip
    await repo.put_resume(
        OWNER, user_id=None, name="cv.pdf", media_type="application/pdf", data=b"%PDF", now=T0
    )
    await repo.create_application(application(ats))
    opener = FakeOpener(page)
    engine = ApplyEngine(repo, ApplySettings(domain_gap_seconds=0), opener, Clock())  # type: ignore[arg-type]
    return repo, engine, opener


async def test_fill_stops_before_submit_with_a_review_and_a_screenshot() -> None:
    repo, engine, opener = await setup()
    assert await ApplyWorker(engine).run_once() is True
    app = await repo.get_application("ap1", OWNER)
    assert (
        app is not None
        and app.stage == "ready_to_submit"
        and app.review is not None
        and app.lease_owner is None
    )
    assert app.review_digest == app.review["digest"] and app.review["mode"] == "playbook"
    assert await repo.get_screenshot("ap1", OWNER) and not opener.drivers[0].submitted
    assert [e.to_stage for e in await repo.list_events("ap1")] == ["filling", "ready_to_submit"]
    (metric,) = repo.metrics
    assert (metric.mode, metric.outcome, metric.playbook_id) == (
        "playbook",
        "success",
        "builtin:greenhouse@1",
    )
    assert await ApplyWorker(engine).run_once() is False  # not approved: nothing to do


async def test_an_approved_application_is_submitted_and_applied() -> None:
    repo, engine, opener = await setup()
    await ApplyWorker(engine).run_once()
    app = await repo.get_application("ap1", OWNER)
    assert app is not None
    await repo.update_application("ap1", {"approved_at": T0, "approved_digest": app.review_digest}, now=T0)
    await ApplyWorker(engine).run_once()
    done = await repo.get_application("ap1", OWNER)
    assert done is not None and done.stage == "applied" and done.submitted_at is not None
    assert opener.drivers[-1].submitted and [m.run_kind for m in repo.metrics] == ["fill", "submit"]


async def test_a_tampered_approval_is_refused() -> None:
    repo, engine, opener = await setup()
    await ApplyWorker(engine).run_once()
    await repo.update_application("ap1", {"approved_at": T0, "approved_digest": "f" * 64}, now=T0)
    await ApplyWorker(engine).run_once()
    app = await repo.get_application("ap1", OWNER)
    assert app is not None and app.stage == "needs_user" and not any(d.submitted for d in opener.drivers)


async def test_no_playbook_and_no_ai_fails_with_a_reason() -> None:
    repo, engine, _ = await setup(ats="unknown")
    await ApplyWorker(engine).run_once()
    app = await repo.get_application("ap1", OWNER)
    assert app is not None and app.stage == "failed" and "No playbook" in (app.stage_reason or "")
    assert repo.metrics[0].failure_reason == "no_playbook"


@pytest.mark.skipif(
    os.environ.get("TAYYARI_TEST_BROWSER") != "1",
    reason="set TAYYARI_TEST_BROWSER=1 to drive a real Chromium",
)
async def test_real_chromium_fills_and_submits_the_saved_page_offline() -> None:
    from tayyari.apply.browser import PlaywrightBrowser

    posts: list[str] = []

    def responder(method: str, url: str) -> tuple[int, str, str] | None:
        if method == "GET" and url.startswith(URL):
            return 200, "text/html", GH
        if method == "POST" and "/applications" in url:
            posts.append(url)
            return 200, "text/html", "<html><body><h1>Thank you for applying!</h1></body></html>"
        return None

    repo, _, _ = await setup()
    browser = PlaywrightBrowser(ApplySettings(), responder)
    engine = ApplyEngine(repo, ApplySettings(domain_gap_seconds=0), browser, Clock())
    try:
        await ApplyWorker(engine).run_once()
        app = await repo.get_application("ap1", OWNER)
        assert app is not None and app.stage == "ready_to_submit", app.stage_reason
        shot = await repo.get_screenshot("ap1", OWNER)
        assert shot is not None and shot[:2] == b"\xff\xd8" and posts == []
        await repo.update_application(
            "ap1", {"approved_at": T0, "approved_digest": app.review_digest}, now=T0
        )
        await ApplyWorker(engine).run_once()
        done = await repo.get_application("ap1", OWNER)
        assert done is not None and done.stage == "applied" and len(posts) == 1
    finally:
        await browser.close()
