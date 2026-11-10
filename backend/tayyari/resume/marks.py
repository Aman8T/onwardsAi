"""How a draft differs from the uploaded resume, and which blocks it leaves out. All counted in code.

marks      one mark per line that differs: ``added`` (a block the uploaded resume does not have: one from GitHub or
           Lovable, or a line the file listed elsewhere), ``edited`` (the same block in other words), ``moved`` (the
           same block and words, at another place among its entry's lines). A line that is both edited and moved is
           ``edited``: one mark per line
changes    the marks plus the lines of the uploaded resume that the draft leaves out
left_out   the bullet blocks the person has that the draft does not use (what "put back" offers)
cited      the facts the draft's lines, entries, degrees and skills name
"""

from __future__ import annotations

from collections.abc import Sequence

from tayyari.contract.match import Fact, ResumeVariant
from tayyari.contract.resume import BulletMark
from tayyari.match.models import FactBook
from tayyari.resume.arrange import block_ids
from tayyari.resume.ops import entries_of, primary_fact


def _kept_in_order(ranks: Sequence[int]) -> set[int]:
    """The positions of a longest run of ``ranks`` that is still in rising order: the lines that did not move."""
    if not ranks:
        return set()
    best = [1] * len(ranks)
    before = [-1] * len(ranks)
    for i in range(len(ranks)):
        for j in range(i):
            if ranks[j] < ranks[i] and best[j] + 1 > best[i]:
                best[i], before[i] = best[j] + 1, j
    at = max(range(len(ranks)), key=lambda i: (best[i], -i))
    kept: set[int] = set()
    while at != -1:
        kept.add(at)
        at = before[at]
    return kept


def marks(variant: ResumeVariant, upload: ResumeVariant) -> list[BulletMark]:
    """The lines of ``variant`` that differ from the uploaded resume ``upload`` (see the module docstring), in reading
    order."""
    wording = {line.id: line.text for entry in entries_of(upload) for line in entry.bullets}
    order = {entry.entryFactId: [line.id for line in entry.bullets] for entry in entries_of(upload)}
    out: list[BulletMark] = []
    for entry in entries_of(variant):
        rank = {line_id: i for i, line_id in enumerate(order.get(entry.entryFactId, []))}
        shared = [line.id for line in entry.bullets if line.id in rank]
        stayed = _kept_in_order([rank[line_id] for line_id in shared])
        moved = {line_id for i, line_id in enumerate(shared) if i not in stayed}
        for line in entry.bullets:
            if line.id not in wording:
                out.append(BulletMark(bulletId=line.id, kind="added"))
            elif line.text != wording[line.id]:
                out.append(BulletMark(bulletId=line.id, kind="edited"))
            elif line.id in moved:
                out.append(BulletMark(bulletId=line.id, kind="moved"))
    return out


def left_out_lines(variant: ResumeVariant, upload: ResumeVariant) -> int:
    """How many lines of the uploaded resume ``variant`` does not have."""
    have = {line.id for entry in entries_of(variant) for line in entry.bullets}
    return sum(1 for entry in entries_of(upload) for line in entry.bullets if line.id not in have)


def changes(variant: ResumeVariant, upload: ResumeVariant) -> int:
    """Lines of the draft that differ from the uploaded resume: moved, edited, added or left out."""
    return len(marks(variant, upload)) + left_out_lines(variant, upload)


def used_blocks(variant: ResumeVariant, book: FactBook) -> set[str]:
    """The ids of the blocks the lines of ``variant`` are written from."""
    facts = book.by_id()
    used: set[str] = set()
    for entry in entries_of(variant):
        for line in entry.bullets:
            source = primary_fact(line, facts)
            if source is not None:
                used.add(source.id)
    return used


def left_out(variant: ResumeVariant, book: FactBook) -> list[Fact]:
    """The bullet blocks of ``book`` that ``variant`` does not use, in the book's order."""
    facts = book.by_id()
    used = used_blocks(variant, book)
    out: list[Fact] = []
    for entry in book.entries:
        if entry.kind not in ("experience", "project"):
            continue
        out.extend(facts[f] for f in block_ids(entry) if f in facts and f not in used)
    return out


def cited_ids(variant: ResumeVariant) -> set[str]:
    """Every fact id ``variant`` names: its lines, its entries and degrees, its skills."""
    ids = {fid for entry in entries_of(variant) for line in entry.bullets for fid in line.factIds}
    ids |= set(variant.summary.factIds)
    ids |= {entry.entryFactId for entry in entries_of(variant)}
    ids |= {entry.factId for entry in variant.education}
    ids |= {fid for group in variant.skills for item in group.items for fid in item.factIds}
    return ids


def cited_facts(variant: ResumeVariant, book: FactBook) -> list[Fact]:
    """The facts ``variant`` names, in the book's order, so the page can show each line's source."""
    ids = cited_ids(variant)
    return [f for f in book.facts if f.id in ids]


__all__ = ["changes", "cited_facts", "cited_ids", "left_out", "left_out_lines", "marks", "used_blocks"]
