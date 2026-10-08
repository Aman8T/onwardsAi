"""The portfolio routes: generate (an NDJSON stream), read, edit, rewrite one section, preview and the public page.

    POST  /api/portfolio                       JSON `PortfolioRequest` -> NDJSON `PortfolioEvent` stream
    GET   /api/portfolio/{profile_id}          the owner's `Portfolio` (404 until one is generated)
    PATCH /api/portfolio/{profile_id}          JSON `PortfolioPatch` -> the updated `Portfolio` (409 if it moved on)
    DELETE /api/portfolio/{profile_id}         remove the page and its public link -> 204 (also when there was none)
    POST  /api/portfolio/{profile_id}/rewrite  JSON ``{"slotId": ...}`` -> the `Portfolio` with that slot rewritten
    GET   /api/portfolio/{profile_id}/public-preview   the `PublicPortfolio` a visitor would get
    GET   /api/p/{public_id}                   the `PublicPortfolio` of a published page (404 otherwise)

Ids are random hex and the only thing protecting a page, so an id that does not have the id shape is answered
exactly like an unknown one (404), and a public id is a separate random id that never reveals the profile id.

Guards, in the order of `routes/chat.py`, all answered as JSON before a stream starts: ``shed`` (the in-memory
bucket, before the body is read), the JSON Content-Type (415), the body with its size cap and deadline (413 / 408),
validation (400), the id shape (404, before any counter is touched), the shared per-IP counters of the ``portfolio``
route (429) and the daily spend ceiling (503, the estimate stays reserved until the response is over). Handing back a
portfolio that already exists AND can be read back (`PortfolioService.has_readable`) costs nothing, so that path skips
the counters and the reservation; a stored document that no longer validates is rebuilt, and paid for. Reads, edits and
the delete call no model and only ``shed`` in the ``chat`` bucket, like `routes/profile.py`; a section rewrite pays like
a generation. State-changing methods are covered by the cross-site guard (`api.security.CrossSiteGuard`).
"""

from __future__ import annotations

from contextlib import AsyncExitStack

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import Response

from tayyari.api.deps import get_request_context
from tayyari.api.lifespan import get_container
from tayyari.api.routes.analyze import HeldResponse
from tayyari.api.security import subnet_hash
from tayyari.api.uploads import read_json_body, require_json_content_type
from tayyari.container import Container
from tayyari.contract.portfolio import PortfolioPatch, PortfolioRequest
from tayyari.contract.requests import RequestContext
from tayyari.core.errors import BadRequest, NotFound
from tayyari.core.ids import is_valid_id
from tayyari.guard.ratelimit import Route

router = APIRouter()

TAG = "portfolio"
PROFILE_NOT_FOUND = "Profile not found"
PORTFOLIO_NOT_FOUND = "Portfolio not found"
BODY_MAX_BYTES = 64 * 1024
"""The largest request body: a patch of 40 text edits is about 17 KB."""
SLOT_ID_MAX_CHARS = 80


class RewriteRequest(BaseModel):
    """``{"slotId": "tagline"}``: which piece of text to write again."""

    model_config = ConfigDict(extra="ignore")

    slotId: str = Field(min_length=1, max_length=SLOT_ID_MAX_CHARS)  # noqa: N815 - the JSON key


def describe_error(err: ValidationError) -> str:
    """``Invalid portfolio request: slotId String should have at least 1 character``: the first problem only,
    and the submitted value is never echoed."""
    first = err.errors()[0]
    path = ".".join(str(part) for part in first["loc"]) or "body"
    return f"Invalid portfolio request: {path} {first['msg']}"


def _no_store(data: dict[str, object]) -> JSONResponse:
    # A page is personal data behind an unguessable URL: never let a shared cache keep it.
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


def _require_id(value: str, message: str) -> None:
    if not is_valid_id(value):
        raise NotFound(message)


