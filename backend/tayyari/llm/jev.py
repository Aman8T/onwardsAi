"""Jev (TypeSafe System One) interface: question and answer types, answer normalisation and the client Protocol.

Wire format (docs.typesafe.ai/api, verified 2026-10-07): ``POST /v1/systemone`` with
``{"state", "model", "questions": {id: {"type", "instructions", "criteria"}}}`` answers
``{"model", "answers": {id: ...}, "usage": {"input_tokens", "output_tokens"}}`` where an answer is

    noul    {"type": "noul", "noul": p}                                  p = probability of yes, no confidence
    choice  {"type": "choice", "choice": label, "probabilities": {label: p}, "confidence": c}
    score   {"type": "score", "score": expected_level, "probabilities": {"0": p, ...}, "confidence": c,
             "legend": {"0": description, ...}}

Everything code consumes is the normalised `JevAnswer`: argmax and thresholds are the caller's job, and the
confidence is always on one scale: ``|2p - 1|`` for a noul, and the API's value (or the documented
formula when it is missing) for a choice or a score.

The transport lives in an adapter (`llm/jev_http.py`, a raw httpx client); this module has no I/O.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from .errors import JevError
from .types import CallContext

JevKind = Literal["noul", "choice", "score"]
JsonContent = str | dict[str, Any] | list[Any]
"""State, instructions and criteria values: free text, a JSON object or a JSON array."""

MAX_CHOICE_OPTIONS = 255
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10
_NOUL_CRITERIA_KEYS = frozenset({"true", "false"})


# ───────────────────────── questions ─────────────────────────


def _blank(value: object) -> bool:
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict | list):
        return not value
    return value is None


class JevQuestion(BaseModel):
    """One question about the shared state. The wording is fixed in code (a spec's template); dynamic parts
    go through ``instructions`` as an object.

    - ``noul``: yes or no. ``criteria`` is optional: ``{"true": "...", "false": "..."}``.
    - ``choice``: pick one. ``criteria`` maps each option label to its description (or None); 1 to 255 options.
    - ``score``: a degree on ordered levels. ``criteria`` is the list of level descriptions, 2 to 10; a level's
      number is its position, starting at 0.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: JevKind
    instructions: JsonContent | None = None
    criteria: dict[str, JsonContent | None] | list[JsonContent] | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if isinstance(self.instructions, str) and not self.instructions.strip():
            raise ValueError("instructions must not be blank")
        criteria = self.criteria
        if self.kind == "noul":
            if criteria is None:
                return self
            if not isinstance(criteria, dict) or not set(criteria) <= _NOUL_CRITERIA_KEYS:
                raise ValueError('noul criteria must be an object with only "true" and/or "false" keys')
        elif self.kind == "choice":
            if not isinstance(criteria, dict) or not criteria:
                raise ValueError(
                    "choice criteria must be an object mapping each option label to its description"
                )
            if len(criteria) > MAX_CHOICE_OPTIONS:
                raise ValueError(f"a choice allows at most {MAX_CHOICE_OPTIONS} options, got {len(criteria)}")
            if any(not label.strip() for label in criteria):
                raise ValueError("choice option labels must not be blank")
        else:
            if not isinstance(criteria, list):
                raise ValueError("score criteria must be a list of level descriptions")
            if not MIN_SCORE_LEVELS <= len(criteria) <= MAX_SCORE_LEVELS:
                raise ValueError(
                    f"a score needs {MIN_SCORE_LEVELS} to {MAX_SCORE_LEVELS} levels, got {len(criteria)}"
                )
            if any(_blank(level) for level in criteria):
                raise ValueError("score level descriptions must not be blank")
        return self

    @classmethod
    def noul(
        cls, instructions: JsonContent, criteria: Mapping[str, JsonContent | None] | None = None
    ) -> JevQuestion:
        return cls(
            kind="noul", instructions=instructions, criteria=None if criteria is None else dict(criteria)
        )

    @classmethod
    def choice(cls, instructions: JsonContent, options: Mapping[str, JsonContent | None]) -> JevQuestion:
        return cls(kind="choice", instructions=instructions, criteria=dict(options))

    @classmethod
    def score(cls, instructions: JsonContent, levels: list[JsonContent]) -> JevQuestion:
        return cls(kind="score", instructions=instructions, criteria=list(levels))

    @property
    def option_labels(self) -> tuple[str, ...]:
        """The labels of a choice, in the order they were declared (empty for other kinds)."""
        if self.kind == "choice" and isinstance(self.criteria, dict):
            return tuple(self.criteria)
        return ()

    @property
    def level_count(self) -> int:
        """The number of levels of a score (0 for other kinds)."""
        if self.kind == "score" and isinstance(self.criteria, list):
            return len(self.criteria)
        return 0

    def to_wire(self) -> dict[str, Any]:
        """The question as sent: ``{"type", "instructions", "criteria"}``, leaving out whatever is None.

        The result is a deep copy, so editing it never changes the question.
        """
        wire: dict[str, Any] = {"type": self.kind}
        if self.instructions is not None:
            wire["instructions"] = copy.deepcopy(self.instructions)
        if self.criteria is not None:
            wire["criteria"] = copy.deepcopy(self.criteria)
        return wire


# ───────────────────────── answers ─────────────────────────


class JevAnswer(BaseModel):
    """A normalised answer. Exactly one of ``noul`` / ``choice`` / ``score_level`` is the headline value.

    - noul: ``noul`` is the probability of yes; ``confidence = |2p - 1|``.
    - choice: ``choice`` is the winning label; ``probabilities`` maps every option to its probability.
    - score: ``score_level`` is the argmax level (ties go to the lower level), ``score_expected`` the
      probability-weighted mean level (not rounded), ``probabilities`` maps ``"0"``..``"n-1"`` to p.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: JevKind
    noul: float | None = None
    choice: str | None = None
    score_level: int | None = None
    score_expected: float | None = None
    probabilities: dict[str, float] | None = None
    confidence: float


def _invalid(message: str) -> JevError:
    return JevError("invalid_response", message)


def _number(raw: Mapping[str, Any], key: str, *, required: bool) -> float | None:
    value = raw.get(key)
    if value is None:
        if required:
            raise _invalid(f"answer is missing {key!r}")
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _invalid(f"answer field {key!r} is not a finite number: {value!r}")
    return float(value)


def _clip01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _probabilities(raw: Mapping[str, Any], *, kind: JevKind) -> dict[str, float] | None:
    probs = raw.get("probabilities")
    if probs is None:
        return None
    items: list[tuple[str, Any]]
    if isinstance(probs, Mapping):
        items = [(str(k), v) for k, v in probs.items()]
    elif kind == "score" and isinstance(probs, list | tuple):
        items = [(str(i), v) for i, v in enumerate(probs)]
    else:
        raise _invalid("answer probabilities must be an object")
    out: dict[str, float] = {}
    for label, value in items:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise _invalid(f"probability for {label!r} is not a finite number: {value!r}")
        out[label] = _clip01(float(value))
    return out


def _argmax_label(probabilities: Mapping[str, float]) -> str:
    """The label with the highest probability; the first one declared wins a tie."""
    return max(probabilities, key=lambda label: probabilities[label])


def choice_confidence(probabilities: Mapping[str, float]) -> float:
    """``(p_max - 1/n) / (1 - 1/n)``: 0 for an even split, 1 for certainty; 1.0 for a single option."""
    n = len(probabilities)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0
    p_max = max(probabilities.values())
    return _clip01((p_max - 1.0 / n) / (1.0 - 1.0 / n))


def score_confidence(probabilities: Mapping[int, float]) -> float:
    """``max(0, 1 - sum_i p_i * |i - m| / MAD_uniform)`` for levels ``0..n-1``, ``m`` the most likely level.

    ``MAD_uniform = (1/n) * sum_i |i - (n-1)/2|`` is the mean absolute deviation of a uniform distribution,
    so probability on a neighbouring level costs less confidence than the same probability further away.
    A single level has confidence 1.0.
    """
    n = max(probabilities) + 1 if probabilities else 0
    if n <= 1:
        return 1.0 if n == 1 else 0.0
    mode = max(range(n), key=lambda i: probabilities.get(i, 0.0))
    spread = sum(p * abs(i - mode) for i, p in probabilities.items())
    mad_uniform = sum(abs(i - (n - 1) / 2) for i in range(n)) / n
    return _clip01(1.0 - spread / mad_uniform)


def normalise_answer(kind: JevKind, raw: Mapping[str, Any]) -> JevAnswer:
    """Turn one raw API answer into a `JevAnswer`.

    Raises `JevError("invalid_response")` when the answer does not fit ``kind`` (wrong ``type``, missing or
    non-numeric fields). A missing ``confidence`` on a choice or score is computed with the documented
    formulas; a choice with neither probabilities nor confidence gets confidence 0.0 (never acted on).
    """
    if not isinstance(raw, Mapping):
        raise _invalid(f"{kind} answer is not an object")
    declared = raw.get("type")
    if declared is not None and declared != kind:
        raise _invalid(f"expected a {kind} answer, got {declared!r}")

    if kind == "noul":
        p = _clip01(_number(raw, "noul", required=True) or 0.0)
        return JevAnswer(kind="noul", noul=p, confidence=abs(2.0 * p - 1.0))

    probabilities = _probabilities(raw, kind=kind)
    api_confidence = _number(raw, "confidence", required=False)

    if kind == "choice":
        label = raw.get("choice")
        if not isinstance(label, str) or not label:
            if not probabilities:
                raise _invalid("choice answer has neither 'choice' nor 'probabilities'")
            label = _argmax_label(probabilities)
        if api_confidence is not None:
            confidence = _clip01(api_confidence)
        elif probabilities:
            confidence = choice_confidence(probabilities)
        else:
            confidence = 0.0
        return JevAnswer(kind="choice", choice=label, probabilities=probabilities, confidence=confidence)

    # score
    if not probabilities:
        raise _invalid("score answer has no 'probabilities'")
    levels: dict[int, float] = {}
    for key, p in probabilities.items():
        try:
            level = int(key)
        except ValueError:
            raise _invalid(f"score level {key!r} is not an integer") from None
        if level < 0:
            raise _invalid(f"score level {key!r} is negative")
        levels[level] = p
    top = max(levels, key=lambda lv: (levels[lv], -lv))  # highest probability, lowest level on a tie
    expected = _number(raw, "score", required=False)
    if expected is None:
        expected = sum(lv * p for lv, p in levels.items())
    confidence = _clip01(api_confidence) if api_confidence is not None else score_confidence(levels)
    return JevAnswer(
        kind="score",
        score_level=top,
        score_expected=expected,
        probabilities={str(lv): levels[lv] for lv in sorted(levels)},
        confidence=confidence,
    )


# ───────────────────────── response and client ─────────────────────────


@dataclass(frozen=True, slots=True)
class JevResponse:
    """The result of one `system_one` call, with the exact request and response JSON kept for replay."""

    model: str  # the model that served the request (a pinned id even when "jev-latest" was asked for)
    answers: dict[str, JevAnswer]
    input_tokens: int
    output_tokens: int
    request_id: str | None
    latency_ms: int
    request_json: dict[str, Any]  # {"state", "model", "questions"} as sent
    response_json: dict[str, Any]  # the raw body
    cost_micro_usd: int


class JevClient(Protocol):
    """One Jev transport. The raw httpx adapter is the only implementation; an SDK adapter would be one more file."""

    @property
    def model_id(self) -> str:
        """The pinned default model, used when a call passes no ``model``."""
        ...

    async def system_one(
        self,
        state: JsonContent,
        questions: Mapping[str, JevQuestion],
        *,
        model: str | None = None,
        timeout_s: float,
        ctx: CallContext,
    ) -> JevResponse:
        """Ask every question about ``state`` in one request.

        Raises `JevError` (``timeout`` after ``timeout_s``, ``disabled`` when no key is configured, ...);
        `SpendCeilingReached` when wrapped by the guard. Cancellation propagates.
        """
        ...

    async def aclose(self) -> None:
        """Release the HTTP connection pool."""
        ...


__all__ = [
    "MAX_CHOICE_OPTIONS",
    "MAX_SCORE_LEVELS",
    "MIN_SCORE_LEVELS",
    "JevAnswer",
    "JevClient",
    "JevKind",
    "JevQuestion",
    "JevResponse",
    "JsonContent",
    "choice_confidence",
    "normalise_answer",
    "score_confidence",
]
