"""Gates: hard conditions of a posting (work authorisation, location, a mandatory degree ...) checked against the
person's facts before anything is scored.

One Haiku call answers every gate. The prompt shows the question and the facts only: neither the severity nor
the consequence of an answer (the model cannot be tempted to soften or harden a verdict). Code then

  - drops answers for unknown gates and keeps the first answer per gate;
  - keeps only fact ids that exist in the book;
  - turns ``pass`` without a valid fact into ``uncertain`` (a pass needs evidence);
  - turns ``fail`` without a valid fact into ``uncertain`` (a fail must cite the fact that contradicts; absence of
    evidence is never a fail);
  - answers ``uncertain`` for a gate the model skipped or when the call fails.

The decision is code's: only a ``fail`` on a gate of severity 7 or more blocks. ``uncertain`` means "not evidenced,
add it if true" and never blocks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from tayyari.contract.match import GATE_BLOCK_SEVERITY, Fact, Gate, GateAnswer
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.calls import ask_or_none
from tayyari.match.schemas import GateAnswerOut, GateAnswersOut, rows_of, schema_of
from tayyari.match.text import clip
from tayyari.prompts.match import GATES_SYSTEM, build_gates_prompt

GATES_MAX_TOKENS: Final = 1500
MAX_GATE_FACTS: Final = 6


@dataclass(frozen=True, slots=True)
class GateDecision:
    """What the answers add up to, decided in code."""

    blocked: bool
    blocking: tuple[str, ...] = ()
    """Ids of the gates answered ``fail`` at severity 7 or more."""
    uncertain: tuple[str, ...] = ()
    """Ids of the gates the facts could not settle."""
    answers: list[GateAnswer] = field(default_factory=list)


def validate_answers(
    gates: Sequence[Gate], rows: Sequence[GateAnswerOut], fact_ids: set[str]
) -> list[GateAnswer]:
    """One `GateAnswer` per gate, in gate order, after the downgrades described in the module docstring."""
    first: dict[str, GateAnswerOut] = {}
    for given in rows:
        first.setdefault(given.gateId, given)
    out: list[GateAnswer] = []
    for gate in gates:
        row = first.get(gate.id)
        if row is None:
            out.append(
                GateAnswer(
                    gateId=gate.id,
                    factIds=[],
                    verdict="uncertain",
                    note="The evidence could not settle this.",
                )
            )
            continue
        valid = [f for f in dict.fromkeys(row.factIds) if f in fact_ids][:MAX_GATE_FACTS]
        verdict = row.verdict
        note = clip(row.note, 200)
        if verdict in ("pass", "fail") and not valid:
            verdict = "uncertain"
            note = clip(f"{note} (No cited fact backs this, so it is not decided.)".strip(), 240)
        out.append(GateAnswer(gateId=gate.id, factIds=valid, verdict=verdict, note=note))
    return out


def decide(gates: Sequence[Gate], answers: Sequence[GateAnswer]) -> GateDecision:
    """Only a ``fail`` on a gate of severity `GATE_BLOCK_SEVERITY` or more blocks; ``uncertain`` never does."""
    severity = {g.id: g.severity for g in gates}
    blocking = tuple(
        a.gateId for a in answers if a.verdict == "fail" and severity.get(a.gateId, 0) >= GATE_BLOCK_SEVERITY
    )
    uncertain = tuple(a.gateId for a in answers if a.verdict == "uncertain")
    return GateDecision(blocked=bool(blocking), blocking=blocking, uncertain=uncertain, answers=list(answers))


async def answer_gates(
    claude: ClaudeClient,
    *,
    model: str,
    gates: Sequence[Gate],
    facts: Sequence[Fact],
    call_ctx: CallContext,
    today: date | None = None,
) -> GateDecision:
    """Answer ``gates`` from ``facts`` (no call when there are no gates) and decide. A failed call leaves every gate
    ``uncertain``: nothing is blocked on a guess. ``today`` heads the message, so a stated minimum of years is counted
    to today's date."""
    if not gates:
        return GateDecision(blocked=False)
    fact_ids = [f.id for f in facts]
    data = await ask_or_none(
        claude,
        model=model,
        purpose="match.gates",
        system=GATES_SYSTEM,
        user=build_gates_prompt(gates, facts, today=today),
        schema=schema_of(GateAnswersOut, gateId=[g.id for g in gates], factIds=fact_ids),
        max_tokens=GATES_MAX_TOKENS,
        call_ctx=call_ctx,
    )
    rows = rows_of(data, "answers", GateAnswerOut) if data is not None else []
    return decide(gates, validate_answers(gates, rows, set(fact_ids)))


__all__ = ["GATES_MAX_TOKENS", "GateDecision", "answer_gates", "decide", "validate_answers"]
