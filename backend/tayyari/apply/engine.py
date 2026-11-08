"""The apply engine: one run of one application, called by the worker (never in a web request). Spec 3.1 steps 2 and 5.

fill    queued -> filling -> waiting_for_you (a review with its digest, the questions, a screenshot) | needs_signin (a
        login wall or a CAPTCHA: the person gets the link) | closed (the posting is gone) | failed (the form never
        opened, or the guard probe was not blocked: nothing was filled). The preview browser blocks every write
        (`runner.preview`); nothing reaches the employer.
submit  approved -> submitting -> sent | check_this_one | failed, or approved -> waiting_for_you when the form changed.
        The submit browser replays the review's script with no model, reads the form back and recomputes the digest;
        only then does `runner.submit_approved` call ``mark_submitting``, a committed compare-and-set approved ->
        submitting with ``submitting_at``, before it lifts the guard for the playbook's hosts, attaches the resume and
        clicks once. From the marker on the outcome is the success signature (``sent``) or ``check_this_one``, never a
        second click: an exception raised after the marker is ``check_this_one`` too, and a crash leaves the row in
        ``submitting``, which the worker's sweep turns into ``check_this_one`` (`worker.ApplyWorker.sweep`).

Credits (`seams.ApplyCredits`, the hold taken at start): ``sent`` captures it; a fill that closed or failed and a send
that failed before the click release it; ``check_this_one`` keeps it until the person resolves the row. Each ledger call
comes after its stage move; one that fails leaves ``credit_state = 'held'`` for the maintenance sweep. A row whose
``credit_state`` is not ``held`` (an unlimited account) is never sent to the ledger.

Spend: every browser session is held inside ``reserve(est_browser_usd)`` (the field-list loop and the answerer reserve
their own model calls). A refused reservation (`llm.errors.SpendCeilingReached`) puts the row back to ``queued`` with no
attempt counted (an approved row stays approved) and is re-raised, like `BrowserUseBusy`, so the worker backs off.
Each session's Browser Use cost, read after the browser stopped, goes to the run's ``run_metrics`` row and to the spend
ledger under ``browser_use``, a browser that failed to open included (`driver.open_cost`). The browser is stopped in a
``finally`` on every path.

Attempts count the fills of one go: a fill that ends (a review, a sign-in wall, closed, failed) sets ``attempts`` back to
0, so `interrupted` fails a row only after `ApplySettings.requeue_cap` interruptions in a row, never because it was
filled normally before. Every run writes a ``run_metrics`` row, an interrupted (``interrupted``) or deferred
(``deferred``) fill included.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal, Protocol

from pydantic import ValidationError

from tayyari.apply import stages
from tayyari.apply.answers import Answerer, FillContext, Resume, bank_of
from tayyari.apply.browser_use import BrowserUseBusy
from tayyari.apply.driver import BrowserDriver, GuardNotActive, PageOpener, open_cost
from tayyari.apply.fill_loop import FieldListLoop
from tayyari.apply.holds import retake_if_reopened
from tayyari.apply.playbook import BUILTIN, Playbook, builtin_id
from tayyari.apply.runner import (
    ATTACH_WAIT_S,
    OPEN_WAIT_S,
    QUESTIONS_GROUP,
    SUBMIT_WAIT_S,
    FillResult,
    SubmitResult,
    preview,
    review_of,
    submit_approved,
)
from tayyari.apply.seams import ApplyCredits, Reserve, ResumeSource
from tayyari.contract.apply import (
    ApplicationReview,
    ApplyProgress,
    NeedsInput,
    ProgressSection,
    ResumeChoice,
    RunMode,
    StopReason,
)
from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.core.settings import ApplySettings
from tayyari.llm.errors import SpendCeilingReached
from tayyari.storage.ports import ApplyRepo, StoredApplication, StoredRunMetric

log = get_logger(__name__)

BROWSER_USE_SCOPE: Final = "browser_use"
"""The spend ledger scope of a Browser Use session (`storage.repos.limits.BROWSER_USE_SCOPE`)."""
STOPPED_AFTER_SENDING: Final = (
    "The run stopped after starting to send. Check it on the employer's page before you do anything else."
)
"""Why a row that was ``submitting`` became ``check_this_one`` without a clear outcome."""
TAILORED_NEED_KEY: Final = "resume:tailored"
"""The question key of the need a tailored resume that cannot be exported yet becomes."""

FactsSource = Callable[[StoredApplication], Awaitable[list[dict[str, str]]]]
"""The person's facts an answer may cite (a match's book): ``{"id", "text"}`` rows, [] when there are none."""


class SpendLedger(Protocol):
    """Where a Browser Use session's cost is recorded (`storage.ports.LimitRepo` is one)."""

    async def record_spend(self, at: datetime, micro_usd: int, scope: str) -> None: ...


