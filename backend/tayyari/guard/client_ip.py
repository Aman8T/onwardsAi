"""Which client is this? The address the rate limiter keys on.

Behind FastAPI Cloud's proxy the TCP peer is the proxy, so the real client address comes from a header the
proxies add. A header the client can write itself must never be trusted, which gives these rules, tried in order:

1. ``header`` (when configured): a header the platform guarantees and overwrites (Cloudflare's
   ``CF-Connecting-IP``). If it holds a list, the rightmost entry (the one nearest to us) is used.
2. ``trusted_hops`` > 0: each trusted proxy appends the address it saw to ``X-Forwarded-For``, so the client
   is the entry ``trusted_hops`` from the *right*. Anything to its left is client-supplied and ignored. With
   fewer entries than hops, the request did not come through the expected chain, so this step is skipped.
3. The TCP peer, ``request.client.host`` (also when a header above is missing or is not an address).

Why the named header goes first (security review finding 6, checked on the live deployment). Taken alone a
header the client can set would be a hole: sending a random ``CF-Connecting-IP`` per request would give every
request a fresh limiter bucket. It is safe on FastAPI Cloud because the app is only reachable through
Cloudflare, and Cloudflare itself rejects a request that arrives with a client-sent ``CF-Connecting-IP``
(HTTP 403, error 1000). So the header the app sees is always the one Cloudflare wrote from the TCP peer it
accepted, and nothing the client sends can reach it. ``X-Forwarded-For`` is different: a client can prepend
anything to it, which is why step 2 only reads the entry ``trusted_hops`` from the right (the proxies' own
appends) and never the left. On a platform that is reachable without Cloudflare, switch the header off
(``TAYYARI_GUARD__CLIENT_IP_HEADER=none``) so that only the hop count is trusted. The app deliberately does
not check the Cloudflare edge address against Cloudflare's published ranges: the platform guarantee above
makes that check redundant and the ranges would be one more thing to keep current.

Addresses are returned in canonical text form (IPv4-mapped IPv6 unwrapped to IPv4, ports and brackets
stripped). `ip_subject` maps an address to what a per-client limit should count: an IPv6 address becomes its
/64 network, because a single household owns a whole /64 and can rotate through it for free. `ip_subjects`
adds the /48 network as a second subject, because an address block bigger than a household (a /48 is 65,536
/64s) lets one customer hop from /64 to /64; the rate limiter counts that wider bucket at a higher limit.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import HTTPConnection

_IPV6_PREFIX = 64
_IPV6_WIDE_PREFIX = 48


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


def ip_subjects(ip: str) -> list[str]:
    """Every subject a per-client limit counts for ``ip``, narrowest first.

    IPv4: just the address. IPv6: the /64 (exactly `ip_subject`) and then the /48 (``2001:db8:1::/48``), so a
    client hopping between /64s inside one /48 still lands in one wider bucket. Anything that is not an IP
    address (a test client's name) is a single unchanged subject. The first entry always equals
    ``ip_subject(ip)``, so existing keyed hashes of it stay valid.
    """
    subjects = [ip_subject(ip)]
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return subjects
    if isinstance(addr, ipaddress.IPv6Address):
        subjects.append(str(ipaddress.IPv6Network((addr, _IPV6_WIDE_PREFIX), strict=False)))
    return subjects


__all__ = ["client_ip", "ip_subject", "ip_subjects"]
