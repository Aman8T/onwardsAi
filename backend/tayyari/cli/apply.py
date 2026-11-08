"""``apply-worker`` and ``apply-metrics``: auto-apply's background worker and its learning-loop numbers.

``apply-worker`` builds a `Container` on the configured database and runs the same worker the web app starts
(`container.build_apply_worker`: Browser Use standalone browsers, the field-list loop and the answerer on the guarded
Claude client, the credit hold), until interrupted (``--once``: until nothing is left to do). It needs
``AUTO_APPLY_ENABLED=true`` and ``BROWSER_USE_API_KEY``; no local browser is installed, because the worker connects to the
hosted browser over CDP. ``TAYYARI_TEST_BROWSER=1`` drives a local Chromium instead (offline runs; ``--headed`` shows
it). Use it when ``TAYYARI_APPLY__WORKER_IN_APP=false`` keeps the worker out of the web app. ``apply-metrics`` prints
playbook vs model runs, outcomes and failure reasons per ATS (no key needed).
"""

from __future__ import annotations

import asyncio
import json

from tayyari.container import Container, apply_worker_skip_reason, build_apply_worker
from tayyari.core.settings import get_settings


async def _worker(once: bool, headed: bool) -> int:
    settings = get_settings()
    if headed:
        settings = settings.model_copy(
            update={"apply": settings.apply.model_copy(update={"headless": False})}
        )
    container = await Container.build(settings)
    try:
        reason = apply_worker_skip_reason(container, in_app=False)
        if reason is not None:
            print(f"The apply worker does not start: {reason}.")
            return 2
        worker = build_apply_worker(container)
        print(
            f"apply worker {worker.name}: polling every {settings.apply.worker_poll_seconds}s (Ctrl+C to stop)"
        )
        if once:
            while await worker.run_once():
                pass
        else:
            await worker.run_forever()
    finally:
        await container.aclose()
    return 0


def run_worker(once: bool = False, headed: bool = False) -> int:
    try:
        return asyncio.run(_worker(once, headed))
    except KeyboardInterrupt:
        return 0


async def _metrics(days: int, as_json: bool) -> int:
    from tayyari.apply.metrics import compute_metrics

    settings = get_settings()
    container = await Container.build(settings)
    try:
        metrics = await compute_metrics(container.repos.apply, container.clock.now(), days)
    finally:
        await container.aclose()
    if as_json:
        print(json.dumps(metrics.to_dict(), indent=2))
        return 0
    print(f"Runs since {metrics.since[:10]} (fill and submit):")
    print(f"{'ATS':<28}{'runs':>6}{'playbook':>10}{'model':>7}{'model %':>9}{'success':>9}{'cost $':>9}")
    for row in [*metrics.byAts, metrics.total]:
        print(f"{row.ats:<28}{row.runs:>6}{row.playbookRuns:>10}{row.aiRuns:>7}{row.aiShare:>9.0%}{row.successRate:>9.0%}"
              f"{row.costMicroUsd / 1e6:>9.3f}")  # fmt: skip
    if metrics.failureReasons:
        print("Failure reasons: " + ", ".join(f"{f.reason} {f.count}" for f in metrics.failureReasons))
    for pb in metrics.playbooks:
        print(
            f"playbook {pb.id} {pb.ats} {pb.domain or ''} {pb.status} v{pb.version} ok={pb.successes} failed={pb.failures}"
        )
    return 0


def run_metrics(days: int = 30, as_json: bool = False) -> int:
    return asyncio.run(_metrics(days, as_json))
