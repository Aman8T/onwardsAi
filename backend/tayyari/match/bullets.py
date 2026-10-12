"""One resume sentence from draft to settled: the checks that decide whether a model's wording may stand, the
questions a sentence raises, and the person's answers applied.

`assess_draft` runs the verifier (`verify.check_text`) on a drafted sentence and decides, in code:

  - rejected      names, tools or puffery that no cited fact has, a posting term nothing supports, an invented spate of
                  numbers, a first-person line: the caller falls back to the fact's own wording (`fallback_bullet`);
  - number        a number no cited fact has is replaced by a ``[x]`` placeholder, the bullet is ``needs_user`` and
                  a ``number`` decision asks for the real figure (or to soften or drop the line);
  - stretch       a scope verb stronger than the facts show (led/owned/managed/built over contributed/supported)
                  marks the bullet ``stretch`` and ``needs_user``: keep, soften or drop;
  - confirm_term  a posting term that no cited fact uses, but that a coverage row ties to those facts
                  (synonym_only, partial ...), keeps the reworded line ``reframed`` and ``needs_user``: use the
                  posting's word or keep the person's own;
  - otherwise     ``verified``.

`apply_decisions` takes the person's answers (`DecisionChoice`) and settles the bullets they name, re-verifying what
changed. A figure the person gives becomes a ``user`` fact that the bullet then cites; nothing else is invented.
Export stays blocked (`export_blockers`) while any bullet is ``needs_user`` or still holds a ``[x]``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from tayyari.contract.match import (
    Bullet,
    BulletStatus,
    BulletVerdict,
    Decision,
    DecisionChoice,
    DecisionChoiceKey,
    DecisionKind,
    ExperienceEntry,
    Fact,
    ResumeVariant,
)
from tayyari.match.facts import user_fact
from tayyari.match.models import FactBook
from tayyari.match.text import fold_text, tidy
from tayyari.match.verify import (
    PLACEHOLDER,
    check_text,
    exact_wording,
    placeholders_in,
    with_placeholders,
)

MAX_NUMBER_SLOTS: Final = 2
"""A draft that invents more numbers than this is not repaired with placeholders: it is thrown away."""
BULLET_MAX_WORDS: Final = 45
SUMMARY_MAX_WORDS: Final = 70

_LEAD = re.compile(r"^[\s\-*•·▪]+")
_FIGURE = re.compile(r"^[\$₹€£]?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|[kKmMbB]|x|X|\+|lakh|crore)?$")

OPTIONS: Final[dict[str, list[DecisionChoiceKey]]] = {
    "number": ["fill", "soften", "drop"],
    "stretch": ["keep", "soften", "drop"],
    "confirm_term": ["use_posting_term", "keep_my_wording", "drop"],
}


@dataclass(frozen=True, slots=True)
class DraftContext:
    """What a sentence is judged against besides its own facts."""

    posting_terms: tuple[str, ...] = ()
    term_support: Mapping[str, frozenset[str]] = field(default_factory=dict)
    """Posting term -> the fact ids a coverage row ties to a requirement that names it."""
    max_words: int = BULLET_MAX_WORDS


@dataclass(frozen=True, slots=True)
class Assessment:
    """The outcome of `assess_draft`: a settled bullet (and its decision), or a rejection."""

    bullet: Bullet | None
    decision: Decision | None = None
    rejected: str | None = None


def fallback_bullet(bullet_id: str, primary: Fact) -> Bullet:
    """The fact's own wording as a verified bullet: always supported, because it is the fact."""
    return Bullet(
        id=bullet_id,
        text=exact_wording(primary),
        factIds=[primary.id],
        verdict="exact",
        placeholders=[],
        status="verified",
    )


def _decision(bullet_id: str, kind: DecisionKind, prompt: str) -> Decision:
    return Decision(bulletId=bullet_id, kind=kind, prompt=prompt, options=list(OPTIONS[kind]))


