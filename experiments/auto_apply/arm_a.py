"""Arm A: Browser Use's hosted V4 agent does the whole task with its own model.

The run's browser loads the guard extension (every non-GET request and CAPTCHA providers blocked), so even if the
agent clicked submit nothing could be sent. After the run we attach our own CDP client to the agent's still-open
browser (keepAlive), prove the guard was live, read the form's DOM state with the same scanner and scorer as the other
arms, take a screenshot, and stop the browser.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from common import bu
from common.formscan import best_page, scan_page
from common.guard import probe
from common.profile import PROFILE, RESUME_NAME
from common.resume import build_resume
from score import score

TERMINAL = {"completed", "failed", "cancelled", "stopped", "error"}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "fields": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                   "properties": {"label": {"type": "string"}, "value": {"type": "string"},
                                  "status": {"type": "string", "enum": ["filled", "skipped", "failed"]}},
                   "required": ["label", "value", "status"]}},
        "blocked": {"type": "string", "description": "captcha, login, no_form, or empty"},
        "submit_button_seen": {"type": "boolean"},
    },
    "required": ["fields", "blocked", "submit_button_seen"],
}  # fmt: skip


def redact(obj: Any) -> Any:
    """Drop live-view and CDP URLs (they embed the browser's control credential) from anything we save."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in ("live_view", "liveurl", "cdp")) else redact(v))
                for k, v in obj.items()}  # fmt: skip
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and ("cdp.browser-use.com" in obj or "live.browser-use.com" in obj):
        return "<redacted>"
    return obj


def task_text(job: dict[str, Any]) -> str:
    details = "\n".join(f"- {k}: {v}" for k, v in PROFILE.items() if v)
    return f"""Open {job['apply_url']} and fill in the job application form for {job['title']} at {job['company']}.

THIS IS A TEST WITH A FICTIONAL APPLICANT. DO NOT SUBMIT. Never click a Submit, Send, Apply or Finish button and never
press Enter in a field. When the form is filled, stop and return the summary. (Submission is blocked anyway.)

Use exactly these applicant details:
{details}

The resume is the file uploads/{RESUME_NAME} in your workspace: upload it to the resume/CV field. If the browser's
upload needs the file in its downloads directory, copy it there first, then upload it.
Fill every required field and every optional field these details answer. Answer screening questions in one or two plain
sentences from the details. For required demographic questions choose "decline" / "prefer not to say". Required
acknowledgement checkboxes may be ticked (test only). If a cookie banner appears, reject non-essential cookies.
If a CAPTCHA, a login page or an account wall appears, stop immediately and report it in "blocked".
Return every field you saw with the value you entered."""


def run(job: dict[str, Any], run_dir: Path, *, model: str = "bu-ultrafast", max_cost: float = 0.15,
        timeout_s: int = 420) -> dict[str, Any]:  # fmt: skip
    resume = build_resume()
    c = bu.client()
    ext_id = bu.ensure_guard_extension(c)
    ws_id = bu.ensure_resume_workspace(c, resume)
    result: dict[str, Any] = {"arm": "A", "model": model}
    started = time.monotonic()
    created = c.runs.create(
        task_text(job), model=model, workspace_id=ws_id, output_schema=OUTPUT_SCHEMA, keep_alive=180,
        max_cost_usd=max_cost, browser_settings={"extensionIds": [ext_id], "proxyCountryCode": None},
    )  # fmt: skip
    run_id, session_id = str(created.id), str(created.session_id)
    result.update(run_id=run_id, session_id=session_id)
    status = str(created.status)
    while time.monotonic() - started < timeout_s:
        status = str(getattr(c.runs.status(run_id), "status", status))
        status = status.split(".")[-1].lower()
        if status in TERMINAL:
            break
        time.sleep(4)
    else:
        c.runs.cancel(run_id)
        status = "timeout(cancelled)"
        time.sleep(3)
    result["agent_seconds"] = round(time.monotonic() - started, 1)
    result["status"] = status

    events: list[dict[str, Any]] = []
    after = None
    for _ in range(40):
        page = c.runs.events(run_id, after=after, limit=100)
        for e in page.events:
            events.append(e.model_dump(mode="json") if hasattr(e, "model_dump") else dict(e))
        after = getattr(page, "next_after", None) or after
        if not getattr(page, "has_more", False):
            break
    events = redact(events)
    (run_dir / "events.json").write_text(json.dumps(events, indent=2, default=str), encoding="utf-8")
    types: dict[str, int] = {}
    for e in events:
        types[str(e.get("type"))] = types.get(str(e.get("type")), 0) + 1
    result["event_types"] = types
    browser_ids = [str((e.get("data") or {}).get("browser_session_id")) for e in events
                   if e.get("type") in ("browser.ready", "browser.reattached") and (e.get("data") or {}).get("browser_session_id")]  # fmt: skip

    summary = c.runs.get(run_id)
    result["agent_output"] = summary.output if summary.output is not None else summary.result
    result["agent_error"] = summary.error
    result["tokens"] = {"input": summary.total_input_tokens, "output": summary.total_output_tokens}
    result["run_cost_usd"] = float(summary.total_cost_usd or 0)

    browser_id = browser_ids[-1] if browser_ids else None
    result["browser_id"] = browser_id
    if browser_id:
        try:
            view = bu.get_browser(browser_id)
            if view.get("cdpUrl") and str(view.get("status", "")).lower().endswith("active"):
                with sync_playwright() as p:
                    browser = bu.connect(p, view["cdpUrl"])
                    context = browser.contexts[0]
                    page = best_page(context) or (context.pages[0] if context.pages else None)
                    if page is not None:
                        result["guard_probe"] = probe(page)
                        final = scan_page(page)
                        (run_dir / "fields.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
                        page.screenshot(path=str(run_dir / "screenshot.jpg"), full_page=True, type="jpeg", quality=55)
                        result["score"] = score(final)
                        result["final_url"] = final["url"]
                    try:
                        browser.close()
                    except Exception:
                        pass
            else:
                result["scan_error"] = "browser no longer active after the run"
        except Exception as exc:
            result["scan_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            try:
                bu.stop_browser(browser_id)
            except Exception:
                pass
    time.sleep(3)
    try:
        cost = c.sessions.cost(session_id)
        result["session_cost"] = {k: float(getattr(cost, k) or 0) for k in
                                  ("llm_cost_usd", "browser_cost_usd", "proxy_cost_usd", "search_cost_usd", "total_cost_usd")}  # fmt: skip
        result["browser_use_usd"] = result["session_cost"]["total_cost_usd"]
    except Exception as exc:
        result["session_cost_error"] = type(exc).__name__
        result["browser_use_usd"] = result["run_cost_usd"]
    result["seconds"] = round(time.monotonic() - started, 1)
    result["anthropic_usd"] = 0.0
    result["steps"] = sum(1 for e in events if e.get("type") == "core.event" and (e.get("data") or {}).get("type") == "step_start")
    result["llm_requests"] = types.get("llm.request", 0)
    result["agent_trace"] = agent_trace(events)
    return result


def agent_trace(events: list[dict[str, Any]]) -> list[str]:
    """The agent's tool calls (its JavaScript actions), tolerant of any event shape."""
    out = []
    for e in events:
        data = e.get("data") if isinstance(e.get("data"), dict) else {}
        part = data.get("part") if isinstance(data.get("part"), dict) else {}
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        inp = state.get("input") if isinstance(state.get("input"), dict) else {}
        if e.get("type") == "core.event" and data.get("type") == "tool_use" and state.get("status") == "running":
            out.append(str(inp.get("code", ""))[:200])
    return out
