"""The engine on fakes (spec 3.1 steps 2 and 5): a queued Greenhouse application is filled to waiting_for_you with its
review script and digest and nothing sent; a login wall gives needs_signin; a posting that is gone is closed and its hold
released; a probe that is not blocked fails with nothing filled; a refused spend reservation leaves the row queued with no
attempt counted. An approved row is sent once (the hold captured, the confirmation stored); a changed form goes back to the
person; a row cancelled before the marker is never clicked. Every browser session is reserved before it opens and stopped
on every path, and its cost is recorded. The last test drives a real Chromium against the saved page (offline) when
TAYYARI_TEST_BROWSER=1."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tayyari.apply.driver import OpenMode, carry_open_cost
from tayyari.apply.engine import TAILORED_NEED_KEY, ApplyEngine
from tayyari.apply.worker import ApplyWorker
from tayyari.core.settings import ApplySettings
from tayyari.llm.errors import SpendCeilingReached
from tayyari.storage.ports import StoredAnswer, StoredApplication, account_owner_key
from tests.fakes.apply import FakeApplyRepo
from tests.fakes.apply_browser import FakeDriver, FakeOpener

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"
GH = (DATA / "greenhouse_job.html").read_text(encoding="utf-8")
T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
USER = "user-riya"
OWNER = account_owner_key(USER)
URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"
TYPED = {"question_102": "I build payment APIs and want to work on Acme's platform."}
SETTINGS = ApplySettings(domain_gap_seconds=0, max_per_domain_per_hour=0)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        self.t += timedelta(seconds=30)
        return self.t

    def monotonic(self) -> float:
        return 0.0

    async def sleep(self, seconds: float) -> None:
        return None


class Credits:
    """`seams.ApplyCredits` that records each call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def hold(self, user_id: str, ref: str) -> None:
        self.calls.append(("hold", ref))

    async def capture(self, user_id: str, ref: str) -> bool:
        self.calls.append(("capture", ref))
        return True

    async def release(self, user_id: str, ref: str) -> bool:
        self.calls.append(("release", ref))
        return True


class Spend:
    """The reserve and the spend ledger: records the order of reservations and page opens; ``refuse`` says no."""

    def __init__(self, log: list[str], *, refuse: bool = False) -> None:
        self.log, self.refuse = log, refuse
        self.recorded: list[tuple[int, str]] = []

    @asynccontextmanager
    async def reserve(self, est_usd: float) -> AsyncIterator[None]:
        if self.refuse:
            raise SpendCeilingReached(60.0)
        self.log.append(f"reserve:{est_usd}")
        try:
            yield
        finally:
            self.log.append("unreserve")

    async def record_spend(self, at: datetime, micro_usd: int, scope: str) -> None:
        self.recorded.append((micro_usd, scope))


