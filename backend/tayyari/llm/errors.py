"""Typed failures of the LLM layer, each with a message that is safe to show to the person using Tayyari.

Callers branch on the class (or `kind`) instead of parsing English: the legacy `humanize_error` and its
regexes are gone. `user_message` is written for the UI; `detail` is for logs only (it can hold the
provider's own message) and is never sent to a client.

    ClaudeError                    base: kind, retryable, status, request_id, user_message
      ClaudeNotConfigured          no API key configured
      ClaudeAuthError              401 / 403 / 402 from Anthropic
      ClaudeRateLimited            429, with retry_after_s
      ClaudeOverloaded             529 / 503, with retry_after_s
      ClaudeTimeout                request timed out (SDK timeout, 504, our own deadline)
      ClaudeConnectionError        could not reach the API
      ClaudeBadRequest             other 4xx (the request itself is wrong)
      ClaudeRefusal                stop_reason "refusal", with the policy category
      ClaudeTruncated              stop_reason "max_tokens"
      ClaudeInvalidOutput          structured output that failed validation, with the issues
    SpendCeilingReached            the daily ceiling is hit; raised by the guard before any Claude or Jev call
    JevError                       any failure of the Jev API, with a kind the decision runner switches on

`asyncio.CancelledError` is never wrapped: let it propagate.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import anthropic

from ..core.errors import ServiceUnavailable

ClaudeErrorKind = Literal[
    "not_configured",
    "auth",
    "rate_limited",
    "overloaded",
    "timeout",
    "connection",
    "bad_request",
    "refusal",
    "truncated",
    "invalid_output",
    "server",
    "unknown",
]

# User-facing texts. The legacy wording is kept where it still fits; the key message names the server's key
# (a visitor cannot fix it, and the old text told them to set an environment variable).
AUTH_MESSAGE = "The server's Anthropic API key is missing or invalid."
BUSY_MESSAGE = "Claude is busy right now (rate limited or overloaded). Try again in a minute."
NETWORK_MESSAGE = "Could not reach Claude (network error or timeout). Try again."
INCOMPLETE_MESSAGE = "The model returned an incomplete answer. Try again."
TRUNCATED_MESSAGE = "Claude's answer was cut off before it was finished. Try again."
BAD_REQUEST_MESSAGE = "Claude could not process that request. Try again, or try with less content."
TOO_LARGE_MESSAGE = "That input is too large for the model. Try again with less content."
SERVER_MESSAGE = "Claude had a problem on its side. Try again in a minute."
UNKNOWN_MESSAGE = "Something went wrong while talking to Claude."
MODEL_UNAVAILABLE_MESSAGE = "The configured Claude model isn't available to the server's Anthropic account."
PERMISSION_MESSAGE = "The server's Anthropic API key isn't allowed to use this model or feature."
BILLING_MESSAGE = "The server's Anthropic account has a billing problem."
SPEND_CEILING_MESSAGE = "Tayyari has reached today's usage limit. The pre-computed sample profile still works. Try again tomorrow."

_REFUSAL_MESSAGES: Mapping[str, str] = {
    "cyber": "Claude declined this request because it looked like a security-sensitive task. Try rephrasing it.",
    "bio": "Claude declined this request because it touches a restricted life-sciences topic. Try rephrasing it.",
    "frontier_llm": "Claude declined this request because it looked like AI model development work. Try rephrasing it.",
    "reasoning_extraction": "Claude declined this request because it asked for its internal reasoning. Try rephrasing it.",
    "general_harms": "Claude declined to work with that content. If you think that was a mistake, try different wording.",
}
_REFUSAL_DEFAULT = "Claude declined to answer that. Try rephrasing and ask again."


class ClaudeError(Exception):
    """A failed Claude call. Subclasses fix `kind` and the defaults; this class is also used directly for
    5xx answers (``kind="server"``) and anything unrecognised (``kind="unknown"``)."""

    def __init__(
        self,
        kind: ClaudeErrorKind = "unknown",
        *,
        retryable: bool = False,
        status: int | None = None,
        request_id: str | None = None,
        user_message: str = UNKNOWN_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(user_message)
        self.kind: ClaudeErrorKind = kind
        self.retryable = retryable
        self.status = status
        self.request_id = request_id
        self.user_message = user_message
        self.detail = detail

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(kind={self.kind!r}, retryable={self.retryable}, status={self.status}, "
            f"request_id={self.request_id!r}, user_message={self.user_message!r})"
        )


class ClaudeNotConfigured(ClaudeError):
    """No Anthropic API key is configured on the server."""

    def __init__(self, *, user_message: str = AUTH_MESSAGE, detail: str | None = None) -> None:
        super().__init__("not_configured", retryable=False, user_message=user_message, detail=detail)


class ClaudeAuthError(ClaudeError):
    """Anthropic rejected the server's credentials (401), the key lacks permission (403) or billing failed (402)."""

    def __init__(
        self,
        *,
        status: int | None = 401,
        request_id: str | None = None,
        user_message: str = AUTH_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "auth",
            retryable=False,
            status=status,
            request_id=request_id,
            user_message=user_message,
            detail=detail,
        )


