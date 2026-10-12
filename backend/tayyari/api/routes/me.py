"""The signed-in account: read it, claim a profile for it, change it, delete it.

    GET    /api/me             JSON `MeResponse`: the user (absent when anonymous), the profiles it owns, its
                               entitlements (`billing.EntitlementService.summary`; empty for an anonymous visitor)
                               and which sign-in methods work here
    POST   /api/me/claim       {"profileId"} -> `MeResponse`; attaches an unowned profile (and its page) to the account
    PATCH  /api/me             {"currency"?, "name"?} -> `MeResponse`
    DELETE /api/me             deletes the account with its profiles, pages and sign-ins (204, cookie cleared)

Everything but the read needs a sign-in (401 `Unauthorized`). ``GET /api/me`` is also what the web app asks first, so
it answers for anonymous visitors too, and it re-issues the session cookie when the sliding refresh just ran (the
database moved the expiry; the browser's copy needs the same lifetime). Responses are ``Cache-Control: no-store``.

Claiming follows the capability model for a profile nobody has claimed yet: its id is 128 random bits and the only
thing that protects it, so whoever presents the id gets it. From then on the id is no longer enough: the profile and its
page are served only to this account (`api.deps.require_profile_access`). A profile that already belongs to another
account is a 409; a sample run, or an id that does not exist, is a 404. Claiming one's own profile again is a no-op.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from tayyari.api.deps import User, get_auth_context, get_request_context, require_user
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.security import subnet_hash
from tayyari.auth.sessions import clear_session_cookie, set_session_cookie
from tayyari.billing.entitlements import ZERO
from tayyari.container import Container
from tayyari.contract.auth import (
    ClaimRequest,
    Currency,
    MeEntitlements,
    MePatch,
    MeProfile,
    MeProviders,
    MeResponse,
    MeUser,
)
from tayyari.core.errors import Conflict, NotFound
from tayyari.core.ids import is_valid_id
from tayyari.storage.ports import ProfileNotClaimable, ProfileOwnedByAnother

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}
PROFILE_NOT_FOUND = "Profile not found"
PROFILE_TAKEN = "That profile already belongs to another account."


def _shed(container: Container, ip_hash: str | None, client_ip: str | None) -> None:
    container.rate_limiter.shed("chat", ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, client_ip))


async def me_response(container: Container, user: User | None) -> MeResponse:
    """The `MeResponse` for ``user`` (None: an anonymous visitor)."""
    google, magic = container.auth.providers
    profiles: list[MeProfile] = []
    if user is not None:
        owned = await container.repos.users.list_profiles(user.id)
        profiles = [
            MeProfile(id=p.id, createdAt=p.created_at.isoformat(), goal=p.goal, personName=p.person_name)
            for p in owned
        ]
    currency: Currency | None = None
    if user is not None and user.currency in ("INR", "USD"):
        currency = "INR" if user.currency == "INR" else "USD"
    summary = ZERO if user is None else await container.entitlements.summary(user.id)
    return MeResponse(
        user=None
        if user is None
        else MeUser(
            id=user.id, email=user.email, name=user.name, pictureUrl=user.picture_url, currency=currency
        ),
        profiles=profiles,
        entitlements=MeEntitlements(
            credits=summary.credits,
            passUntil=None if summary.pass_until is None else summary.pass_until.isoformat(),
            freeMatchAvailable=summary.free_match_available,
        ),
        providers=MeProviders(google=google, magic=magic),
    )


def _json(answer: MeResponse) -> JSONResponse:
    return JSONResponse(answer.to_dict(), headers=NO_STORE)


@router.get("/api/me")
async def get_me(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc.ip_hash, rc.client_ip)
    signed_in = await get_auth_context(request)
    response = _json(await me_response(container, None if signed_in is None else signed_in.user))
    if signed_in is not None and signed_in.refreshed:
        set_session_cookie(response, signed_in.token, container.settings)
    return response


@router.post("/api/me/claim")
async def claim_profile(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc.ip_hash, rc.client_ip)
    user = await require_user(request)
    body = await read_model(request, container, ClaimRequest)
    if not is_valid_id(body.profileId):
        raise NotFound(PROFILE_NOT_FOUND)
    try:
        await container.repos.users.claim_profile(user.id, body.profileId)
    except ProfileNotClaimable:
        raise NotFound(PROFILE_NOT_FOUND) from None
    except ProfileOwnedByAnother:
        raise Conflict(PROFILE_TAKEN, code="profile_taken") from None
    return _json(await me_response(container, user))


@router.patch("/api/me")
async def patch_me(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc.ip_hash, rc.client_ip)
    user = await require_user(request)
    body = await read_model(request, container, MePatch)
    updated = await container.repos.users.update(user.id, name=body.name, currency=body.currency)
    return _json(await me_response(container, updated or user))


@router.delete("/api/me")
async def delete_me(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc.ip_hash, rc.client_ip)
    user = await require_user(request)
    await container.repos.users.delete(user.id)
    response = Response(status_code=204, headers=NO_STORE)
    clear_session_cookie(response, container.settings)
    return response


__all__ = ["PROFILE_NOT_FOUND", "PROFILE_TAKEN", "me_response", "router"]
