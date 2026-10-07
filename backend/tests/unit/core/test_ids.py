from __future__ import annotations

import re
import secrets
import time
import uuid

import pytest

from tayyari.core import ids
from tayyari.core.ids import (
    is_sample_id,
    is_valid_id,
    new_profile_id,
    new_session_id,
    uuid7,
)
from tayyari.core.limits import ID_MAX_CHARS

HEX32 = re.compile(r"[0-9a-f]{32}")


def test_profile_id_is_128_bit_hex() -> None:
    pid = new_profile_id(sample=False)
    assert HEX32.fullmatch(pid)
    assert not is_sample_id(pid)


def test_sample_profile_id_is_prefixed() -> None:
    pid = new_profile_id(sample=True)
    assert pid.startswith("sample-")
    assert HEX32.fullmatch(pid.removeprefix("sample-"))
    assert is_sample_id(pid)


def test_session_id_is_128_bit_hex() -> None:
    assert HEX32.fullmatch(new_session_id())


def test_ids_do_not_collide() -> None:
    made = {new_profile_id(sample=False) for _ in range(2000)} | {new_session_id() for _ in range(2000)}
    assert len(made) == 4000


def test_generated_ids_pass_validation() -> None:
    assert is_valid_id(new_profile_id(sample=False))
    assert is_valid_id(new_profile_id(sample=True))
    assert is_valid_id(new_session_id())


def test_ids_come_from_the_secrets_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids.secrets, "token_hex", lambda n: "ab" * n)
    assert new_profile_id(sample=False) == "ab" * 16
    assert new_session_id() == "ab" * 16


@pytest.mark.parametrize(
    "good",
    [
        "a",
        "A1",
        "abc-def_ghi",
        "sample-0123456789abcdef",
        "mfx9k2-a1b2c3",  # the old base-36 + hex ids must stay readable
        "0" * ID_MAX_CHARS,
        "Z" * ID_MAX_CHARS,
    ],
)
def test_legacy_shaped_ids_are_valid(good: str) -> None:
    assert is_valid_id(good)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "-abc",
        "_abc",
        "abc\n",
        "abc def",
        "../etc/passwd",
        "a/b",
        "a.b",
        "é",
        "a" * (ID_MAX_CHARS + 1),
        "%2e%2e",
    ],
)
def test_other_ids_are_invalid(bad: str) -> None:
    assert not is_valid_id(bad)


@pytest.mark.parametrize("not_a_string", [None, 5, b"abc", ["abc"], object()])
def test_non_strings_are_invalid(not_a_string: object) -> None:
    assert not is_valid_id(not_a_string)


def test_validation_agrees_with_the_legacy_store_on_everything_but_a_trailing_newline() -> None:
    legacy = pytest.importorskip("tayyari.legacy.store")
    samples = ["a", "A1", "ab-c_d", "-x", "x y", "x/y", "", "x" * 81, "x" * 82, "é", "sample-abc", "9"]
    for s in samples:
        assert is_valid_id(s) == legacy._valid_id(s), s
    # the one deliberate difference: `$` in the legacy regex accepted a trailing newline
    assert legacy._valid_id("abc\n") is True
    assert is_valid_id("abc\n") is False


# ───────────────────────────── uuid7 ─────────────────────────────


def test_uuid7_version_and_variant() -> None:
    u = uuid7()
    assert isinstance(u, uuid.UUID)
    assert u.version == 7
    assert u.variant == uuid.RFC_4122
    assert str(u)[14] == "7"
    assert str(u)[19] in "89ab"


def test_uuid7_carries_the_timestamp() -> None:
    before = int(time.time() * 1000)
    u = uuid7()
    after = int(time.time() * 1000)
    embedded = int.from_bytes(u.bytes[:6], "big")
    assert before <= embedded <= after
    assert int.from_bytes(uuid7(timestamp_ms=1_700_000_000_123).bytes[:6], "big") == 1_700_000_000_123


def test_uuid7_sorts_by_time() -> None:
    stamps = [1_000, 1_001, 5_000, 1_700_000_000_000, 1_700_000_000_001]
    made = [uuid7(timestamp_ms=t) for t in stamps]
    assert made == sorted(made)
    assert [u.bytes for u in made] == sorted(u.bytes for u in made)


def test_uuid7_is_unique_within_one_millisecond() -> None:
    made = {uuid7(timestamp_ms=1_700_000_000_000) for _ in range(5000)}
    assert len(made) == 5000


def test_uuid7_matches_the_rfc_9562_test_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    # RFC 9562 appendix A.6: unix_ts_ms 0x017F22E279B0, rand_a 0xCC3, rand_b 0x18C4DC0C0C07398F
    rand = (0xCC3 << 68) | 0x18C4DC0C0C07398F
    monkeypatch.setattr(secrets, "token_bytes", lambda n: rand.to_bytes(n, "big"))
    assert str(uuid7(timestamp_ms=0x017F22E279B0)) == "017f22e2-79b0-7cc3-98c4-dc0c0c07398f"


def test_uuid7_rejects_a_negative_timestamp() -> None:
    with pytest.raises(ValueError):
        uuid7(timestamp_ms=-1)
