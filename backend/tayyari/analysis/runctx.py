"""What every stage of one analysis run shares: the settings, the clients, the ids and the event sink.

`RunContext` is built once per `PipelineAnalysisService.run` call. Stages (readers, signals, first read,
synthesis, evidence check) take it instead of a long list of arguments, so they can be tested alone with
fakes and a hand-made `send`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from tayyari.contract import AnalysisEvent, AnalyzeRequest, RequestContext
from tayyari.core.clock import Clock, paced
from tayyari.core.settings import Settings
from tayyari.decisions.service import DecisionContext, DecisionService
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext, Purpose

Send = Callable[[AnalysisEvent], None]
"""Puts one event on the stream (never blocks; the stream's queue is unbounded)."""


@dataclass(frozen=True, slots=True)
class RunContext:
    settings: Settings
    clock: Clock
    claude: ClaudeClient
    decisions: DecisionService
    req: AnalyzeRequest
    rc: RequestContext
    profile_id: str
    analysis_id: UUID
    send: Send

    @property
    def scale(self) -> float:
        """Multiplier for every pacing delay (`Settings.pacing_scale`; 0 in tests)."""
        return self.settings.pacing_scale

    async def pace(self, seconds: float) -> None:
        """Sleep ``seconds * pacing_scale`` on the injected clock (nothing at all when that is zero)."""
        await paced(self.clock, seconds, self.scale)

    def call_ctx(self, purpose: Purpose) -> CallContext:
        """Correlation data for a Claude call made on behalf of this analysis."""
        return CallContext(
            purpose=purpose,
            request_id=self.rc.request_id,
            profile_id=self.profile_id,
            analysis_id=self.analysis_id,
            ip_hash=self.rc.ip_hash,
        )

    @property
    def decision_ctx(self) -> DecisionContext:
        """Correlation data for a Jev decision made on behalf of this analysis."""
        return DecisionContext(
            profile_id=self.profile_id,
            analysis_id=self.analysis_id,
            is_sample=self.req.sample,
            request_id=self.rc.request_id,
            purpose_tag="analysis",
        )


__all__ = ["RunContext", "Send"]
