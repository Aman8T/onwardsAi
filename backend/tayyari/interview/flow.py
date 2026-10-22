"""The interviewer's loop: what the interview says next, decided by fixed rules over the session state. No model, no I/O.

A slot has three rungs and at most one of the last two is asked:

    surface   the slot's question.             Defended: next slot. Shaky or not assessed: push. Couldn't defend: floor.
    push      a follow-up for specifics.       Next slot.
    floor     an easier question.              Next slot.

A story slot asks for the part of the story that is missing instead: situation, then action, then result, then (when those
three are there) what the person would do differently. A work sample's questions ask once.

A re-drill slot (I2) may open at its push instead (``PlanSlot.start``: the claim was Shaky last time); asked first, the push
is the slot's question, and an answer that could not be defended still gets the floor. "I don't know" and "skip" go
straight to the next slot; a dispute or an off-topic answer asks the same line again, once a slot. Every line the
interviewer says is also a turn of the transcript, at whole seconds since the interview began.
"""

from __future__ import annotations

from typing import Final

from tayyari.contract.interview import (
    AnswerMode,
    InterviewLine,
    LineKind,
    PlanSlot,
    Quote,
    Rung,
    Turn,
)

from .judge import WHY_TEMPLATE, Judged
from .phrase import STORY_PARTS
from .sources import evidence_of
from .state import RungResult, SessionState, SlotState
from .text import clip_quote

DONT_KNOW_LEAD: Final = "That's fine. I'll note it and we'll move on. You'll get a step to close it."
UNCLEAR_LEAD: Final = "I didn't catch that."
DRIFT_LEAD: Final = "Let's come back to the question."
DISPUTE_TEXT: Final = "Tell me about the part that was yours."
DONE_TEXT: Final = "That's the last question. Thank you. Your debrief is ready."
DONT_KNOW_QUOTE: Final = "I don't know."

_LINE_KIND: Final[dict[Rung, LineKind]] = {"surface": "question", "push": "followup", "floor": "floor"}


# ───────────────────────── what the interviewer says ─────────────────────────


def present(state: SessionState, line: InterviewLine, at: int) -> None:
    """Say ``line``: it becomes the current line and a turn of the transcript."""
    state.current = line
    text = f"{line.lead} {line.text}" if line.lead else line.text
    state.turns.append(Turn(speaker="interviewer", text=text, at=at, slotId=line.slotId, rung=line.rung))


def record_answer(state: SessionState, text: str, mode: AnswerMode, at: int) -> None:
    """The person's turn: what the browser heard or what they typed, on the line being asked."""
    line = state.current
    state.turns.append(
        Turn(
            speaker="you",
            text=text,
            at=at,
            slotId=line.slotId if line is not None else None,
            rung=line.rung if line is not None else None,
            mode=mode,
        )
    )
    state.answered += 1


def _ask(state: SessionState, index: int, at: int, lead: str | None) -> None:
    """Ask the question of the first slot from ``index`` on that applies; the done line when none is left. The question about
    the assistant is passed over when the assistant was never used."""
    count = len(state.slots)
    while index < count and state.slotStates[index].needsAssistant and not state.assistant:
        index += 1
    state.slotIndex = index
    if index >= count:
        present(state, InterviewLine(kind="done", text=DONE_TEXT, lead=lead), at)
        return
    st = state.slotStates[index]
    slot = state.slots[index]
    st.reached = True
    rung: Rung = "push" if slot.start == "push" and st.push else "surface"
    present(
        state,
        InterviewLine(
            kind="question",
            text=st.push if rung == "push" else st.question,
            lead=lead,
            slotId=slot.id,
            rung=rung,
        ),
        at,
    )


def begin(state: SessionState, at: int) -> None:
    """Ask the first question (at the start, and for a work sample once the work is in)."""
    _ask(state, 0, at, None)


def next_slot(state: SessionState, at: int, lead: str | None = None) -> None:
    """Done with this slot: on to the next."""
    _ask(state, state.slotIndex + 1, at, lead)


def finish(state: SessionState, at: int) -> None:
    """End the questions here: the rest are not assessed."""
    state.slotIndex = len(state.slots)
    present(state, InterviewLine(kind="done", text=DONE_TEXT), at)


def spend(state: SessionState, max_turns: int, at: int) -> None:
    """Past ``max_turns`` answers (buttons included) the interview goes straight to its end."""
    if state.answered >= max_turns and not state.over:
        finish(state, at)


def say_again(state: SessionState, lead: str) -> None:
    """The same line again, with ``lead`` before it."""
    if state.current is not None:
        state.current = state.current.model_copy(update={"lead": lead})


def repeat(state: SessionState) -> None:
    """The same line, with nothing before it. Nothing is recorded."""
    if state.current is not None and state.current.lead is not None:
        state.current = state.current.model_copy(update={"lead": None})


def _repeat_turn(state: SessionState, at: int) -> None:
    """Record the interviewer saying the current line again with its lead."""
    line = state.current
    if line is not None:
        present(state, line, at)


