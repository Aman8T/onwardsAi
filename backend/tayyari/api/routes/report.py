"""Where can I go next (N1, beta): the owner's sheet, and the report's two public addresses.

The owner (JSON, ``Cache-Control: no-store``; the light routes' ``chat`` bucket is shed first; no model is called):

    GET  /api/report?profileId=      `ReportView`; ``blocked`` for the shared sample or a graph no account owns
    PUT  /api/report                 `ReportUpdate` -> `ReportView` (turning sharing on makes a link, off removes it;
                                     400 for a hidden line the report does not offer; 403 when blocked)
    POST /api/report/new-link        `ReportNewLink` -> `ReportView` (a new link; the old one stops at once)

A profile is opened as everywhere else (`api.deps.require_profile_access`), then `ReportService` checks that the caller's
account owns it: another account's profile is the same 404 as an unknown id.

Public, no account (outside ``/api``, so the sign-in gate does not apply), counted per address in the radar card's ``card``
window (``shed`` before the link is read). The random slug is the only key:

    GET /next/{slug}                          the report's page (server-rendered, no script, no inline style, ``noindex``);
                                              an unknown, stopped or malformed link is a plain 404 page
    GET /og/next/{slug}-{version}.png         the 1200 x 630 link preview; an old version is a 301 to the current one, an
                                              unknown link a 404 with a neutral picture

The preview is ``public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400`` with an ``ETag`` from the version (a
matching ``If-None-Match`` is a 304), ``nosniff`` and ``noindex``. These paths are registered with the API routes, so they are
answered before the web app's catch-all and are none of its `APP_ROUTES`.
"""

from __future__ import annotations

import re
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

from tayyari.api.deps import get_current_user, get_request_context, require_profile_access, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.security import subnet_hash
from tayyari.container import Container
from tayyari.contract.report import SLUG_LEN, ReportNewLink, ReportUpdate
from tayyari.contract.requests import RequestContext
from tayyari.core.ids import is_valid_id
from tayyari.core.logging import get_logger
from tayyari.report.page import PAGE_CSP, missing_page, report_page
from tayyari.report.service import ReportProfileNotFound

router = APIRouter()
log = get_logger(__name__)

NO_STORE: Final = {"Cache-Control": "no-store"}
BODY_MAX_BYTES: Final = 4 * 1024
"""An update holds a profile id, five switches and at most twelve short quote ids."""
IMAGE_CACHE: Final = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
STOPPED_CACHE: Final = "public, max-age=300"
ERROR_CACHE: Final = "public, max-age=600"
PAGE_CACHE: Final = "public, max-age=300"
_PNG_NAME = re.compile(rf"([a-z0-9]{{{SLUG_LEN}}})-([0-9a-f]{{8}})\.png")
_SLUG = re.compile(rf"[a-z0-9]{{{SLUG_LEN}}}")


# ───────────────────────── the owner's routes ─────────────────────────


async def _shed_light(request: Request, container: Container, rc: RequestContext) -> None:
    container.rate_limiter.shed(
        "chat",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        exempt=await unlimited(request, container),
    )


async def _owner(request: Request, profile_id: str) -> str | None:
    """The id's shape and who may open the profile (the 404 an unknown id gets), then the caller's account id or None."""
    if not is_valid_id(profile_id):
        raise ReportProfileNotFound()
    await require_profile_access(request, profile_id, not_found=ReportProfileNotFound.user_message)
    user = await get_current_user(request)
    return user.id if user is not None else None


@router.get("/api/report")
async def get_report(request: Request) -> JSONResponse:
    container = get_container(request)
    await _shed_light(request, container, await get_request_context(request))
    profile_id = request.query_params.get("profileId", "")
    user_id = await _owner(request, profile_id)
    view = await container.report.view(user_id, profile_id)
    return JSONResponse(view.to_dict(), headers=NO_STORE)


@router.put("/api/report")
async def put_report(request: Request) -> JSONResponse:
    container = get_container(request)
    await _shed_light(request, container, await get_request_context(request))
    body = await read_model(request, container, ReportUpdate, max_bytes=BODY_MAX_BYTES)
    user_id = await _owner(request, body.profileId)
    view = await container.report.update(user_id, body)
    return JSONResponse(view.to_dict(), headers=NO_STORE)


@router.post("/api/report/new-link")
async def new_link(request: Request) -> JSONResponse:
    container = get_container(request)
    await _shed_light(request, container, await get_request_context(request))
    body = await read_model(request, container, ReportNewLink, max_bytes=BODY_MAX_BYTES)
    user_id = await _owner(request, body.profileId)
    view = await container.report.new_link(user_id, body)
    return JSONResponse(view.to_dict(), headers=NO_STORE)


# ───────────────────────── the public addresses ─────────────────────────


async def _count(request: Request, container: Container) -> None:
    """Shed in memory, then count in the shared ``card`` windows, before anything about the link is read."""
    rc = await get_request_context(request)
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    container.rate_limiter.shed("card", rc.ip_hash, ip_hash_48=ip48)
    await container.rate_limiter.check("card", rc.ip_hash, ip_hash_48=ip48, memory=False)


def _image_headers(cache: str, etag: str | None = None) -> dict[str, str]:
    headers = {"Cache-Control": cache, "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex"}
    if etag is not None:
        headers["ETag"] = etag
    return headers


def _matches(request: Request, etag: str) -> bool:
    sent = request.headers.get("if-none-match", "")
    return any(part.strip() in (etag, f"W/{etag}") for part in sent.split(",")) or sent.strip() == "*"


@router.api_route("/og/next/{name}", methods=["GET", "HEAD"], include_in_schema=False)
async def report_picture(name: str, request: Request) -> Response:
    container = get_container(request)
    await _count(request, container)
    found = _PNG_NAME.fullmatch(name)
    stored = await container.report.shared(found.group(1)) if found is not None else None
    if found is None or stored is None:
        png = await container.report_pictures.notice()
        return Response(png, status_code=404, media_type="image/png", headers=_image_headers(STOPPED_CACHE))
    slug, version = found.group(1), found.group(2)
    if version != stored.version:
        return RedirectResponse(f"/og/next/{slug}-{stored.version}.png", status_code=301)
    etag = f'"{stored.version}"'
    if _matches(request, etag):
        return Response(status_code=304, headers=_image_headers(IMAGE_CACHE, etag))
    try:
        png = await container.report_pictures.picture(slug, version, container.report.content(stored))
    except Exception:
        log.error("report_png_failed", exc_info=True)
        png = await container.report_pictures.notice()
        return Response(png, media_type="image/png", headers=_image_headers(ERROR_CACHE))
    return Response(png, media_type="image/png", headers=_image_headers(IMAGE_CACHE, etag))


@router.api_route("/next/{slug}", methods=["GET", "HEAD"], include_in_schema=False)
async def report_page_route(slug: str, request: Request) -> Response:
    container = get_container(request)
    await _count(request, container)
    stored = await container.report.shared(slug) if _SLUG.fullmatch(slug) else None
    headers = {"Cache-Control": PAGE_CACHE, "X-Robots-Tag": "noindex", "Content-Security-Policy": PAGE_CSP}
    if stored is None:
        return HTMLResponse(missing_page(), status_code=404, headers=headers)
    html = report_page(
        stored,
        container.report.content(stored),
        public_url=container.settings.auth.public_url,
        report_email=container.settings.card.report_email,
        offer_url=container.report.offer_url,
    )
    return HTMLResponse(html, headers=headers)


__all__ = ["IMAGE_CACHE", "router"]
