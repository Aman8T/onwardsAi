"""The NDJSON streaming response behind POST /api/analyze and POST /api/chat.

`NdjsonResponse(request, events, tag)` writes one `contract.encode_event` line per event as soon as the
producer yields it, and a blank line (`contract.HEARTBEAT`) after `HEARTBEAT_S` of quiet so an idle proxy does
not close a slow stream (the client skips blank lines). Headers are those of the legacy server:
``application/x-ndjson; charset=utf-8``, ``Cache-Control: no-cache, no-transform``, ``X-Accel-Buffering: no``.

The producer (the analysis pipeline or a chat turn) runs as its own task feeding a queue. It is cancelled as
soon as the client goes away, however that is noticed: a failed send (`ClientDisconnect` / `OSError`), a
disconnect seen while the stream is quiet (polled every `DISCONNECT_POLL_S`), or the server cancelling the task.
A client hanging up is logged as ``[tag] cancelled by client``, never as an error.

A producer that raises (and has not already sent a ``done`` or ``error`` event) ends the stream with an
``error`` event carrying `api.errors.user_message_for` of the exception, and the traceback goes to the log.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator

from starlette.requests import ClientDisconnect, Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from tayyari.api.errors import user_message_for
from tayyari.contract.events import (
    HEARTBEAT,
    AnalysisEvent,
    ChatDoneEvent,
    ChatErrorEvent,
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    encode_event,
    ev_error,
)
from tayyari.core.limits import DISCONNECT_POLL_S, HEARTBEAT_S
from tayyari.core.logging import get_logger

log = get_logger(__name__)

MEDIA_TYPE = "application/x-ndjson; charset=utf-8"
_TERMINAL = (DoneEvent, ErrorEvent, ChatDoneEvent, ChatErrorEvent)


async def _pump(
    events: AsyncIterator[AnalysisEvent | ChatEvent], queue: asyncio.Queue[bytes | None], tag: str
) -> None:
    """Serialise ``events`` into ``queue``; ``None`` marks the end.

    A crash becomes a final ``error`` event unless a terminal event (done or error) was already sent.
    Cancellation is not caught: it ends the producer and the ``finally`` still marks the end.
    """
    terminal = False
    try:
        async for event in events:
            terminal = terminal or isinstance(event, _TERMINAL)
            queue.put_nowait(encode_event(event))
    except Exception as exc:
        log.error(f"[{tag}] stream failed", tag=tag, exc_info=(type(exc), exc, exc.__traceback__))
        if not terminal:
            queue.put_nowait(encode_event(ev_error(user_message_for(exc))))
    finally:
        queue.put_nowait(None)


async def _stop(producer: asyncio.Task[None], events: AsyncIterator[AnalysisEvent | ChatEvent]) -> None:
    """Cancel the producer, wait until it has cleaned up, then close the event iterator if it can be closed."""
    if not producer.done():
        producer.cancel()
        try:
            await asyncio.wait([producer])
        except asyncio.CancelledError:
            # We are being cancelled ourselves (an anyio scope re-delivers the cancellation). The producer is
            # already cancelled and finishes on its own; the cancellation keeps propagating from the caller.
            return
    aclose = getattr(events, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()


class NdjsonResponse(StreamingResponse):
    """One JSON object per line, flushed as produced, with blank-line heartbeats while quiet.

    ``heartbeat_s`` and ``poll_s`` default to the limits in `core.limits`; tests shrink them.
    """

    def __init__(
        self,
        request: Request,
        events: AsyncIterator[AnalysisEvent | ChatEvent],
        tag: str,
        *,
        heartbeat_s: float = HEARTBEAT_S,
        poll_s: float = DISCONNECT_POLL_S,
    ) -> None:
        self.request, self.events, self.tag = request, events, tag
        self.heartbeat_s, self.poll_s = heartbeat_s, poll_s
        self.finished = False
        """True once the stream ended on its own or the disconnect was already handled."""
        self._cancel_logged = False
        self._lines = self._body()
        super().__init__(
            self._lines,
            media_type=MEDIA_TYPE,
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except (ClientDisconnect, OSError):  # a send failed: the client hung up mid-stream
            if not self.finished:
                self._cancelled()
        finally:
            await self._lines.aclose()

    def _cancelled(self) -> None:
        if not self._cancel_logged:
            self._cancel_logged = True
            log.info(f"[{self.tag}] cancelled by client", tag=self.tag)

    async def _body(self) -> AsyncGenerator[bytes, None]:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        producer = asyncio.create_task(_pump(self.events, queue, self.tag), name=f"ndjson-{self.tag}")
        loop = asyncio.get_running_loop()
        last_write = loop.time()
        try:
            while True:
                try:
                    async with asyncio.timeout(self.poll_s):
                        line = await queue.get()
                except TimeoutError:
                    if await self.request.is_disconnected():
                        self.finished = True
                        self._cancelled()
                        return
                    if loop.time() - last_write >= self.heartbeat_s:
                        last_write = loop.time()
                        yield HEARTBEAT
                    continue
                if line is None:
                    self.finished = True
                    return
                last_write = loop.time()
                yield line
        except asyncio.CancelledError:
            self._cancelled()
            raise
        finally:
            await _stop(producer, self.events)


__all__ = ["MEDIA_TYPE", "NdjsonResponse"]
