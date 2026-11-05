"""The apply engine: one run of one application, called by the worker (never in a web request).

fill    queued -> filling -> ready_to_submit (screenshot + review, nothing sent) | needs_user (questions, a CAPTCHA or a
        login wall: the person gets the link) | failed. Playbooks are tried in order: the site's own active and draft
        playbooks, then the built-in one for the ATS; with none, or when they fail, the AI fallback (`BrowserAgent`).
submit  only for ready_to_submit with an approval whose digest equals the review's; the same values are filled again (no
        model call) and the digest checked once more before the submit button is clicked.
Every run writes a `run_metrics` row (playbook vs AI, outcome, cost) for the learning loop.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from tayyari.apply import stages
from tayyari.apply.answers import Answerer, FillContext, Resume
from tayyari.apply.playbook import BUILTIN, Playbook, builtin_id
from tayyari.apply.runner import BrowserDriver, FillResult
from tayyari.apply.runner import fill as run_fill
from tayyari.apply.runner import submit as run_submit
from tayyari.contract.apply import ApplicationReview, RunMode
from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.core.settings import ApplySettings
from tayyari.storage.ports import ApplyRepo, StoredApplication, StoredRunMetric

log = get_logger(__name__)


class PageDriver(BrowserDriver, Protocol):
    async def close(self) -> None: ...


class PageOpener(Protocol):
    async def new_page(self) -> Any: ...


FactsSource = Callable[[StoredApplication], Awaitable[list[dict[str, str]]]]
"""The person's facts an answer may cite (a match's book); [] when there are none."""
ResumeSource = Callable[[StoredApplication], Awaitable[Resume | None]]
"""A resume built for this application (a match's resume PDF), tried before the saved file."""


@dataclass(slots=True)
class AiRun:
    """What an AI fallback run returns to the engine (see `apply.agent`)."""

    result: FillResult
    agent: str
    cost_micro_usd: int = 0
    playbook: Playbook | None = None
    """A draft playbook recorded from the run (saved by the engine when the fill succeeded)."""


class Fallback(Protocol):
    def available(self) -> Sequence[str]: ...

    async def fill(self, driver: Any, app: StoredApplication, url: str, ctx: FillContext) -> AiRun | None: ...


@dataclass(slots=True)
class ApplyEngine:
    repo: ApplyRepo
    settings: ApplySettings
    browser: PageOpener
    clock: Clock
    answerer: Answerer | None = None
    facts: FactsSource | None = None
    resume_for: ResumeSource | None = None
    fallback: Fallback | None = None
    new_id: Callable[[str], str] = field(default=lambda prefix: f"{prefix}_{time.time_ns():x}")

    async def run(self, app: StoredApplication) -> None:
        if app.stage == "queued":
            await self._fill(app)
        elif app.stage == "ready_to_submit" and app.approved_at is not None and app.submitted_at is None:
            await self._submit(app)

    # ── context ──

    async def _context(self, app: StoredApplication, *, for_submit: bool = False) -> FillContext:
        profile = await self.repo.get_profile(app.owner_key)
        details = {k: str(v) for k, v in (profile.details if profile else {}).items() if isinstance(v, str)}
        resume = await self.resume_for(app) if self.resume_for else None
        if resume is None and profile and profile.resume_bytes:
            resume = Resume(
                profile.resume_name or "resume.pdf",
                profile.resume_bytes,
                profile.resume_type or "application/pdf",
            )
        typed = {str(k): str(v) for k, v in ((app.job or {}).get("typed") or {}).items()}
        if for_submit and app.review:
            for f in app.review.get("fields", []):
                if f.get("source") == "model" and f.get("value"):
                    typed.setdefault(str(f["key"]), str(f["value"]))
        facts = [] if for_submit or self.facts is None else await self.facts(app)
        return FillContext(
            details, profile.answers if profile else {}, typed, resume, facts, app.title, app.company
        )

    async def _playbooks(self, app: StoredApplication) -> list[tuple[Playbook, str, str]]:
        found: list[tuple[Playbook, str, str]] = []
        for status in ("active", "draft"):
            for stored in await self.repo.list_playbooks(domain=app.domain, statuses=[status]):
                try:
                    found.append((Playbook.model_validate(stored.spec), stored.id, stored.status))
                except ValueError:
                    log.warning("apply_playbook_unreadable", playbook_id=stored.id)
        if app.ats in BUILTIN:
            pb = BUILTIN[app.ats]
            found.append((pb, builtin_id(pb), "builtin"))
        return found

    # ── fill ──

    async def _fill(self, app: StoredApplication) -> None:
        now = self.clock.now()
        started = time.monotonic()
        moving = await stages.move(
            self.repo, app, "filling", source="engine", now=now, changes={"attempts": app.attempts + 1}
        )
        if moving is None:
            return
        app = moving
        ctx = await self._context(app)
        url = app.apply_url or app.job_url
        result: FillResult | None = None
        used: tuple[str, str] | None = None
        mode: RunMode = "playbook"
        agent: str | None = None
        cost = 0
        driver = await self.browser.new_page()
        try:
            for playbook, pb_id, pb_status in await self._playbooks(app):
                result = await run_fill(driver, playbook, url, ctx, self.answerer)
                used = (pb_id, pb_status)
                if pb_status == "draft" or pb_status == "active":
                    await self.repo.record_playbook_result(
                        pb_id, success=result.status in ("ready", "needs_user"), now=self.clock.now()
                    )
                    await self._maybe_promote(pb_id)
                if result.status != "failed":
                    break
            if (
                (result is None or result.status == "failed")
                and self.fallback is not None
                and self.fallback.available()
            ):
                ai = await self.fallback.fill(driver, app, url, ctx)
                if ai is not None:
                    result, mode, agent, cost, used = ai.result, "ai", ai.agent, ai.cost_micro_usd, None
                    if ai.playbook is not None and ai.result.status in ("ready", "needs_user"):
                        used = await self._save_draft(app, ai.playbook)
        except Exception as exc:
            log.warning("apply_fill_crashed", application_id=app.id, error=type(exc).__name__)
            result = FillResult("failed", reason="error", note=f"The run stopped ({type(exc).__name__}).")
        finally:
            with contextlib.suppress(Exception):
                await driver.close()
        if result is None:
            result = FillResult(
                "failed",
                reason="no_playbook",
                note="No playbook for this site yet and no AI fallback is set up.",
            )
        await self._finish_fill(
            app, result, mode, used, agent, cost, int((time.monotonic() - started) * 1000)
        )

    async def _finish_fill(
        self, app: StoredApplication, result: FillResult, mode: RunMode, used: tuple[str, str] | None,
        agent: str | None, cost: int, duration_ms: int,
    ) -> None:  # fmt: skip
        now = self.clock.now()
        plan = result.plan
        if result.screenshot is not None:
            await self.repo.put_screenshot(app.id, result.screenshot)
        review = None
        if plan is not None:
            review = ApplicationReview(
                digest=plan.digest(), capturedAt=now.isoformat(), mode=mode, fields=plan.review_fields(),
                skipped=plan.skipped, warnings=result.warnings, playbookId=used[0] if used else None, agent=agent,
                submitLabel=result.submit_label,
            ).to_dict()  # fmt: skip
        needs = [n.to_dict() for n in plan.needs] if plan is not None else None
        changes: dict[str, Any] = {"needs": needs, "review": review, "review_digest": review["digest"] if review else None,
                                   "approved_at": None, "approved_digest": None}  # fmt: skip
        if result.status == "ready":
            to, reason = "ready_to_submit", None
        elif result.status in ("needs_user", "blocked"):
            to, reason = "needs_user", result.note or "Some questions need your answer."
            if result.status == "blocked":
                changes["manual_url"] = app.apply_url or app.job_url
        else:
            to, reason = "failed", result.note or "The form could not be filled."
        changes["stage_reason"] = reason
        await stages.move(self.repo, app, to, source="engine", now=now, note=reason, changes=changes)
        await self._metric(app, "fill", mode, used, agent, result.status, result.reason, result.steps, duration_ms, cost,
                           result.fields_total, len(plan.filled) if plan else 0, plan.model_fields if plan else 0)  # fmt: skip

    async def _save_draft(self, app: StoredApplication, playbook: Playbook) -> tuple[str, str]:
        from tayyari.storage.ports import StoredPlaybook

        now = self.clock.now()
        existing = await self.repo.list_playbooks(domain=app.domain, statuses=["draft"])
        pb_id = existing[0].id if existing else self.new_id("pb")
        version = (existing[0].version + 1) if existing else 1
        await self.repo.save_playbook(
            StoredPlaybook(
                id=pb_id, ats=playbook.ats, domain=app.domain, status="draft", origin="recorded", version=version,
                spec=playbook.model_copy(update={"version": version, "domain": app.domain}).model_dump(mode="json"),
                successes=existing[0].successes if existing else 0, failures=existing[0].failures if existing else 0,
                created_at=existing[0].created_at if existing else now, updated_at=now,
            )
        )  # fmt: skip
        await stages.note(
            self.repo,
            app.id,
            "Recorded a draft playbook for this site.",
            source="engine",
            now=now,
            kind="playbook",
        )
        return pb_id, "draft"

    async def _maybe_promote(self, pb_id: str) -> None:
        pb = await self.repo.get_playbook(pb_id)
        if (
            pb is not None
            and pb.status == "draft"
            and pb.successes >= self.settings.draft_promote_after
            and pb.failures == 0
        ):
            await self.repo.set_playbook_status(pb_id, "active", now=self.clock.now())
            log.info("apply_playbook_promoted", playbook_id=pb_id, domain=pb.domain)

    # ── submit ──

    async def _submit(self, app: StoredApplication) -> None:
        started = time.monotonic()
        now = self.clock.now()
        if not app.approved_digest or app.approved_digest != app.review_digest:
            await stages.move(self.repo, app, "needs_user", source="engine", now=now, note="The approval does not match the review.",
                              changes={"approved_at": None, "approved_digest": None, "stage_reason": "Approve the form again."})  # fmt: skip
            return
        review = app.review or {}
        pb_id = str(review.get("playbookId") or "")
        playbook = None
        for candidate, cid, _ in await self._playbooks(app):
            if cid == pb_id:
                playbook = candidate
                break
        if playbook is None:
            await stages.move(self.repo, app, "needs_user", source="engine", now=now, note="No replayable playbook to submit with.",
                              changes={"manual_url": app.apply_url or app.job_url, "stage_reason": "Open the link to submit it yourself."})  # fmt: skip
            return
        ctx = await self._context(app, for_submit=True)
        driver = await self.browser.new_page()
        try:
            result = await run_submit(
                driver, playbook, app.apply_url or app.job_url, ctx, app.approved_digest
            )
        except Exception as exc:
            log.warning("apply_submit_crashed", application_id=app.id, error=type(exc).__name__)
            result = None
        finally:
            with contextlib.suppress(Exception):
                await driver.close()
        now = self.clock.now()
        if result is None:
            await stages.move(self.repo, app, "failed", source="engine", now=now, note="The submit run stopped.",
                              changes={"stage_reason": "The submit run stopped; try again."})  # fmt: skip
            outcome, reason, steps = "failed", "error", 0
        elif result.status == "applied":
            if result.screenshot is not None:
                await self.repo.put_screenshot(app.id, result.screenshot)
            await stages.move(self.repo, app, "applied", source="engine", now=now, note="Submitted.",
                              changes={"submitted_at": now, "stage_reason": None, "needs": None})  # fmt: skip
            outcome, reason, steps = "success", None, result.steps
        else:
            to = "needs_user" if result.status == "needs_user" else "failed"
            await stages.move(self.repo, app, to, source="engine", now=now, note=result.note,
                              changes={"stage_reason": result.note, "approved_at": None, "approved_digest": None,
                                       "manual_url": app.apply_url or app.job_url})  # fmt: skip
            outcome, reason, steps = (
                ("needs_user" if to == "needs_user" else "failed"),
                result.reason,
                result.steps,
            )
        await self._metric(app, "submit", "playbook", (pb_id, "builtin" if str(pb_id).startswith("builtin:") else "stored"),
                           None, outcome, reason, steps, int((time.monotonic() - started) * 1000), 0, 0, 0, 0)  # fmt: skip

    async def _metric(
        self, app: StoredApplication, kind: str, mode: RunMode, used: tuple[str, str] | None, agent: str | None,
        outcome: str, reason: str | None, steps: int, duration_ms: int, cost: int, total: int, filled: int, model: int,
    ) -> None:  # fmt: skip
        mapped = {
            "ready": "success",
            "needs_user": "needs_user",
            "blocked": "blocked",
            "failed": "failed",
        }.get(outcome, outcome)
        await self.repo.add_run_metric(
            StoredRunMetric(
                id=self.new_id("run"), application_id=app.id, created_at=self.clock.now(), run_kind=kind, ats=app.ats,
                domain=app.domain, mode=mode, outcome=mapped, playbook_id=used[0] if used else None,
                playbook_status=used[1] if used else None, agent=agent, failure_reason=reason if mapped != "success" else None,
                steps=steps, duration_ms=duration_ms, cost_micro_usd=cost, fields_total=total, fields_filled=filled,
                fields_model=model,
            )
        )  # fmt: skip


__all__ = ["AiRun", "ApplyEngine", "FactsSource", "Fallback", "PageOpener", "ResumeSource"]
