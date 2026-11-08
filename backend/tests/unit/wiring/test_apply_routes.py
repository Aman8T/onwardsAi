"""Auto-apply through the real app with fakes: details and resume saved, a pasted Greenhouse link queued, the worker fills it,
the person answers the custom question, approves exactly the reviewed digest, the worker submits, and the tracker shows the
stage. A LinkedIn link is only tracked (manual apply); with AUTO_APPLY_ENABLED off everything but the status is a 404."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tayyari.api.routes import apply as apply_routes
from tayyari.apply.engine import ApplyEngine
from tayyari.apply.worker import ApplyWorker
from tayyari.core.settings import ApplySettings, Settings
from tests.fakes.apply_browser import FakeOpener
from tests.fakes.repos import FakeRepositories
from tests.unit.wiring.support import running_app

GH = (Path(__file__).resolve().parents[2] / "data" / "apply" / "greenhouse_job.html").read_text(
    encoding="utf-8"
)
URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"


class Pages:
    async def fetch(self, url: str) -> tuple[str, str]:
        return url, GH


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_routes, "IngestFetcher", Pages)


async def test_paste_fill_answer_approve_submit_track(make_settings: Callable[..., Settings]) -> None:
    async with running_app(make_settings(auto_apply_enabled=True), repos=FakeRepositories().bundle) as h:
        c = h.client
        status = (await c.get("/api/apply/status")).json()
        assert status["enabled"] is True and status["anonymous"] is True
        details = {
            "firstName": "Riya",
            "lastName": "Kapoor",
            "email": "riya@example.com",
            "needsSponsorship": "No",
        }
        assert (await c.put("/api/apply/profile", json={"details": details})).status_code == 200
        assert "tayyari_apply" in c.cookies
        cv = await c.put(
            "/api/apply/profile/resume",
            content=b"%PDF-1.7",
            headers={"content-type": "application/pdf", "x-file-name": "cv.pdf"},
        )
        assert cv.json()["hasResume"] is True
        made = await c.post("/api/apply/applications", json={"url": URL})
        assert made.status_code == 201, made.text
        app: dict[str, Any] = made.json()
        assert (app["stage"], app["ats"], app["company"]) == ("queued", "greenhouse", "Acme")

        engine = ApplyEngine(
            h.container.repos.apply, ApplySettings(domain_gap_seconds=0), FakeOpener(GH), h.container.clock
        )
        worker = ApplyWorker(engine)
        await worker.run_once()
        app = (await c.get(f"/api/apply/applications/{app['id']}")).json()
        # the India sponsorship question is asked too: the legacy details name no country, so they never fill it
        assert app["stage"] == "needs_user" and sorted(n["key"] for n in app["needs"]) == [
            "question_102",
            "question_103",
        ]
        answered = await c.post(f"/api/apply/applications/{app['id']}/answers",
                                json={"answers": [{"key": "question_102", "value": "To build your APIs."},
                                                  {"key": "question_103", "value": "No"}], "remember": True})  # fmt: skip
        assert answered.json()["stage"] == "queued"
        assert (await c.get("/api/apply/profile")).json()["savedAnswers"] == 2

        await worker.run_once()
        app = (await c.get(f"/api/apply/applications/{app['id']}")).json()
        assert app["stage"] == "ready_to_submit" and app["hasScreenshot"] is True
        shot = await c.get(f"/api/apply/applications/{app['id']}/screenshot")
        assert shot.status_code == 200 and shot.headers["content-type"] == "image/jpeg"
        wrong = await c.post(f"/api/apply/applications/{app['id']}/approve", json={"digest": "0" * 64})
        assert wrong.status_code == 409
        ok = await c.post(
            f"/api/apply/applications/{app['id']}/approve", json={"digest": app["review"]["digest"]}
        )
        assert ok.status_code == 200 and ok.json()["approvedAt"]

        await worker.run_once()
        listed = (await c.get("/api/apply/applications")).json()["applications"]
        assert [a["stage"] for a in listed] == ["applied"]
        moved = await c.post(f"/api/apply/applications/{app['id']}/stage", json={"stage": "interviewing"})
        assert moved.json()["stage"] == "interviewing"
        bad = await c.post(f"/api/apply/applications/{app['id']}/stage", json={"stage": "ready_to_submit"})
        assert bad.status_code == 400

        linkedin = (
            await c.post("/api/apply/applications", json={"url": "https://www.linkedin.com/jobs/view/1"})
        ).json()
        assert linkedin["stage"] == "needs_user" and linkedin["manualUrl"].startswith(
            "https://www.linkedin.com"
        )


async def test_everything_but_the_status_is_off_by_default(make_settings: Callable[..., Settings]) -> None:
    async with running_app(make_settings(), repos=FakeRepositories().bundle) as h:
        assert (await h.client.get("/api/apply/status")).json()["enabled"] is False
        assert (await h.client.get("/api/apply/applications")).status_code == 404
        assert (await h.client.post("/api/apply/applications", json={"url": URL})).status_code == 404
