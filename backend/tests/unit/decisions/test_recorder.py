"""The decision recorder: buffering, flushing on size and on the interval, drain, state deduplication and failures."""

from __future__ import annotations

import asyncio
import json
import zlib
from datetime import UTC, datetime
from typing import Any

import pytest

from tayyari.contract.records import DecisionRecord, JevCallRecord, StateBlob
from tayyari.core.hashing import canonical_json, sha256_hex
from tayyari.core.ids import uuid7
from tayyari.decisions import recorder as recorder_module
from tayyari.decisions.recorder import MAX_CONSECUTIVE_FAILURES, DecisionRecorder, make_state_blob
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def call(state_hash: str, **kwargs: Any) -> JevCallRecord:
    return JevCallRecord(
        id=uuid7(),
        created_at=NOW,
        pack="p",
        model_requested="jev-1.13.0",
        state_hash=state_hash,
        questions={"q": {"type": "noul"}},
        status="ok",
        cost_micro_usd=100,
        **kwargs,
    )


def decision(call_id: Any = None) -> DecisionRecord:
    return DecisionRecord(
        id=uuid7(),
        created_at=NOW,
        spec_id="p.q",
        spec_version="1",
        pack="p",
        mode="primary",
        used=True,
        source_used="jev",
        call_id=call_id,
    )


class FlakyRepo:
    """Fails the first ``failures`` batches, then delegates; records every batch it was given."""

    def __init__(self, inner: Any, failures: int = 0, error: Exception | None = None) -> None:
        self.inner = inner
        self.failures = failures
        self.error = error or RuntimeError("database is waking up")
        self.batches: list[tuple[list[StateBlob], list[JevCallRecord], list[DecisionRecord]]] = []

    async def record_batch(self, states: Any, calls: Any, rows: Any) -> None:
        self.batches.append((list(states), list(calls), list(rows)))
        if self.failures > 0:
            self.failures -= 1
            raise self.error
        await self.inner.record_batch(states, calls, rows)


def make(
    repo: Any = None, *, interval: float = 3600.0, max_rows: int = 50, **kwargs: Any
) -> tuple[DecisionRecorder, FakeRepositories, FakeClock]:
    clock = FakeClock()
    repos = FakeRepositories(clock)
    recorder = DecisionRecorder(
        repo if repo is not None else repos.decisions, clock, interval, max_rows, **kwargs
    )
    return recorder, repos, clock


# ───────────────────────── states ─────────────────────────


def test_a_state_blob_is_the_sha256_of_its_canonical_json_zlib_compressed() -> None:
    state = {"b": 2, "a": ["héllo", None, 1.5]}
    blob = make_state_blob(state, is_sample=True)
    raw = canonical_json(state)
    assert blob.hash == sha256_hex(raw)
    assert zlib.decompress(blob.payload) == raw
    assert blob.raw_bytes == len(raw) and blob.is_sample is True
    assert make_state_blob({"a": ["héllo", None, 1.5], "b": 2}).hash == blob.hash  # key order does not matter


def test_a_state_that_is_not_json_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_state_blob({"x": float("nan")})
    with pytest.raises(TypeError):
        make_state_blob({"x": object()})


def test_a_long_state_is_compressed_well() -> None:
    blob = make_state_blob({"resume": "led a team of designers " * 800})
    assert len(blob.payload) < blob.raw_bytes / 10


async def test_identical_states_are_buffered_once() -> None:
    recorder, repos, _ = make()
    first = recorder.add_state({"resume": "same"})
    second = recorder.add_state({"resume": "same"})
    other = recorder.add_state({"resume": "different"})
    assert first == second != other
    recorder.add_call(call(first))
    recorder.add_call(call(second))
    recorder.add_call(call(other))
    await recorder.drain()
    assert set(repos.decisions.states) == {first, other}
    assert len(repos.decisions.calls) == 3


async def test_a_state_written_recently_is_not_sent_again_until_the_ttl_passes() -> None:
    inner_clock = FakeClock()
    repos = FakeRepositories(inner_clock)
    repo = FlakyRepo(repos.decisions)
    recorder = DecisionRecorder(repo, inner_clock, 3600.0, 50, state_ttl_s=100.0)  # type: ignore[arg-type]

    h = recorder.add_state({"resume": "same"})
    recorder.add_call(call(h))
    await recorder.drain()
    assert len(repo.batches[0][0]) == 1

    recorder.add_state({"resume": "same"})
    recorder.add_call(call(h))
    await recorder.drain()
    assert repo.batches[1][0] == []  # the payload is not re-sent

    inner_clock.advance(101)
    recorder.add_state({"resume": "same"})
    recorder.add_call(call(h))
    await recorder.drain()
    assert len(repo.batches[2][0]) == 1  # the ttl passed: it is touched again
    assert len(repos.decisions.calls) == 3


