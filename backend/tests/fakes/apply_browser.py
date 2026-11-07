"""A `BrowserDriver` over saved HTML: it records every action, refuses selectors the page does not have, and serves the
"submitted" page only after a submit click that `allow_submit(True)` permitted (mirroring the real driver's request guard)."""

from __future__ import annotations

import re

THANKS = (
    "<html><body><h1>Thank you for applying!</h1><p>Your application has been submitted.</p></body></html>"
)


class FakeDriver:
    def __init__(
        self, page: str, *, after_submit: str = THANKS, url: str = "https://example.com/apply"
    ) -> None:
        self.page, self.after_submit, self.url = page, after_submit, url
        self.actions: list[tuple[str, str, str]] = []
        self.submit_allowed = False
        self.submitted = False
        self.blocked_submits = 0
        self.closed = False

    def _has(self, selector: str) -> bool:
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

    async def goto(self, url: str) -> None:
        self.url = url
        self.actions.append(("goto", url, ""))

    async def content(self) -> str:
        return self.page

    async def current_url(self) -> str:
        return self.url

    async def visible_text(self) -> str:
        return re.sub(r"<[^>]+>", " ", self.page)

    async def exists(self, selector: str) -> bool:
        return self._has(selector)

    async def click(self, selector: str) -> None:
        self._need(selector)
        self.actions.append(("click", selector, ""))
        if "submit" in selector or selector.startswith("#btn-submit") or "button" in selector:
            if not self.submit_allowed:
                self.blocked_submits += 1
                return
            self.submitted = True
            self.page = self.after_submit
            self.url = self.url.rstrip("/") + "/thanks"

    async def fill(self, selector: str, value: str) -> None:
        self._need(selector)
        self.actions.append(("fill", selector, value))

    async def select(self, selector: str, value: str) -> None:
        self._need(selector)
        self.actions.append(("select", selector, value))

    async def check(self, selector: str) -> None:
        self._need(selector)
        self.actions.append(("check", selector, ""))

    async def upload(self, selector: str, name: str, data: bytes, media_type: str) -> None:
        self._need(selector)
        self.actions.append(("upload", selector, name))

    async def screenshot(self) -> bytes:
        return b"\xff\xd8fake-jpeg"

    def allow_submit(self, allowed: bool) -> None:
        self.submit_allowed = allowed

    async def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, page: str, **kwargs: str) -> None:
        self.page, self.kwargs = page, kwargs
        self.drivers: list[FakeDriver] = []

    async def new_page(self) -> FakeDriver:
        driver = FakeDriver(self.page, **self.kwargs)
        self.drivers.append(driver)
        return driver