class Opener(FakeOpener):
    """`FakeOpener` that logs each open in the shared order log, and each driver's close. ``fail`` makes the next
    opening raise it, with ``fail_cost`` on it the way `BrowserUseOpener` puts a stopped browser's cost on any error."""

    def __init__(self, page: Any, log: list[str], **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self.log = log
        self.fail: BaseException | None = None
        self.fail_cost = 0

    async def new_page(self, mode: OpenMode = "preview", write_hosts: Collection[str] = ()) -> FakeDriver:
        self.log.append(f"open:{mode}")
        if self.fail is not None:
            failure, self.fail = self.fail, None
            carry_open_cost(failure, self.fail_cost)
            raise failure
        return await super().new_page(mode, write_hosts)


def application(**fields: Any) -> StoredApplication:
    values: dict[str, Any] = {
        "id": "ap1", "owner_key": OWNER, "user_id": USER, "source": "match", "job_url": URL, "apply_url": URL,
        "domain": "job-boards.greenhouse.io", "ats": "greenhouse", "stage": "queued", "created_at": T0,
        "updated_at": T0, "title": "Backend Engineer", "company": "Acme", "job": {"typed": dict(TYPED)},
        "resume_choice": "saved", "credit_ref": "apply:ap1", "credit_state": "held",
    }  # fmt: skip
    values.update(fields)
    return StoredApplication(**values)


class Env:
    def __init__(self, page: Any = GH, *, refuse: bool = False, **opener_kw: Any) -> None:
        self.repo = FakeApplyRepo()
        self.log: list[str] = []
        self.spend = Spend(self.log, refuse=refuse)
        self.credits = Credits()
        opener_kw.setdefault("url", URL)
        self.opener = Opener(page, self.log, **opener_kw)
        self.engine = ApplyEngine(
            self.repo, self.spend, SETTINGS, self.opener, Clock(), credits=self.credits, reserve=self.spend.reserve,
            submit_wait_s=0.0, attach_wait_s=0.0, open_wait_s=0.0, sleep=no_sleep,
        )  # fmt: skip
        self.worker = ApplyWorker(self.engine)

    async def seed(self, **fields: Any) -> None:
        for key, value in (("fullName", "Riya Kapoor"), ("email", "riya@example.com")):
            await self.repo.put_answer(
                StoredAnswer(user_id=USER, question_key=key, question=key, source="you", ask_each_time=False,
                             answered_at=T0, created_at=T0, updated_at=T0, value=value)
            )  # fmt: skip
        await self.repo.put_answer(
            StoredAnswer(user_id=USER, question_key="needsSponsorship:IN", question="Sponsorship in India",
                         source="first_check", ask_each_time=False, answered_at=T0, created_at=T0, updated_at=T0,
                         value="No")
        )  # fmt: skip
        await self.repo.put_resume(
            OWNER,
            user_id=USER,
            name="Riya-Kapoor.pdf",
            media_type="application/pdf",
            data=b"%PDF-1.7 cv",
            now=T0,
        )
        await self.repo.create_application(application(**fields))

    async def app(self) -> StoredApplication:
        found = await self.repo.get_application("ap1", OWNER)
        assert found is not None
        return found

    async def approve(self) -> None:
        app = await self.app()
        await self.repo.update_application(
            "ap1", {"stage": "approved", "approved_at": T0, "approved_digest": app.review_digest}, now=T0
        )

    def stages(self) -> list[str | None]:
        return [e.to_stage for e in self.repo.events if e.application_id == "ap1"]


async def no_sleep(_: float) -> None:
    return None


# ───────────────────────── fill ─────────────────────────


async def test_a_fill_reaches_waiting_for_you_with_the_review_script_and_digest_and_sends_nothing() -> None:
    env = Env(cost_micro_usd=7000)
    await env.seed(job={})  # no typed answer: the custom question is the person's
    assert await env.worker.run_once() is True
    app = await env.app()
    assert app.stage == "waiting_for_you" and app.stop_reason == "questions" and app.lease_owner is None
    assert app.review is not None and app.review_digest == app.review["digest"] and app.review["script"]
    assert {s["op"] for s in app.review["script"]} <= {"fill", "select", "check", "choose", "group"}
    # attempts count the fills of one go: a fill that ended starts the next go at 0 (H#3)
    assert app.review["resume"]["name"] == "Riya-Kapoor.pdf" and app.attempts == 0
    assert [n["key"] for n in app.needs or []] == ["question_102"] and app.needs[0]["questionKey"]  # type: ignore[index]
    progress = app.progress or {}
    assert (
        progress["sections"][-1]["label"] == "Questions for you" and progress["sections"][-1]["waiting"] == 1
    )
    assert await env.repo.get_screenshot("ap1", OWNER)
    driver = env.opener.drivers[0]
    assert driver.closed and driver.sent_writes() == [] and not driver.submitted and driver.allow_calls == []
    assert env.opener.opened == [("preview", frozenset())]
    assert env.stages() == ["filling", "waiting_for_you"] and env.credits.calls == []
    assert env.spend.recorded == [(7000, "browser_use")]
    (metric,) = env.repo.metrics
    assert (metric.run_kind, metric.mode, metric.outcome, metric.cost_micro_usd) == (
        "fill",
        "playbook",
        "questions",
        7000,
    )
    assert await env.worker.run_once() is False  # waiting for the person: nothing to run


async def test_a_typed_answer_gives_a_review_with_nothing_left_to_answer() -> None:
    env = Env()
    await env.seed()
    await env.worker.run_once()
    app = await env.app()
    assert app.stage == "waiting_for_you" and app.stop_reason is None and app.stage_reason is None
    assert not [n for n in app.needs or [] if n.get("required")]
    assert env.repo.metrics[0].outcome == "success"


async def test_a_login_wall_gives_needs_signin_with_the_link_and_keeps_the_hold() -> None:
    env = Env('<html><body><form><input type="password" name="pw"></form></body></html>')
    await env.seed()
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.stop_reason, app.manual_url, app.credit_state) == (
        "needs_signin",
        "signin",
        URL,
        "held",
    )
    assert env.credits.calls == [] and env.opener.drivers[0].closed


