"""The debrief: counts, the one thing to work on first and the summary are code; Say, Avoid and Fix are Claude Haiku's words,
each checked against what the person said and the sources they were about.

    verdicts     already decided turn by turn (`flow`, `judge`); the debrief only reads them. A slot never reached, skipped, or
                 with nothing quotable is Not assessed, which never counts against anyone.
    counts       by verdict, in code; ``minutes`` is the last turn's second rounded up (at least 1); ``keptUntil`` the day the
                 transcript is deleted.
    firstThing   the first slot that was shaky or couldn't be defended, in the order ask, gap, claim, story, task:
                 "{topic}. {why}". None when every slot that was assessed was defended.
    summary      a role interview's asks, counted: "Of Headout's 2 asks you show, you defended 1 and 1 was shaky."
    say          at most 260 characters in the person's voice; ``[placeholders]`` (2 to 60 characters) stand for what only they
                 know; every number and name outside them is in the person's answers or the slot's sources; no personality
                 words; its sources are the slot's own labels or "Your answer". Failing: for a Defended slot the evidence
                 line that holds a number the answer did not use ("Lead with the result you already have: ..."), else
                 nothing; for the others the kind's template of placeholders.
    avoid        a quote that is a passage of that slot's answers, and short advice without personality words; else dropped.
    fix          only for Shaky and Couldn't defend: a step of at most 200 characters that starts with a verb and says nothing
                 about the person; 5 to 120 minutes; else "Write down one real example for {topic} ...", 15 minutes.
    failure      a model that fails, or a spend ceiling, still gives a debrief: code alone.

A re-drill (I2) adds, in code, each claim's previous mark (``previous``) and how many claims ranked above it ("2 of 3 moved
up", ``redrill``). A claim that could not be defended twice may get ``softer``: a resume line that says less, at most 160
characters, starting with a verb, with no brackets and no personality words, whose every name and number is in the answer
alone (not the resume line, so it cannot restate the claim). One that fails is dropped; no template stands in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Final

from tayyari.contract.interview import (
    AvoidLine,
    CheckResult,
    Debrief,
    FixStep,
    PlanSlot,
    PreviousMark,
    Quote,
    RedrillMoved,
    SayLine,
    SlotKind,
    SlotResult,
    StoryParts,
    Turn,
    Verdict,
    VerdictCounts,
)
from tayyari.core.logging import get_logger
from tayyari.core.text import tidy
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError, SpendCeilingReached
from tayyari.llm.types import CallContext, TextBlock
from tayyari.prompts.interview import DEBRIEF_SYSTEM, DebriefSlot, build_debrief_prompt

from .sources import YOUR_ANSWER, evidence_labels, evidence_of, source_lines, sources_text
from .state import RANK, SessionState, SlotState
from .text import (
    claims_person,
    clean_quote,
    clip_quote,
    fold,
    has_personality,
    inflated,
    locate,
    numbers_of,
    outside_brackets,
    passage,
    placeholders_of,
    starts_with_verb,
    unseen_openers,
    unsupported,
)

log = get_logger(__name__)

DEBRIEF_MAX_TOKENS: Final = 3500
SAY_MAX_CHARS: Final = 260
SAY_MIN_WORDS: Final = 4
AVOID_ADVICE_MAX_CHARS: Final = 120
FIX_MAX_CHARS: Final = 200
FIX_MIN_MINUTES: Final = 5
FIX_MAX_MINUTES: Final = 120
FIX_DEFAULT_MINUTES: Final = 15
SOFTER_MAX_CHARS: Final = 160

SKIPPED_WHY: Final = "You skipped this one. Not assessed never counts against you."
NOT_ASKED_WHY: Final = "This one wasn't asked. Not assessed never counts against you."
UNANSWERED_WHY: Final = "This one wasn't answered. Not assessed never counts against you."
KIND_ORDER: Final[dict[SlotKind, int]] = {"ask": 0, "gap": 1, "claim": 2, "story": 3, "task": 4}

_WHAT_CHANGED = "what changed, with a number you can stand behind"
SAY_TEMPLATES: Final[dict[SlotKind, str]] = {
    "ask": f"“[What the situation was]. I [what you did myself], and [{_WHAT_CHANGED}].”",
    "claim": f"“[What the situation was]. I [what you did myself], and [{_WHAT_CHANGED}].”",
    "story": "“On [the project], [what happened]. I [what you did], and [how it ended].”",
    "gap": "“I haven't done [the ask] at work yet. The closest is [what you did], and I'd start with [your first step].”",
    "task": "“I [what you did first] because [why]. I'd test it by [how].”",
}

DEBRIEF_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["slots"],
    "properties": {
        "slots": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "say", "avoid", "fix", "softer"],
                "properties": {
                    "id": {"type": "string"},
                    "say": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["sources", "text"],
                        "properties": {
                            "sources": {"type": "array", "items": {"type": "string"}},
                            "text": {"type": "string"},
                        },
                    },
                    "avoid": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["quote", "advice"],
                        "properties": {"quote": {"type": "string"}, "advice": {"type": "string"}},
                    },
                    "fix": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["text", "minutes"],
                        "properties": {"text": {"type": "string"}, "minutes": {"type": "integer"}},
                    },
                    "softer": {"type": "string"},
                },
            },
        }
    },
}
"""Citations before the text they support: a Say's sources before its text, an Avoid's quote before its advice."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """A slot as the interview left it: its best verdict, the words that decided it, and everything the person said on it."""

    slot: PlanSlot
    question: str
    verdict: Verdict
    quote: Quote | None
    why: str
    story: StoryParts | None
    new_fact: str | None
    answers: tuple[Turn, ...]

    @property
    def answer_text(self) -> str:
        return "\n".join(t.text for t in self.answers)


@dataclass(frozen=True, slots=True)
class Written:
    say: SayLine | None = None
    avoid: AvoidLine | None = None
    fix: FixStep | None = None
    softer: SayLine | None = None


# ───────────────────────── outcomes ─────────────────────────


def asked_first(slot: PlanSlot, st: SlotState) -> str:
    """The question a slot opened with: its push for a re-drill that started there, else its question."""
    return st.push if slot.start == "push" and st.push else st.question


def outcomes(state: SessionState) -> list[Outcome]:
    """Every slot that applied, in order. The question about the assistant does not apply when it was never used."""
    found: list[Outcome] = []
    for slot, st in zip(state.slots, state.slotStates, strict=True):
        if st.needsAssistant and not state.assistant:
            continue
        answers = tuple(t for t in state.turns if t.speaker == "you" and t.slotId == slot.id)
        best = st.best()
        if best is None:
            why = SKIPPED_WHY if st.skipped else UNANSWERED_WHY if st.reached else NOT_ASKED_WHY
            found.append(Outcome(slot, asked_first(slot, st), "not_assessed", None, why, None, None, answers))
            continue
        _, result = best
        found.append(
            Outcome(
                slot=slot,
                question=asked_first(slot, st),
                verdict=result.verdict,
                quote=result.quote,
                why=result.why,
                story=st.story_parts() if slot.kind == "story" else None,
                new_fact=result.newFact if result.verdict == "defended" else None,
                answers=answers,
            )
        )
    return found


# ───────────────────────── Say, Avoid, Fix ─────────────────────────


def fallback_say(out: Outcome) -> SayLine | None:
    """The Say line code can stand behind alone: for a Defended slot the evidence line with a number the answer did not use,
    else nothing; for the others the template of placeholders for the slot's kind."""
    if out.verdict == "defended":
        used = numbers_of(out.answer_text)
        for source in evidence_of(out.slot.sources):
            if numbers_of(source.text) - used:
                text = f"Lead with the result you already have: “{clip_quote(source.text, 120)}”."
                return SayLine(text=text, placeholders=[], sources=[source.source] if source.source else [])
        return None
    template = SAY_TEMPLATES[out.slot.kind]
    return SayLine(text=template, placeholders=placeholders_of(template) or [], sources=[])


