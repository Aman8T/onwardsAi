"""The judge: Claude Haiku reads one answer; code decides the verdict.

The schema puts the citations before the verdict: ``quotes`` (and a story's four parts) come first, then ``onTopic``, the
``verdict``, ``why`` and ``newFact``. Then code:

    quotes      a quote counts only when it is a passage of the answer (`jobsearch.judge.quoted`: case, spacing, quote marks
                and dashes are ignored, 3 to 40 words). The deciding quote is the longest valid one. None valid: Not
                assessed for this rung; the person is never marked down for a judge that could not quote them.
    story       each part counts only with a valid quote. Defended needs situation, action and result; with an action quote
                the most it can be is Shaky; with none, Couldn't defend.
    floor       an answer to the easier question is at most Shaky.
    why         one sentence about the answer. One that reads the person (confidence, tone, accent, filler words ...), is too
                long, or brings a name or number the answer and sources do not hold gets the verdict's own sentence; so does
                a `why` whose verdict code changed.
    newFact     kept only on a Defended answer to an ask, gap, claim or story slot (never a work sample's task): at most 200
                characters, every name and number in it from the answer, not already in the slot's sources, and not
                written about the person ("The person ...", "They ...", "She ..."): it is their own line, "Tested ...".
    onTopic     the model's word, used by the service: once a slot, an off-topic answer is asked again; the second is judged
                as it stands.

The rung that follows, and the slot's best verdict across rungs, are the service's, also in code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from tayyari.contract.interview import PlanSlot, Rung, SlotKind, SlotSource, StoryParts, Verdict
from tayyari.core.text import tidy
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext, TextBlock
from tayyari.prompts.interview import JUDGE_SYSTEM, build_judge_prompt

from .sources import source_lines, sources_text
from .state import RANK
from .text import (
    clean_quote,
    fold,
    has_personality,
    locate,
    passage,
    tokens,
    unsupported,
)

JUDGE_MAX_TOKENS: Final = 900
WHY_MAX_CHARS: Final = 160
FACT_MAX_CHARS: Final = 200
FACT_MIN_WORDS: Final = 3
MAX_QUOTES: Final = 3

WHY_TEMPLATE: Final[dict[str, str]] = {
    "defended": "You named what you did and what came of it.",
    "shaky": "Some of it, but not how or what was yours.",
    "couldnt": "No example or no specifics yet.",
}
NO_QUOTE_WHY: Final = "Nothing in the answer could be quoted word for word, so it isn't assessed."

_PARTS: Final = ("situation", "action", "result", "reflection")
_VERDICTS: Final[dict[str, Verdict]] = {"defended": "defended", "shaky": "shaky", "couldnt": "couldnt"}

JUDGE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["quotes", "onTopic", "story", "verdict", "why", "newFact"],
    "properties": {
        "quotes": {"type": "array", "items": {"type": "string"}},
        "onTopic": {"type": "boolean"},
        "story": {
            "type": "object",
            "additionalProperties": False,
            "required": list(_PARTS),
            "properties": {part: {"type": "string"} for part in _PARTS},
        },
        "verdict": {"type": "string", "enum": ["defended", "shaky", "couldnt"]},
        "why": {"type": "string"},
        "newFact": {"type": "string"},
    },
}
"""Citations before the verdict: the answer's own words first, then what they show."""


@dataclass(frozen=True, slots=True)
class Judged:
    """One answer after the checks."""

    verdict: Verdict
    quote: str | None
    why: str
    on_topic: bool = True
    story: StoryParts | None = None
    new_fact: str | None = None
    dropped: dict[str, int] = field(default_factory=dict)
    """What the checks threw away or replaced (by reason, for the log)."""


def lower_of(verdict: Verdict, cap: Verdict) -> Verdict:
    """The lower of two verdicts (Defended is the highest, Not assessed the lowest)."""
    return verdict if RANK[verdict] <= RANK[cap] else cap


def best_of(a: Verdict, b: Verdict) -> Verdict:
    return a if RANK[a] >= RANK[b] else b


def _story_cap(parts: StoryParts) -> Verdict:
    if parts.situation and parts.action and parts.result:
        return "defended"
    return "shaky" if parts.action else "couldnt"


def _why_ok(why: str, around: str) -> bool:
    return (
        bool(why)
        and len(why) <= WHY_MAX_CHARS
        and not has_personality(why, around)
        and not unsupported(why, around)
    )