@asynccontextmanager
async def _no_reserve(_est_usd: float) -> AsyncIterator[None]:
    yield


def _new_id(prefix: str) -> str:
    return f"{prefix}_{time.time_ns():x}"


def _url(app: StoredApplication) -> str:
    return app.apply_url or app.job_url


def _choice(app: StoredApplication) -> ResumeChoice:
    return "tailored" if app.resume_choice == "tailored" else "saved"


def _opening() -> dict[str, Any]:
    return ApplyProgress(
        sections=[ProgressSection(label="Opening the form", state="now", filled=0, total=0)],
        filled=0,
        total=0,
    ).to_dict()


def _reading() -> dict[str, Any]:
    return ApplyProgress(
        sections=[
            ProgressSection(label="Opening the form", state="done", filled=0, total=0),
            ProgressSection(label="Filling the form", state="now", filled=0, total=0),
        ],
        filled=0,
        total=0,
    ).to_dict()


def filled_progress(result: FillResult) -> dict[str, Any]:
    """The progress of a finished fill: one section per review group (in the form's order), then the questions."""
    groups: dict[str, list[bool]] = {}
    for row in result.fields:
        groups.setdefault(row.group or QUESTIONS_GROUP, []).append(bool(row.value))
    sections = [
        ProgressSection(label=label, state="done", filled=sum(rows), total=len(rows))
        for label, rows in groups.items()
    ]
    waiting = sum(1 for n in result.needs if n.required)
    if waiting:
        sections.append(
            ProgressSection(label="Questions for you", state="now", filled=0, total=waiting, waiting=waiting)
        )
    return ApplyProgress(
        sections=sections, filled=sum(s.filled for s in sections), total=sum(s.total for s in sections)
    ).to_dict()


_STOP_OF_REASON: Final[dict[str, StopReason]] = {
    "signin": "signin",
    "challenge": "challenge",
    "verification_code": "verification_code",
    "guard_probe": "guard_probe",
    "form_changed": "form_changed",
    "form_changed_after_attach": "form_changed",
}

FILL_OUTCOME: Final[dict[str, str]] = {
    "ready": "success",
    "questions": "questions",
    "needs_signin": "needs_signin",
    "closed": "closed",
    "failed": "failed",
}
"""A fill's `runner.FillResult.status` -> its ``run_metrics.outcome``."""


def _deferred_reason(exc: BaseException) -> str:
    return "browser_use_busy" if isinstance(exc, BrowserUseBusy) else "spend_ceiling"


@dataclass(slots=True)
class _Run:
    """What one run cost and did, for its ``run_metrics`` row."""

    kind: Literal["fill", "submit"]
    started: float
    playbook_id: str | None = None
    browser_cost: int = 0
    model_cost: int = 0
    spent: int = 0
    """How much of ``browser_cost`` is already in the spend ledger (each session is recorded once, cancelled or not)."""