def assess_draft(
    bullet_id: str,
    text: str,
    cited: Sequence[Fact],
    ctx: DraftContext,
    *,
    primary: Fact,
    model_verdict: str = "exact",
) -> Assessment:
    """Verify a drafted sentence against ``cited`` and settle it (see the module docstring)."""
    body = tidy(_LEAD.sub("", text))
    check = check_text(body, cited, posting_terms=ctx.posting_terms, max_words=ctx.max_words)
    if check.names or check.problems:
        why = (check.names + check.problems)[0]
        return Assessment(None, rejected=f"unsupported: {why}")
    cited_ids = {f.id for f in cited}
    confirm: list[str] = []
    for term in check.terms:
        if ctx.term_support.get(term, frozenset()) & cited_ids:
            confirm.append(term)
        else:
            return Assessment(
                None, rejected=f"uses the posting's word {term!r}, which no cited fact supports"
            )
    if len(check.numbers) > MAX_NUMBER_SLOTS:
        return Assessment(None, rejected="invents several numbers")
    final = with_placeholders(body, check.numbers) if check.numbers else body
    placeholders = placeholders_in(final)
    stretch = bool(check.escalated) or model_verdict == "stretch"
    is_exact = fold_text(final) == fold_text(primary.text) or fold_text(final) == fold_text(
        exact_wording(primary)
    )
    verdict: BulletVerdict = "stretch" if stretch else "reframed" if (confirm or not is_exact) else "exact"

    decision: Decision | None = None
    status: BulletStatus = "verified"
    if placeholders:
        status = "needs_user"
        decision = _decision(
            bullet_id,
            "number",
            f'What is the figure for: "{final}"? Give it only if you can stand behind it, or soften the line.',
        )
    elif stretch:
        status = "needs_user"
        verbs = ", ".join(check.escalated) or "a stronger role"
        decision = _decision(
            bullet_id,
            "stretch",
            f'"{final}" claims more than your own words show ({verbs}). Keep it, soften it to what you wrote, or drop it?',
        )
    elif confirm:
        status = "needs_user"
        decision = _decision(
            bullet_id,
            "confirm_term",
            f'The posting says "{confirm[0]}"; your documents say it another way. Use the posting\'s word in this line?',
        )
    bullet = Bullet(
        id=bullet_id,
        text=final,
        factIds=list(dict.fromkeys(f.id for f in cited)),
        verdict=verdict,
        placeholders=placeholders,
        status=status,
    )
    return Assessment(bullet, decision)


# ───────────────────────── export blockers ─────────────────────────


def all_bullets(variant: ResumeVariant) -> list[Bullet]:
    """The summary and every bullet of experience and projects, in reading order."""
    out = [variant.summary]
    for entry in (*variant.experience, *variant.projects):
        out.extend(entry.bullets)
    return out


def export_blockers(variant: ResumeVariant) -> list[str]:
    """Why the resume cannot be downloaded yet: one line per bullet that still needs the person (empty when it can)."""
    reasons: list[str] = []
    for bullet in all_bullets(variant):
        if bullet.status == "dropped":
            continue
        if bullet.status == "needs_user":
            reasons.append(f"{bullet.id}: waiting for your answer")
        elif bullet.placeholders or PLACEHOLDER.search(bullet.text):
            reasons.append(f"{bullet.id}: still has a [x] to fill")
    return reasons


# ───────────────────────── applying the person's answers ─────────────────────────


@dataclass(slots=True)
class DecisionOutcome:
    """The settled resume, the book with the facts the person's figures added, and what could not be applied."""

    variant: ResumeVariant
    book: FactBook
    refused: list[str] = field(default_factory=list)


def _map_bullets(variant: ResumeVariant, changed: Mapping[str, Bullet]) -> ResumeVariant:
    def swap(entry: ExperienceEntry) -> ExperienceEntry:
        return entry.model_copy(update={"bullets": [changed.get(b.id, b) for b in entry.bullets]})

    return variant.model_copy(
        update={
            "summary": changed.get(variant.summary.id, variant.summary),
            "experience": [swap(e) for e in variant.experience],
            "projects": [swap(e) for e in variant.projects],
        }
    )


def _primary(bullet: Bullet, facts: Mapping[str, Fact]) -> Fact | None:
    """The fact a bullet is about: its first cited fact that is not something the person typed."""
    return next((facts[i] for i in bullet.factIds if i in facts and not i.startswith("user-")), None)


