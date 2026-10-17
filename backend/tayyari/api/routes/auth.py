"""Sign in and out: Google, emailed links, logout.

    GET  /api/auth/google/start?next=/            302 to Google (state, PKCE and nonce in a signed cookie)
    GET  /api/auth/google/callback                verifies, signs in, sets the session cookie, 302 to ``next``
    POST /api/auth/magic          {"email"}       202 always: a link is emailed when it can be (no enumeration)
    GET  /api/auth/magic/verify?token=...         spends the link, signs in, sets the cookie, 302 to ``/``
    POST /api/auth/logout                         ends the session and clears the cookie (204)

The two GET routes a browser lands on never answer with JSON: success or failure, the answer is a redirect, and a
failure goes to ``/?auth=<code>`` (``google_unavailable``, ``google_denied``, ``google_failed``, ``link_invalid``) so
the web app can say so on the sign-in sheet. Every redirect target is a path on this site (`google.safe_next`).
State-changing methods are covered by the cross-site guard (`api.security.CrossSiteGuard`), the session cookie is
SameSite=Lax, and the JSON body of ``/magic`` needs ``Content-Type: application/json``.

The emailed link only ever goes to the address that asked for it: the 202 body carries a ``devLink`` on a local server
with no email key and never anywhere else (`auth.magic.MagicLinks.dev_mode`).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.responses import Response

from tayyari.api.deps import get_request_context, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.security import subnet_hash
from tayyari.auth.google import CALLBACK_PATH, STATE_COOKIE, STATE_MAX_AGE_S, GoogleError
from tayyari.auth.sessions import clear_session_cookie, cookies_are_secure, set_session_cookie
from tayyari.container import Container
from tayyari.contract.auth import MagicLinkRequest, MagicLinkResponse
from tayyari.core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)

NO_STORE = {"Cache-Control": "no-store"}


def _redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=302, headers=NO_STORE)


def _failed(code: str) -> RedirectResponse:
    return _redirect(f"/?auth={code}")


def _clear_state_cookie(response: Response, container: Container) -> None:
    response.delete_cookie(
        STATE_COOKIE,
        path=CALLBACK_PATH,
        httponly=True,
        samesite="lax",
        secure=cookies_are_secure(container.settings),
    )


async def _shed(
    request: Request, container: Container, request_ip_hash: str | None, client_ip: str | None
) -> None:
    container.rate_limiter.shed(
        "chat",
        request_ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, client_ip),
        exempt=await unlimited(request, container),
    )


@router.get("/api/auth/google/start")
async def google_start(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc.ip_hash, rc.client_ip)
    try:
        start = container.auth.google_start(request.query_params.get("next"))
    except GoogleError as err:
        log.warning("google_sign_in_unavailable", reason=err.reason)
        return _failed("google_unavailable")
    response = _redirect(start.url)
    response.set_cookie(
        STATE_COOKIE,
        start.cookie_value,
        max_age=STATE_MAX_AGE_S,
        path=CALLBACK_PATH,
        httponly=True,
        samesite="lax",
        secure=cookies_are_secure(container.settings),
    )
    return response


@router.get("/api/auth/google/callback")
async def google_callback(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc.ip_hash, rc.client_ip)
    params = request.query_params
    if params.get("error"):
        # The person said no on Google's page (access_denied) or Google refused: nothing to verify.
        error = "".join(ch for ch in params.get("error", "") if ch.isalnum() or ch in "_-")[:40]
        log.info("google_sign_in_declined", error=error)
        response: Response = _failed("google_denied")
    else:
        try:
            signed = await container.auth.google_finish(
                code=params.get("code"),
                state=params.get("state"),
                cookie_value=request.cookies.get(STATE_COOKIE),
                ip_hash=rc.ip_hash,
                user_agent=request.headers.get("user-agent"),
            )
        except GoogleError as err:
            log.warning("google_sign_in_failed", reason=err.reason)
            response = _failed("google_failed")
        else:
            response = _redirect(signed.next_path)
            set_session_cookie(response, signed.token, container.settings)
    _clear_state_cookie(response, container)
    return response


@router.post("/api/auth/magic")
async def request_magic_link(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc.ip_hash, rc.client_ip)
    body = await read_model(request, container, MagicLinkRequest)
    outcome = await container.auth.request_magic_link(body.email, rc.ip_hash)
    answer = MagicLinkResponse(ok=True, devLink=outcome.dev_link)
    return JSONResponse(answer.to_dict(), status_code=202, headers=NO_STORE)


@router.get("/api/auth/magic/verify")
async def verify_magic_link(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc.ip_hash, rc.client_ip)
    signed = await container.auth.verify_magic_link(
        request.query_params.get("token", ""),
        ip_hash=rc.ip_hash,
        user_agent=request.headers.get("user-agent"),
    )
    if signed is None:
        return _failed("link_invalid")
    response = _redirect("/")
    set_session_cookie(response, signed.token, container.settings)
    return response


@router.post("/api/auth/logout")
async def logout(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed(request, container, rc.ip_hash, rc.client_ip)
    await container.auth.sessions.end(request.cookies.get(container.settings.auth.cookie_name))
    response = Response(status_code=204, headers=NO_STORE)
    clear_session_cookie(response, container.settings)
    return response


__all__ = ["router"]
