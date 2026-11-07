"""The seams auto-apply is handed from outside, so it never imports billing, guard or match (tests/unit/test_layering.py).

`container.py` (the composition root) builds each one from the real service and passes it in; tests pass small fakes.

    Reserve         the spend guard's reservation, the `weekly.compose` pattern: ``async with reserve(est_usd): ...``
                    around every browser session and every model call. A refused reservation raises
                    `llm.errors.SpendCeilingReached`, and the row stays queued with no attempt counted.
    ApplyCredits    the credit hold on the append-only credit ledger: hold at start, capture when the application is
                    sent, release when it was not. Every call is keyed by the ref ``apply:<application id>`` and is
                    idempotent, so the maintenance sweep can reconcile a capture or release that failed.
    FactSink        writes an allowlisted answer (notice period, languages, education and marks, registrations and
                    licences, work authorisation per country) into the person's facts as a sentence built in code. Never
                    CTC, date of birth, gender, self-ID or anything in `contract.apply.NEVER_STORED_KEYS`.
    ResumeSource    the resume that goes with one application: the match's tailored resume exported to PDF, or the saved
                    file. None when there is none to send.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from tayyari.apply.answers import Resume
from tayyari.storage.ports import StoredApplication

Reserve = Callable[[float], AbstractAsyncContextManager[None]]
"""``reserve(est_usd)``: an async context manager that holds ``est_usd`` of the daily spend ceiling while it is open."""


class ApplyCredits(Protocol):
    """The credit hold for one application, keyed by ``ref`` (``apply:<application id>``)."""

    async def hold(self, user_id: str, ref: str) -> None:
        """Hold a credit (or record a free application). Raises `core.errors.PaymentRequired` when there is none; a ref
        that already holds is a no-op."""
        ...

    async def capture(self, user_id: str, ref: str) -> bool:
        """Charge the held credit; False when nothing is held under ``ref`` (already captured or released)."""
        ...

    async def release(self, user_id: str, ref: str) -> bool:
        """Give the held credit back; False when nothing is held under ``ref`` (already captured or released)."""
        ...


class FactSink(Protocol):
    """Adds one allowlisted answer to the person's facts, as a sentence built in code."""

    async def __call__(self, user_id: str, profile_id: str, question_key: str, sentence: str) -> None: ...


ResumeSource = Callable[[StoredApplication], Awaitable[Resume | None]]
"""The resume to attach for ``application`` (its ``resume_choice``), or None."""


__all__ = ["ApplyCredits", "FactSink", "Reserve", "ResumeSource"]
