"""The decision runner: asks Jev, applies the mode and threshold of each spec, falls back to Claude, logs everything.

`DecisionRunner.run(pack, subject, ctx=...)` builds the pack's state, plans the applicable questions (a spec
without `bind` is one question; a spec with `bind` is one per binding), and makes ONE Jev call over the state
with all of them (chunks of at most `JEV_MAX_QUESTIONS_PER_CALL`, asked in parallel). Per question, by the
effective mode of its spec:

    off      nothing is asked; the result is the neutral value (`source="default"`, `used=False`)
    shadow   Jev is asked, the answer is logged next to the caller's `incumbent` value and returned with
             `used=False`. The caller never waits more than `shadow_wait_s` (1.5 s) for a call that holds only
             shadow questions: it gets neutral results and the call finishes (and logs) in a tracked task.
    primary  the Jev answer is used when `confidence >= threshold`. Otherwise, and on a Jev error, an open
             circuit, a disabled Jev or a spend trip, the Claude fallback answers the same rubric
             (`fallback="claude"`) or the result stays neutral (`fallback="skip"`). `escalated` and
             `escalation_reason` say what happened. A spend-guard trip makes everything neutral (reason
             `spend`): not even the fallback is called.

Jev failures never raise out of `run`: they become fallback or neutral results (a last safety net also turns an
unexpected exception into neutral results and logs it). Only cancellation propagates.

Logging: one `JevCallRecord` per Jev request (not for calls that were never made) and one `DecisionRecord` per
question whenever Jev or the fallback was actually asked, buffered by the `DecisionRecorder`. The state is
stored as it was sent: with `send_pii` off, emails, phones and address lines are masked first. A used Jev
answer is audited with probability `audit_rate`: a background Claude answer is added to that same row
(`extra["audit_agrees"]` says whether the two interpreted values match).

`spend_gate(kind)` is awaited before every Jev request (``kind="jev"``) and every fallback or audit call
(``kind="decision_fallback"``); raising `SpendCeilingReached` (or anything else) blocks the call.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import random
from collections.abc import Awaitable, Callable, Collection, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from ..contract.records import DecisionRecord, JevCallRecord
from ..core.clock import Clock
from ..core.ids import uuid7
from ..core.limits import JEV_MAX_QUESTIONS_PER_CALL
from ..core.logging import get_logger
from ..llm.breaker import CircuitBreaker
from ..llm.claude import ClaudeClient
from ..llm.errors import JevError, SpendCeilingReached
from ..llm.jev import JevAnswer, JevClient, JevQuestion, JevResponse, JsonContent
from ..llm.jev_http import prepare_state
from ..llm.types import CallContext
from .config import DecisionConfigStore
from .fallback import claude_fallback_result, to_answer
from .recorder import DecisionRecorder
from .service import DecisionContext
from .spec import (
    DecisionConfig,
    DecisionPack,
    DecisionResult,
    DecisionSpec,
    EscalationReason,
    Mode,
    PackResult,
    split_key,
)

log = get_logger(__name__)

SpendGate = Callable[[str], Awaitable[None]]
SHADOW_WAIT_S = 1.5
AUDIT_TIMEOUT_S = 20.0
FALLBACK_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class _Item:
    """One question to ask: its key, its spec, the question (None when `bind` failed) and the spec's effective config."""

    key: str
    spec: DecisionSpec[Any]
    question: JevQuestion | None
    cfg: DecisionConfig


@dataclass(slots=True)
class _Call:
    """What happened to the Jev request of one chunk."""

    response: JevResponse | None = None
    reason: EscalationReason | None = None  # why there is no Jev answer
    attempted: bool = False  # a request was really sent
    error_kind: str | None = None
    call_id: UUID | None = None
    state_hash: str | None = None
    latency_ms: int = 0
    cost_micro_usd: int = 0
    n_items: int = 1


@dataclass(slots=True)
class _Fallback:
    """What happened to one Claude fallback (or audit) call."""

    attempted: bool = False
    spend_blocked: bool = False
    answer: JevAnswer | None = None
    data: dict[str, Any] | None = None
    value: Any = None
    latency_ms: int = 0
    cost_micro_usd: int = 0
    request_id: str | None = None
    model: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _share(total: int, n: int, index: int) -> int:
    """The ``index``-th of ``n`` integer shares of ``total`` (the shares add up to ``total``)."""
    base, rest = divmod(total, max(n, 1))
    return base + (1 if index < rest else 0)


