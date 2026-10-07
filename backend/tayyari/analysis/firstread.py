"""Jev's "first read": a few log lines and chips within about two seconds, before any reader has finished.

It starts the moment the resume (or, with no resume, the pasted notes) has been ingested and has text, and runs
alongside the Haiku readers: it is a separate task, so it can never delay them. Its lines animate 0.25 s apart
and its chips go through the same dedupe set as every other signal. A failure, a timeout or an empty answer
just means nothing is shown: the analysis never notices.

When synthesis is about to start and the first read is still running it gets `FIRST_READ_GRACE_S` more, then it
is dropped (the lines and chips it has not sent yet are lost).
"""

from __future__ import annotations

import asyncio

from tayyari.contract import ev_log
from tayyari.core.limits import NOTES_MAX_CHARS, READER_SIGNAL_LABEL_MAX_CHARS
from tayyari.core.logging import get_logger
from tayyari.core.text import clip_words
from tayyari.decisions.service import FirstReadInput
from tayyari.ingest import PlannedSource, SourceDoc

from .normalise import SignalItem
from .runctx import RunContext
from .signals import SignalStream

log = get_logger(__name__)

FIRST_READ_WHO = "Jev"
FIRST_READ_SRC = "first read"
FIRST_READ_LINE_PACE_S = 0.25
FIRST_READ_GRACE_S = 0.5
"""How long a first read that is still running may keep going once all readers are done."""


def _notes_text(run: RunContext) -> str | None:
    text = (run.req.notes or "").strip()[:NOTES_MAX_CHARS]
    return text or None


class FirstRead:
    """Starts and supervises the one first-read task of an analysis."""

    def __init__(self, run: RunContext, signals: SignalStream, planned: list[PlannedSource]) -> None:
        self._run = run
        self._signals = signals
        self._has_resume = any(p.kind == "resume" for p in planned)
        self._started = False
        self._task: asyncio.Task[None] | None = None

    @property
    def started(self) -> bool:
        return self._started

    @property
    def task(self) -> asyncio.Task[None] | None:
        """The running (or finished) first-read task; None before it starts."""
        return self._task

    def on_ingested(self, p: PlannedSource, raw: SourceDoc) -> None:
        """Called for every source right after its ingest; starts the first read when this is the right one.

        - the resume, when it has text (a scanned resume has none: it falls back to the notes);
        - the notes, when there is no resume at all.
        """
        has_text = raw.ok and not raw.error and bool((raw.text or "").strip())
        if p.kind == "resume":
            notes = _notes_text(self._run)
            if has_text:
                self._start(raw.text, notes)
            elif notes:
                self._start(None, notes)
        elif p.kind == "notes" and not self._has_resume and has_text:
            self._start(None, raw.text)

    def _start(self, resume_text: str | None, notes_text: str | None) -> None:
        if self._started:
            return
        self._started = True
        inp = FirstReadInput(
            resume_text=resume_text, notes_text=notes_text, today=self._run.clock.now().date()
        )
        self._task = asyncio.create_task(self._go(inp))

    async def _go(self, inp: FirstReadInput) -> None:
        run = self._run
        try:
            result = await run.decisions.first_read(inp, run.decision_ctx)
        except Exception as exc:
            log.warning("first_read_failed", error_type=type(exc).__name__)
            return
        if result is None:
            return
        try:
            for line in result.lines:
                run.send(ev_log(FIRST_READ_WHO, line, "resume"))
                await run.pace(FIRST_READ_LINE_PACE_S)
            chips: list[SignalItem] = [
                {"label": clip_words(label, READER_SIGNAL_LABEL_MAX_CHARS), "detail": FIRST_READ_SRC}
                for label in result.signals
            ]
            await self._signals.emit(chips, "resume")
        except Exception as exc:
            log.warning("first_read_emit_failed", error_type=type(exc).__name__)

    async def settle(self) -> None:
        """Give a first read that is still running `FIRST_READ_GRACE_S` to finish, then drop it."""
        task = self._task
        if task is None or task.done():
            return
        done, _ = await asyncio.wait({task}, timeout=FIRST_READ_GRACE_S)
        if not done:
            log.info("first_read_dropped", reason="still running when synthesis started")
            await self.cancel()

    async def cancel(self) -> None:
        """Stop the first read now (no-op when it has finished) and wait until it is gone."""
        task = self._task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


__all__ = ["FIRST_READ_GRACE_S", "FIRST_READ_LINE_PACE_S", "FIRST_READ_SRC", "FIRST_READ_WHO", "FirstRead"]