async def test_a_closed_posting_releases_the_hold() -> None:
    env = Env(status=404)
    await env.seed()
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.credit_state) == ("closed", "released")
    assert env.credits.calls == [("release", "apply:ap1")] and env.opener.drivers[0].closed
    assert env.repo.metrics[0].outcome == "closed"


async def test_a_form_that_never_opens_fails_before_any_write_and_releases_the_hold() -> None:
    env = Env("<html><body><p>Hello</p></body></html>")
    await env.seed()
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.stop_reason, app.credit_state) == ("failed", "fill_error", "released")
    assert env.opener.drivers[0].sent_writes() == [] and env.credits.calls == [("release", "apply:ap1")]


async def test_a_probe_that_is_not_blocked_fails_with_guard_probe_and_nothing_filled() -> None:
    env = Env(guard_active=False)
    await env.seed()
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.stop_reason, app.credit_state) == ("failed", "guard_probe", "released")
    driver = env.opener.drivers[0]
    assert driver.closed and not [a for a in driver.actions if a[0] != "goto"] and driver.actions == []
    assert app.review is None and env.repo.metrics[0].failure_reason == "guard_probe"


async def test_a_refused_spend_reservation_leaves_the_row_queued_with_no_attempt_counted() -> None:
    env = Env(refuse=True)
    await env.seed()
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.attempts, app.credit_state) == ("queued", 0, "held")
    assert env.opener.opened == [] and env.credits.calls == []
    # a deferred fill still gets its run row, which the per-site limit leaves out (it never reached the site)
    assert [(m.outcome, m.failure_reason, m.cost_micro_usd) for m in env.repo.metrics] == [
        ("deferred", "spend_ceiling", 0)
    ]
    assert await env.repo.count_domain_runs_since("job-boards.greenhouse.io", T0) == 0


class Raising:
    """An answerer whose model call is refused by the spend ceiling, after the browser opened."""

    async def answer(self, questions: Any, ctx: Any) -> dict[str, dict[str, Any]]:
        raise SpendCeilingReached(60.0)


async def test_a_model_call_refused_mid_fill_puts_the_row_back_and_still_stops_the_browser() -> None:
    env = Env(cost_micro_usd=500)
    await env.seed(job={})
    env.engine.answerer = Raising()
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.attempts) == ("queued", 0)
    assert env.opener.drivers[0].closed and env.spend.recorded == [(500, "browser_use")]


async def test_a_tailored_resume_that_cannot_be_exported_becomes_a_need_and_opens_no_browser() -> None:
    env = Env()
    await env.seed(resume_choice="tailored")

    async def blocked(_: StoredApplication) -> None:
        return None

    env.engine.resume_for = blocked
    await env.worker.run_once()
    app = await env.app()
    assert app.stage == "waiting_for_you" and [n["questionKey"] for n in app.needs or []] == [
        TAILORED_NEED_KEY
    ]
    assert env.opener.opened == [] and env.credits.calls == []


# ───────────────────────── submit ─────────────────────────


