"""The analysis service as the API layer sees it: a Protocol, so routes are tested with fakes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from tayyari.contract.events import AnalysisEvent
from tayyari.contract.requests import AnalyzeRequest, RequestContext


@runtime_checkable
class AnalysisService(Protocol):
    """Turns one `AnalyzeRequest` into a stream of `AnalysisEvent`.

    The stream ends with exactly one `DoneEvent` or `ErrorEvent`. Expected failures (an unreadable
    resume, a model that gave no usable answer, a spend ceiling) are reported as an `ErrorEvent`, not
    raised; ``asyncio.CancelledError`` (the client went away) is allowed to propagate.
    """

    def run(self, req: AnalyzeRequest, rc: RequestContext) -> AsyncIterator[AnalysisEvent]: ...
