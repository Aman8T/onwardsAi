"""Prove the submit guard before any real run: in a guarded standalone browser, a POST must be BLOCKED and a file must
attach to a file input over CDP. Costs about one browser-minute ($0.0003). Usage: uv run python guard_check.py"""

from __future__ import annotations

import json
import time

from playwright.sync_api import sync_playwright

from common import bu, spend
from common.env import load_env, presence
from common.guard import probe
from common.profile import RESUME_NAME
from common.resume import build_resume


def main() -> int:
    load_env()
    print("env:", presence())
    spend.check(bu_estimate=0.002)
    resume = build_resume()
    c = bu.client()
    before = bu.balance()
    print("billing fields:", sorted(before))
    ext_id = bu.ensure_guard_extension(c)
    ext = c.extensions.get(ext_id)
    print("guard extension status:", getattr(ext, "status", None))
    started = time.monotonic()
    result: dict[str, object] = {"probe_extension_only": None}
    browser_id = None
    try:
        with bu.standalone_browser(c, ext_id, minutes=3, label="guard-check") as b:
            browser_id = str(b.id)
            with sync_playwright() as p:
                browser = bu.connect(p, b.cdp_url)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://example.com/", wait_until="domcontentloaded", timeout=45_000)
                result["probe_extension_only"] = probe(page)
                print("probe:", result["probe_extension_only"])
                page.evaluate("() => { document.body.innerHTML = '<form><input type=\"file\" id=\"cv\"></form>'; }")
                page.set_input_files("#cv", files=[{"name": RESUME_NAME, "mimeType": "application/pdf",
                                                    "buffer": resume.read_bytes()}], timeout=20_000)  # fmt: skip
                result["file_attached"] = page.evaluate("() => document.querySelector('#cv').files[0]?.name || ''")
                browser.close()
    finally:
        cost = bu.browser_cost(c, browser_id) if browser_id else {"browser": 0.0, "proxy": 0.0, "proxy_mb": 0.0}
        after = bu.balance()
        result.update(seconds=round(time.monotonic() - started, 1), cost=cost, active_after=after.get("activeSessionCount"),
                      credit_before=bu.credit_usd(before), credit_after=bu.credit_usd(after))  # fmt: skip
        spend.record({"kind": "guard_check", "browser_use_usd": cost["browser"] + cost["proxy"], "anthropic_usd": 0})
        print(json.dumps(result, indent=2))
    return 0 if result["probe_extension_only"] == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
