"""The sentences the person sees for a failure: typed messages, and parity with the legacy humaniser where it still applies."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tayyari.analysis.failures import (
    CANCELLED_MESSAGE,
    GENERIC_MESSAGE,
    READER_AUTH_LINE,
    is_auth_error,
    is_spend_error,
    reader_error_line,
    user_message,
)
from tayyari.core.errors import RateLimited
from tayyari.llm.errors import (
    AUTH_MESSAGE,
    BILLING_MESSAGE,
    BUSY_MESSAGE,
    INCOMPLETE_MESSAGE,
    NETWORK_MESSAGE,
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
    SpendCeilingReached,
)

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "legacy" / "humanize_error.json"


def _legacy(name: str) -> str:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    return str(next(c["output"] for c in cases if c["name"] == name))


def test_typed_errors_carry_their_own_sentence() -> None:
    assert user_message(ClaudeRateLimited()) == BUSY_MESSAGE
    assert user_message(ClaudeOverloaded()) == BUSY_MESSAGE
    assert user_message(ClaudeTimeout()) == NETWORK_MESSAGE
    assert user_message(ClaudeConnectionError()) == NETWORK_MESSAGE
    assert user_message(ClaudeInvalidOutput(["bad"])) == INCOMPLETE_MESSAGE
    assert user_message(ClaudeAuthError()) == AUTH_MESSAGE
    assert user_message(ClaudeAuthError(status=402, user_message=BILLING_MESSAGE)) == BILLING_MESSAGE
    assert user_message(ClaudeTruncated()) == ClaudeTruncated().user_message
    assert user_message(ClaudeRefusal("cyber")) == ClaudeRefusal("cyber").user_message
    assert user_message(ClaudeBadRequest()) == ClaudeBadRequest().user_message


@pytest.mark.parametrize(
    ("legacy_case", "error"),
    [
        ("rate_limited", ClaudeRateLimited()),
        ("overloaded", ClaudeOverloaded()),
        ("timeout", ClaudeTimeout()),
        ("connection_error", ClaudeConnectionError()),
        ("incomplete", ClaudeInvalidOutput(["no JSON object"])),
        ("cancelled", asyncio.CancelledError()),
    ],
)
def test_same_sentence_as_the_legacy_humaniser(legacy_case: str, error: BaseException) -> None:
    assert user_message(error) == _legacy(legacy_case)


def test_the_auth_sentence_changed_on_purpose() -> None:
    # Legacy told a visitor to set ANTHROPIC_API_KEY; they cannot, so the new text names the server's key.
    assert _legacy("auth_key") != AUTH_MESSAGE
    assert "ANTHROPIC_API_KEY" not in AUTH_MESSAGE


def test_app_errors_use_their_user_message() -> None:
    assert user_message(SpendCeilingReached()) == SpendCeilingReached.user_message
    assert user_message(RateLimited()) == RateLimited.user_message


def test_a_timeout_of_our_own_reads_as_a_network_problem() -> None:
    assert user_message(TimeoutError()) == NETWORK_MESSAGE


def test_unknown_exceptions_never_leak_their_text() -> None:
    assert user_message(ValueError("secret resume text")) == GENERIC_MESSAGE
    assert user_message(None) == GENERIC_MESSAGE
    assert (
        user_message(ClaudeError("unknown", detail="provider said resume text"))
        != "provider said resume text"
    )
    assert user_message(asyncio.CancelledError()) == CANCELLED_MESSAGE


def test_auth_and_spend_classifiers() -> None:
    assert is_auth_error(ClaudeAuthError())
    assert is_auth_error(ClaudeNotConfigured())
    assert not is_auth_error(ClaudeRateLimited())
    assert not is_auth_error(ValueError("401"))
    assert not is_auth_error(None)
    assert is_spend_error(SpendCeilingReached())
    assert not is_spend_error(ClaudeAuthError())


def test_reader_error_line() -> None:
    assert reader_error_line(ClaudeAuthError()) == READER_AUTH_LINE
    assert reader_error_line(ClaudeNotConfigured()) == READER_AUTH_LINE
    line = reader_error_line(ClaudeRateLimited())
    assert len(line) <= 64
    assert not line.endswith(".")
    assert line.startswith("Claude is busy right now")
    billing = reader_error_line(ClaudeAuthError(status=402, user_message=BILLING_MESSAGE))
    assert billing != READER_AUTH_LINE
    assert billing.startswith("The server's Anthropic account has a billing problem")
    assert reader_error_line(ValueError("x")) == "Something went wrong"
