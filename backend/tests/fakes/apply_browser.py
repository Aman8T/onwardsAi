"""A `BrowserDriver` over saved HTML or a saved live scan, with the real driver's guard decisions.

`FakeDriver` records every action, refuses selectors the page does not have, keeps the current value of every input, and
logs every request the page tries as ``(method, url)``. Each request is put to `netguard.decide`, as the real route guard
does: a write passes only after `allow_submit_hosts` names its host, so the "submitted" page is served only after a
permitted submit click (`click_submit`, which refuses to run before the hosts are lifted). `click_text` refuses submit-like texts exactly like the
real driver and records the refusals.

Pages come from saved HTML (``scan()`` then reads them with `forms.scan_form`) or from a saved `SCAN_JS` result
(``tests/data/apply/scans/*.json``, parsed with `scan.parse_scan`). ``reveals`` maps a button text to the page that
clicking it opens (a form behind "Apply for this job").

For the submit pass: ``after_submit`` (HTML or a scan) and ``after_submit_url`` script what the click leads to (a thank-you
page, an error, a code prompt, nothing); ``raise_after_click`` makes the click go through and then raise, as a dropped
connection would; ``resume_shows=False`` is a widget that never shows the attached file's name. ``page_posts`` are URLs
the page's own scripts POST to on every fill step (analytics, autosave), which the guard must stop. ``allow_calls``
records every `allow_submit_hosts` call.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from tayyari.apply.driver import (
    PROBE_BLOCKED,
    ClickOutcome,
    GuardNotActive,
    OpenMode,
    best_option,
    click_refused,
)
from tayyari.apply.forms import FormField, scan_form
from tayyari.apply.netguard import READ_METHODS, decide, norm_write_hosts, site_of
from tayyari.apply.scan import LiveScan, ScanBlockers, parse_scan, resume_name_inferable

THANKS = (
    "<html><body><h1>Thank you for applying!</h1><p>Your application has been submitted.</p></body></html>"
)

Page = str | Mapping[str, Any]
"""Saved HTML, or a saved live scan (a `SCAN_JS` result)."""

_BUTTON = re.compile(r"<(?:button|a)\b[^>]*>(.*?)</(?:button|a)>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_PASSWORD = re.compile(r"<input[^>]+type=[\"']password[\"']", re.I)
_CAPTCHA = re.compile(r"g-recaptcha|h-captcha|cf-turnstile|recaptcha/api|hcaptcha\.com", re.I)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class FakeDriver:
    def __init__(
        self,
        page: Page,
        *,
        after_submit: Page = THANKS,
        url: str = "https://example.com/apply",
        buttons: Sequence[str] | None = None,
        reveals: Mapping[str, Page] | None = None,
        status: int = 200,
        probe_result: str = PROBE_BLOCKED,
        cost_micro_usd: int = 0,
        choose_sticks: bool = True,
        upload_url: str | None = None,
        after_submit_url: str | None = None,
        raise_after_click: BaseException | None = None,
        resume_shows: bool = True,
        page_posts: Sequence[str] = (),
    ) -> None:
        self.page: str = page if isinstance(page, str) else ""
        self.scan_data: Mapping[str, Any] | None = None if isinstance(page, str) else page
        self.after_submit, self.url = after_submit, url
        self.status, self.probe_result, self.choose_sticks = status, probe_result, choose_sticks
        self.upload_url = upload_url
        self.after_submit_url, self.raise_after_click = after_submit_url, raise_after_click
        self.resume_shows, self.page_posts = resume_shows, tuple(page_posts)
        self.allow_calls: list[frozenset[str]] = []
        """When set, attaching a file POSTs to it at once (Greenhouse and Ashby upload on attach)."""
        self._buttons = list(buttons) if buttons is not None else None
        self._reveals = dict(reveals or {})
        self._cost = cost_micro_usd
        self._site: str | None = None
        self.actions: list[tuple[str, str, str]] = []
        self.values: dict[str, str] = {}
        """The current value of every input the fake changed, by selector (a group by its ``[name="..."]``)."""
        self.requests: list[tuple[str, str]] = []
        """Every request the page tried, ``(method, url)``."""
        self.blocked: list[tuple[str, str]] = []
        """The requests the guard stopped."""
        self.passed: list[tuple[str, str]] = []
        """The requests the guard let through."""
        self.refusals: list[str] = []
        self.clicked_texts: list[str] = []
        self.write_hosts: frozenset[str] = frozenset()
        self.submitted = False
        self.blocked_submits = 0
        self.closed = False

    # ── the guard ──

    def _request(self, method: str, url: str, *, navigation: bool = False) -> bool:
        self.requests.append((method, url))
        if (
            decide(method, url, is_navigation=navigation, site=self._site, write_hosts=self.write_hosts)
            == "allow"
        ):
            self.passed.append((method, url))
            return True
        self.blocked.append((method, url))
        return False

    def sent_writes(self) -> list[tuple[str, str]]:
        """The writes that got past the guard (empty for any preview fill)."""
        return [r for r in self.passed if r[0] not in READ_METHODS]

    def allow_submit_hosts(self, hosts: Collection[str]) -> None:
        self.write_hosts = frozenset(norm_write_hosts([h for h in hosts if h.strip()]))
        self.allow_calls.append(self.write_hosts)

    def _page_scripts(self) -> None:
        for url in self.page_posts:
            self._request("POST", url)

    @property
    def cost_micro_usd(self) -> int:
        return self._cost

    # ── the page ──

    def _scan_fields(self) -> list[FormField]:
        return parse_scan([self.scan_data]).fields if self.scan_data is not None else []

    def _has(self, selector: str) -> bool:
        for item in self._scan_fields():
            if selector and (
                item.selector == selector or (item.name and selector == f'[name="{item.name}"]')
            ):
                return True
        for part in selector.split(","):
            part = part.strip()
            m = re.fullmatch(r'\[(id|name)="([^"]+)"\]', part)
            if m and f'{m[1]}="{m[2]}"' in self.page:
                return True
            m = re.fullmatch(r"#([\w\-]+)", part)
            if m and f'id="{m[1]}"' in self.page:
                return True
            m = re.fullmatch(r"(?:button|input)?\.([\w\-]+)", part)
            if m and re.search(rf'class="[^"]*\b{re.escape(m[1])}\b', self.page):
                return True
            if part in ("button[type=submit]", "input[type=submit]") and 'type="submit"' in self.page:
                return True
        return False

    def _need(self, selector: str) -> None:
        if not self._has(selector):
            raise LookupError(f"no element {selector}")

    def _options(self, selector: str) -> list[str]:
        for item in self._scan_fields():
            if item.selector == selector or (item.name and selector == f'[name="{item.name}"]'):
                return [text for _, text in item.options]
        for item in scan_form(self.page).fields if self.page else []:
            if item.selector == selector:
                return [text for _, text in item.options]
        return []

    async def goto(self, url: str) -> int:
        self.url = url
        self._site = site_of(url)
        self._request("GET", url, navigation=True)
        self.actions.append(("goto", url, ""))
        return self.status

    async def content(self) -> str:
        return self.page

    async def current_url(self) -> str:
        return self.url

    async def visible_text(self) -> str:
        return re.sub(r"<[^>]+>", " ", self.page)

    async def exists(self, selector: str, frame: int = 0) -> bool:
        return self._has(selector)

    async def scan(self, resume_name: str = "") -> LiveScan:
        if self.scan_data is not None:
            found = parse_scan([self.scan_data], resume_name=resume_name)
        else:
            form = scan_form(self.page)
            texts = (form.submit_label,) if form.submit_label else ()
            blockers = ScanBlockers(
                bool(_CAPTCHA.search(self.page)), bool(_PASSWORD.search(self.page)), texts
            )
            found = LiveScan(self.url, form.fields, blockers)
        for item in found.fields:
            if item.selector in self.values:
                item.value = self.values[item.selector]
        attached = {item.value for item in found.fields if item.kind == "file" and item.value}
        if self.resume_shows and resume_name_inferable(resume_name) and resume_name in attached:
            found.blockers = replace(found.blockers, resume_name_on_page=True)
        return found

    async def wait_for_form(self, seconds: float, resume_name: str = "") -> LiveScan:
        return await self.scan(resume_name)

    async def buttons(self) -> list[str]:
        if self._buttons is not None:
            return list(self._buttons)
        texts = (" ".join(_TAG.sub(" ", m).split()) for m in _BUTTON.findall(self.page))
        return list(dict.fromkeys(t for t in texts if t and len(t) <= 40))[:30]

    async def probe(self) -> str:
        return self.probe_result

    # ── acting ──

    async def click_submit(self, selector: str, frame: int = 0) -> None:
        if not self.write_hosts:
            raise PermissionError("the submit control is clicked only after allow_submit_hosts")
        self._need(selector)
        self.actions.append(("click_submit", selector, ""))
        if not self._request("POST", self.url):
            self.blocked_submits += 1
            return
        self.submitted = True
        self._show(self.after_submit)
        self.url = self.after_submit_url or self.url.rstrip("/") + "/thanks"
        if self.raise_after_click is not None:
            raise self.raise_after_click

    def _show(self, page: Page) -> None:
        self.page = page if isinstance(page, str) else ""
        self.scan_data = None if isinstance(page, str) else page
        self.values = {}

    async def click_text(self, text: str, *, form_on_screen: bool) -> ClickOutcome:
        if click_refused(text, form_on_screen=form_on_screen):
            self.refusals.append(text)
            return "refused"
        wanted = " ".join(text.split()).casefold()
        if not any(" ".join(b.split()).casefold() == wanted for b in await self.buttons()):
            return "not_found"
        self.clicked_texts.append(text)
        self.actions.append(("click_text", text, ""))
        revealed = next((page for label, page in self._reveals.items() if label.casefold() == wanted), None)
        if revealed is not None:
            self.page = revealed if isinstance(revealed, str) else ""
            self.scan_data = None if isinstance(revealed, str) else revealed
            self._buttons = None if isinstance(revealed, str) else self._buttons
        return "clicked"

    async def fill(self, selector: str, value: str, frame: int = 0) -> None:
        self._need(selector)
        self.actions.append(("fill", selector, value))
        self.values[selector] = value
        self._page_scripts()

    async def select_label(self, selector: str, text: str, frame: int = 0) -> None:
        self._need(selector)
        options = self._options(selector)
        if options:
            index = best_option(options, text)
            if index is None:
                raise LookupError("no option fits")
            text = options[index]
        self.actions.append(("select_label", selector, text))
        self.values[selector] = text
        self._page_scripts()

    async def check(self, selector: str, frame: int = 0) -> None:
        self._need(selector)
        self.actions.append(("check", selector, ""))
        self.values[selector] = "checked"

    async def pick_in_group(self, name: str, text: str, frame: int = 0) -> None:
        selector = f'[name="{name}"]'
        self._need(selector)
        options = self._options(selector)
        if options:
            index = best_option(options, text)
            if index is None:
                raise LookupError("no option fits")
            text = options[index]
        self.actions.append(("pick_in_group", name, text))
        self.values[selector] = text

    async def choose(self, selector: str, text: str, frame: int = 0) -> None:
        self._need(selector)
        if not self.choose_sticks:
            raise LookupError("the pick did not stick")
        self.actions.append(("choose", selector, text))
        self.values[selector] = text
        self._page_scripts()

    async def upload(self, selector: str, name: str, data: bytes, media_type: str, frame: int = 0) -> None:
        self._need(selector)
        self.actions.append(("upload", selector, name))
        self.values[selector] = name
        if self.upload_url is not None:
            self._request("POST", self.upload_url)

    async def screenshot(self) -> bytes:
        return b"\xff\xd8fake-jpeg"

    async def close(self) -> None:
        self.closed = True


class FakeOpener:
    """Hands out `FakeDriver` pages and records each ``(mode, write_hosts)``. ``guard_active=False`` makes every open
    fail the probe (`GuardNotActive`), after closing the page, as the Browser Use opener does."""

    def __init__(self, page: Page, *, guard_active: bool = True, **kwargs: Any) -> None:
        self.page, self.kwargs, self.guard_active = page, kwargs, guard_active
        self.drivers: list[FakeDriver] = []
        self.opened: list[tuple[OpenMode, frozenset[str]]] = []

    async def new_page(self, mode: OpenMode = "preview", write_hosts: Collection[str] = ()) -> FakeDriver:
        self.opened.append((mode, frozenset(write_hosts)))
        driver = FakeDriver(self.page, **self.kwargs)
        self.drivers.append(driver)
        if not self.guard_active:
            await driver.close()
            raise GuardNotActive("the guard probe was not blocked")
        return driver
