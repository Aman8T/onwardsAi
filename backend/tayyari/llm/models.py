"""What each model can do and what it costs: the one place that knows prices and capabilities.

Requests are built from `ModelSpec` (no `effort` for Haiku, no thinking knobs for models that reject
them), and every `Usage.cost_micro_usd` comes from `claude_cost_micro` / `jev_cost_micro`, so the spend
ledger and the daily ceiling agree with the price list.

Prices are USD per million tokens (MTok). One USD/MTok is exactly one micro-USD per token, so a cost in
micro-USD is ``tokens * price`` with no unit conversion. Sums use `Decimal` and round once at the end
(half up), so ``0.1`` micro-USD per cached token never drifts.

Sources: Anthropic pricing page (verified 2026-10-07 by the orchestrator) for the price columns, and the
bundled Claude API docs (`shared/models.md`, `shared/prompt-caching.md`, `shared/model-migration.md`,
`shared/error-codes.md`) for capabilities. Anything the docs do not pin down is marked UNVERIFIED below.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Literal, Protocol

from ..core.logging import get_logger

log = get_logger(__name__)

ThinkingStyle = Literal["adaptive", "budget", "none"]
WebToolsStyle = Literal["dynamic", "basic"]
"""Which server web tools a model takes: the ``_20260209`` versions with dynamic filtering, or the basic ones."""
"""``adaptive``: thinking is on by default and steered by ``effort`` (a thinking budget or a disabled
thinking block is a 400 on the newest models). ``budget``: the older ``thinking={"type": "enabled",
"budget_tokens": N}`` style. ``none``: send no thinking parameter at all."""

WEB_SEARCH_MICRO_USD = 10_000
"""Anthropic bills $10 per 1,000 web searches on top of tokens (verified 2026-10-07): $0.01 each.
Web fetch has no extra charge."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Price list entry and capability flags of one Claude model."""

    id: str
    price_in: float  # USD per MTok, uncached input
    price_out: float  # USD per MTok, output (thinking tokens included)
    cache_write_5m: float  # USD per MTok, writing a 5-minute cache entry
    cache_write_1h: float  # USD per MTok, writing a 1-hour cache entry
    cache_read: float  # USD per MTok, reading a cache entry
    min_cache_tokens: int  # shortest prefix that gets cached; shorter ones silently do not
    thinking: ThinkingStyle
    supports_effort: bool  # accepts output_config.effort
    structured_output: bool  # accepts output_config.format json_schema
    web_tools: WebToolsStyle = "dynamic"  # web_search/web_fetch versions it accepts (chat declares them)
    thinking_can_disable: bool = False  # accepts thinking {"type": "disabled"} (Models API)
    # Prompts above `long_prompt_tokens` tokens pay `long_multiplier` x every token price (None: flat pricing).
    long_prompt_tokens: int | None = None
    long_multiplier: float = 1.0


