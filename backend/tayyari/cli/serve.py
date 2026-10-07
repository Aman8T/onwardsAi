"""``serve``: the API without reload (one process, nothing watching the files)."""

from __future__ import annotations

import uvicorn

from tayyari.core.settings import get_settings

from .dev import APP, banner


def run(host: str = "127.0.0.1", port: int | None = None) -> int:
    """Run uvicorn on ``host``:``port`` (default port: ``TAYYARI_API_PORT``, else 8787). Returns 0 on a clean stop."""
    settings = get_settings()
    listen_port = port if port is not None else settings.api_port
    print(banner(settings, host, listen_port, reload=False), flush=True)
    uvicorn.run(
        APP,
        factory=True,
        host=host,
        port=listen_port,
        log_level=settings.log_level.lower(),
    )
    return 0