def check_say(raw: object, out: Outcome) -> SayLine | None:
    """The model's Say line when it passes every check, else None."""
    if not isinstance(raw, Mapping):
        return None
    inner = tidy(raw.get("text")).strip('“”"').strip()
    wrapped = f"“{inner}”"
    if not inner or len(wrapped) > SAY_MAX_CHARS or len(inner.split()) < SAY_MIN_WORDS:
        return None
    placeholders = placeholders_of(inner)
    around = f"{out.answer_text}\n{sources_text(out.slot.sources)}"
    if placeholders is None or has_personality(inner, around):
        return None
    outside = outside_brackets(inner)
    if unsupported(outside, around) or unseen_openers(outside, around) or inflated(outside, around):
        return None
    allowed = [*evidence_labels(out.slot.sources), YOUR_ANSWER]
    cited = raw.get("sources")
    kept = list(
        dict.fromkeys(
            s for s in (cited if isinstance(cited, list) else []) if isinstance(s, str) and s in allowed
        )
    )
    if not kept:
        return None
    return SayLine(text=wrapped, placeholders=placeholders, sources=kept)


def check_avoid(raw: object, out: Outcome) -> AvoidLine | None:
    """The model's Avoid line when its quote is a passage of the slot's answers and its advice is clean, else None."""
    if not isinstance(raw, Mapping):
        return None
    quote = clean_quote(raw.get("quote"))
    advice = tidy(raw.get("advice"))
    around = f"{out.answer_text}\n{sources_text(out.slot.sources)}"
    if not quote or not advice or len(advice) > AVOID_ADVICE_MAX_CHARS or has_personality(advice, around):
        return None
    texts = [t.text for t in out.answers]
    if not passage(quote, [fold(t) for t in texts]):
        return None
    if unsupported(advice, out.answer_text, sources_text(out.slot.sources)):
        return None
    source = next(t for t in texts if fold(quote) in fold(t))
    return AvoidLine(quote=locate(source, quote), advice=advice)


