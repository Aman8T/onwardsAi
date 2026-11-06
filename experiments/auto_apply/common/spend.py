"""The spend ledger (`spend.json`) and the hard caps. Every run calls `check()` before it starts and `record()` after.

Browser Use: stop once cumulative spend reaches $0.80 (of the ~$1 credit). Anthropic: cap $2.00.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
LEDGER = HERE / "spend.json"

BU_STOP_USD = 0.80
ANTHROPIC_CAP_USD = 2.00

# USD per 1M tokens (backend/tayyari/llm/models.py): input, output, cache read, cache write (5 min)
SONNET_MODEL = "claude-sonnet-5-5"
HAIKU_MODEL = "claude-haiku-5-5"
PRICES = {SONNET_MODEL: (2.0, 10.0, 0.20, 2.5), HAIKU_MODEL: (0.10, 0.50, 0.01, 0.125)}
SONNET_IN, SONNET_OUT, SONNET_CACHE_READ, SONNET_CACHE_WRITE = PRICES[SONNET_MODEL]


class BudgetExceeded(RuntimeError):
    pass


def load() -> dict[str, Any]:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    return {"browser_use_usd": 0.0, "anthropic_usd": 0.0, "caps": {"browser_use_stop_usd": BU_STOP_USD,
            "anthropic_cap_usd": ANTHROPIC_CAP_USD}, "runs": []}  # fmt: skip


def check(bu_estimate: float, anthropic_estimate: float = 0.0) -> None:
    """Refuse a run whose estimate would cross a cap."""
    led = load()
    if led["browser_use_usd"] >= BU_STOP_USD:
        raise BudgetExceeded(f"Browser Use spend {led['browser_use_usd']:.4f} reached the ${BU_STOP_USD} stop line")
    if led["browser_use_usd"] + bu_estimate > BU_STOP_USD:
        raise BudgetExceeded(
            f"Browser Use spend {led['browser_use_usd']:.4f} + estimate {bu_estimate:.4f} would cross ${BU_STOP_USD}"
        )
    if led["anthropic_usd"] + anthropic_estimate > ANTHROPIC_CAP_USD:
        raise BudgetExceeded(
            f"Anthropic spend {led['anthropic_usd']:.4f} + estimate {anthropic_estimate:.4f} would cross ${ANTHROPIC_CAP_USD}"
        )


def anthropic_remaining() -> float:
    return ANTHROPIC_CAP_USD - load()["anthropic_usd"]


def record(entry: dict[str, Any]) -> dict[str, Any]:
    led = load()
    led["browser_use_usd"] = round(led["browser_use_usd"] + float(entry.get("browser_use_usd") or 0), 6)
    led["anthropic_usd"] = round(led["anthropic_usd"] + float(entry.get("anthropic_usd") or 0), 6)
    led["runs"].append({"at": datetime.now(UTC).isoformat(timespec="seconds"), **entry})
    LEDGER.write_text(json.dumps(led, indent=2), encoding="utf-8")
    return led


def model_cost(usage: Any, model: str = SONNET_MODEL) -> float:
    """USD for one Messages API response's usage, at the repo's price for `model`."""
    p_in, p_out, p_read, p_write = PRICES[model]
    get = (lambda k: getattr(usage, k, 0) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k) or 0)
    return (
        get("input_tokens") * p_in
        + get("output_tokens") * p_out
        + get("cache_read_input_tokens") * p_read
        + get("cache_creation_input_tokens") * p_write
    ) / 1_000_000


def sonnet_cost(usage: Any) -> float:
    return model_cost(usage, SONNET_MODEL)
