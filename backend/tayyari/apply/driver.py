"""What the apply engine drives: a browser page behind the two-layer guard (`apply.netguard`), opened by a `PageOpener`.

`BrowserDriver` is async and frame-aware (``frame`` is the index of a page frame, 0 = the main frame, as in a
`scan.LiveScan`). The worker's driver is Playwright over a Browser Use browser (`apply.browser_use`); tests use
``tests/fakes/apply_browser.FakeDriver``. Rules that keep a person's application safe live here, in code, not in a prompt:

- `click_text` refuses anything that reads as a submit (`SUBMIT_WORDS`), and "apply" once a form is on screen (on a
  posting page "Apply for this job" only opens the form).
- Every write is blocked until `allow_submit_hosts`, which only the approved submit path calls, after the submitting
  marker; then writes may go to those hosts only.
- `PageOpener.new_page` hands out a driver only after `probe` came back ``'BLOCKED'``; otherwise it raises
  `GuardNotActive` and nothing is filled.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Sequence
from typing import Final, Literal, Protocol

from tayyari.apply.scan import LiveScan

OpenMode = Literal["preview", "submit"]
"""``preview``: the fill pass, every write blocked by both layers. ``submit``: the approved send, whose extension allows
writes to the ATS's submit and upload hosts and whose route guard still blocks them until `allow_submit_hosts`."""
ClickOutcome = Literal["clicked", "refused", "not_found"]

SUBMIT_WORDS: Final = re.compile(r"\b(?:submit|send|finish|complete(?: my)? application)\b", re.I)
APPLY_WORDS: Final = re.compile(r"\bapply\b", re.I)
PROBE_BLOCKED: Final = "BLOCKED"


class GuardNotActive(RuntimeError):
    """The guard probe did not come back blocked: the browser was stopped and nothing was filled (stop reason
    ``guard_probe``). The message never holds a URL. ``cost_micro_usd`` is what the stopped browser cost (it was
    billed even though it never filled anything), for the caller to record as spend."""

    cost_micro_usd: int = 0


def click_refused(text: str, *, form_on_screen: bool) -> bool:
    """True when ``text`` must not be clicked by a fill: a submit-like control, or "apply" once a form is on screen."""
    return bool(SUBMIT_WORDS.search(text) or (form_on_screen and APPLY_WORDS.search(text)))


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def best_option(texts: Sequence[str], want: str) -> int | None:
    """The index of the option that fits ``want`` best: the same text, then one that starts with it (or that it starts
    with), then one that contains it (or that it contains). None when nothing fits."""
    folded, target = [_fold(t) for t in texts], _fold(want)
    if not target:
        return None
    tests: tuple[Callable[[str], bool], ...] = (
        lambda t: t == target,
        lambda t: bool(t) and (t.startswith(target) or target.startswith(t)),
        lambda t: bool(t) and (target in t or t in target),
    )
    for test in tests:
        hit = next((i for i, t in enumerate(folded) if test(t)), None)
        if hit is not None:
            return hit
    return None


def shows(shown: str, want: str) -> bool:
    """True when a widget's displayed value shows the picked ``want`` (case and spacing ignored; a widget may add a
    region, ``Pune`` -> ``Pune, Maharashtra, India``, or shorten to a dial code)."""
    a, b = _fold(shown), _fold(want)
    return bool(a and b) and (a == b or b in a or a in b)


class BrowserDriver(Protocol):
    async def goto(self, url: str) -> int:
        """Open ``url`` (the code's own navigation; it sets the site off which page navigations are blocked). The HTTP
        status of the response, 0 when there was none."""
        ...

    async def content(self) -> str: ...
    async def current_url(self) -> str: ...
    async def visible_text(self) -> str: ...
    async def exists(self, selector: str, frame: int = 0) -> bool: ...
    async def fill(self, selector: str, value: str, frame: int = 0) -> None: ...

    async def select_label(self, selector: str, text: str, frame: int = 0) -> None:
        """Pick the ``<select>`` option whose text is ``text`` (else the one that contains it, or that it contains)."""
        ...

    async def check(self, selector: str, frame: int = 0) -> None: ...

    async def pick_in_group(self, name: str, text: str, frame: int = 0) -> None:
        """Check the radio or checkbox of the group ``name`` whose label matches ``text``."""
        ...

    async def choose(self, selector: str, text: str, frame: int = 0) -> None:
        """A combobox or typeahead: type, pick the best option of its own list, read the field back and retry once
        (with Enter) when the pick did not stick. Raises `LookupError` when no option fits."""
        ...

    async def upload(
        self, selector: str, name: str, data: bytes, media_type: str, frame: int = 0
    ) -> None: ...

    async def buttons(self) -> list[str]:
        """The visible button and link texts (at most 30), for opening a form behind a button."""
        ...

    async def click_text(self, text: str, *, form_on_screen: bool) -> ClickOutcome:
        """Click a button or link by its text, unless `click_refused` says no."""
        ...

    async def click_submit(self, selector: str, frame: int = 0) -> None:
        """The approved submit path's only click: the form's submit control. Raises `PermissionError` unless
        `allow_submit_hosts` was called first; a fill never calls it."""
        ...

    async def wait_for_form(self, seconds: float, resume_name: str = "") -> LiveScan:
        """Scan until a form is on screen (`LiveScan.has_form`) or ``seconds`` passed; the last scan."""
        ...

    async def scan(self, resume_name: str = "") -> LiveScan: ...

    async def probe(self) -> str:
        """``'BLOCKED'`` when layer 1 stopped the probe POST; ``'SENT'`` or ``'ERROR:<reason>'`` otherwise."""
        ...

    def allow_submit_hosts(self, hosts: Collection[str]) -> None:
        """Lift layer 2 for writes to ``hosts`` only (the playbook's submit and upload hosts). Called once, by the
        approved submit path, after the submitting marker."""
        ...

    async def screenshot(self) -> bytes: ...

    async def close(self) -> None:
        """Close the page and, for a hosted browser, always stop it (which also ends its billing)."""
        ...

    @property
    def cost_micro_usd(self) -> int:
        """What the browser session cost, read after `close` (0 for a local or fake browser, or when unknown)."""
        ...


class PageOpener(Protocol):
    async def new_page(self, mode: OpenMode = "preview", write_hosts: Collection[str] = ()) -> BrowserDriver:
        """A fresh, guarded page: no cookies, no storage, the probe already blocked. ``write_hosts`` only for
        ``submit``. Raises `GuardNotActive` (after stopping the browser) when the probe was not blocked."""
        ...


__all__ = [
    "APPLY_WORDS",
    "PROBE_BLOCKED",
    "SUBMIT_WORDS",
    "BrowserDriver",
    "ClickOutcome",
    "GuardNotActive",
    "OpenMode",
    "PageOpener",
    "best_option",
    "click_refused",
    "shows",
]
