"""A match's tailored resume as a resume of the list (`kind` ``posting``): the variant as the match left it, made ready.

The match composes a resume for one posting (`ResumeVariant`, its lines citing the facts of the match's book). Saving it
into `/resume` keeps those lines and their facts exactly as they stood, with these changes so it behaves like any other
resume of the list:

* the lines take the id every resume gives the line of their block (``b-<block id>``), when it is free, so the page can
  compare them with the uploaded resume and offer wordings across resumes;
* a variant the match has exported (a checklist attached and nothing waiting for the person) is already finalised;
* otherwise it is a draft: a line that still holds a ``[x]`` goes back to its block's own wording (or leaves when it has
  none), the match's open questions are dropped with the ids they named, and a summary that is not settled is left empty.
  Lines that wait for the person (``needs_user``) stay as they are, and Finalise checks them like any typed line.

Nothing here writes a word of its own.
"""

from __future__ import annotations

from tayyari.contract.match import Bullet, ResumeVariant
from tayyari.match.bullets import export_blockers, fallback_bullet
from tayyari.match.models import FactBook
from tayyari.match.verify import PLACEHOLDER
from tayyari.resume.arrange import bullet_id, empty_summary
from tayyari.resume.ops import entries_of, primary_fact


def _open_slot(line: Bullet) -> bool:
    return bool(line.placeholders) or PLACEHOLDER.search(line.text) is not None


def as_posting_resume(variant: ResumeVariant, book: FactBook) -> tuple[ResumeVariant, bool]:
    """``variant`` as a resume of the list, and whether it is already finalised (see the module docstring)."""
    finalised = variant.lint is not None and not export_blockers(variant)
    facts = book.by_id()
    work = variant.model_copy(deep=True)
    used: set[str] = set()
    for entry in entries_of(work):
        kept: list[Bullet] = []
        for line in entry.bullets:
            if line.status == "dropped":
                continue
            home = primary_fact(line, facts)
            if not finalised and _open_slot(line):
                if home is None:
                    continue
                line = fallback_bullet(line.id, home)
            wanted = bullet_id(home.id) if home is not None else line.id
            line = line.model_copy(update={"id": wanted if wanted not in used else line.id})
            while line.id in used:
                line = line.model_copy(update={"id": f"{line.id}-2"})
            used.add(line.id)
            kept.append(line)
        entry.bullets = kept
    if finalised:
        return work, True
    summary = work.summary
    settled = summary.status == "verified" and not _open_slot(summary)
    return (
        work.model_copy(
            update={"summary": summary if settled else empty_summary(), "decisions": [], "lint": None}
        ),
        False,
    )


__all__ = ["as_posting_resume"]
