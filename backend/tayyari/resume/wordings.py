"""A wording finalised in one resume, offered to the others. Pure code over stored variants: no model call.

A line of a draft is offered the finalised wordings that the profile's other resumes use for the same block or blocks (the
same set of cited facts, the person's own ``user-`` facts left out of the comparison, so a line the person vouched for in
their own words still counts as a wording of its block). Only a finalised version counts: a line of another resume's draft
is not offered, and neither is the block's own wording (``reset`` is the way back to it). Wordings are de-duplicated (case
and spacing aside), are never the line's own current text, come newest finalised resume first, and number at most
`MAX_WORDINGS_PER_LINE` per line. Taking one is the ordinary ``edit`` op, so Finalise checks it again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tayyari.contract.match import Bullet, ResumeVariant
from tayyari.contract.resume import MAX_WORDINGS_PER_LINE, LineWording
from tayyari.match.models import FactBook
from tayyari.match.text import fold_text
from tayyari.match.verify import exact_wording
from tayyari.resume.ops import entries_of, primary_fact

USER_FACT_PREFIX = "user-"


@dataclass(frozen=True, slots=True)
class WordingSource:
    """Another resume of the profile, as far as it is finalised: its id and title, its finalised version and its facts."""

    resume_id: str
    title: str
    final: ResumeVariant
    book: FactBook


def blocks_of(line: Bullet) -> frozenset[str]:
    """The blocks a line is written from: the facts it cites, less the ones the person typed."""
    return frozenset(i for i in line.factIds if not i.startswith(USER_FACT_PREFIX))


def offered(
    draft: ResumeVariant, book: FactBook, sources: Sequence[WordingSource], limit: int = MAX_WORDINGS_PER_LINE
) -> list[LineWording]:
    """The wordings ``sources`` (newest first) offer the lines of ``draft``, in the order of the draft's lines."""
    candidates: dict[frozenset[str], list[tuple[str, WordingSource]]] = {}
    for source in sources:
        facts = source.book.by_id()
        for entry in entries_of(source.final):
            for line in entry.bullets:
                key = blocks_of(line)
                if line.status != "verified" or not line.text.strip() or not key:
                    continue
                home = primary_fact(line, facts)
                if home is not None and fold_text(line.text) == fold_text(exact_wording(home)):
                    continue  # the block's own words: not a wording of anyone's
                candidates.setdefault(key, []).append((line.text, source))
    out: list[LineWording] = []
    for entry in entries_of(draft):
        for line in entry.bullets:
            seen = {fold_text(line.text)}
            taken = 0
            for text, source in candidates.get(blocks_of(line), []):
                folded = fold_text(text)
                if folded in seen:
                    continue
                seen.add(folded)
                out.append(
                    LineWording(
                        lineId=line.id, text=text, fromResumeId=source.resume_id, fromTitle=source.title
                    )
                )
                taken += 1
                if taken >= limit:
                    break
    return out


__all__ = ["USER_FACT_PREFIX", "WordingSource", "blocks_of", "offered"]