_THIRD_PERSON = re.compile(
    r"^(?:the\s+(?:person|candidate|applicant|interviewee|user)|they|she|he|this\s+person)\b", re.IGNORECASE
)
"""A fact is kept as the person's own line ("Tested checkout in Safari on iPhone"), never a sentence about them."""
FACT_KINDS: Final = frozenset({"ask", "gap", "claim", "story"})
"""Slots whose answers can become a fact "From you"; a work sample's answers are about the task, not the person's record."""


def _fact_ok(fact: str, answer: str, sources: str) -> bool:
    if not fact or len(fact) > FACT_MAX_CHARS or len(fact.split()) < FACT_MIN_WORDS:
        return False
    if _THIRD_PERSON.match(fact):
        return False
    if unsupported(fact, answer):
        return False
    return not (tokens(fact) <= tokens(sources) or fold(fact) in fold(sources))


def check_judgement(
    raw: Mapping[str, Any] | None,
    *,
    answer: str,
    kind: SlotKind,
    rung: Rung,
    sources: Sequence[SlotSource],
    question: str = "",
) -> Judged:
    """What the model's read of ``answer`` comes to (see the module docstring). Never raises on a malformed read: it
    drops what it cannot use and, with nothing left to stand on, says Not assessed."""
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    folded = [fold(answer)]
    valid: list[str] = []
    listed = data.get("quotes")
    for value in (listed if isinstance(listed, list) else [])[:MAX_QUOTES]:
        cleaned = clean_quote(value)
        if cleaned and passage(cleaned, folded):
            valid.append(locate(answer, cleaned))
        else:
            drop("quote_not_in_answer")

    parts: StoryParts | None = None
    if kind == "story":
        raw_story = data.get("story")
        said: Mapping[str, Any] = raw_story if isinstance(raw_story, Mapping) else {}
        found: dict[str, bool] = {}
        for name in _PARTS:
            cleaned = clean_quote(said.get(name))
            ok = bool(cleaned) and passage(cleaned, folded)
            found[name] = ok
            if ok:
                valid.append(locate(answer, cleaned))
            elif cleaned:
                drop("story_part_not_in_answer")
        parts = StoryParts(**found)

    on_topic = data.get("onTopic") is not False
    raw_verdict = data.get("verdict")
    chosen = _VERDICTS.get(raw_verdict) if isinstance(raw_verdict, str) else None
    if not valid:
        return Judged("not_assessed", None, NO_QUOTE_WHY, on_topic, parts, None, dropped)
    if chosen is None:
        drop("verdict_invalid")
        return Judged("not_assessed", None, NO_QUOTE_WHY, on_topic, parts, None, dropped)

    verdict: Verdict = chosen
    if parts is not None:
        verdict = lower_of(verdict, _story_cap(parts))
    if rung == "floor":
        verdict = lower_of(verdict, "shaky")
    quote = max(valid, key=lambda q: len(q.split()))

    around = "\n".join([answer, sources_text(sources), question])
    why = tidy(data.get("why"))
    if verdict != chosen or not _why_ok(why, around):
        if verdict == chosen:
            drop("why_replaced")
        why = WHY_TEMPLATE[verdict]

    fact: str | None = None
    if verdict == "defended" and kind in FACT_KINDS:
        candidate = tidy(data.get("newFact"))
        if candidate and _fact_ok(candidate, answer, sources_text(sources)):
            fact = candidate
        elif candidate:
            drop("fact_dropped")
    return Judged(verdict, quote, why, on_topic, parts, fact, dropped)


async def judge_answer(
    *,
    claude: ClaudeClient,
    model: str,
    slot: PlanSlot,
    question: str,
    covers: Sequence[str],
    answer: str,
    rung: Rung,
    context: str | None,
    ctx: CallContext,
) -> Mapping[str, Any]:
    """One small structured call. A model failure propagates (the answer is not recorded, so the person can answer again);
    the spend ceiling is raised by the guarded client before the call."""
    prompt = build_judge_prompt(
        kind=slot.kind,
        topic=slot.topic,
        sources=source_lines(slot.sources),
        question=question,
        covers=covers,
        answer=answer,
        rung=rung,
        context=context,
    )
    result = await claude.structured(
        model=model,
        system=[TextBlock(JUDGE_SYSTEM)],
        user=[TextBlock(prompt)],
        schema=JUDGE_SCHEMA,
        max_tokens=JUDGE_MAX_TOKENS,
        effort=None,
        ctx=replace(ctx, purpose="interview.judge"),
    )
    return result.data


__all__ = [
    "JUDGE_SCHEMA",
    "NO_QUOTE_WHY",
    "WHY_TEMPLATE",
    "Judged",
    "best_of",
    "check_judgement",
    "judge_answer",
    "lower_of",
]
