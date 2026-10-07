"""Reading one source: deterministic ingest, then a Haiku reader (with Jev's triage alongside for web pages).

`SourceReader.read(planned)` is the whole life of one reader card: it announces progress through a throttled
`ReaderChannel`, fetches the source, streams its quick signals, calls Claude under the shared semaphore and
timeout, and returns a `ReadResult`. It never raises for an expected failure (a failed source or a failed
reader becomes a `ReadResult` with an error); only cancellation propagates, after every child task is gone.

Web sources (link, portfolio, lovable, linkedin) are triaged while their reader runs. A skip verdict that
arrives before the reader finishes cancels the reader and fails the source with the verdict's reason; any
other verdict only refines the label, adds notes and log lines. Resume, notes and GitHub are never triaged.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from tayyari.contract import ReaderState, ReaderStatus, SourceKind, ev_log, ev_reader
from tayyari.core import limits
from tayyari.core.logging import get_logger
from tayyari.core.text import clip_words, js_round, plural, tidy
from tayyari.decisions.service import TriageInput, TriageResult
from tayyari.ingest import PlannedSource, SourceDoc, ingest_planned
from tayyari.llm.errors import ClaudeTimeout
from tayyari.llm.types import TextBlock
from tayyari.prompts import READER_SYSTEM, build_reader_prompt

from .failures import reader_error_line, user_message
from .firstread import FirstRead
from .normalise import (
    ReadResult,
    activity_lines,
    clean_reader,
    quick_detail,
    result_line,
    source_kind_or_link,
    split_signal,
)
from .planning import WEB_KINDS, who_for
from .runctx import RunContext, Send
from .schemas import READER_SCHEMA
from .signals import SignalStream

log = get_logger(__name__)

CHANNEL_THROTTLE_S = 0.26
"""A reader card is re-sent at most this often (progress updates arrive per token)."""
TICK_S = 1.4
"""How often the card's activity line rotates while the reader works."""
TRIAGE_GRACE_S = 0.5
"""A reader that finishes before its triage verdict waits this long for the label, then moves on."""
SKIPPED_BY_TRIAGE = "This page can't be read (it is behind a login wall or has no content)."
"""Used when triage skips a source without giving a reason."""

_FIRST_SENTENCE = re.compile(r"(?<=\.)\s")
_TRAILING_DOT = re.compile(r"\.$")


class ReaderChannel:
    """Throttled emitter for one reader card: updates arrive per token, events leave at most every 0.26 s."""

    def __init__(
        self, state: ReaderState, send: Send, *, now: Callable[[], float], throttle_s: float
    ) -> None:
        self.state = state
        self._send = send
        self._now = now
        self._throttle_s = throttle_s
        self._loop = asyncio.get_running_loop()
        self._last: float | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False

    def _flush(self) -> None:
        self._timer = None
        if self._closed:
            return
        self._last = self._now()
        self._send(ev_reader(self.state))

    def update(
        self,
        *,
        status: ReaderStatus | None = None,
        line: str | None = None,
        progress: int | None = None,
        kind_label: str | None = None,
        src: str | None = None,
        force: bool = False,
    ) -> None:
        """Change the card. It is sent now when ``force`` or the throttle has passed, otherwise shortly after."""
        if status is not None:
            self.state.status = status
        if line is not None:
            self.state.line = line
        if progress is not None:
            self.state.progress = progress
        if kind_label is not None:
            self.state.kindLabel = kind_label
        if src is not None:
            self.state.src = src
        if self._closed:
            return
        wait = 0.0 if self._last is None else self._throttle_s - (self._now() - self._last)
        if force or wait <= 0:
            if self._timer:
                self._timer.cancel()
            self._flush()
        elif self._timer is None:
            self._timer = self._loop.call_later(wait, self._flush)

    def close(self) -> None:
        """Drop any pending update: nothing is sent after this."""
        if self._timer:
            self._timer.cancel()
        self._timer = None
        self._closed = True


def _host_of(url: str | None) -> str | None:
    try:
        return urlsplit(url or "").hostname
    except ValueError:
        return None


