"""Shared record types written by the LLM layer and the decisions layer and persisted by storage.

Frozen by the orchestrator at the Wave 0 interface freeze: units read these, they do not change them.
Every cost is integer micro-USD (1 USD = 1_000_000) so sums never drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

CallStatus = Literal["ok", "error", "cancelled"]
JevCallStatus = Literal["ok", "error", "skipped"]
DecisionSource = Literal["jev", "claude", "default"]


@dataclass(frozen=True, slots=True)
class LlmCallRecord:
    """One Anthropic API request (a `pause_turn` continuation is its own row)."""

    id: UUID
    created_at: datetime
    purpose: str  # "reader" | "synth" | "chat" | "decision_fallback" | "smoke" | "eval" | "label"
    model: str
    status: CallStatus
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    web_searches: int = 0
    cost_micro_usd: int = 0
    latency_ms: int = 0
    ttft_ms: int | None = None
    attempt: int = 1
    request_id: str | None = None
    stop_reason: str | None = None
    error_kind: str | None = None
    profile_id: str | None = None
    session_id: str | None = None
    analysis_id: UUID | None = None
    ip_hash: str | None = None


@dataclass(frozen=True, slots=True)
class StateBlob:
    """A Jev request state, stored once per distinct content (sha256 of its canonical JSON)."""

    hash: str  # sha256 hex of core.hashing.canonical_json(state)
    payload: bytes  # zlib-compressed canonical JSON
    raw_bytes: int  # length of the uncompressed canonical JSON
    is_sample: bool = False


@dataclass(frozen=True, slots=True)
class JevCallRecord:
    """One Jev `systemone` request: a pack of questions over one state."""

    id: UUID
    created_at: datetime
    pack: str
    model_requested: str
    state_hash: str
    questions: dict[str, Any]  # the canonical question JSON exactly as sent
    status: JevCallStatus
    model_served: str | None = None
    response: dict[str, Any] | None = None  # raw response JSON
    error_kind: str | None = None
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micro_usd: int = 0
    latency_ms: int = 0
    profile_id: str | None = None
    analysis_id: UUID | None = None
    is_sample: bool = False


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One decision outcome (a spec applied to a subject), linked to its Jev call and any Claude call."""

    id: UUID
    created_at: datetime
    spec_id: str
    spec_version: str
    pack: str
    mode: str  # effective mode: "off" | "shadow" | "primary"
    used: bool
    source_used: DecisionSource
    escalated: bool = False
    escalation_reason: str | None = None
    call_id: UUID | None = None  # JevCallRecord.id
    claude_call_id: UUID | None = None  # LlmCallRecord.id of the fallback or audit answer
    subject_ref: str | None = None  # e.g. skill id, "role:<id>#2", source id
    jev_answer: dict[str, Any] | None = None  # normalised JevAnswer
    jev_confidence: float | None = None
    threshold: float | None = None
    claude_answer: dict[str, Any] | None = None
    incumbent: dict[str, Any] | None = None  # the value the decision is compared with (shadow)
    latency_ms: int = 0
    cost_micro_usd: int = 0
    profile_id: str | None = None
    analysis_id: UUID | None = None
    is_sample: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
