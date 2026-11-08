"""The worker's browser page: Playwright behind `driver.BrowserDriver`.

Every request the page makes goes through `_route`, layer 2 of the guard: only http(s) to public hosts (no localhost,
private or link-local addresses: a screenshot would otherwise leak internal pages), then `netguard.decide`, which blocks
CAPTCHA providers, main-frame navigation off the form's site, and every write until `allow_submit_hosts` (writes then go
to those hosts only). Layer 1, the guard extension, is loaded into the hosted browser by `apply.browser_use`.

`PlaywrightBrowser` launches a local Chromium: the opt-in offline test opener (``TAYYARI_TEST_BROWSER=1``). It has no
extension, so only layer 2 guards it. Playwright is imported lazily: the web app never loads it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Collection, Sequence
from typing import Any, Final
from urllib.parse import urlsplit

from tayyari.apply.driver import ClickOutcome, OpenMode, best_option, click_refused, shows
from tayyari.apply.netguard import (
    PROBE_JS,
    PROBE_PAGE,
    PROBE_URL,
    decide,
    norm_write_hosts,
    site_of,
    socket_decision,
)
from tayyari.apply.scan import SCAN_JS, LiveScan, parse_scan
from tayyari.core.logging import get_logger
from tayyari.core.settings import ApplySettings

log = get_logger(__name__)

Responder = Callable[[str, str], "tuple[int, str, str] | None"]
"""Test hook: ``(method, url) -> (status, content_type, body)`` to answer a request from memory (offline runs), or None."""

BUTTONS_JS: Final[str] = r"""() => {
  const txt = s => (s || '').replace(/\s+/g, ' ').trim();
  const seen = new Set(); const out = [];
  for (const b of document.querySelectorAll('button, [role=button], a, input[type=button]')) {
    const r = b.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const t = txt(b.innerText || b.value || b.getAttribute('aria-label')); if (!t || t.length > 40 || seen.has(t)) continue;
    seen.add(t); out.push(t); if (out.length >= 30) break; }
  return out; }"""
GROUP_PICK_JS: Final[str] = r"""([name, want]) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const inputs = [...document.querySelectorAll('input[name="' + CSS.escape(name) + '"]')];
  const lab = el => { if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                                   if (l) return l.innerText; }
                      const l = el.closest('label'); return l ? l.innerText : (el.value || ''); };
  let i = inputs.findIndex(el => norm(lab(el)) === norm(want));
  if (i < 0) i = inputs.findIndex(el => norm(lab(el)) && (norm(lab(el)).includes(norm(want)) || norm(want).includes(norm(lab(el)))));
  return i; }"""
SHOWN_VALUE_JS: Final[str] = r"""el => {
  const txt = s => (s || '').replace(/\s+/g, ' ').trim();
  let box = el.parentElement; let v = '';
  for (let i = 0; i < 5 && box && !v; i++, box = box.parentElement) {
    const sv = box.querySelector('[class*="single-value" i], [class*="singleValue" i], [class*="multi-value__label" i], [class*="multiValue" i] > div');
    if (sv && !sv.contains(el)) v = txt(sv.innerText); }
  return v || txt(el.value); }"""
OPTION_TEXTS_JS: Final[str] = (
    r"""el => [...(el.options || [])].map(o => (o.text || '').replace(/\s+/g, ' ').trim())"""
)
SUGGESTIONS: Final[str] = (
    '[role="option"]:visible, .dropdown-results li:visible, [class*="suggest" i] li:visible, '
    '[class*="autocomplete" i] li:visible, .pac-item:visible, [class*="dropdown" i] li:visible'
)
"""A typeahead's suggestions when they carry no ``role=option`` (Lever's location box)."""

_FORM_POLL_S: Final = 1.5
_CLICK_WAIT_S: Final = 4.0


def public_url(url: str) -> bool:
    if url.startswith(("data:", "blob:", "about:")):
        return True
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    from tayyari.ingest.web import is_private_host

    return not is_private_host(parts.hostname)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _main_frame_navigation(request: Any) -> bool:
    try:
        return bool(request.is_navigation_request()) and request.frame.parent_frame is None
    except Exception:  # a service worker's request has no frame
        return False


_CONTROL_JS: Final[str] = r"""el => {
  const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  const tag = el.tagName.toLowerCase(); const type = (el.getAttribute('type') || '').toLowerCase();
  const submits = (tag === 'input' && (type === 'submit' || type === 'image'))
    || (tag === 'button' && !!el.form && (type === '' || type === 'submit'));
  return { text, submits };
}"""
"""What `click_text` checks before a click: the control's own text (a submit input's ``value`` included) and whether it
would submit a form (an ``input[type=submit]``, or a button inside a form whose type is submit or missing)."""


class PlaywrightDriver:
    """One page (and its context) behind layer 2 of the guard. ``read_only`` are the playbook's extra read-only POST
    patterns (`netguard.read_only_post`)."""

    def __init__(
        self,
        page: Any,
        context: Any,
        settings: ApplySettings,
        responder: Responder | None = None,
        *,
        read_only: Sequence[str] = (),
    ) -> None:
        self.page, self._context, self._settings, self._responder = page, context, settings, responder
        self._read_only = tuple(read_only)
        self._write_hosts: frozenset[str] = frozenset()
        self._site: str | None = None
        self.blocked_requests = 0

    async def install_guard(self) -> None:
        """Route every request of the context (popups included) through `_route`, and every WebSocket through
        `_route_socket` (a context route never sees sockets, so a page script could otherwise stream typed values)."""
        await self._context.route("**/*", self._route)
        await self._context.route_web_socket("**/*", self._route_socket)

    async def _route_socket(self, ws: Any) -> None:
        url = str(ws.url)
        if socket_decision(url, write_hosts=self._write_hosts) == "allow":
            connected = ws.connect_to_server()
            if asyncio.iscoroutine(connected):
                await connected
            return
        self.blocked_requests += 1
        log.info("apply_socket_blocked", host=_host(url))
        await ws.close(code=1008, reason="blocked")

    async def _route(self, route: Any, request: Any) -> None:
        url, method = request.url, request.method.upper()
        host = _host(url)
        if self._responder is None and not public_url(url):
            await self._block(route, method, host)
            return
        verdict = decide(
            method, url, is_navigation=_main_frame_navigation(request), site=self._site, write_hosts=self._write_hosts,
            read_only=self._read_only,
        )  # fmt: skip
        if verdict == "block":
            await self._block(route, method, host)
            return
        if self._responder is not None:
            answer = self._responder(method, url)
            if answer is None:
                await route.abort("blockedbyclient")
                return
            status, content_type, body = answer
            await route.fulfill(status=status, content_type=content_type, body=body)
            return
        await route.continue_()

    async def _block(self, route: Any, method: str, host: str) -> None:
        self.blocked_requests += 1
        log.info("apply_request_blocked", method=method, host=host)  # the host only: a URL may carry a token
        await route.abort("blockedbyclient")

    def allow_submit_hosts(self, hosts: Collection[str]) -> None:
        """Checked as layer 1 checks its write hosts (`netguard.norm_write_hosts`): bare host names only, never a CAPTCHA
        provider, the probe host or a bare public suffix."""
        self._write_hosts = frozenset(norm_write_hosts([h for h in hosts if h.strip()]))

    @property
    def cost_micro_usd(self) -> int:
        return 0

    def _frame(self, index: int) -> Any:
        """The frame a field was scanned in. An index that no longer exists raises `LookupError`: falling back to the
        main frame could fill the wrong frame without any error."""
        if index == 0:
            return self.page.main_frame
        frames = self.page.frames
        if 0 < index < len(frames):
            return frames[index]
        raise LookupError("the field's frame is gone")

    # ── reading the page ──

    async def goto(self, url: str) -> int:
        if not public_url(url) and self._responder is None:
            raise PermissionError("not a public web address")
        self._site = site_of(url)
        response = await self.page.goto(
            url, wait_until="domcontentloaded", timeout=self._settings.navigation_timeout_s * 1000
        )
        with contextlib.suppress(Exception):  # a page that keeps polling never goes idle; the DOM is enough
            await self.page.wait_for_load_state("networkidle", timeout=5000)
        return int(response.status) if response is not None else 0

    async def content(self) -> str:
        return str(await self.page.content())

    async def current_url(self) -> str:
        return str(self.page.url)

    async def visible_text(self) -> str:
        return str(await self.page.inner_text("body", timeout=5000))

    async def exists(self, selector: str, frame: int = 0) -> bool:
        return bool(await self._frame(frame).locator(selector).count())

    async def scan(self, resume_name: str = "") -> LiveScan:
        results: list[dict[str, Any]] = []
        for index, frame in enumerate(self.page.frames):
            if index != 0 and not str(frame.url or "").startswith("http"):
                continue
            try:
                got = await frame.evaluate(SCAN_JS, resume_name)
            except Exception as exc:  # a frame that navigated away or was detached mid-scan
                log.info("apply_frame_unread", frame=index, error=type(exc).__name__)
                continue
            if isinstance(got, dict):
                results.append({**got, "frame": index})
        if not results or results[0]["frame"] != 0:
            results.insert(0, {"url": str(self.page.url), "title": "", "fields": [], "frame": 0})
        return parse_scan(results, resume_name=resume_name)

    async def wait_for_form(self, seconds: float, resume_name: str = "") -> LiveScan:
        deadline = asyncio.get_running_loop().time() + seconds
        found = await self.scan(resume_name)
        while not found.has_form and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_FORM_POLL_S)
            found = await self.scan(resume_name)
        return found

    async def buttons(self) -> list[str]:
        got = await self.page.evaluate(BUTTONS_JS)
        return [str(t) for t in got if isinstance(t, str)] if isinstance(got, list) else []

    async def probe(self) -> str:
        try:
            if not str(self.page.url).startswith(
                "https://"
            ):  # about:blank, chrome://newtab, a vendor start page
                await self.page.goto(PROBE_PAGE, wait_until="domcontentloaded", timeout=30_000)
            return str(await self.page.evaluate(PROBE_JS, PROBE_URL))
        except Exception as exc:  # the page may be navigating or closed
            return f"ERROR:{type(exc).__name__}"

    # ── acting on the page ──

    async def click_submit(self, selector: str, frame: int = 0) -> None:
        if not self._write_hosts:
            raise PermissionError("the submit control is clicked only after allow_submit_hosts")
        await self._frame(frame).locator(selector).first.click(timeout=10000)

    async def fill(self, selector: str, value: str, frame: int = 0) -> None:
        await self._frame(frame).locator(selector).first.fill(value, timeout=10000)

    async def select_label(self, selector: str, text: str, frame: int = 0) -> None:
        loc = self._frame(frame).locator(selector).first
        with contextlib.suppress(Exception):  # not the exact text: fall back to the closest option
            await loc.select_option(label=text, timeout=5000)
            return
        texts = await loc.evaluate(OPTION_TEXTS_JS)
        index = best_option([str(t) for t in texts] if isinstance(texts, list) else [], text)
        if index is None:
            raise LookupError("no option fits")
        await loc.select_option(index=index, timeout=5000)

    async def check(self, selector: str, frame: int = 0) -> None:
        await self._frame(frame).locator(selector).first.check(force=True, timeout=8000)

    async def pick_in_group(self, name: str, text: str, frame: int = 0) -> None:
        target = self._frame(frame)
        index = await target.evaluate(GROUP_PICK_JS, [name, text])
        if not isinstance(index, int) or index < 0:
            raise LookupError("no option fits")
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        await target.locator(f'input[name="{escaped}"]').nth(index).check(force=True, timeout=8000)

    async def choose(self, selector: str, text: str, frame: int = 0) -> None:
        target = self._frame(frame)
        loc = target.locator(selector).first
        for attempt in range(2):
            listed = await self._type_and_pick(target, loc, text, enter=attempt == 1)
            if not listed:
                return  # a free-text typeahead: the typed text stays
            shown = str(await loc.evaluate(SHOWN_VALUE_JS))
            if shows(shown, text):
                return
        raise LookupError("the pick did not stick")

    async def _options(self, target: Any, loc: Any) -> Any:
        ctrl = await loc.get_attribute("aria-controls") or ""
        escaped = ctrl.replace("\\", "\\\\").replace('"', '\\"')  # the page's own text, never a selector
        return (
            target.locator(f'[id="{escaped}"] [role="option"]')
            if ctrl
            else target.locator('[role="option"]:visible')
        )

    async def _type_and_pick(self, target: Any, loc: Any, text: str, *, enter: bool) -> bool:
        """Type ``text`` and pick its option (by click; by Enter on the retry). False when the box lists no options."""
        keyboard = self.page.keyboard
        await loc.focus(timeout=8000)
        await keyboard.press("Control+A")
        await keyboard.type(text[:30], delay=25)
        await self.page.wait_for_timeout(700)
        options = await self._options(target, loc)
        if not await options.count():
            await keyboard.press("ArrowDown")
            await self.page.wait_for_timeout(500)
            options = await self._options(target, loc)
        if not await options.count():
            options = target.locator(SUGGESTIONS)
            if not await options.count():
                return False
        texts = [" ".join(t.split()) for t in (await options.all_inner_texts())[:300]]
        pick = best_option(texts, text)
        if pick is None:
            await keyboard.press("Escape")
            raise LookupError("no option fits")
        if enter:
            await keyboard.press("Enter")
        else:
            await options.nth(pick).click(timeout=5000)
        await self.page.wait_for_timeout(300)
        return True

    async def upload(self, selector: str, name: str, data: bytes, media_type: str, frame: int = 0) -> None:
        await (
            self._frame(frame)
            .locator(selector)
            .first.set_input_files(
                files=[{"name": name, "mimeType": media_type, "buffer": data}], timeout=10000
            )
        )

    async def click_text(self, text: str, *, form_on_screen: bool) -> ClickOutcome:
        if click_refused(text, form_on_screen=form_on_screen):
            log.info("apply_click_refused", form_on_screen=form_on_screen)
            return "refused"
        page = self.page
        getters: tuple[Callable[[], Any], ...] = (
            lambda: page.get_by_role("button", name=text, exact=True),
            lambda: page.get_by_role("link", name=text, exact=True),
            lambda: page.get_by_text(text, exact=False),
        )
        deadline = asyncio.get_running_loop().time() + _CLICK_WAIT_S
        while True:
            for getter in getters:
                try:
                    loc = getter()
                    if not await loc.count():
                        continue
                    own = await loc.first.evaluate(_CONTROL_JS)
                    own_text = str(own.get("text", "")) if isinstance(own, dict) else ""
                    if (isinstance(own, dict) and own.get("submits")) or click_refused(
                        own_text, form_on_screen=form_on_screen
                    ):
                        continue  # the text matched a submit control or a submit-like text: never click it
                    await loc.first.click(timeout=5000)
                    await page.wait_for_timeout(1200)
                    return "clicked"
                except Exception as exc:  # detached or covered: try the next way of finding it
                    log.info("apply_click_retry", error=type(exc).__name__)
                    continue
            if asyncio.get_running_loop().time() >= deadline:
                return "not_found"
            await asyncio.sleep(0.5)  # a button that appears late (a cookie banner, a slow script)

    async def screenshot(self) -> bytes:
        return bytes(await self.page.screenshot(full_page=True, type="jpeg", quality=70))

    async def close(self) -> None:
        await self._context.close()


class PlaywrightBrowser:
    """One local Chromium for the process's life; a fresh context (no cookies, no storage) per page. The opt-in offline
    test opener: it loads no guard extension and runs no probe, so only layer 2 guards it."""

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

    async def new_page(
        self, mode: OpenMode = "preview", write_hosts: Collection[str] = ()
    ) -> PlaywrightDriver:
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


__all__ = [
    "BUTTONS_JS",
    "SUGGESTIONS",
    "PlaywrightBrowser",
    "PlaywrightDriver",
    "Responder",
    "public_url",
]
