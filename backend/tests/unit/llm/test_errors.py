"""Typed errors: the class family, user messages, and `from_anthropic` over the real SDK exception classes.

The SDK (anthropic 1.x) builds its exceptions from `httpx2` request and response objects, so the tests do too.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import anthropic
import httpx2
import pytest

from tayyari.core.errors import AppError, ServiceUnavailable
from tayyari.llm import errors
from tayyari.llm.errors import (
    AUTH_MESSAGE,
    BAD_REQUEST_MESSAGE,
    BILLING_MESSAGE,
    BUSY_MESSAGE,
    INCOMPLETE_MESSAGE,
    MODEL_UNAVAILABLE_MESSAGE,
    NETWORK_MESSAGE,
    PERMISSION_MESSAGE,
    SERVER_MESSAGE,
    SPEND_CEILING_MESSAGE,
    TOO_LARGE_MESSAGE,
    TRUNCATED_MESSAGE,
    ClaudeAuthError,
    ClaudeBadRequest,
    ClaudeConnectionError,
    ClaudeError,
    ClaudeInvalidOutput,
    ClaudeNotConfigured,
    ClaudeOverloaded,
    ClaudeRateLimited,
    ClaudeRefusal,
    ClaudeTimeout,
    ClaudeTruncated,
    JevError,
    SpendCeilingReached,
    from_anthropic,
)

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _error_body(error_type: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "error", "error": {"type": error_type, "message": message}}
    if request_id:
        body["request_id"] = request_id
    return body


def _response(status: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status, headers=headers, request=REQUEST)


_UNSET: Any = object()


def _status_error(
    cls: type[anthropic.APIStatusError],
    status: int,
    *,
    error_type: str | None = None,
    message: str = "provider text",
    headers: dict[str, str] | None = None,
    body: Any = _UNSET,
) -> anthropic.APIStatusError:
    if body is _UNSET:
        body = _error_body(error_type, message) if error_type else None
    return cls(message, response=_response(status, headers), body=body)


# ───────────────────────── mapping the SDK's HTTP errors ─────────────────────────


def test_401_is_an_auth_error_with_the_key_message() -> None:
    exc = _status_error(
        anthropic.AuthenticationError,
        401,
        error_type="authentication_error",
        message="invalid x-api-key",
        headers={"request-id": "req_401"},
    )
    mapped = from_anthropic(exc)
    assert isinstance(mapped, ClaudeAuthError)
    assert (mapped.kind, mapped.retryable, mapped.status, mapped.request_id) == (
        "auth",
        False,
        401,
        "req_401",
    )
    assert mapped.user_message == AUTH_MESSAGE == "The server's Anthropic API key is missing or invalid."
    assert mapped.detail == "invalid x-api-key"  # the provider's words stay in the log detail only
    assert "x-api-key" not in mapped.user_message


def test_403_and_402_keep_the_auth_family_with_their_own_text() -> None:
    forbidden = from_anthropic(
        _status_error(anthropic.PermissionDeniedError, 403, error_type="permission_error")
    )
    assert isinstance(forbidden, ClaudeAuthError)
    assert (forbidden.status, forbidden.user_message) == (403, PERMISSION_MESSAGE)
    billing = from_anthropic(
        _status_error(
            anthropic.APIStatusError, 402, error_type="billing_error", message="credit balance too low"
        )
    )
    assert isinstance(billing, ClaudeAuthError)
    assert (billing.status, billing.user_message, billing.retryable) == (402, BILLING_MESSAGE, False)


def test_429_carries_retry_after_seconds() -> None:
    exc = _status_error(
        anthropic.RateLimitError,
        429,
        error_type="rate_limit_error",
        headers={"retry-after": "7", "request-id": "req_429"},
    )
    mapped = from_anthropic(exc)
    assert isinstance(mapped, ClaudeRateLimited)
    assert (mapped.kind, mapped.retryable, mapped.status, mapped.request_id) == (
        "rate_limited",
        True,
        429,
        "req_429",
    )
    assert mapped.retry_after_s == 7.0
    assert mapped.user_message == BUSY_MESSAGE


def test_429_without_a_header_has_no_retry_after() -> None:
    mapped = from_anthropic(_status_error(anthropic.RateLimitError, 429, error_type="rate_limit_error"))
    assert isinstance(mapped, ClaudeRateLimited)
    assert mapped.retry_after_s is None


def test_retry_after_ms_beats_retry_after() -> None:
    exc = _status_error(
        anthropic.RateLimitError,
        429,
        error_type="rate_limit_error",
        headers={"retry-after-ms": "1500", "retry-after": "9"},
    )
    mapped = from_anthropic(exc)
    assert isinstance(mapped, ClaudeRateLimited)
    assert mapped.retry_after_s == 1.5


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, None),
        ({"retry-after": "30"}, 30.0),
        ({"retry-after": "0.25"}, 0.25),
        ({"retry-after": "-5"}, 0.0),
        ({"retry-after": "999999"}, 3600.0),
        ({"retry-after": "soon"}, None),
        ({"retry-after": "nan"}, None),
        ({"retry-after-ms": "250"}, 0.25),
        ({"retry-after-ms": "junk", "retry-after": "4"}, 4.0),
        ({"retry-after": "Wed, 07 Oct 2026 12:01:30 GMT"}, 90.0),
        ({"retry-after": "Wed, 07 Oct 2026 11:00:00 GMT"}, 0.0),
    ],
)
def test_retry_after_parsing(headers: dict[str, str], expected: float | None) -> None:
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    assert errors._parse_retry_after(httpx2.Headers(headers), now=now) == expected


def test_retry_after_parsing_tolerates_missing_headers_objects() -> None:
    assert errors._parse_retry_after(None) is None
    assert errors._parse_retry_after(object()) is None


def test_529_and_503_are_overloaded() -> None:
    overloaded = from_anthropic(
        _status_error(
            anthropic.OverloadedError, 529, error_type="overloaded_error", headers={"retry-after": "12"}
        )
    )
    assert isinstance(overloaded, ClaudeOverloaded)
    assert (overloaded.kind, overloaded.retryable, overloaded.status, overloaded.retry_after_s) == (
        "overloaded",
        True,
        529,
        12.0,
    )
    assert overloaded.user_message == BUSY_MESSAGE
    unavailable = from_anthropic(_status_error(anthropic.ServiceUnavailableError, 503))
    assert isinstance(unavailable, ClaudeOverloaded) and unavailable.status == 503


def test_a_mid_stream_error_event_is_classified_by_its_body_type_not_its_200() -> None:
    event = _status_error(anthropic.APIStatusError, 200, error_type="overloaded_error", message="Overloaded")
    mapped = from_anthropic(event)
    assert isinstance(mapped, ClaudeOverloaded)
    assert mapped.status == 200
    api_error = from_anthropic(_status_error(anthropic.APIStatusError, 200, error_type="api_error"))
    assert api_error.kind == "server" and api_error.retryable


def test_504_and_timeout_error_bodies_are_timeouts() -> None:
    mapped = from_anthropic(_status_error(anthropic.DeadlineExceededError, 504))
    assert isinstance(mapped, ClaudeTimeout)
    assert (mapped.kind, mapped.retryable, mapped.status) == ("timeout", True, 504)
    assert mapped.user_message == NETWORK_MESSAGE
    assert isinstance(
        from_anthropic(_status_error(anthropic.APIStatusError, 200, error_type="timeout_error")),
        ClaudeTimeout,
    )


def test_500_is_a_generic_retryable_server_error() -> None:
    mapped = from_anthropic(
        _status_error(
            anthropic.InternalServerError, 500, error_type="api_error", headers={"request-id": "req_500"}
        )
    )
    assert type(mapped) is ClaudeError
    assert (mapped.kind, mapped.retryable, mapped.status, mapped.request_id) == (
        "server",
        True,
        500,
        "req_500",
    )
    assert mapped.user_message == SERVER_MESSAGE
    assert from_anthropic(_status_error(anthropic.InternalServerError, 502)).kind == "server"


def test_400_is_a_bad_request_that_does_not_leak_the_provider_text() -> None:
    mapped = from_anthropic(
        _status_error(
            anthropic.BadRequestError,
            400,
            error_type="invalid_request_error",
            message="output_config.effort: not supported for claude-haiku-4-5",
        )
    )
    assert isinstance(mapped, ClaudeBadRequest)
    assert (mapped.kind, mapped.retryable, mapped.status) == ("bad_request", False, 400)
    assert mapped.user_message == BAD_REQUEST_MESSAGE
    assert mapped.detail == "output_config.effort: not supported for claude-haiku-4-5"


def test_404_413_and_422_are_bad_requests_with_their_own_text() -> None:
    not_found = from_anthropic(_status_error(anthropic.NotFoundError, 404, error_type="not_found_error"))
    assert isinstance(not_found, ClaudeBadRequest)
    assert (not_found.status, not_found.user_message) == (404, MODEL_UNAVAILABLE_MESSAGE)
    too_large = from_anthropic(_status_error(anthropic.RequestTooLargeError, 413))
    assert isinstance(too_large, ClaudeBadRequest)
    assert (too_large.status, too_large.user_message) == (413, TOO_LARGE_MESSAGE)
    unprocessable = from_anthropic(_status_error(anthropic.UnprocessableEntityError, 422))
    assert isinstance(unprocessable, ClaudeBadRequest) and unprocessable.user_message == BAD_REQUEST_MESSAGE
    conflict = from_anthropic(_status_error(anthropic.ConflictError, 409))
    assert isinstance(conflict, ClaudeBadRequest) and conflict.status == 409


def test_request_id_falls_back_to_the_body() -> None:
    body = _error_body("api_error", "boom", request_id="req_from_body")
    mapped = from_anthropic(_status_error(anthropic.InternalServerError, 500, body=body))
    assert mapped.request_id == "req_from_body"
    assert from_anthropic(_status_error(anthropic.InternalServerError, 500)).request_id is None


def test_a_status_error_without_a_json_body_still_maps() -> None:
    mapped = from_anthropic(_status_error(anthropic.RateLimitError, 429, body="<html>slow down</html>"))
    assert isinstance(mapped, ClaudeRateLimited)
    assert mapped.detail == "provider text"  # the exception message, since there is no JSON body


def test_detail_is_clipped() -> None:
    long = "x" * 5_000
    mapped = from_anthropic(
        _status_error(anthropic.BadRequestError, 400, error_type="invalid_request_error", message=long)
    )
    assert mapped.detail is not None and len(mapped.detail) == 300


# ───────────────────────── mapping everything else ─────────────────────────


def test_sdk_timeouts_and_our_own_deadline_are_timeouts() -> None:
    for exc in (anthropic.APITimeoutError(REQUEST), TimeoutError("deadline"), TimeoutError()):
        mapped = from_anthropic(exc)
        assert isinstance(mapped, ClaudeTimeout), exc
        assert (mapped.kind, mapped.retryable, mapped.user_message) == ("timeout", True, NETWORK_MESSAGE)


def test_connection_errors_are_not_confused_with_timeouts() -> None:
    mapped = from_anthropic(anthropic.APIConnectionError(request=REQUEST))
    assert isinstance(mapped, ClaudeConnectionError) and not isinstance(mapped, ClaudeTimeout)
    assert (mapped.kind, mapped.retryable, mapped.user_message) == ("connection", True, NETWORK_MESSAGE)


def test_missing_credentials_mean_not_configured() -> None:
    mapped = from_anthropic(anthropic.CredentialsError("no key"))
    assert isinstance(mapped, ClaudeNotConfigured)
    assert (mapped.kind, mapped.retryable, mapped.user_message) == ("not_configured", False, AUTH_MESSAGE)


def test_an_unparseable_api_response_is_a_retryable_server_problem() -> None:
    exc = anthropic.APIResponseValidationError(_response(200), body={"weird": 1})
    mapped = from_anthropic(exc)
    assert (mapped.kind, mapped.retryable, mapped.user_message) == ("server", True, SERVER_MESSAGE)


def test_unknown_exceptions_become_unknown_errors_with_a_log_detail() -> None:
    mapped = from_anthropic(ValueError("something odd"))
    assert (mapped.kind, mapped.retryable) == ("unknown", False)
    assert mapped.detail == "ValueError: something odd"
    assert "something odd" not in mapped.user_message


def test_a_claude_error_passes_through_unchanged() -> None:
    original = ClaudeOverloaded(3.0)
    assert from_anthropic(original) is original


def test_cancellation_is_never_wrapped() -> None:
    cancelled = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError) as caught:
        from_anthropic(cancelled)
    assert caught.value is cancelled
    with pytest.raises(KeyboardInterrupt):
        from_anthropic(KeyboardInterrupt())


# ───────────────────────── the class family ─────────────────────────


@pytest.mark.parametrize(
    ("error", "kind", "retryable", "message"),
    [
        (ClaudeNotConfigured(), "not_configured", False, AUTH_MESSAGE),
        (ClaudeAuthError(), "auth", False, AUTH_MESSAGE),
        (ClaudeRateLimited(), "rate_limited", True, BUSY_MESSAGE),
        (ClaudeOverloaded(), "overloaded", True, BUSY_MESSAGE),
        (ClaudeTimeout(), "timeout", True, NETWORK_MESSAGE),
        (ClaudeConnectionError(), "connection", True, NETWORK_MESSAGE),
        (ClaudeBadRequest(), "bad_request", False, BAD_REQUEST_MESSAGE),
        (ClaudeTruncated(), "truncated", False, TRUNCATED_MESSAGE),
        (ClaudeInvalidOutput(), "invalid_output", True, INCOMPLETE_MESSAGE),
        (ClaudeError(), "unknown", False, errors.UNKNOWN_MESSAGE),
    ],
)
def test_every_error_has_a_kind_a_retry_flag_and_a_ui_message(
    error: ClaudeError, kind: str, retryable: bool, message: str
) -> None:
    assert isinstance(error, ClaudeError) and isinstance(error, Exception)
    assert (error.kind, error.retryable, error.user_message) == (kind, retryable, message)
    assert str(error) == message
    assert kind in repr(error)


def test_user_messages_keep_the_legacy_wording_where_it_still_fits() -> None:
    assert BUSY_MESSAGE == "Claude is busy right now (rate limited or overloaded). Try again in a minute."
    assert NETWORK_MESSAGE == "Could not reach Claude (network error or timeout). Try again."
    assert INCOMPLETE_MESSAGE == "The model returned an incomplete answer. Try again."


def test_errors_keep_their_fields() -> None:
    err = ClaudeRateLimited(12.5, request_id="req_x", detail="why")
    assert (err.retry_after_s, err.request_id, err.detail, err.status) == (12.5, "req_x", "why", 429)
    truncated = ClaudeTruncated(8000, request_id="req_t")
    assert (truncated.max_tokens, truncated.request_id) == (8000, "req_t")
    invalid = ClaudeInvalidOutput(["$.name: missing required field", "$.age: expected integer"])
    assert invalid.issues == ("$.name: missing required field", "$.age: expected integer")
    assert ClaudeInvalidOutput().issues == ()


@pytest.mark.parametrize(
    "category", ["cyber", "bio", "frontier_llm", "reasoning_extraction", "general_harms"]
)
def test_refusals_have_a_message_per_category(category: str) -> None:
    err = ClaudeRefusal(category, request_id="req_r")
    assert (err.kind, err.retryable, err.category, err.request_id) == ("refusal", False, category, "req_r")
    assert err.user_message and err.user_message != errors._REFUSAL_DEFAULT
    assert "declined" in err.user_message


def test_a_refusal_without_a_known_category_uses_the_default_message() -> None:
    for category in (None, "something_new"):
        err = ClaudeRefusal(category)
        assert err.user_message == errors._REFUSAL_DEFAULT
    assert len({ClaudeRefusal(c).user_message for c in ("cyber", "bio", "general_harms")}) == 3
    assert ClaudeRefusal("bio", user_message="custom").user_message == "custom"


# ───────────────────────── spend ceiling ─────────────────────────


def test_spend_ceiling_is_a_503_app_error_with_retry_after() -> None:
    err = SpendCeilingReached(3 * 3600 + 10.2)
    assert isinstance(err, ServiceUnavailable) and isinstance(err, AppError)
    assert (err.status, err.code) == (503, "spend_ceiling_reached")
    assert err.user_message == SPEND_CEILING_MESSAGE
    assert "sample" in err.user_message
    assert err.to_body() == {"error": SPEND_CEILING_MESSAGE}
    assert err.retry_after_header() == "10811"  # whole seconds, rounded up


def test_spend_ceiling_without_a_retry_hint() -> None:
    err = SpendCeilingReached()
    assert err.retry_after_s is None and err.retry_after_header() is None


# ───────────────────────── Jev ─────────────────────────


@pytest.mark.parametrize(
    ("kind", "retryable"),
    [
        ("auth", False),
        ("rate_limit", True),
        ("bad_request", False),
        ("timeout", True),
        ("connection", True),
        ("server", True),
        ("invalid_response", False),
        ("disabled", False),
    ],
)
def test_jev_error_kinds_and_retryability(kind: Any, retryable: bool) -> None:
    err = JevError(kind, "boom", retry_after_s=2.0, request_id="rid", status=429)
    assert (err.kind, err.retryable, err.retry_after_s, err.request_id, err.status) == (
        kind,
        retryable,
        2.0,
        "rid",
        429,
    )
    assert str(err) == "boom"
    assert kind in repr(err)


def test_jev_error_has_a_default_message() -> None:
    err = JevError("timeout")
    assert "timeout" in str(err)
    assert (err.retry_after_s, err.request_id, err.status) == (None, None, None)
