"""The chat web-search gate: may the coach use web tools for this message?

State: ``{"message": <the message, at most JEV_GATE_MESSAGE_CHARS>, "thread": "<origin kind>: <title>"}``. One noul,
``needs_outside_info``.

The policy is deliberately lopsided. A search that was wrongly withheld hurts the answer; one that was allowed and not
needed costs nothing unless the model uses it. So tools are withheld only when Jev is sure the message can be answered
from the person's own profile: ``p <= 0.15`` AND confidence ``|2p-1| >= 0.7`` (the same thing, written twice so a
lowered configured threshold cannot weaken the rule). Everything else, including any error, a timeout or a low
confidence, allows tools. Code decides first: a link in the message always allows tools (the coach may be asked to
read it), and an empty message is not worth a call.

"Withhold" is `tool_choice: none` with the tools still declared, so the prompt cache prefix does not change.
"""

from __future__ import annotations

import re
from typing import Any

from ...core.limits import JEV_GATE_MESSAGE_CHARS
from ...llm.jev import JevQuestion
from ..service import GateInput, GateResult
from ..spec import DecisionConfig, DecisionPack, DecisionSpec, PackResult
from .common import head, noul_criteria, noul_probability, used_value

PACK_ID = "chat_gate"
SPEC_ID = "chat.web_gate"
VERSION = "1"

WITHHOLD_AT_OR_BELOW = 0.15
"""Tools are withheld only when the probability that outside information is needed is at or below this."""
WITHHOLD_MIN_CONFIDENCE = 0.7
THREAD_MAX_CHARS = 300

WEB_GATE_SPEC: DecisionSpec[float | None] = DecisionSpec(
    id=SPEC_ID,
    version=VERSION,
    pack=PACK_ID,
    question=JevQuestion.noul(
        "Does a good answer to this message need current facts from outside the person's own profile, such as "
        "job postings, salaries, courses, tools, news or a specific web page?",
        noul_criteria(
            "It asks for current or external information, resources, links, prices or dates, or to read a page",
            "It can be answered from the person's background and general career knowledge",
        ),
    ),
    interpret=noul_probability,
    neutral=None,
    default=DecisionConfig(mode="primary", threshold=0.7, audit_rate=0.05, fallback="claude", timeout_s=2.0),
)


def build_state(inp: GateInput) -> dict[str, Any]:
    return {
        "message": head(inp.message, JEV_GATE_MESSAGE_CHARS),
        "thread": head(inp.thread, THREAD_MAX_CHARS),
    }


CHAT_GATE_PACK: DecisionPack[GateInput] = DecisionPack(
    id=PACK_ID, specs=(WEB_GATE_SPEC,), build_state=build_state
)

_LINK = re.compile(
    r"https?://\S+|\bwww\.\S+"
    r"|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|app|ai|co|in|me|edu|gov|xyz|tech|so|sh)\b",
    re.IGNORECASE,
)


def has_link(message: str) -> bool:
    """True when the message contains a URL or a bare domain such as ``github.com/me``."""
    return _LINK.search(message) is not None


def interpret_gate(results: PackResult) -> GateResult:
    """Withhold tools only for a sure ``p <= 0.15``; allow them in every other case."""
    result = results.get(SPEC_ID)
    p = used_value(results, SPEC_ID, min_confidence=WITHHOLD_MIN_CONFIDENCE)
    if isinstance(p, float) and p <= WITHHOLD_AT_OR_BELOW:
        return GateResult(allow_tools=False, reason="answerable from the profile", p_outside=p)
    p_outside: float | None = None  # what was answered, even when it was not sure enough to act on
    if result is not None:
        if result.used and isinstance(result.value, float):
            p_outside = result.value
        elif result.jev is not None:
            p_outside = result.jev.noul
    if p_outside is None:
        reason = "no answer, tools allowed"
    elif p_outside <= 0.5:
        reason = "not sure enough to withhold tools"
    else:
        reason = "may need outside information"
    return GateResult(allow_tools=True, reason=reason, p_outside=p_outside)


__all__ = [
    "CHAT_GATE_PACK",
    "PACK_ID",
    "SPEC_ID",
    "THREAD_MAX_CHARS",
    "VERSION",
    "WEB_GATE_SPEC",
    "WITHHOLD_AT_OR_BELOW",
    "WITHHOLD_MIN_CONFIDENCE",
    "build_state",
    "has_link",
    "interpret_gate",
]
