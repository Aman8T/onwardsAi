"""Phrasing: one structured call at the start of an interview turns every slot into a question, a push, an easier floor and
the hidden notes of what a full answer covers. Code checks every line and a failing one is replaced by a template.

    line        at most 200 characters, ends with "?", four words or more, and every number and capitalised name in it is in
                the slot's sources, the posting, the job title or the employer (`portfolio.ground`). One that is not gets the
                slot kind's template, built from the source's own words. A role on the person's graph has a title and no
                employer or posting: its requirements are among the slots' sources.
    covers      at most four notes of at most 120 characters, with the same name and number check; the rest are dropped.
    posting     text aimed at an AI in the posting (`jobsearch.judge.has_injection`): no call at all, templates only.
    failure     a model that fails, or leaves a slot out, gives the templates for it. An interview never waits on a model.

    re-drill    a slot that comes back (I2, ``PlanSlot.last``) is phrased with its last question, verdict and quote. One that
                could not be defended opens at a surface in new words: a question that folds to the last one is refused,
                and its template (`redrill_surface`) is never the last question either.

A work sample's questions come from its template: nothing here runs for it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

from tayyari.contract.interview import PlanSlot
from tayyari.core.logging import get_logger
from tayyari.core.text import tidy
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.errors import ClaudeError
from tayyari.llm.types import CallContext, TextBlock
from tayyari.portfolio.ground import Corpus, unsupported_anchors
from tayyari.prompts.interview import PHRASE_SYSTEM, PhraseLast, PhraseSlot, build_phrase_prompt

from .sources import quote_source, source_lines, sources_text
from .text import clip_quote, fold, has_injection

log = get_logger(__name__)

PHRASE_MAX_TOKENS: Final = 3500
LINE_MAX_CHARS: Final = 200
LINE_MIN_WORDS: Final = 4
COVER_MAX_CHARS: Final = 120
COVERS_MAX: Final = 4

STORY_PARTS: Final[dict[str, str]] = {
    "situation": "What was going on at the time, and what was at stake?",
    "action": "What did you do yourself?",
    "result": "What happened in the end?",
    "reflection": "Looking back, what would you do differently?",
}
"""A story's pushes are chosen by the part the answer is missing, never phrased by the model."""

REDRILL_SURFACES: Final = (
    "Let's take this one again: “{said}”. What was your part, and how did it end?",
    "Once more, in other words: on “{said}”, what did you do yourself?",
)
"""A re-drill's surface for a claim that could not be defended: asked about the same line, never the last question again."""

PHRASE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["slots"],
    "properties": {
        "slots": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "covers", "question", "push", "floor"],
                "properties": {
                    "id": {"type": "string"},
                    "covers": {"type": "array", "items": {"type": "string"}},
                    "question": {"type": "string"},
                    "push": {"type": "string"},
                    "floor": {"type": "string"},
                },
            },
        }
    },
}
"""What a full answer covers comes before the questions that rest on it."""


@dataclass(frozen=True, slots=True)
class Phrased:
    """A slot's lines: the question, the push and the floor, and the hidden notes (empty for a template)."""

    question: str
    push: str
    floor: str
    covers: tuple[str, ...] = ()


def template_lines(kind: str, text: str, *, posting: bool = True) -> Phrased:
    """The plain lines of a slot kind, built from the words of its source (clipped to 90 characters). ``posting=False`` is
    a graph role's gap: the role needs it, and no posting asked for it."""
    said = clip_quote(text)
    if kind == "ask":
        return Phrased(
            f"The role asks for “{said}”. Tell me about a time you did that.",
            "What exactly was your part, and how did you know it worked?",
            "What is one thing you did there, in a sentence?",
        )
    if kind == "gap":
        return Phrased(
            f"The role {'asks for' if posting else 'needs'} “{said}”. How would you handle that in your first month?",
            "What's the closest thing you've done?",
            "Where would you start?",
        )
    if kind == "story":
        return Phrased(
            f"Tell me about this: “{said}”. What happened?",
            STORY_PARTS["situation"],
            "Who else was involved?",
        )
    return Phrased(
        f"Your resume says “{said}”. Walk me through it.",
        "What did you do yourself, and how was it measured?",
        "What was your part, in one sentence?",
    )


def slot_template(slot: PlanSlot) -> Phrased:
    source = quote_source(slot.sources)
    if source is None:
        return template_lines(slot.kind, slot.topic)
    return template_lines(slot.kind, source.text, posting=source.origin != "role")


def new_words(slot: PlanSlot) -> bool:
    """A re-drill slot that opens at its surface: it must be asked in new words."""
    return slot.last is not None and slot.start == "surface"


