"""Browser Use Cloud standalone browsers for the apply worker: raw REST over ``httpx2`` (API v4), no SDK.

`HttpBrowserUseApi` speaks the four calls the worker needs, in the shapes the SDK (browser-use-sdk 3.12.1, read, not
installed) sends them:

    POST  /extensions          multipart, the ZIP in the ``file`` part        -> {"id", "name", "version", "createdAt"}
    POST  /browsers            {"proxyCountryCode": null, "solveCaptchas": false, "extensionIds": [...],
                                "timeout": <minutes>, "metadata": {...}}     -> {"id", "cdpUrl", "liveUrl", ...}
    PATCH /browsers/{id}       {"action": "stop"}                             200, or 409 when it already stopped
    GET   /browsers/{id}       {"finishedAt", "browserCost", "proxyCost", ...} (USD as decimal strings)

Every call is retried with back-off on 429, 5xx and a dropped connection. Closing Playwright does not stop a standalone
browser or its billing, so `BrowserUseOpener` always stops it: when opening fails, and in the driver's `close`.

The CDP URL and the live-view URL are credentials: `CreatedBrowser.cdp_url` is ``repr=False``, the live-view URL is never
read, nothing here logs a URL or a response body, and errors carry the operation and the HTTP status only (scrubbed
through `core.redact` all the same). The key is sent in the ``X-Browser-Use-API-Key`` header and never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Protocol

import httpx2
from pydantic import SecretStr

from tayyari.apply.browser import PlaywrightDriver
from tayyari.apply.driver import PROBE_BLOCKED, GuardNotActive, OpenMode, carry_open_cost
from tayyari.apply.netguard import extension_zip
from tayyari.apply.seams import Reserve
from tayyari.core.logging import get_logger
from tayyari.core.redact import redact_pii
from tayyari.core.settings import ApplySettings, Settings

log = get_logger(__name__)

API_KEY_HEADER: Final = "X-Browser-Use-API-Key"
_RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_DELAY_S: Final = 10.0
_LABEL_MAX: Final = 40
Sleep = Callable[[float], Awaitable[None]]


class BrowserUseError(RuntimeError):
    """A Browser Use call failed. The message names the operation and the HTTP status, never a URL or a body."""

    def __init__(self, op: str, status: int | None, detail: str = "") -> None:
        self.op, self.status = op, status
        text = f"Browser Use {op} answered {status}" if status is not None else f"Browser Use {op} failed"
        super().__init__(redact_pii(f"{text}{f' ({detail})' if detail else ''}"))


class BrowserUseBusy(BrowserUseError):
    """Browser Use still answered 429 after the retries (too many browsers): the worker backs off before claiming."""


@dataclass(frozen=True, slots=True)
class CreatedBrowser:
    id: str
    cdp_url: str = field(repr=False)
    """A credential: never logged, stored or shown."""


class BrowserUseApi(Protocol):
    async def upload_extension(self, data: bytes, digest: str) -> str:
        """Upload a Manifest V3 extension ZIP; its id."""
        ...

    async def create_browser(self, *, extension_ids: list[str], minutes: int, label: str) -> CreatedBrowser:
        """A standalone browser with these extensions, no CAPTCHA solving and no managed proxy, that stops by itself
        after ``minutes``."""
        ...

    async def stop_browser(self, browser_id: str) -> None:
        """Stop the browser (ends billing, refunds unused time). An already stopped browser is fine."""
        ...

    async def browser_cost_micro_usd(self, browser_id: str) -> int:
        """What a stopped browser cost (browser plus proxy), in micro-USD."""
        ...


def usd_to_micro(*values: Any) -> int:
    """The sum of USD amounts (Browser Use sends decimal strings such as ``"0.0123"``) in whole micro-USD; anything that
    is not a number counts as 0."""
    total = Decimal(0)
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if amount.is_finite() and amount > 0:
            total += amount
    return int((total * 1_000_000).to_integral_value())


class HttpBrowserUseApi:
    """`BrowserUseApi` over ``httpx2``. ``transport`` (an ``httpx2.MockTransport`` in tests) reaches every call."""

    def __init__(
        self,
        api_key: SecretStr,
        base_url: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        max_retries: int = 3,
        cost_polls: int = 5,
        cost_poll_s: float = 2.0,
        timeout_s: float = 60.0,
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep
        self._max_retries = max_retries
        self._cost_polls, self._cost_poll_s = cost_polls, cost_poll_s
        self._timeout = timeout_s

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> HttpBrowserUseApi | None:
        """The client for ``BROWSER_USE_API_KEY`` and ``TAYYARI_APPLY__BROWSER_USE_BASE_URL``; None without a key."""
        key = settings.browser_use_api_key
        return None if key is None else cls(key, settings.apply.browser_use_base_url, **kwargs)

    def __repr__(self) -> str:
        return "HttpBrowserUseApi()"

    async def _request(
        self, op: str, method: str, path: str, *, ok: Collection[int] = (), **kwargs: Any
    ) -> Any:
        """One call with retries; the decoded JSON body (None for an empty one). Never logs or raises with a URL."""
        headers = {API_KEY_HEADER: self._key.get_secret_value()}
        attempt = 0
        async with httpx2.AsyncClient(
            base_url=self._base, transport=self._transport, timeout=self._timeout, headers=headers
        ) as client:
            while True:
                try:
                    response = await client.request(method, path, **kwargs)
                except httpx2.TransportError as err:
                    if attempt >= self._max_retries:
                        raise BrowserUseError(op, None, type(err).__name__) from None
                    await self._back_off(op, attempt, None, None)
                    attempt += 1
                    continue
                status = response.status_code
                if status < 300 or status in ok:
                    if not response.content:
                        return None
                    try:
                        return response.json()
                    except ValueError:
                        raise BrowserUseError(op, status, "not JSON") from None
                if status in _RETRY_STATUSES and attempt < self._max_retries:
                    await self._back_off(op, attempt, status, response.headers.get("Retry-After"))
                    attempt += 1
                    continue
                if status == 429:
                    raise BrowserUseBusy(op, status)
                raise BrowserUseError(op, status)

    async def _back_off(self, op: str, attempt: int, status: int | None, retry_after: str | None) -> None:
        delay = min(_MAX_RETRY_DELAY_S, 0.5 * 2 ** (attempt + 1))
        if retry_after and retry_after.strip().isdigit():
            delay = min(_MAX_RETRY_DELAY_S, max(delay, float(retry_after.strip())))
        log.info("browser_use_retry", op=op, status=status, attempt=attempt + 1)
        await self._sleep(delay)

    async def upload_extension(self, data: bytes, digest: str) -> str:
        body = await self._request(
            "upload_extension", "POST", "/extensions",
            files={"file": (f"tayyari-guard-{digest}.zip", data, "application/zip")},
        )  # fmt: skip
        ext_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(ext_id, str) or not ext_id:
            raise BrowserUseError("upload_extension", None, "no extension id")
        return ext_id

    async def create_browser(self, *, extension_ids: list[str], minutes: int, label: str) -> CreatedBrowser:
        payload = {
            "proxyCountryCode": None,
            "solveCaptchas": False,
            "extensionIds": list(extension_ids),
            "timeout": minutes,
            "metadata": {"app": "tayyari", "label": label[:_LABEL_MAX]},
        }
        body = await self._request("create_browser", "POST", "/browsers", json=payload)
        browser_id = body.get("id") if isinstance(body, dict) else None
        cdp_url = body.get("cdpUrl") if isinstance(body, dict) else None
        if not isinstance(browser_id, str) or not browser_id:
            raise BrowserUseError("create_browser", None, "no browser id")
        if not isinstance(cdp_url, str) or not cdp_url:
            with contextlib.suppress(BrowserUseError):
                await self.stop_browser(browser_id)
            raise BrowserUseError("create_browser", None, "no CDP address")
        return CreatedBrowser(browser_id, cdp_url)

    async def stop_browser(self, browser_id: str) -> None:
        await self._request(
            "stop_browser", "PATCH", f"/browsers/{browser_id}", ok=(409,), json={"action": "stop"}
        )

    async def browser_cost_micro_usd(self, browser_id: str) -> int:
        view: Any = None
        for poll in range(self._cost_polls):
            view = await self._request("browser_cost", "GET", f"/browsers/{browser_id}")
            if isinstance(view, dict) and view.get("finishedAt"):
                break  # the final numbers are in once the browser shows when it finished
            if poll + 1 < self._cost_polls:
                await self._sleep(self._cost_poll_s)
        if not isinstance(view, dict):
            return 0
        return usd_to_micro(view.get("browserCost"), view.get("proxyCost"))


# ── the opener ──


class CdpSession(Protocol):
    """A Playwright connection to a hosted browser."""

    @property
    def context(self) -> Any:
        """The browser's default context (``browser.contexts[0]``), where the extension runs."""
        ...

    async def close(self) -> None: ...


