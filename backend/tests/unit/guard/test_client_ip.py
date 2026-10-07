from __future__ import annotations

from collections.abc import Sequence

import pytest
from starlette.requests import Request

from tayyari.guard.client_ip import client_ip, ip_subject, ip_subjects

PEER = "10.1.2.3"


def make(headers: Sequence[tuple[str, str]] = (), peer: str | None = PEER) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "client": (peer, 5000) if peer is not None else None,
    }
    return Request(scope)


def xff(value: str, *, hops: int = 1, peer: str | None = PEER) -> str | None:
    return client_ip(make([("X-Forwarded-For", value)], peer), trusted_hops=hops, header=None)


# ───────────────────────────── X-Forwarded-For and trusted hops ─────────────────────────────


@pytest.mark.parametrize(
    ("value", "hops", "expected"),
    [
        ("203.0.113.5", 1, "203.0.113.5"),
        ("198.51.100.1, 203.0.113.5", 1, "203.0.113.5"),
        ("198.51.100.1, 203.0.113.5", 2, "198.51.100.1"),
        ("1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4", 1, "4.4.4.4"),
        ("1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4", 3, "2.2.2.2"),
        ("1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4", 4, "1.1.1.1"),
        ("1.1.1.1,2.2.2.2,3.3.3.3", 3, "1.1.1.1"),  # no spaces
        ("  1.1.1.1 ,\t2.2.2.2  ", 1, "2.2.2.2"),
    ],
)
def test_the_client_is_the_entry_that_many_hops_from_the_right(value: str, hops: int, expected: str) -> None:
    assert xff(value, hops=hops) == expected


def test_what_the_client_put_on_the_left_is_ignored() -> None:
    # A client that sends its own X-Forwarded-For only controls the entries to the left of the proxies' own.
    assert xff("9.9.9.9, 8.8.8.8, 203.0.113.5", hops=1) == "203.0.113.5"
    assert xff("6.6.6.6, 203.0.113.5", hops=1) == "203.0.113.5"


def test_fewer_entries_than_hops_falls_back_to_the_peer() -> None:
    assert xff("203.0.113.5", hops=2) == PEER
    assert xff("203.0.113.5", hops=5) == PEER


def test_zero_hops_means_xff_is_not_trusted_at_all() -> None:
    assert xff("203.0.113.5", hops=0) == PEER


def test_a_missing_or_empty_header_falls_back_to_the_peer() -> None:
    assert client_ip(make(), trusted_hops=1, header=None) == PEER
    assert xff("", hops=1) == PEER
    assert xff(" , ,", hops=1) == PEER


def test_empty_entries_are_dropped_before_counting() -> None:
    assert xff("1.1.1.1,,2.2.2.2,", hops=2) == "1.1.1.1"


def test_repeated_header_lines_are_joined_in_order() -> None:
    request = make([("X-Forwarded-For", "1.1.1.1"), ("X-Forwarded-For", "2.2.2.2, 3.3.3.3")])
    assert client_ip(request, trusted_hops=1, header=None) == "3.3.3.3"
    assert client_ip(request, trusted_hops=3, header=None) == "1.1.1.1"


@pytest.mark.parametrize(
    "junk", ["unknown", "not-an-ip", "999.1.1.1", "_hidden", "1.2.3", "1.2.3.4.5", "evil<script>"]
)
def test_an_entry_that_is_not_an_address_falls_back_to_the_peer(junk: str) -> None:
    assert xff(f"1.1.1.1, {junk}", hops=1) == PEER


# ───────────────────────────── address formats ─────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.5:51234", "203.0.113.5"),
        ("[2001:db8::7]", "2001:db8::7"),
        ("[2001:db8::7]:443", "2001:db8::7"),
        ("2001:DB8:0:0:0:0:0:7", "2001:db8::7"),
        ("2001:db8::7", "2001:db8::7"),
        ("::ffff:203.0.113.5", "203.0.113.5"),  # IPv4-mapped IPv6 is the IPv4 address
        ("[::ffff:203.0.113.5]:80", "203.0.113.5"),
        ('"203.0.113.5"', "203.0.113.5"),
        ("fe80::1%eth0", "fe80::1"),
        ("0203.0.113.5", None),  # leading zero: ambiguous (octal), not an address
    ],
)
def test_entries_are_normalised(raw: str, expected: str | None) -> None:
    assert xff(raw, hops=1) == (expected if expected is not None else PEER)


def test_the_same_client_in_different_spellings_is_one_address() -> None:
    assert xff("::ffff:203.0.113.5") == xff("203.0.113.5:80") == xff("203.0.113.5")


# ───────────────────────────── the named header ─────────────────────────────


def named(
    value: str, *, header: str = "CF-Connecting-IP", hops: int = 1, xff_value: str | None = None
) -> str | None:
    headers = [(header, value)]
    if xff_value is not None:
        headers.append(("X-Forwarded-For", xff_value))
    return client_ip(make(headers), trusted_hops=hops, header=header)


def test_the_named_header_wins_over_xff() -> None:
    assert named("203.0.113.50", xff_value="198.51.100.1, 192.0.2.9") == "203.0.113.50"


def test_the_name_is_case_insensitive() -> None:
    request = make([("cf-connecting-ip", "203.0.113.50")])
    assert client_ip(request, trusted_hops=0, header="CF-CONNECTING-IP") == "203.0.113.50"


def test_a_list_in_the_named_header_uses_the_entry_nearest_to_us() -> None:
    assert named("6.6.6.6, 203.0.113.50") == "203.0.113.50"