def default_fix(topic: str) -> FixStep:
    return FixStep(
        text=f"Write down one real example for {topic}: what you did and what changed.",
        minutes=FIX_DEFAULT_MINUTES,
    )


def check_fix(raw: object, out: Outcome) -> FixStep:
    """The model's Fix step when it passes, else the plain one. Always a step: a slot that was not defended gets one."""
    if not isinstance(raw, Mapping):
        return default_fix(out.slot.topic)
    text = tidy(raw.get("text"))
    minutes = raw.get("minutes")
    ok = (
        bool(text)
        and len(text) <= FIX_MAX_CHARS
        and starts_with_verb(text)
        and not claims_person(text)
        and not has_personality(text, f"{out.answer_text}\n{sources_text(out.slot.sources)}")
        and not unsupported(
            text, out.answer_text, sources_text(out.slot.sources), out.slot.topic, out.question
        )
    )
    if not ok:
        return default_fix(out.slot.topic)
    if isinstance(minutes, bool) or not isinstance(minutes, int | float):
        return FixStep(text=text, minutes=FIX_DEFAULT_MINUTES)
    return FixStep(text=text, minutes=max(FIX_MIN_MINUTES, min(FIX_MAX_MINUTES, round(minutes))))


def say_less(out: Outcome) -> bool:
    """A re-drilled claim that could not be defended this time either: the one that may get "Or say less"."""
    last = out.slot.last
    return last is not None and last.verdict == "couldnt" and out.verdict == "couldnt"


def check_softer(raw: object, out: Outcome) -> SayLine | None:
    """The model's "Or say less" line when it passes every check (see the module docstring), else None."""
    if not say_less(out) or not isinstance(raw, str):
        return None
    inner = tidy(raw).strip('“”"').strip()
    answer = out.answer_text
    ok = (
        bool(inner)
        and len(inner) <= SOFTER_MAX_CHARS
        and len(inner.split()) >= SAY_MIN_WORDS
        and starts_with_verb(inner)
        and "[" not in inner
        and "]" not in inner
        and not claims_person(inner)
        and not has_personality(inner, answer)
        and not unsupported(inner, answer)
        and not inflated(inner, answer)
    )
    if not ok:
        return None
    return SayLine(text=f"“{inner}”", placeholders=[], sources=[YOUR_ANSWER])