async def test_an_approved_review_is_sent_once_the_hold_captured_and_the_confirmation_stored() -> None:
    env = Env(cost_micro_usd=3000)
    await env.seed()
    await env.worker.run_once()
    await env.approve()
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.credit_state) == ("sent", "charged") and app.submitted_at and app.submitting_at
    assert env.credits.calls == [("capture", "apply:ap1")]
    assert await env.repo.get_confirmation("ap1", OWNER) and app.has_confirmation
    send = env.opener.drivers[-1]
    assert env.opener.opened[-1] == (
        "submit",
        frozenset({"boards.greenhouse.io", "job-boards.greenhouse.io"}),
    )
    assert send.submitted and [a[0] for a in send.actions].count("click_submit") == 1 and send.closed
    assert env.stages() == ["filling", "waiting_for_you", "submitting", "sent"]
    assert [(m.run_kind, m.outcome) for m in env.repo.metrics] == [("fill", "success"), ("submit", "success")]
    assert env.spend.recorded == [(3000, "browser_use"), (3000, "browser_use")]


async def test_the_reserve_is_entered_before_every_new_page_and_left_after_the_browser_stops() -> None:
    env = Env()
    await env.seed()
    await env.worker.run_once()
    await env.approve()
    await env.worker.run_once()
    assert env.log == [
        f"reserve:{SETTINGS.est_browser_usd}", "open:preview", "unreserve",
        f"reserve:{SETTINGS.est_browser_usd}", "open:submit", "unreserve",
    ]  # fmt: skip
    assert all(d.closed for d in env.opener.drivers)


async def test_a_changed_form_goes_back_to_waiting_for_you_with_the_approval_cleared() -> None:
    env = Env()
    await env.seed()
    await env.worker.run_once()
    await env.approve()
    first_digest = (await env.app()).review_digest
    env.opener.page = GH.replace(
        '<div class="g-recaptcha"',
        '<div class="field"><label for="question_104">Years of SQL<span class="required">*</span></label>'
        '<input id="question_104" name="question_104" type="text" aria-required="true"></div><div class="g-recaptcha"',
    )
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.stop_reason, app.approved_digest, app.approved_at) == (
        "waiting_for_you", "form_changed", None, None,
    )  # fmt: skip
    assert app.review_digest not in (None, first_digest) and [n["key"] for n in app.needs or []] == [
        "question_104"
    ]
    assert not env.opener.drivers[-1].submitted and env.credits.calls == [] and app.credit_state == "held"


class CancelOnOpen(Opener):
    """The person cancels while the submit browser opens: the marker's compare-and-set then finds the row moved."""

    def __init__(self, page: Any, log: list[str], repo: FakeApplyRepo, **kwargs: Any) -> None:
        super().__init__(page, log, **kwargs)
        self.repo = repo

    async def new_page(self, mode: OpenMode = "preview", write_hosts: Collection[str] = ()) -> FakeDriver:
        if mode == "submit":
            await self.repo.update_application(
                "ap1", {"stage": "cancelled"}, now=T0, expected_stage="approved"
            )
        return await super().new_page(mode, write_hosts)


async def test_a_row_cancelled_before_the_marker_is_never_clicked() -> None:
    env = Env()
    await env.seed()
    env.opener = env.engine.opener = CancelOnOpen(GH, env.log, env.repo, url=URL)
    await env.worker.run_once()
    await env.approve()
    await env.worker.run_once()
    app = await env.app()
    send = env.opener.drivers[-1]
    assert (
        app.stage == "cancelled" and not send.submitted and "click_submit" not in [a[0] for a in send.actions]
    )
    assert send.allow_calls == [] and send.closed and env.credits.calls == []  # the cancel route releases it


async def test_no_confirmation_after_the_click_is_check_this_one_and_keeps_the_hold() -> None:
    quiet = "<html><body><p>Working on it</p></body></html>"
    env = Env(after_submit=quiet, after_submit_url=URL)
    await env.seed()
    await env.worker.run_once()
    await env.approve()
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.credit_state, app.manual_url) == ("check_this_one", "held", URL)
    assert env.credits.calls == [] and env.repo.metrics[-1].outcome == "check_this_one"


