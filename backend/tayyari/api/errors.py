"""How failures become HTTP answers: always JSON ``{"error": message}`` before a stream starts.

    core.errors.AppError           its status, ``{"error": user_message}`` and a ``Retry-After`` header when
                                   `retry_after_s` is set (429 rate limits and the 503 spend ceiling are
                                   AppErrors); `detail` is logged, never sent
    starlette HTTPException        ``{"error": detail}`` with its status and headers (a missing route, 405 ...)
    RequestValidationError         400 ``{"error": "Invalid request: <field> <what is wrong>"}``
    ClientDisconnect / ClientClosed  499 with no body: the client left, nobody is listening
    any other Exception            500 ``{"error": "Something went wrong on the server."}``; the traceback is
                                   logged, the client only learns it was not their fault

Once a response has started streaming none of this applies: `api.ndjson` turns a late failure into an
``error`` event with `user_message_for`.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from tayyari.core.errors import AppError, ClientClosed
from tayyari.core.logging import get_logger
from tayyari.llm.errors import ClaudeError

log = get_logger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong on the server."
INVALID_JSON_MESSAGE = "Invalid JSON body"
_VALIDATION_MSG_MAX_CHARS = 200
_NO_BODY_STATUSES = frozenset({204, 304})


def user_message_for(exc: BaseException) -> str:
    """The sentence a person may see for ``exc``: the message of an `AppError` or `ClaudeError`, else the
    generic one. Never ``str(exc)``: that can hold paths, provider text or content."""
    if isinstance(exc, AppError | ClaudeError):
        return exc.user_message
    return GENERIC_ERROR_MESSAGE


def describe_validation_error(exc: RequestValidationError) -> str:
    """``Invalid request: message String should have at least 1 character``: the first problem only.

    The location drops a leading ``body`` (the body is the only thing a client sends us), and the
    submitted value is never echoed (it can be a resume or a chat message).
    """
    errors = exc.errors()
    if not errors:
        return "Invalid request"
    first = errors[0]
    if first.get("type") == "json_invalid":
        return INVALID_JSON_MESSAGE
    loc = [str(part) for part in first.get("loc", ())]
    if len(loc) > 1 and loc[0] == "body":
        loc = loc[1:]
    where = ".".join(loc) or "body"
    message = str(first.get("msg", "is not valid"))[:_VALIDATION_MSG_MAX_CHARS]
    return f"Invalid request: {where} {message}"


def _request_id_headers(request: Request) -> dict[str, str]:
    """``X-Request-ID`` for responses built outside `RequestIdMiddleware` (the 500 handler runs outside it)."""
    state: Any = request.scope.get("state")
    rid = state.get("request_id") if isinstance(state, dict) else None
    return {"X-Request-ID": rid} if isinstance(rid, str) and rid else {}


def install_error_handlers(app: FastAPI) -> None:
    """Register every handler above on ``app``. Call once, from the app factory."""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> Response:
        if isinstance(exc, ClientClosed):
            return Response(status_code=499)
        if exc.status >= 500 and exc.status != 503:
            log.error("app_error", code=exc.code, status=exc.status, detail=exc.detail)
        else:
            log.info("app_error", code=exc.code, status=exc.status, detail=exc.detail)
        headers: dict[str, str] = {}
        retry_after = exc.retry_after_header()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return JSONResponse(exc.to_body(), status_code=exc.status, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code in _NO_BODY_STATUSES:
            return Response(status_code=exc.status_code, headers=exc.headers)
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> Response:
        return JSONResponse({"error": describe_validation_error(exc)}, status_code=400)

    @app.exception_handler(ClientDisconnect)
    async def _client_gone(request: Request, exc: ClientDisconnect) -> Response:
        # The client left while its request body was still being read: nothing to answer, nothing to log.
        return Response(status_code=499)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        log.error(
            "unhandled_exception",
            method=request.method,
            path="/api/handoff/…" if request.url.path.startswith("/api/handoff/") else request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            {"error": GENERIC_ERROR_MESSAGE}, status_code=500, headers=_request_id_headers(request)
        )


__all__ = [
    "GENERIC_ERROR_MESSAGE",
    "INVALID_JSON_MESSAGE",
    "describe_validation_error",
    "install_error_handlers",
    "user_message_for",
]
