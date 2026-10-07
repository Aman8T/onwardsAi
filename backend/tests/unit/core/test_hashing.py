from __future__ import annotations

import hashlib
import json
import math

import pytest

from tayyari.core.hashing import canonical_json, hmac_hex, sha256_hex


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    assert (
        canonical_json({"b": 1, "a": [1, 2, {"d": None, "c": True}]})
        == b'{"a":[1,2,{"c":true,"d":null}],"b":1}'
    )


def test_canonical_json_ignores_insertion_order() -> None:
    first = {"x": 1, "y": {"b": 2, "a": 1}}
    second = {"y": {"a": 1, "b": 2}, "x": 1}
    assert canonical_json(first) == canonical_json(second)


def test_canonical_json_keeps_non_ascii_as_utf8() -> None:
    out = canonical_json({"name": "Zoë 日本"})
    assert out == '{"name":"Zoë 日本"}'.encode()
    assert b"\\u" not in out


def test_canonical_json_is_valid_json_that_round_trips() -> None:
    data = {"a": [1, 2.5, "x", None, False], "b": {"c": "d"}}
    assert json.loads(canonical_json(data)) == data


def test_canonical_json_distinguishes_values() -> None:
    assert canonical_json({"a": 1}) != canonical_json({"a": "1"})
    assert canonical_json([1, 2]) != canonical_json([2, 1])


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_canonical_json_refuses_non_finite_numbers(bad: float) -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": bad})


def test_canonical_json_refuses_unserialisable_values() -> None:
    with pytest.raises(TypeError):
        canonical_json({"x": {1, 2}})


def test_sha256_hex_known_vectors() -> None:
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert sha256_hex("é") == hashlib.sha256("é".encode()).hexdigest()
    assert len(sha256_hex(b"x")) == 64


def test_state_hash_is_stable_for_equal_states() -> None:
    assert sha256_hex(canonical_json({"resume": "r", "notes": None})) == sha256_hex(
        canonical_json({"notes": None, "resume": "r"})
    )


def test_hmac_hex_matches_rfc_4231_test_case_2() -> None:
    full = hmac_hex(b"Jefe", "what do ya want for nothing?", length=64)
    assert full == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"


def test_hmac_hex_truncates_and_defaults_to_16() -> None:
    full = hmac_hex(b"key", "value", length=64)
    assert hmac_hex(b"key", "value") == full[:16]
    assert hmac_hex(b"key", "value", length=8) == full[:8]
    assert len(hmac_hex(b"key", "value")) == 16


def test_hmac_hex_depends_on_key_and_value() -> None:
    assert hmac_hex(b"k1", "ip") != hmac_hex(b"k2", "ip")
    assert hmac_hex(b"k1", "ip1") != hmac_hex(b"k1", "ip2")
    assert hmac_hex(b"k1", "ip") == hmac_hex(b"k1", "ip")


@pytest.mark.parametrize("length", [0, -1, 65])
def test_hmac_hex_rejects_bad_lengths(length: int) -> None:
    with pytest.raises(ValueError):
        hmac_hex(b"k", "v", length=length)
