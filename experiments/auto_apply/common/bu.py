"""Browser Use Cloud (API V4) helpers: the guard extension, standalone browsers that are always stopped, the account
balance, and a CDP connection that never leaks its URL.

Traps from the docs that this module handles:
  - closing Playwright or dropping CDP does NOT stop a standalone browser or its billing: `standalone_browser` always
    calls ``PATCH /api/v4/browsers/{id} {"action": "stop"}`` (SDK `browsers.stop`) in ``finally``;
  - the CDP URL is a credential: it is never printed, logged or written; connection errors are re-raised without it.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
from browser_use_sdk.v4 import BrowserUse

from .guard import extension_zip

HERE = Path(__file__).resolve().parents[1]
STATE = HERE / "state.json"
API_V2 = "https://api.browser-use.com/api/v2"


def client() -> BrowserUse:
    return BrowserUse(timeout=60.0)


def _state() -> dict[str, Any]:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def _save_state(state: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def ensure_guard_extension(c: BrowserUse) -> str:
    """Upload the guard extension once per content hash; wait until it is ready; return its id."""
    data, digest = extension_zip()
    state = _state()
    known = state.get("guard_extension", {})
    if known.get("hash") == digest and known.get("id"):
        return str(known["id"])
    zip_path = HERE / "data" / f"guard-{digest}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(data)
    ext = c.extensions.create(zip_path)
    ext_id = str(ext.id)
    for _ in range(30):
        status = str(getattr(c.extensions.get(ext_id), "status", "") or "").lower()
        if status in ("ready", "active", ""):
            break
        if status in ("failed", "error", "rejected"):
            raise RuntimeError(f"guard extension upload {status}")
        time.sleep(2)
    state["guard_extension"] = {"id": ext_id, "hash": digest}
    _save_state(state)
    return ext_id


def ensure_resume_workspace(c: BrowserUse, resume: Path) -> str:
    """A workspace holding the dummy resume (V4 restores it under uploads/ for Arm A runs)."""
    state = _state()
    if state.get("workspace_id") and state.get("workspace_file") == resume.name:
        return str(state["workspace_id"])
    ws = c.workspaces.create(name="onwards-auto-apply-experiment")
    c.workspaces.upload(ws.id, resume)
    state["workspace_id"], state["workspace_file"] = str(ws.id), resume.name
    _save_state(state)
    return str(ws.id)


def balance() -> dict[str, Any]:
    """The project's billing numbers (credit balance etc.); only numeric/boolean fields are returned."""
    resp = httpx.get(f"{API_V2}/billing/account", headers={"X-Browser-Use-API-Key": os.environ["BROWSER_USE_API_KEY"]},
                     timeout=30)  # fmt: skip
    resp.raise_for_status()
    body = resp.json()
    return {k: v for k, v in body.items() if isinstance(v, int | float | bool) or (isinstance(v, str) and k.lower().endswith(("credits", "balance", "usd")))}


def credit_usd(bal: dict[str, Any]) -> float | None:
    for key in ("totalCreditsBalanceUsd", "creditsBalanceUsd", "balanceUsd", "totalCredits", "balance"):
        if key in bal:
            try:
                return float(bal[key])
            except (TypeError, ValueError):
                pass
    return None


API_V4 = "https://api.browser-use.com/api/v4"


def _headers() -> dict[str, str]:
    return {"X-Browser-Use-API-Key": os.environ["BROWSER_USE_API_KEY"], "Content-Type": "application/json"}


def get_browser(browser_id: str) -> dict[str, Any]:
    """GET /browsers/{id} as plain JSON (the SDK model rejects agent browsers whose extension entries lack runtimeId)."""
    resp = httpx.get(f"{API_V4}/browsers/{browser_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return dict(resp.json())


def stop_browser(browser_id: str) -> None:
    """PATCH /browsers/{id} {"action": "stop"}: ends billing and refunds unused reserved time."""
    resp = httpx.patch(f"{API_V4}/browsers/{browser_id}", headers=_headers(), json={"action": "stop"}, timeout=30)
    if resp.status_code not in (200, 409):  # 409/already stopped is fine
        resp.raise_for_status()


@contextlib.contextmanager
def standalone_browser(c: BrowserUse, ext_id: str, minutes: int, label: str) -> Iterator[Any]:
    """A guarded standalone browser: no CAPTCHA solving, no managed proxy (direct egress), our guard extension.
    Always stopped on exit, which also refunds unused time."""
    b = c.browsers.create(proxy_country_code=None, solve_captchas=False, extension_ids=[ext_id], timeout=minutes,
                          metadata={"experiment": "auto-apply", "label": label[:40]})  # fmt: skip
    try:
        yield b
    finally:
        for attempt in range(3):
            try:
                stop_browser(str(b.id))
                break
            except Exception:
                time.sleep(2 * (attempt + 1))


def browser_cost(c: BrowserUse, browser_id: str) -> dict[str, float]:
    """browserCost + proxyCost of a (stopped) browser, retried while the final numbers settle."""
    view: dict[str, Any] = {}
    for _ in range(5):
        try:
            view = get_browser(browser_id)
        except Exception:
            view = {}
        if view.get("finishedAt"):
            break
        time.sleep(2)
    f = lambda name: float(view.get(name) or 0)  # noqa: E731
    return {"browser": f("browserCost"), "proxy": f("proxyCost"), "proxy_mb": f("proxyUsedMb")}


def connect(playwright: Any, cdp_url: str) -> Any:
    try:
        return playwright.chromium.connect_over_cdp(cdp_url, timeout=60_000)
    except Exception:
        raise RuntimeError("CDP connection failed (URL withheld)") from None
