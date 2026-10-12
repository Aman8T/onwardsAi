from __future__ import annotations

import pytest

from tayyari.core import errors
from tayyari.core.errors import (
    AppError,
    BadRequest,
    ClientClosed,
    Conflict,
    NotFound,
    PayloadTooLarge,
    PaymentRequired,
    RateLimited,
    ServiceUnavailable,
    Unauthorized,
)

SUBCLASSES = [
    (BadRequest, 400, "bad_request"),
    (NotFound, 404, "not_found"),
    (PayloadTooLarge, 413, "payload_too_large"),
    (RateLimited, 429, "rate_limited"),
    (ServiceUnavailable, 503, "service_unavailable"),
    (ClientClosed, 499, "client_closed"),
    (Unauthorized, 401, "unauthorized"),
    (Conflict, 409, "conflict"),
]


@pytest.mark.parametrize(("cls", "status", "code"), SUBCLASSES)
def test_subclass_defaults(cls: type[AppError], status: int, code: str) -> None:
    err = cls()
    assert isinstance(err, AppError)
    assert isinstance(err, Exception)
    assert (err.status, err.code) == (status, code)
    assert err.user_message
    assert err.retry_after_s is None
    assert str(err) == err.user_message
    assert err.to_body() == {"error": err.user_message}


def test_payment_required_is_a_402_whose_body_is_a_code_and_a_reason() -> None:
    err = PaymentRequired()
    assert isinstance(err, AppError) and (err.status, err.code, err.reason) == (
        402,
        "payment_required",
        "no_credits",
    )
    assert err.to_body() == {"error": "payment_required", "reason": "no_credits"}
    assert (
        err.user_message and "free posting" in err.user_message
    )  # the sentence a stream's error event shows
    assert str(err) == err.user_message and err.retry_after_s is None
    assert PaymentRequired("pass_expired").to_body() == {
        "error": "payment_required",
        "reason": "pass_expired",
    }
    assert PaymentRequired("x", user_message="Pay up.", detail="why").user_message == "Pay up."


def test_base_class_is_a_500() -> None:
    err = AppError()
    assert (err.status, err.code) == (500, "internal_error")


def test_message_and_fields_can_be_overridden_per_instance() -> None:
    err = RateLimited("Slow down.", retry_after_s=12.2, detail="analyze/minute", code="analyze_minute")
    assert err.user_message == "Slow down."
    assert err.retry_after_s == 12.2
    assert err.detail == "analyze/minute"
    assert err.code == "analyze_minute"
    assert err.status == 429
    assert str(err) == "Slow down."
    # the class defaults are untouched
    assert RateLimited().code == "rate_limited"
    assert RateLimited().retry_after_s is None
    assert RateLimited().user_message != "Slow down."


def test_status_can_be_overridden_on_the_base_class() -> None:
    assert AppError("nope", status=418, code="teapot").status == 418


@pytest.mark.parametrize(
    ("seconds", "header"), [(None, None), (0.2, "1"), (1, "1"), (1.01, "2"), (59.0, "59"), (3600.4, "3601")]
)
def test_retry_after_header_rounds_up(seconds: float | None, header: str | None) -> None:
    assert RateLimited(retry_after_s=seconds).retry_after_header() == header


def test_exceptions_can_be_raised_and_caught_as_app_error() -> None:
    with pytest.raises(AppError) as excinfo:
        raise PayloadTooLarge("That file is larger than 10 MB.")
    assert excinfo.value.status == 413
    assert excinfo.value.to_body() == {"error": "That file is larger than 10 MB."}


def test_repr_names_the_class_and_status() -> None:
    text = repr(NotFound("Profile not found"))
    assert "NotFound" in text and "404" in text and "Profile not found" in text


def test_the_module_imports_no_web_framework() -> None:
    with open(errors.__file__, encoding="utf-8") as f:
        source = f.read().lower()
    assert "import fastapi" not in source and "from fastapi" not in source
    assert "import starlette" not in source and "from starlette" not in source
