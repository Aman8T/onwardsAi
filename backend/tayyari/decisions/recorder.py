"""The decision log writer: buffers rows in memory and writes them in batches, off the hot path.

`DecisionRecorder` collects three kinds of rows and hands them to `DecisionLogRepo.record_batch` in one
transaction:

    StateBlob       a Jev request state, stored once per distinct content: sha256 of its canonical JSON,
                    zlib-compressed. Identical states (one resume asked about 20 times) are buffered once, and
                    a state written in the last `state_ttl_s` seconds is not sent to the database again.
    JevCallRecord   one Jev request, with its exact questions and the raw response.
    DecisionRecord  one decision outcome, linked to its call.

A flush happens when the buffer reaches `max_rows` calls plus decisions, or `flush_interval_s` after the first
row was added, whichever is first; `drain()` flushes everything now (end of an analysis, shutdown). The
background flusher exists only while there is something to write, so an idle recorder holds no task.

Decision logs are not critical: a failed flush is logged, never raised. Only the exception class goes to the
log: a database error's message can carry the bound parameters of the failed statement, which hold resume and
chat text. The batch is put back and retried on the next flush up to three times in a row, then dropped; the
buffer is capped at `max_buffered` rows (the oldest are dropped first), so an unreachable database cannot grow
memory without bound.
"""

from __future__ import annotations

import asyncio
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..contract.records import DecisionRecord, JevCallRecord, StateBlob
from ..core.clock import Clock
from ..core.hashing import canonical_json, sha256_hex
from ..core.logging import get_logger
from ..storage.ports import DecisionLogRepo

log = get_logger(__name__)

MAX_CONSECUTIVE_FAILURES = 3
_STATE_CACHE_SIZE = 4096
_COMPRESSION_LEVEL = 6


@dataclass(frozen=True, slots=True)
class RecorderStats:
    flushes: int
    rows_written: int
    failures: int
    rows_dropped: int
    last_flush_at: datetime | None


def make_state_blob(state: Any, *, is_sample: bool = False) -> StateBlob:
    """The storable form of a state: its sha256, the zlib-compressed canonical JSON and the raw size.

    Raises ``ValueError`` / ``TypeError`` for a state that is not JSON (NaN, an object).
    """
    raw = canonical_json(state)
    return StateBlob(
        hash=sha256_hex(raw),
        payload=zlib.compress(raw, _COMPRESSION_LEVEL),
        raw_bytes=len(raw),
        is_sample=is_sample,
    )


