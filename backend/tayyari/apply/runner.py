"""The playbook runner: fill a form from a playbook with no model call (beyond optional screening drafts), screenshot it, and
stop before submit. `submit` re-fills the same values and clicks submit only when the fill's digest equals the approved one.

It drives a `BrowserDriver` (Playwright in the worker, a fake over saved HTML in tests). The driver blocks every request that
is not a GET until `allow_submit(True)`, so nothing can be sent while filling, even by the page's own scripts.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

from tayyari.apply.answers import Answerer, FillContext, Plan, apply_model_answers, plan_fields
from tayyari.apply.forms import blocker_of, checks_on_submit, scan_form
from tayyari.apply.playbook import Playbook

BOT_CHECK_WARNING = "This form runs a bot check when it is submitted. If it asks for a human check, you will get a link to finish it yourself."


class BrowserDriver(Protocol):
    async def goto(self, url: str) -> None: ...
    async def content(self) -> str: ...
    async def current_url(self) -> str: ...
    async def visible_text(self) -> str: ...
    async def exists(self, selector: str) -> bool: ...
    async def click(self, selector: str) -> None: ...
    async def fill(self, selector: str, value: str) -> None: ...
    async def select(self, selector: str, value: str) -> None: ...
    async def check(self, selector: str) -> None: ...
    async def upload(self, selector: str, name: str, data: bytes, media_type: str) -> None: ...
    async def screenshot(self) -> bytes: ...
    def allow_submit(self, allowed: bool) -> None: ...


FillStatus = Literal["ready", "needs_user", "blocked", "failed"]


@dataclass(slots=True)
class FillResult:
    status: FillStatus
    plan: Plan | None = None
    reason: str | None = None
    """A short code for metrics: captcha, login_wall, no_form, step_failed, navigation, needs_answers."""
    note: str | None = None
    screenshot: bytes | None = None
    submit_label: str | None = None
    warnings: list[str] = field(default_factory=list)
    fields_total: int = 0
    steps: int = 0


@dataclass(slots=True)
class SubmitResult:
    status: Literal["applied", "needs_user", "failed"]
    reason: str | None = None
    note: str | None = None
    screenshot: bytes | None = None
    steps: int = 0


async def fill(
    driver: BrowserDriver, playbook: Playbook, url: str, ctx: FillContext, answerer: Answerer | None
) -> FillResult:
    driver.allow_submit(False)
    steps = 0
    try:
        await driver.goto(url)
        steps += 1
        for selector in playbook.open_clicks:
            if await driver.exists(selector):
                await driver.click(selector)
                steps += 1
        page = await driver.content()
    except Exception as exc:
        return FillResult(
            "failed", reason="navigation", note=f"The page did not open ({type(exc).__name__}).", steps=steps
        )
    blocker = blocker_of(page)
    if blocker is not None:
        note = (
            "The site asks for a human check (CAPTCHA)."
            if blocker == "captcha"
            else "The site wants you to sign in first."
        )
        return FillResult("blocked", reason=blocker, note=note, screenshot=await _shot(driver), steps=steps)
    scan = scan_form(page)
    if not scan.fields:
        return FillResult("failed", reason="no_form", note="No application form on the page.", steps=steps)
    plan, questions = plan_fields(scan.fields, playbook, ctx)
    drafted = await answerer.answer(questions, ctx) if (answerer is not None and questions) else {}
    apply_model_answers(plan, questions, drafted, ctx)
    for planned in plan.filled:
        try:
            if planned.file is not None:
                await driver.upload(
                    planned.item.selector, planned.file.name, planned.file.data, planned.file.media_type
                )
            elif planned.item.kind in ("select", "radio"):
                await driver.select(planned.item.selector, planned.option_value or planned.value)
            elif planned.item.kind == "checkbox":
                await driver.check(planned.item.selector)
            else:
                await driver.fill(planned.item.selector, planned.value)
            steps += 1
        except Exception as exc:
            return FillResult(
                "failed", plan, "step_failed", f'Could not fill "{planned.item.label}" ({type(exc).__name__}).',
                await _shot(driver), scan.submit_label, fields_total=len(scan.fields), steps=steps,
            )  # fmt: skip
    warnings = [BOT_CHECK_WARNING] if checks_on_submit(page) else []
    status: FillStatus = "needs_user" if any(n.required for n in plan.needs) else "ready"
    return FillResult(
        status, plan, "needs_answers" if status == "needs_user" else None, None, await _shot(driver), scan.submit_label,
        warnings, len(scan.fields), steps,
    )  # fmt: skip


async def submit(
    driver: BrowserDriver,
    playbook: Playbook,
    url: str,
    ctx: FillContext,
    approved_digest: str,
    *,
    wait_s: float = 15.0,
) -> SubmitResult:
    """Fill again with exactly the approved values (no model call: drafted answers come back as typed ones), check the digest,
    then click submit and wait for the success page."""
    filled = await fill(driver, playbook, url, ctx, None)
    if filled.status != "ready" or filled.plan is None:
        return SubmitResult("needs_user", filled.reason or "form_changed", filled.note or "The form changed since you approved it.",
                            filled.screenshot, filled.steps)  # fmt: skip
    if filled.plan.digest() != approved_digest:
        return SubmitResult("needs_user", "form_changed", "The form changed since you approved it: check it again.",
                            filled.screenshot, filled.steps)  # fmt: skip
    driver.allow_submit(True)
    steps = filled.steps
    try:
        await driver.click(playbook.submit_selector)
        steps += 1
    except Exception as exc:
        return SubmitResult(
            "failed",
            "submit_missing",
            f"No submit button ({type(exc).__name__}).",
            await _shot(driver),
            steps,
        )
    deadline = asyncio.get_running_loop().time() + wait_s
    while True:
        here, text, page = await driver.current_url(), await driver.visible_text(), await driver.content()
        if (playbook.success_url and re.search(playbook.success_url, here, re.I)) or re.search(
            playbook.success_text, text, re.I
        ):
            return SubmitResult("applied", screenshot=await _shot(driver), steps=steps)
        if blocker_of(page) == "captcha":
            return SubmitResult("needs_user", "captcha", "The site asked for a human check: open the link and submit it yourself.",
                                await _shot(driver), steps)  # fmt: skip
        if asyncio.get_running_loop().time() >= deadline:
            error = re.search(playbook.error_text, text, re.I)
            reason, note = (
                ("form_error", "The form refused the application.")
                if error
                else ("no_confirmation", "No confirmation after submitting.")
            )
            return SubmitResult("failed", reason, note, await _shot(driver), steps)
        await asyncio.sleep(0.5)


async def _shot(driver: BrowserDriver) -> bytes | None:
    try:
        return await driver.screenshot()
    except Exception:
        return None


__all__ = ["BOT_CHECK_WARNING", "BrowserDriver", "FillResult", "SubmitResult", "fill", "submit"]