def _fill(bullet: Bullet, value: str) -> str:
    """``bullet.text`` with its first ``[x]`` replaced by ``value`` (a unit the slot already has is not doubled)."""
    match = PLACEHOLDER.search(bullet.text)
    if match is None:
        return bullet.text
    unit = match.group()[3:]
    figure = value.strip()
    if unit and figure.endswith(unit):
        figure = figure[: -len(unit)].rstrip()
    return bullet.text[: match.start()] + figure + unit + bullet.text[match.end() :]


def apply_decisions(
    variant: ResumeVariant,
    book: FactBook,
    choices: Iterable[DecisionChoice],
    ctx: DraftContext,
) -> DecisionOutcome:
    """Apply the person's ``choices`` to the bullets they name and re-verify what changed.

    ``keep`` on a stretch is the person vouching for it: the bullet becomes ``verified`` and stays marked
    ``stretch``. ``fill`` needs a figure (``35%``, ``₹12 lakh``); the sentence is re-checked with the figure as a
    ``user`` fact, and may raise the next question. A choice that does not fit its bullet, or an unusable figure,
    is refused and the bullet stays as it was.
    """
    bullets = {b.id: b for b in all_bullets(variant)}
    pending = {d.bulletId: d for d in variant.decisions}
    facts = book.by_id()
    changed: dict[str, Bullet] = {}
    new_decisions: dict[str, Decision | None] = {}
    new_facts: list[Fact] = []
    refused: list[str] = []

    for choice in choices:
        bullet = changed.get(choice.bulletId) or bullets.get(choice.bulletId)
        decision = new_decisions.get(choice.bulletId) or pending.get(choice.bulletId)
        if bullet is None or decision is None or choice.choice not in decision.options:
            refused.append(f"{choice.bulletId}: that answer does not fit this line")
            continue
        primary = _primary(bullet, facts)
        if choice.choice == "drop":
            changed[bullet.id] = bullet.model_copy(update={"status": "dropped", "placeholders": []})
            new_decisions[bullet.id] = None
        elif choice.choice in ("soften", "keep_my_wording"):
            if primary is None:
                refused.append(f"{bullet.id}: no fact to fall back on")
                continue
            changed[bullet.id] = fallback_bullet(bullet.id, primary)
            new_decisions[bullet.id] = None
        elif choice.choice in ("keep", "use_posting_term"):
            changed[bullet.id] = bullet.model_copy(update={"status": "verified"})
            new_decisions[bullet.id] = None
        elif choice.choice == "fill":
            value = tidy(choice.value or "")
            if not _FIGURE.match(value) or primary is None:
                refused.append(f"{bullet.id}: that is not a figure we can use")
                continue
            filled = _fill(bullet, value)
            fact = user_fact(f"Figure given by the person: {value}", key=f"answer:{bullet.id}", quote=value)
            new_facts.append(fact)
            cited = [facts[i] for i in bullet.factIds if i in facts] + [fact]
            result = assess_draft(
                bullet.id,
                filled,
                cited,
                ctx,
                primary=primary,
                model_verdict="stretch" if bullet.verdict == "stretch" else "exact",
            )
            if result.bullet is None:
                refused.append(f"{bullet.id}: {result.rejected}")
                new_facts.remove(fact)
                continue
            changed[bullet.id] = result.bullet
            new_decisions[bullet.id] = result.decision
            facts[fact.id] = fact

    settled = _map_bullets(variant, changed)
    open_decisions = dict(pending)
    for bullet_id, decision in new_decisions.items():
        if decision is None:
            open_decisions.pop(bullet_id, None)
        else:
            open_decisions[bullet_id] = decision
    remaining = [
        d
        for b in all_bullets(settled)
        if b.status == "needs_user" and (d := open_decisions.get(b.id)) is not None
    ]
    settled = settled.model_copy(update={"decisions": remaining})
    return DecisionOutcome(settled, book.with_facts(new_facts) if new_facts else book, refused)


__all__ = [
    "BULLET_MAX_WORDS",
    "OPTIONS",
    "SUMMARY_MAX_WORDS",
    "Assessment",
    "DecisionOutcome",
    "DraftContext",
    "all_bullets",
    "apply_decisions",
    "assess_draft",
    "export_blockers",
    "fallback_bullet",
]
