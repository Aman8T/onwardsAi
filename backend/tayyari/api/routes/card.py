"""The radar card (R1): the owner's share sheet, and the card's three public addresses.

The owner (JSON, ``Cache-Control: no-store``; the light routes' ``chat`` bucket is shed first; no model is called):

    GET  /api/card?profileId=      `CardView`; ``blocked`` for the shared sample or a graph no account owns
    PUT  /api/card                 `CardUpdate` -> `CardView` (turning sharing on makes a link, off removes it;
                                   400 for a skill the sheet did not offer or a role not on the graph; 403 when blocked)
    POST /api/card/new-link        `CardNewLink` -> `CardView` (a new link; the old one stops at once)

A profile is opened as everywhere else (`api.deps.require_profile_access`), then `CardService` checks that the caller's
account owns it: another account's profile is the same 404 as an unknown id.

Public, no account, counted per address in the ``card`` window (``shed`` before the link is read, then the shared
counters). The random slug is the only key and the real defence against walking through cards:

    GET /r/{slug}.svg?theme=&size=&compare=   the README card, ``image/svg+xml``. ``theme`` (light, dark, transparent) and
                                              ``size`` (md, sm) override the stored theme (``match`` draws light) and md;
                                              ``compare`` draws the role's outline only when it names the stored role.
                                              An unknown, stopped or malformed link is a 404 whose body is the neutral card
                                              ("This card isn't shared any more", five minutes' cache); a failure is a 200
                                              "can't be drawn right now" card (ten minutes).
    GET /og/{slug}-{version}.png              the 1200 x 627 LinkedIn picture; an old version is a 301 to the current one,
                                              an unknown link a 404 with the neutral picture.
    GET /u/{slug}                             the card's page (server-rendered, no script, ``noindex``); an unknown link is
                                              a plain 404 page.

The images are ``public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400`` with an ``ETag`` from the version and
the drawing's options (a matching ``If-None-Match`` is a 304), ``X-Content-Type-Options: nosniff`` and ``X-Robots-Tag: noindex`` (the images are for embedding, never
for a search result), and the SVG carries its
own ``Content-Security-Policy`` (the security middleware keeps a route's header). These paths are registered with the API
routes, so they are answered before the web app's catch-all and are none of its `APP_ROUTES`.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

from tayyari.api.deps import get_current_user, get_request_context, require_profile_access, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.security import subnet_hash
from tayyari.card.page import PAGE_CSP, card_page, missing_page
from tayyari.card.service import CardProfileNotFound
from tayyari.card.svg import Notice, Size, Theme, card_svg, notice_svg
from tayyari.container import Container
from tayyari.contract.card import SLUG_LEN, CardNewLink, CardUpdate
from tayyari.contract.requests import RequestContext
from tayyari.core.ids import is_valid_id
from tayyari.core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)

NO_STORE: Final = {"Cache-Control": "no-store"}
BODY_MAX_BYTES: Final = 4 * 1024
"""A card update holds a profile id, three skill ids, a role id and a theme."""
IMAGE_CACHE: Final = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
STOPPED_CACHE: Final = "public, max-age=300"
ERROR_CACHE: Final = "public, max-age=600"
PAGE_CACHE: Final = "public, max-age=300"
SVG_CSP: Final = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
"""What GitHub's image proxy sends with an SVG, so the card is held to the same rules wherever it is opened."""
SVG_TYPE: Final = "image/svg+xml; charset=utf-8"
_SVG_NAME = re.compile(rf"([a-z0-9]{{{SLUG_LEN}}})\.svg")
_PNG_NAME = re.compile(rf"([a-z0-9]{{{SLUG_LEN}}})-([0-9a-f]{{8}})\.png")
_SLUG = re.compile(rf"[a-z0-9]{{{SLUG_LEN}}}")
_THEMES: Final[frozenset[str]] = frozenset({"light", "dark", "transparent"})


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
        raise CardProfileNotFound()
    await require_profile_access(request, profile_id, not_found=CardProfileNotFound.user_message)
    user = await get_current_user(request)
    return user.id if user is not None else None


@router.get("/api/card")
async def get_card(request: Request) -> JSONResponse:
    container = get_container(request)
    await _shed_light(request, container, await get_request_context(request))
    profile_id = request.query_params.get("profileId", "")
    user_id = await _owner(request, profile_id)
    view = await container.card.view(user_id, profile_id)
    return JSONResponse(view.to_dict(), headers=NO_STORE)


@router.put("/api/card")
async def put_card(request: Request) -> JSONResponse:
    container = get_container(request)
    await _shed_light(request, container, await get_request_context(request))
    body = await read_model(request, container, CardUpdate, max_bytes=BODY_MAX_BYTES)
    user_id = await _owner(request, body.profileId)
    view = await container.card.update(user_id, body)
    return JSONResponse(view.to_dict(), headers=NO_STORE)