def _rung(state: SessionState) -> Rung:
    line = state.current
    return line.rung if line is not None and line.rung is not None else "surface"


# ───────────────────────── the rules ─────────────────────────


def follow_up(slot: PlanSlot, st: SlotState, rung: Rung, verdict: str) -> tuple[Rung, str] | None:
    """The next question on the same slot (its rung and text), or None to go on to the next slot."""
    if slot.kind == "task":
        return None
    if rung != "surface":
        # a re-drill that opened at the push keeps its easier question for an answer that could not be defended
        if rung == "push" and slot.start == "push" and verdict == "couldnt" and st.floor:
            return "floor", st.floor
        return None
    if slot.kind == "story":
        if verdict == "couldnt":
            return "floor", st.floor
        parts = st.story_parts()
        for name in ("situation", "action", "result"):
            if not getattr(parts, name):
                return "push", STORY_PARTS[name]
        if not parts.reflection:
            return "push", STORY_PARTS["reflection"]
        return None
    if verdict == "defended":
        return None
    if verdict == "couldnt":
        return "floor", st.floor
    return "push", st.push


def can_dispute(slot: PlanSlot, st: SlotState) -> bool:
    """A dispute is checked against a line of the person's evidence, once a slot."""
    return not st.disputed and bool(evidence_of(slot.sources))


# ───────────────────────── outcomes of a turn ─────────────────────────


def on_unclear(state: SessionState, text: str, mode: AnswerMode, at: int) -> None:
    """Too few words to be an answer: the line comes back, nothing is judged."""
    if text.strip():
        record_answer(state, text.strip(), mode, at)
    say_again(state, UNCLEAR_LEAD)
    _repeat_turn(state, at)


def on_dont_know(state: SessionState, text: str, mode: AnswerMode, at: int) -> None:
    """The person gives up on this one: Couldn't defend with their own words, on to the next slot."""
    current = state.slot()
    said = text.strip() or DONT_KNOW_QUOTE
    record_answer(state, said, mode, at)
    if current is not None:
        _, st = current
        st.rungs[_rung(state)] = RungResult(
            verdict="couldnt", quote=Quote(text=said, at=at), why=WHY_TEMPLATE["couldnt"]
        )
    next_slot(state, at, DONT_KNOW_LEAD)


def on_skip(state: SessionState, at: int) -> None:
    """The person passes on this one: the slot keeps what it has (Not assessed if nothing), on to the next."""
    current = state.slot()
    state.answered += 1
    if current is not None:
        current[1].skipped = True
    next_slot(state, at)


def on_dispute(state: SessionState, text: str, mode: AnswerMode, at: int) -> None:
    """ "That's not what my resume says": the line being asked about is read back and the person tells what was theirs. No
    verdict is given for the dispute itself."""
    current = state.slot()
    line = state.current
    record_answer(state, text.strip(), mode, at)
    if current is None or line is None:
        return
    slot, st = current
    st.disputed = True
    shown = evidence_of(slot.sources)[0].text
    present(
        state,
        InterviewLine(
            kind=line.kind,
            text=DISPUTE_TEXT,
            lead=f"Here's the line I'm asking about: “{clip_quote(shown)}”.",
            slotId=line.slotId,
            rung=line.rung,
        ),
        at,
    )


def on_judged(state: SessionState, judged: Judged, text: str, mode: AnswerMode, at: int) -> None:
    """An answer that was judged: an off-topic one is asked again (once a slot); otherwise its rung is recorded and the
    rules pick the next question."""
    current = state.slot()
    record_answer(state, text.strip(), mode, at)
    if current is None:
        return
    slot, st = current
    if not judged.on_topic and not st.drifted:
        st.drifted = True
        say_again(state, DRIFT_LEAD)
        _repeat_turn(state, at)
        return
    rung = _rung(state)
    st.rungs[rung] = RungResult(
        verdict=judged.verdict,
        quote=Quote(text=judged.quote, at=at) if judged.quote is not None else None,
        why=judged.why,
        story=judged.story,
        newFact=judged.new_fact,
    )
    step = follow_up(slot, st, rung, judged.verdict)
    if step is None:
        next_slot(state, at)
        return
    to_rung, question = step
    present(
        state,
        InterviewLine(
            kind=_LINE_KIND[to_rung],
            text=question,
            slotId=slot.id,
            rung=to_rung,
        ),
        at,
    )


__all__ = [
    "DISPUTE_TEXT",
    "DONE_TEXT",
    "DONT_KNOW_LEAD",
    "DRIFT_LEAD",
    "UNCLEAR_LEAD",
    "begin",
    "can_dispute",
    "finish",
    "follow_up",
    "next_slot",
    "on_dispute",
    "on_dont_know",
    "on_judged",
    "on_skip",
    "on_unclear",
    "present",
    "record_answer",
    "repeat",
    "say_again",
    "spend",
]
