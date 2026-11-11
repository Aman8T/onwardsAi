"""A resume as the coach beside `/resume` is shown it, and the check a rewrite it proposes must pass.

`CoachView` is what `chat.ports.ResumeInView` asks for (the chat service sees it only through that Protocol): the lines of
a draft with the job or project they sit under, their words and the quotes of the facts they rest on, and `check`, which
says whether other words may stand as a line's: they must pass `finalise.check_line` (the match's sentence verifier) against
the facts the line cites, so a figure, a scope verb, a term of the role or a name those facts lack drops the rewrite. The
check is the very one Finalise runs on a typed line, so a rewrite the coach may propose is one Finalise would not stop.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tayyari.contract.match import Bullet, ExperienceEntry, Fact, ResumeVariant
from tayyari.match.models import FactBook
from tayyari.resume.finalise import check_line
from tayyari.resume.ops import entries_of


@dataclass(frozen=True, slots=True)
class CoachLine:
    id: str
    heading: str
    text: str
    quotes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoachView:
    """One resume in view: its lines and what checks new words for one of them. ``cited`` maps a line id to the facts the
    line cites; ``terms`` are the role's requirement words (empty for a resume that is not for a role)."""

    resume_id: str
    title: str
    lines: tuple[CoachLine, ...]
    cited: Mapping[str, tuple[Fact, ...]]
    terms: tuple[str, ...]
    bullets: Mapping[str, Bullet]
    """The lines as the draft has them, by id (what a rewrite is checked as: the same line with other words)."""

    def check(self, line_id: str, text: str) -> str | None:
        found = self.bullets.get(line_id)
        facts = self.cited.get(line_id)
        if found is None or facts is None:
            return "unknown_line"
        problems = check_line(found.model_copy(update={"text": text}), facts, self.terms)
        return f"unsupported_{problems[0].kind}" if problems else None


def _heading(entry: ExperienceEntry) -> str:
    return ", ".join(part for part in (entry.title, entry.org, entry.dates) if part)


def _quotes(cited: Sequence[Fact]) -> tuple[str, ...]:
    """The passages of the sources the cited facts quote, without repeats; a fact that quotes nothing gives its own words."""
    quotes = [ref.quote for fact in cited for ref in fact.refs if ref.quote.strip()]
    return tuple(dict.fromkeys(quotes or [fact.text for fact in cited if fact.text.strip()]))


def coach_view(
    resume_id: str, title: str, draft: ResumeVariant, book: FactBook, terms: Sequence[str] = ()
) -> CoachView:
    """``draft`` as the coach sees it (see the module docstring)."""
    facts = book.by_id()
    lines: list[CoachLine] = []
    cited: dict[str, tuple[Fact, ...]] = {}
    bullets: dict[str, Bullet] = {}
    for entry in entries_of(draft):
        heading = _heading(entry)
        for line in entry.bullets:
            if line.status == "dropped" or not line.text.strip():
                continue
            ground = tuple(facts[i] for i in line.factIds if i in facts)
            cited[line.id] = ground
            bullets[line.id] = line
            lines.append(CoachLine(line.id, heading, line.text, _quotes(ground)))
    return CoachView(resume_id, title, tuple(lines), cited, tuple(terms), bullets)


__all__ = ["CoachLine", "CoachView", "coach_view"]
