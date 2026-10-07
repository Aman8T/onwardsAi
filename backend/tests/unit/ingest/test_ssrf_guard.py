"""The SSRF guard's address and host judgement: which literals and names may never be fetched.

These tests never resolve a name for real: `dns` is a fake resolver that fails the test on an unregistered
lookup, and the "must not touch DNS" cases assert that the resolver was not called at all.
"""

from __future__ import annotations

import pytest

from tayyari.ingest.web import (
    NOT_FOUND_MSG,
    PRIVATE_MSG,
    FetchProblem,
    is_private_address,
    is_private_host,
    resolve_public,
)

PRIVATE_V4 = [
    # loopback and "this network"
    "127.0.0.1",
    "127.1.2.3",
    "127.255.255.254",
    "0.0.0.0",  # noqa: S104 - an address to refuse, not a bind address
    "0.1.2.3",
    # RFC 1918
    "10.0.0.1",
    "10.255.255.255",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.0.1",
    "192.168.255.255",
    # link-local, including the cloud metadata address
    "169.254.0.1",
    "169.254.169.254",
    "169.254.255.255",
    # carrier-grade NAT (100.100.100.200 is Alibaba's metadata service)
    "100.64.0.1",
    "100.100.100.200",
    "100.127.255.255",
    # multicast, reserved, broadcast
    "224.0.0.1",
    "239.255.255.255",
    "240.0.0.1",
    "255.255.255.255",
    # IETF protocol assignments, benchmarking, documentation ranges
    "192.0.0.1",
    "192.0.0.192",
    "198.18.0.1",
    "198.19.255.255",
    "192.0.2.1",
    "198.51.100.7",
    "203.0.113.9",
]

PUBLIC_V4 = [
    "93.184.216.34",
    "8.8.8.8",
    "1.1.1.1",
    "11.0.0.1",
    "172.15.255.255",  # just below 172.16.0.0/12
    "172.32.0.1",  # just above it
    "100.63.255.255",  # just below 100.64.0.0/10
    "100.128.0.1",
    "169.253.255.255",
    "169.255.0.1",
    "192.167.255.255",
    "192.169.0.1",
    "198.17.255.255",
    "198.20.0.1",
    "223.255.255.254",
]

PRIVATE_V6 = [
    "::1",
    "::",
    "fe80::1",
    "febf::1",
    "fe80::abcd%eth0",  # a zone id is ignored
    "fec0::1",  # deprecated site-local
    "fc00::1",
    "fd12:3456:789a::1",
    "fdff::1",
    "fd00:ec2::254",  # AWS's IPv6 metadata address
    "ff00::1",
    "ff02::1",
    # IPv4 addresses embedded in IPv6 notation are judged by the IPv4 rules or blocked outright
    "::ffff:127.0.0.1",
    "::ffff:7f00:1",
    "::ffff:10.0.0.1",
    "::ffff:169.254.169.254",
    "::127.0.0.1",
    "::10.0.0.1",
    "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
    "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
    "64:ff9b:1::1",
    "2002:7f00:1::1",  # 6to4 of 127.0.0.1
    "2002:a00:1::1",  # 6to4 of 10.0.0.1
    "2001::1",  # Teredo
    "2001:db8::1",  # documentation
    "100::1",  # discard-only
]

PUBLIC_V6 = [
    "2606:4700:4700::1111",
    "2001:4860:4860::8888",
    "2a00:1450:4001:81b::200e",
    "2620:fe::fe",
]


@pytest.mark.parametrize("address", PRIVATE_V4 + PRIVATE_V6)
def test_private_addresses_are_blocked(address: str) -> None:
    assert is_private_address(address) is True


@pytest.mark.parametrize("address", PUBLIC_V4 + PUBLIC_V6)
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_private_address(address) is False


@pytest.mark.parametrize(
    "not_an_address", ["", "example.com", "127.0.0.1.nip.io", "1.2.3", "256.1.1.1", "::g"]
)
def test_something_that_is_not_an_address_is_never_allowed(not_an_address: str) -> None:
    assert is_private_address(not_an_address) is True


# ----------------------------------------------------------------------------- hosts


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "localhost.",
        "app.localhost",
        "a.b.localhost",
        "printer.local",
        "Host.LOCAL",
        "metadata.google.internal",
        "nas.lan",
        "router.home.arpa",
        "x.y.home.arpa",
    ],
)
def test_private_names_are_blocked_without_a_lookup(host: str, no_dns) -> None:
    assert is_private_host(host) is True
    assert no_dns.calls == []


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "localhost.example.com",
        "mylocal.com",
        "notlocal.dev",
        "internal.example.com",
        "lan.example.com",
    ],
)
def test_ordinary_names_are_not_private_by_name(host: str) -> None:
    assert is_private_host(host) is False


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.1.2.3", "[::1]", "::1", "[::ffff:7f00:1]", "169.254.169.254", "[fe80::1]"]
)
def test_ip_literals_are_judged_by_address(host: str) -> None:
    assert is_private_host(host) is True


@pytest.mark.parametrize("host", ["8.8.8.8", "93.184.216.34", "[2606:4700:4700::1111]"])
def test_public_ip_literals_pass(host: str) -> None:
    assert is_private_host(host) is False