def test_the_named_header_works_for_ipv6_and_ports() -> None:
    assert named("[2001:db8::7]:443") == "2001:db8::7"
    assert named("203.0.113.50:9999") == "203.0.113.50"


def test_a_missing_named_header_falls_back_to_xff_then_the_peer() -> None:
    request = make([("X-Forwarded-For", "198.51.100.1, 192.0.2.9")])
    assert client_ip(request, trusted_hops=1, header="CF-Connecting-IP") == "192.0.2.9"
    assert client_ip(request, trusted_hops=0, header="CF-Connecting-IP") == PEER
    assert client_ip(make(), trusted_hops=1, header="CF-Connecting-IP") == PEER


def test_an_unusable_named_header_falls_back_too() -> None:
    assert named("unknown", xff_value="198.51.100.1, 192.0.2.9") == "192.0.2.9"
    assert named("", xff_value=None) == PEER


def test_a_blank_header_name_is_the_same_as_none() -> None:
    request = make([("X-Forwarded-For", "192.0.2.9")])
    assert client_ip(request, trusted_hops=1, header="") == "192.0.2.9"
    assert client_ip(request, trusted_hops=1, header=None) == "192.0.2.9"


# ───────────────────────────── the peer ─────────────────────────────


def test_no_client_at_all_is_none() -> None:
    assert client_ip(make(peer=None), trusted_hops=1, header=None) is None
    assert client_ip(make([("X-Forwarded-For", "garbage")], peer=None), trusted_hops=1, header=None) is None


def test_the_peer_is_returned_even_when_it_is_not_an_ip() -> None:
    assert client_ip(make(peer="testclient"), trusted_hops=1, header=None) == "testclient"


def test_an_ipv4_mapped_peer_is_unwrapped() -> None:
    assert client_ip(make(peer="::ffff:198.51.100.7"), trusted_hops=0, header=None) == "198.51.100.7"


# ───────────────────────────── ip_subject ─────────────────────────────


def test_ipv4_is_its_own_subject() -> None:
    assert ip_subject("203.0.113.5") == "203.0.113.5"


def test_ipv6_is_counted_by_its_slash_64() -> None:
    assert ip_subject("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == "2001:db8:1:2::/64"
    assert ip_subject("2001:db8:1:2::1") == ip_subject("2001:db8:1:2:ffff::") == "2001:db8:1:2::/64"
    assert ip_subject("2001:db8:1:3::1") != ip_subject("2001:db8:1:2::1")


def test_something_that_is_not_an_ip_is_unchanged() -> None:
    assert ip_subject("testclient") == "testclient"


# ───────────────────────────── ip_subjects ─────────────────────────────


def test_ipv4_has_one_subject() -> None:
    assert ip_subjects("203.0.113.5") == ["203.0.113.5"]


def test_ipv6_has_the_slash_64_and_the_slash_48() -> None:
    assert ip_subjects("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == ["2001:db8:1:2::/64", "2001:db8:1::/48"]


def test_the_first_subject_is_always_the_one_the_per_client_limits_already_used() -> None:
    for ip in ("203.0.113.5", "2001:db8:1:2::1", "::1", "testclient"):
        assert ip_subjects(ip)[0] == ip_subject(ip)


def test_slash_64s_inside_one_slash_48_share_the_second_subject() -> None:
    first = ip_subjects("2001:db8:1:2::1")
    other = ip_subjects("2001:db8:1:ffff:1234::9")
    assert first[0] != other[0]  # different households
    assert first[1] == other[1] == "2001:db8:1::/48"  # one customer's block


def test_different_slash_48s_have_different_second_subjects() -> None:
    assert ip_subjects("2001:db8:1:2::1")[1] != ip_subjects("2001:db8:2:2::1")[1]


def test_the_slash_48_subject_never_equals_a_slash_64_subject() -> None:
    subjects = ip_subjects("2001:db8:1::1")  # a /64 that starts exactly where its /48 does
    assert subjects == ["2001:db8:1::/64", "2001:db8:1::/48"]
    assert subjects[0] != subjects[1]


def test_something_that_is_not_an_ip_has_one_unchanged_subject() -> None:
    assert ip_subjects("testclient") == ["testclient"]


# ───────────────────────────── the header-first order (security review finding 6) ─────────────────────────────


def test_a_forged_xff_prefix_does_not_change_the_address_from_the_three_hop_fallback() -> None:
    # Cloudflare edge address, then two internal hops: the client is three entries from the right, and
    # whatever the client put on the left of that is ignored, however much of it there is.
    chain = "9.9.9.9, 8.8.8.8, 7.7.7.7, 203.0.113.5, 172.68.1.1, 10.0.0.7, 10.0.0.8"
    assert xff(chain, hops=4) == "203.0.113.5"
    assert xff("203.0.113.5, 172.68.1.1, 10.0.0.7", hops=3) == "203.0.113.5"
    assert xff("6.6.6.6, 203.0.113.5, 172.68.1.1, 10.0.0.7", hops=3) == "203.0.113.5"


def test_the_named_header_is_still_tried_before_xff() -> None:
    # Cloudflare rejects a client-sent CF-Connecting-IP (403, error 1000), so the value the app sees is the
    # edge's own and is trusted first; see the module docstring.
    request = make(
        [
            ("CF-Connecting-IP", "203.0.113.5"),
            ("X-Forwarded-For", "6.6.6.6, 6.6.6.7, 203.0.113.9, 172.68.1.1, 10.0.0.7"),
        ]
    )
    assert client_ip(request, trusted_hops=3, header="cf-connecting-ip") == "203.0.113.5"
