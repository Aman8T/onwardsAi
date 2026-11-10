"""The Svix check of Resend's webhooks, in our own code: a good signature passes; a wrong secret, a changed body, a stale or
future timestamp, a missing header and malformed signatures fail; several signatures (a secret being rotated) pass when any
one matches. The secrets here are made up for the test."""

from __future__ import annotations

import base64

import pytest

from tayyari.inbox.svix import TOLERANCE_S, SignatureError, secret_bytes, sign, verify

SECRET = "whsec_" + base64.b64encode(b"test-secret-of-32-bytes-length!!").decode()
OTHER = "whsec_" + base64.b64encode(b"another-test-secret-32-bytes-ok!").decode()
NOW = 1_800_000_000
BODY = b'{"type":"email.received","data":{"email_id":"e1"}}'


def headers(signature: str, *, msg_id: str = "msg_1", stamp: int = NOW) -> dict[str, str]:
    return {"svix-id": msg_id, "svix-timestamp": str(stamp), "svix-signature": signature}


def test_a_genuine_call_passes() -> None:
    verify(SECRET, headers(sign(SECRET, "msg_1", str(NOW), BODY)), BODY, now=NOW)


def test_the_known_svix_vector() -> None:
    """The signature is base64(HMAC-SHA256(base64-decoded secret, "id.timestamp.body")), computed independently here."""
    import hashlib
    import hmac

    key = base64.b64decode(SECRET.removeprefix("whsec_"))
    expected = base64.b64encode(
        hmac.new(key, b"msg_1." + str(NOW).encode() + b"." + BODY, hashlib.sha256).digest()
    )
    assert sign(SECRET, "msg_1", str(NOW), BODY) == "v1," + expected.decode()


def test_a_secret_without_the_prefix_is_read_the_same() -> None:
    assert secret_bytes(SECRET) == secret_bytes(SECRET.removeprefix("whsec_"))


def test_a_wrong_secret_fails() -> None:
    with pytest.raises(SignatureError, match="no signature matches"):
        verify(SECRET, headers(sign(OTHER, "msg_1", str(NOW), BODY)), BODY, now=NOW)


def test_a_changed_body_or_id_fails() -> None:
    good = sign(SECRET, "msg_1", str(NOW), BODY)
    with pytest.raises(SignatureError):
        verify(SECRET, headers(good), BODY + b" ", now=NOW)
    with pytest.raises(SignatureError):
        verify(SECRET, headers(good, msg_id="msg_2"), BODY, now=NOW)


@pytest.mark.parametrize("skew", [TOLERANCE_S + 1, -(TOLERANCE_S + 1), 3600])
def test_a_stale_or_future_timestamp_fails_even_when_signed(skew: int) -> None:
    stamp = NOW - skew
    with pytest.raises(SignatureError, match="timestamp"):
        verify(SECRET, headers(sign(SECRET, "msg_1", str(stamp), BODY), stamp=stamp), BODY, now=NOW)


def test_the_edge_of_the_window_still_passes() -> None:
    stamp = NOW - TOLERANCE_S
    verify(SECRET, headers(sign(SECRET, "msg_1", str(stamp), BODY), stamp=stamp), BODY, now=NOW)


def test_any_one_of_several_signatures_is_enough() -> None:
    good = sign(SECRET, "msg_1", str(NOW), BODY)
    stale = sign(OTHER, "msg_1", str(NOW), BODY)
    verify(SECRET, headers(f"{stale} {good}"), BODY, now=NOW)
    verify(SECRET, headers(f"v2,abc {good}"), BODY, now=NOW)  # an unknown version is skipped


@pytest.mark.parametrize(
    "signature",
    ["", "v1,", "v1", "nonsense", "v2," + "A" * 44, "v1,!!!not base64!!!", "v1,AAAA v1,BBBB"],
)
def test_malformed_signatures_fail(signature: str) -> None:
    with pytest.raises(SignatureError):
        verify(SECRET, headers(signature), BODY, now=NOW)


@pytest.mark.parametrize("missing", ["svix-id", "svix-timestamp", "svix-signature"])
def test_a_missing_header_fails(missing: str) -> None:
    given = headers(sign(SECRET, "msg_1", str(NOW), BODY))
    del given[missing]
    with pytest.raises(SignatureError, match="missing"):
        verify(SECRET, given, BODY, now=NOW)


def test_a_timestamp_that_is_not_a_number_fails() -> None:
    given = headers(sign(SECRET, "msg_1", str(NOW), BODY))
    given["svix-timestamp"] = "yesterday"
    with pytest.raises(SignatureError, match="number"):
        verify(SECRET, given, BODY, now=NOW)


def test_a_secret_that_is_not_base64_fails_without_echoing_it() -> None:
    with pytest.raises(SignatureError) as caught:
        verify("whsec_%%%not-base64%%%", headers("v1,AAAA"), BODY, now=NOW)
    assert "%%%" not in str(caught.value)