@pytest.mark.parametrize(
    "spelling",
    [
        "2130706433",  # decimal 127.0.0.1
        "0x7f000001",  # hex
        "0X7F000001",
        "017700000001",  # octal
        "0x7f.0.0.1",  # per-part hex
        "0x7f.1",  # hex head, short tail
        "127.1",  # shortened
        "127.0.1",
        "0177.0.0.1",  # per-part octal
        "0177.0.0.01",
        "127.000.000.001",  # zero padded = octal zeros and a one
        "0x",  # the empty hex number is 0, which is 0.0.0.0
        "2852039166",  # 169.254.169.254 in decimal
        "0xa9fea9fe",
        "10.1",  # 10.0.0.1
        "192.168.1",  # 192.168.0.1
        "3232235777",  # 192.168.1.1
        "0300.0250.0.1",  # 192.168.0.1 in octal
        "127.0.0.1.",  # trailing dot
    ],
)
def test_alternative_ipv4_spellings_are_judged_by_the_address_they_mean(spelling: str, no_dns) -> None:
    """The verdict must not depend on how the system resolver reads these (some do, some refuse them)."""
    assert is_private_host(spelling) is True
    assert no_dns.calls == []


@pytest.mark.parametrize("spelling", ["1.1.1.256", "1.2.3.4.5", "08.1.1.1", "256.1", "4294967296"])
def test_a_host_that_ends_in_a_number_but_is_no_address_is_refused(spelling: str, no_dns) -> None:
    assert is_private_host(spelling) is True
    assert no_dns.calls == []


@pytest.mark.parametrize("spelling", ["134744072", "0x08080808", "1.1", "93.184.216", "0135.0270.0330.042"])
def test_alternative_spellings_of_public_addresses_are_not_blocked_by_form_alone(spelling: str) -> None:
    assert is_private_host(spelling) is False


# ----------------------------------------------------------------------------- resolve_public


async def test_a_public_literal_is_returned_without_a_lookup(no_dns) -> None:
    assert await resolve_public("93.184.216.34") == ["93.184.216.34"]
    assert await resolve_public("[2606:4700:4700::1111]") == ["2606:4700:4700::1111"]
    assert no_dns.calls == []


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "[::1]", "10.0.0.1", "localhost", "0x7f.0.0.1", "foo.internal", "[::ffff:7f00:1]"]
)
async def test_a_private_host_is_refused_before_any_lookup(host: str, no_dns) -> None:
    with pytest.raises(FetchProblem, match="private"):
        await resolve_public(host)
    assert no_dns.calls == []


async def test_a_name_resolving_to_a_public_address_is_allowed(dns) -> None:
    dns.answer("example.test", ["93.184.216.34"])
    assert await resolve_public("example.test") == ["93.184.216.34"]
    assert dns.calls == ["example.test"]


async def test_answers_come_back_deduplicated_with_ipv4_first(dns) -> None:
    dns.answer("dual.test", ["2606:4700:4700::1111", "93.184.216.34", "93.184.216.34", "8.8.8.8"])
    assert await resolve_public("dual.test") == ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"]


@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],  # 127.0.0.1.nip.io, localtest.me
        ["169.254.169.254"],
        ["10.0.0.5"],
        ["::1"],
        ["::ffff:127.0.0.1"],
        ["fd00:ec2::254"],
        ["93.184.216.34", "10.0.0.5"],  # one bad answer among good ones still blocks the name
        ["2606:4700:4700::1111", "127.0.0.1"],
        ["100.64.0.1"],
    ],
)
async def test_a_name_with_any_private_answer_is_refused(answers: list[str], dns) -> None:
    dns.answer("rebound.test", answers)
    with pytest.raises(FetchProblem) as exc:
        await resolve_public("rebound.test")
    assert str(exc.value) == PRIVATE_MSG


async def test_an_unknown_name_is_reported_as_not_found(dns) -> None:
    dns.missing("gone.test")
    with pytest.raises(FetchProblem) as exc:
        await resolve_public("gone.test")
    assert str(exc.value) == NOT_FOUND_MSG


async def test_an_empty_answer_is_reported_as_not_found(dns) -> None:
    dns.answer("empty.test", [])
    with pytest.raises(FetchProblem) as exc:
        await resolve_public("empty.test")
    assert str(exc.value) == NOT_FOUND_MSG


async def test_a_blocked_name_is_logged_with_the_host_only(dns, record_logs) -> None:
    dns.answer("rebound.test", ["127.0.0.1"])
    with pytest.raises(FetchProblem):
        await resolve_public("rebound.test")
    [event] = record_logs.named("web_fetch_blocked")
    assert event["host"] == "rebound.test"
    assert event["reason"] == "private_dns_answer"
    assert event["level"] == "warning"


@pytest.mark.parametrize(
    ("host", "logged"),
    [
        ("169.254.169.254", "169[.]254[.]169[.]254"),
        ("93.184.216.34", "93[.]184[.]216[.]34"),
        ("127.1", "127[.]1"),
        ("0x7f.0.0.1", "0x7f[.]0[.]0[.]1"),
        ("example.com", "example.com"),
        ("metadata.google.internal", "metadata.google.internal"),
        ("localhost", "localhost"),
    ],
)
def test_ip_hosts_are_defanged_for_the_log_so_the_redactor_keeps_them(host: str, logged: str) -> None:
    from tayyari.core.redact import redact_pii
    from tayyari.ingest.web import log_host

    assert log_host(host) == logged
    assert redact_pii(log_host(host)) == logged  # the plain form of 169.254.169.254 would become "[phone]"


async def test_a_blocked_ip_literal_is_logged_in_a_form_that_survives_redaction(no_dns, record_logs) -> None:
    with pytest.raises(FetchProblem):
        await resolve_public("169.254.169.254")
    [event] = record_logs.named("web_fetch_blocked")
    assert event["host"] == "169[.]254[.]169[.]254"
    assert event["reason"] == "private_host"
