"""``apply-worker`` and ``apply-metrics``: auto-apply's background worker and its learning-loop numbers.

``apply-worker`` builds a `Container` on the configured database and runs the engine on queued and approved applications with
a Playwright Chromium, until interrupted (``--once``: a single pass). It needs ``AUTO_APPLY_ENABLED=true`` and Playwright's
browser (``uv run --directory backend --group worker playwright install chromium`` once). The AI fallback is on for each
adapter whose key is set (GEMINI_API_KEY, ANTHROPIC_API_KEY); screening answers use the reader model when ANTHROPIC_API_KEY is
set. ``apply-metrics`` prints playbook vs AI runs, success and failure reasons per ATS (no key needed).
"""

from __future__ import annotations

import asyncio
import json

from tayyari.container import Container
from tayyari.core.settings import Settings, get_settings
from tayyari.storage.ports import StoredApplication


def build_engine(container: Container, settings: Settings, browser: object) -> object:
    from tayyari.apply.answers import ClaudeAnswerer
    from tayyari.apply.engine import ApplyEngine

    async def facts(app: StoredApplication) -> list[dict[str, str]]:
        if not app.match_id:
            return []
        stored = await container.repos.matches.get(app.match_id, app.user_id)
        rows = (stored.facts.get("facts") if stored else None) or []
        return [{"id": str(r.get("id")), "text": str(r.get("text", ""))} for r in rows if isinstance(r, dict)]

    answerer = None
    if settings.anthropic_api_key is not None:
        answerer = ClaudeAnswerer(container.claude, settings.apply.answer_model or settings.reader_model)
    fallback = None
    try:
        from tayyari.apply.agent import build_fallback

        fallback = build_fallback(settings, container)
    except ImportError:
        fallback = None
    return ApplyEngine(
        repo=container.repos.apply, settings=settings.apply, browser=browser, clock=container.clock,  # type: ignore[arg-type]
        answerer=answerer, facts=facts, fallback=fallback,
    )  # fmt: skip


async def _worker(once: bool, headed: bool) -> int:
    from tayyari.apply.browser import PlaywrightBrowser
    from tayyari.apply.worker import ApplyWorker

    settings = get_settings()
    if not settings.auto_apply_enabled:
        print("Auto-apply is off. Set AUTO_APPLY_ENABLED=true to run the worker.")
        return 2
    if headed:
        settings = settings.model_copy(
            update={"apply": settings.apply.model_copy(update={"headless": False})}
        )
    container = await Container.build(settings)
    browser = PlaywrightBrowser(settings.apply)
    try:
        worker = ApplyWorker(build_engine(container, settings, browser))  # type: ignore[arg-type]
        print(
            f"apply worker {worker.name}: polling every {settings.apply.worker_poll_seconds}s (Ctrl+C to stop)"
        )
        if once:
            while await worker.run_once():
                pass
        else:
            await worker.run_forever()
    finally:
        await browser.close()
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
    print(f"{'ATS':<28}{'runs':>6}{'playbook':>10}{'AI':>6}{'AI share':>10}{'success':>9}{'cost $':>9}")
    for row in [*metrics.byAts, metrics.total]:
        print(f"{row.ats:<28}{row.runs:>6}{row.playbookRuns:>10}{row.aiRuns:>6}{row.aiShare:>10.0%}{row.successRate:>9.0%}"
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
