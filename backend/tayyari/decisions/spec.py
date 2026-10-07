"""The decision vocabulary: a spec says what to ask, a pack batches specs over one state, a config says how
much to trust the answer, and a result says what happened.

    DecisionSpec    one question with a fixed template, `interpret` (argmax and thresholds live in code, never a
                    raw fraction), a `neutral` value for "no answer" and a default `DecisionConfig`.
    DecisionPack    specs that share one state; they go to Jev in ONE call. `build_state` makes the state.
    DecisionConfig  mode off | shadow | primary, the confidence threshold, the audit rate, the fallback, the timeout.
    DecisionResult  one decision's outcome. ``used`` is the only flag a caller needs: act on ``value`` only when
                    it is True. Otherwise do what you did before decisions existed.
    PackResult      the results of one `DecisionRunner.run`, by question key.

Question keys: the question sent to Jev (and the key of its result) is the spec id, so ``evidence.support`` is
asked as ``evidence.support``. A spec with a `bind` function can be asked several times over one state (a
skill level per skill): each instance is ``<spec id>#<instance>``, for example ``skill.level#python``, and
the caller supplies its arguments in ``bindings={"skill.level#python": {...}}``. A spec without `bind` is asked
once.

Specs are built with keyword arguments only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args
from uuid import UUID

from ..llm.jev import JevAnswer, JevQuestion, JsonContent

Mode = Literal["off", "shadow", "primary"]
Fallback = Literal["claude", "skip"]
EscalationReason = Literal["low_confidence", "jev_error", "circuit_open", "jev_disabled", "spend"]
Source = Literal["jev", "claude", "default"]

MODES: tuple[str, ...] = get_args(Mode)
FALLBACKS: tuple[str, ...] = get_args(Fallback)

INSTANCE_SEPARATOR = "#"


def question_key(spec_id: str, instance: str | None = None) -> str:
    """``spec_id`` for a plain question, ``spec_id#instance`` for one instance of a bound spec."""
    return spec_id if instance is None else f"{spec_id}{INSTANCE_SEPARATOR}{instance}"


def split_key(key: str) -> tuple[str, str | None]:
    """The inverse of `question_key`: ``("skill.level", "python")`` for ``skill.level#python``."""
    spec_id, sep, instance = key.partition(INSTANCE_SEPARATOR)
    return (spec_id, instance) if sep else (spec_id, None)


@dataclass(frozen=True)
class DecisionConfig:
    """How one spec behaves. Defaults live in ``defaults.toml``; ``TAYYARI_DECISION_OVERRIDES`` can change them live.

    - ``mode``: ``off`` asks nothing; ``shadow`` asks Jev and logs next to the incumbent but never uses the answer;
      ``primary`` uses Jev's answer when ``confidence >= threshold`` and otherwise (or on any failure) the fallback.
    - ``threshold``: the confidence a Jev answer needs to be used (``|2p-1|`` for a noul).
    - ``audit_rate``: the share of used Jev answers that also get a background Claude answer, to track agreement.
    - ``fallback``: ``claude`` answers the same rubric with a Haiku call; ``skip`` leaves the decision neutral.
    - ``timeout_s``: the budget of one Jev attempt.
    - ``fallback_model``: the Claude model for the fallback; None uses the runner's.
    """

    mode: Mode
    threshold: float
    audit_rate: float = 0.0
    fallback: Fallback = "claude"
    timeout_s: float = 2.5
    fallback_model: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.fallback not in FALLBACKS:
            raise ValueError(f"fallback must be one of {FALLBACKS}, got {self.fallback!r}")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be between 0 and 1, got {self.threshold!r}")
        if not 0.0 <= self.audit_rate <= 1.0:
            raise ValueError(f"audit_rate must be between 0 and 1, got {self.audit_rate!r}")
        if not self.timeout_s > 0:
            raise ValueError(f"timeout_s must be positive, got {self.timeout_s!r}")
        if self.fallback_model is not None and not self.fallback_model.strip():
            raise ValueError("fallback_model must not be blank")


