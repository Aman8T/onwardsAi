"""`MatchService`: the pipeline of the posting match, as pure async services over typed inputs.

    1  read_posting   Haiku reads the pasted text ALONE (no profile in the prompt), code checks quotes, tiers, caps
    2  build_facts    the person's `FactBook`: portfolio facts (cached or extracted), skills of the graph, user facts
    3  answer_gates   one Haiku call; only a `fail` with severity 7 or more blocks
    4  assess_coverage  Haiku per batch of at most 10 requirements, concurrently; downgrades in code
       place_requirements  one Haiku call, at the same time: each requirement's area among the person's own
    5  build_radar    role expectation per profile domain, from requirement weights (no "Other" axis)
    6  make_questions at most three, must-haves first
    7  compose_variant  the resume from cited facts (relevance, selection, phrasing, verification)
    8  write_cover    the cover note from (requirement, fact) pairs; gaps stay on the match page, never in the note
    9  export_*       DOCX and PDF, then the lint reads them back

Steps 1 to 6 are `analyse`; 7 to 9 are called one by one, after the person has settled what the match asked. Every
model call has a ``match.*`` purpose and thinking off (extraction-type calls). Nothing is stored here: the caller
hands in the cached posting, facts and user facts through the optional ports, or as arguments.

Order of effects: the posting is read at the same time as the facts are gathered (they never meet in a prompt); the
gates and the coverage are checked at the same time; the band, counts, confidence, radar and questions are computed
afterwards, in code, from the answers. A re-check keeps the radar: the requirements and the graph have not changed.
A re-checked gate takes the requirements that quote the same sentence of the posting with it (`requirements_of_gates`),
so an answer that settles "valid KNC or INC registration" as a gate settles it as a requirement too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Final

from tayyari.contract.match import (
    MATCH_VERSION,
    Coverage,
    CoverNote,
    Fact,
    GateAnswer,
    MatchResult,
    Posting,
    Question,
    Radar,
    ResumeVariant,
)
from tayyari.contract.models import Profile
from tayyari.contract.portfolio import Fact as PortfolioFact
from tayyari.contract.stored import StoredContext
from tayyari.core.clock import Clock, SystemClock
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.areas import Placement, place_requirements
from tayyari.match.bullets import DecisionOutcome, DraftContext, apply_decisions
from tayyari.match.calls import call_timeout, gather2
from tayyari.match.cover import CoverResult, write_cover
from tayyari.match.coverage import (
    assess_coverage,
    band_of,
    confidence_of,
    count_statuses,
)
from tayyari.match.export import ExportedFiles, export_cover, export_resume
from tayyari.match.facts import build_facts, extract_portfolio_facts
from tayyari.match.gate import GateDecision, answer_gates, decide
from tayyari.match.lint import lint_cover, lint_resume
from tayyari.match.models import DecisionChoice, FactBook, Report
from tayyari.match.ports import FactsCache, PostingCache
from tayyari.match.posting import PostingResult, clean_posting_text, read_posting
from tayyari.match.questions import make_questions
from tayyari.match.radar import build_radar
from tayyari.match.text import fold_text, posting_hash
from tayyari.match.variant import VariantResult, compose_variant, draft_context
from tayyari.portfolio.facts import facts_hash
from tayyari.portfolio.ground import tokens_of

# ───────────────────────── events (the stream of M2 sends these on) ─────────────────────────


@dataclass(frozen=True, slots=True)
class PostingReady:
    posting: Posting
    facts: list[Fact] = field(default_factory=list)
    """The facts the rows will cite (the person's `FactBook`), so a stream can show citations as rows arrive."""


@dataclass(frozen=True, slots=True)
class GatesReady:
    answers: list[GateAnswer]
    blocked: bool


@dataclass(frozen=True, slots=True)
class CoverageBatch:
    rows: list[Coverage]


@dataclass(frozen=True, slots=True)
class RadarReady:
    radar: Radar


@dataclass(frozen=True, slots=True)
class QuestionsReady:
    questions: list[Question]


MatchEvent = PostingReady | GatesReady | CoverageBatch | RadarReady | QuestionsReady
Emit = Callable[[MatchEvent], None]


@dataclass(slots=True)
class MatchOutcome:
    """Everything `analyse` produced: the posting, the evidence it was checked against and the result."""

    posting: Posting
    book: FactBook
    result: MatchResult
    report: Report = field(default_factory=Report)
    """The refusals while reading the posting (quotes not found, injected text ...)."""


@dataclass(frozen=True, slots=True)
class ExportedResume:
    files: ExportedFiles
    variant: ResumeVariant
    """The variant with its lint report attached."""


@dataclass(frozen=True, slots=True)
class ExportedCover:
    files: ExportedFiles
    note: CoverNote
    """The note with its lint report attached."""


def assemble_result(
    profile: Profile,
    posting: Posting,
    decision: GateDecision,
    coverage: Sequence[Coverage],
    *,
    version: int = 1,
    placed: Placement | None = None,
    radar: Radar | None = None,
) -> MatchResult:
    """The `MatchResult` of the checked rows: band, counts, confidence, blocked, radar and questions, all by code.

    ``radar`` is kept as given (a re-check); otherwise it is built from ``placed`` (the areas call) and code.
    """
    confidence, note = confidence_of(
        posting.requirements, coverage, decision.answers, gate_count=len(posting.gates)
    )
    return MatchResult(
        postingHash=posting.hash,
        band=band_of(posting.requirements, coverage),
        counts=count_statuses(coverage),
        confidence=confidence,
        confidenceNote=note,
        gates=list(decision.answers),
        blocked=decision.blocked,
        coverage=list(coverage),
        radar=radar if radar is not None else build_radar(profile, posting.requirements, placed),
        questions=make_questions(posting, coverage, decision.answers),
        version=version,
    )


class MatchService:
    """The pipeline (see the module docstring). ``model`` is the Haiku model that reads and classifies; ``writer_model``
    (default: the same) is the one that phrases the resume sentences and writes the cover note."""

    def __init__(
        self,
        *,
        claude: ClaudeClient,
        model: str,
        writer_model: str | None = None,
        postings: PostingCache | None = None,
        facts_cache: FactsCache | None = None,
        call_timeout_s: float | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._claude = claude
        self._clock: Clock = clock or SystemClock()
        """Gives the gate and coverage checks today's date (years of experience are counted to it)."""
        self._model = model
        self._writer = writer_model or model
        self._postings = postings
        self._facts_cache = facts_cache
        self._timeout = call_timeout_s
        """How long one classification call may take before its stage settles for the honest default."""

    # ── 1 · the posting ──

    async def read_posting(self, text: str, *, call_ctx: CallContext) -> PostingResult:
        """The posting, from the cache when this text was read before, else read by the model (no person in the prompt)."""
        cleaned = clean_posting_text(text)
        if self._postings is not None:
            cached = await self._postings.get(posting_hash(cleaned))
            if cached is not None:
                return PostingResult(posting=cached)
        result = await read_posting(self._claude, model=self._model, text=cleaned, call_ctx=call_ctx)
        if self._postings is not None:
            await self._postings.put(result.posting)
        return result

    # ── 2 · the facts ──

    async def build_book(
        self,
        profile: Profile,
        context: StoredContext,
        *,
        call_ctx: CallContext,
        user_facts: Sequence[Fact] = (),
        portfolio_facts: Sequence[PortfolioFact] | None = None,
    ) -> FactBook:
        """The person's `FactBook`. ``portfolio_facts`` (already extracted) skip the model; otherwise the cache is
        asked for them, and only then the portfolio extractor is run."""
        facts = list(portfolio_facts) if portfolio_facts is not None else None
        if facts is None and self._facts_cache is not None:
            facts = await self._facts_cache.get(profile.id, facts_hash(context))
        if facts is None:
            extracted = await extract_portfolio_facts(
                self._claude,
                model=self._model,
                context=context,
                profile=profile,
                call_ctx=replace(call_ctx, purpose="match.facts"),
            )
            facts = extracted.facts
            if self._facts_cache is not None and not extracted.failed_sources:
                await self._facts_cache.put(profile.id, facts_hash(context), facts)
        return await asyncio.to_thread(build_facts, profile, context, facts, user_facts)

    # ── 1-6 · the match ──

    async def analyse(
        self,
        *,
        profile: Profile,
        context: StoredContext,
        posting_text: str,
        call_ctx: CallContext,
        user_facts: Sequence[Fact] = (),
        portfolio_facts: Sequence[PortfolioFact] | None = None,
        emit: Emit | None = None,
    ) -> MatchOutcome:
        """Read the posting, gather the facts, check gates and coverage, compute the result."""
        with call_timeout(self._timeout):
            read, book = await gather2(
                self.read_posting(posting_text, call_ctx=call_ctx),
                self.build_book(
                    profile,
                    context,
                    call_ctx=call_ctx,
                    user_facts=user_facts,
                    portfolio_facts=portfolio_facts,
                ),
            )
            posting = read.posting
            _send(emit, PostingReady(posting, list(book.facts)))

            def batch(rows: list[Coverage]) -> None:
                _send(emit, CoverageBatch(rows))

            decision, (coverage, placed) = await gather2(
                self._gates(posting, book, call_ctx),
                gather2(
                    assess_coverage(
                        self._claude,
                        model=self._model,
                        requirements=posting.requirements,
                        facts=book.facts,
                        call_ctx=call_ctx,
                        on_batch=batch,
                        today=self._today(),
                    ),
                    place_requirements(
                        self._claude,
                        model=self._model,
                        profile=profile,
                        requirements=posting.requirements,
                        call_ctx=call_ctx,
                    ),
                ),
            )
            _send(emit, GatesReady(decision.answers, decision.blocked))
            result = assemble_result(
                profile, posting, decision, coverage, version=MATCH_VERSION, placed=placed
            )
            _send(emit, RadarReady(result.radar))
            _send(emit, QuestionsReady(result.questions))
            return MatchOutcome(posting=posting, book=book, result=result, report=read.report)

    def _today(self) -> date:
        return self._clock.now().date()

    async def _gates(self, posting: Posting, book: FactBook, call_ctx: CallContext) -> GateDecision:
        return await answer_gates(
            self._claude,
            model=self._model,
            gates=posting.gates,
            facts=book.facts,
            call_ctx=call_ctx,
            today=self._today(),
        )

    async def recheck(
        self,
        *,
        profile: Profile,
        posting: Posting,
        book: FactBook,
        previous: MatchResult,
        call_ctx: CallContext,
        requirement_ids: Iterable[str] | None = None,
        gate_ids: Iterable[str] | None = None,
    ) -> MatchResult:
        """Re-check only the named rows and gates (after the person answered), keep the rest, bump the version.

        ``None`` means "all". Never consumes anything: it is the free re-check the product promises. The radar is
        kept (a radar stored before ``outside`` existed is built again by code).
        """
        wanted = set(requirement_ids) if requirement_ids is not None else {r.id for r in posting.requirements}
        wanted_gates = set(gate_ids) if gate_ids is not None else {g.id for g in posting.gates}
        wanted |= requirements_of_gates(posting, wanted_gates)
        redo = [r for r in posting.requirements if r.id in wanted]
        gates = [g for g in posting.gates if g.id in wanted_gates]
        with call_timeout(self._timeout):
            fresh_decision, fresh_rows = await gather2(
                answer_gates(
                    self._claude,
                    model=self._model,
                    gates=gates,
                    facts=book.facts,
                    call_ctx=call_ctx,
                    today=self._today(),
                ),
                assess_coverage(
                    self._claude,
                    model=self._model,
                    requirements=redo,
                    facts=book.facts,
                    call_ctx=call_ctx,
                    today=self._today(),
                ),
            )
        rows = {c.requirementId: c for c in previous.coverage}
        rows.update({c.requirementId: c for c in fresh_rows})
        answers = {a.gateId: a for a in previous.gates}
        answers.update({a.gateId: a for a in fresh_decision.answers})
        merged = decide(posting.gates, [answers[g.id] for g in posting.gates if g.id in answers])
        coverage = [rows[r.id] for r in posting.requirements if r.id in rows]
        kept = previous.radar if previous.radar.outside is not None else None
        return assemble_result(profile, posting, merged, coverage, version=previous.version + 1, radar=kept)

    # ── 7 · the resume ──

    async def compose_resume(
        self,
        *,
        book: FactBook,
        posting: Posting,
        result: MatchResult,
        call_ctx: CallContext,
    ) -> VariantResult:
        """The resume for this posting (see `variant`). Raises nothing for thin evidence: it shows what there is."""
        with call_timeout(self._timeout):
            return await compose_variant(
                self._claude,
                model=self._model,
                writer_model=self._writer,
                book=book,
                posting=posting,
                coverage=result.coverage,
                call_ctx=call_ctx,
            )

    def settle_resume(
        self,
        variant: ResumeVariant,
        book: FactBook,
        posting: Posting,
        result: MatchResult,
        choices: Iterable[DecisionChoice],
    ) -> DecisionOutcome:
        """Apply the person's answers to the resume's open questions and re-verify what changed (no model call)."""
        ctx: DraftContext = draft_context(posting, result.coverage)
        return apply_decisions(variant, book, choices, ctx)

    async def export_resume(self, variant: ResumeVariant, book: FactBook) -> ExportedResume:
        """The files and their lint, built in a worker thread. Raises `ExportBlocked` while the resume has open
        questions."""
        return await asyncio.to_thread(self._export_resume, variant, book)

    @staticmethod
    def _export_resume(variant: ResumeVariant, book: FactBook) -> ExportedResume:
        files = export_resume(variant)
        lint = lint_resume(variant, book, pdf=files.pdf, docx=files.docx, expected_pages=files.pages)
        return ExportedResume(files, variant.model_copy(update={"lint": lint}))

    # ── 8 · the cover note ──

    async def compose_cover(
        self,
        *,
        book: FactBook,
        posting: Posting,
        result: MatchResult,
        call_ctx: CallContext,
    ) -> CoverResult:
        """The cover note (see `cover`). Raises `CoverUnavailable` when no covered requirement has facts."""
        with call_timeout(self._timeout):
            return await write_cover(
                self._claude,
                model=self._writer,
                book=book,
                posting=posting,
                coverage=result.coverage,
                call_ctx=call_ctx,
            )

    async def export_cover(self, note: CoverNote, book: FactBook, posting: Posting) -> ExportedCover:
        """The note as files, with its lint attached (built in a worker thread)."""
        return await asyncio.to_thread(self._export_cover, note, book, posting)

    @staticmethod
    def _export_cover(note: CoverNote, book: FactBook, posting: Posting) -> ExportedCover:
        files = export_cover(note, name=book.name, contact=book.contact, company=posting.company)
        lint = lint_cover(note, book, posting=posting, pdf=files.pdf, docx=files.docx)
        return ExportedCover(files, note.model_copy(update={"lint": lint}))


def _send(emit: Emit | None, event: MatchEvent) -> None:
    if emit is not None:
        emit(event)


GATE_OVERLAP: Final = 0.6
"""Share of the shorter quote's words two quotes must have in common to be the same sentence of the posting."""


def _words(text: str) -> set[str]:
    return {t for t in tokens_of(text) if len(t) > 2}


def requirements_of_gates(posting: Posting, gate_ids: Iterable[str]) -> set[str]:
    """The requirements that quote the same sentence of the posting as one of the gates ``gate_ids``: one quote holds
    the other, or they share most of their words. Code only; the posting's own wording decides."""
    gates = [g for g in posting.gates if g.id in set(gate_ids)]
    out: set[str] = set()
    for gate in gates:
        g_folded, g_words = fold_text(gate.quote), _words(gate.quote)
        for req in posting.requirements:
            r_folded, r_words = fold_text(req.quote), _words(req.quote)
            if not r_folded or not g_folded:
                continue
            if r_folded in g_folded or g_folded in r_folded:
                out.add(req.id)
                continue
            smaller = min(len(g_words), len(r_words))
            if smaller >= 2 and len(g_words & r_words) >= GATE_OVERLAP * smaller:
                out.add(req.id)
    return out


__all__ = [
    "CoverageBatch",
    "Emit",
    "ExportedCover",
    "ExportedResume",
    "GatesReady",
    "MatchEvent",
    "MatchOutcome",
    "MatchService",
    "PostingReady",
    "QuestionsReady",
    "RadarReady",
    "assemble_result",
    "requirements_of_gates",
]