def _jsonable(value: Any, depth: int = 0) -> Any:
    """Best-effort JSON form of an interpreted value, for the log."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if depth > 6:
        return repr(value)[:200]
    if isinstance(value, Enum):
        return _jsonable(value.value, depth + 1)
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"), depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value), depth + 1)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v, depth + 1) for v in value]
    return repr(value)[:200]


def _pick(arg: Any, index: int) -> Any:
    """For `run_many`: one mapping for every subject, or a sequence with one entry per subject."""
    if arg is None or isinstance(arg, Mapping):
        return arg
    return arg[index]


class DecisionRunner:
    def __init__(
        self,
        jev: JevClient,
        claude: ClaudeClient | None,
        recorder: DecisionRecorder,
        config: DecisionConfigStore,
        breaker: CircuitBreaker,
        spend_gate: SpendGate | None,
        clock: Clock,
        fallback_model: str,
        *,
        rng: random.Random | None = None,
        max_questions_per_call: int = JEV_MAX_QUESTIONS_PER_CALL,
        shadow_wait_s: float = SHADOW_WAIT_S,
        audit_timeout_s: float = AUDIT_TIMEOUT_S,
        fallback_concurrency: int = FALLBACK_CONCURRENCY,
    ) -> None:
        if max_questions_per_call < 1:
            raise ValueError("max_questions_per_call must be at least 1")
        self._jev = jev
        self._claude = claude
        self._recorder = recorder
        self._config = config
        self._breaker = breaker
        self._spend_gate = spend_gate
        self._clock = clock
        self._fallback_model = fallback_model
        self._rng = rng if rng is not None else random.Random()
        self._max_questions = max_questions_per_call
        self._shadow_wait_s = shadow_wait_s
        self._audit_timeout_s = audit_timeout_s
        self._fallback_sem = asyncio.Semaphore(max(1, fallback_concurrency))
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def config(self) -> DecisionConfigStore:
        return self._config

    def config_for(self, spec: DecisionSpec[Any]) -> DecisionConfig:
        """The effective config of ``spec`` (defaults, toml and overrides merged)."""
        return self._config.for_spec(spec)

    # ───────────────────────── public API ─────────────────────────

    async def run(
        self,
        pack: DecisionPack[Any],
        subject: Any,
        *,
        ctx: DecisionContext,
        only: Collection[str] | None = None,
        bindings: Mapping[str, Mapping[str, Any]] | None = None,
        incumbent: Mapping[str, Any] | None = None,
    ) -> PackResult:
        """Decide every applicable question of ``pack`` about ``subject``.

        ``only`` limits the run to those spec ids or question keys. ``bindings`` maps a question key
        (``"skill.level#python"``, or the spec id for a spec asked once) to the arguments its spec's `bind`
        turns into a question; a bound spec without bindings is not asked. ``incumbent`` maps a question key
        (or spec id) to the value the decision is compared with in the log (shadow decisions).
        """
        try:
            return await self._run(pack, subject, ctx, only, bindings, incumbent)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("decision_run_failed", pack=pack.id, exc_info=True)
            return self._neutral_pack(pack, only, bindings)

    async def run_many(
        self,
        pack: DecisionPack[Any],
        subjects: Sequence[Any],
        *,
        concurrency: int = 8,
        ctx: DecisionContext,
        only: Collection[str] | None = None,
        bindings: Mapping[str, Mapping[str, Any]]
        | Sequence[Mapping[str, Mapping[str, Any]] | None]
        | None = None,
        incumbent: Mapping[str, Any] | Sequence[Mapping[str, Any] | None] | None = None,
    ) -> list[PackResult]:
        """`run` for many subjects (one Jev call per state), at most ``concurrency`` at a time, in input order.

        ``bindings`` and ``incumbent`` are either one mapping for every subject or a sequence with one entry each.
        """
        for name, arg in (("bindings", bindings), ("incumbent", incumbent)):
            if arg is not None and not isinstance(arg, Mapping) and len(arg) != len(subjects):
                raise ValueError(f"{name} must have one entry per subject ({len(arg)} != {len(subjects)})")
        gate = asyncio.Semaphore(max(1, concurrency))

        async def one(index: int, subject: Any) -> PackResult:
            async with gate:
                return await self.run(
                    pack,
                    subject,
                    ctx=ctx,
                    only=only,
                    bindings=_pick(bindings, index),
                    incumbent=_pick(incumbent, index),
                )

        return list(await asyncio.gather(*(one(i, s) for i, s in enumerate(subjects))))

    def spawn[T](self, coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
        """Run ``coro`` in a tracked background task: `drain` waits for it, and an exception is logged, not lost."""
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    async def drain(self, timeout_s: float, *, cancel_remaining: bool = False) -> None:
        """Wait up to ``timeout_s`` for the tracked tasks (shadow and audit work), then flush the decision log.

        Tasks still running after the timeout are left running, unless ``cancel_remaining`` (shutdown).
        Never raises for a failed task or a failed log write.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout_s, 0.0)
        while True:
            pending = {task for task in self._tasks if not task.done()}
            remaining = deadline - loop.time()
            if not pending or remaining <= 0:
                break
            await asyncio.wait(pending, timeout=remaining)
        left = [task for task in self._tasks if not task.done()]
        if cancel_remaining and left:
            for task in left:
                task.cancel()
            await asyncio.gather(*left, return_exceptions=True)
        elif left:
            log.warning("decision_drain_incomplete", tasks=len(left))
        await self._recorder.drain()

    @property
    def pending_tasks(self) -> int:
        return len(self._tasks)

    # ───────────────────────── planning ─────────────────────────

    def _plan(
        self,
        pack: DecisionPack[Any],
        only: Collection[str] | None,
        bindings: Mapping[str, Mapping[str, Any]] | None,
    ) -> list[_Item]:
        wanted = frozenset(only) if only is not None else None
        items: list[_Item] = []
        for spec in pack.specs:
            cfg = self._config.for_spec(spec)
            if spec.bind is None:
                keys = [spec.id]
            else:
                keys = [k for k in (bindings or {}) if split_key(k)[0] == spec.id]
            for key in keys:
                if wanted is not None and key not in wanted and spec.id not in wanted:
                    continue
                question: JevQuestion | None = spec.question
                if spec.bind is not None and bindings is not None:
                    try:
                        question = spec.bind(bindings[key])
                    except Exception:
                        log.error("decision_bind_failed", spec=spec.id, key=key, exc_info=True)
                        question = None
                items.append(_Item(key, spec, question, cfg))
        return items

    def _neutral_pack(
        self,
        pack: DecisionPack[Any],
        only: Collection[str] | None,
        bindings: Mapping[str, Mapping[str, Any]] | None,
    ) -> PackResult:
        try:
            return PackResult(
                pack.id, {it.key: self._default(it, it.cfg.mode) for it in self._plan(pack, only, bindings)}
            )
        except Exception:
            log.error("decision_neutral_pack_failed", pack=pack.id, exc_info=True)
            return PackResult(pack.id, {})

    def _chunks(self, items: list[_Item]) -> list[list[_Item]]:
        """Primary questions first, then shadow ones, in chunks of at most ``max_questions_per_call``."""
        ordered = [i for i in items if i.cfg.mode == "primary"] + [i for i in items if i.cfg.mode == "shadow"]
        size = self._max_questions
        return [ordered[start : start + size] for start in range(0, len(ordered), size)]

    # ───────────────────────── running ─────────────────────────

    async def _run(
        self,
        pack: DecisionPack[Any],
        subject: Any,
        ctx: DecisionContext,
        only: Collection[str] | None,
        bindings: Mapping[str, Mapping[str, Any]] | None,
        incumbent: Mapping[str, Any] | None,
    ) -> PackResult:
        state = pack.build_state(subject)
        items = self._plan(pack, only, bindings)
        results: dict[str, DecisionResult[Any]] = {}
        active: list[_Item] = []
        for item in items:
            if item.cfg.mode == "off" or item.question is None:
                results[item.key] = self._default(item, item.cfg.mode)
            else:
                active.append(item)
        if active:
            sent_state = prepare_state(state, redact=not self._config.send_pii)
            parts = await asyncio.gather(
                *(self._run_chunk(pack, chunk, sent_state, ctx, incumbent) for chunk in self._chunks(active))
            )
            for part in parts:
                results.update(part)
        return PackResult(pack.id, {item.key: results[item.key] for item in items})

    async def _run_chunk(
        self,
        pack: DecisionPack[Any],
        chunk: list[_Item],
        sent_state: JsonContent,
        ctx: DecisionContext,
        incumbent: Mapping[str, Any] | None,
    ) -> dict[str, DecisionResult[Any]]:
        if any(item.cfg.mode == "primary" for item in chunk):
            return await self._execute_chunk(pack, chunk, sent_state, ctx, incumbent)
        # Only shadow questions: never hold the caller up for long. The call finishes in a tracked task.
        task = self.spawn(
            self._execute_chunk(pack, chunk, sent_state, ctx, incumbent), name=f"decision-shadow-{pack.id}"
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=self._shadow_wait_s)
        except TimeoutError:
            return {item.key: self._default(item, "shadow") for item in chunk}

    async def _execute_chunk(
        self,
        pack: DecisionPack[Any],
        chunk: list[_Item],
        sent_state: JsonContent,
        ctx: DecisionContext,
        incumbent: Mapping[str, Any] | None,
    ) -> dict[str, DecisionResult[Any]]:
        """Ask Jev once for the whole chunk and decide every question. Never raises (except cancellation)."""
        try:
            call_ctx = CallContext(
                purpose="decision_fallback",
                request_id=ctx.request_id,
                profile_id=ctx.profile_id,
                analysis_id=ctx.analysis_id,
            )
            call = await self._call_jev(pack, chunk, sent_state, ctx, call_ctx)
            decided = await asyncio.gather(
                *(
                    self._decide(pack, item, index, call, sent_state, ctx, call_ctx, incumbent)
                    for index, item in enumerate(chunk)
                )
            )
            return {item.key: result for item, result in zip(chunk, decided, strict=True)}
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("decision_chunk_failed", pack=pack.id, exc_info=True)
            return {item.key: self._default(item, item.cfg.mode) for item in chunk}

    # ───────────────────────── the Jev call ─────────────────────────

    async def _call_jev(
        self,
        pack: DecisionPack[Any],
        chunk: list[_Item],
        sent_state: JsonContent,
        ctx: DecisionContext,
        call_ctx: CallContext,
    ) -> _Call:
        call = _Call(n_items=len(chunk))
        if not self._config.jev_enabled:
            call.reason = "jev_disabled"
            return call
        if self._spend_gate is not None:
            try:
                await self._spend_gate("jev")
            except asyncio.CancelledError:
                raise
            except SpendCeilingReached:
                call.reason = "spend"
                return call
            except Exception:
                log.warning("decision_spend_gate_failed", exc_info=True)
                call.reason = "spend"  # fail closed: decisions are optional, spend is not
                return call
        if not self._breaker.allow():
            call.reason = "circuit_open"
            return call

        questions = {item.key: item.question for item in chunk if item.question is not None}
        timeout_s = max(item.cfg.timeout_s for item in chunk)
        call.call_id = uuid7()
        started = self._clock.monotonic()
        response: JevResponse | None = None
        failure: Exception | None = None
        try:
            response = await self._jev.system_one(sent_state, questions, timeout_s=timeout_s, ctx=call_ctx)
        except asyncio.CancelledError:
            raise
        except SpendCeilingReached:
            call.reason = "spend"
            call.call_id = None
            return call
        except JevError as exc:
            if exc.kind == "disabled":
                call.reason = "jev_disabled"
                call.call_id = None
                return call
            failure = exc
            call.error_kind = exc.kind
            log.warning("decision_jev_failed", pack=pack.id, kind=exc.kind, status=exc.status)
        except Exception as exc:  # a client bug: treat like a failed call
            failure = exc
            call.error_kind = type(exc).__name__
            log.error("decision_jev_crashed", pack=pack.id, exc_info=True)
        call.attempted = True

        if response is not None:
            self._breaker.record_success()
            call.response = response
            call.latency_ms = response.latency_ms
            call.cost_micro_usd = response.cost_micro_usd
        else:
            call.latency_ms = round((self._clock.monotonic() - started) * 1000)
            # a 400 means Jev is up and our request is wrong: that is not the breaker's business
            if isinstance(failure, JevError) and failure.kind == "bad_request":
                self._breaker.record_success()
            else:
                self._breaker.record_failure()
            call.reason = "jev_error"

        self._record_call(pack, call, questions, sent_state, ctx)
        return call

    def _record_call(
        self,
        pack: DecisionPack[Any],
        call: _Call,
        questions: Mapping[str, JevQuestion],
        sent_state: JsonContent,
        ctx: DecisionContext,
    ) -> None:
        try:
            call.state_hash = self._recorder.add_state(sent_state, is_sample=ctx.is_sample)
            response = call.response
            self._recorder.add_call(
                JevCallRecord(
                    id=call.call_id if call.call_id is not None else uuid7(),
                    created_at=self._clock.now(),
                    pack=pack.id,
                    model_requested=self._jev.model_id,
                    state_hash=call.state_hash,
                    questions={qid: q.to_wire() for qid, q in questions.items()},
                    status="ok" if response is not None else "error",
                    model_served=response.model if response is not None else None,
                    response=response.response_json if response is not None else None,
                    error_kind=call.error_kind,
                    request_id=response.request_id if response is not None else None,
                    input_tokens=response.input_tokens if response is not None else 0,
                    output_tokens=response.output_tokens if response is not None else 0,
                    cost_micro_usd=call.cost_micro_usd,
                    latency_ms=call.latency_ms,
                    profile_id=ctx.profile_id,
                    analysis_id=ctx.analysis_id,
                    is_sample=ctx.is_sample,
                )
            )
        except Exception:
            log.warning("decision_call_record_failed", pack=pack.id, exc_info=True)

    # ───────────────────────── deciding ─────────────────────────

    async def _decide(
        self,
        pack: DecisionPack[Any],
        item: _Item,
        index: int,
        call: _Call,
        sent_state: JsonContent,
        ctx: DecisionContext,
        call_ctx: CallContext,
        incumbent: Mapping[str, Any] | None,
    ) -> DecisionResult[Any]:
        try:
            return await self._decide_inner(pack, item, index, call, sent_state, ctx, call_ctx, incumbent)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("decision_failed", spec=item.spec.id, key=item.key, exc_info=True)
            return self._default(item, item.cfg.mode)

    async def _decide_inner(
        self,
        pack: DecisionPack[Any],
        item: _Item,
        index: int,
        call: _Call,
        sent_state: JsonContent,
        ctx: DecisionContext,
        call_ctx: CallContext,
        incumbent: Mapping[str, Any] | None,
    ) -> DecisionResult[Any]:
        spec, cfg = item.spec, item.cfg
        reason: EscalationReason | None = call.reason
        answer: JevAnswer | None = None
        value: Any = None
        if call.response is not None:
            answer = call.response.answers.get(item.key)
            if answer is None:
                log.warning("decision_answer_missing", spec=spec.id, key=item.key)
                reason = "jev_error"
            else:
                try:
                    value = spec.interpret(answer)
                except Exception:
                    log.error("decision_interpret_failed", spec=spec.id, key=item.key, exc_info=True)
                    answer = None
                    reason = "jev_error"

        jev_cost = _share(call.cost_micro_usd, call.n_items, index)
        incumbent_value = self._incumbent_for(item, incumbent)
        log_id = uuid7()

        # ── shadow: log it, never use it ──
        if cfg.mode == "shadow":
            if answer is None:
                result = self._default(item, "shadow" if call.attempted else "off", reason=reason)
                if call.attempted:
                    result = dataclasses.replace(result, log_id=log_id, latency_ms=call.latency_ms)
                    self._record_decision(pack, item, result, call, ctx, incumbent_value=incumbent_value)
                return result
            result = DecisionResult(
                spec_id=spec.id,
                spec_version=spec.version,
                mode="shadow",
                value=value,
                used=False,
                source="jev",
                confidence=answer.confidence,
                threshold=cfg.threshold,
                escalated=False,
                escalation_reason=None,
                jev=answer,
                claude=None,
                latency_ms=call.latency_ms,
                cost_micro_usd=jev_cost,
                log_id=log_id,
                key=item.key,
            )
            self._record_decision(pack, item, result, call, ctx, incumbent_value=incumbent_value)
            return result

        # ── primary: Jev's answer when it is confident enough ──
        if answer is not None and answer.confidence >= cfg.threshold:
            result = DecisionResult(
                spec_id=spec.id,
                spec_version=spec.version,
                mode="primary",
                value=value,
                used=True,
                source="jev",
                confidence=answer.confidence,
                threshold=cfg.threshold,
                escalated=False,
                escalation_reason=None,
                jev=answer,
                claude=None,
                latency_ms=call.latency_ms,
                cost_micro_usd=jev_cost,
                log_id=log_id,
                key=item.key,
            )
            if self._claude is not None and cfg.audit_rate > 0 and self._rng.random() < cfg.audit_rate:
                self.spawn(
                    self._audit(pack, item, result, call, sent_state, ctx, call_ctx, incumbent_value),
                    name=f"decision-audit-{spec.id}",
                )
            else:
                self._record_decision(pack, item, result, call, ctx, incumbent_value=incumbent_value)
            return result

        # ── primary: below the threshold, or no Jev answer: the fallback decides, or the result stays neutral ──
        if answer is not None:
            reason = "low_confidence"
        reason = reason or "jev_error"
        fb = _Fallback()
        if cfg.fallback == "claude" and reason != "spend":
            fb = await self._fallback_call(item, sent_state, call_ctx)
        if fb.spend_blocked:
            reason = "spend"

        if fb.answer is not None:
            result = DecisionResult(
                spec_id=spec.id,
                spec_version=spec.version,
                mode="primary",
                value=fb.value,
                used=True,
                source="claude",
                confidence=fb.answer.confidence,
                threshold=cfg.threshold,
                escalated=True,
                escalation_reason=reason,
                jev=answer,
                claude=fb.data,
                latency_ms=call.latency_ms + fb.latency_ms,
                cost_micro_usd=jev_cost + fb.cost_micro_usd,
                log_id=log_id,
                key=item.key,
            )
        else:
            neutral = self._default(
                item,
                "primary",
                reason=reason,
                escalated=fb.attempted,
                jev=answer,
                confidence=answer.confidence if answer is not None else None,
                latency_ms=call.latency_ms + fb.latency_ms,
                cost=jev_cost + fb.cost_micro_usd,
            )
            result = (
                dataclasses.replace(neutral, log_id=log_id) if (call.attempted or fb.attempted) else neutral
            )
        if call.attempted or fb.attempted:
            self._record_decision(pack, item, result, call, ctx, incumbent_value=incumbent_value, fallback=fb)
        return result

    def _default(
        self,
        item: _Item,
        mode: Mode,
        *,
        reason: EscalationReason | None = None,
        escalated: bool = False,
        jev: JevAnswer | None = None,
        confidence: float | None = None,
        latency_ms: int = 0,
        cost: int = 0,
    ) -> DecisionResult[Any]:
        """The neutral result: ``used=False``, the spec's neutral value, ``source="default"``."""
        return DecisionResult(
            spec_id=item.spec.id,
            spec_version=item.spec.version,
            mode=mode,
            value=item.spec.neutral,
            used=False,
            source="default",
            confidence=confidence,
            threshold=item.cfg.threshold,
            escalated=escalated,
            escalation_reason=reason,
            jev=jev,
            claude=None,
            latency_ms=latency_ms,
            cost_micro_usd=cost,
            log_id=None,
            key=item.key,
        )

    # ───────────────────────── the Claude fallback and the audit ─────────────────────────

    async def _fallback_call(self, item: _Item, sent_state: JsonContent, call_ctx: CallContext) -> _Fallback:
        """One Claude answer for ``item``. Never raises (except cancellation); the outcome is in the result."""
        fb = _Fallback()
        if self._claude is None or item.question is None:
            return fb
        model = item.cfg.fallback_model or self._fallback_model
        if self._spend_gate is not None:
            try:
                await self._spend_gate("decision_fallback")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not isinstance(exc, SpendCeilingReached):
                    log.warning("decision_spend_gate_failed", exc_info=True)
                fb.spend_blocked = True
                return fb
        fb.attempted = True
        fb.model = model
        started = self._clock.monotonic()
        try:
            async with self._fallback_sem:
                result = await claude_fallback_result(
                    self._claude, item.spec, sent_state, ctx=call_ctx, model=model, question=item.question
                )
        except asyncio.CancelledError:
            raise
        except SpendCeilingReached:
            fb.attempted = False
            fb.spend_blocked = True
            return fb
        except Exception as exc:
            fb.error = getattr(exc, "kind", None) or type(exc).__name__
            fb.latency_ms = round((self._clock.monotonic() - started) * 1000)
            log.warning("decision_fallback_failed", spec=item.spec.id, error=fb.error)
            return fb
        fb.latency_ms = round((self._clock.monotonic() - started) * 1000)
        fb.data = result.data
        fb.cost_micro_usd = result.usage.cost_micro_usd
        fb.request_id = result.usage.request_id
        try:
            fb.answer = to_answer(item.spec, result.data, question=item.question)
            fb.value = item.spec.interpret(fb.answer)
        except Exception as exc:
            fb.answer = None
            fb.value = None
            fb.error = "invalid_answer"
            log.warning("decision_fallback_invalid", spec=item.spec.id, error=str(exc)[:200])
        return fb

    async def _audit(
        self,
        pack: DecisionPack[Any],
        item: _Item,
        result: DecisionResult[Any],
        call: _Call,
        sent_state: JsonContent,
        ctx: DecisionContext,
        call_ctx: CallContext,
        incumbent_value: dict[str, Any] | None,
    ) -> None:
        """Ask Claude the same question in the background, then write the (single) row of a used Jev answer."""
        fb = _Fallback()
        try:
            async with asyncio.timeout(self._audit_timeout_s):
                fb = await self._fallback_call(item, sent_state, call_ctx)
        except TimeoutError:
            fb.error = "timeout"
        fb.extra["audit"] = True
        if fb.answer is not None:
            fb.extra["audit_agrees"] = fb.value == result.value
        self._record_decision(
            pack, item, result, call, ctx, incumbent_value=incumbent_value, fallback=fb, audit=True
        )

    # ───────────────────────── logging ─────────────────────────

    @staticmethod
    def _incumbent_for(item: _Item, incumbent: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not incumbent:
            return None
        for key in (item.key, item.spec.id):
            if key in incumbent:
                value = _jsonable(incumbent[key])
                return value if isinstance(value, dict) else {"value": value}
        return None

    def _record_decision(
        self,
        pack: DecisionPack[Any],
        item: _Item,
        result: DecisionResult[Any],
        call: _Call,
        ctx: DecisionContext,
        *,
        incumbent_value: dict[str, Any] | None,
        fallback: _Fallback | None = None,
        audit: bool = False,
    ) -> None:
        try:
            fb = fallback if fallback is not None else _Fallback()
            extra: dict[str, Any] = {"key": item.key, "value": _jsonable(result.value)}
            if call.state_hash:
                extra["state_hash"] = call.state_hash
            if call.response is not None:
                extra["jev_model"] = call.response.model
            if call.error_kind:
                extra["jev_error"] = call.error_kind
            if ctx.purpose_tag:
                extra["purpose_tag"] = ctx.purpose_tag
            if fb.model:
                extra["claude_model"] = fb.model
            if fb.request_id:
                extra["claude_request_id"] = fb.request_id
            if fb.error:
                extra["claude_error"] = fb.error
            extra.update(fb.extra)
            claude_answer = result.claude if result.claude is not None else fb.data
            self._recorder.add_decision(
                DecisionRecord(
                    id=result.log_id if result.log_id is not None else uuid7(),
                    created_at=self._clock.now(),
                    spec_id=result.spec_id,
                    spec_version=result.spec_version,
                    pack=pack.id,
                    mode=result.mode,
                    used=result.used,
                    source_used=result.source,
                    escalated=result.escalated,
                    escalation_reason=result.escalation_reason,
                    call_id=call.call_id if call.attempted else None,
                    claude_call_id=None,
                    subject_ref=item.key,
                    jev_answer=result.jev.model_dump(exclude_none=True) if result.jev is not None else None,
                    jev_confidence=result.jev.confidence if result.jev is not None else None,
                    threshold=result.threshold,
                    claude_answer=claude_answer,
                    incumbent=incumbent_value,
                    latency_ms=result.latency_ms,
                    cost_micro_usd=result.cost_micro_usd + (fb.cost_micro_usd if audit else 0),
                    profile_id=ctx.profile_id,
                    analysis_id=ctx.analysis_id,
                    is_sample=ctx.is_sample,
                    extra=extra,
                )
            )
        except Exception:
            log.warning("decision_record_failed", spec=result.spec_id, exc_info=True)

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.warning(
                "decision_task_failed", task=task.get_name(), error=f"{type(exc).__name__}: {exc}"[:300]
            )


__all__ = ["AUDIT_TIMEOUT_S", "FALLBACK_CONCURRENCY", "SHADOW_WAIT_S", "DecisionRunner", "SpendGate"]