@dataclass(frozen=True, kw_only=True)
class DecisionSpec[R]:
    """One question Jev can answer, with everything needed to use or replace its answer.

    ``version`` is stored on every log row: bump it when the wording or criteria change, so evaluations never
    mix answers to different questions. ``question`` is the fixed template (and, for a bound spec, the shape the
    bound questions must keep: the Claude fallback and the log derive from it).
    """

    id: str
    version: str
    pack: str
    question: JevQuestion
    interpret: Callable[[JevAnswer], R]
    neutral: R
    default: DecisionConfig
    bind: Callable[[Mapping[str, Any]], JevQuestion] | None = None

    def __post_init__(self) -> None:
        if not self.id.strip() or INSTANCE_SEPARATOR in self.id:
            raise ValueError(f"spec id must be non-empty and contain no {INSTANCE_SEPARATOR!r}: {self.id!r}")
        if not self.version.strip():
            raise ValueError(f"spec {self.id!r} needs a version")
        if not self.pack.strip():
            raise ValueError(f"spec {self.id!r} needs a pack")


@dataclass(frozen=True)
class DecisionPack[S]:
    """Specs that share a state: one Jev call (or several chunks of at most JEV_MAX_QUESTIONS_PER_CALL questions)."""

    id: str
    specs: tuple[DecisionSpec[Any], ...]
    build_state: Callable[[S], JsonContent]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("a pack needs an id")
        seen: set[str] = set()
        for spec in self.specs:
            if spec.pack != self.id:
                raise ValueError(f"spec {spec.id!r} belongs to pack {spec.pack!r}, not {self.id!r}")
            if spec.id in seen:
                raise ValueError(f"pack {self.id!r} lists spec {spec.id!r} twice")
            seen.add(spec.id)

    def spec(self, spec_id: str) -> DecisionSpec[Any]:
        for spec in self.specs:
            if spec.id == spec_id:
                return spec
        raise KeyError(spec_id)


@dataclass(frozen=True)
class DecisionResult[R]:
    """The outcome of one decision.

    - ``mode``: the effective mode (a shadow spec that could not call Jev reads ``off``).
    - ``value`` / ``used``: ``used`` is True when the caller may act on ``value`` (Jev above its threshold, or the
      Claude fallback). When it is False, ``value`` is the spec's neutral value, except for a shadow decision
      where it is Jev's interpreted answer (for logging; never act on it).
    - ``source``: who produced ``value``: ``jev``, ``claude`` or ``default``.
    - ``confidence``: of the answer behind ``value`` (Jev's, or the mapped Claude one), None for a default.
    - ``escalated``: a Claude fallback was attempted. ``escalation_reason`` says why Jev's answer was not used
      (it is also set when the fallback is ``skip``, or when nothing could be asked).
    - ``jev`` / ``claude``: the raw normalised Jev answer and the raw Claude answer, when they exist.
    - ``latency_ms`` / ``cost_micro_usd``: Jev (this question's share of the call) plus the fallback.
    - ``log_id``: the decision_log row id (rows are written in the background; None when nothing was logged).
    """

    spec_id: str
    spec_version: str
    mode: Mode
    value: R | None
    used: bool
    source: Source
    confidence: float | None
    threshold: float
    escalated: bool
    escalation_reason: EscalationReason | None
    jev: JevAnswer | None
    claude: dict[str, Any] | None
    latency_ms: int
    cost_micro_usd: int
    log_id: UUID | None
    key: str = ""
    """The question key (``spec_id`` or ``spec_id#instance``)."""


@dataclass(frozen=True)
class PackResult:
    """What one `DecisionRunner.run` returns: a result per question key, in plan order."""

    pack_id: str
    results: Mapping[str, DecisionResult[Any]] = field(default_factory=dict)

    def __getitem__(self, key: str) -> DecisionResult[Any]:
        return self.results[key]

    def __contains__(self, key: object) -> bool:
        return key in self.results

    def __iter__(self) -> Iterator[str]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def get(self, key: str) -> DecisionResult[Any] | None:
        return self.results.get(key)

    def values(self) -> list[DecisionResult[Any]]:
        return list(self.results.values())

    def used_value(self, key: str, default: Any = None) -> Any:
        """``value`` of the result for ``key`` when it is usable, else ``default``."""
        result = self.results.get(key)
        return result.value if result is not None and result.used else default

    def instances(self, spec_id: str) -> dict[str, DecisionResult[Any]]:
        """The results of one spec by instance (``""`` for a spec asked once)."""
        out: dict[str, DecisionResult[Any]] = {}
        for key, result in self.results.items():
            sid, instance = split_key(key)
            if sid == spec_id:
                out[instance or ""] = result
        return out


__all__ = [
    "FALLBACKS",
    "INSTANCE_SEPARATOR",
    "MODES",
    "DecisionConfig",
    "DecisionPack",
    "DecisionResult",
    "DecisionSpec",
    "EscalationReason",
    "Fallback",
    "Mode",
    "PackResult",
    "Source",
    "question_key",
    "split_key",
]
