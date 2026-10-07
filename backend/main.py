"""FastAPI Cloud entry point: exposes the Tayyari ASGI app as ``main:app``.

FastAPI Cloud finds it through ``[tool.fastapi] entrypoint`` in pyproject.toml. For local work use
``uv run python -m tayyari.cli dev`` (reload) or ``... serve``; see DEPLOY.md.
"""

from tayyari.api.app import create_app

app = create_app()

__all__ = ["app"]