@dataclass(slots=True)
class ApplyEngine:
    repo: ApplyRepo
    limits: SpendLedger | None
    settings: ApplySettings
    opener: PageOpener
    clock: Clock
    answerer: Answerer | None = None
    loop: FieldListLoop | None = None
    credits: ApplyCredits | None = None
    reserve: Reserve | None = None
    resume_for: ResumeSource | None = None
    """The resume of the row's choice (`seams.ResumeSource`); without one, the saved file."""
    facts: FactsSource | None = None
    new_id: Callable[[str], str] = field(default=_new_id)
    submit_wait_s: float = SUBMIT_WAIT_S
    attach_wait_s: float = ATTACH_WAIT_S
    open_wait_s: float = OPEN_WAIT_S
    poll_s: float = 1.0
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)

    async def run(self, app: StoredApplication) -> None:
        if app.stage == "queued":
            await self._fill(app)
        elif app.stage == "approved":
            await self._submit(app)

    # ── credits and spend ──

    def _reserve(self, est_usd: float) -> AbstractAsyncContextManager[None]:
        return (self.reserve or _no_reserve)(est_usd)

    @staticmethod
    def _ref(app: StoredApplication) -> str:
        return app.credit_ref or f"apply:{app.id}"

    async def release_hold(self, app: StoredApplication) -> None:
        """Give the held credit back after a move to failed, closed or cancelled; a failure is left to the sweep."""
        await self._settle(app, "release")

    async def _settle(self, app: StoredApplication, how: Literal["capture", "release"]) -> None:
        if self.credits is None or app.credit_state != "held":
            return  # an unlimited account holds nothing
        ref = self._ref(app)
        try:
            if how == "capture":
                done = await self.credits.capture(app.user_id, ref)
            else:
                done = await self.credits.release(app.user_id, ref)
        except Exception as exc:
            log.error("apply_hold_settle_failed", application_id=app.id, how=how, error=type(exc).__name__)
            return
        if not done:
            log.warning("apply_hold_not_settled", application_id=app.id, how=how)
            return
        state = "charged" if how == "capture" else "released"
        written = await self.repo.update_application(
            app.id, {"credit_state": state}, now=self.clock.now(), expected_stage=app.stage
        )
        if written is None and how == "release" and app.user_id is not None:
            # "Try again" or a fresh start landed between the stage move and this write, counting on this very hold
            await retake_if_reopened(self.repo, self.credits, app.id, app.user_id, ref, self.clock.now())

    async def _spend(self, run: _Run) -> None:
        """Record what the run's browser cost that is not recorded yet (called in the browser's ``finally`` too, so a
        cancelled run, a deploy mid-fill, still reaches the daily ceiling)."""
        cost = run.browser_cost - run.spent
        if cost <= 0 or self.limits is None:
            return
        run.spent = run.browser_cost
        try:
            await self.limits.record_spend(self.clock.now(), cost, BROWSER_USE_SCOPE)
        except Exception as exc:
            log.error("apply_spend_unrecorded", error=type(exc).__name__)

    @staticmethod
    async def _close(driver: BrowserDriver) -> int:
        """Stop the browser (never raises) and read what it cost."""
        try:
            await driver.close()
        except Exception as exc:
            log.warning("apply_browser_close_failed", error=type(exc).__name__)
        try:
            return max(int(driver.cost_micro_usd), 0)
        except Exception:
            return 0

    # ── context ──

    async def _context(self, app: StoredApplication, *, with_facts: bool) -> FillContext:
        profile = await self.repo.get_profile(app.owner_key)
        stored = await self.repo.list_answers(app.user_id)
        bank = bank_of({a.question_key: a for a in stored})
        resume: Resume | None = None
        if self.resume_for is not None:
            resume = await self.resume_for(app)
        elif profile is not None and profile.resume_bytes:
            resume = Resume(
                profile.resume_name or "resume.pdf",
                profile.resume_bytes,
                profile.resume_type or "application/pdf",
            )
        typed = {str(k): str(v) for k, v in ((app.job or {}).get("typed") or {}).items()}
        facts = await self.facts(app) if (with_facts and self.facts is not None) else []
        return FillContext(
            typed=typed, resume=resume, facts=facts, job_title=app.title, company=app.company, bank=bank,
            job_location=app.location, self_id_on_forms=profile.self_id_on_forms if profile else False,
            profile_id=app.profile_id,
        )  # fmt: skip

    async def _playbook(
        self, app: StoredApplication, prefer: str | None = None
    ) -> tuple[Playbook | None, str | None]:
        """The site's own active playbook (the one the review names first), else the built-in one for its ATS."""
        found: list[tuple[Playbook, str]] = []
        for stored in await self.repo.list_playbooks(domain=app.domain, statuses=["active"]):
            try:
                found.append((Playbook.model_validate(stored.spec), stored.id))
            except ValueError:
                log.warning("apply_playbook_unreadable", playbook_id=stored.id)
        builtin = BUILTIN.get(app.ats)
        if builtin is not None:
            found.append((builtin, builtin_id(builtin)))
        for pb, pb_id in found:
            if prefer is not None and pb_id == prefer:
                return pb, pb_id
        if not found:
            return None, None
        return found[0]

    # ── fill ──

    async def _fill(self, app: StoredApplication) -> None:
        run = _Run("fill", time.monotonic())
        attempts_before = app.attempts
        moving = await stages.move(
            self.repo, app, "filling", source="engine", now=self.clock.now(),
            changes={"attempts": app.attempts + 1, "progress": _opening(), "stop_reason": None},
        )  # fmt: skip
        if moving is None:
            return
        app = moving
        try:
            playbook, run.playbook_id = await self._playbook(app)
            ctx = await self._context(app, with_facts=True) if playbook is not None else None
        except Exception as exc:
            # a read before the browser: back to the queue while this go has re-queues left
            log.warning("apply_fill_setup_failed", application_id=app.id, error=type(exc).__name__)
            await self.interrupted(app, run, reason=type(exc).__name__)
            return
        if playbook is None or ctx is None:
            result = FillResult("failed", reason="no_playbook", note="This site is not one we can fill yet.",
                                stop_reason="fill_error")  # fmt: skip
            await self._finish_fill(app, result, run)
            return
        if _choice(app) == "tailored" and ctx.resume is None:
            await self._resume_not_ready(app)
            return
        result_or_none: FillResult | None = None
        try:
            async with self._reserve(self.settings.est_browser_usd):
                driver = await self.opener.new_page("preview")
                try:
                    await self.repo.update_application(
                        app.id, {"progress": _reading()}, now=self.clock.now(), expected_stage="filling"
                    )
                    result_or_none = await preview(
                        driver, playbook, _url(app), ctx, self.answerer, self.loop, request_id=app.id,
                        open_wait_s=self.open_wait_s,
                    )  # fmt: skip
                finally:
                    run.browser_cost = await self._close(driver)
                    await self._spend(run)
        except GuardNotActive as exc:
            run.browser_cost = max(run.browser_cost, open_cost(exc))
            log.warning("apply_guard_not_active", application_id=app.id, run_kind="fill")
            result_or_none = FillResult(
                "failed", reason="guard_probe", stop_reason="guard_probe",
                note="The browser's safety check failed, so nothing was filled. Try again later.",
            )  # fmt: skip
        except (SpendCeilingReached, BrowserUseBusy) as exc:
            run.browser_cost = max(run.browser_cost, open_cost(exc))
            await self._spend(run)
            log.info("apply_fill_deferred", application_id=app.id, reason=type(exc).__name__)
            await stages.move(
                self.repo, app, "queued", source="engine", now=self.clock.now(), note="Waiting for capacity.",
                changes={"attempts": attempts_before, "progress": None},
            )  # fmt: skip
            await self._metric(app, run, "playbook", "deferred", _deferred_reason(exc), 0)
            raise  # the worker backs off (a 429, or the spend ceiling): no hot loop on the same row
        except Exception as exc:
            run.browser_cost = max(run.browser_cost, open_cost(exc))
            await self._spend(run)
            log.warning("apply_fill_interrupted", application_id=app.id, error=type(exc).__name__)
            await self.interrupted(app, run, reason=type(exc).__name__)
            return
        await self._spend(run)
        result = result_or_none
        if result is None:  # pragma: no cover - every path above sets it or returns
            return
        run.model_cost = result.cost_micro_usd
        await self._finish_fill(app, result, run)

    async def interrupted(
        self, app: StoredApplication, run: _Run | None = None, *, reason: str = "lease_expired"
    ) -> None:
        """A fill that stopped half way (an error, an expired lease): back to the queue while this go of fills has
        re-queues left (``attempts`` counts the fills since the last one that ended, `ApplySettings.requeue_cap`), else
        failed with the hold released. Either way the run gets its ``run_metrics`` row (outcome ``interrupted``)."""
        now = self.clock.now()
        if app.attempts <= self.settings.requeue_cap:
            await stages.move(
                self.repo, app, "queued", source="engine", now=now, note="The fill stopped; queued again.",
                changes={"progress": None},
            )  # fmt: skip
        else:
            moved = await stages.move(
                self.repo, app, "failed", source="engine", now=now, note="The fill stopped too many times.",
                changes={"progress": None, "stop_reason": "fill_error", "manual_url": _url(app), "attempts": 0,
                         "stage_reason": "The form could not be filled. Try again, or open it on the employer's site."},
            )  # fmt: skip
            if moved is not None:
                await self.release_hold(moved)
        await self._metric(app, run or _Run("fill", time.monotonic()), "playbook", "interrupted", reason, 0)

    async def _resume_not_ready(self, app: StoredApplication) -> None:
        """A tailored resume that cannot be exported yet (open questions on it): a need, never the saved file instead."""
        need = NeedsInput(
            key=TAILORED_NEED_KEY, label="Finish your resume first", kind="file", required=True,
            reason="Your tailored resume still has open questions. Settle them on the match, then fill this again.",
            questionKey=TAILORED_NEED_KEY, perOffer=True, requiredBy=app.company or None,
        )  # fmt: skip
        await stages.move(
            self.repo, app, "waiting_for_you", source="engine", now=self.clock.now(), note="The resume is not ready.",
            changes={"needs": [need.to_dict()], "stop_reason": "questions", "progress": None, "attempts": 0,
                     "stage_reason": "Finish your tailored resume first.", "approved_at": None,
                     "approved_digest": None},
        )  # fmt: skip

    async def _finish_fill(self, app: StoredApplication, result: FillResult, run: _Run) -> None:
        now = self.clock.now()
        if result.screenshot is not None:
            await self.repo.put_screenshot(app.id, result.screenshot)
        release = False
        changes: dict[str, Any]
        if result.status in ("ready", "questions"):
            review = review_of(
                result, captured_at=now.isoformat(), resume_choice=_choice(app), playbook_id=run.playbook_id
            )
            waiting = result.status == "questions"
            to = "waiting_for_you"
            changes = {
                "review": review.to_dict(), "review_digest": review.digest,
                "needs": [n.to_dict() for n in result.needs], "approved_at": None, "approved_digest": None,
                "stop_reason": "questions" if waiting else None,
                "stage_reason": "Some questions need your answer." if waiting else None,
                "progress": filled_progress(result),
            }  # fmt: skip
        elif result.status == "needs_signin":
            to = "needs_signin"
            changes = {"stop_reason": result.stop_reason or "signin", "manual_url": _url(app),
                       "stage_reason": result.note, "progress": None}  # fmt: skip
        elif result.status == "closed":
            to, release = "closed", True
            changes = {"stage_reason": result.note, "stop_reason": None, "progress": None}
        else:
            to, release = "failed", True
            changes = {"stage_reason": result.note or "The form could not be filled.", "progress": None,
                       "stop_reason": result.stop_reason or "fill_error", "manual_url": _url(app)}  # fmt: skip
        changes["attempts"] = 0  # this fill ended: the next go of fills starts its own count
        moved = await stages.move(
            self.repo, app, to, source="engine", now=now, note=result.note, changes=changes
        )
        if moved is not None and release:
            await self.release_hold(moved)
        plan = result.plan
        await self._metric(
            app, run, result.mode, FILL_OUTCOME.get(result.status, result.status), result.reason, result.steps,
            total=result.fields_total, filled=len(plan.filled) if plan else len(result.fields),
            model=plan.model_fields if plan else 0,
        )  # fmt: skip

    # ── submit ──

    async def _submit(self, app: StoredApplication) -> None:
        run = _Run("submit", time.monotonic())
        now = self.clock.now()
        if not app.review or not app.approved_digest or app.approved_digest != app.review_digest:
            await stages.move(
                self.repo, app, "waiting_for_you", source="engine", now=now,
                note="The approval does not match the review.",
                changes={"approved_at": None, "approved_digest": None, "stage_reason": "Approve the form again."},
            )  # fmt: skip
            return
        try:
            review = ApplicationReview.model_validate(app.review)
        except ValidationError:
            await self._submit_failed(
                app, "The review could not be read. Nothing was sent: fill it again.", "fill_error", run
            )
            return
        try:
            playbook, run.playbook_id = await self._playbook(app, review.playbookId)
            ctx = await self._context(app, with_facts=False) if playbook is not None else None
        except Exception as exc:
            # before the browser and the marker: nothing was sent. Failed (hold released), never left approved at the
            # head of the queue to fail again on every pass
            log.warning("apply_submit_setup_failed", application_id=app.id, error=type(exc).__name__)
            await self._submit_failed(
                app,
                f"Sending did not start ({type(exc).__name__}). Nothing was sent.",
                "fill_error",
                run,
                "error",
            )
            return
        if playbook is None or ctx is None:
            await self._submit_failed(
                app, "This site is not one we can send to. Nothing was sent.", "fill_error", run
            )
            return
        current = app
        marked = False

        async def mark_submitting() -> bool:
            nonlocal current, marked
            at = self.clock.now()
            moved = await stages.move(
                self.repo, app, "submitting", source="engine", now=at, note="Sending.",
                changes={"submitting_at": at, "stop_reason": None},
            )  # fmt: skip
            if moved is None:
                return False  # the person cancelled meanwhile: nothing is clicked
            current, marked = moved, True
            return True

        hosts: Collection[str] = [*playbook.submit_hosts, *playbook.upload_hosts]
        result_or_none: SubmitResult | None = None
        try:
            async with self._reserve(self.settings.est_browser_usd):
                driver = await self.opener.new_page("submit", hosts)
                try:
                    result_or_none = await submit_approved(
                        driver, review, ctx, url=_url(app), playbook=playbook, mark_submitting=mark_submitting,
                        resume=ctx.resume, wait_s=self.submit_wait_s, attach_wait_s=self.attach_wait_s,
                        open_wait_s=self.open_wait_s, poll_s=self.poll_s, sleep=self.sleep,
                    )  # fmt: skip
                finally:
                    run.browser_cost = await self._close(driver)
                    await self._spend(run)
        except GuardNotActive as exc:
            run.browser_cost = max(run.browser_cost, open_cost(exc))
            log.warning("apply_guard_not_active", application_id=app.id, run_kind="submit")
            result_or_none = SubmitResult(
                "failed", "guard_probe", "The browser's safety check failed. Nothing was sent."
            )
        except (SpendCeilingReached, BrowserUseBusy) as exc:
            run.browser_cost = max(run.browser_cost, open_cost(exc))
            await self._spend(run)
            log.info("apply_submit_deferred", application_id=app.id, reason=type(exc).__name__)
            if marked:  # not reached in practice: the reservation and the browser come before the marker
                await self._check_this_one(current, "error", STOPPED_AFTER_SENDING, None, run)
                return
            await self._metric(app, run, "playbook", "deferred", _deferred_reason(exc), 0)
            raise  # still approved: a later pass sends it, after the worker's back-off
        except Exception as exc:
            run.browser_cost = max(run.browser_cost, open_cost(exc))
            await self._spend(run)
            log.warning(
                "apply_submit_stopped", application_id=app.id, marked=marked, error=type(exc).__name__
            )
            if marked:
                await self._check_this_one(current, "error", STOPPED_AFTER_SENDING, None, run)
            else:
                note = f"Sending stopped ({type(exc).__name__}). Nothing was sent."
                await self._submit_failed(app, note, "fill_error", run, "error")
            return
        await self._spend(run)
        result = result_or_none
        if result is None:  # pragma: no cover - every path above sets it or returns
            return
        await self._finish_submit(app, current, marked, result, run)

    async def _finish_submit(
        self,
        app: StoredApplication,
        current: StoredApplication,
        marked: bool,
        result: SubmitResult,
        run: _Run,
    ) -> None:
        now = self.clock.now()
        if result.status == "sent" and marked:
            moved = await stages.move(
                self.repo, current, "sent", source="engine", now=now, note="Sent.",
                changes={"submitted_at": now, "stage_reason": None, "needs": None, "stop_reason": None},
            )  # fmt: skip
            if result.screenshot is not None:
                await self.repo.put_confirmation(app.id, result.screenshot)
            if moved is not None:
                await self._settle(moved, "capture")
            else:
                log.error("apply_sent_move_lost", application_id=app.id)
            await self._metric(app, run, "playbook", "success", None, result.steps)
        elif marked and result.status != "failed":
            # check_this_one, or anything else once the marker is written: the person checks it, never a resend
            await self._check_this_one(
                current, result.reason, result.note, result.screenshot, run, result.steps
            )
        elif result.status == "form_changed" and result.review is not None:
            review = review_of(
                result.review,
                captured_at=now.isoformat(),
                resume_choice=_choice(app),
                playbook_id=run.playbook_id,
            )
            if result.screenshot is not None:
                await self.repo.put_screenshot(app.id, result.screenshot)
            await stages.move(
                self.repo, app, "waiting_for_you", source="engine", now=now, note=result.note,
                changes={"review": review.to_dict(), "review_digest": review.digest,
                         "needs": [n.to_dict() for n in result.review.needs], "approved_at": None,
                         "approved_digest": None, "stop_reason": "form_changed",
                         "stage_reason": result.note or "The form changed since you approved it: check it again."},
            )  # fmt: skip
            await self._metric(app, run, "playbook", "questions", "form_changed", result.steps)
        elif result.reason == "not_marked":
            # the row moved before the marker (the person cancelled): it is theirs now, nothing to change
            await self._metric(app, run, "playbook", "failed", "not_marked", result.steps)
        else:
            stop = _STOP_OF_REASON.get(result.reason or "", "fill_error")
            note = result.note or "Sending did not start. Nothing was sent."
            await self._submit_failed(
                current if marked else app, note, stop, run, result.reason, result.steps
            )

    async def _check_this_one(
        self, app: StoredApplication, reason: str | None, note: str | None, shot: bytes | None, run: _Run,
        steps: int = 0,
    ) -> None:  # fmt: skip
        if shot is not None:
            await self.repo.put_confirmation(app.id, shot)
        stop = _STOP_OF_REASON.get(reason or "")
        await stages.move(
            self.repo, app, "check_this_one", source="engine", now=self.clock.now(), note=note,
            changes={"stage_reason": note or STOPPED_AFTER_SENDING, "manual_url": _url(app),
                     "stop_reason": stop if stop in ("verification_code", "challenge") else None},
        )  # fmt: skip
        await self._metric(app, run, "playbook", "check_this_one", reason or "error", steps)

    async def _submit_failed(
        self, app: StoredApplication, note: str, stop: StopReason, run: _Run, reason: str | None = None,
        steps: int = 0,
    ) -> None:  # fmt: skip
        moved = await stages.move(
            self.repo, app, "failed", source="engine", now=self.clock.now(), note=note,
            changes={"stage_reason": note, "stop_reason": stop, "manual_url": _url(app),
                     "approved_at": None, "approved_digest": None},
        )  # fmt: skip
        if moved is not None:
            await self.release_hold(moved)
        await self._metric(app, run, "playbook", "failed", reason or stop, steps)

    # ── metrics ──

    async def _metric(
        self, app: StoredApplication, run: _Run, mode: RunMode, outcome: str, reason: str | None, steps: int, *,
        total: int = 0, filled: int = 0, model: int = 0,
    ) -> None:  # fmt: skip
        pb_id = run.playbook_id
        status = None if pb_id is None else ("builtin" if pb_id.startswith("builtin:") else "active")
        try:
            await self.repo.add_run_metric(
                StoredRunMetric(
                    id=self.new_id("run"), application_id=app.id, created_at=self.clock.now(), run_kind=run.kind,
                    ats=app.ats, domain=app.domain, mode=mode, outcome=outcome, playbook_id=pb_id,
                    playbook_status=status, failure_reason=reason if outcome != "success" else None, steps=steps,
                    duration_ms=int((time.monotonic() - run.started) * 1000),
                    cost_micro_usd=run.browser_cost + run.model_cost, fields_total=total, fields_filled=filled,
                    fields_model=model,
                )
            )  # fmt: skip
        except Exception as exc:
            log.error("apply_metric_unrecorded", application_id=app.id, error=type(exc).__name__)


__all__ = [
    "BROWSER_USE_SCOPE",
    "FILL_OUTCOME",
    "STOPPED_AFTER_SENDING",
    "TAILORED_NEED_KEY",
    "ApplyEngine",
    "FactsSource",
    "PageOpener",
    "SpendLedger",
    "filled_progress",
]