class DecisionRecorder:
    def __init__(
        self,
        repo: DecisionLogRepo,
        clock: Clock,
        flush_interval_s: float = 1.0,
        max_rows: int = 50,
        *,
        max_buffered: int = 2000,
        state_ttl_s: float = 600.0,
    ) -> None:
        if flush_interval_s <= 0 or max_rows < 1 or max_buffered < max_rows:
            raise ValueError("flush_interval_s must be positive and 1 <= max_rows <= max_buffered")
        self._repo = repo
        self._clock = clock
        self._interval = flush_interval_s
        self._max_rows = max_rows
        self._max_buffered = max_buffered
        self._state_ttl = state_ttl_s

        self._states: dict[str, StateBlob] = {}
        self._calls: list[JevCallRecord] = []
        self._rows: list[DecisionRecord] = []
        self._stored: OrderedDict[str, float] = OrderedDict()  # state hash -> monotonic time it was written

        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._flush_lock = asyncio.Lock()
        self._failures_in_a_row = 0
        self._flushes = 0
        self._rows_written = 0
        self._failures = 0
        self._rows_dropped = 0
        self._last_flush_at: datetime | None = None

    # ── adding ──

    def add_state(self, state: Any, *, is_sample: bool = False) -> str:
        """Buffer ``state`` (once) and return its hash for `JevCallRecord.state_hash`.

        A state written recently, or already buffered, is not buffered again.
        """
        blob = make_state_blob(state, is_sample=is_sample)
        if blob.hash in self._states or self._recently_stored(blob.hash):
            return blob.hash
        self._states[blob.hash] = blob
        return blob.hash

    def add_call(self, record: JevCallRecord) -> None:
        self._calls.append(record)
        self._after_add()

    def add_decision(self, record: DecisionRecord) -> None:
        self._rows.append(record)
        self._after_add()

    # ── flushing ──

    @property
    def pending(self) -> int:
        """Calls plus decisions waiting to be written."""
        return len(self._calls) + len(self._rows)

    @property
    def stats(self) -> RecorderStats:
        return RecorderStats(
            flushes=self._flushes,
            rows_written=self._rows_written,
            failures=self._failures,
            rows_dropped=self._rows_dropped,
            last_flush_at=self._last_flush_at,
        )

    async def flush(self) -> None:
        """Write what is buffered now. Never raises (except for cancellation, which re-buffers the batch)."""
        async with self._flush_lock:
            states, calls, rows = self._take()
            if not (states or calls or rows):
                return
            try:
                await self._repo.record_batch(list(states.values()), calls, rows)
            except asyncio.CancelledError:
                self._put_back(states, calls, rows)
                raise
            except Exception as exc:
                self._failures += 1
                self._failures_in_a_row += 1
                if self._failures_in_a_row > MAX_CONSECUTIVE_FAILURES:
                    self._rows_dropped += len(calls) + len(rows)
                    self._failures_in_a_row = 0
                    log.error(
                        "decision_flush_dropped",
                        calls=len(calls),
                        rows=len(rows),
                        error=type(exc).__name__,
                    )
                else:
                    log.warning(
                        "decision_flush_failed",
                        calls=len(calls),
                        rows=len(rows),
                        attempt=self._failures_in_a_row,
                        error=type(exc).__name__,
                    )
                    self._put_back(states, calls, rows)
                return
            self._failures_in_a_row = 0
            self._flushes += 1
            self._rows_written += len(calls) + len(rows)
            self._last_flush_at = self._clock.now()
            now = self._clock.monotonic()
            for blob_hash in states:
                self._stored[blob_hash] = now
                self._stored.move_to_end(blob_hash)
            while len(self._stored) > _STATE_CACHE_SIZE:
                self._stored.popitem(last=False)

    async def drain(self, timeout_s: float | None = 10.0) -> None:
        """Flush everything now, retrying failed batches, and let the background flusher finish.

        Bounded by ``timeout_s`` (None waits as long as it takes); never raises for a write failure or a timeout.
        """
        try:
            async with asyncio.timeout(timeout_s):
                for _ in range(MAX_CONSECUTIVE_FAILURES + 2):
                    if not (self._states or self._calls or self._rows):
                        break
                    await self.flush()
        except TimeoutError:
            log.warning("decision_drain_timeout", pending=self.pending)
        self._wake.set()  # lets an idle flusher notice that there is nothing left and end
        task = self._task
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=0.5)

    async def aclose(self) -> None:
        """Drain, then stop the background flusher."""
        await self.drain()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # ── internals ──

    def _recently_stored(self, blob_hash: str) -> bool:
        stored_at = self._stored.get(blob_hash)
        if stored_at is None:
            return False
        if self._clock.monotonic() - stored_at >= self._state_ttl:
            del self._stored[blob_hash]
            return False
        return True

    def _take(self) -> tuple[dict[str, StateBlob], list[JevCallRecord], list[DecisionRecord]]:
        states, calls, rows = self._states, self._calls, self._rows
        self._states, self._calls, self._rows = {}, [], []
        return states, calls, rows

    def _put_back(
        self, states: dict[str, StateBlob], calls: list[JevCallRecord], rows: list[DecisionRecord]
    ) -> None:
        self._states = {**states, **self._states}
        self._calls[:0] = calls
        self._rows[:0] = rows
        self._enforce_cap()

    def _enforce_cap(self) -> None:
        excess = self.pending - self._max_buffered
        if excess <= 0:
            return
        drop_rows = min(excess, len(self._rows))
        del self._rows[:drop_rows]
        drop_calls = min(excess - drop_rows, len(self._calls))
        del self._calls[:drop_calls]
        self._rows_dropped += drop_rows + drop_calls
        log.warning("decision_buffer_overflow", dropped=drop_rows + drop_calls, cap=self._max_buffered)

    def _after_add(self) -> None:
        self._enforce_cap()
        if self.pending >= self._max_rows:
            self._wake.set()
        self._ensure_task()

    def _ensure_task(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop yet: the rows wait for drain() or the next add from async code
        self._task = loop.create_task(self._flusher(), name="decision-recorder")

    async def _flusher(self) -> None:
        while True:
            if self.pending < self._max_rows:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
                except TimeoutError:
                    pass
            self._wake.clear()
            await self.flush()
            if not (self._states or self._calls or self._rows):
                return  # idle: the next add starts a new flusher


__all__ = ["MAX_CONSECUTIVE_FAILURES", "DecisionRecorder", "RecorderStats", "make_state_blob"]
