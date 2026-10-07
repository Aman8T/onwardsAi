"""FastAPI Cloud entrypoint: exposes the Tayyari ASGI app as ``main:app``.

Deploy with ``fastapi deploy`` (see DEPLOY.md). For local use run ``python main.py`` from
this folder; the port comes from the PORT environment variable (default 8787, the same
port the Vite dev proxy targets).
"""

import os

from tayyari import canary
from tayyari.legacy.server import app

canary.mount(app)  # temporary Wave 0c diagnostics; removed when main.py flips to the new app

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("tayyari.legacy.server:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8787")))
