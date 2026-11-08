"""Browser Use: the REST client over ``httpx2.MockTransport``, and the opener over fakes (no network, no browser).

The opener must hand out a driver only after a blocked probe, and must stop the browser on every path: a failed
connection, a probe that was not blocked, a run that raised, a Playwright close that raised.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from tayyari.apply.browser_use import (
    API_KEY_HEADER,
    BrowserUseBusy,
    BrowserUseDriver,
    BrowserUseError,
    BrowserUseOpener,
    CreatedBrowser,
    HttpBrowserUseApi,
    usd_to_micro,
)
from tayyari.apply.driver import GuardNotActive
from tayyari.apply.netguard import PROBE_PAGE, PROBE_URL, extension_zip
from tayyari.core.settings import ApplySettings, Settings
from tayyari.llm.errors import SpendCeilingReached
from tests.fakes.billing import captured_events
from tests.fakes.browser_use import FAKE_CDP_URL, FakeBrowserUseApi, FakeCdpSession, FakeConnect

BASE = "https://api.browser-use.test/api/v4"
KEY = "bu-test-key-NOT-REAL"
Handler = Callable[[httpx2.Request], httpx2.Response]


class Recorder:
    """A scripted Browser Use: one answer per call, in order, and every request kept."""

    def __init__(self, *answers: httpx2.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def api(recorder: Recorder, sleeps: list[float] | None = None, **kwargs: Any) -> HttpBrowserUseApi:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return HttpBrowserUseApi(
        SecretStr(KEY), BASE, transport=httpx2.MockTransport(recorder), sleep=sleep, cost_poll_s=0, **kwargs
    )


def created(browser_id: str = "b-1") -> httpx2.Response:
    return httpx2.Response(
        201,
        json={"id": browser_id, "status": "active", "cdpUrl": FAKE_CDP_URL, "liveUrl": "https://live.test/x"},
    )


# ── the REST client ──


async def test_create_sends_no_captcha_solving_no_proxy_and_the_extension() -> None:
    rec = Recorder(created())
    got = await api(rec).create_browser(extension_ids=["ext-1"], minutes=7, label="apply-preview")
    assert got.id == "b-1" and got.cdp_url == FAKE_CDP_URL
    request = rec.requests[0]
    assert request.method == "POST" and str(request.url) == f"{BASE}/browsers"
    assert request.headers[API_KEY_HEADER] == KEY
    body = json.loads(request.content)
    assert body["solveCaptchas"] is False and body["proxyCountryCode"] is None
    assert body["extensionIds"] == ["ext-1"] and body["timeout"] == 7
    assert body["metadata"] == {"app": "tayyari", "label": "apply-preview"}


async def test_the_extension_is_uploaded_as_a_multipart_zip() -> None:
    rec = Recorder(httpx2.Response(201, json={"id": "ext-9", "name": "Tayyari guard", "version": "1.0.0"}))
    data, digest = extension_zip()
    assert await api(rec).upload_extension(data, digest) == "ext-9"
    request = rec.requests[0]
    assert request.method == "POST" and str(request.url) == f"{BASE}/extensions"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"' in request.content and f"tayyari-guard-{digest}.zip".encode() in request.content
    assert data in request.content


async def test_stop_accepts_200_and_409() -> None:
    rec = Recorder(
        httpx2.Response(200, json={"id": "b-1", "status": "stopped"}), httpx2.Response(409, json={})
    )
    client = api(rec)
    await client.stop_browser("b-1")
    await client.stop_browser("b-1")
    assert [(r.method, r.url.path) for r in rec.requests] == [("PATCH", "/api/v4/browsers/b-1")] * 2
    assert json.loads(rec.requests[0].content) == {"action": "stop"}


async def test_429_5xx_and_resets_are_retried_with_back_off() -> None:
    sleeps: list[float] = []
    rec = Recorder(
        httpx2.Response(429, headers={"Retry-After": "3"}),
        httpx2.Response(503),
        httpx2.ConnectError("reset"),
        created(),
    )
    got = await api(rec, sleeps).create_browser(extension_ids=["e"], minutes=4, label="x")
    assert got.id == "b-1" and len(rec.requests) == 4
    assert sleeps[0] >= 3 and all(0 < s <= 10 for s in sleeps) and len(sleeps) == 3


async def test_429_after_the_retries_is_busy_and_other_errors_name_only_the_operation_and_status() -> None:
    rec = Recorder(*[httpx2.Response(429)] * 4)
    with pytest.raises(BrowserUseBusy) as busy:
        await api(rec, max_retries=3).create_browser(extension_ids=["e"], minutes=4, label="x")
    assert busy.value.status == 429 and len(rec.requests) == 4
    secret_body = {"detail": f"bad {FAKE_CDP_URL}", "key": KEY}
    rec = Recorder(httpx2.Response(400, json=secret_body))
    with pytest.raises(BrowserUseError) as failed:
        await api(rec).create_browser(extension_ids=["e"], minutes=4, label="x")
    text = str(failed.value) + repr(failed.value)
    assert "create_browser" in text and "400" in text
    assert (
        FAKE_CDP_URL not in text and KEY not in text and BASE not in text and failed.value.__cause__ is None
    )
    rec = Recorder(*[httpx2.ConnectError(f"cannot reach {BASE}")] * 4)
    with pytest.raises(BrowserUseError) as unreachable:
        await api(rec).stop_browser("b-1")
    assert BASE not in str(unreachable.value) and "ConnectError" in str(unreachable.value)


async def test_a_create_answer_without_a_cdp_address_stops_that_browser() -> None:
    rec = Recorder(httpx2.Response(201, json={"id": "b-7"}), httpx2.Response(200, json={}))
    with pytest.raises(BrowserUseError):
        await api(rec).create_browser(extension_ids=["e"], minutes=4, label="x")
    assert [(r.method, r.url.path) for r in rec.requests][-1] == ("PATCH", "/api/v4/browsers/b-7")


async def test_the_cost_is_read_once_the_browser_finished_and_parsed_to_micro_usd() -> None:
    rec = Recorder(
        httpx2.Response(200, json={"id": "b-1", "finishedAt": None, "browserCost": "0"}),
        httpx2.Response(200, json={"id": "b-1", "finishedAt": "2026-11-08T10:00:00Z", "browserCost": "0.0123",
                                   "proxyCost": "0.0007", "cdpUrl": FAKE_CDP_URL}),
    )  # fmt: skip
    assert await api(rec).browser_cost_micro_usd("b-1") == 13_000
    assert [r.method for r in rec.requests] == ["GET", "GET"]
    assert usd_to_micro("0.000001", None, "x", -1, True, 0.5) == 500_001


async def test_nothing_logged_or_repr_ed_holds_the_cdp_address_or_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = Recorder(httpx2.Response(429), created(), httpx2.Response(500), httpx2.Response(200, json={}))
    with captured_events(monkeypatch, "tayyari.apply.browser_use") as logs:
        client = api(rec)
        got = await client.create_browser(extension_ids=["e"], minutes=4, label="x")
        await client.stop_browser(got.id)
    assert logs, "the retries are logged"
    text = repr(logs) + repr(got) + repr(client)
    assert FAKE_CDP_URL not in text and "SECRET-cdp-token" not in text and KEY not in text
    assert repr(CreatedBrowser("b", FAKE_CDP_URL)) == "CreatedBrowser(id='b')"


def test_the_client_is_built_from_settings_only_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    assert HttpBrowserUseApi.from_settings(Settings(_env_file=None, env="test")) is None  # type: ignore[call-arg]
    monkeypatch.setenv("BROWSER_USE_API_KEY", KEY)
    client = HttpBrowserUseApi.from_settings(Settings(_env_file=None, env="test"))  # type: ignore[call-arg]
    assert client is not None and KEY not in repr(client)


# ── the opener ──


class Reservations:
    def __init__(self, refuse: bool = False) -> None:
        self.amounts: list[float] = []
        self.refuse = refuse

    def __call__(self, est_usd: float) -> AbstractAsyncContextManager[None]:
        @asynccontextmanager
        async def hold() -> AsyncIterator[None]:
            if self.refuse:
                raise SpendCeilingReached(60)
            self.amounts.append(est_usd)
            yield

        return hold()


def opener(
    bu: FakeBrowserUseApi, connect: FakeConnect, reserve: Reservations | None = None
) -> tuple[BrowserUseOpener, Reservations]:
    reservations = reserve or Reservations()
    settings = ApplySettings(fill_browser_minutes=7, submit_browser_minutes=4, est_browser_usd=0.01)
    return BrowserUseOpener(bu, settings, reservations, connect=connect), reservations


async def test_a_guarded_driver_only_after_a_blocked_probe_and_the_browser_is_always_stopped() -> None:
    bu, connect = FakeBrowserUseApi(), FakeConnect()
    open_, reservations = opener(bu, connect)
    driver = await open_.new_page()
    assert isinstance(driver, BrowserUseDriver) and reservations.amounts == [0.01]
    assert bu.created == [{"extension_ids": [f"ext-{bu.uploads[0]}"], "minutes": 7, "label": "apply-preview"}]
    assert connect.urls == [FAKE_CDP_URL]
    session = connect.sessions[0]
    assert session.page.visited == [PROBE_PAGE] and session.page.evaluated == [PROBE_URL]
    context = session.context
    assert await context.ask("POST", PROBE_URL) == "continue"  # only the extension may stop the probe
    assert await context.ask("POST", "https://boards.greenhouse.io/acme/jobs/1") == "abort"
    assert await context.ask("GET", "https://www.google.com/recaptcha/api.js") == "abort"
    assert await context.ask("GET", "http://169.254.169.254/latest/meta-data") == "abort"
    assert await context.ask("GET", "https://boards.greenhouse.io/acme/jobs/1") == "continue"
    assert bu.stopped == []
    await driver.close()
    await driver.close()  # closing twice stops once
    assert session.closed and bu.stopped == ["br-1"] and bu.cost_reads == ["br-1"]
    assert driver.cost_micro_usd == 12_000


async def test_the_extension_is_uploaded_once_per_variant() -> None:
    bu, connect = FakeBrowserUseApi(), FakeConnect()
    open_, _ = opener(bu, connect)
    for _ in range(2):
        await (await open_.new_page()).close()
    submit = await open_.new_page("submit", ["boards.greenhouse.io"])
    await (await open_.new_page("submit", {"boards.greenhouse.io"})).close()
    preview_digest, submit_digest = bu.uploads
    assert preview_digest == extension_zip()[1]
    assert submit_digest == extension_zip(allow_write_hosts=["boards.greenhouse.io"])[1]
    assert [c["minutes"] for c in bu.created] == [7, 7, 4, 4]
    assert (
        bu.created[2]["extension_ids"] == [f"ext-{submit_digest}"]
        and bu.created[2]["label"] == "apply-submit"
    )
    # the submit browser's route guard still blocks every write until the submit path lifts it
    context = connect.sessions[2].context
    assert await context.ask("POST", "https://boards.greenhouse.io/acme/jobs/1/applications") == "abort"
    submit.allow_submit_hosts(["boards.greenhouse.io"])
    assert await context.ask("POST", "https://boards.greenhouse.io/acme/jobs/1/applications") == "continue"
    assert await context.ask("POST", "https://www.google-analytics.com/collect") == "abort"
    await submit.close()


@pytest.mark.parametrize("answer", ["SENT", "ERROR:TypeError", "", "blocked"])
async def test_a_probe_that_is_not_blocked_raises_guard_not_active_and_stops_the_browser(answer: str) -> None:
    bu, connect = FakeBrowserUseApi(), FakeConnect(probe_answer=answer)
    open_, _ = opener(bu, connect)
    with pytest.raises(GuardNotActive) as raised:
        await open_.new_page()
    assert FAKE_CDP_URL not in str(raised.value)
    assert bu.stopped == ["br-1"] and connect.sessions[0].closed


async def test_a_probe_that_raises_counts_as_not_blocked() -> None:
    bu = FakeBrowserUseApi()

    async def connect(cdp_url: str) -> FakeCdpSession:
        session = FakeCdpSession()
        session.page.probe_raises = True
        return session

    open_ = BrowserUseOpener(bu, ApplySettings(), Reservations(), connect=connect)
    with pytest.raises(GuardNotActive):
        await open_.new_page()
    assert bu.stopped == ["br-1"]


async def test_a_failed_connection_stops_the_browser_and_withholds_the_address() -> None:
    bu, connect = FakeBrowserUseApi(), FakeConnect(fail=True)
    open_, _ = opener(bu, connect)
    with pytest.raises(RuntimeError) as raised:
        await open_.new_page()
    assert FAKE_CDP_URL not in str(raised.value) and bu.stopped == ["br-1"]


async def test_stop_is_sent_when_the_run_raises_and_when_closing_playwright_raises() -> None:
    bu = FakeBrowserUseApi()

    class BrokenSession(FakeCdpSession):
        async def close(self) -> None:
            raise RuntimeError("playwright went away")

    async def connect(cdp_url: str) -> FakeCdpSession:
        return BrokenSession()

    open_ = BrowserUseOpener(bu, ApplySettings(), Reservations(), connect=connect)
    driver = await open_.new_page()
    with pytest.raises(ValueError, match="the fill failed"):  # a close error never hides the run's own error
        try:
            raise ValueError("the fill failed")
        finally:
            await driver.close()
    assert bu.stopped == ["br-1"] and bu.cost_reads == ["br-1"]


async def test_a_stop_that_fails_is_logged_and_never_hides_the_run() -> None:
    bu, connect = FakeBrowserUseApi(fail_stop=True), FakeConnect()
    open_, _ = opener(bu, connect)
    driver = await open_.new_page()
    await driver.close()
    assert bu.stopped == ["br-1"]


async def test_a_refused_reservation_creates_nothing() -> None:
    bu, connect = FakeBrowserUseApi(), FakeConnect()
    open_, _ = opener(bu, connect, Reservations(refuse=True))
    with pytest.raises(SpendCeilingReached):
        await open_.new_page()
    assert bu.created == [] and bu.stopped == [] and connect.urls == []


async def test_a_failed_create_needs_no_stop_and_a_preview_never_takes_write_hosts() -> None:
    bu, connect = FakeBrowserUseApi(fail_create=True), FakeConnect()
    open_, _ = opener(bu, connect)
    with pytest.raises(BrowserUseError):
        await open_.new_page()
    assert bu.stopped == [] and connect.urls == []
    with pytest.raises(ValueError):
        await open_.new_page("preview", ["boards.greenhouse.io"])


async def test_the_opener_logs_no_cdp_address(monkeypatch: pytest.MonkeyPatch) -> None:
    bu, connect = FakeBrowserUseApi(), FakeConnect()
    with captured_events(monkeypatch, "tayyari.apply.browser_use") as logs:
        open_, _ = opener(bu, connect)
        await (await open_.new_page()).close()
        with pytest.raises(GuardNotActive):
            await opener(bu, FakeConnect(probe_answer="SENT"))[0].new_page()
    assert any(e["event"] == "apply_guard_probe_failed" for e in logs)
    assert FAKE_CDP_URL not in repr(logs) and "SECRET-cdp-token" not in repr(logs)


async def test_the_socket_guard_closes_every_socket_until_the_submit_hosts_are_lifted() -> None:
    bu, connect = FakeBrowserUseApi(), FakeConnect()
    open_, _ = opener(bu, connect)
    driver = await open_.new_page("submit", ["boards.greenhouse.io"])
    context = connect.sessions[0].context
    assert await context.open_socket("wss://ws.hotjar.com/api/v2/client/ws") == "close"
    assert await context.open_socket("wss://boards.greenhouse.io/live") == "close"
    driver.allow_submit_hosts(["boards.greenhouse.io"])
    assert await context.open_socket("wss://boards.greenhouse.io/live") == "connect"
    assert await context.open_socket("wss://ws.hotjar.com/api/v2/client/ws") == "close"
    with pytest.raises(ValueError):
        driver.allow_submit_hosts(["co.uk"])  # a bare suffix would lift writes for a whole domain
    await driver.close()


async def test_a_browser_that_fails_its_probe_still_reports_its_cost_and_drops_the_cached_extension() -> None:
    bu, connect = FakeBrowserUseApi(cost=3_000), FakeConnect(probe_answer="SENT")
    open_, _ = opener(bu, connect)
    with pytest.raises(GuardNotActive) as raised:
        await open_.new_page()
    assert raised.value.cost_micro_usd == 3_000 and bu.cost_reads == ["br-1"]
    with pytest.raises(GuardNotActive):
        await open_.new_page()
    assert len(bu.uploads) == 2  # uploaded again after the failed probe
