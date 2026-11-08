"""The app lifespan: build the `Container` at startup, hand its parts to the request layer, close it at shutdown.

Startup runs the migrations (retrying while a suspended Neon database wakes up), seeds the bundled sample
profile and starts the retention task. If any of that fails the lifespan raises, so the process never reports
ready: on a rolling deploy the platform keeps the previous version serving.

The auto-apply worker (`apply.worker.ApplyWorker`, one run at a time) starts as a task only when ``AUTO_APPLY_ENABLED``
is on, ``BROWSER_USE_API_KEY`` is set (or a test hands in an opener) and ``ApplySettings.worker_in_app`` is true
(`container.apply_worker_skip_reason`). It is cancelled and awaited at shutdown, before the container closes: a fill cut
short is queued again by the next pass, a send cut short becomes "check this one", never a second send. The log says
only that it started, or why it was skipped.

What the request layer finds on ``app.state``:

    container    the `Container` (routes read it through `get_container`)
    settings     the `Settings` (set by `create_app` already, so it also exists without a lifespan)
    clock        the `Clock` (``started_at`` of every `RequestContext`)
    ip_hash_key  the HMAC key for client-IP hashes, as bytes (read by `api.deps.get_request_context`)
    apply_worker the running auto-apply worker task, or None when it was skipped
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

import tayyari
from tayyari.container import Container, ContainerOverrides, apply_worker_skip_reason, build_apply_worker
from tayyari.core.errors import ServiceUnavailable
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings

log = get_logger(__name__)

STARTING_MESSAGE = "Leafout is starting up. Please try again in a moment."

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def get_container(request: Request) -> Container:
    """The container the lifespan built. A request that arrives without one (no lifespan ran) gets a 503."""
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, Container):
        raise ServiceUnavailable(STARTING_MESSAGE)
    return container


def log_startup(container: Container, static_dir: Path) -> None:
    """One line with the non-secret facts of this process (which keys exist, never their values)."""
    settings = container.settings
    facts = {
        **settings.summary(),
        "version": tayyari.__version__,
        "storage": container.storage_kind or "sqlite",
        "static_present": (static_dir / "index.html").is_file(),
        "has_sample": container.sample.has_sample(),
        "jev_active": container.jev_active,
    }
    log.info("startup", **facts)
    if settings.anthropic_api_key is None:
        log.warning("anthropic_key_missing", effect="analysis and chat fail until ANTHROPIC_API_KEY is set")


def start_apply_worker(container: Container) -> asyncio.Task[None] | None:
    """The auto-apply worker as a background task, or None (logged with the reason) when it must not run here."""
    reason = apply_worker_skip_reason(container)
    if reason is not None:
        log.info("apply_worker_skipped", reason=reason)
        return None
    try:
        worker = build_apply_worker(container)
    except Exception as exc:  # the rest of the app still serves; only the error's type is logged
        log.error("apply_worker_skipped", reason="the worker could not be built", error=type(exc).__name__)
        return None
    task = asyncio.create_task(worker.run_forever(), name="tayyari-apply-worker")
    log.info("apply_worker_started")
    return task


async def stop_apply_worker(task: asyncio.Task[None] | None) -> None:
    """Cancel the worker task and wait for it (its run closes its browser in a ``finally``)."""
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def build_lifespan(settings: Settings, overrides: ContainerOverrides | None, static_dir: Path) -> Lifespan:
    """The lifespan function `create_app` passes to FastAPI."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await Container.build(settings, overrides=overrides)
        app.state.container = container
        app.state.settings = settings
        app.state.clock = container.clock
        app.state.ip_hash_key = container.ip_hash_key
        app.state.apply_worker = None
        try:
            log_startup(container, static_dir)
            app.state.apply_worker = start_apply_worker(container)
            yield
        finally:
            log.info("shutdown")
            try:
                await stop_apply_worker(app.state.apply_worker)
            finally:
                app.state.apply_worker = None
                await container.aclose()

    return lifespan


__all__ = [
    "STARTING_MESSAGE",
    "build_lifespan",
    "get_container",
    "log_startup",
    "start_apply_worker",
    "stop_apply_worker",
]