@router.post("/api/card/new-link")
async def new_link(request: Request) -> JSONResponse:
    container = get_container(request)
    await _shed_light(request, container, await get_request_context(request))
    body = await read_model(request, container, CardNewLink, max_bytes=BODY_MAX_BYTES)
    user_id = await _owner(request, body.profileId)
    view = await container.card.new_link(user_id, body)
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


def _svg(body: str, cache: str, etag: str | None = None, status: int = 200) -> Response:
    headers = _image_headers(cache, etag)
    headers["Content-Security-Policy"] = SVG_CSP
    return Response(body, status_code=status, media_type=SVG_TYPE, headers=headers)


def _notice(kind: Notice, theme: Theme) -> Response:
    """The neutral card: a 404 (with the card's own picture as its body) for an unknown or stopped link, a 200 for a failure."""
    if kind == "stopped":
        return _svg(notice_svg(kind, theme), STOPPED_CACHE, status=404)
    return _svg(notice_svg(kind, theme), ERROR_CACHE)


@router.api_route("/r/{name}", methods=["GET", "HEAD"], include_in_schema=False)
async def card_image(name: str, request: Request) -> Response:
    container = get_container(request)
    await _count(request, container)
    asked = request.query_params.get("theme", "")
    fallback: Theme = asked if asked in _THEMES else "light"  # type: ignore[assignment]
    found = _SVG_NAME.fullmatch(name)
    if found is None:
        return _notice("stopped", fallback)
    try:
        stored = await container.card.shared(found.group(1))
        if stored is None:
            return _notice("stopped", fallback)
        stored_theme = stored.theme if stored.theme in _THEMES else "light"
        theme: Theme = asked if asked in _THEMES else stored_theme  # type: ignore[assignment]
        size: Size = "sm" if request.query_params.get("size") == "sm" else "md"
        wanted = request.query_params.get("compare")
        compare = wanted is not None and stored.role_id is not None and wanted == stored.role_id
        etag = f'"{stored.version}-{theme}-{size}-{"c" if compare else "n"}"'
        if _matches(request, etag):
            return Response(status_code=304, headers=_image_headers(IMAGE_CACHE, etag))
        body = card_svg(container.card.content(stored), theme, size, compare=compare)
        return _svg(body, IMAGE_CACHE, etag)
    except Exception:
        log.error("card_svg_failed", exc_info=True)
        return _notice("error", fallback)


@router.api_route("/og/{name}", methods=["GET", "HEAD"], include_in_schema=False)
async def card_picture(name: str, request: Request) -> Response:
    container = get_container(request)
    await _count(request, container)
    found = _PNG_NAME.fullmatch(name)
    stored = await container.card.shared(found.group(1)) if found is not None else None
    if found is None or stored is None:
        return await _picture_notice(container, "stopped")
    slug, version = found.group(1), found.group(2)
    if version != stored.version:
        return RedirectResponse(f"/og/{slug}-{stored.version}.png", status_code=301)
    etag = f'"{stored.version}"'
    if _matches(request, etag):
        return Response(status_code=304, headers=_image_headers(IMAGE_CACHE, etag))
    try:
        png = await container.card_pictures.picture(slug, version, container.card.content(stored))
    except Exception:
        log.error("card_png_failed", exc_info=True)
        return await _picture_notice(container, "error")
    return Response(png, media_type="image/png", headers=_image_headers(IMAGE_CACHE, etag))


async def _picture_notice(container: Container, kind: Literal["stopped", "error"]) -> Response:
    png = await container.card_pictures.notice(kind)
    return Response(
        png,
        status_code=404 if kind == "stopped" else 200,
        media_type="image/png",
        headers=_image_headers(STOPPED_CACHE if kind == "stopped" else ERROR_CACHE),
    )


@router.api_route("/u/{slug}", methods=["GET", "HEAD"], include_in_schema=False)
async def card_page_route(slug: str, request: Request) -> Response:
    container = get_container(request)
    await _count(request, container)
    stored = await container.card.shared(slug) if _SLUG.fullmatch(slug) else None
    headers = {"Cache-Control": PAGE_CACHE, "X-Robots-Tag": "noindex", "Content-Security-Policy": PAGE_CSP}
    if stored is None:
        return HTMLResponse(missing_page(), status_code=404, headers=headers)
    html = card_page(
        stored,
        container.card.content(stored),
        public_url=container.settings.auth.public_url,
        report_email=container.settings.card.report_email,
    )
    return HTMLResponse(html, headers=headers)


__all__ = ["IMAGE_CACHE", "SVG_CSP", "router"]
