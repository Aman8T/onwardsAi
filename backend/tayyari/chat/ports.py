"""The chat service as the API layer sees it: a Protocol, so routes are tested with fakes. And the resume in view, as the
coach sees it: a Protocol too, so the chat service never imports the resume or the match."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from tayyari.contract.events import ChatEvent
from tayyari.contract.requests import ChatRequestBody, RequestContext


class ViewedLine(Protocol):
    """One line of the resume in view."""

    @property
    def id(self) -> str:
        """The line's ``bulletId`` on its resume: what the ``edit`` op addresses."""
        ...

    @property
    def heading(self) -> str:
        """The job, project or degree it sits under, as one line (``"Growth Manager, Acme, 2020-2023"``)."""
        ...

    @property
    def text(self) -> str:
        """Its words now."""
        ...

    @property
    def quotes(self) -> Sequence[str]:
        """The quotes of the facts it rests on (the words of its sources), the person's documents copied as data."""
        ...


class ResumeInView(Protocol):
    """A resume the caller may open, as the coach is shown it. Built by the resume service for the API layer, which has
    already decided that this caller may open it."""

    @property
    def resume_id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def lines(self) -> Sequence[ViewedLine]:
        """The lines of the draft in reading order."""
        ...

    def check(self, line_id: str, text: str) -> str | None:
        """None when ``text`` may stand as the new words of the line ``line_id`` (it rests only on the facts the line cites:
        no figure, scope verb, term or name those facts lack), else a short slug saying why not (``unsupported_number``,
        ``unsupported_scope``, ``unsupported_term``, ``unsupported_name``, ``unsupported`` or ``unknown_line``)."""
        ...


@runtime_checkable
class ChatService(Protocol):
    """Streams one coach turn as `ChatEvent`.

    The stream ends with exactly one `ChatDoneEvent` (carrying the thread's ``sessionId``) or one `ChatErrorEvent`. Expected
    failures (unknown profile, refusal, spend ceiling) are reported as an error event, not raised; ``asyncio.CancelledError``
    (the client went away) is allowed to propagate.

    ``resume`` is the resume the request names (`ChatRequestBody.resume`) when the caller may open it, else None: the API
    layer decides that, and passes the keyword only when there is one.
    """

    def stream_turn(
        self, req: ChatRequestBody, rc: RequestContext, *, resume: ResumeInView | None = None
    ) -> AsyncIterator[ChatEvent]: ...


__all__ = ["ChatService", "ResumeInView", "ViewedLine"]