class SourceReader:
    """Reads the planned sources of one analysis (one `read` call per source, all at once)."""

    def __init__(
        self,
        run: RunContext,
        channels: Mapping[str, ReaderChannel],
        signals: SignalStream,
        first_read: FirstRead,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self._run = run
        self._channels = channels
        self._signals = signals
        self._first_read = first_read
        self._sem = semaphore

    # ── one source ──

    async def read(self, p: PlannedSource) -> ReadResult:
        run = self._run
        ch = self._channels[p.id]
        who = who_for(p.kind)
        kind = source_kind_or_link(p.kind)
        raw: SourceDoc | None = None
        try:
            ch.update(
                status="reading",
                line=f"Opening {clip_words(p.src, limits.READER_OPENING_SRC_MAX_CHARS)}",
                progress=6,
                force=True,
            )

            # deterministic ingest (expected failures come back as a doc with ok=False, never as an exception)
            def ingest_log(text: str) -> None:
                run.send(ev_log(who, text, kind))
                ch.update(
                    line=clip_words(text, limits.READER_LINE_MAX_CHARS),
                    progress=min(35, max(10, ch.state.progress + 7)),
                )

            raw = await ingest_planned(p, ingest_log)
            ch.update(kind_label=raw.kind_label or p.kind_label, src=raw.src or p.src)
            self._first_read.on_ingested(p, raw)

            error = raw.error or ("" if raw.ok else "Could not read this source.")
            if error:
                return self._source_failed(p, raw, ch, error, who, kind)

            ch.update(progress=max(ch.state.progress, 35))
            quick = self._signals.spawn(
                [split_signal(s, quick_detail(raw)) for s in raw.quick_signals or []], kind
            )
            return await self._read_with_ai(p, raw, ch, who, kind, quick)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            log.warning("reader_failed", source_id=p.id, kind=p.kind, error_type=type(err).__name__)
            line = reader_error_line(err)
            ch.update(status="error", line=line, force=True)
            run.send(ev_log(who, f"Reader failed: {line}", kind))
            return ReadResult(planned=p, raw=raw, error=user_message(err), ai_error=err)
        finally:
            ch.close()

    def _source_failed(
        self, p: PlannedSource, raw: SourceDoc, ch: ReaderChannel, error: str, who: str, kind: SourceKind
    ) -> ReadResult:
        first = _FIRST_SENTENCE.split(tidy(error))[0] or error
        ch.update(
            status="error",
            line=clip_words(_TRAILING_DOT.sub("", first), limits.READER_ERROR_LINE_MAX_CHARS),
            force=True,
        )
        self._run.send(
            ev_log(
                who,
                f"Could not read {raw.label}: {clip_words(error, limits.LOG_ERROR_DETAIL_MAX_CHARS)}",
                kind,
            )
        )
        return ReadResult(planned=p, raw=raw, error=error)

    # ── the reader, with triage alongside ──

    async def _read_with_ai(
        self,
        p: PlannedSource,
        raw: SourceDoc,
        ch: ReaderChannel,
        who: str,
        kind: SourceKind,
        quick: asyncio.Task[None],
    ) -> ReadResult:
        run = self._run
        lines = activity_lines(raw)
        ch.update(line=lines[0], force=True)
        pdf = raw.pdf_bytes if raw.pdf_bytes and not (raw.text or "").strip() else None

        # The triage task is created first so that, when both answer in the same loop turn, its verdict is seen first.
        triage_task: asyncio.Task[TriageResult] | None = None
        if p.kind in WEB_KINDS:
            triage_task = asyncio.create_task(self._triage(p, raw))
        reader_task = asyncio.create_task(self._run_reader(raw, ch, lines, pdf))
        extra_notes: list[str] = []
        try:
            triage_pending = triage_task is not None
            while True:
                if triage_task is not None and triage_pending and triage_task.done():
                    triage_pending = False
                    verdict = triage_task.result()
                    if verdict.skip:
                        # The page is a wall or empty: no reader, and no more chips from a page nobody can read.
                        await _stop(reader_task)
                        await _stop(quick)
                        reason = verdict.skip_reason or SKIPPED_BY_TRIAGE
                        log.info("source_skipped_by_triage", source_id=p.id, kind=p.kind)
                        return self._source_failed(p, raw, ch, reason, who, kind)
                    extra_notes = self._apply_triage(raw, ch, verdict, who, kind)
                if reader_task.done():
                    break
                waiting: set[asyncio.Task[Any]] = {reader_task}
                if triage_task is not None and triage_pending:
                    waiting.add(triage_task)
                await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

            if triage_task is not None and triage_pending:
                # The reader won the race: give the verdict a moment to label the source. A skip that comes this
                # late is ignored altogether (the page was read), and so is a verdict that is still not there.
                await asyncio.wait({triage_task}, timeout=TRIAGE_GRACE_S)
                if triage_task.done():
                    verdict = triage_task.result()
                    if not verdict.skip:
                        extra_notes = self._apply_triage(raw, ch, verdict, who, kind)
                else:
                    await _stop(triage_task)

            out = reader_task.result()
        finally:
            await _stop(reader_task)
            if triage_task is not None:
                await _stop(triage_task)

        ch.update(status="done", progress=100, line=result_line(out), force=True)
        run.send(
            ev_log(
                who,
                f"{raw.label} read · {plural(len(out['skills']), 'skill')}, {plural(len(out['signals']), 'signal')}",
                kind,
            )
        )
        if out["notes"]:
            run.send(ev_log(who, clip_words(out["notes"][0], limits.LOG_NOTE_MAX_CHARS), kind))
        self._signals.spawn(
            [{"label": s["label"], "detail": s["detail"] or quick_detail(raw)} for s in out["signals"]],
            kind,
            quick,
        )
        return ReadResult(planned=p, raw=raw, reader=out, extra_notes=extra_notes)

    async def _run_reader(
        self, raw: SourceDoc, ch: ReaderChannel, lines: list[str], pdf: bytes | None
    ) -> dict[str, Any]:
        """The Claude call for one source, with the activity ticker running beside it."""
        run = self._run
        ticker: asyncio.Task[None] | None = None
        interval = TICK_S * run.scale
        if interval > 0:
            ticker = asyncio.create_task(self._tick(ch, lines, interval))

        def on_progress(chars: int) -> None:
            pr = 35 + js_round(57 * (1 - math.exp(-chars / 1800)))
            if pr > ch.state.progress:
                ch.update(progress=pr)

        prompt = build_reader_prompt(
            {
                "id": raw.id,
                "kind": raw.kind,
                "label": raw.label,
                "kindLabel": raw.kind_label,
                "url": raw.url,
                "src": raw.src,
                "content": raw.text or "",
                "facts": raw.meta,
                "pdfAttached": pdf is not None,
            }
        )
        try:
            async with self._sem:
                try:
                    async with asyncio.timeout(run.settings.concurrency.reader_timeout_s):
                        res = await run.claude.structured(
                            model=run.settings.reader_model,
                            system=[TextBlock(READER_SYSTEM(raw.kind))],
                            user=[TextBlock(prompt)],
                            schema=READER_SCHEMA,
                            max_tokens=run.settings.reader_max_tokens,
                            effort="low",
                            pdf=pdf,
                            on_progress=on_progress,
                            ctx=run.call_ctx("reader"),
                        )
                except TimeoutError as exc:
                    raise ClaudeTimeout(detail="the reader exceeded its time limit") from exc
            return clean_reader(res.data)
        finally:
            if ticker is not None:
                await _stop(ticker)

    async def _tick(self, ch: ReaderChannel, lines: list[str], interval: float) -> None:
        li = 0
        while True:
            await self._run.clock.sleep(interval)
            li = (li + 1) % len(lines)
            # never lower progress (on_progress may already have pushed it past 90)
            pr = ch.state.progress
            ch.update(line=lines[li], progress=pr + 1 if pr < 90 else pr)

    # ── triage ──

    async def _triage(self, p: PlannedSource, raw: SourceDoc) -> TriageResult:
        """The triage verdict for one web source; any failure is a neutral verdict (never raises, never skips)."""
        run = self._run
        title = raw.meta.get("title") if isinstance(raw.meta, Mapping) else None
        inp = TriageInput(
            source_id=p.id,
            kind=p.kind,
            host=_host_of(raw.url or p.url),
            title=title if isinstance(title, str) else None,
            text=raw.text or "",
        )
        try:
            return await run.decisions.triage(inp, run.decision_ctx)
        except Exception as exc:
            log.warning("triage_failed", source_id=p.id, kind=p.kind, error_type=type(exc).__name__)
            return TriageResult()

    def _apply_triage(
        self, raw: SourceDoc, ch: ReaderChannel, verdict: TriageResult, who: str, kind: SourceKind
    ) -> list[str]:
        """Refine the label of a non-skip verdict and send its log lines; returns the notes for the source's popover."""
        if verdict.label:
            raw.label = verdict.label
            raw.kind_label = verdict.label
            ch.update(kind_label=verdict.label)
        for line in verdict.log_lines:
            self._run.send(ev_log(who, line, kind))
        return [n for n in verdict.notes if tidy(n)]


async def _stop(task: asyncio.Task[Any]) -> None:
    """Cancel a task (a no-op when it is done) and wait until it is gone, swallowing its outcome."""
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


__all__ = [
    "CHANNEL_THROTTLE_S",
    "SKIPPED_BY_TRIAGE",
    "TICK_S",
    "TRIAGE_GRACE_S",
    "ReaderChannel",
    "SourceReader",
]