async def test_a_state_whose_write_failed_is_not_remembered_as_stored() -> None:
    clock = FakeClock()
    repos = FakeRepositories(clock)
    repo = FlakyRepo(repos.decisions, failures=1)
    recorder = DecisionRecorder(repo, clock, 3600.0, 50)  # type: ignore[arg-type]
    h = recorder.add_state({"resume": "same"})
    recorder.add_call(call(h))
    await recorder.drain()
    # the failed batch was put back (state included) and written by the retry inside drain
    assert h in repos.decisions.states and len(repos.decisions.calls) == 1


# ───────────────────────── flushing ─────────────────────────


async def test_nothing_is_written_until_the_buffer_is_full_or_the_interval_passes() -> None:
    recorder, repos, _ = make(max_rows=3)
    h = recorder.add_state({"s": 1})
    recorder.add_call(call(h))
    recorder.add_decision(decision())
    await asyncio.sleep(0.05)
    assert repos.decisions.rows == {} and recorder.pending == 2

    recorder.add_decision(decision())  # the third row: flush on size
    for _ in range(50):
        if recorder.pending == 0:
            break
        await asyncio.sleep(0.01)
    assert recorder.pending == 0
    assert len(repos.decisions.rows) == 2 and len(repos.decisions.calls) == 1
    assert recorder.stats.flushes == 1 and recorder.stats.rows_written == 3
    await recorder.aclose()


async def test_the_interval_flushes_a_small_buffer() -> None:
    recorder, repos, _ = make(interval=0.05)
    h = recorder.add_state({"s": 1})
    recorder.add_call(call(h))
    recorder.add_decision(decision())
    assert recorder.pending == 2
    for _ in range(100):
        if repos.decisions.rows:
            break
        await asyncio.sleep(0.01)
    assert len(repos.decisions.rows) == 1 and len(repos.decisions.calls) == 1
    await recorder.aclose()


async def test_the_flusher_ends_when_idle_and_restarts_on_the_next_row() -> None:
    recorder, repos, _ = make(interval=0.02)
    recorder.add_decision(decision())
    for _ in range(100):
        if repos.decisions.rows:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert recorder._task is not None and recorder._task.done()  # no task lingers while idle

    recorder.add_decision(decision())
    for _ in range(100):
        if len(repos.decisions.rows) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(repos.decisions.rows) == 2
    await recorder.aclose()


async def test_drain_flushes_everything_now() -> None:
    recorder, repos, _ = make()
    h = recorder.add_state({"s": 1})
    for _ in range(7):
        recorder.add_call(call(h))
        recorder.add_decision(decision())
    assert repos.decisions.rows == {}
    await recorder.drain()
    assert len(repos.decisions.rows) == 7 and len(repos.decisions.calls) == 7 and recorder.pending == 0
    assert recorder.stats.last_flush_at == NOW or recorder.stats.last_flush_at is not None
    await recorder.drain()  # a second drain with nothing to do is harmless


async def test_rows_added_without_a_running_loop_wait_for_drain() -> None:
    recorder, repos, _ = make()

    def add_in_a_thread_without_a_loop() -> None:
        recorder.add_decision(decision())

    await asyncio.to_thread(add_in_a_thread_without_a_loop)
    await recorder.drain()
    assert len(repos.decisions.rows) == 1


# ───────────────────────── failures ─────────────────────────


async def test_a_failed_flush_is_logged_not_raised_and_retried() -> None:
    clock = FakeClock()
    repos = FakeRepositories(clock)
    repo = FlakyRepo(repos.decisions, failures=2)
    recorder = DecisionRecorder(repo, clock, 3600.0, 50)  # type: ignore[arg-type]
    recorder.add_decision(decision())
    await recorder.flush()  # fails: does not raise, the row stays buffered
    assert recorder.pending == 1 and recorder.stats.failures == 1 and repos.decisions.rows == {}
    await recorder.flush()  # fails again
    assert recorder.pending == 1 and recorder.stats.failures == 2
    await recorder.flush()  # works
    assert recorder.pending == 0 and len(repos.decisions.rows) == 1


async def test_a_batch_that_keeps_failing_is_dropped_after_a_few_attempts() -> None:
    clock = FakeClock()
    repos = FakeRepositories(clock)
    repo = FlakyRepo(repos.decisions, failures=100)
    recorder = DecisionRecorder(repo, clock, 3600.0, 50)  # type: ignore[arg-type]
    recorder.add_decision(decision())
    for _ in range(MAX_CONSECUTIVE_FAILURES + 1):
        await recorder.flush()
    assert recorder.pending == 0 and recorder.stats.rows_dropped == 1
    repo.failures = 0
    recorder.add_decision(decision())
    await recorder.flush()
    assert len(repos.decisions.rows) == 1  # the poison batch is gone, new rows flow


