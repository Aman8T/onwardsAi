"""The FastAPI app factory: ``create_app()`` is what ``main.py`` and ``uvicorn --factory`` run.

    uvicorn tayyari.api.app:create_app --factory

Order matters in `create_app`: the error handlers and the middlewares first, then the API routes, and
`mount_spa` last because its catch-all routes would shadow anything registered after them. The middlewares,
outermost first: the request id, the security headers (so a refusal below carries them too), the
cross-site guard, and the session cookie (which adds ``Set-Cookie`` when a request signed someone in or out). The docs pages are off: this is a private API for the bundled web app.

FastAPI's own OpenTelemetry (`TELEMETRY`) records request traces and metrics; on FastAPI Cloud the metrics reach
its Metrics dashboard with no setup, and elsewhere nothing is exported until `FASTAPI_OTEL_AUTO_CONFIGURE=true`
and an `OTEL_EXPORTER_OTLP_ENDPOINT` are set.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from fastapi import FastAPI
from fastapi.telemetry import TelemetryConfig

import tayyari
from tayyari.api.deps import RequestIdMiddleware
from tayyari.api.errors import install_error_handlers
from tayyari.api.lifespan import build_lifespan
from tayyari.api.routes import api_router
from tayyari.api.security import CrossSiteGuard, SecurityHeadersMiddleware
from tayyari.api.session_cookie import SessionCookieMiddleware
from tayyari.api.static import mount_spa
from tayyari.container import ContainerOverrides
from tayyari.core.paths import STATIC_DIR
from tayyari.core.settings import Settings, get_settings

# Sign-in links and callbacks carry a one-time secret in the query string (so does The Leafout Weekly's unsubscribe link), which a span records as `url.query`.
_SECRET_IN_QUERY = frozenset(
    {"/api/auth/magic/verify", "/api/auth/google/callback", "/api/weekly/unsubscribe"}
)
# A hand-off's id is a one-time secret in the path of its GET (and in the query of the redirect, which is outside /api/).
_HANDOFF_GET_PREFIX = "/api/handoff/"


def _skip_telemetry(scope: MutableMapping[str, Any]) -> bool:
    """Skip the health probes and the web app's files (so counts and error rates are the API's) and sign-in links."""
    path = str(scope.get("path", ""))
    return (
        not path.startswith("/api/")
        or path.startswith("/api/health")
        or path in _SECRET_IN_QUERY
        or path.startswith(_HANDOFF_GET_PREFIX)
    )


TELEMETRY: TelemetryConfig = {
    # FastAPI's logs carry exception messages and stack traces past `core.redact`; unhandled errors are already
    # logged, redacted, by `api.errors`.
    "logs": False,
    "exclude": _skip_telemetry,
}


def create_app(settings: Settings | None = None, *, overrides: ContainerOverrides | None = None) -> FastAPI:
    """Build the app. ``settings`` defaults to the process-wide `get_settings()`; ``overrides`` is for tests.

    Nothing heavy happens here: the database, the clients and the services are built by the lifespan, when
    the server starts.
    """
    resolved = settings if settings is not None else get_settings()
    app = FastAPI(
        title="Tayyari API",
        version=tayyari.__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=build_lifespan(resolved, overrides, STATIC_DIR),
        telemetry=TELEMETRY,
    )
    app.state.settings = resolved
    install_error_handlers(app)
    # Starlette makes the last middleware added the outermost one.
    app.add_middleware(SessionCookieMiddleware)
    app.add_middleware(CrossSiteGuard)
    app.add_middleware(SecurityHeadersMiddleware, index_html=STATIC_DIR / "index.html")
    app.add_middleware(RequestIdMiddleware)
    app.include_router(api_router)
    mount_spa(app, STATIC_DIR)
    return app


__all__ = ["create_app"]
