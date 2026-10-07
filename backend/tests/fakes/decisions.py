"""A scriptable `DecisionService` for tests of the analysis and chat services: neutral by default, every call recorded.

    decisions = FakeDecisionService(
        first_read=FirstReadResult(lines=["First read: Design and UX"], signals=["Leads others"], facts={}),
        triage=lambda inp: TriageResult(skip=True, skip_reason="login wall") if "login" in inp.text else TriageResult(),
        evidence={("python", "github"): False},          # drop that claim, keep the rest
        gate=GateResult(allow_tools=False, reason="from the profile", p_outside=0.05),
    )
    ...
    assert decisions.gate_calls[0][0].message == "What is my strongest skill?"

Each result can be a value or a callable of the input (``first_read`` and ``gate`` also of the context). Evidence is a
mapping of ``(skill_id, source_id)`` to keep (default: keep everything), a callable of the claim returning keep
or an `EvidenceVerdict`, or None. ``fail("triage", RuntimeError("boom"))`` makes the next call of that method raise
(``times`` calls), for tests that check the caller survives a broken service. ``shadow_compare`` and ``drain`` are
recorded too.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tayyari.decisions.service import (
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

FirstReadScript = FirstReadResult | None | Callable[[FirstReadInput, DecisionContext], FirstReadResult | None]
TriageScript = TriageResult | Callable[[TriageInput], TriageResult]
GateScript = GateResult | Callable[[GateInput, DecisionContext], GateResult]
EvidenceScript = Mapping[tuple[str, str], bool] | Callable[[EvidenceClaim], bool | EvidenceVerdict] | None

_METHODS = frozenset({"first_read", "triage", "check_evidence", "chat_web_gate", "shadow_compare", "drain"})


class FakeDecisionService:
    def __init__(
        self,
        *,
        first_read: FirstReadScript = None,
        triage: TriageScript | None = None,
        evidence: EvidenceScript = None,
        gate: GateScript | None = None,
    ) -> None:
        self.first_read_script: FirstReadScript = first_read
        self.triage_script: TriageScript = triage if triage is not None else TriageResult()
        self.evidence_script: EvidenceScript = evidence
        self.gate_script: GateScript = (
            gate if gate is not None else GateResult(allow_tools=True, reason="fake default")
        )

        self.first_read_calls: list[tuple[FirstReadInput, DecisionContext]] = []
        self.triage_calls: list[tuple[TriageInput, DecisionContext]] = []
        self.evidence_calls: list[tuple[list[EvidenceClaim], DecisionContext]] = []
        self.gate_calls: list[tuple[GateInput, DecisionContext]] = []
        self.shadow_calls: list[tuple[ShadowMaterials, DecisionContext]] = []
        self.drain_calls: list[float] = []
        self._failures: dict[str, list[BaseException]] = {}

    # ── scripting ──

    def fail(self, method: str, error: BaseException, *, times: int = 1) -> FakeDecisionService:
        """Make the next ``times`` calls of ``method`` raise ``error``."""
        if method not in _METHODS:
            raise ValueError(f"unknown DecisionService method {method!r}; use one of {sorted(_METHODS)}")
        self._failures.setdefault(method, []).extend([error] * times)
        return self

    def _maybe_fail(self, method: str) -> None:
        queue = self._failures.get(method)
        if queue:
            raise queue.pop(0)

    # ── DecisionService ──

    async def first_read(self, inp: FirstReadInput, ctx: DecisionContext) -> FirstReadResult | None:
        self.first_read_calls.append((inp, ctx))
        self._maybe_fail("first_read")
        script = self.first_read_script
        return script(inp, ctx) if callable(script) else script

    async def triage(self, inp: TriageInput, ctx: DecisionContext) -> TriageResult:
        self.triage_calls.append((inp, ctx))
        self._maybe_fail("triage")
        script = self.triage_script
        return script(inp) if callable(script) else script

    async def check_evidence(
        self, claims: Sequence[EvidenceClaim], ctx: DecisionContext
    ) -> list[EvidenceVerdict]:
        self.evidence_calls.append((list(claims), ctx))
        self._maybe_fail("check_evidence")
        script = self.evidence_script
        verdicts: list[EvidenceVerdict] = []
        for claim in claims:
            outcome: bool | EvidenceVerdict
            if script is None:
                outcome = True
            elif callable(script):
                outcome = script(claim)
            else:
                outcome = script.get((claim.skill_id, claim.source_id), True)
            if isinstance(outcome, EvidenceVerdict):
                verdicts.append(outcome)
            else:
                verdicts.append(
                    EvidenceVerdict(
                        claim.skill_id,
                        claim.source_id,
                        claim.text,
                        keep=outcome,
                        relation=None if outcome else "says_nothing",
                        reason="fake",
                    )
                )
        return verdicts

    async def chat_web_gate(self, inp: GateInput, ctx: DecisionContext) -> GateResult:
        self.gate_calls.append((inp, ctx))
        self._maybe_fail("chat_web_gate")
        script = self.gate_script
        return script(inp, ctx) if callable(script) else script

    def shadow_compare(self, materials: ShadowMaterials, ctx: DecisionContext) -> None:
        self.shadow_calls.append((materials, ctx))
        self._maybe_fail("shadow_compare")

    async def drain(self, timeout_s: float) -> None:
        self.drain_calls.append(timeout_s)
        self._maybe_fail("drain")

    # ── inspection ──

    @property
    def calls(self) -> dict[str, int]:
        """How many times each method was called."""
        return {
            "first_read": len(self.first_read_calls),
            "triage": len(self.triage_calls),
            "check_evidence": len(self.evidence_calls),
            "chat_web_gate": len(self.gate_calls),
            "shadow_compare": len(self.shadow_calls),
            "drain": len(self.drain_calls),
        }

    def snapshot(self) -> dict[str, Any]:
        """The recorded inputs, for one assertion over everything."""
        return {
            "first_read": [i for i, _ in self.first_read_calls],
            "triage": [i for i, _ in self.triage_calls],
            "check_evidence": [c for c, _ in self.evidence_calls],
            "chat_web_gate": [i for i, _ in self.gate_calls],
            "shadow_compare": [m for m, _ in self.shadow_calls],
            "drain": list(self.drain_calls),
        }


__all__ = ["FakeDecisionService"]
