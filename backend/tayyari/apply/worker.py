"""The apply worker: claims runnable applications (a lease in the ``applications`` table) and runs the engine on each, one at
a time (concurrency 1), outside the web request path. It runs inside the web app (`api.lifespan`) or as its own process
(``apply-worker``).

Each pass first sweeps the rows a run left behind (`ApplyRepo.list_expired`: a free or expired lease):

- ``submitting`` -> ``check_this_one`` ("the run stopped after starting to send"): the click may have reached the
  employer, so it is never sent again (spec 1 rule 5). `ApplyRepo.claim_next` never hands out a ``submitting`` row.
- ``filling`` -> ``queued`` while the row has re-queues left (`ApplySettings.requeue_cap`), else ``failed`` with its hold
  released (`ApplyEngine.interrupted`).

Browser Use answering 429 after its retries (`BrowserUseBusy`), or a refused spend reservation (`SpendCeilingReached`),
sets a global back-off of ``worker_backoff_s``: no row is claimed until it passes (the engine already put the row back). Per-site politeness: a site over
``max_per_domain_per_hour`` runs, or run less than ``domain_gap_seconds`` ago, is skipped (its application waits).
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from collections.abc import Callable
from datetime import timedelta

from tayyari.apply import stages
from tayyari.apply.browser_use import BrowserUseBusy
from tayyari.apply.engine import STOPPED_AFTER_SENDING, ApplyEngine
from tayyari.core.logging import get_logger
from tayyari.llm.errors import SpendCeilingReached

log = get_logger(__name__)


class ApplyWorker:
    def __init__(self, engine: ApplyEngine, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self.engine = engine
        self.name = f"{socket.gethostname()[:40]}:{os.getpid()}"
        self._monotonic = monotonic
        self._wait_until: dict[str, float] = {}
        self._busy_until = 0.0

    @property
    def backing_off(self) -> bool:
        """True while the back-off (a Browser Use 429, or the spend ceiling) lasts."""
        return self._monotonic() < self._busy_until

    async def _over_limit(self, domain: str) -> bool:
        s, now = self.engine.settings, self.engine.clock.now()
        if (
            s.max_per_domain_per_hour
            and await self.engine.repo.count_domain_runs_since(domain, now - timedelta(hours=1))
            >= s.max_per_domain_per_hour
        ):
            return True
        last = await self.engine.repo.last_domain_run_at(domain)
        return last is not None and (now - last).total_seconds() < s.domain_gap_seconds

    async def sweep(self) -> int:
        """Settle the rows an earlier run left in ``submitting`` or ``filling``; how many moved."""
        repo, now = self.engine.repo, self.engine.clock.now()
        moved = 0
        for app in await repo.list_expired(["submitting"], now=now):
            done = await stages.move(
                repo, app, "check_this_one", source="engine", now=now, note="the run stopped after starting to send",
                changes={"stage_reason": STOPPED_AFTER_SENDING, "manual_url": app.apply_url or app.job_url,
                         "stop_reason": None},
            )  # fmt: skip
            if done is not None:
                moved += 1
                log.warning("apply_stale_submitting", application_id=app.id)
        for app in await repo.list_expired(["filling"], now=now):
            await self.engine.interrupted(app)
            after = await repo.get_application(app.id, None)
            if after is not None and after.stage != "filling":
                moved += 1
                log.info("apply_stale_filling", application_id=app.id, to=after.stage)
        return moved

    async def run_once(self) -> bool:
        """Sweep, then claim and run one application; False when there was nothing to do (or the worker backs off: after a
        429 or a refused reservation the next pass claims nothing, so ``apply-worker --once`` stops and no row loops)."""
        try:
            await self.sweep()
        except Exception:
            log.exception("apply_sweep_failed")
        if self.backing_off:
            return False
        loop = asyncio.get_running_loop()
        excluded = [d for d, until in self._wait_until.items() if until > loop.time()]
        now = self.engine.clock.now()
        app = await self.engine.repo.claim_next(
            now=now, lease_owner=self.name, lease_until=now + timedelta(seconds=self.engine.settings.worker_lease_seconds),
            exclude_domains=excluded,
        )  # fmt: skip
        if app is None:
            return False
        try:
            if await self._over_limit(app.domain):
                self._wait_until[app.domain] = loop.time() + max(self.engine.settings.domain_gap_seconds, 5.0)
                return True
            log.info("apply_run", application_id=app.id, stage=app.stage, ats=app.ats)
            await self.engine.run(app)
        except (BrowserUseBusy, SpendCeilingReached) as exc:
            self._busy_until = self._monotonic() + self.engine.settings.worker_backoff_s
            log.warning(
                "apply_worker_backoff",
                seconds=self.engine.settings.worker_backoff_s,
                reason=type(exc).__name__,
            )
        finally:
            await self.engine.repo.release(app.id, self.name)
        return True

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                busy = await self.run_once()
            except Exception:
                log.exception("apply_worker_pass_failed")
                busy = False
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.engine.settings.worker_poll_seconds)
                except TimeoutError:
                    pass


__all__ = ["ApplyWorker"]