def check_written(raw: Mapping[str, Any] | None, outs: Sequence[Outcome]) -> dict[str, Written]:
    """Say, Avoid and Fix for every slot that was assessed, after the checks (see the module docstring)."""
    items: dict[str, Mapping[str, Any]] = {}
    listed = raw.get("slots") if isinstance(raw, Mapping) else None
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, Mapping) and isinstance(item.get("id"), str):
            items.setdefault(item["id"], item)
    written: dict[str, Written] = {}
    for out in outs:
        if out.verdict == "not_assessed":
            continue
        item = items.get(out.slot.id, {})
        written[out.slot.id] = Written(
            say=check_say(item.get("say"), out) or fallback_say(out),
            avoid=check_avoid(item.get("avoid"), out),
            fix=check_fix(item.get("fix"), out) if out.verdict in ("shaky", "couldnt") else None,
            softer=check_softer(item.get("softer"), out),
        )
    return written


async def write_debrief(
    *,
    claude: ClaudeClient,
    model: str,
    outs: Sequence[Outcome],
    title: str,
    ctx: CallContext,
) -> dict[str, Written]:
    """Say, Avoid and Fix for the slots that were assessed. One model call; a failed call (or the spend ceiling) gives the
    code-only lines."""
    asked = [o for o in outs if o.verdict != "not_assessed"]
    raw: Mapping[str, Any] | None = None
    if asked:
        prompt = build_debrief_prompt(
            slots=[
                DebriefSlot(
                    id=o.slot.id,
                    kind=o.slot.kind,
                    topic=o.slot.topic,
                    question=o.question,
                    verdict=o.verdict,
                    sources=source_lines(o.slot.sources),
                    answers=[(t.at, t.text) for t in o.answers],
                    say_less=say_less(o),
                )
                for o in asked
            ],
            title=title,
        )
        try:
            result = await claude.structured(
                model=model,
                system=[TextBlock(DEBRIEF_SYSTEM)],
                user=[TextBlock(prompt)],
                schema=DEBRIEF_SCHEMA,
                max_tokens=DEBRIEF_MAX_TOKENS,
                effort=None,
                ctx=replace(ctx, purpose="interview.debrief"),
            )
            raw = result.data
        except ClaudeError as exc:
            log.warning("interview_debrief_failed", kind=exc.kind)
        except SpendCeilingReached:
            log.warning("interview_debrief_skipped", reason="spend ceiling")
    return check_written(raw, outs)


# ───────────────────────── the debrief ─────────────────────────


def results_of(outs: Sequence[Outcome], written: Mapping[str, Written]) -> list[SlotResult]:
    results: list[SlotResult] = []
    for out in outs:
        lines = written.get(out.slot.id, Written())
        results.append(
            SlotResult(
                slotId=out.slot.id,
                kind=out.slot.kind,
                topic=out.slot.topic,
                question=out.question,
                sources=out.slot.sources,
                verdict=out.verdict,
                quote=out.quote if out.verdict != "not_assessed" else None,
                why=out.why,
                say=lines.say,
                avoid=lines.avoid,
                fix=lines.fix,
                story=out.story,
                newFact=out.new_fact,
                previous=PreviousMark(verdict=out.slot.last.verdict, on=out.slot.last.on)
                if out.slot.last is not None
                else None,
                softer=lines.softer,
            )
        )
    return results


def counts_of(results: Sequence[SlotResult]) -> VerdictCounts:
    def count(verdict: Verdict) -> int:
        return sum(1 for r in results if r.verdict == verdict)

    return VerdictCounts(
        defended=count("defended"),
        shaky=count("shaky"),
        couldnt=count("couldnt"),
        notAssessed=count("not_assessed"),
    )


