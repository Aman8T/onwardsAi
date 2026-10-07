"""A scripted `JevClient` for tests: raw answers per question id, optional failures and delays, every request recorded.

    jev = FakeJev({
        "career_family": choice_answer("design_ux", options=["design_ux", "other"], p=0.9),
        "depth": score_answer(3, n_levels=6),
        "public_proof": noul_answer(0.95),
    })
    response = await jev.system_one(state, questions, timeout_s=2.5, ctx=ctx)

Answers are scripted as the raw dicts the API returns (so `normalise_answer` runs for real, and a malformed
dict yields `JevError("invalid_response")` exactly as production would), either as a mapping per question id or
as a callable ``(state, questions) -> mapping``. An id like ``skill_level#python`` falls back to a script entry
for ``skill_level``. A question with no entry gets an uninformative answer (noul 0.5, uniform choice and
score: confidence 0), or raises `AssertionError` with ``strict=True``.

Failures and delay: ``fail_next(JevError("timeout"))``, ``fail_with(error)`` (every call), ``set_delay(s)``. A delay
runs on the injected clock (instant with a `FakeClock`); a delay longer than ``timeout_s`` sleeps ``timeout_s``
and raises ``JevError("timeout")``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tayyari.core.clock import Clock
from tayyari.llm.errors import JevError
from tayyari.llm.jev import (
    JevQuestion,
    JevResponse,
    JsonContent,
    choice_confidence,
    normalise_answer,
    score_confidence,
)
from tayyari.llm.models import jev_cost_micro
from tayyari.llm.types import CallContext
from tests.fakes.clock import FakeClock

RawAnswer = Mapping[str, Any]
AnswerScript = (
    Mapping[str, RawAnswer] | Callable[[JsonContent, Mapping[str, JevQuestion]], Mapping[str, RawAnswer]]
)


# ───────────────────────── raw answer builders ─────────────────────────


def noul_answer(p: float) -> dict[str, Any]:
    """``{"type": "noul", "noul": p}``."""
    return {"type": "noul", "noul": p}


def choice_answer(
    choice: str,
    probabilities: Mapping[str, float] | None = None,
    *,
    options: Sequence[str] | None = None,
    p: float = 0.9,
    confidence: float | None = None,
) -> dict[str, Any]:
    """A choice answer. ``probabilities`` wins; else ``p`` goes to ``choice`` and the rest is shared among the other
    ``options``; else the choice has probability 1. ``confidence`` defaults to the documented formula."""
    if probabilities is not None:
        probs = dict(probabilities)
    elif options:
        others = [o for o in options if o != choice]
        share = (1.0 - p) / len(others) if others else 0.0
        probs = {o: (p if o == choice else share) for o in options}
        if choice not in probs:
            probs[choice] = p
    else:
        probs = {choice: 1.0}
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probs,
        "confidence": choice_confidence(probs) if confidence is None else confidence,
    }


def score_answer(
    level_or_probabilities: int | Sequence[float],
    *,
    n_levels: int = 6,
    p: float = 0.8,
    confidence: float | None = None,
) -> dict[str, Any]:
    """A score answer. A sequence is the per-level probabilities. An int puts ``p`` on that level and spreads the
    rest over the other levels, weighted by 1/(1 + distance), so neighbours get more than far levels."""
    if isinstance(level_or_probabilities, int):
        level = level_or_probabilities
        weights = {i: 1.0 / (1 + abs(i - level)) for i in range(n_levels) if i != level}
        total = sum(weights.values()) or 1.0
        probs = {i: ((1.0 - p) * weights[i] / total if i != level else p) for i in range(n_levels)}
    else:
        probs = dict(enumerate(level_or_probabilities))
    expected = sum(i * v for i, v in probs.items())
    return {
        "type": "score",
        "score": expected,
        "legend": {str(i): f"level {i}" for i in probs},
        "probabilities": {str(i): v for i, v in probs.items()},
        "confidence": score_confidence(probs) if confidence is None else confidence,
    }


def _uninformative(question: JevQuestion) -> dict[str, Any]:
    if question.kind == "noul":
        return noul_answer(0.5)
    if question.kind == "choice":
        labels = question.option_labels
        return choice_answer(labels[0], {label: 1.0 / len(labels) for label in labels})
    n = question.level_count
    return score_answer([1.0 / n] * n)


# ───────────────────────── the fake ─────────────────────────


@dataclass(frozen=True)
class JevCall:
    """One recorded `system_one` request."""

    state: JsonContent
    questions: dict[str, JevQuestion]
    model: str
    timeout_s: float
    ctx: CallContext

    @property
    def question_ids(self) -> list[str]:
        return list(self.questions)


class FakeJev:
    """Scripted stand-in for `tayyari.llm.jev.JevClient`."""

    def __init__(
        self,
        answers: AnswerScript | None = None,
        *,
        model_id: str = "jev-1.13.0",
        clock: Clock | None = None,
        delay_s: float = 0.0,
        input_tokens: int | None = None,
        strict: bool = False,
    ) -> None:
        self._model_id = model_id
        self._clock: Clock = clock if clock is not None else FakeClock()
        self._script: AnswerScript = answers if answers is not None else {}
        self._delay_s = delay_s
        self._input_tokens = input_tokens
        self._strict = strict
        self._failures: list[JevError] = []
        self._fail_forever: JevError | None = None
        self.calls: list[JevCall] = []
        self.closed = False

    @property
    def model_id(self) -> str:
        return self._model_id

    # ── scripting ──

    def script(self, answers: AnswerScript) -> FakeJev:
        """Replace the whole answer script."""
        self._script = answers
        return self

    def set_answer(self, question_id: str, raw: RawAnswer) -> FakeJev:
        """Add or replace the answer for one question id (the script must be a mapping)."""
        if callable(self._script):
            raise TypeError("set_answer needs a mapping script, not a callable")
        self._script = {**self._script, question_id: raw}
        return self

    def fail_next(self, error: JevError, *, times: int = 1) -> FakeJev:
        """Raise ``error`` on the next ``times`` calls (they are still recorded)."""
        self._failures.extend([error] * times)
        return self

    def fail_with(self, error: JevError | None) -> FakeJev:
        """Raise ``error`` on every call until called again with None."""
        self._fail_forever = error
        return self

    def set_delay(self, seconds: float) -> FakeJev:
        self._delay_s = seconds
        return self

    # ── JevClient ──

    async def system_one(
        self,
        state: JsonContent,
        questions: Mapping[str, JevQuestion],
        *,
        model: str | None = None,
        timeout_s: float,
        ctx: CallContext,
    ) -> JevResponse:
        served = model or self._model_id
        self.calls.append(JevCall(state, dict(questions), served, timeout_s, ctx))
        started = self._clock.monotonic()

        if self._delay_s > 0:
            if self._delay_s > timeout_s:
                await self._clock.sleep(timeout_s)
                raise JevError("timeout", f"fake Jev timed out after {timeout_s}s")
            await self._clock.sleep(self._delay_s)
        if self._failures:
            raise self._failures.pop(0)
        if self._fail_forever is not None:
            raise self._fail_forever

        scripted = self._script(state, questions) if callable(self._script) else self._script
        raw_answers: dict[str, dict[str, Any]] = {}
        for question_id, question in questions.items():
            raw = scripted.get(question_id)
            if raw is None:
                raw = scripted.get(question_id.split("#", 1)[0])
            if raw is None:
                if self._strict:
                    raise AssertionError(f"FakeJev: no answer scripted for question {question_id!r}")
                raw = _uninformative(question)
            raw_answers[question_id] = {"type": question.kind, **raw}
        answers = {qid: normalise_answer(questions[qid].kind, raw) for qid, raw in raw_answers.items()}

        request_json: dict[str, Any] = {
            "state": state,
            "model": served,
            "questions": {qid: q.to_wire() for qid, q in questions.items()},
        }
        input_tokens = (
            self._input_tokens
            if self._input_tokens is not None
            else max(1, len(json.dumps(request_json, sort_keys=True)) // 4)
        )
        output_tokens = 8 * len(questions)
        response_json: dict[str, Any] = {
            "model": served,
            "answers": raw_answers,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
        return JevResponse(
            model=served,
            answers=answers,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=f"fake-jev-{len(self.calls)}",
            latency_ms=round((self._clock.monotonic() - started) * 1000),
            request_json=request_json,
            response_json=response_json,
            cost_micro_usd=jev_cost_micro(served, input_tokens),
        )

    async def aclose(self) -> None:
        self.closed = True


__all__ = [
    "AnswerScript",
    "FakeJev",
    "JevCall",
    "RawAnswer",
    "choice_answer",
    "noul_answer",
    "score_answer",
]
