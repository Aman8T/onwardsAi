"""The Leafout Weekly: a personal newspaper, built every week from the reader's own graph.

    GET  /api/weekly/subscription           `WeeklySubscription` (signed in; ``status: "off"`` when never subscribed)
    PUT  /api/weekly/subscription           `WeeklySubscriptionUpdate` -> `WeeklySubscription`
    GET  /api/weekly/editions               `WeeklyEditionList`, newest first
    GET  /api/weekly/editions/{id}          `WeeklyEdition` (the reader's own; opening it marks it read)
    POST /api/weekly/editions/preview       `WeeklyEdition`: this week's edition built now, or the one already built
    GET  /api/weekly/unsubscribe?token=     a small HTML page with one button (no sign-in; the token is the credential)
    POST /api/weekly/unsubscribe?token=     does it (RFC 8058 one-click, and the button); a small HTML page

The first five need a sign-in (401 without) and answer only for the account's own editions; another reader's edition id is the
404 an unknown one gets, and so is an id that is not 32 hexadecimal characters. Every answer is ``Cache-Control: no-store``.

Building on demand, in this order: ``shed`` (in-memory, before anything is read) -> sign-in -> an edition already built for the
week is returned at once, counting nothing -> the client's counters in the ``weekly`` window and the account's (429 with
``Retry-After``) -> the build. The paid part of a build (the lead's read, the news) reserves ``est_weekly_usd`` itself and is
left out, never failed, when the daily ceiling is reached. Nothing is charged to an account.

The unsubscribe token travels in the query string because mail clients post to the exact address in ``List-Unsubscribe``; the
telemetry leaves the path out (`api.app._skip_telemetry`) and the log redaction masks ``token=``. The GET only shows a button, so
a mail scanner that opens the link unsubscribes nobody.
"""

from __future__ import annotations

import re
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import Response

from tayyari.api.deps import get_request_context, require_user, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.security import subnet_hash
from tayyari.container import Container
from tayyari.contract.requests import RequestContext
from tayyari.contract.weekly import WeeklySubscriptionUpdate
from tayyari.core.errors import NotFound
from tayyari.weekly.render import unknown_link_page, unsubscribe_confirm_page, unsubscribed_page

router = APIRouter()

NO_STORE: Final = {"Cache-Control": "no-store"}
PAGE_HEADERS: Final = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
BODY_MAX_BYTES: Final = 2 * 1024
_HEX_ID = re.compile(r"[0-9a-f]{32}")


def _json(data: dict[str, object]) -> JSONResponse:
    return JSONResponse(data, headers=NO_STORE)


async def _shed(request: Request, container: Container, rc: RequestContext, *, build: bool = False) -> None:
    """The light in-memory bucket (``chat``, or ``weekly`` for a build), before anything is read."""
    container.rate_limiter.shed(
        "weekly" if build else "chat",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        exempt=await unlimited(request, container),
    )


@router.get("/api/weekly/subscription")
async def get_subscription(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc)
    user = await require_user(request)
    return _json((await container.weekly.subscription(user.id)).to_dict())


@router.put("/api/weekly/subscription")
async def put_subscription(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc)
    user = await require_user(request)
    body = await read_model(request, container, WeeklySubscriptionUpdate, max_bytes=BODY_MAX_BYTES)
    return _json((await container.weekly.update(user.id, body)).to_dict())


@router.get("/api/weekly/editions")
async def list_editions(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc)
    user = await require_user(request)
    return _json((await container.weekly.editions(user.id)).to_dict())


@router.get("/api/weekly/editions/{edition_id}")
async def get_edition(edition_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc)
    user = await require_user(request)
    if not _HEX_ID.fullmatch(edition_id):
        raise NotFound("That edition was not found.")
    return _json((await container.weekly.edition(user.id, edition_id)).to_dict())


@router.post("/api/weekly/editions/preview")
async def preview_edition(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc, build=True)
    user = await require_user(request)
    service = container.weekly
    if await service.current(user.id) is None:
        limiter = container.rate_limiter
        free = await unlimited(request, container)
        await limiter.check(
            "weekly",
            rc.ip_hash,
            ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
            memory=False,
            exempt=free,
        )
        await limiter.check("weekly", f"u:{user.id}", memory=False, globally=False, exempt=free)
    return _json((await service.preview(user.id)).to_dict())


# ───────────────────────── unsubscribe: no sign-in, the token is the credential ─────────────────────────


def _page(html: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(html, status_code=status, headers=PAGE_HEADERS)


@router.get("/api/weekly/unsubscribe")
async def unsubscribe_page(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc)
    token = request.query_params.get("token", "")
    if not await container.weekly.token_known(token):
        return _page(unknown_link_page(container.weekly.app_url), 404)
    return _page(unsubscribe_confirm_page(token))


@router.post("/api/weekly/unsubscribe")
async def unsubscribe(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc)
    token = request.query_params.get("token", "")
    if not await container.weekly.unsubscribe(token):
        return _page(unknown_link_page(container.weekly.app_url), 404)
    return _page(unsubscribed_page(container.weekly.app_url))


__all__ = ["router"]