async def test_a_submit_that_fails_before_the_click_releases_the_hold() -> None:
    env = Env()
    await env.seed()
    await env.worker.run_once()
    await env.approve()
    env.opener.page = GH.replace(
        '<button type="submit" class="btn btn--pill">Submit application</button>', ""
    )
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.credit_state) == ("failed", "released") and app.submitting_at is None
    assert env.credits.calls == [("release", "apply:ap1")] and not env.opener.drivers[-1].submitted


async def test_an_approval_of_another_digest_is_never_sent() -> None:
    env = Env()
    await env.seed()
    await env.worker.run_once()
    await env.repo.update_application("ap1", {"stage": "approved", "approved_digest": "f" * 64}, now=T0)
    await env.worker.run_once()
    app = await env.app()
    assert app.stage == "waiting_for_you" and app.approved_digest is None
    assert len(env.opener.drivers) == 1  # no submit browser was opened


async def test_an_unlimited_account_without_a_hold_is_never_sent_to_the_ledger() -> None:
    env = Env()
    await env.seed(credit_state=None, credit_ref=None)
    await env.worker.run_once()
    await env.approve()
    await env.worker.run_once()
    assert (await env.app()).stage == "sent" and env.credits.calls == []


@pytest.mark.skipif(
    os.environ.get("TAYYARI_TEST_BROWSER") != "1",
    reason="set TAYYARI_TEST_BROWSER=1 to drive a real Chromium",
)
async def test_real_chromium_fills_and_sends_the_saved_page_offline() -> None:
    from tayyari.apply.browser import PlaywrightBrowser

    posts: list[str] = []

    def responder(method: str, url: str) -> tuple[int, str, str] | None:
        if method == "GET" and url.startswith(URL):
            return 200, "text/html", GH
        if method == "POST" and "greenhouse.io" in url:
            posts.append(url)
            return 200, "text/html", "<html><body><h1>Thank you for applying!</h1></body></html>"
        return None

    env = Env()
    await env.seed()
    browser = PlaywrightBrowser(ApplySettings(), responder)
    env.engine.opener = browser
    try:
        await env.worker.run_once()
        app = await env.app()
        assert app.stage == "waiting_for_you", app.stage_reason
        assert posts == []
        await env.approve()
        await env.worker.run_once()
        assert (await env.app()).stage in ("sent", "check_this_one")
    finally:
        await browser.close()


# ───────────────────────── interrupted fills (H#3, H#4) ─────────────────────────


async def _answered(env: Env) -> None:
    """The person answered: the row goes back to the queue (what `ApplyService.answer` does)."""
    app = await env.app()
    await env.repo.update_application(
        "ap1", {"stage": "queued", "job": {"typed": dict(TYPED)}}, now=app.updated_at
    )


async def test_a_row_that_filled_normally_once_is_not_failed_early() -> None:
    env = Env(cost_micro_usd=100)
    env.engine.settings = ApplySettings(domain_gap_seconds=0, max_per_domain_per_hour=0, requeue_cap=1)
    await env.seed(job={})
    await env.worker.run_once()
    assert (await env.app()).stage == "waiting_for_you"  # a normal fill: this go of fills is over
    await _answered(env)
    env.opener.fail, env.opener.fail_cost = ConnectionError("the browser went away"), 1_200
    await env.worker.run_once()
    app = await env.app()
    # lifetime attempts would be 2 > cap 1 and fail the row; per-fill attempts are 1: queued again
    assert (app.stage, app.attempts, app.credit_state) == ("queued", 1, "held")
    env.opener.fail, env.opener.fail_cost = ConnectionError("again"), 800
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.stop_reason, app.credit_state) == ("failed", "fill_error", "released")
    assert env.credits.calls == [("release", "apply:ap1")]


