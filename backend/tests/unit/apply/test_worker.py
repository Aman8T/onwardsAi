"""The worker's pass: the sweep of rows a run left behind (a stale ``submitting`` row becomes check_this_one, a stale
``filling`` row is queued again, or failed with its hold released once it ran out of re-queues), the global back-off when
Browser Use answers 429, and the per-site cap."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from tayyari.apply import wake
from tayyari.apply.browser_use import BrowserUseBusy
from tayyari.apply.driver import OpenMode
from tayyari.apply.engine import ApplyEngine
from tayyari.apply.worker import ApplyWorker
from tayyari.core.settings import ApplySettings
from tayyari.llm.errors import SpendCeilingReached
from tayyari.storage.ports import StoredApplication, StoredRunMetric, account_owner_key
from tests.fakes.apply import FakeApplyRepo
from tests.fakes.apply_browser import FakeDriver, FakeOpener

T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
USER = "user-riya"
OWNER = account_owner_key(USER)
URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        return self.t

    def monotonic(self) -> float:
        return 0.0

    async def sleep(self, seconds: float) -> None:
        return None


class Credits:
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


class Busy(FakeOpener):
    """Browser Use answering 429: no browser is created."""

    def __init__(self) -> None:
        super().__init__("<html></html>")
        self.tries = 0

    async def new_page(self, mode: OpenMode = "preview", write_hosts: Collection[str] = ()) -> FakeDriver:
        self.tries += 1
        raise BrowserUseBusy("create_browser", 429)


class Monotonic:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def row(app_id: str, stage: str, **fields: Any) -> StoredApplication:
    values: dict[str, Any] = {
        "id": app_id, "owner_key": OWNER, "user_id": USER, "source": "match", "job_url": URL, "apply_url": URL,
        "domain": "job-boards.greenhouse.io", "ats": "greenhouse", "stage": stage, "created_at": T0, "updated_at": T0,
        "credit_ref": f"apply:{app_id}", "credit_state": "held",
    }  # fmt: skip
    values.update(fields)
    return StoredApplication(**values)


def setup(
    opener: FakeOpener | None = None, *, monotonic: Monotonic | None = None, **settings: Any
) -> tuple[FakeApplyRepo, ApplyWorker, Clock, Credits]:
    repo, clock, credits = FakeApplyRepo(), Clock(), Credits()
    config = ApplySettings(**{"domain_gap_seconds": 0, "max_per_domain_per_hour": 0, **settings})
    engine = ApplyEngine(repo, None, config, opener or FakeOpener("<html></html>"), clock, credits=credits)
    return repo, ApplyWorker(engine, monotonic=monotonic or Monotonic()), clock, credits


async def stage_of(repo: FakeApplyRepo, app_id: str) -> str:
    app = await repo.get_application(app_id, None)
    assert app is not None
    return app.stage


async def test_a_stale_submitting_row_becomes_check_this_one_and_is_never_run_again() -> None:
    repo, worker, _, credits = setup()
    await repo.create_application(row("stale", "submitting", submitting_at=T0, lease_owner="dead:1",
                                      lease_until=T0 - timedelta(seconds=1)))  # fmt: skip
    await repo.create_application(row("live", "submitting", submitting_at=T0, lease_owner="other:2",
                                      lease_until=T0 + timedelta(minutes=5)))  # fmt: skip
    assert await worker.sweep() == 1
    stale = await repo.get_application("stale", None)
    assert stale is not None and stale.stage == "check_this_one" and stale.manual_url == URL
    assert "after starting to send" in (stale.stage_reason or "")
    assert await stage_of(repo, "live") == "submitting"  # another worker still holds it
    assert [e.detail for e in repo.events if e.application_id == "stale"] == [
        {"note": "the run stopped after starting to send"}
    ]
    assert credits.calls == []  # the hold stays until the person resolves it
    assert await worker.run_once() is False  # nothing runnable: a submitting row is never claimed


async def test_a_stale_filling_row_is_queued_again_until_it_runs_out_of_requeues() -> None:
    repo, worker, _, credits = setup(requeue_cap=2)
    await repo.create_application(row("again", "filling", attempts=2, progress={"sections": []}))
    await repo.create_application(row("spent", "filling", attempts=3))
    assert await worker.sweep() == 2
    again = await repo.get_application("again", None)
    assert again is not None and (again.stage, again.progress, again.attempts) == ("queued", None, 2)
    spent = await repo.get_application("spent", None)
    assert spent is not None and (spent.stage, spent.stop_reason, spent.credit_state) == (
        "failed", "fill_error", "released",
    )  # fmt: skip
    assert credits.calls == [("release", "apply:spent")]


async def test_a_browser_use_429_backs_off_every_claim_until_it_passes() -> None:
    clock_mono = Monotonic()
    opener = Busy()
    repo, worker, _, _ = setup(opener, monotonic=clock_mono, worker_backoff_s=60.0)
    await repo.create_application(row("ap1", "queued"))
    assert await worker.run_once() is True
    app = await repo.get_application("ap1", None)
    assert app is not None and (app.stage, app.attempts, app.lease_owner) == ("queued", 0, None)
    assert worker.backing_off and opener.tries == 1
    clock_mono.t += 30
    assert await worker.run_once() is False and opener.tries == 1  # still backing off: nothing claimed
    clock_mono.t += 31
    assert not worker.backing_off
    await worker.run_once()
    assert opener.tries == 2


async def test_a_refused_spend_reservation_backs_off_instead_of_claiming_the_row_again() -> None:
    clock_mono = Monotonic()
    repo, worker, _, _ = setup(monotonic=clock_mono, worker_backoff_s=60.0)

    @asynccontextmanager
    async def refuse(_est: float) -> AsyncIterator[None]:
        raise SpendCeilingReached(60.0)
        yield  # pragma: no cover

    worker.engine.reserve = refuse
    await repo.create_application(row("ap1", "queued"))
    await repo.create_application(
        row("ap2", "approved", review={"x": 1}, review_digest="d", approved_digest="d")
    )
    assert await worker.run_once() is True
    app = await repo.get_application("ap1", None)
    assert app is not None and (app.stage, app.attempts, app.lease_owner) == ("queued", 0, None)
    events = len(repo.events)
    assert worker.backing_off
    assert await worker.run_once() is False and len(repo.events) == events  # nothing claimed, nothing written
    clock_mono.t += 61
    assert not worker.backing_off


async def test_the_per_domain_cap_holds_a_run_back() -> None:
    repo, worker, _, _ = setup(max_per_domain_per_hour=1)
    await repo.add_run_metric(
        StoredRunMetric(id="r1", created_at=T0 - timedelta(minutes=10), run_kind="fill", ats="greenhouse",
                        domain="job-boards.greenhouse.io", mode="playbook", outcome="success")
    )  # fmt: skip
    await repo.create_application(row("ap1", "queued"))
    assert await worker.run_once() is True
    app = await repo.get_application("ap1", None)
    assert app is not None and app.stage == "queued" and app.attempts == 0 and app.lease_owner is None
    assert await worker.run_once() is False  # the site is skipped for a while


async def test_the_per_site_cap_is_per_employer_not_per_ats_host() -> None:
    """Every Lever posting is on jobs.lever.co: the cap counts the employer's board, so Acme's runs never hold Beta's."""
    repo, worker, _, _ = setup(max_per_domain_per_hour=1)
    await repo.add_run_metric(
        StoredRunMetric(id="r1", created_at=T0 - timedelta(minutes=10), run_kind="fill", ats="lever",
                        domain="jobs.lever.co/acme", mode="playbook", outcome="success")
    )  # fmt: skip
    assert await worker._over_limit("jobs.lever.co/acme") is True
    assert await worker._over_limit("jobs.lever.co/beta") is False


class Passes(ApplyWorker):
    """Counts the passes; never finds work."""

    def __init__(self, engine: ApplyEngine) -> None:
        super().__init__(engine)
        self.passes = 0

    async def run_once(self) -> bool:
        self.passes += 1
        return False


async def test_an_empty_queue_backs_off_and_a_poke_wakes_the_worker() -> None:
    _, plain, _, _ = setup(worker_poll_seconds=0.1, worker_idle_max_seconds=0.4)
    worker = Passes(plain.engine)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop))
    await asyncio.sleep(1.2)
    # waits of 0.1, 0.2, 0.4, 0.4 ...: far fewer passes than one every 0.1 s
    idle_passes = worker.passes
    assert 3 <= idle_passes <= 6, idle_passes
    await asyncio.sleep(0.05)
    before = worker.passes
    wake.poke()
    await asyncio.sleep(0.15)  # within one poll step
    assert worker.passes > before
    stop.set()
    await asyncio.wait_for(task, timeout=1)
