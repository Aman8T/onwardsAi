"""Builders shared by the decisions tests: small specs of every kind, a pack, and a fully wired runner on fakes.

``build_harness(specs, answers=...)`` returns a `Harness`: a `DecisionRunner` over `FakeJev`, `FakeClaudeClient`,
`FakeRepositories`, a `FakeClock` and a real `DecisionRecorder` (its timer is far away: tests call
``await h.runner.drain(1)`` and then read ``h.repos.decisions``).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from tayyari.core.settings import JevSettings
from tayyari.decisions.config import DecisionConfigStore
from tayyari.decisions.recorder import DecisionRecorder
from tayyari.decisions.runner import DecisionRunner, SpendGate
from tayyari.decisions.service import DecisionContext
from tayyari.decisions.spec import DecisionConfig, DecisionPack, DecisionSpec, Fallback, Mode
from tayyari.llm.breaker import CircuitBreaker
from tayyari.llm.errors import SpendCeilingReached
from tayyari.llm.jev import JevQuestion, JevResponse, JsonContent
from tayyari.llm.types import CallContext
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.clock import FakeClock
from tests.fakes.jev import AnswerScript, FakeJev
from tests.fakes.repos import FakeRepositories

CTX = DecisionContext(
    profile_id="profile-1",
    analysis_id=UUID(int=7),
    is_sample=False,
    request_id="req-1",
    purpose_tag="test",
)
SAMPLE_CTX = DecisionContext(
    profile_id="sample-1", analysis_id=UUID(int=8), is_sample=True, request_id="req-2", purpose_tag="test"
)

OPTIONS = {"alpha": "the first option", "beta": "the second option", "gamma": None}
LEVELS: list[Any] = ["none", "low", "medium", "high"]


def config(
    mode: Mode = "primary",
    threshold: float = 0.6,
    *,
    fallback: Fallback = "claude",
    audit_rate: float = 0.0,
    timeout_s: float = 2.5,
) -> DecisionConfig:
    return DecisionConfig(
        mode=mode, threshold=threshold, audit_rate=audit_rate, fallback=fallback, timeout_s=timeout_s
    )


def noul_spec(
    spec_id: str = "t.flag",
    *,
    mode: Mode = "primary",
    threshold: float = 0.6,
    fallback: Fallback = "claude",
    audit_rate: float = 0.0,
    timeout_s: float = 2.5,
    neutral: bool = False,
    version: str = "1",
    pack: str = "t",
) -> DecisionSpec[bool]:
    return DecisionSpec(
        id=spec_id,
        version=version,
        pack=pack,
        question=JevQuestion.noul("Is the statement true?"),
        interpret=lambda answer: (answer.noul or 0.0) >= 0.5,
        neutral=neutral,
        default=config(mode, threshold, fallback=fallback, audit_rate=audit_rate, timeout_s=timeout_s),
    )


def choice_spec(
    spec_id: str = "t.pick",
    *,
    mode: Mode = "primary",
    threshold: float = 0.5,
    fallback: Fallback = "claude",
    pack: str = "t",
) -> DecisionSpec[str]:
    return DecisionSpec(
        id=spec_id,
        version="1",
        pack=pack,
        question=JevQuestion.choice("Which one?", OPTIONS),
        interpret=lambda answer: answer.choice or "",
        neutral="unknown",
        default=config(mode, threshold, fallback=fallback),
    )


def score_spec(
    spec_id: str = "t.level",
    *,
    mode: Mode = "primary",
    threshold: float = 0.5,
    fallback: Fallback = "claude",
    pack: str = "t",
) -> DecisionSpec[int]:
    return DecisionSpec(
        id=spec_id,
        version="1",
        pack=pack,
        question=JevQuestion.score("How much?", LEVELS),
        interpret=lambda answer: answer.score_level if answer.score_level is not None else -1,
        neutral=-1,
        default=config(mode, threshold, fallback=fallback),
    )


def bound_spec(
    spec_id: str = "t.bound",
    *,
    mode: Mode = "shadow",
    threshold: float = 1.0,
    fallback: Fallback = "skip",
    pack: str = "t",
) -> DecisionSpec[bool]:
    """A noul asked once per binding: ``bindings={"t.bound#x": {"name": "x"}}``."""

    def bind(args: Mapping[str, Any]) -> JevQuestion:
        return JevQuestion.noul({"question": "Is it present?", "name": args["name"]})

    return DecisionSpec(
        id=spec_id,
        version="2",
        pack=pack,
        question=JevQuestion.noul("Is it present?"),
        bind=bind,
        interpret=lambda answer: (answer.noul or 0.0) >= 0.5,
        neutral=False,
        default=config(mode, threshold, fallback=fallback),
    )


def make_pack(*specs: DecisionSpec[Any], pack_id: str = "t") -> DecisionPack[Any]:
    return DecisionPack(id=pack_id, specs=tuple(specs), build_state=lambda subject: {"text": subject})


class SlowJev:
    """Wraps a `FakeJev` and really waits (real time) before answering; tracks how many calls are in flight."""

    def __init__(self, inner: FakeJev, delay_s: float) -> None:
        self.inner = inner
        self.delay_s = delay_s
        self.in_flight = 0
        self.max_in_flight = 0
        self.started = 0

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    async def system_one(
        self,
        state: JsonContent,
        questions: Mapping[str, JevQuestion],
        *,
        model: str | None = None,
        timeout_s: float,
        ctx: CallContext,
    ) -> JevResponse:
        self.started += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay_s)
            return await self.inner.system_one(state, questions, model=model, timeout_s=timeout_s, ctx=ctx)
        finally:
            self.in_flight -= 1

    async def aclose(self) -> None:
        await self.inner.aclose()


class SpendTrip:
    """A spend gate that raises `SpendCeilingReached` for the kinds in ``trip`` (all kinds when ``trip`` is None)."""

    def __init__(self, trip: set[str] | None = None) -> None:
        self.trip = trip
        self.calls: list[str] = []

    async def __call__(self, kind: str) -> None:
        self.calls.append(kind)
        if self.trip is None or kind in self.trip:
            raise SpendCeilingReached(retry_after_s=60)


class ScriptedRandom(random.Random):
    """A `Random` whose ``random()`` returns the scripted values in order (then 0.99)."""

    def __init__(self, values: Sequence[float]) -> None:
        super().__init__(0)
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0) if self._values else 0.99


@dataclass
class Harness:
    clock: FakeClock
    repos: FakeRepositories
    recorder: DecisionRecorder
    breaker: CircuitBreaker
    jev: Any
    fake_jev: FakeJev
    claude: FakeClaudeClient | None
    config: DecisionConfigStore
    runner: DecisionRunner
    gate: SpendTrip | None
    extra: dict[str, Any] = field(default_factory=dict)

    async def drain(self) -> None:
        await self.runner.drain(2.0)

    @property
    def rows(self) -> list[Any]:
        return list(self.repos.decisions.rows.values())

    @property
    def calls(self) -> list[Any]:
        return list(self.repos.decisions.calls.values())

    @property
    def fallback_calls(self) -> list[Any]:
        return [] if self.claude is None else self.claude.calls_for("decision_fallback")


def build_harness(
    answers: AnswerScript | None = None,
    *,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
    jev_enabled: bool = True,
    send_pii: bool = False,
    with_claude: bool = True,
    spend: SpendTrip | None = None,
    rng: random.Random | None = None,
    max_questions_per_call: int | None = None,
    shadow_wait_s: float = 1.5,
    slow_jev_s: float | None = None,
    breaker_failures: int = 2,
    fallback_model: str = "claude-haiku-4-5",
    fake_jev: FakeJev | None = None,
) -> Harness:
    clock = FakeClock()
    repos = FakeRepositories(clock)
    recorder = DecisionRecorder(repos.decisions, clock, flush_interval_s=3600.0)
    breaker = CircuitBreaker(failures=breaker_failures, window_s=30.0, open_for_s=60.0, clock=clock)
    inner = fake_jev if fake_jev is not None else FakeJev(answers, clock=clock)
    jev: Any = SlowJev(inner, slow_jev_s) if slow_jev_s is not None else inner
    claude = FakeClaudeClient(clock=clock) if with_claude else None
    store = DecisionConfigStore(None, overrides, jev_enabled=jev_enabled, send_pii=send_pii)
    gate: SpendGate | None = spend
    kwargs: dict[str, Any] = {}
    if max_questions_per_call is not None:
        kwargs["max_questions_per_call"] = max_questions_per_call
    runner = DecisionRunner(
        jev,
        claude,
        recorder,
        store,
        breaker,
        gate,
        clock,
        fallback_model,
        rng=rng,
        shadow_wait_s=shadow_wait_s,
        **kwargs,
    )
    return Harness(clock, repos, recorder, breaker, jev, inner, claude, store, runner, spend)


def jev_settings(**overrides: Any) -> JevSettings:
    return JevSettings(**overrides)


__all__ = [
    "CTX",
    "LEVELS",
    "OPTIONS",
    "SAMPLE_CTX",
    "Harness",
    "ScriptedRandom",
    "SlowJev",
    "SpendTrip",
    "bound_spec",
    "build_harness",
    "choice_spec",
    "config",
    "jev_settings",
    "make_pack",
    "noul_spec",
    "score_spec",
]
