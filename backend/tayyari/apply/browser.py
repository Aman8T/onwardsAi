"""The worker's browser: Playwright Chromium behind `BrowserDriver` (the playbook runner) and `AgentBrowser` (computer use).

Guards on every request the page makes (``page.route``): only http(s) to public hosts (no localhost, private or link-local
addresses: the screenshot would otherwise leak internal pages), and nothing but GET/HEAD/OPTIONS until `allow_submit(True)`,
which only the approved submit path calls. Run the worker in a network-isolated container all the same (DNS rebinding is not
covered by a host check). Playwright is imported lazily: the web app never loads it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from tayyari.core.logging import get_logger
from tayyari.core.settings import ApplySettings

log = get_logger(__name__)

Responder = Callable[[str, str], "tuple[int, str, str] | None"]
"""Test hook: ``(method, url) -> (status, content_type, body)`` to answer a request from memory (offline runs), or None."""

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def public_url(url: str) -> bool:
    if url.startswith(("data:", "blob:", "about:")):
        return True
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    from tayyari.ingest.web import is_private_host

    return not is_private_host(parts.hostname)


@dataclass(frozen=True, slots=True)
class ElementInfo:
    tag: str
    id: str | None = None
    name: str | None = None
    type: str | None = None
    label: str | None = None
    text: str | None = None
    selector: str | None = None
    in_form: bool = False

    @property
    def is_submit(self) -> bool:
        if self.tag in ("button", "input") and (self.type or "submit") == "submit" and self.in_form:
            return True
        words = f"{self.text or ''} {self.label or ''}".lower()
        return self.tag in ("button", "input", "a") and any(
            w in words for w in ("submit", "send application", "apply now", "finish application")
        )


_ELEMENT_AT = """([x, y]) => {
  const el = document.elementFromPoint(x, y); if (!el) return null;
  const t = el.closest('button, input, select, textarea, a, label') || el;
  const lab = t.id ? document.querySelector('label[for="' + CSS.escape(t.id) + '"]') : t.closest('label');
  const sel = t.id ? '[id="' + t.id + '"]' : (t.getAttribute('name') ? '[name="' + t.getAttribute('name') + '"]' : null);
  return {tag: t.tagName.toLowerCase(), id: t.id || null, name: t.getAttribute('name'), type: t.getAttribute('type'),
          label: lab ? lab.innerText.trim().slice(0, 200) : (t.getAttribute('aria-label') || t.getAttribute('placeholder')),
          text: (t.innerText || t.value || '').trim().slice(0, 120), selector: sel, in_form: !!t.closest('form')};
}"""


class PlaywrightDriver:
    def __init__(self, page: Any, context: Any, settings: ApplySettings, responder: Responder | None) -> None:
        self.page, self._context, self._settings, self._responder = page, context, settings, responder
        self._submit_allowed = False
        self.blocked_requests = 0

    async def _route(self, route: Any, request: Any) -> None:
        url, method = request.url, request.method.upper()
        if not public_url(url) and self._responder is None:
            self.blocked_requests += 1
            await route.abort()
            return
        if method not in _SAFE_METHODS and not self._submit_allowed:
            self.blocked_requests += 1
            log.info("apply_request_blocked", method=method, host=urlsplit(url).hostname)
            await route.abort()
            return
        if self._responder is not None:
            answer = self._responder(method, url)
            if answer is None:
                await route.abort()
                return
            status, content_type, body = answer
            await route.fulfill(status=status, content_type=content_type, body=body)
            return
        await route.continue_()

    def allow_submit(self, allowed: bool) -> None:
        self._submit_allowed = allowed

    # ── BrowserDriver ──

    async def goto(self, url: str) -> None:
        if not public_url(url) and self._responder is None:
            raise PermissionError("not a public web address")
        await self.page.goto(
            url, wait_until="domcontentloaded", timeout=self._settings.navigation_timeout_s * 1000
        )
        with contextlib.suppress(Exception):  # a page that keeps polling never goes idle; the DOM is enough
            await self.page.wait_for_load_state("networkidle", timeout=5000)

    async def content(self) -> str:
        return str(await self.page.content())

    async def current_url(self) -> str:
        return str(self.page.url)

    async def visible_text(self) -> str:
        return str(await self.page.inner_text("body", timeout=5000))

    async def exists(self, selector: str) -> bool:
        return bool(await self.page.locator(selector).count())

    async def click(self, selector: str) -> None:
        await self.page.locator(selector).first.click(timeout=10000)

    async def fill(self, selector: str, value: str) -> None:
        await self.page.locator(selector).first.fill(value, timeout=10000)

    async def select(self, selector: str, value: str) -> None:
        await self.page.locator(selector).first.select_option(value=value, timeout=10000)

    async def check(self, selector: str) -> None:
        await self.page.locator(selector).first.check(timeout=10000)

    async def upload(self, selector: str, name: str, data: bytes, media_type: str) -> None:
        await self.page.locator(selector).first.set_input_files(
            files=[{"name": name, "mimeType": media_type, "buffer": data}], timeout=10000
        )

    async def screenshot(self) -> bytes:
        return bytes(await self.page.screenshot(full_page=True, type="jpeg", quality=70))

    # ── AgentBrowser (computer use) ──

    async def screenshot_png(self) -> bytes:
        return bytes(await self.page.screenshot(type="png"))

    def viewport(self) -> tuple[int, int]:
        return self._settings.viewport_width, self._settings.viewport_height

    async def element_at(self, x: int, y: int) -> ElementInfo | None:
        got = await self.page.evaluate(_ELEMENT_AT, [x, y])
        return None if not got else ElementInfo(**got)

    async def click_at(self, x: int, y: int) -> None:
        await self.page.mouse.click(x, y)

    async def type_text(self, text: str) -> None:
        await self.page.keyboard.type(text, delay=10)

    async def press(self, keys: str) -> None:
        await self.page.keyboard.press(keys)

    async def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        await self.page.mouse.move(x, y)
        await self.page.mouse.wheel(dx, dy)

    async def wait(self, seconds: float) -> None:
        await asyncio.sleep(min(seconds, 10))

    async def close(self) -> None:
        await self._context.close()


class PlaywrightBrowser:
    """One Chromium for the worker's life; a fresh context (no cookies, no storage) per application run."""

    def __init__(self, settings: ApplySettings, responder: Responder | None = None) -> None:
        self._settings, self._responder = settings, responder
        self._pw: Any = None
        self._browser: Any = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._settings.headless, executable_path=self._settings.chromium_path or None
        )

    async def new_page(self) -> PlaywrightDriver:
        if self._browser is None:
            await self.start()
        context = await self._browser.new_context(
            viewport={"width": self._settings.viewport_width, "height": self._settings.viewport_height},
            accept_downloads=False,
            service_workers="block",
        )
        page = await context.new_page()
        driver = PlaywrightDriver(page, context, self._settings, self._responder)
        await page.route("**/*", driver._route)
        return driver

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None


__all__ = ["ElementInfo", "PlaywrightBrowser", "PlaywrightDriver", "Responder", "public_url"]
