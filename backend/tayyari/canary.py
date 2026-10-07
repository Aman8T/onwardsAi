"""Temporary Wave 0c canary routes, mounted on the legacy app. Deleted in Wave 4.

They answer the facts the new backend depends on but the FastAPI Cloud docs leave open:
- /api/_diag/env     the build installed the new dependencies, and whether the project is installed as a package
- /api/_diag/ip      what the proxy forwards (X-Forwarded-For hops) next to the socket peer
- /api/_diag/db      DATABASE_URL normalisation and a real connection (asyncpg on Neon, or the SQLite fallback)
- /api/_diag/stream  whether NDJSON lines reach the client one by one through the proxy

Nothing here reveals secrets: only variable names, masked URLs and the caller's own request headers.
"""

from __future__ import annotations

import asyncio
import importlib.metadata as md
import json
import os
import platform
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/_diag")


def _version(dist: str) -> str | None:
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return None


@router.get("/env")
async def env() -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "project_installed": _version("tayyari-backend") is not None,
        "versions": {
            name: _version(name)
            for name in (
                "fastapi",
                "anthropic",
                "sqlalchemy",
                "asyncpg",
                "aiosqlite",
                "alembic",
                "structlog",
                "pydantic-settings",
            )
        },
        "env_names": sorted(
            k
            for k in os.environ
            if k.startswith(("FASTAPI", "TAYYARI", "DATABASE", "LOGFIRE", "JEV", "ANTHROPIC"))
        ),
    }


@router.get("/ip")
async def ip(request: Request) -> dict[str, Any]:
    h = request.headers
    return {
        "peer": request.client.host if request.client else None,
        "x_forwarded_for": h.get("x-forwarded-for"),
        "x_real_ip": h.get("x-real-ip"),
        "forwarded": h.get("forwarded"),
        "x_forwarded_proto": h.get("x-forwarded-proto"),
        "via": h.get("via"),
        "header_names": sorted(h.keys()),
    }


@router.get("/db")
async def db() -> dict[str, Any]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from .core.settings import get_settings

    cfg = get_settings().db
    out: dict[str, Any] = {"kind": cfg.kind, "ephemeral": cfg.ephemeral, "url": cfg.safe_url}
    started = time.perf_counter()
    if cfg.kind == "sqlite" and not cfg.in_memory:
        from pathlib import Path

        from sqlalchemy.engine import make_url

        Path(make_url(cfg.url).database or ".").parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(cfg.url, connect_args=cfg.connect_args, **cfg.engine_kwargs)
    try:
        async with engine.connect() as conn:
            query = "select version()" if cfg.kind == "postgres" else "select sqlite_version()"
            out["server_version"] = (await conn.execute(text(query))).scalar()
        out["ok"] = True
    except Exception as exc:  # report, never raise: this is a probe
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        await engine.dispose()
    out["ms"] = round((time.perf_counter() - started) * 1000)
    return out


@router.get("/stream")
async def stream(n: int = 6, gap: float = 1.0) -> StreamingResponse:
    n = max(1, min(n, 20))
    gap = max(0.1, min(gap, 3.0))

    async def body() -> AsyncIterator[bytes]:
        start = time.time()
        for i in range(n):
            yield (json.dumps({"i": i, "t": round(time.time() - start, 3)}) + "\n").encode()
            await asyncio.sleep(gap)

    return StreamingResponse(
        body(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def mount(app: FastAPI) -> None:
    """Put the canary routes ahead of the legacy /api/* catch-all and the SPA fallback."""
    for route in reversed(router.routes):
        app.router.routes.insert(0, route)
