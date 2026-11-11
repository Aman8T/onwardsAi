"""POST /api/analyze: a multipart form in, an NDJSON stream of `AnalysisEvent` out.

The order of the guards is part of the contract (everything before the stream is a JSON error):

0. ``deps.unlimited``: an account on the unlimited list (``TAYYARI_GUARD__UNLIMITED_EMAILS``) skips steps 1 and 4; steps 2,
   3, 5 and 6 (the upload limits, the spend ceiling, the slots) apply to everyone.
1. ``rate_limiter.shed``: the in-memory bucket turns hammering away before the body is read.
2. ``parse_analyze_form`` (and, for ``fromProfile``, ``resolve_reread``: the access rule, 404 or 409 before any cost): streamed multipart with hard caps (413 / 400), a deadline for the whole read (408),
   one upload being read per client (429) and a process-wide budget of buffered upload bytes (503).
3. a quick look at the per-process slots, so a busy server does not charge the caller's rate-limit windows.
4. ``rate_limiter.check(memory=False)``: the shared per-IP and global counters (429).
5. ``spend_guard.reserve``: the daily spend ceiling (503 with ``Retry-After`` until UTC midnight). Unlike a
   one-off check, the estimate stays reserved against the ceiling until the stream is over, so analyses that
   start together cannot all be admitted by the same stale ledger reading.
6. one of `max_concurrent_analyses` slots, taken without waiting (429 "busy").

The reservation and the slot are given back when the response is over: the stream finished, the producer
crashed, or the client went away. They are tied to the response object (not to the event generator), because
a generator that never started cannot run its ``finally``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

from fastapi import APIRouter, Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from tayyari.api.deps import get_request_context, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.ndjson import NdjsonResponse, StreamEvent
from tayyari.api.reread import resolve_reread
from tayyari.api.security import subnet_hash
from tayyari.api.uploads import parse_analyze_form, upload_gate
from tayyari.core.errors import RateLimited

router = APIRouter()

TAG = "analyze"
BUSY_MESSAGE = "Leafout is busy, try again in a minute."
BUSY_RETRY_AFTER_S = 30.0


class HeldResponse(NdjsonResponse):
    """An `NdjsonResponse` that closes ``held`` (the guard slots and reservations it was given) once it is over,
    however it ends: finished, failed, the client left or the server cancelled it."""

    def __init__(
        self,
        request: Request,
        events: AsyncIterator[StreamEvent],
        tag: str,
        held: AsyncExitStack,
    ) -> None:
        super().__init__(request, events, tag)
        self._held = held

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._held.aclose()


def _busy() -> RateLimited:
    return RateLimited(BUSY_MESSAGE, code="busy", retry_after_s=BUSY_RETRY_AFTER_S)


@router.post("/api/analyze")
async def analyze(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    limiter = container.rate_limiter
    guard = container.settings.guard
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    free = await unlimited(request, container)
    limiter.shed("analyze", rc.ip_hash, ip_hash_48=ip48, exempt=free)
    req = await parse_analyze_form(
        request,
        read_timeout_s=guard.upload_read_timeout_s,
        gate=upload_gate(request, guard),
        client_key=rc.ip_hash,
    )
    if req.reread is not None:  # ``fromProfile``: who may open it, then the inputs (404 / 409 cost nothing)
        req = await resolve_reread(request, container, req)

    slots = container.analysis_slots
    if slots.locked():
        raise _busy()
    await limiter.check("analyze", rc.ip_hash, ip_hash_48=ip48, memory=False, exempt=free)

    held = AsyncExitStack()
    try:
        await held.enter_async_context(container.spend_guard.reserve(guard.est_analyze_usd))
        # No await between this check and the acquire, so the slot is ours (the semaphore is never waited on).
        if slots.locked():
            raise _busy()
        await slots.acquire()
        held.callback(slots.release)
        return HeldResponse(request, container.analysis.run(req, rc), TAG, held)
    except BaseException:
        await held.aclose()
        raise


__all__ = ["BUSY_MESSAGE", "BUSY_RETRY_AFTER_S", "HeldResponse", "router"]
