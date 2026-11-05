"""Arm C: replay a recorded Arm B trace deterministically, with no model call. This is what a saved per-ATS playbook
costs once it exists: browser time only. Same guarded browser, same scanner and scorer, never submits.

Each recorded action is matched to the live form by (label, type) first, then by its recorded selector.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from arm_b import click_text, execute, wait_for_form
from common import bu
from common.formscan import scan_page
from common.guard import install_route_guard, probe
from common.resume import build_resume
from score import score


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split()).rstrip("* ")


def run(job: dict[str, Any], run_dir: Path, trace_path: Path) -> dict[str, Any]:
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    resume = build_resume().read_bytes()
    c = bu.client()
    ext_id = bu.ensure_guard_extension(c)
    result: dict[str, Any] = {"arm": "C", "model": "none (replay)", "replayed_from": str(trace_path.parent.name)}
    blocked: list[dict[str, str]] = []
    errors: list[str] = []
    started = time.monotonic()
    browser_id = None
    failure = None
    with bu.standalone_browser(c, ext_id, minutes=4, label=f"C-{job['label']}") as b:
        browser_id = str(b.id)
        with sync_playwright() as p:
            browser = bu.connect(p, b.cdp_url)
            context = browser.contexts[0]
            install_route_guard(context, blocked)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=45_000)
                result["guard_probe"] = probe(page)
                scan = wait_for_form(page)
                for act in trace:
                    if act["op"] == "click":
                        if act.get("outcome") == "clicked":
                            click_text(page, act["text"])
                            scan = wait_for_form(page, 8)
                        continue
                    fields = scan["fields"]
                    f = next((x for x in fields if _norm(x["label"]) == _norm(act["label"]) and x["type"] == act["type"]), None)
                    if f is None:
                        f = next((x for x in fields if x["selector"] == act["selector"]), None)
                    if f is None:
                        errors.append(f"not found: {act['label'][:60]}")
                        continue
                    try:
                        execute(page, f, act["op"], act["value"], resume)
                    except Exception as exc:
                        errors.append(f"{act['label'][:60]}: {type(exc).__name__}")
                final = scan_page(page)
                (run_dir / "fields.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
                page.screenshot(path=str(run_dir / "screenshot.jpg"), full_page=True, type="jpeg", quality=55)
                result["score"] = score(final)
                result["final_url"] = final["url"]
            except Exception as exc:
                failure = f"error: {type(exc).__name__}: {str(exc)[:160]}"
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    cost = bu.browser_cost(c, browser_id) if browser_id else {"browser": 0.0, "proxy": 0.0, "proxy_mb": 0.0}
    result.update(seconds=round(time.monotonic() - started, 1), failure=failure, errors=errors,
                  steps=sum(1 for a in trace if a["op"] != "click"), anthropic_usd=0.0,
                  browser_use_usd=round(cost["browser"] + cost["proxy"], 6), browser_cost=cost,
                  blocked_requests=len(blocked))  # fmt: skip
    return result
