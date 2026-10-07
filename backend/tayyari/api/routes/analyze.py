"""POST /api/analyze: a multipart form in, an NDJSON stream of `AnalysisEvent` out.

The order of the guards is part of the contract (everything before the stream is a JSON error):

1. ``rate_limiter.shed``: the in-memory bucket turns hammering away before the body is read.
2. ``parse_analyze_form``: streamed multipart with hard caps (413 / 400).
3. a quick look at the per-process slots, so a busy server does not charge the caller's rate-limit windows.
4. ``rate_limiter.check(memory=False)``: the shared per-IP and global counters (429).
5. ``spend_guard.admit``: the daily spend ceiling (503 with ``Retry-After`` until UTC midnight).
6. one of `max_concurrent_analyses` slots, taken without waiting (429 "busy").

The slot is given back when the response is over: the stream finished, the producer crashed, or the client
went away. It is tied to the response object (not to the event generator), because a generator that never
started cannot run its ``finally``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from tayyari.api.deps import get_request_context
from tayyari.api.lifespan import get_container
from tayyari.api.ndjson import NdjsonResponse
from tayyari.api.uploads import parse_analyze_form
from tayyari.contract.events import AnalysisEvent, ChatEvent
from tayyari.core.errors import RateLimited

router = APIRouter()

TAG = "analyze"
BUSY_MESSAGE = "Tayyari is busy, try again in a minute."
BUSY_RETRY_AFTER_S = 30.0


class _SlotResponse(NdjsonResponse):
    """An `NdjsonResponse` that runs ``release`` once it is over, however it ends."""

    def __init__(
        self,
        request: Request,
        events: AsyncIterator[AnalysisEvent | ChatEvent],
        tag: str,
        release: Callable[[], None],
    ) -> None:
        super().__init__(request, events, tag)
        self._release = release

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


def _busy() -> RateLimited:
    return RateLimited(BUSY_MESSAGE, code="busy", retry_after_s=BUSY_RETRY_AFTER_S)


@router.post("/api/analyze")
async def analyze(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    limiter = container.rate_limiter
    limiter.shed("analyze", rc.ip_hash)
    req = await parse_analyze_form(request)

    slots = container.analysis_slots
    if slots.locked():
        raise _busy()
    await limiter.check("analyze", rc.ip_hash, memory=False)
    await container.spend_guard.admit(container.settings.guard.est_analyze_usd)

    # No await between this check and the acquire, so the slot is ours (the semaphore is never waited on).
    if slots.locked():
        raise _busy()
    await slots.acquire()
    released = False

    def release() -> None:
        nonlocal released
        if not released:
            released = True
            slots.release()

    try:
        return _SlotResponse(request, container.analysis.run(req, rc), TAG, release)
    except BaseException:
        release()
        raise


__all__ = ["BUSY_MESSAGE", "BUSY_RETRY_AFTER_S", "router"]
