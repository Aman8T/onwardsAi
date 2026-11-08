"""Fakes for the Browser Use opener: the REST API, and a Playwright CDP session small enough to drive the real
`PlaywrightDriver` wiring (route guard, probe) with no browser.

`FakeBrowserUseApi` records what the opener asked for (uploads, the create keyword arguments, stops, cost reads) and can
be told to fail a step. `FakeCdpSession` holds one `FakePage` whose probe answer is scripted, and keeps the route handler
the driver installed so a test can put requests to it (`FakeRoute`, `FakeRequest`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from tayyari.apply.browser_use import BrowserUseError, CreatedBrowser
from tayyari.apply.netguard import PROBE_JS

FAKE_CDP_URL = "wss://cdp.fake-browser-use.test/devtools/browser/SECRET-cdp-token"
"""Looks like a credential on purpose: tests assert it never reaches a log line or a repr."""


@dataclass
class FakeBrowserUseApi:
    fail_upload: bool = False
    fail_create: bool = False
    fail_stop: bool = False
    cost: int = 12_000
    uploads: list[str] = field(default_factory=list)
    """The digest of every extension uploaded."""
    created: list[dict[str, Any]] = field(default_factory=list)
    """The keyword arguments of every `create_browser`."""
    stopped: list[str] = field(default_factory=list)
    cost_reads: list[str] = field(default_factory=list)

    async def upload_extension(self, data: bytes, digest: str) -> str:
        if self.fail_upload:
            raise BrowserUseError("upload_extension", 500)
        assert data[:2] == b"PK", "an extension is uploaded as a ZIP"
        self.uploads.append(digest)
        return f"ext-{digest}"

    async def create_browser(self, *, extension_ids: list[str], minutes: int, label: str) -> CreatedBrowser:
        if self.fail_create:
            raise BrowserUseError("create_browser", 429)
        self.created.append({"extension_ids": list(extension_ids), "minutes": minutes, "label": label})
        return CreatedBrowser(f"br-{len(self.created)}", FAKE_CDP_URL)

    async def stop_browser(self, browser_id: str) -> None:
        self.stopped.append(browser_id)
        if self.fail_stop:
            raise BrowserUseError("stop_browser", 503)

    async def browser_cost_micro_usd(self, browser_id: str) -> int:
        self.cost_reads.append(browser_id)
        return self.cost


Handler = Callable[[Any, Any], Awaitable[None]]


@dataclass
class FakeRequest:
    url: str
    method: str = "GET"
    navigation: bool = False

    def is_navigation_request(self) -> bool:
        return self.navigation

    @property
    def frame(self) -> Any:
        return _MainFrame()


class _MainFrame:
    parent_frame = None


@dataclass
class FakeRoute:
    outcome: str = ""

    async def abort(self, error_code: str = "failed") -> None:
        self.outcome = "abort"

    async def continue_(self) -> None:
        self.outcome = "continue"

    async def fulfill(self, **kwargs: Any) -> None:
        self.outcome = "fulfill"


@dataclass
class FakePage:
    probe_answer: str = "BLOCKED"
    probe_raises: bool = False
    url: str = "about:blank"
    visited: list[str] = field(default_factory=list)
    evaluated: list[Any] = field(default_factory=list)

    async def goto(self, url: str, **kwargs: Any) -> Any:
        self.visited.append(url)
        self.url = url
        return None

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.evaluated.append(arg)
        if script == PROBE_JS:
            if self.probe_raises:
                raise RuntimeError("page closed")
            return self.probe_answer
        return None


@dataclass
class FakeWebSocketRoute:
    url: str
    outcome: str = ""

    def connect_to_server(self) -> None:
        self.outcome = "connect"

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.outcome = "close"


@dataclass
class FakeContext:
    page: FakePage
    routes: list[tuple[str, Handler]] = field(default_factory=list)
    socket_routes: list[tuple[str, Callable[[Any], Awaitable[None]]]] = field(default_factory=list)
    closed: bool = False

    @property
    def pages(self) -> list[FakePage]:
        return [self.page]

    async def new_page(self) -> FakePage:
        return self.page

    async def route(self, pattern: str, handler: Handler) -> None:
        self.routes.append((pattern, handler))

    async def route_web_socket(self, pattern: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        self.socket_routes.append((pattern, handler))

    async def open_socket(self, url: str) -> str:
        """Open one WebSocket through the installed socket guard; ``connect`` or ``close``."""
        assert self.socket_routes, "the socket guard was never installed"
        ws = FakeWebSocketRoute(url)
        await self.socket_routes[-1][1](ws)
        return ws.outcome

    async def close(self) -> None:
        self.closed = True

    async def ask(self, method: str, url: str, *, navigation: bool = False) -> str:
        """Put one request to the installed route guard; ``continue`` or ``abort``."""
        assert self.routes, "the route guard was never installed"
        route = FakeRoute()
        await self.routes[-1][1](route, FakeRequest(url, method, navigation))
        return route.outcome


@dataclass
class FakeCdpSession:
    page: FakePage = field(default_factory=FakePage)
    closed: bool = False
    context: FakeContext = field(init=False)

    def __post_init__(self) -> None:
        self.context = FakeContext(self.page)

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeConnect:
    """Stands in for `browser_use.connect_over_cdp`: records the addresses it was given (a test checks they were the
    API's), hands out one session per call, or fails like a CDP error."""

    probe_answer: str = "BLOCKED"
    fail: bool = False
    urls: list[str] = field(default_factory=list)
    sessions: list[FakeCdpSession] = field(default_factory=list)

    async def __call__(self, cdp_url: str) -> FakeCdpSession:
        self.urls.append(cdp_url)
        if self.fail:
            raise RuntimeError("CDP connection failed (address withheld)")
        session = FakeCdpSession(FakePage(probe_answer=self.probe_answer))
        self.sessions.append(session)
        return session