Connect = Callable[[str], Awaitable[CdpSession]]
"""``connect(cdp_url)``: a Playwright session over CDP. Raises without the URL in its message."""


@dataclass(slots=True)
class _PlaywrightSession:
    playwright: Any
    browser: Any
    context: Any

    async def close(self) -> None:
        try:
            with contextlib.suppress(Exception):
                await self.browser.close()
        finally:
            with contextlib.suppress(Exception):
                await self.playwright.stop()


async def connect_over_cdp(cdp_url: str) -> CdpSession:
    """Playwright (imported here, so the web app never loads it) over CDP; no browser binaries are needed."""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=60_000)
    except Exception:
        with contextlib.suppress(Exception):
            await pw.stop()
        raise RuntimeError("CDP connection failed (address withheld)") from None
    contexts = browser.contexts
    if not contexts:
        session = _PlaywrightSession(pw, browser, None)
        await session.close()
        raise RuntimeError("the hosted browser has no context")
    return _PlaywrightSession(pw, browser, contexts[0])


class BrowserUseDriver(PlaywrightDriver):
    """A `PlaywrightDriver` on a Browser Use browser: `close` closes Playwright, then always stops the browser and reads
    what it cost (`cost_micro_usd`)."""

    def __init__(
        self, page: Any, session: CdpSession, api: BrowserUseApi, browser_id: str, settings: ApplySettings
    ) -> None:
        super().__init__(page, session.context, settings)
        self._session, self._api, self._browser_id = session, api, browser_id
        self._cost = 0
        self._closed = False

    @property
    def cost_micro_usd(self) -> int:
        return self._cost

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._session.close()
        except Exception as exc:  # never hides the run's own error when close runs in a finally
            log.warning("browser_use_session_close_failed", error=type(exc).__name__)
        finally:
            await _stop(self._api, self._browser_id)
            self._cost = await _cost(self._api, self._browser_id)