class ClaudeRateLimited(ClaudeError):
    """429: too many requests or tokens. `retry_after_s` comes from the ``retry-after`` header when present."""

    def __init__(
        self,
        retry_after_s: float | None = None,
        *,
        status: int | None = 429,
        request_id: str | None = None,
        user_message: str = BUSY_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "rate_limited",
            retryable=True,
            status=status,
            request_id=request_id,
            user_message=user_message,
            detail=detail,
        )
        self.retry_after_s = retry_after_s


class ClaudeOverloaded(ClaudeError):
    """529 (overloaded) or 503: the API is temporarily out of capacity."""

    def __init__(
        self,
        retry_after_s: float | None = None,
        *,
        status: int | None = 529,
        request_id: str | None = None,
        user_message: str = BUSY_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "overloaded",
            retryable=True,
            status=status,
            request_id=request_id,
            user_message=user_message,
            detail=detail,
        )
        self.retry_after_s = retry_after_s


class ClaudeTimeout(ClaudeError):
    """The request timed out: an SDK timeout, a 504, or a deadline of our own."""

    def __init__(
        self,
        *,
        status: int | None = None,
        request_id: str | None = None,
        user_message: str = NETWORK_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "timeout",
            retryable=True,
            status=status,
            request_id=request_id,
            user_message=user_message,
            detail=detail,
        )


class ClaudeConnectionError(ClaudeError):
    """The API could not be reached at all (DNS, TLS, reset, closed stream)."""

    def __init__(
        self,
        *,
        request_id: str | None = None,
        user_message: str = NETWORK_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "connection", retryable=True, request_id=request_id, user_message=user_message, detail=detail
        )


class ClaudeBadRequest(ClaudeError):
    """The request itself was rejected (400, 404, 413, 422 ...). Sending it again unchanged will not help."""

    def __init__(
        self,
        *,
        status: int | None = 400,
        request_id: str | None = None,
        user_message: str = BAD_REQUEST_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "bad_request",
            retryable=False,
            status=status,
            request_id=request_id,
            user_message=user_message,
            detail=detail,
        )


class ClaudeRefusal(ClaudeError):
    """The model declined (``stop_reason == "refusal"``). `category` is the API's policy category, or None.

    ``general_harms`` can fire on benign but sensitive resumes, so the message invites rewording rather
    than blaming the user.
    """

    def __init__(
        self,
        category: str | None = None,
        *,
        request_id: str | None = None,
        user_message: str | None = None,
        detail: str | None = None,
    ) -> None:
        text = user_message or _REFUSAL_MESSAGES.get(category or "", _REFUSAL_DEFAULT)
        super().__init__("refusal", retryable=False, request_id=request_id, user_message=text, detail=detail)
        self.category = category


class ClaudeTruncated(ClaudeError):
    """The answer hit ``max_tokens`` (``stop_reason == "max_tokens"``); adaptive thinking tokens count against it."""

    def __init__(
        self,
        max_tokens: int | None = None,
        *,
        request_id: str | None = None,
        user_message: str = TRUNCATED_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "truncated", retryable=False, request_id=request_id, user_message=user_message, detail=detail
        )
        self.max_tokens = max_tokens


class ClaudeInvalidOutput(ClaudeError):
    """Structured output that is not JSON or fails the schema, after the client's own attempts. `issues` is capped by the caller."""

    def __init__(
        self,
        issues: Sequence[str] = (),
        *,
        request_id: str | None = None,
        user_message: str = INCOMPLETE_MESSAGE,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "invalid_output", retryable=True, request_id=request_id, user_message=user_message, detail=detail
        )
        self.issues: tuple[str, ...] = tuple(issues)


