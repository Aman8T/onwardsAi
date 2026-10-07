"""Which client is this? The address the rate limiter keys on.

Behind FastAPI Cloud's proxy the TCP peer is the proxy, so the real client address comes from a header the
proxies add. A header the client can write itself must never be trusted, which gives these rules, tried in order:

1. ``header`` (when configured): a header the platform guarantees and overwrites (Cloudflare's
   ``CF-Connecting-IP``). If it holds a list, the rightmost entry (the one nearest to us) is used.
2. ``trusted_hops`` > 0: each trusted proxy appends the address it saw to ``X-Forwarded-For``, so the client
   is the entry ``trusted_hops`` from the *right*. Anything to its left is client-supplied and ignored. With
   fewer entries than hops, the request did not come through the expected chain, so this step is skipped.
3. The TCP peer, ``request.client.host`` (also when a header above is missing or is not an address).

Addresses are returned in canonical text form (IPv4-mapped IPv6 unwrapped to IPv4, ports and brackets
stripped). `ip_subject` maps an address to what a per-client limit should count: an IPv6 address becomes its
/64 network, because a single household owns a whole /64 and can rotate through it for free.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import HTTPConnection

_IPV6_PREFIX = 64


def _normalise(raw: str) -> str | None:
    """One header entry as a canonical IP string, or None when it is not an address.

    Accepts ``1.2.3.4``, ``1.2.3.4:5678``, ``::1``, ``[::1]``, ``[::1]:80`` and ``fe80::1%eth0``.
    """
    value = raw.strip().strip('"')
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        value = value[1:end]
    elif value.count(":") == 1:  # IPv4 with a port (a bare IPv6 address has at least two colons)
        value = value.split(":", 1)[0]
    value = value.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return str(addr)


def _entries(request: HTTPConnection, name: str) -> list[str]:
    """Every comma-separated entry of a header (repeated header lines joined), empties dropped."""
    joined = ",".join(request.headers.getlist(name))
    return [part.strip() for part in joined.split(",") if part.strip()]


def _peer(request: HTTPConnection) -> str | None:
    """The TCP peer. Not validated as an address (test clients report names like "testclient")."""
    client = request.client
    if client is None or not client.host:
        return None
    return _normalise(client.host) or client.host


def client_ip(request: HTTPConnection, *, trusted_hops: int, header: str | None) -> str | None:
    """The client's address, or None when it cannot be determined. See the module docstring for the rules."""
    if header:
        entries = _entries(request, header)
        if entries:
            found = _normalise(entries[-1])
            if found is not None:
                return found
    if trusted_hops > 0:
        entries = _entries(request, "x-forwarded-for")
        if len(entries) >= trusted_hops:
            found = _normalise(entries[-trusted_hops])
            if found is not None:
                return found
    return _peer(request)


def ip_subject(ip: str) -> str:
    """What a per-client limit counts: IPv4 as is, IPv6 as its /64 network (``2001:db8:1:2::/64``).

    Anything that is not an IP address (a test client's name) is returned unchanged.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((addr, _IPV6_PREFIX), strict=False))
    return str(addr)


__all__ = ["client_ip", "ip_subject"]
