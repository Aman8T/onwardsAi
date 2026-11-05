"""Coverage: every requirement checked against the person's facts, and the band, counts and confidence computed
from the answers by code.

Haiku checks the requirements in batches of at most ten, all at the same time. Each row is cited first (``factIds``)
and judged after (``status``, ``level``); code then re-reads every row:

  - a fact id that is not in the book is removed;
  - a non-gap status (covered, partial, synonym_only, missing_have_it) with no valid fact id becomes ``unknown``;
  - ``missing_have_it`` needs a cited fact that does not come from the resume (a repository, an app, a public page,
    the person's own words); otherwise it becomes ``unknown``;
  - ``level`` is pulled into the range its status allows (covered 3-4, synonym_only and missing_have_it 2-3,
    partial 1-3, gaps and unknown 0);
  - when every cited fact is only *listed* (a bare mention in a skills line) the row cannot be covered: it becomes
    ``partial`` with a level of at most 2;
  - a requirement the model skipped, or whose batch failed, is ``unknown`` (never a gap, never a fail).

Band, counts and confidence never come from a model: the band is the tier-weighted mean of the levels of the
assessed rows (must 1.0, required 0.8, preferred 0.5, nice 0.25), capped at ``good`` by one missing must and at
``stretch`` by two. ``unknown`` rows are left out of the mean and lower the confidence instead.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Final

from tayyari.contract.match import (
    BAND_GOOD,
    BAND_STRETCH,
    BAND_STRONG,
    Confidence,
    Coverage,
    CoverageStatus,
    Fact,
    GateAnswer,
    MatchBand,
    Requirement,
    StatusCounts,
    tier_weight,
)
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.calls import ask_or_none, gather_all
from tayyari.match.schemas import CoverageOut, CoverageRowOut, rows_of, schema_of
from tayyari.match.text import clip
from tayyari.prompts.match import COVERAGE_SYSTEM, build_coverage_prompt

COVERAGE_BATCH: Final = 10
COVERAGE_MAX_TOKENS: Final = 4096
MAX_ROW_FACTS: Final = 6

NON_GAP: Final[frozenset[str]] = frozenset({"covered", "partial", "synonym_only", "missing_have_it"})
LEVEL_RANGE: Final[dict[str, tuple[int, int]]] = {
    "covered": (3, 4),
    "synonym_only": (2, 3),
    "missing_have_it": (2, 3),
    "partial": (1, 3),
    "missing_gap": (0, 0),
    "unknown": (0, 0),
}
"""The levels each status allows (anchors: 4 direct and verifiable, 3 close, 2 claimed, 1 weak, 0 absent)."""

_BAND_ORDER: Final[tuple[MatchBand, ...]] = ("weak", "stretch", "good", "strong")


def _unknown(req: Requirement, note: str) -> Coverage:
    return Coverage(requirementId=req.id, factIds=[], status="unknown", level=0, note=note)


def validate_row(row: CoverageRowOut, req: Requirement, book: Mapping[str, Fact]) -> Coverage:
    """One checked row (see the module docstring for the rules)."""
    fact_ids = [f for f in dict.fromkeys(row.factIds) if f in book][:MAX_ROW_FACTS]
    status: CoverageStatus = row.status
    note = clip(row.note, 240)

    def downgrade(why: str) -> None:
        nonlocal status, note
        status = "unknown"
        note = clip(f"{note} ({why})".strip(), 260)

    if status in NON_GAP and not fact_ids:
        downgrade("no cited fact backs this")
    elif status == "missing_have_it" and not any(
        all(ref.source != "resume" for ref in book[f].refs) for f in fact_ids
    ):
        downgrade("no fact outside the resume backs this")

    low, high = LEVEL_RANGE[status]
    level = min(high, max(low, int(row.level)))
    if status in NON_GAP and fact_ids and all(book[f].evidenceTier == "listed" for f in fact_ids):
        if status == "covered":
            status = "partial"
            note = clip(f"{note} (Only named in a skills list, so not shown in use.)".strip(), 260)
        level = min(level, 2)
        low, high = LEVEL_RANGE[status]
        level = min(high, max(low, level))

    keep_advice = req.tier in ("must", "required") and status in (
        "partial",
        "synonym_only",
        "missing_gap",
        "missing_have_it",
    )
    return Coverage(
        requirementId=req.id,
        factIds=fact_ids if status != "unknown" else fact_ids[:0],
        status=status,
        level=level,
        note=note,
        interviewRisk=clip(row.interviewRisk, 220) or None if keep_advice else None,
        mitigation=clip(row.mitigation, 220) or None if keep_advice else None,
    )


def validate_coverage(
    rows: Sequence[CoverageRowOut], requirements: Sequence[Requirement], book: Mapping[str, Fact]
) -> list[Coverage]:
    """One `Coverage` per requirement, in requirement order; a requirement without a row is ``unknown``."""
    first: dict[str, CoverageRowOut] = {}
    for given in rows:
        first.setdefault(given.requirementId, given)
    out: list[Coverage] = []
    for req in requirements:
        row = first.get(req.id)
        out.append(
            validate_row(row, req, book)
            if row is not None
            else _unknown(req, "This row could not be checked.")
        )
    return out


async def _assess_batch(
    claude: ClaudeClient,
    *,
    model: str,
    batch: Sequence[Requirement],
    facts: Sequence[Fact],
    call_ctx: CallContext,
    today: date | None = None,
) -> list[Coverage]:
    book = {f.id: f for f in facts}
    data = await ask_or_none(
        claude,
        model=model,
        purpose="match.coverage",
        system=COVERAGE_SYSTEM,
        user=build_coverage_prompt(batch, facts, today=today),
        schema=schema_of(CoverageOut, requirementId=[r.id for r in batch], factIds=list(book)),
        max_tokens=COVERAGE_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    if data is None:
        return [_unknown(r, "The check did not run for this row. Try again.") for r in batch]
    return validate_coverage(rows_of(data, "rows", CoverageRowOut), batch, book)


async def assess_coverage(
    claude: ClaudeClient,
    *,
    model: str,
    requirements: Sequence[Requirement],
    facts: Sequence[Fact],
    call_ctx: CallContext,
    batch_size: int = COVERAGE_BATCH,
    on_batch: Callable[[list[Coverage]], None] | None = None,
    today: date | None = None,
) -> list[Coverage]:
    """Check ``requirements`` against ``facts`` in concurrent batches; rows come back in requirement order.

    ``on_batch`` is called with each batch's rows as soon as that batch is done (the stream sends them on). ``today``
    heads every batch's message, so "fewer years" is judged against today's date.
    """
    batches = [list(requirements[i : i + batch_size]) for i in range(0, len(requirements), batch_size)]

    async def run(batch: list[Requirement]) -> list[Coverage]:
        rows = await _assess_batch(
            claude, model=model, batch=batch, facts=facts, call_ctx=call_ctx, today=today
        )
        if on_batch is not None:
            on_batch(rows)
        return rows

    done = await gather_all(*(run(b) for b in batches))
    return [row for rows in done for row in rows]


# ───────────────────────── counts, band, confidence ─────────────────────────


def count_statuses(coverage: Sequence[Coverage]) -> StatusCounts:
    counts = dict.fromkeys(LEVEL_RANGE, 0)
    for row in coverage:
        counts[row.status] += 1
    return StatusCounts(**counts)


def weighted_score(requirements: Sequence[Requirement], coverage: Sequence[Coverage]) -> float | None:
    """Tier-weighted mean of ``level / 4`` over the assessed rows (``unknown`` left out); None when none is assessed."""
    by_req = {c.requirementId: c for c in coverage}
    total = weight = 0.0
    for req in requirements:
        row = by_req.get(req.id)
        if row is None or row.status == "unknown":
            continue
        w = tier_weight(req.tier)
        total += w * (row.level / 4)
        weight += w
    return total / weight if weight else None


def _cap(band: MatchBand, ceiling: MatchBand) -> MatchBand:
    return _BAND_ORDER[min(_BAND_ORDER.index(band), _BAND_ORDER.index(ceiling))]


def band_of(requirements: Sequence[Requirement], coverage: Sequence[Coverage]) -> MatchBand:
    """The band: from the weighted score, capped by missing musts. Nothing assessed yet reads as ``stretch``
    (neither strong nor weak: the evidence has not spoken)."""
    score = weighted_score(requirements, coverage)
    if score is None:
        return "stretch"
    band: MatchBand = (
        "strong"
        if score >= BAND_STRONG
        else "good"
        if score >= BAND_GOOD
        else "stretch"
        if score >= BAND_STRETCH
        else "weak"
    )
    by_req = {c.requirementId: c for c in coverage}
    must_gaps = sum(
        1
        for r in requirements
        if r.tier == "must" and by_req.get(r.id) and by_req[r.id].status == "missing_gap"
    )
    if must_gaps >= 2:
        return _cap(band, "stretch")
    if must_gaps == 1:
        return _cap(band, "good")
    return band


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def confidence_of(
    requirements: Sequence[Requirement],
    coverage: Sequence[Coverage],
    answers: Sequence[GateAnswer],
    *,
    gate_count: int | None = None,
) -> tuple[Confidence, str]:
    """``(confidence, note)``: high only when every must and required row is covered or partial with facts; low when
    a must is unknown or a gate is uncertain; medium otherwise."""
    by_req = {c.requirementId: c for c in coverage}
    hard = [(r, by_req[r.id]) for r in requirements if r.tier in ("must", "required") and r.id in by_req]
    unknown_musts = sum(1 for r, c in hard if r.tier == "must" and c.status == "unknown")
    uncertain = sum(1 for a in answers if a.verdict == "uncertain")
    if unknown_musts or uncertain:
        parts = []
        if unknown_musts:
            parts.append(f"{_plural(unknown_musts, 'must-have')} can't be told from your evidence yet.")
        if uncertain:
            parts.append(f"{_plural(uncertain, 'hard condition')} aren't evidenced yet.")
        return "low", " ".join([*parts, "Answering the questions firms this up."])
    high = bool(hard) and all(c.status in ("covered", "partial") and c.factIds for _, c in hard)
    if high:
        note = "Every must-have and required row is backed by facts we can cite."
    elif not hard:
        note = "The posting has no must-have rows, so this rests on the preferred ones."
    else:
        note = "Some required rows are reworded matches, only partly shown, or gaps, so the band could move."
    if gate_count == 0:
        note += " The posting names no hard conditions."
    return ("high" if high else "medium"), note


__all__ = [
    "COVERAGE_BATCH",
    "COVERAGE_MAX_TOKENS",
    "LEVEL_RANGE",
    "NON_GAP",
    "assess_coverage",
    "band_of",
    "confidence_of",
    "count_statuses",
    "validate_coverage",
    "validate_row",
    "weighted_score",
]
