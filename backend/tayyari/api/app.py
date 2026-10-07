"""The FastAPI app factory: ``create_app()`` is what ``main.py`` and ``uvicorn --factory`` run.

    uvicorn tayyari.api.app:create_app --factory

Order matters in `create_app`: the error handlers and the middlewares first, then the API routes, and
`mount_spa` last because its catch-all routes would shadow anything registered after them. The middlewares,
outermost first: the request id, the security headers (so a refusal below carries them too), and the
cross-site guard. The docs pages are off: this is a private API for the bundled web app.
"""

from __future__ import annotations

from fastapi import FastAPI

import tayyari
from tayyari.api.deps import RequestIdMiddleware
from tayyari.api.errors import install_error_handlers
from tayyari.api.lifespan import build_lifespan
from tayyari.api.routes import api_router
from tayyari.api.security import CrossSiteGuard, SecurityHeadersMiddleware
from tayyari.api.static import mount_spa
from tayyari.container import ContainerOverrides
from tayyari.core.paths import STATIC_DIR
from tayyari.core.settings import Settings, get_settings


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
    )
    app.state.settings = resolved
    install_error_handlers(app)
    # Starlette makes the last middleware added the outermost one.
    app.add_middleware(CrossSiteGuard)
    app.add_middleware(SecurityHeadersMiddleware, index_html=STATIC_DIR / "index.html")
    app.add_middleware(RequestIdMiddleware)
    app.include_router(api_router)
    mount_spa(app, STATIC_DIR)
    return app


__all__ = ["create_app"]
