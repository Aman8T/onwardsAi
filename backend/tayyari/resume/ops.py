"""The edits of a draft, applied by code: all of a request's operations or none.

    move        swap a line with its neighbour inside its job or project
    leave_out   take a line off the resume (its block stays the person's and can be put back)
    put_back    append a block to its job or project, in the fact's own words; a job the resume had left out returns
                with it
    edit        set the line's text to what the person typed (one line, at most `MAX_LINE_CHARS`) and mark it
                ``needs_user``: Finalise is what checks it, and a draft with such a line cannot be exported
    reset       restore the fact's own wording

Titles, employers and dates are not lines: no operation reaches them. An operation that does not fit (an unknown line,
a move past the edge, a block that is already there, text that is empty, too long or more than one line) raises
`OpRefused` with its place in the request, and the draft is left exactly as it was.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from tayyari.contract.match import Bullet, ExperienceEntry, Fact, ResumeVariant
from tayyari.contract.resume import MAX_LINE_CHARS, ResumeOp
from tayyari.match.bullets import fallback_bullet
from tayyari.match.models import BookEntry, FactBook
from tayyari.match.text import fold_text, tidy
from tayyari.match.verify import PLACEHOLDER, exact_wording
from tayyari.resume.arrange import bullet_id, entry_heading, entry_of_block
from tayyari.resume.errors import OpRefused

NOT_A_LINE = "that line is not on this resume"


class _Refusal(Exception):
    """Why one operation does not fit (the sentence the person reads, without its full stop)."""


def primary_fact(bullet: Bullet, facts: Mapping[str, Fact]) -> Fact | None:
    """The block a line is written from: its first cited fact that is not something the person typed."""
    return next((facts[i] for i in bullet.factIds if i in facts and not i.startswith("user-")), None)


def entries_of(variant: ResumeVariant) -> list[ExperienceEntry]:
    """The jobs and projects of a resume, in reading order (the entries that hold lines)."""
    return [*variant.experience, *variant.projects]


def _locate(variant: ResumeVariant, bullet: str | None) -> tuple[ExperienceEntry, int]:
    if bullet:
        for entry in entries_of(variant):
            for index, line in enumerate(entry.bullets):
                if line.id == bullet:
                    return entry, index
    raise _Refusal(NOT_A_LINE)


def _move(variant: ResumeVariant, op: ResumeOp) -> None:
    entry, index = _locate(variant, op.bulletId)
    if op.dir is None:
        raise _Refusal("a move needs a direction, up or down")
    other = index - 1 if op.dir == "up" else index + 1
    if other < 0:
        raise _Refusal("that line is already the first of its entry")
    if other >= len(entry.bullets):
        raise _Refusal("that line is already the last of its entry")
    entry.bullets[index], entry.bullets[other] = entry.bullets[other], entry.bullets[index]


def _leave_out(variant: ResumeVariant, op: ResumeOp) -> None:
    entry, index = _locate(variant, op.bulletId)
    del entry.bullets[index]


def _insert_entry(variant: ResumeVariant, book: FactBook, entry: BookEntry) -> ExperienceEntry:
    """Bring a job or project the resume had left out back as an empty entry: a job before the first job that is less
    recent (resumes list jobs newest first), a project after the others."""
    added = entry_heading(entry, [])
    if entry.kind == "project":
        variant.projects.append(added)
        return added
    recency = {e.factId: e.recency for e in book.entries}
    at = next(
        (
            i
            for i, existing in enumerate(variant.experience)
            if recency.get(existing.entryFactId, 0) < entry.recency
        ),
        len(variant.experience),
    )
    variant.experience.insert(at, added)
    return added


def _put_back(variant: ResumeVariant, book: FactBook, facts: Mapping[str, Fact], op: ResumeOp) -> None:
    fact = facts.get(op.factId or "")
    if fact is None:
        raise _Refusal("that block is not one of your blocks")
    home = entry_of_block(book, fact.id)
    if home is None:
        raise _Refusal("that block is not a line of a job or project")
    line_id = bullet_id(fact.id)
    for entry in entries_of(variant):
        for line in entry.bullets:
            written_from = primary_fact(line, facts)
            if line.id == line_id or (written_from is not None and written_from.id == fact.id):
                raise _Refusal("that block is already on this resume")
    target = next((e for e in entries_of(variant) if e.entryFactId == home.factId), None)
    if target is None:
        target = _insert_entry(variant, book, home)
    target.bullets.append(fallback_bullet(line_id, fact))


def _edit(variant: ResumeVariant, facts: Mapping[str, Fact], op: ResumeOp) -> None:
    entry, index = _locate(variant, op.bulletId)
    raw = (op.text or "").strip()
    if not raw:
        raise _Refusal("a line needs some text (leave the line out to remove it)")
    if "\n" in raw or "\r" in raw:
        raise _Refusal("a line is one line of text")
    text = tidy(raw)
    if len(text) > MAX_LINE_CHARS:
        raise _Refusal(f"a line is at most {MAX_LINE_CHARS} characters")
    if PLACEHOLDER.search(text):
        raise _Refusal("a line cannot hold a [x]: type the figure itself, or leave it out")
    line = entry.bullets[index]
    source = primary_fact(line, facts)
    if source is not None and fold_text(text) == fold_text(exact_wording(source)):
        entry.bullets[index] = fallback_bullet(line.id, source)  # typed back to the fact's own words
        return
    entry.bullets[index] = line.model_copy(
        update={"text": text, "verdict": "reframed", "placeholders": [], "status": "needs_user"}
    )


def _reset(variant: ResumeVariant, facts: Mapping[str, Fact], op: ResumeOp) -> None:
    entry, index = _locate(variant, op.bulletId)
    line = entry.bullets[index]
    source = primary_fact(line, facts)
    if source is None:
        raise _Refusal("that line has no block to go back to")
    entry.bullets[index] = fallback_bullet(line.id, source)


def apply_ops(variant: ResumeVariant, book: FactBook, ops: Sequence[ResumeOp]) -> ResumeVariant:
    """``variant`` with ``ops`` applied in order (see the module docstring), without its lint: a checklist from before
    the change would describe text that is no longer there. Raises `OpRefused` for the first operation that does not
    fit; ``variant`` itself is never changed."""
    work = variant.model_copy(deep=True)
    facts = book.by_id()
    for index, op in enumerate(ops):
        try:
            if op.op == "move":
                _move(work, op)
            elif op.op == "leave_out":
                _leave_out(work, op)
            elif op.op == "put_back":
                _put_back(work, book, facts, op)
            elif op.op == "edit":
                _edit(work, facts, op)
            else:
                _reset(work, facts, op)
        except _Refusal as refusal:
            raise OpRefused(index, str(refusal)) from None
    return work.model_copy(update={"lint": None})


__all__ = ["NOT_A_LINE", "apply_ops", "entries_of", "primary_fact"]