# ───────────────────────── anthropic SDK mapping ─────────────────────────

_MAX_RETRY_AFTER_S = 3600.0
_DETAIL_CHARS = 300


def _parse_retry_after(headers: Any, *, now: datetime | None = None) -> float | None:
    """Seconds to wait from ``retry-after-ms`` or ``retry-after`` (seconds or an HTTP date); None when absent."""
    if headers is None:
        return None
    try:
        raw_ms = headers.get("retry-after-ms")
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    seconds: float | None = None
    if raw_ms is not None:
        try:
            seconds = float(raw_ms) / 1000.0
        except (TypeError, ValueError):
            seconds = None
    if seconds is None and raw is not None:
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            try:
                when = parsedate_to_datetime(str(raw))
            except (TypeError, ValueError):
                return None
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if seconds is None or math.isnan(seconds):
        return None
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_S)


def _api_message(exc: anthropic.APIError) -> str | None:
    """The provider's own error text (for logs), clipped."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:_DETAIL_CHARS]
    message = getattr(exc, "message", None)
    return str(message)[:_DETAIL_CHARS] if message else None


def _request_id_of(exc: anthropic.APIStatusError) -> str | None:
    if exc.request_id:
        return exc.request_id
    body = exc.body
    if isinstance(body, dict) and isinstance(body.get("request_id"), str):
        return str(body["request_id"])
    return None


def _from_status_error(exc: anthropic.APIStatusError) -> ClaudeError:
    status = exc.status_code
    rid = _request_id_of(exc)
    detail = _api_message(exc)
    kind = exc.type  # error.type of the body; also set for mid-stream SSE error events (HTTP 200)
    retry_after = _parse_retry_after(getattr(exc.response, "headers", None))

    if kind == "authentication_error" or status == 401:
        return ClaudeAuthError(status=status, request_id=rid, detail=detail)
    if kind == "billing_error" or status == 402:
        return ClaudeAuthError(status=status, request_id=rid, user_message=BILLING_MESSAGE, detail=detail)
    if kind == "permission_error" or status == 403:
        return ClaudeAuthError(status=status, request_id=rid, user_message=PERMISSION_MESSAGE, detail=detail)
    if kind == "rate_limit_error" or status == 429:
        return ClaudeRateLimited(retry_after, status=status, request_id=rid, detail=detail)
    if kind == "overloaded_error" or status in (503, 529):
        return ClaudeOverloaded(retry_after, status=status, request_id=rid, detail=detail)
    if kind == "timeout_error" or status == 504:
        return ClaudeTimeout(status=status, request_id=rid, detail=detail)
    if kind == "not_found_error" or status == 404:
        return ClaudeBadRequest(
            status=status, request_id=rid, user_message=MODEL_UNAVAILABLE_MESSAGE, detail=detail
        )
    if status == 413:
        return ClaudeBadRequest(status=status, request_id=rid, user_message=TOO_LARGE_MESSAGE, detail=detail)
    if kind == "invalid_request_error" or 400 <= status < 500:
        return ClaudeBadRequest(status=status, request_id=rid, detail=detail)
    if kind == "api_error" or status >= 500:
        return ClaudeError(
            "server",
            retryable=True,
            status=status,
            request_id=rid,
            user_message=SERVER_MESSAGE,
            detail=detail,
        )
    return ClaudeError("unknown", status=status, request_id=rid, detail=detail)


def from_anthropic(exc: BaseException) -> ClaudeError:
    """Map an exception raised by the Anthropic SDK (or a deadline of ours) to a `ClaudeError`.

    - HTTP errors are classified by the body's ``error.type`` first (it is also what a mid-stream error
      event carries, with HTTP 200) and by status code second. ``retry-after(-ms)`` is read from the
      response headers and the request id from the ``request-id`` header.
    - `anthropic.APITimeoutError`, `TimeoutError` -> `ClaudeTimeout`; other `anthropic.APIConnectionError`
      -> `ClaudeConnectionError`; missing credentials -> `ClaudeNotConfigured`.
    - A `ClaudeError` is returned unchanged. Anything else becomes ``ClaudeError(kind="unknown")``.

    `asyncio.CancelledError`, `KeyboardInterrupt` and other non-`Exception` exceptions are re-raised rather
    than wrapped: cancellation has to propagate, so a wrapper that catches ``BaseException`` stays safe.
    """
    if not isinstance(exc, Exception):
        raise exc
    if isinstance(exc, ClaudeError):
        return exc
    if isinstance(exc, anthropic.APIStatusError):
        return _from_status_error(exc)
    if isinstance(exc, anthropic.APITimeoutError | TimeoutError):
        return ClaudeTimeout(detail=str(exc)[:_DETAIL_CHARS] or None)
    if isinstance(exc, anthropic.APIConnectionError):
        return ClaudeConnectionError(detail=_api_message(exc))
    if isinstance(exc, anthropic.CredentialsError):
        return ClaudeNotConfigured(detail=str(exc)[:_DETAIL_CHARS] or None)
    if isinstance(exc, anthropic.APIError):
        # e.g. APIResponseValidationError: the API answered, but not in a shape the SDK understands.
        return ClaudeError(
            "server",
            retryable=True,
            user_message=SERVER_MESSAGE,
            detail=(_api_message(exc) or type(exc).__name__),
        )
    return ClaudeError("unknown", detail=f"{type(exc).__name__}: {str(exc)[:_DETAIL_CHARS]}")


# ───────────────────────── spend ceiling ─────────────────────────


class SpendCeilingReached(ServiceUnavailable):
    """The daily spend ceiling is reached: raised by the guard before any Claude or Jev call.

    It is an `AppError` (503 with ``Retry-After`` = seconds to the next UTC midnight), so the API turns it
    into the documented JSON answer, and decisions treat it as "answer with the neutral value".
    """

    code = "spend_ceiling_reached"
    user_message = SPEND_CEILING_MESSAGE

    def __init__(self, retry_after_s: float | None = None, *, detail: str | None = None) -> None:
        super().__init__(retry_after_s=retry_after_s, detail=detail)


# ───────────────────────── Jev ─────────────────────────

JevErrorKind = Literal[
    "auth",
    "rate_limit",
    "bad_request",
    "timeout",
    "connection",
    "server",
    "invalid_response",
    "disabled",
]
_JEV_RETRYABLE: frozenset[str] = frozenset({"rate_limit", "timeout", "connection", "server"})


class JevError(Exception):
    """A failed Jev call. The decision runner switches on `kind` (and the circuit breaker counts the
    retryable ones); the message is for logs, never for the UI.

    ``disabled`` means no Jev key is configured or Jev was switched off: nothing was sent.
    """

    def __init__(
        self,
        kind: JevErrorKind,
        message: str | None = None,
        *,
        retry_after_s: float | None = None,
        request_id: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message or f"Jev call failed ({kind})")
        self.kind: JevErrorKind = kind
        self.retry_after_s = retry_after_s
        self.request_id = request_id
        self.status = status

    @property
    def retryable(self) -> bool:
        """True for failures a later attempt can fix (rate limit, timeout, connection, 5xx)."""
        return self.kind in _JEV_RETRYABLE

    def __repr__(self) -> str:
        return (
            f"JevError(kind={self.kind!r}, status={self.status}, retry_after_s={self.retry_after_s}, "
            f"request_id={self.request_id!r}, message={str(self)!r})"
        )


__all__ = [
    "AUTH_MESSAGE",
    "BAD_REQUEST_MESSAGE",
    "BILLING_MESSAGE",
    "BUSY_MESSAGE",
    "INCOMPLETE_MESSAGE",
    "MODEL_UNAVAILABLE_MESSAGE",
    "NETWORK_MESSAGE",
    "PERMISSION_MESSAGE",
    "SERVER_MESSAGE",
    "SPEND_CEILING_MESSAGE",
    "TOO_LARGE_MESSAGE",
    "TRUNCATED_MESSAGE",
    "UNKNOWN_MESSAGE",
    "ClaudeAuthError",
    "ClaudeBadRequest",
    "ClaudeConnectionError",
    "ClaudeError",
    "ClaudeErrorKind",
    "ClaudeInvalidOutput",
    "ClaudeNotConfigured",
    "ClaudeOverloaded",
    "ClaudeRateLimited",
    "ClaudeRefusal",
    "ClaudeTimeout",
    "ClaudeTruncated",
    "JevError",
    "JevErrorKind",
    "SpendCeilingReached",
    "from_anthropic",
]
