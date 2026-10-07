"""The chat service as the API layer sees it: a Protocol, so routes are tested with fakes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from tayyari.contract.events import ChatEvent
from tayyari.contract.requests import ChatRequestBody, RequestContext


@runtime_checkable
class ChatService(Protocol):
    """Streams one coach turn as `ChatEvent`.

    The stream ends with exactly one `ChatDoneEvent` (carrying the thread's ``sessionId``) or one
    `ChatErrorEvent`. Expected failures (unknown profile, refusal, spend ceiling) are reported as an error
    event, not raised; ``asyncio.CancelledError`` (the client went away) is allowed to propagate.
    """

    def stream_turn(self, req: ChatRequestBody, rc: RequestContext) -> AsyncIterator[ChatEvent]: ...