def _shed(container: Container, rc: RequestContext, route: Route) -> None:
    container.rate_limiter.shed(
        route, rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )


async def _read_body[M: BaseModel](request: Request, container: Container, model: type[M]) -> M:
    """The JSON body as ``model``: 415 for another Content-Type, 413 / 408 / 400 from the reader, 400 when invalid."""
    require_json_content_type(request)
    raw = await read_json_body(
        request, BODY_MAX_BYTES, read_timeout_s=container.settings.guard.upload_read_timeout_s
    )
    try:
        return model.model_validate(raw)
    except ValidationError as err:
        raise BadRequest(describe_error(err)) from None


@router.post("/api/portfolio")
async def generate(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    limiter = container.rate_limiter
    guard = container.settings.guard
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    limiter.shed("portfolio", rc.ip_hash, ip_hash_48=ip48)
    body = await _read_body(request, container, PortfolioRequest)
    _require_id(body.profileId, PROFILE_NOT_FOUND)
    regenerate = bool(body.regenerate)

    held = AsyncExitStack()
    try:
        has_page = not regenerate and await container.portfolio.has_readable(body.profileId)
        if not has_page:
            await limiter.check("portfolio", rc.ip_hash, ip_hash_48=ip48, memory=False)
            await held.enter_async_context(container.spend_guard.reserve(guard.est_portfolio_usd))
        return HeldResponse(request, container.portfolio.generate(body.profileId, regenerate, rc), TAG, held)
    except BaseException:
        await held.aclose()
        raise


@router.get("/api/portfolio/{profile_id}")
async def get_portfolio(profile_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc, "chat")
    _require_id(profile_id, PORTFOLIO_NOT_FOUND)
    portfolio = await container.portfolio.get(profile_id)
    if portfolio is None:
        raise NotFound(PORTFOLIO_NOT_FOUND)
    return _no_store(portfolio.to_dict())


@router.patch("/api/portfolio/{profile_id}")
async def patch_portfolio(profile_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc, "chat")
    _require_id(profile_id, PORTFOLIO_NOT_FOUND)
    patch = await _read_body(request, container, PortfolioPatch)
    portfolio = await container.portfolio.patch(profile_id, patch, rc)
    return _no_store(portfolio.to_dict())


@router.delete("/api/portfolio/{profile_id}")
async def delete_portfolio(profile_id: str, request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc, "chat")
    _require_id(profile_id, PORTFOLIO_NOT_FOUND)
    await container.portfolio.delete(profile_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/api/portfolio/{profile_id}/rewrite")
async def rewrite_slot(profile_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    container.rate_limiter.shed("portfolio", rc.ip_hash, ip_hash_48=ip48)
    _require_id(profile_id, PORTFOLIO_NOT_FOUND)
    body = await _read_body(request, container, RewriteRequest)
    await container.rate_limiter.check("portfolio", rc.ip_hash, ip_hash_48=ip48, memory=False)
    async with container.spend_guard.reserve(container.settings.guard.est_portfolio_usd):
        portfolio = await container.portfolio.rewrite(profile_id, body.slotId, rc)
    return _no_store(portfolio.to_dict())


@router.get("/api/portfolio/{profile_id}/public-preview")
async def public_preview(profile_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc, "chat")
    _require_id(profile_id, PORTFOLIO_NOT_FOUND)
    preview = await container.portfolio.public_preview(profile_id)
    return _no_store(preview.to_dict())


@router.get("/api/p/{public_id}")
async def public_page(public_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc, "chat")
    _require_id(public_id, PORTFOLIO_NOT_FOUND)
    page = await container.portfolio.public(public_id)
    if page is None:
        raise NotFound(PORTFOLIO_NOT_FOUND)
    return _no_store(page.to_dict())


__all__ = [
    "BODY_MAX_BYTES",
    "PORTFOLIO_NOT_FOUND",
    "PROFILE_NOT_FOUND",
    "RewriteRequest",
    "describe_error",
    "router",
]