def first_thing(results: Sequence[SlotResult]) -> tuple[str | None, str | None]:
    """The first slot that was not defended (ask, gap, claim, story, task; Not assessed is not a slot to work on), as
    "{topic}. {why}" and its id."""
    pool = [r for r in results if r.verdict in ("shaky", "couldnt")]
    if not pool:
        return None, None
    first = min(pool, key=lambda r: KIND_ORDER[r.kind])
    return f"{first.topic}. {first.why}", first.slotId


def summary_of(state: SessionState, results: Sequence[SlotResult]) -> str | None:
    """A role interview's asks, counted in code. None for the other kinds."""
    if state.kind != "role" or not state.employer:
        return None
    sentences: list[str] = []
    asks = [r for r in results if r.kind == "ask"]
    if asks:
        defended = sum(1 for r in asks if r.verdict == "defended")
        shaky = sum(1 for r in asks if r.verdict == "shaky")
        text = (
            f"Of {state.employer}'s {len(asks)} {'ask' if len(asks) == 1 else 'asks'} you show, "
            f"you defended {defended}"
        )
        if shaky:
            text += f" and {shaky} {'was' if shaky == 1 else 'were'} shaky"
        sentences.append(f"{text}.")
    if any(r.kind == "gap" and r.verdict == "defended" and r.newFact for r in results):
        sentences.append("The gap question went well: add what you said to your profile.")
    return " ".join(sentences) or None


def moved_of(results: Sequence[SlotResult]) -> RedrillMoved | None:
    """A re-drill's count: of the claims that came back, how many ranked above their previous mark. None for other kinds."""
    came_back = [r for r in results if r.previous is not None]
    if not came_back:
        return None
    moved = sum(1 for r in came_back if r.previous is not None and RANK[r.verdict] > RANK[r.previous.verdict])
    return RedrillMoved(moved=moved, of=len(came_back))


def minutes_of(turns: Sequence[Turn]) -> int:
    """Whole minutes the interview took: the last turn's second, rounded up, at least 1."""
    return max(1, math.ceil(turns[-1].at / 60)) if turns else 1


def kept_until(created_at: datetime, days: int) -> str:
    return (created_at + timedelta(days=days)).date().isoformat()


def assemble(
    *,
    session_id: str,
    state: SessionState,
    created_at: datetime,
    retention_days: int,
    written: Mapping[str, Written],
    checks: list[CheckResult] | None = None,
    assistant_use: str | None = None,
) -> Debrief:
    """The debrief of a session: every slot's result, the counts and the pointers computed in code."""
    outs = outcomes(state)
    results = results_of(outs, written)
    first, first_id = first_thing(results)
    return Debrief(
        sessionId=session_id,
        kind=state.kind,
        title=state.title,
        createdAt=created_at.isoformat(),
        minutes=minutes_of(state.turns),
        counts=counts_of(results),
        firstThing=first,
        firstSlotId=first_id,
        summary=summary_of(state, results),
        results=results,
        checks=checks,
        assistantUse=assistant_use,
        transcript=state.turns,
        keptUntil=kept_until(created_at, retention_days),
        role=state.role,
        graphRole=state.graphRole,
        task=state.task,
        work=state.work,
        redrill=moved_of(results) if state.kind == "redrill" else None,
    )


__all__ = [
    "DEBRIEF_SCHEMA",
    "KIND_ORDER",
    "SAY_TEMPLATES",
    "SOFTER_MAX_CHARS",
    "Outcome",
    "Written",
    "asked_first",
    "assemble",
    "check_avoid",
    "check_fix",
    "check_say",
    "check_softer",
    "check_written",
    "counts_of",
    "default_fix",
    "fallback_say",
    "first_thing",
    "kept_until",
    "minutes_of",
    "moved_of",
    "outcomes",
    "results_of",
    "say_less",
    "summary_of",
    "write_debrief",
]
