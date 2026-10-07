"""Process-wide configuration: model ids, credentials and the folders the app reads and writes.

Everything is read once, at import time, from the environment (the variables are listed in DEPLOY.md).
An empty variable counts as unset.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


MODELS: dict[str, str] = {
    "reader": _env("TAYYARI_READER_MODEL") or "claude-haiku-4-5",
    "synth": _env("TAYYARI_SYNTH_MODEL") or "claude-sonnet-5-5",
    "chat": _env("TAYYARI_CHAT_MODEL") or "claude-sonnet-5-5",
}
"""Model ids per role; the `models` field of /api/health and of every Profile."""

API_KEY: str | None = _env("ANTHROPIC_API_KEY")
"""Anthropic API key. Analysis and chat fail with a clear message when it is missing."""

GITHUB_TOKEN: str | None = _env("GITHUB_TOKEN")
"""Optional token that raises the GitHub API rate limit for the GitHub ingest."""

PKG_DIR: Path = Path(__file__).resolve().parent
SAMPLE_DIR: Path = PKG_DIR / "sample"
"""The bundled sample persona: fixture sources plus the pre-computed profile and chat context."""
STATIC_DIR: Path = PKG_DIR.parent / "static"
"""The built web app (filled by cloud/build-static.mjs); served with an SPA fallback when present."""
DATA_DIR: Path = Path(_env("TAYYARI_DATA_DIR") or tempfile.gettempdir()) / "tayyari"
"""Best-effort on-disk cache of analysed profiles (the filesystem is ephemeral in the cloud)."""

READER_THINKING: bool = _flag("TAYYARI_READER_THINKING")
"""Extended thinking for the Haiku readers; off by default because they extract rather than reason."""