async def _cost(api: BrowserUseApi, browser_id: str) -> int:
    """What a stopped browser cost, best effort (0 when it cannot be read; the failure is logged by type only)."""
    try:
        return await api.browser_cost_micro_usd(browser_id)
    except Exception as exc:
        log.warning("browser_use_cost_unread", error=type(exc).__name__)
        return 0


async def _stop(api: BrowserUseApi, browser_id: str) -> None:
    """Stop a browser; a failure is logged (it stops by itself at its timeout) and never hides the original error."""
    try:
        await api.stop_browser(browser_id)
    except Exception as exc:
        log.warning("browser_use_stop_failed", error=type(exc).__name__)


class BrowserUseOpener:
    """`driver.PageOpener` over Browser Use: one fresh standalone browser per page, guarded by both layers.

    `new_page` reserves ``est_browser_usd`` while it creates the browser; uploads (or reuses, by content hash) the guard
    extension of the mode; connects over CDP; installs the route guard on the default context; and runs the probe. The
    driver comes back only when the probe was ``'BLOCKED'``; on any failure the browser is stopped before the error is
    raised (`GuardNotActive` for a probe that was not blocked)."""

    def __init__(
        self,
        api: BrowserUseApi,
        settings: ApplySettings,
        reserve: Reserve,
        *,
        connect: Connect = connect_over_cdp,
    ) -> None:
        self._api, self._settings, self._reserve, self._connect = api, settings, reserve, connect
        self._extensions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def _extension(self, write_hosts: Collection[str]) -> str:
        data, digest = extension_zip(allow_write_hosts=sorted(write_hosts))
        async with self._lock:
            known = self._extensions.get(digest)
            if known is None:
                known = await self._api.upload_extension(data, digest)
                self._extensions[digest] = known
                log.info("browser_use_extension_uploaded", digest=digest)
            return known

    async def new_page(
        self, mode: OpenMode = "preview", write_hosts: Collection[str] = ()
    ) -> BrowserUseDriver:
        if mode == "preview" and write_hosts:
            raise ValueError("a preview browser never allows writes")
        minutes = (
            self._settings.fill_browser_minutes
            if mode == "preview"
            else self._settings.submit_browser_minutes
        )
        created: CreatedBrowser | None = None
        session: CdpSession | None = None
        try:
            async with self._reserve(self._settings.est_browser_usd):
                ext_id = await self._extension(write_hosts if mode == "submit" else ())
                created = await self._api.create_browser(
                    extension_ids=[ext_id], minutes=minutes, label=f"apply-{mode}"
                )
            session = await self._connect(created.cdp_url)
            context = session.context
            pages = list(context.pages)
            page = pages[0] if pages else await context.new_page()
            driver = BrowserUseDriver(page, session, self._api, created.id, self._settings)
            await driver.install_guard()
            verdict = await driver.probe()
            if verdict != PROBE_BLOCKED:
                log.warning("apply_guard_probe_failed", mode=mode, verdict=verdict[:40])
                raise GuardNotActive("the guard probe was not blocked")
        except BaseException as exc:
            try:
                if session is not None:
                    with contextlib.suppress(Exception):
                        await session.close()
            finally:
                if created is not None:
                    await _stop(self._api, created.id)
                    # a browser that failed to open is still billed: its cost is logged and rides on the error, whatever
                    # it is, so the engine records it as spend (`driver.open_cost`)
                    cost = await _cost(self._api, created.id)
                    log.info(
                        "apply_browser_open_failed", mode=mode, cost_micro_usd=cost, error=type(exc).__name__
                    )
                    carry_open_cost(exc, cost)
                    if isinstance(exc, GuardNotActive):
                        self._extensions.clear()  # a probe that was not blocked: upload the extension again next time
            raise
        log.info("apply_browser_ready", mode=mode)
        return driver


__all__ = [
    "API_KEY_HEADER",
    "BrowserUseApi",
    "BrowserUseBusy",
    "BrowserUseDriver",
    "BrowserUseError",
    "BrowserUseOpener",
    "CdpSession",
    "Connect",
    "CreatedBrowser",
    "HttpBrowserUseApi",
    "connect_over_cdp",
    "usd_to_micro",
]
