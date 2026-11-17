"""Application errors that map to an HTTP answer, without importing any web framework.

Services raise these; `api/errors.py` turns them into the JSON body ``{"error": user_message}`` plus
status (and a ``Retry-After`` header when `retry_after_s` is set). `user_message` is written for the
person using the product and is safe to show; `detail` is for logs only and is never sent to a client.
"""

from __future__ import annotations

import math
from collections.abc import Mapping


class AppError(Exception):
    """Base class: an expected failure with a status, a stable machine code and a human message."""

    status: int = 500
    code: str = "internal_error"
    user_message: str = "Something went wrong on the server."
    retry_after_s: float | None = None

    def __init__(
        self,
        user_message: str | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
        retry_after_s: float | None = None,
        detail: str | None = None,
    ) -> None:
        """Class-level defaults apply to anything not passed (subclasses set their own)."""
        if user_message is not None:
            self.user_message = user_message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        if retry_after_s is not None:
            self.retry_after_s = retry_after_s
        self.detail = detail
        super().__init__(self.user_message)

    def to_body(self) -> Mapping[str, object]:
        """The JSON error body the frontend reads (``{"error": message}``)."""
        return {"error": self.user_message}

    def retry_after_header(self) -> str | None:
        """``Retry-After`` value in whole seconds (rounded up, at least 1), or None."""
        if self.retry_after_s is None:
            return None
        return str(max(1, math.ceil(self.retry_after_s)))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status}, code={self.code!r}, user_message={self.user_message!r})"


class BadRequest(AppError):
    status = 400
    code = "bad_request"
    user_message = "That request was not valid."


class NotFound(AppError):
    status = 404
    code = "not_found"
    user_message = "Not found"


class Unauthorized(AppError):
    """No valid sign-in: the person has to sign in (or sign in again) to do this."""

    status = 401
    code = "unauthorized"
    user_message = "Sign in to continue."


class PaymentRequired(AppError):
    """The person has nothing left to spend on this: no pass, no free posting this month, no credits.

    The JSON body is ``{"error": "payment_required", "reason": ...}`` (the web app reads ``error`` as a code and
    sends the person to the pricing page); ``user_message`` is the sentence a stream's ``error`` event shows.

    A Just Looking count that has run out (``reason="limit_reached"``, roadmap B2) adds ``feature``, ``limit`` and
    ``resetsAt`` (an ISO timestamp), each only when it is given; ``resetsAt`` is left out for ``applications``, which
    is counted once. A ``no_credits`` body has no more keys.
    """

    status = 402
    code = "payment_required"
    user_message = "You have used this month's free posting. Choose a plan to continue."

    def __init__(
        self,
        reason: str = "no_credits",
        *,
        feature: str | None = None,
        limit: int | None = None,
        resets_at: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.reason = reason
        self.feature = feature
        self.limit = limit
        self.resets_at = resets_at

    def to_body(self) -> dict[str, str | int]:
        body: dict[str, str | int] = {"error": self.code, "reason": self.reason}
        if self.feature is not None:
            body["feature"] = self.feature
        if self.limit is not None:
            body["limit"] = self.limit
        if self.resets_at is not None:
            body["resetsAt"] = self.resets_at
        return body


class Conflict(AppError):
    """The request clashes with the current state (a profile that already belongs to someone else, say)."""

    status = 409
    code = "conflict"
    user_message = "That conflicts with what is already saved."


class PayloadTooLarge(AppError):
    status = 413
    code = "payload_too_large"
    user_message = "That upload is too large."


class RateLimited(AppError):
    status = 429
    code = "rate_limited"
    user_message = "Too many requests right now. Please wait a moment and try again."


class ServiceUnavailable(AppError):
    status = 503
    code = "service_unavailable"
    user_message = "Leafout is temporarily unavailable. Please try again soon."


class ClientClosed(AppError):
    """The client went away before the answer was ready (nginx's non-standard 499); nothing is sent."""

    status = 499
    code = "client_closed"
    user_message = "The request was cancelled."


__all__ = [
    "AppError",
    "BadRequest",
    "ClientClosed",
    "Conflict",
    "NotFound",
    "PayloadTooLarge",
    "PaymentRequired",
    "RateLimited",
    "ServiceUnavailable",
    "Unauthorized",
]
