"""The Leafout Weekly's routes end to end through the real app over a real SQLite file, with the real service and a scripted
Claude at the edge: sign-in is required, the subscription, the edition built on demand (once a week, rate limited), the archive
and the reading of one edition (marking it read, scoped to its reader), the one-click unsubscribe by token with no sign-in, the
cross-site guard, and `Cache-Control: no-store` on every answer. Nothing here calls the network.

The reader is a copy of the bundled persona's graph stored as an ordinary profile and claimed by the account.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tayyari.contract.weekly import WeeklyEdition, WeeklyEditionList, WeeklySubscription
from tayyari.core.settings import Settings
from tayyari.weekly.times import week_of
from tests.integration.support import Env, RecordingClaude, roomy_guard, running
from tests.unit.jobsearch.support import listing
from tests.unit.prepare.support import riya
from tests.unit.weekly.support import (
    POSTING_REACT,
    judge_answer,
    news_answer,
    news_search,
)

MakeSettings = Callable[..., Settings]
SESSION_COOKIE = "tayyari_session"
COPY_ID = "c" * 32


def is_no_store(response: httpx.Response) -> bool:
    return response.headers.get("cache-control") == "no-store"


@dataclass
class Rig:
    env: Env

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def account(
        self, email: str = "ana@example.com", *, graph: bool = True
    ) -> tuple[str, dict[str, str]]:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        if graph:
            pid = COPY_ID if email == "ana@example.com" else ("d" + user.id.replace("-", ""))[:32]
            profile, context = riya()
            doc = {**profile.to_dict(), "id": pid}
            await container.repos.profiles.save(
                pid, doc, context, goal=profile.goal, owner_hash=None, is_sample=False
            )
            await container.repos.users.claim_profile(user.id, pid)
        return user.id, {"cookie": f"{SESSION_COOKIE}={token}"}

    async def open_role(self) -> None:
        """One new opening of the reader's kind, first seen now, on an employer's own board."""
        item = listing("1" * 32, title="Frontend Engineer", employer_id="kestrelpay", text=POSTING_REACT)
        await self.env.container.repos.discover.upsert_employer("kestrelpay", [item], now=datetime.now(UTC))

    async def subscribe(self, headers: dict[str, str], **body: Any) -> httpx.Response:
        payload = {"status": "active", "day": "mon", "sections": ["jobs", "progress", "news", "tip"], **body}
        return await self.client.put("/api/weekly/subscription", json=payload, headers=headers)

    async def preview(self, headers: dict[str, str]) -> httpx.Response:
        return await self.client.post("/api/weekly/editions/preview", headers=headers)

    async def token_of(self, user_id: str) -> str:
        row = await self.env.container.repos.weekly.get_subscription(user_id)
        assert row is not None
        return row.unsubscribe_token


def good_claude() -> RecordingClaude:
    claude = RecordingClaude()
    claude.script_structured("weekly.judge", judge_answer, repeat=True)
    claude.script_search(news_search(), repeat=True)
    claude.script_structured("weekly.news.read", news_answer(), repeat=True)
    return claude


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(*, claude: RecordingClaude | None = None, **overrides: Any) -> AsyncIterator[Rig]:
        overrides.setdefault("guard", roomy_guard())
        async with running(build_settings(**overrides), claude=claude or good_claude()) as env:
            yield Rig(env)

    return run


# ───────────────────────── sign-in ─────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/weekly/subscription"),
        ("PUT", "/api/weekly/subscription"),
        ("GET", "/api/weekly/editions"),
        ("GET", "/api/weekly/editions/" + "a" * 32),
        ("POST", "/api/weekly/editions/preview"),
    ],
)
async def test_the_reader_routes_need_a_sign_in(start: Any, method: str, path: str) -> None:
    async with start() as rig:
        body = {"json": {"status": "active", "day": "mon", "sections": ["tip"]}} if method == "PUT" else {}
        response = await rig.client.request(method, path, **body)
        assert response.status_code == 401


# ───────────────────────── the subscription ─────────────────────────


async def test_a_reader_who_never_subscribed_is_off(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.account()
        response = await rig.client.get("/api/weekly/subscription", headers=headers)
        assert response.status_code == 200 and is_no_store(response)
        sub = WeeklySubscription.model_validate(response.json())
        assert (sub.status, sub.email, sub.day, sub.unread) == ("off", "ana@example.com", "mon", 0)
        assert sub.sections == ["jobs", "progress", "news", "tip"]


async def test_subscribing_changing_the_day_pausing_and_stopping(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.account()
        on = await rig.subscribe(headers, sections=["tip", "jobs"])
        assert on.status_code == 200 and is_no_store(on)
        assert WeeklySubscription.model_validate(on.json()).sections == ["jobs", "tip"]
        friday = await rig.subscribe(headers, day="fri")
        assert friday.json()["day"] == "fri" and friday.json()["status"] == "active"
        paused = await rig.subscribe(headers, status="paused", pauseDays=14)
        assert paused.json()["status"] == "paused" and len(paused.json()["pausedUntil"]) == 10
        off = await rig.subscribe(headers, status="off", day="fri")
        assert off.json()["status"] == "off" and "pausedUntil" not in off.json()
        read = await rig.client.get("/api/weekly/subscription", headers=headers)
        assert read.json()["status"] == "off" and read.json()["day"] == "fri"


@pytest.mark.parametrize(
    "body",
    [
        {"status": "paused", "pauseDays": 3},
        {"status": "paused", "pauseDays": 120},
        {"status": "active", "pauseDays": 14},
        {"status": "active", "sections": []},
        {"status": "weekly"},
        {"status": "active", "day": "wed"},
        {"status": "active", "sections": ["jobs", "gossip"]},
    ],
)
async def test_a_bad_subscription_is_a_400(start: Any, body: dict[str, Any]) -> None:
    async with start() as rig:
        _, headers = await rig.account()
        response = await rig.subscribe(headers, **body)
        assert response.status_code == 400 and "error" in response.json()
        assert (await rig.client.get("/api/weekly/subscription", headers=headers)).json()["status"] == "off"


async def test_the_body_must_be_json(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.account()
        response = await rig.client.put("/api/weekly/subscription", content=b"status=active", headers=headers)
        assert response.status_code == 415


# ───────────────────────── the edition ─────────────────────────


async def test_the_edition_is_built_now_and_the_second_ask_returns_the_same_one(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.account()
        await rig.open_role()
        first = await rig.preview(headers)
        assert first.status_code == 200 and is_no_store(first), first.text
        edition = WeeklyEdition.model_validate(first.json())
        assert edition.issue == 1 and edition.reader == "Riya Kapoor" and edition.readAt is not None
        assert edition.weekOf == week_of(datetime.now(UTC), "mon").isoformat()
        assert len(edition.jobs) == 1 and edition.jobs[0].applyUrl.startswith(
            "https://job-boards.greenhouse.io/kestrelpay/"
        )
        assert edition.jobs[0].met and edition.news and edition.tip and edition.quote
        calls = len(rig.env.claude.calls)
        assert calls > 0 and rig.env.container.spend_guard.reserved_micro == 0
        again = await rig.preview(headers)
        assert again.status_code == 200 and again.json()["id"] == edition.id
        assert len(rig.env.claude.calls) == calls  # nothing was built or paid for the second time
        sub = await rig.client.get("/api/weekly/subscription", headers=headers)
        assert sub.json()["unread"] == 0  # a preview is shown at once, so it is read


async def test_the_paid_part_is_recorded_with_its_purposes(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.account()
        await rig.open_role()
        await rig.preview(headers)
        purposes = sorted(c.ctx.purpose for c in rig.env.claude.calls)
        assert purposes == ["weekly.judge", "weekly.news", "weekly.news", "weekly.news", "weekly.news.read"]
        assert all(c.model == rig.env.settings.weekly.model for c in rig.env.claude.calls)


async def test_an_edition_is_built_even_when_the_spend_ceiling_is_reached(start: Any) -> None:
    async with start(guard=roomy_guard(daily_ceiling_usd=0.001)) as rig:
        _, headers = await rig.account()
        await rig.open_role()
        response = await rig.preview(headers)
        assert response.status_code == 200
        edition = WeeklyEdition.model_validate(response.json())
        assert edition.news == [] and len(edition.jobs) == 1 and edition.jobs[0].met == []
        assert rig.env.claude.calls == []


async def test_building_on_demand_is_rate_limited_but_returning_a_built_one_is_free(start: Any) -> None:
    async with start(guard=roomy_guard(weekly_preview_per_hour=1)) as rig:
        _, ana = await rig.account("ana@example.com")
        _, ben = await rig.account("ben@example.com")
        same_address = {"cf-connecting-ip": "203.0.113.9"}
        assert (await rig.preview({**ana, **same_address})).status_code == 200
        assert (await rig.preview({**ana, **same_address})).status_code == 200  # already built: not counted
        limited = await rig.preview({**ben, **same_address})
        assert limited.status_code == 429 and "Retry-After" in limited.headers
        assert (await rig.preview({**ben, "cf-connecting-ip": "203.0.113.10"})).status_code == 200


async def test_a_reader_with_no_graph_gets_a_short_paper(start: Any) -> None:
    async with start() as rig:
        _, headers = await rig.account(graph=False)
        response = await rig.preview(headers)
        assert response.status_code == 200
        edition = WeeklyEdition.model_validate(response.json())
        assert edition.jobs == [] and edition.news == [] and edition.tip
        assert rig.env.claude.calls == []


# ───────────────────────── the archive ─────────────────────────


async def test_the_archive_lists_editions_and_opening_one_marks_it_read(start: Any) -> None:
    async with start() as rig:
        uid, headers = await rig.account()
        await rig.open_role()
        await rig.subscribe(headers)
        container = rig.env.container
        built = await container.weekly.build(uid)
        assert built is not None
        listed = await rig.client.get("/api/weekly/editions", headers=headers)
        assert listed.status_code == 200 and is_no_store(listed)
        archive = WeeklyEditionList.model_validate(listed.json())
        assert [(e.id, e.issue, e.read) for e in archive.editions] == [(built.id, 1, False)]
        assert (await rig.client.get("/api/weekly/subscription", headers=headers)).json()["unread"] == 1

        opened = await rig.client.get(f"/api/weekly/editions/{built.id}", headers=headers)
        assert opened.status_code == 200 and is_no_store(opened)
        edition = WeeklyEdition.model_validate(opened.json())
        assert edition.id == built.id and edition.readAt is not None
        assert (await rig.client.get("/api/weekly/subscription", headers=headers)).json()["unread"] == 0
        relisted = WeeklyEditionList.model_validate(
            (await rig.client.get("/api/weekly/editions", headers=headers)).json()
        )
        assert relisted.editions[0].read is True


async def test_another_readers_edition_and_a_bad_id_are_the_same_404(start: Any) -> None:
    async with start() as rig:
        uid, ana = await rig.account("ana@example.com")
        _, ben = await rig.account("ben@example.com")
        built = await rig.env.container.weekly.build(uid)
        assert built is not None
        theirs = await rig.client.get(f"/api/weekly/editions/{built.id}", headers=ben)
        unknown = await rig.client.get("/api/weekly/editions/" + "0" * 32, headers=ana)
        malformed = await rig.client.get("/api/weekly/editions/not-an-id", headers=ana)
        assert theirs.status_code == unknown.status_code == malformed.status_code == 404
        assert theirs.json() == unknown.json() == malformed.json()
        assert (await rig.client.get("/api/weekly/editions", headers=ben)).json() == {"editions": []}


# ───────────────────────── unsubscribe: no sign-in, the token is the credential ─────────────────────────


async def test_the_link_opens_a_page_with_a_button_and_does_nothing_until_it_is_pressed(start: Any) -> None:
    async with start() as rig:
        uid, headers = await rig.account()
        await rig.subscribe(headers)
        token = await rig.token_of(uid)
        page = await rig.client.get("/api/weekly/unsubscribe", params={"token": token})
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        assert is_no_store(page) and page.headers["x-robots-tag"] == "noindex"
        assert "<button" in page.text and f"/api/weekly/unsubscribe?token={token}" in page.text
        assert (await rig.client.get("/api/weekly/subscription", headers=headers)).json()[
            "status"
        ] == "active"


async def test_the_button_and_the_one_click_post_both_unsubscribe_with_no_sign_in(start: Any) -> None:
    async with start() as rig:
        uid, headers = await rig.account()
        await rig.subscribe(headers)
        token = await rig.token_of(uid)
        # RFC 8058: the mail provider posts the address from the header with this body, no cookie, no Origin
        done = await rig.client.post(
            "/api/weekly/unsubscribe",
            params={"token": token},
            content=b"List-Unsubscribe=One-Click",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert done.status_code == 200 and "unsubscribed" in done.text.lower() and is_no_store(done)
        assert (await rig.client.get("/api/weekly/subscription", headers=headers)).json()["status"] == "off"
        again = await rig.client.post("/api/weekly/unsubscribe", params={"token": token})
        assert again.status_code == 200  # safe to repeat


async def test_an_unknown_or_missing_token_changes_nothing(start: Any) -> None:
    async with start() as rig:
        uid, headers = await rig.account()
        await rig.subscribe(headers)
        for params in ({"token": "nope"}, {}, {"token": "x" * 500}):
            page = await rig.client.get("/api/weekly/unsubscribe", params=params)
            posted = await rig.client.post("/api/weekly/unsubscribe", params=params)
            assert page.status_code == posted.status_code == 404
            assert "not recognised" in posted.text
        assert (await rig.client.get("/api/weekly/subscription", headers=headers)).json()[
            "status"
        ] == "active"
        assert uid


async def test_a_cross_site_write_is_refused_but_the_mail_providers_post_is_not(start: Any) -> None:
    async with start() as rig:
        uid, headers = await rig.account()
        await rig.subscribe(headers)
        hostile = {**headers, "origin": "https://evil.example", "sec-fetch-site": "cross-site"}
        refused = await rig.client.put(
            "/api/weekly/subscription",
            json={"status": "off", "day": "mon", "sections": ["tip"]},
            headers=hostile,
        )
        assert refused.status_code == 403
        assert (await rig.client.get("/api/weekly/subscription", headers=headers)).json()[
            "status"
        ] == "active"
        token = await rig.token_of(uid)
        provider = await rig.client.post(
            "/api/weekly/unsubscribe", params={"token": token}
        )  # no Origin, no Sec-Fetch-Site
        assert provider.status_code == 200


async def test_deleting_the_account_takes_its_paper_and_its_unsubscribe_link(start: Any) -> None:
    async with start() as rig:
        uid, headers = await rig.account()
        await rig.subscribe(headers)
        token = await rig.token_of(uid)
        built = await rig.env.container.weekly.build(uid)
        assert built is not None
        assert await rig.env.container.repos.users.delete(uid)
        assert (await rig.client.get("/api/weekly/unsubscribe", params={"token": token})).status_code == 404
        assert await rig.env.container.repos.weekly.list_editions(uid, 10) == []


async def test_the_background_loop_is_off_in_tests_and_on_by_default() -> None:
    assert (
        Settings(_env_file=None).weekly.enabled is False
    )  # the test environment switches it off (tests/conftest.py)
    from tayyari.core.settings import WeeklySettings

    assert WeeklySettings().enabled is True


async def test_the_loop_builds_and_emails_when_it_is_switched_on(build_settings: MakeSettings) -> None:
    from tayyari.core.settings import WeeklySettings
    from tests.fakes.auth import FakeMailer
    from tests.fakes.clock import FakeClock
    from tests.integration.support import eventually
    from tests.unit.weekly.support import MONDAY_MORNING

    mailer = FakeMailer()
    settings = build_settings(weekly=WeeklySettings(enabled=True, first_tick_delay_s=0.0, tick_seconds=0.05))
    async with running(settings, claude=good_claude(), mailer=mailer, clock=FakeClock(MONDAY_MORNING)) as env:
        rig = Rig(env)
        assert env.container.weekly_loop is not None and not env.container.weekly_loop.done()
        uid, headers = await rig.account()
        await rig.open_role()
        assert (await rig.subscribe(headers)).status_code == 200
        await eventually(lambda: bool(mailer.sent), within=10.0, what="the first email")
        assert mailer.sent[0].to == "ana@example.com"
        assert mailer.sent[0].headers is not None and "List-Unsubscribe" in mailer.sent[0].headers
        stored = await env.container.repos.weekly.list_editions(uid, 5)
        assert [(e.issue, e.week_of.isoformat()) for e in stored] == [(1, "2026-10-19")]
        await eventually(lambda: stored[0].id in mailer.sent[0].html, what="the email to name the edition")
    assert env.container.weekly_loop is None  # stopped with the app
