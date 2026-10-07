"""Well-known folders, derived from this file's location (no environment reads, no I/O).

Layout::

    <REPO_ROOT>/               the git repo (frontend, shared/, backend/ ...)
      backend/                 BACKEND_DIR: the FastAPI Cloud deploy root (main.py, pyproject.toml, static/)
        tayyari/               PKG_DIR: the flat, importable package
          sample/              SAMPLE_DIR: the bundled sample persona
        static/                STATIC_DIR: the built web app (filled by build-static.mjs)
"""

from __future__ import annotations

from pathlib import Path

PKG_DIR: Path = Path(__file__).resolve().parent.parent
BACKEND_DIR: Path = PKG_DIR.parent
REPO_ROOT: Path = BACKEND_DIR.parent
STATIC_DIR: Path = BACKEND_DIR / "static"
SAMPLE_DIR: Path = PKG_DIR / "sample"

__all__ = ["BACKEND_DIR", "PKG_DIR", "REPO_ROOT", "SAMPLE_DIR", "STATIC_DIR"]
