"""Turning API usage into `Usage` (with cost) and a sink that receives one `LlmCallRecord` per request.

`usage_from_message` reads an Anthropic `Message` (or anything shaped like one, including a plain dict
from a recorded response) and tolerates every optional field being absent, so a model that stops
reporting cache or server-tool counters never breaks a call.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ..contract.records import LlmCallRecord
from .models import RawTokens, claude_cost_micro, spec_for
from .types import Usage


class UsageSink(Protocol):
    """Where finished Claude calls are reported: the guarded client writes them to `llm_calls` (and the spend
    ledger) awaited, because the spend guard reads them back."""

    async def record(self, rec: LlmCallRecord) -> None: ...


class NullUsageSink:
    """Drops every record (the CLI smoke run and tests that do not look at usage)."""

    async def record(self, rec: LlmCallRecord) -> None:
        return None


class MemoryUsageSink:
    """Keeps records in a list, in arrival order."""

    def __init__(self) -> None:
        self.records: list[LlmCallRecord] = []

    async def record(self, rec: LlmCallRecord) -> None:
        self.records.append(rec)

    @property
    def total_cost_micro_usd(self) -> int:
        return sum(r.cost_micro_usd for r in self.records)

    def of_purpose(self, purpose: str) -> list[LlmCallRecord]:
        return [r for r in self.records if r.purpose == purpose]


def _get(obj: Any, name: str) -> Any:
    """``obj.name`` for objects, ``obj[name]`` for mappings, None when missing."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _count(obj: Any, name: str) -> int:
    value = _get(obj, name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(int(value), 0)


def usage_from_message(message: Any, model: str) -> Usage:
    """Build a `Usage` for one finished response.

    Reads ``message.usage``: ``input_tokens``, ``output_tokens``, ``cache_creation_input_tokens``,
    ``cache_read_input_tokens`` and ``server_tool_use.web_search_requests`` (web fetch is free and is not
    counted). The request id is the SDK's ``message._request_id`` (or a ``request_id`` key on a dict).
    Cache writes are priced at the 1-hour rate for the part ``usage.cache_creation`` reports as 1-hour.
    ``model`` is the id the call asked for; the cost is priced with `spec_for(model)`.
    """
    usage = _get(message, "usage")
    tokens = RawTokens(
        input_tokens=_count(usage, "input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        cache_write_tokens=_count(usage, "cache_creation_input_tokens"),
        cache_read_tokens=_count(usage, "cache_read_input_tokens"),
        web_searches=_count(_get(usage, "server_tool_use"), "web_search_requests"),
    )
    one_hour = _count(_get(usage, "cache_creation"), "ephemeral_1h_input_tokens")
    request_id = _get(message, "_request_id") or _get(message, "request_id")
    return Usage(
        model=model,
        input_tokens=tokens.input_tokens,
        output_tokens=tokens.output_tokens,
        cache_write_tokens=tokens.cache_write_tokens,
        cache_read_tokens=tokens.cache_read_tokens,
        web_searches=tokens.web_searches,
        request_id=str(request_id) if request_id else None,
        cost_micro_usd=claude_cost_micro(spec_for(model), tokens, cache_1h_tokens=one_hour),
    )


__all__ = ["MemoryUsageSink", "NullUsageSink", "UsageSink", "usage_from_message"]
