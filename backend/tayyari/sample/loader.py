"""Cached file readers for sample fixtures."""

from __future__ import annotations

from functools import cache
from pathlib import Path

__all__ = ["load_fixture"]


@cache
def load_fixture(name: str, directory: Path | None = None) -> str | None:
    """Load a fixture file by name, with caching.

    Args:
        name: The fixture filename (e.g., "sample-profile.json", "resume.md")
        directory: The directory containing fixtures. If not provided, uses the sample dir.

    Returns:
        The file contents as a string, normalized to LF line endings and stripped.
        Returns None if the file doesn't exist.
    """
    if directory is None:
        directory = Path(__file__).parent

    filepath = directory / name
    try:
        text = filepath.read_text(encoding="utf-8")
        # Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text.strip()
    except FileNotFoundError:
        return None
