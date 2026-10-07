"""Helpers the decision packs share: plain text cuts for states, readers of a normalised Jev answer, and the
"may I act on this result" checks.

A pack never trusts the runner's ``used`` flag alone for a decision that moves something on screen: it also
checks the confidence against its own bar (a config override must not be able to lower the bar of a safety rule
such as "withhold web tools only when p <= 0.15"). `acted_on` and `used_value` take that bar as an argument.
"""

from __future__ import annotations

from typing import Any

from ...llm.jev import JevAnswer
from ..spec import DecisionResult, PackResult

SIGNAL_MAX_CHARS = 32
"""Longest chip label shown on the analysis screen (the UI truncates anything longer)."""


# ───────────────────────── state text ─────────────────────────


def head(text: str | None, limit: int) -> str:
    """The first ``limit`` characters of ``text`` (stripped); ``""`` for None. A plain cut with no marker: the text is
    state for a model, not UI copy, and a marker would only add noise to the question."""
    if not text or limit <= 0:
        return ""
    return text.strip()[:limit]


def tail(text: str | None, limit: int) -> str:
    """The last ``limit`` characters of ``text`` (stripped); ``""`` for None."""
    if not text or limit <= 0:
        return ""
    return text.strip()[-limit:]


# ───────────────────────── reading an answer ─────────────────────────


def noul_probability(answer: JevAnswer) -> float | None:
    """The probability of yes of a noul answer."""
    return answer.noul


def noul_yes(answer: JevAnswer) -> bool | None:
    """The argmax of a noul: True at ``p >= 0.5``. How sure it is lives in the result's confidence (``|2p-1|``)."""
    return None if answer.noul is None else answer.noul >= 0.5


def choice_label(answer: JevAnswer) -> str | None:
    """The winning label of a choice answer."""
    return answer.choice or None


def score_level(answer: JevAnswer) -> int | None:
    """The argmax level of a score answer (a tie goes to the lower level, see `normalise_answer`)."""
    return answer.score_level


def noul_criteria(true_text: str, false_text: str) -> dict[str, str]:
    """The ``{"true": ..., "false": ...}`` criteria of a noul question."""
    return {"true": true_text, "false": false_text}


# ───────────────────────── may I act on it ─────────────────────────


def acted_on(result: DecisionResult[Any] | None, *, min_confidence: float = 0.0) -> bool:
    """True when ``result`` may drive behaviour: the runner marked it used, it carries a value, and its confidence
    reaches ``min_confidence`` (a bar the pack sets itself, on top of the configured threshold)."""
    if result is None or not result.used or result.value is None:
        return False
    return min_confidence <= 0 or (result.confidence or 0.0) >= min_confidence


def used_value(results: PackResult, key: str, *, min_confidence: float = 0.0) -> Any:
    """``value`` of the result for ``key`` when `acted_on`, else None."""
    result = results.get(key)
    if result is None or not acted_on(result, min_confidence=min_confidence):
        return None
    return result.value


def confidence_of(results: PackResult, key: str) -> float | None:
    """The confidence behind the result for ``key`` (Jev's, or the mapped Claude one); None when there is none."""
    result = results.get(key)
    return None if result is None else result.confidence


__all__ = [
    "SIGNAL_MAX_CHARS",
    "acted_on",
    "choice_label",
    "confidence_of",
    "head",
    "noul_criteria",
    "noul_probability",
    "noul_yes",
    "score_level",
    "tail",
    "used_value",
]