async def test_a_browser_that_failed_to_open_is_recorded_as_spend_and_the_run_as_interrupted() -> None:
    env = Env()
    await env.seed()
    env.opener.fail, env.opener.fail_cost = RuntimeError("cdp connect failed"), 2_500
    await env.worker.run_once()
    app = await env.app()
    assert app.stage == "queued" and env.spend.recorded == [(2_500, "browser_use")]
    (metric,) = env.repo.metrics
    assert (metric.outcome, metric.failure_reason, metric.cost_micro_usd) == (
        "interrupted",
        "RuntimeError",
        2_500,
    )


async def test_an_expired_fill_lease_writes_an_interrupted_run() -> None:
    env = Env()
    await env.seed(stage="filling", attempts=1, lease_until=T0 - timedelta(minutes=1))
    await env.worker.sweep()
    app = await env.app()
    assert app.stage == "queued"
    (metric,) = env.repo.metrics
    assert (metric.outcome, metric.failure_reason, metric.cost_micro_usd) == (
        "interrupted",
        "lease_expired",
        0,
    )


# ───────────────────────── A1 review fixes ─────────────────────────


class Cancelled:
    """An answerer whose call is cancelled: the worker task stopped mid-fill (a deploy, a restart)."""

    async def answer(self, questions: Any, ctx: Any) -> dict[str, dict[str, Any]]:
        raise asyncio.CancelledError


async def test_a_cancelled_run_still_records_what_its_browser_cost() -> None:
    env = Env(cost_micro_usd=700)
    await env.seed(job={})
    env.engine.answerer = Cancelled()
    with pytest.raises(asyncio.CancelledError):
        await env.engine.run(await env.app())
    assert env.opener.drivers[0].closed and env.spend.recorded == [(700, "browser_use")]


async def test_a_submit_whose_setup_raises_fails_before_any_browser_and_never_loops() -> None:
    """A resume that cannot be rendered at submit time: failed with the hold released, never left approved at the head
    of the queue to fail again on every pass."""
    env = Env()
    await env.seed()
    await env.worker.run_once()
    await env.approve()

    async def broken(_: StoredApplication) -> None:
        raise ValueError("cannot render")

    env.engine.resume_for = broken
    opened = len(env.opener.opened)
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.credit_state, app.approved_digest) == ("failed", "released", None)
    assert len(env.opener.opened) == opened and env.credits.calls == [("release", "apply:ap1")]
    assert await env.worker.run_once() is False  # nothing runnable is left behind


async def test_a_fill_whose_setup_raises_is_an_interrupted_fill() -> None:
    env = Env()
    await env.seed()

    async def broken(_: StoredApplication) -> None:
        raise RuntimeError("db hiccup")

    env.engine.resume_for = broken
    await env.worker.run_once()
    app = await env.app()
    assert (app.stage, app.attempts) == ("queued", 1) and env.opener.opened == []
    assert [(m.outcome, m.failure_reason) for m in env.repo.metrics] == [("interrupted", "RuntimeError")]


class RetriedDuringRelease(FakeApplyRepo):
    """ "Try again" lands between the engine's move to closed and its credit write-back, trusting ``held``."""

    async def update_application(self, application_id: str, changes: Any, *, now: datetime,
                                 expected_stage: str | None = None) -> StoredApplication | None:  # fmt: skip
        if dict(changes) == {"credit_state": "released"} and expected_stage == "closed":
            await super().update_application(
                application_id, {"stage": "queued"}, now=now, expected_stage="closed"
            )
        return await super().update_application(
            application_id, changes, now=now, expected_stage=expected_stage
        )


async def test_a_release_that_races_a_retry_takes_the_hold_again() -> None:
    env = Env(status=404)
    env.repo = env.engine.repo = RetriedDuringRelease()
    await env.seed()
    await env.engine.run(await env.app())
    app = await env.app()
    assert (app.stage, app.credit_state) == ("queued", "held")
    assert env.credits.calls == [("release", "apply:ap1"), ("hold", "apply:ap1")]