MODEL_SPECS: Mapping[str, ModelSpec] = MappingProxyType(
    {
        spec.id: spec
        for spec in (
            ModelSpec(
                id="claude-haiku-4-5",
                price_in=1.0,
                price_out=5.0,
                cache_write_5m=1.25,
                cache_write_1h=2.0,
                cache_read=0.10,
                # shared/prompt-caching.md: Haiku 4.5 needs 4096 tokens, so the ~1k-token reader prompts never cache.
                min_cache_tokens=4096,
                # Extended thinking with budget_tokens (the pre-adaptive style). The pipeline sends none for readers.
                thinking="budget",
                # The orchestrator's audit (finding F2) and shared/model-migration.md say effort is rejected here
                # (the docs name `max` explicitly). UNVERIFIED for low/medium/high individually: we send none.
                supports_effort=False,
                # shared/tool-use-concepts.md lists Haiku 4.5 among the models with structured outputs.
                structured_output=True,
                # Dynamic filtering (the _20260209 web tools) is Opus/Sonnet 4.6+ and the 5.x family only
                # (shared/tool-use-concepts.md); Haiku 4.5 takes web_search_20250305 and web_fetch_20250910.
                web_tools="basic",
            ),
            ModelSpec(
                # Released 2026-10-07. Capabilities from the Models API (effort low..max, adaptive thinking, thinking
                # can be disabled, structured outputs, web search, code execution; 1M context, 128K output); prices and
                # the 512-token cache minimum from platform.claude.com/docs pricing + prompt caching (2026-10-08).
                id="claude-haiku-5-5",
                price_in=0.10,
                price_out=0.50,
                cache_write_5m=0.125,
                cache_write_1h=0.20,
                cache_read=0.01,
                min_cache_tokens=512,
                thinking="adaptive",
                # Models API: thinking.types.disabled.supported = true (Sonnet 5.5 rejects "disabled").
                thinking_can_disable=True,
                supports_effort=True,
                structured_output=True,
                # Dynamic filtering is "Claude 4.6 and later" (web search tool docs); Haiku 5.5 has code execution.
                web_tools="dynamic",
                # Priced by prompt length: over 100,000 tokens every rate is 5x ($0.50 in / $2.50 out ...).
                long_prompt_tokens=100_000,
                long_multiplier=5.0,
            ),
            ModelSpec(
                id="claude-sonnet-5-5",
                price_in=2.0,
                price_out=10.0,
                cache_write_5m=2.5,
                cache_write_1h=4.0,
                cache_read=0.20,
                # UNVERIFIED: shared/prompt-caching.md lists 512 for Sonnet 5.5 but tells readers to check the live
                # prompt caching docs before relying on it. The profile block is ~10k tokens, far above either value.
                min_cache_tokens=512,
                # Adaptive thinking is on by default, effort steers it; {"type": "disabled"} is a 400
                # (shared/error-codes.md) and thinking tokens count against max_tokens.
                thinking="adaptive",
                supports_effort=True,
                structured_output=True,
            ),
            ModelSpec(
                id="claude-opus-5-5",
                price_in=4.0,
                price_out=20.0,
                cache_write_5m=5.0,
                cache_write_1h=8.0,
                cache_read=0.20,
                min_cache_tokens=512,  # shared/prompt-caching.md
                thinking="adaptive",  # thinking is always on; effort is the only control (shared/models.md)
                supports_effort=True,
                structured_output=True,
            ),
            ModelSpec(
                id="claude-fable-5-1",
                price_in=10.0,
                price_out=50.0,
                cache_write_5m=12.5,
                cache_write_1h=20.0,
                cache_read=0.25,
                min_cache_tokens=512,  # shared/prompt-caching.md
                thinking="adaptive",  # thinking always on; an explicit {"type": "disabled"} is a 400
                supports_effort=True,
                structured_output=True,
            ),
        )
    }
)
"""Known models, by canonical alias. Dated ids and provider-prefixed ids resolve to these via `spec_for`."""


def _worst_case_spec() -> ModelSpec:
    """A spec that can never undercount: the highest price in every column, and only capabilities that
    cannot make a request fail (no `effort`, no thinking parameter, the largest cache minimum)."""
    specs = list(MODEL_SPECS.values())
    return ModelSpec(
        id="unknown",
        price_in=max(s.price_in for s in specs),
        price_out=max(s.price_out for s in specs),
        cache_write_5m=max(s.cache_write_5m for s in specs),
        cache_write_1h=max(s.cache_write_1h for s in specs),
        cache_read=max(s.cache_read for s in specs),
        min_cache_tokens=max(s.min_cache_tokens for s in specs),
        thinking="none",
        supports_effort=False,
        structured_output=True,
        web_tools="basic",
    )


_WORST_CASE = _worst_case_spec()
_ID_SEPARATORS = "-@"
_warned: set[str] = set()


def _canonical(model_id: str) -> str:
    """Lower-case the id and drop a provider prefix (``anthropic.``, ``us.anthropic.``, ``vertex/``)."""
    text = model_id.strip().lower()
    start = text.find("claude-")
    return text[start:] if start > 0 else text


def _known_spec(model_id: str) -> ModelSpec | None:
    text = _canonical(model_id)
    exact = MODEL_SPECS.get(text)
    if exact is not None:
        return exact
    # Longest known id that is followed by a separator: "claude-haiku-4-5-20251001" and
    # "claude-haiku-4-5@20251001" are Haiku 4.5, but "claude-sonnet-5-50" is not Sonnet 5.5.
    for spec_id in sorted(MODEL_SPECS, key=len, reverse=True):
        if text.startswith(spec_id) and text[len(spec_id)] in _ID_SEPARATORS:
            return MODEL_SPECS[spec_id]
    return None


def is_known_model(model_id: str) -> bool:
    """True when `spec_for` can price the id from the table (an alias, a dated id or a provider-prefixed id)."""
    return _known_spec(model_id) is not None


