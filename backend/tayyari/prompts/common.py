"""Common prompt helper functions."""

from __future__ import annotations

import json
from typing import Any


def clip(text: str, max_len: int) -> str:
    """Cut prompt text to `max_len` characters, marking the cut (never used for UI strings)."""
    if len(text) <= max_len:
        return text
    if max_len < 40:
        return text[: max(0, max_len)]
    return f"{text[: max_len - 20].rstrip()}\n…[trimmed]"


def compact_json(value: Any, max_len: int) -> str:
    """Render value as compact JSON, truncated to max_len with ellipsis."""
    try:
        s = _json(value if value is not None else {})
    except (TypeError, ValueError):
        return "{}"
    return f"{s[:max_len]}…" if len(s) > max_len else s


def escape_attr(s: str) -> str:
    """Escape a string for use as an XML/HTML attribute value."""
    return s.replace('"', "&quot;")


def _json(value: Any) -> str:
    """JSON.stringify equivalent: compact separators, non-ASCII kept as is."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _num(n: Any) -> str:
    """Render a number the way a JS template literal would (4 → "4", 4.0 → "4", 2.4 → "2.4")."""
    if isinstance(n, float) and n.is_integer():
        return str(int(n))
    return str(n)


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    """Remove None values from a dict."""
    return {k: v for k, v in d.items() if v is not None}
