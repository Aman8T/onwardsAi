"""Discover: roles near the person that fit their profile, from employers' own public job boards.

    GET /api/discover?profileId=…[&city=…&locality=…&otherCity=…&reach=…&breadth=…&lane=…&order=…]
                                `DiscoverResponse`: the lenses with their counts, the lane's groups and listings, the
                                companies lane, coverage and why this order. ``reach`` ``city``, ``breadth`` ``next``,
                                ``lane`` ``near`` and ``order`` ``ranked`` by default
    GET /api/discover/cities    `DiscoverCitiesResponse`: the cities and localities we know, for the pickers
    GET /api/discover/listings/{id}?profileId=…[&city=…&locality=…]
                                `ListingDetail`: the card and the posting text the web app sends to ``POST /api/match``;
                                404 for an unknown or closed listing

A profile is opened exactly as every other profile route does (`api.deps.require_profile_access`): one nobody has claimed or a
sample run is open to whoever holds its id; a claimed profile only to its owner's session, and anyone else gets the 404 an
unknown id gets. **The person's area is not stored**: ``city``, ``locality`` and ``otherCity`` are names from
``/api/discover/cities`` that arrive with the request, answer it and are forgotten (they are never logged either). Names
we do not know are a 400.

Every read goes through the in-memory limiter first (``shed``, no database), in the ``chat`` bucket like the other light
reads. Nothing here calls a model, so nothing is spent. The answer is never cached by a shared cache. When Discover is
switched off (``TAYYARI_DISCOVER__ENABLED=false``) every route answers 503 ``{"error": "discover_disabled"}``. A request that
finds the data old starts a refresh in the background and does not wait for it.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from tayyari.api.deps import get_request_context, require_profile_access
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import describe
from tayyari.api.security import subnet_hash
from tayyari.container import Container
from tayyari.contract.discover import DiscoverQuery
from tayyari.contract.models import Profile
from tayyari.core.errors import BadRequest, NotFound
from tayyari.core.ids import is_valid_id
from tayyari.discover.normalise import is_listing_id

router = APIRouter()

NO_STORE: Final = {"Cache-Control": "no-store"}
PROFILE_NOT_FOUND: Final = "Profile not found"
LISTING_NOT_FOUND: Final = "Listing not found"
_QUERY_KEYS: Final = ("profileId", "city", "locality", "otherCity", "reach", "breadth", "lane", "order")


def _gate(container: Container, rc_ip_hash: str | None, client_ip: str | None) -> None:
    container.rate_limiter.shed("chat", rc_ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, client_ip))
    container.discover.require_enabled()


def _query(request: Request) -> DiscoverQuery:
    raw = {key: request.query_params[key] for key in _QUERY_KEYS if key in request.query_params}
    try:
        return DiscoverQuery.model_validate(raw)
    except ValidationError as err:
        raise BadRequest(describe(err)) from None


async def _profile(request: Request, container: Container, profile_id: str) -> Profile:
    if not is_valid_id(profile_id):
        raise NotFound(PROFILE_NOT_FOUND)
    await require_profile_access(request, profile_id, not_found=PROFILE_NOT_FOUND)
    found = await container.repos.profiles.get_profile(profile_id)
    if found is None and container.sample.is_sample_profile(profile_id):
        sample = container.sample.sample_profile
        found = sample.to_dict() if sample is not None else None
    if found is None:
        raise NotFound(PROFILE_NOT_FOUND)
    try:
        return Profile.from_dict(found)
    except ValidationError:
        raise NotFound(PROFILE_NOT_FOUND) from None


@router.get("/api/discover")
async def get_discover(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _gate(container, rc.ip_hash, rc.client_ip)
    query = _query(request)
    profile = await _profile(request, container, query.profileId)
    answer = await container.discover.discover(profile, query)
    return JSONResponse(answer.to_dict(), headers=NO_STORE)


@router.get("/api/discover/cities")
async def get_cities(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _gate(container, rc.ip_hash, rc.client_ip)
    return JSONResponse(container.discover.cities().to_dict(), headers=NO_STORE)


@router.get("/api/discover/listings/{listing_id}")
async def get_listing(listing_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _gate(container, rc.ip_hash, rc.client_ip)
    query = _query(request)
    if not is_listing_id(listing_id):
        raise NotFound(LISTING_NOT_FOUND)
    profile = await _profile(request, container, query.profileId)
    found = await container.discover.detail(listing_id, profile, city=query.city, locality=query.locality)
    if found is None:
        raise NotFound(LISTING_NOT_FOUND)
    return JSONResponse(found.to_dict(), headers=NO_STORE)


__all__ = ["LISTING_NOT_FOUND", "PROFILE_NOT_FOUND", "router"]
