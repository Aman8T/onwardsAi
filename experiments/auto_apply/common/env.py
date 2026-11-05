"""Load the repo's `.env` into the process environment without ever printing a value.

The app reads the Anthropic key as ``ANTHROPIC_API_KEY`` (`backend/tayyari/core/settings.py`), and the Browser Use
SDK reads ``BROWSER_USE_API_KEY``. Existing environment variables win over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
ENV_FILE = REPO / ".env"
NEEDED = ("ANTHROPIC_API_KEY", "BROWSER_USE_API_KEY")


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def presence() -> dict[str, bool]:
    """Which needed variables are set (True/False only)."""
    return {key: bool(os.environ.get(key)) for key in NEEDED}
