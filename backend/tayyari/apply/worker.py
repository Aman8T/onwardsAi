"""The apply worker: claims runnable applications (a lease in the ``applications`` table) and runs the engine on each, one at a
time, outside the web request path. Per-site politeness: a site over `max_per_domain_per_hour` runs, or run less than
`domain_gap_seconds` ago, is skipped (its application waits for a later pass).
"""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import timedelta

from tayyari.apply.engine import ApplyEngine
from tayyari.core.logging import get_logger

log = get_logger(__name__)


class ApplyWorker:
    def __init__(self, engine: ApplyEngine) -> None:
        self.engine = engine
        self.name = f"{socket.gethostname()[:40]}:{os.getpid()}"
        self._wait_until: dict[str, float] = {}

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

    async def run_once(self) -> bool:
        """Claim and run one application; False when there was nothing to do."""
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