def redrill_surface(slot: PlanSlot) -> str:
    """The plain surface of a re-drill in new words: the first of `REDRILL_SURFACES` that is not the last question."""
    source = quote_source(slot.sources)
    said = clip_quote(source.text if source is not None else slot.topic)
    last = fold(slot.last.question) if slot.last is not None else ""
    options = [template.format(said=said) for template in REDRILL_SURFACES]
    return next((q for q in options if fold(q) != last), options[-1])


def _line_ok(line: str, corpus: Corpus) -> bool:
    return (
        0 < len(line) <= LINE_MAX_CHARS
        and line.endswith("?")
        and len(line.split()) >= LINE_MIN_WORDS
        and not unsupported_anchors(line, corpus)
    )


def check_phrasing(
    raw: Mapping[str, Any] | None,
    slots: Sequence[PlanSlot],
    *,
    around: str = "",
) -> tuple[dict[str, Phrased], dict[str, int]]:
    """Each slot's lines after the checks, and what was replaced or dropped (by reason, for the log). ``around`` is the
    text the lines may also use: the posting, the job title and the employer."""
    items: dict[str, Mapping[str, Any]] = {}
    listed = raw.get("slots") if isinstance(raw, Mapping) else None
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, Mapping) and isinstance(item.get("id"), str):
            items.setdefault(item["id"], item)
    replaced: dict[str, int] = {}

    def count(reason: str) -> None:
        replaced[reason] = replaced.get(reason, 0) + 1

    out: dict[str, Phrased] = {}
    for slot in slots:
        base = slot_template(slot)
        if new_words(slot):
            base = replace(base, question=redrill_surface(slot))
        item = items.get(slot.id)
        if item is None:
            count("slot_missing")
            out[slot.id] = base
            continue
        corpus = Corpus.of("\n".join([sources_text(slot.sources), around]))
        lines: dict[str, str] = {}
        for name in ("question", "push", "floor"):
            line = tidy(item.get(name))
            repeated = (
                name == "question"
                and new_words(slot)
                and slot.last is not None
                and fold(line) == fold(slot.last.question)
            )
            if repeated:
                count("question_repeated")
            elif _line_ok(line, corpus):
                lines[name] = line
            else:
                count(f"{name}_replaced")
        covers: list[str] = []
        raw_covers = item.get("covers")
        for note in raw_covers if isinstance(raw_covers, list) else []:
            text = tidy(note)
            if not text or len(text) > COVER_MAX_CHARS or unsupported_anchors(text, corpus):
                count("cover_dropped")
            elif len(covers) < COVERS_MAX:
                covers.append(text)
        out[slot.id] = replace(
            base,
            question=lines.get("question", base.question),
            push=lines.get("push", base.push),
            floor=lines.get("floor", base.floor),
            covers=tuple(covers),
        )
    return out, replaced


async def phrase_slots(
    *,
    claude: ClaudeClient,
    model: str,
    slots: Sequence[PlanSlot],
    title: str | None,
    employer: str | None,
    posting_text: str | None,
    posting_chars: int,
    ctx: CallContext,
) -> dict[str, Phrased]:
    """The lines of every slot (see the module docstring). One model call; templates when it fails or must not run."""
    if posting_text and has_injection(posting_text):
        log.warning("interview_phrase_anomaly", anomaly="text aimed at an AI in the posting")
        return {slot.id: slot_template(slot) for slot in slots}
    prompt = build_phrase_prompt(
        slots=[
            PhraseSlot(
                s.id,
                s.kind,
                s.topic,
                source_lines(s.sources),
                PhraseLast(s.last.question, s.last.verdict, s.last.quote or "", s.start or "surface")
                if s.last is not None
                else None,
            )
            for s in slots
        ],
        title=title,
        employer=employer,
        posting_text=posting_text,
        posting_chars=posting_chars,
    )
    raw: Mapping[str, Any] | None = None
    try:
        result = await claude.structured(
            model=model,
            system=[TextBlock(PHRASE_SYSTEM)],
            user=[TextBlock(prompt)],
            schema=PHRASE_SCHEMA,
            max_tokens=PHRASE_MAX_TOKENS,
            effort=None,
            ctx=replace(ctx, purpose="interview.phrase"),
        )
        raw = result.data
    except ClaudeError as exc:
        log.warning("interview_phrase_failed", kind=exc.kind)
    around = "\n".join(part for part in (title, employer, (posting_text or "")[:posting_chars]) if part)
    lines, replaced = check_phrasing(raw, slots, around=around)
    log.info("interview_phrase", answered=raw is not None, slots=len(slots), replaced=replaced)
    return lines


__all__ = [
    "PHRASE_SCHEMA",
    "REDRILL_SURFACES",
    "STORY_PARTS",
    "Phrased",
    "check_phrasing",
    "new_words",
    "phrase_slots",
    "redrill_surface",
    "slot_template",
    "template_lines",
]
