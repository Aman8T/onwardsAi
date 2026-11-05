"""The AI fallback: a `BrowserAgent` fills a form no playbook can (adapters arrive in auto-apply step 6)."""

from __future__ import annotations

from typing import Any


def build_fallback(settings: Any, container: Any) -> Any:
    """The fallback for the engine, or None while no adapter is available."""
    return None


__all__ = ["build_fallback"]