def spec_for(model_id: str) -> ModelSpec:
    """The spec for ``model_id``.

    Aliases, dated ids (``claude-haiku-4-5-20251001``) and provider-prefixed ids resolve by prefix. An
    unknown id is priced at the most expensive known rate in every column, so spend is never undercounted,
    and a warning is logged once per id. Its capabilities are the conservative ones (no effort, no
    thinking parameter), because a request using a capability the model lacks fails outright, while one
    that omits it merely runs at the default. The returned spec carries the requested id.
    """
    known = _known_spec(model_id)
    if known is not None:
        return known
    if model_id not in _warned:
        _warned.add(model_id)
        log.warning("unknown_model_priced_at_maximum", model=model_id)
    return replace(_WORST_CASE, id=model_id)


class TokenCounts(Protocol):
    """Anything with the five billable counters: `Usage` and `RawTokens` both qualify."""

    @property
    def input_tokens(self) -> int: ...
    @property
    def output_tokens(self) -> int: ...
    @property
    def cache_write_tokens(self) -> int: ...
    @property
    def cache_read_tokens(self) -> int: ...
    @property
    def web_searches(self) -> int: ...


@dataclass(frozen=True, slots=True)
class RawTokens:
    """The billable counters before a `Usage` exists (a `Usage` carries its own cost, so it cannot be built first)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    web_searches: int = 0


def _micro(tokens: int, usd_per_mtok: float) -> Decimal:
    return Decimal(max(tokens, 0)) * Decimal(str(usd_per_mtok))


def _round_micro(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def claude_cost_micro(
    spec: ModelSpec,
    usage: TokenCounts,
    *,
    cache_ttl: Literal["5m", "1h"] = "5m",
    cache_1h_tokens: int | None = None,
) -> int:
    """Cost of one request in micro-USD.

    Cache writes are priced at the 5-minute rate unless told otherwise: ``cache_ttl="1h"`` prices them
    all at the 1-hour rate, and ``cache_1h_tokens`` splits a mixed write (the API reports the split in
    ``usage.cache_creation``). Each web search adds `WEB_SEARCH_MICRO_USD`. Negative counters count as 0.
    A model priced by prompt length (`ModelSpec.long_prompt_tokens`) pays `long_multiplier` x every token rate
    when the prompt (input + cache writes + cache reads) is over its threshold.
    """
    writes = max(usage.cache_write_tokens, 0)
    if cache_1h_tokens is None:
        writes_1h = writes if cache_ttl == "1h" else 0
    else:
        writes_1h = min(max(cache_1h_tokens, 0), writes)
    prompt = max(usage.input_tokens, 0) + writes + max(usage.cache_read_tokens, 0)
    k = (
        spec.long_multiplier
        if spec.long_prompt_tokens is not None and prompt > spec.long_prompt_tokens
        else 1.0
    )
    total = (
        _micro(usage.input_tokens, spec.price_in * k)
        + _micro(usage.output_tokens, spec.price_out * k)
        + _micro(writes - writes_1h, spec.cache_write_5m * k)
        + _micro(writes_1h, spec.cache_write_1h * k)
        + _micro(usage.cache_read_tokens, spec.cache_read * k)
        + Decimal(max(usage.web_searches, 0) * WEB_SEARCH_MICRO_USD)
    )
    return _round_micro(total)


JEV_PRICES: Mapping[str, float] = MappingProxyType({"jev-1.13.0": 0.042})
"""USD per MTok of input for each Jev model. Output tokens are free. Verified against the TypeSafe docs."""


def jev_price_per_mtok(model: str) -> float:
    """The input price of a Jev model; an unknown id (``jev-latest``, a newer pin) costs the maximum known."""
    price = JEV_PRICES.get(model.strip().lower())
    return price if price is not None else max(JEV_PRICES.values())


def jev_cost_micro(model: str, input_tokens: int) -> int:
    """Cost of one Jev request in micro-USD: input tokens only, rounded half up."""
    return _round_micro(_micro(input_tokens, jev_price_per_mtok(model)))


__all__ = [
    "JEV_PRICES",
    "MODEL_SPECS",
    "WEB_SEARCH_MICRO_USD",
    "ModelSpec",
    "RawTokens",
    "ThinkingStyle",
    "TokenCounts",
    "claude_cost_micro",
    "is_known_model",
    "jev_cost_micro",
    "jev_price_per_mtok",
    "spec_for",
]
