"""``dev``: the API with auto-reload, on 127.0.0.1 and the port the Vite proxy targets."""

from __future__ import annotations

import uvicorn

from tayyari.core.paths import PKG_DIR
from tayyari.core.settings import Settings, get_settings

APP = "tayyari.api.app:create_app"
"""The uvicorn application factory (also what ``serve`` runs)."""
HOST = "127.0.0.1"


def banner(settings: Settings, host: str, port: int, *, reload: bool) -> str:
    """One startup line: where it listens and which keys and storage are configured (never a secret's value)."""
    facts = settings.summary()
    keys = " ".join(
        f"{name}={'yes' if facts[key] else 'no'}"
        for name, key in (("anthropic", "anthropic_key"), ("jev", "jev_key"), ("github", "github_token"))
    )
    mode = "reload on" if reload else "reload off"
    return f"Tayyari API on http://{host}:{port} ({mode}, env={facts['env']}) | keys: {keys} | storage: {facts['database']}"


def run() -> int:
    """Run uvicorn with reload, watching the ``tayyari`` package. Returns 0 when the server is stopped."""
    settings = get_settings()
    print(banner(settings, HOST, settings.api_port, reload=True), flush=True)
    uvicorn.run(
        APP,
        factory=True,
        reload=True,
        reload_dirs=[str(PKG_DIR)],
        host=HOST,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
    return 0
