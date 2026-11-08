"""A submit happens once (spec 1 rule 5, 7). The process dies right after the click: the row is left in ``submitting``,
and the next pass, once the lease has expired, turns it into ``check_this_one`` without opening another browser, so the
form was clicked exactly once. A crash before the marker leaves the row ``approved`` and runnable: it is sent on the
next pass, still with one click."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tayyari.apply.driver import OpenMode
from tayyari.apply.engine import ApplyEngine
from tayyari.apply.worker import ApplyWorker
from tayyari.core.settings import ApplySettings
from tayyari.storage.ports import StoredAnswer, StoredApplication, account_owner_key
from tests.fakes.apply import FakeApplyRepo
from tests.fakes.apply_browser import FakeDriver, FakeOpener

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"
GH = (DATA / "greenhouse_job.html").read_text(encoding="utf-8")
T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
USER = "user-riya"
OWNER = account_owner_key(USER)
URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"
SETTINGS = ApplySettings(domain_gap_seconds=0, max_per_domain_per_hour=0, worker_lease_seconds=600)


class Crash(BaseException):
    """The process going away (a kill, a deploy): nothing below the worker gets to handle it."""


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t

    def monotonic(self) -> float:
        return 0.0

    async def sleep(self, seconds: float) -> None:
        return None


class Credits:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def hold(self, user_id: str, ref: str) -> None:
        self.calls.append("hold")

    async def capture(self, user_id: str, ref: str) -> bool:
        self.calls.append("capture")
        return True

    async def release(self, user_id: str, ref: str) -> bool:
        self.calls.append("release")
        return True


class CrashOnce(FakeDriver):
    """A submit browser that dies once, at the chosen moment."""

    crash_at: str = ""
    crashed = False

    async def fill(self, selector: str, value: str, frame: int = 0) -> None:
        if CrashOnce.crash_at == "replay" and not CrashOnce.crashed:
            CrashOnce.crashed = True
            raise Crash
        await super().fill(selector, value, frame)


class Opener(FakeOpener):
    """Every submit browser is a `CrashOnce`; the clicks of all of them are counted."""

    def __init__(self, page: str, *, after_click: BaseException | None = None) -> None:
        super().__init__(page, url=URL)
        self.after_click = after_click
        self.submit_opens = 0

    async def new_page(self, mode: OpenMode = "preview", write_hosts: Collection[str] = ()) -> FakeDriver:
        if mode == "preview":
            return await super().new_page(mode, write_hosts)
        self.submit_opens += 1
        self.opened.append((mode, frozenset(write_hosts)))
        raise_after = self.after_click if self.submit_opens == 1 else None
        driver = CrashOnce(self.page, url=URL, raise_after_click=raise_after)
        self.drivers.append(driver)
        return driver

    def clicks(self) -> int:
        return sum(1 for d in self.drivers for a in d.actions if a[0] == "click_submit")


async def setup(opener: Opener, clock: Clock) -> tuple[FakeApplyRepo, ApplyWorker, Credits]:
    repo = FakeApplyRepo()
    await repo.put_details(OWNER, user_id=USER, now=T0, details={"firstName": "Riya", "lastName": "Kapoor",
                                                                 "email": "riya@example.com"})  # fmt: skip
    await repo.put_answer(
        StoredAnswer(user_id=USER, question_key="needsSponsorship:IN", question="Sponsorship in India",
                     source="first_check", ask_each_time=False, answered_at=T0, created_at=T0, updated_at=T0, value="No")
    )  # fmt: skip
    await repo.put_resume(OWNER, user_id=USER, name="Riya-Kapoor.pdf", media_type="application/pdf",
                          data=b"%PDF-1.7 cv", now=T0)  # fmt: skip
    await repo.create_application(
        StoredApplication(
            id="ap1", owner_key=OWNER, user_id=USER, source="match", job_url=URL, apply_url=URL,
            domain="job-boards.greenhouse.io", ats="greenhouse", stage="queued", created_at=T0, updated_at=T0,
            company="Acme", job={"typed": {"question_102": "I build payment APIs."}}, resume_choice="saved",
            credit_ref="apply:ap1", credit_state="held",
        )
    )  # fmt: skip
    credits = Credits()

    async def no_sleep(_: float) -> None:
        return None

    engine = ApplyEngine(repo, None, SETTINGS, opener, clock, credits=credits, submit_wait_s=0.0,
                         attach_wait_s=0.0, open_wait_s=0.0, sleep=no_sleep)  # fmt: skip
    worker = ApplyWorker(engine)
    await worker.run_once()  # the fill
    app = await repo.get_application("ap1", OWNER)
    assert app is not None
    assert app.stage == "waiting_for_you", app.needs
    await repo.update_application(
        "ap1", {"stage": "approved", "approved_at": T0, "approved_digest": app.review_digest}, now=T0
    )
    return repo, worker, credits


async def run_crashing(worker: ApplyWorker) -> None:
    try:
        await worker.run_once()
    except Crash:
        return
    raise AssertionError("the run did not crash")


async def test_a_crash_after_the_click_is_never_clicked_again_and_ends_in_check_this_one() -> None:
    clock = Clock()
    opener = Opener(GH, after_click=Crash())
    repo, worker, credits = await setup(opener, clock)
    await run_crashing(worker)
    left = await repo.get_application("ap1", OWNER)
    assert left is not None and left.stage == "submitting" and left.submitting_at is not None
    assert opener.clicks() == 1 and opener.drivers[-1].closed  # the browser was still stopped

    # a dead process keeps its lease until it expires: nothing happens to the row before then
    await repo.update_application("ap1", {"lease_owner": "dead:1", "lease_until": clock.t + timedelta(minutes=5)},
                                  now=clock.t)  # fmt: skip
    assert await worker.run_once() is False
    held = await repo.get_application("ap1", OWNER)
    assert held is not None and held.stage == "submitting"

    clock.t += timedelta(minutes=11)  # the lease has expired
    await worker.run_once()
    await worker.run_once()
    app = await repo.get_application("ap1", OWNER)
    assert app is not None and app.stage == "check_this_one" and app.manual_url == URL
    assert "after starting to send" in (app.stage_reason or "")
    assert opener.clicks() == 1 and opener.submit_opens == 1  # never a second browser, never a second click
    assert credits.calls == []  # the hold waits for the person's answer


async def test_an_error_raised_after_the_click_is_check_this_one_at_once() -> None:
    clock = Clock()
    opener = Opener(GH, after_click=ConnectionResetError("reset"))
    repo, worker, credits = await setup(opener, clock)
    await worker.run_once()
    app = await repo.get_application("ap1", OWNER)
    assert app is not None and app.stage == "check_this_one" and opener.clicks() == 1
    await worker.run_once()
    assert opener.clicks() == 1 and credits.calls == []


async def test_a_crash_before_the_marker_leaves_the_row_approved_and_runnable() -> None:
    clock = Clock()
    opener = Opener(GH)
    repo, worker, credits = await setup(opener, clock)
    CrashOnce.crash_at, CrashOnce.crashed = "replay", False
    try:
        await run_crashing(worker)
        left = await repo.get_application("ap1", OWNER)
        assert left is not None and left.stage == "approved" and left.submitting_at is None
        assert opener.clicks() == 0 and opener.drivers[-1].closed
        clock.t += timedelta(minutes=11)
        await worker.run_once()
    finally:
        CrashOnce.crash_at = ""
    app = await repo.get_application("ap1", OWNER)
    assert app is not None and app.stage == "sent" and opener.clicks() == 1 and credits.calls == ["capture"]
