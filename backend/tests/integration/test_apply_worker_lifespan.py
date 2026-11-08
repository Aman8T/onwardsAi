"""The auto-apply worker inside the app (spec 8 decision 4): the lifespan starts it only when auto-apply is on, a Browser
Use key is set and ``worker_in_app`` is true, logs only that it started or why it was skipped, and cancels it at
shutdown. With the settings overridden and a fake opener (no Browser Use call), the started worker really runs: a
queued application in the real SQLite database is filled to waiting_for_you."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tayyari.core.settings import ApplySettings, Settings
from tayyari.storage.ports import StoredAnswer, StoredApplication, account_owner_key
from tests.fakes.apply_browser import FakeOpener
from tests.integration.support import eventually, running

MakeSettings = Callable[..., Settings]
GH = (Path(__file__).resolve().parents[1] / "data" / "apply" / "greenhouse_job.html").read_text(
    encoding="utf-8"
)
URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"
T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
FAKE_KEY = SecretStr("bu-test-not-a-key")


class Logs:
    """Stands in for the lifespan's logger (`Container.build` reconfigures structlog, so a capture would miss it)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __getattr__(self, level: str) -> Callable[..., None]:
        def record(event: str, **kw: Any) -> None:
            self.events.append({"event": event, "level": level, **kw})

        return record


def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    logs = Logs()
    monkeypatch.setattr("tayyari.api.lifespan.log", logs)
    return logs.events


FAST = ApplySettings(worker_poll_seconds=0.1, domain_gap_seconds=0, max_per_domain_per_hour=0)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({}, "auto-apply is off"),
        ({"auto_apply_enabled": True}, "no Browser Use key"),
        (
            {
                "auto_apply_enabled": True,
                "browser_use_api_key": FAKE_KEY,
                "apply": ApplySettings(worker_in_app=False),
            },
            "the worker runs as its own process",
        ),
    ],
)
async def test_the_worker_does_not_start_unless_it_is_configured(
    build_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object], reason: str
) -> None:
    logs = captured(monkeypatch)
    async with running(build_settings(**overrides)) as env:
        assert env.app.state.apply_worker is None
    events = [e for e in logs if str(e["event"]).startswith("apply_worker")]
    assert [(e["event"], e.get("reason")) for e in events] == [("apply_worker_skipped", reason)]


async def test_with_a_key_and_a_fake_opener_the_worker_starts_runs_and_stops_cleanly(
    build_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = build_settings(auto_apply_enabled=True, browser_use_api_key=FAKE_KEY, apply=FAST)
    opener = FakeOpener(GH, url=URL)
    logs = captured(monkeypatch)
    async with running(settings, apply_opener=opener) as env:
        task = env.app.state.apply_worker
        assert isinstance(task, asyncio.Task) and not task.done()
        repos = env.container.repos
        user = (await repos.users.sign_in_email(email="riya@example.com", now=T0)).id
        owner = account_owner_key(user)
        await repos.apply.put_answer(
            StoredAnswer(user_id=user, question_key="needsSponsorship:IN", question="Sponsorship in India",
                         source="first_check", ask_each_time=False, answered_at=T0, created_at=T0, updated_at=T0,
                         value="No")
        )  # fmt: skip
        await repos.apply.put_resume(owner, user_id=user, name="Riya-Kapoor.pdf", media_type="application/pdf",
                                     data=b"%PDF-1.7 cv", now=T0)  # fmt: skip
        await repos.apply.create_application(
            StoredApplication(
                id="ap1", owner_key=owner, user_id=user, source="match", job_url=URL, apply_url=URL,
                domain="job-boards.greenhouse.io", ats="greenhouse", stage="queued", created_at=T0, updated_at=T0,
                company="Acme", resume_choice="saved", job={"typed": {"question_102": "I build payment APIs."}},
            )
        )  # fmt: skip
        await eventually(
            lambda: bool(opener.drivers) and opener.drivers[0].closed, what="a fill by the worker"
        )
        stage = ""
        for _ in range(100):
            app = await repos.apply.get_application("ap1", None)
            stage = app.stage if app is not None else ""
            if stage == "waiting_for_you":
                break
            await asyncio.sleep(0.05)
        assert stage == "waiting_for_you"
        assert opener.opened == [("preview", frozenset())] and opener.drivers[0].sent_writes() == []
    assert task.done() and env.app.state.apply_worker is None
    assert [e["event"] for e in logs if str(e["event"]).startswith("apply_worker")] == [
        "apply_worker_started"
    ]
    assert not any("bu-test" in str(e) for e in logs)  # the key is never logged
