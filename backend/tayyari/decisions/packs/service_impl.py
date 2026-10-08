"""`JevDecisionService`: the `DecisionService` the analysis and chat services call, built on the packs and the runner.

    first_read      the instant read of the resume (lines and chips for the analysis screen)
    triage          one source: skip a login wall or an empty page, label the rest
    check_evidence  one verdict per evidence claim: code first, Jev only for claims whose names or numbers are missing
    check_copy      one verdict per generated portfolio sentence: Jev (or Claude) is asked about every one
    chat_web_gate   may the coach use web tools for this message
    shadow_compare  queue Jev's own skill, requirement and role numbers next to Sonnet's (logged, never used)
    drain           wait for the background work and flush the decision log

Nothing here raises for a Jev, Claude or spend failure, or for a bug in a pack: each method has a neutral answer (no
first read, no skip, keep the claim, allow tools) and returns it. Only cancellation (the client went away)
propagates. Each method also has a wall-clock budget, so a slow Jev can never hold up an analysis or a chat turn
for long:

    first_read    timeout_display_s + 0.5 s        triage   2 x timeout_display_s (room for one Claude fallback)
    check_evidence  timeout_evidence_s, for all claims together (what finished by then counts, the rest is kept)
    check_copy      timeout_evidence_s, for all sentences together (same rule)
    chat_web_gate  timeout_display_s + 0.5 s

`build_registry()` lists every pack, for the container to log config ids that match no spec.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ...core.clock import Clock, SystemClock
from ...core.logging import get_logger
from ...core.settings import Settings
from ..registry import DecisionRegistry
from ..runner import DecisionRunner
from ..service import (
    CopyClaim,
    CopyVerdict,
    DecisionContext,
    EvidenceClaim,
    EvidenceVerdict,
    FirstReadInput,
    FirstReadResult,
    GateInput,
    GateResult,
    ShadowMaterials,
    TriageInput,
    TriageResult,
)
from ..spec import DecisionPack, DecisionResult
from . import evidence
from .chat_gate import CHAT_GATE_PACK, has_link, interpret_gate
from .common import head
from .evidence import EVIDENCE_PACK, CodeCheck, EvidenceQuestion, SourceCorpus
from .first_read import FIRST_READ_PACK, interpret_first_read
from .first_read import spec_ids_to_ask as first_read_questions
from .shadow import SHADOW_PACK, ShadowPlan, build_plan
from .triage import TRIAGE_PACK, interpret_triage
from .triage import spec_ids_to_ask as triage_questions

log = get_logger(__name__)

COPY_PURPOSE = "portfolio"
"""The ``purpose_tag`` of the decision-log rows written by `JevDecisionService.check_copy`."""

ALL_PACKS: tuple[DecisionPack[Any], ...] = (
    FIRST_READ_PACK,
    TRIAGE_PACK,
    EVIDENCE_PACK,
    CHAT_GATE_PACK,
    SHADOW_PACK,
)


def build_registry() -> DecisionRegistry:
    """A registry holding every pack of the build (the container checks the configured ids against it)."""
    return DecisionRegistry(ALL_PACKS)


@dataclass(slots=True)
class _Ask:
    """One evidence question and the claims (by index) that share it."""

    question: EvidenceQuestion
    check: CodeCheck
    indices: list[int] = field(default_factory=list)


class JevDecisionService:
    """Implements `tayyari.decisions.service.DecisionService` on top of a `DecisionRunner`."""

    def __init__(self, *, runner: DecisionRunner, settings: Settings, clock: Clock | None = None) -> None:
        self._runner = runner
        self._settings = settings
        self._clock: Clock = clock if clock is not None else SystemClock()
        display_s = settings.jev.timeout_display_s
        self._first_read_budget_s = display_s + 0.5
        self._triage_budget_s = 2 * display_s
        self._gate_budget_s = display_s + 0.5
        self._evidence_budget_s = settings.jev.timeout_evidence_s
        self._copy_budget_s = settings.jev.timeout_evidence_s

    # ───────────────────────── first read ─────────────────────────

    async def first_read(self, inp: FirstReadInput, ctx: DecisionContext) -> FirstReadResult | None:
        if not (head(inp.resume_text, 1) or head(inp.notes_text, 1)):
            return None  # nothing to read (a scanned resume has no text: the Haiku reader gets its PDF)
        started = self._clock.monotonic()
        try:
            async with asyncio.timeout(self._first_read_budget_s):
                results = await self._runner.run(
                    FIRST_READ_PACK, inp, ctx=ctx, only=first_read_questions(inp)
                )
            read = interpret_first_read(results)
        except TimeoutError:
            log.warning("first_read_timeout", budget_s=self._first_read_budget_s)
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("first_read_failed", exc_info=True)
            return None
        log.info(
            "first_read_done",
            lines=0 if read is None else len(read.lines),
            signals=0 if read is None else len(read.signals),
            latency_ms=round((self._clock.monotonic() - started) * 1000),
        )
        return read

    # ───────────────────────── triage ─────────────────────────

    async def triage(self, inp: TriageInput, ctx: DecisionContext) -> TriageResult:
        if not inp.text.strip():
            return (
                TriageResult()
            )  # no text: ingest has already said why, and a scanned resume must be read from its PDF
        try:
            async with asyncio.timeout(self._triage_budget_s):
                results = await self._runner.run(TRIAGE_PACK, inp, ctx=ctx, only=triage_questions(inp.kind))
            result = interpret_triage(inp, results)
        except TimeoutError:
            log.warning("triage_timeout", kind=inp.kind, budget_s=self._triage_budget_s)
            return TriageResult()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("triage_failed", kind=inp.kind, exc_info=True)
            return TriageResult()
        if result.skip:
            log.info("triage_skip", kind=inp.kind, reason=result.text_quality)
        return result

    # ───────────────────────── evidence ─────────────────────────

    async def check_evidence(
        self, claims: Sequence[EvidenceClaim], ctx: DecisionContext
    ) -> list[EvidenceVerdict]:
        items = list(claims)
        verdicts: list[EvidenceVerdict | None] = [None] * len(items)
        try:
            await self._check_evidence(items, ctx, verdicts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("evidence_check_failed", exc_info=True)
        return [
            v if v is not None else evidence.kept(claim, "the check did not finish, kept")
            for claim, v in zip(items, verdicts, strict=True)
        ]

    async def _check_evidence(
        self, claims: list[EvidenceClaim], ctx: DecisionContext, verdicts: list[EvidenceVerdict | None]
    ) -> None:
        """Fill ``verdicts`` (None stays "kept"): the code stage for every claim, then Jev for the unsettled ones."""
        # The code stage reads sources of hundreds of kilobytes: keep it off the event loop's thread.
        asks = await asyncio.to_thread(self._code_stage, claims, verdicts)

        answered = 0
        dropped = 0
        if asks:
            ordered = list(asks.values())
            results = await self._ask_evidence([a.question for a in ordered], ctx)
            for ask, result in zip(ordered, results, strict=True):
                answered += result is not None and result.used
                for index in ask.indices:
                    verdict = evidence.jev_verdict(claims[index], ask.check, result)
                    verdicts[index] = verdict
                    dropped += not verdict.keep
        log.info(
            "evidence_checked",
            claims=len(claims),
            asked=len(asks),
            answered=answered,
            dropped=dropped,
        )

    @staticmethod
    def _code_stage(
        claims: list[EvidenceClaim], verdicts: list[EvidenceVerdict | None]
    ) -> dict[tuple[str, str], _Ask]:
        """The code stage: settle what the code can (into ``verdicts``) and return the questions Jev must answer,
        one per distinct (source, claim). A claim whose check crashes is left as None (kept)."""
        corpora: dict[str, SourceCorpus] = {}
        asks: dict[tuple[str, str], _Ask] = {}
        for index, claim in enumerate(claims):
            try:
                corpus = corpora.get(claim.source_id)
                if corpus is None or not (corpus.raw is claim.source_text or corpus.raw == claim.source_text):
                    corpus = evidence.build_corpus(claim.source_text, claim.reader_output, claim.facts)
                    corpora[claim.source_id] = corpus
                check = evidence.code_check(claim.text, corpus)
                if check.keep:
                    verdicts[index] = evidence.code_verdict(claim, check)
                    continue
                key = (claim.source_id, evidence.normalise(claim.text))
                ask = asks.get(key)
                if ask is None:
                    window = evidence.best_window(claim.source_text, claim.text, positions=corpus.positions())
                    ask = asks[key] = _Ask(EvidenceQuestion(claim.text, window), check)
                ask.indices.append(index)
            except Exception:
                log.warning("evidence_code_check_failed", source=claim.source_id, exc_info=True)
        return asks

    async def _ask_evidence(
        self, questions: list[EvidenceQuestion], ctx: DecisionContext
    ) -> list[DecisionResult[Any] | None]:
        """One `evidence.support` answer per question, asked in waves of `evidence.CONCURRENCY` within the budget.

        A question that did not finish in time (or whose wave failed) has no answer: its claim is kept.
        """
        return await self._ask_support(
            questions, ctx, budget_s=self._evidence_budget_s, event="evidence_budget_spent"
        )

    async def _ask_support(
        self, questions: list[EvidenceQuestion], ctx: DecisionContext, *, budget_s: float, event: str
    ) -> list[DecisionResult[Any] | None]:
        """The shared asking loop of the evidence and portfolio checks (see `_ask_evidence`)."""
        answers: list[DecisionResult[Any] | None] = [None] * len(questions)
        width = evidence.CONCURRENCY
        try:
            async with asyncio.timeout(budget_s):
                for start in range(0, len(questions), width):
                    wave = questions[start : start + width]
                    packs = await self._runner.run_many(EVIDENCE_PACK, wave, concurrency=width, ctx=ctx)
                    for offset, pack_result in enumerate(packs):
                        answers[start + offset] = pack_result.get(evidence.SPEC_ID)
        except TimeoutError:
            log.warning(event, budget_s=budget_s, unanswered=sum(a is None for a in answers))
        return answers

    # ───────────────────────── portfolio copy ─────────────────────────

    async def check_copy(self, claims: Sequence[CopyClaim], ctx: DecisionContext) -> list[CopyVerdict]:
        items = list(claims)
        verdicts: list[CopyVerdict | None] = [None] * len(items)
        ctx = dataclasses.replace(ctx, purpose_tag=COPY_PURPOSE)
        try:
            await self._check_copy(items, ctx, verdicts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("copy_check_failed", exc_info=True)
        return [
            v if v is not None else evidence.copy_kept(claim, "the check did not finish, kept")
            for claim, v in zip(items, verdicts, strict=True)
        ]

    async def _check_copy(
        self, claims: list[CopyClaim], ctx: DecisionContext, verdicts: list[CopyVerdict | None]
    ) -> None:
        """Fill ``verdicts`` (None stays "kept"). Every sentence is asked: no anchor short-cut, the portfolio
        grounder has already checked names and numbers strictly, and this is the semantic second opinion."""
        asks: dict[tuple[str, str], list[int]] = {}
        questions: list[EvidenceQuestion] = []
        for index, claim in enumerate(claims):
            try:
                if not claim.sentence.strip():
                    verdicts[index] = evidence.copy_kept(claim, "empty sentence, kept")
                    continue
                if not claim.corpus.strip():
                    verdicts[index] = evidence.copy_kept(claim, "no text to check against, kept")
                    continue
                key = (evidence.normalise(claim.sentence), claim.corpus)
                slots = asks.get(key)
                if slots is None:
                    question = evidence.copy_question(claim)
                    asks[key] = slots = []
                    questions.append(question)
                slots.append(index)
            except Exception:
                log.warning("copy_question_failed", slot=claim.slot_id, exc_info=True)
        # a sentence whose question could not be built is in no list: it stays None (kept)
        indices_by_question = list(asks.values())

        answered = 0
        dropped = 0
        if questions:
            results = await self._ask_support(
                questions, ctx, budget_s=self._copy_budget_s, event="copy_budget_spent"
            )
            for indices, result in zip(indices_by_question, results, strict=True):
                answered += result is not None and result.used
                for index in indices:
                    verdict = evidence.copy_verdict(claims[index], result)
                    verdicts[index] = verdict
                    dropped += not verdict.keep
        log.info(
            "copy_checked", sentences=len(claims), asked=len(questions), answered=answered, dropped=dropped
        )

    # ───────────────────────── chat gate ─────────────────────────

    async def chat_web_gate(self, inp: GateInput, ctx: DecisionContext) -> GateResult:
        message = inp.message.strip()
        if not message:
            return GateResult(allow_tools=True, reason="empty message, tools allowed")
        if has_link(message):
            return GateResult(allow_tools=True, reason="the message has a link, tools allowed")
        try:
            async with asyncio.timeout(self._gate_budget_s):
                results = await self._runner.run(CHAT_GATE_PACK, inp, ctx=ctx)
            return interpret_gate(results)
        except TimeoutError:
            log.warning("chat_gate_timeout", budget_s=self._gate_budget_s)
            return GateResult(allow_tools=True, reason="the gate timed out, tools allowed")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("chat_gate_failed", exc_info=True)
            return GateResult(allow_tools=True, reason="the gate failed, tools allowed")

    # ───────────────────────── shadow ─────────────────────────

    def shadow_compare(self, materials: ShadowMaterials, ctx: DecisionContext) -> None:
        """Queue the shadow questions as a tracked task and return at once. Never raises."""
        try:
            plan = build_plan(materials.profile)
            if not plan.bindings:
                return
            coro = self._run_shadow(materials, plan, ctx)
            try:
                self._runner.spawn(coro, name="decision-shadow-compare")
            except BaseException:
                coro.close()  # no running loop (or a refused task): do not leave a coroutine never awaited
                raise
        except Exception:
            log.warning("shadow_compare_not_started", exc_info=True)

    async def _run_shadow(self, materials: ShadowMaterials, plan: ShadowPlan, ctx: DecisionContext) -> None:
        try:
            await self._runner.run(
                SHADOW_PACK, materials, ctx=ctx, bindings=plan.bindings, incumbent=plan.incumbent
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("shadow_compare_failed", exc_info=True)

    # ───────────────────────── background work ─────────────────────────

    async def drain(self, timeout_s: float) -> None:
        try:
            await self._runner.drain(timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("decision_drain_failed", exc_info=True)


__all__ = ["ALL_PACKS", "JevDecisionService", "build_registry"]