class RecordingLog:
    """Stands in for the module's structlog logger."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, event: str, **fields: Any) -> None:
        self.events.append((level, event, fields))

    def info(self, event: str, **fields: Any) -> None:
        self._add("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._add("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._add("error", event, **fields)


class LeakyDatabaseError(Exception):
    """What SQLAlchemy raises when parameters are not hidden: the statement's bound values are in the text."""


SECRET = "MARKER_RESUME_TEXT about my layoff at ACME"


async def test_flush_failures_are_logged_by_class_name_never_by_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#10: a database error's text can hold the bound parameters of the statement (resume, chat, Jev states)."""
    log = RecordingLog()
    clock = FakeClock()
    repos = FakeRepositories(clock)
    boom = LeakyDatabaseError(f"(psycopg.errors.DataError) invalid input [parameters: ('{SECRET}', 3)]")
    repo = FlakyRepo(repos.decisions, failures=100, error=boom)
    recorder = DecisionRecorder(repo, clock, 3600.0, 50)  # type: ignore[arg-type]
    monkeypatch.setattr(recorder_module, "log", log)
    recorder.add_decision(decision())
    for _ in range(MAX_CONSECUTIVE_FAILURES + 1):  # failed, failed, failed, then dropped
        await recorder.flush()

    names = [event for _level, event, _fields in log.events]
    assert names == ["decision_flush_failed"] * MAX_CONSECUTIVE_FAILURES + ["decision_flush_dropped"]
    for _level, _event, fields in log.events:
        assert fields["error"] == "LeakyDatabaseError"
        assert SECRET not in json.dumps(fields, default=str)
        assert "parameters" not in json.dumps(fields, default=str)
    assert recorder.stats.failures == MAX_CONSECUTIVE_FAILURES + 1 and recorder.stats.rows_dropped == 1


async def test_drain_survives_a_database_that_is_down() -> None:
    clock = FakeClock()
    repos = FakeRepositories(clock)
    repo = FlakyRepo(repos.decisions, failures=100, error=ConnectionError("no route"))
    recorder = DecisionRecorder(repo, clock, 3600.0, 50)  # type: ignore[arg-type]
    recorder.add_decision(decision())
    await asyncio.wait_for(recorder.drain(), 5)  # returns, does not raise, does not loop forever
    assert repos.decisions.rows == {}


async def test_drain_gives_up_at_its_timeout() -> None:
    class Hanging:
        async def record_batch(self, states: Any, calls: Any, rows: Any) -> None:
            await asyncio.sleep(30)

    clock = FakeClock()
    recorder = DecisionRecorder(Hanging(), clock, 3600.0, 50)  # type: ignore[arg-type]
    recorder.add_decision(decision())
    await asyncio.wait_for(recorder.drain(timeout_s=0.05), 2)


async def test_cancelling_a_flush_puts_the_batch_back() -> None:
    gate = asyncio.Event()

    class Slow:
        async def record_batch(self, states: Any, calls: Any, rows: Any) -> None:
            await gate.wait()

    clock = FakeClock()
    recorder = DecisionRecorder(Slow(), clock, 3600.0, 50)  # type: ignore[arg-type]
    recorder.add_decision(decision())
    task = asyncio.create_task(recorder.flush())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert recorder.pending == 1


async def test_the_buffer_is_capped_so_a_dead_database_cannot_grow_memory() -> None:
    recorder, _, _ = make(max_rows=5, max_buffered=10)
    for _ in range(25):
        recorder.add_decision(decision())
    assert recorder.pending == 10 and recorder.stats.rows_dropped == 15
    recorder._wake.clear()
    await recorder.aclose()


def test_the_constructor_validates_its_limits() -> None:
    clock = FakeClock()
    repos = FakeRepositories(clock)
    with pytest.raises(ValueError):
        DecisionRecorder(repos.decisions, clock, 0.0)
    with pytest.raises(ValueError):
        DecisionRecorder(repos.decisions, clock, 1.0, 0)
    with pytest.raises(ValueError):
        DecisionRecorder(repos.decisions, clock, 1.0, 10, max_buffered=5)


async def test_logged_rows_carry_enough_to_replay_a_request() -> None:
    recorder, repos, _ = make()
    state = {"claim": "led a team", "section": "…led 6 designers…"}
    h = recorder.add_state(state)
    c = call(h, response={"answers": {"q": {"type": "noul", "noul": 0.9}}})
    recorder.add_call(c)
    recorder.add_decision(decision(c.id))
    await recorder.drain()
    exported = [row async for row in repos.decisions.export(NOW.replace(year=2020))]
    [row] = exported
    assert row["state"] == state
    assert row["call"]["questions"] == {"q": {"type": "noul"}} and row["call"]["state_hash"] == h
    assert json.dumps(row)  # JSON-ready
