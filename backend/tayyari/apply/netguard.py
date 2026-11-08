"""The two-layer network guard of an apply browser: nothing reaches an employer before the person approved it.

Ported from ``experiments/auto_apply/common/guard.py`` (measured there: the guard held on 42 of 42 probes). Standard
library only: the worker builds the extension and the route handler calls `decide`, and both are unit-tested here.

Layer 1 is a Manifest V3 extension (declarativeNetRequest) loaded into the Browser Use browser. It sees every request,
including those of service workers, which a Playwright route on a CDP context does not. A static extension cannot be
lifted for one host at run time, so there are two variants (`rules`):

    preview   every write (any method but GET, HEAD and OPTIONS) is blocked for every resource type;
    submit    writes are allowed only to the ATS's own submit and upload hosts (``allow_write_hosts``).

Both variants block every CAPTCHA and bot-challenge provider (rule 6 of the spec: a challenge is never loaded, so it is
never solved), and both allow the read-only POSTs a form needs just to render (Ashby loads its posting through GraphQL
POSTs; its submit and upload operations are not listed and stay blocked).

Layer 2 is the Playwright route handler (`apply.browser.PlaywrightDriver`), which asks `decide` about every request.
It blocks every write until the submit path calls ``allow_submit_hosts`` after the submitting marker, and it blocks any
main-frame navigation off the form's site (rule 3: never follow a link found in a page). The probe request is the one
thing it lets through, so that a 'BLOCKED' probe proves layer 1 is live.

WebSockets and WebTransport are writes too, whatever their handshake's method: a page script could stream typed values
over one. Layer 1 blocks those resource types for every method (`RULE_SOCKETS`, outside the submit hosts in the submit
variant), and layer 2 closes every socket `socket_decision` does not allow (Playwright's ``route_web_socket``, since a
context route never sees sockets).

HEAD and OPTIONS count as reads in both layers, as they did in the measured experiment: they carry no form data beyond
what a GET could put in its query string, and CORS preflights use OPTIONS.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Collection, Sequence
from typing import Any, Final, Literal
from urllib.parse import urlsplit

Decision = Literal["allow", "block"]

ALL_TYPES: Final[tuple[str, ...]] = (
    "main_frame", "sub_frame", "stylesheet", "script", "image", "font", "object", "xmlhttprequest", "ping",
    "csp_report", "media", "websocket", "webtransport", "webbundle", "other",
)  # fmt: skip
CAPTCHA_DOMAINS: Final[tuple[str, ...]] = (
    "recaptcha.net", "hcaptcha.com", "challenges.cloudflare.com", "arkoselabs.com", "funcaptcha.com", "geetest.com",
    "captcha-delivery.com", "mtcaptcha.com", "friendlycaptcha.com", "frcapi.com",
)  # fmt: skip
CAPTCHA_PATHS: Final[tuple[str, ...]] = (
    "||google.com/recaptcha",
    "||gstatic.com/recaptcha",
    "||www.google.com/recaptcha",
)
READ_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods that carry no form data (see the module docstring); every other method is a write."""

READ_ONLY_POSTS: Final[tuple[str, ...]] = (
    r"^https://jobs\.ashbyhq\.com/api/non-user-graphql\?op=(ApiJobPosting|ApiOrganizationFromHostedJobsPageName)$",
)
"""POSTs a form needs just to render, allowed by exact operation name after inspection (``inspect_page.py --posts``).
Submit and upload operations are never listed."""

PROBE_URL: Final[str] = "https://example.com/tayyari-guard-probe"
"""The probe's target: IANA's example domain, so a probe that does get through reaches nobody."""
PROBE_PAGE: Final[str] = "https://example.com/"
"""Where a fresh tab goes before the probe runs, so the probe's fetch runs from a real origin."""
PROBE_JS: Final[str] = """async (url) => {
  try { await fetch(url, {method: 'GET', mode: 'no-cors', cache: 'no-store'}); }
  catch (e) { return 'ERROR:unreachable'; }
  try { await fetch(url, {method: 'POST', body: 'probe', mode: 'no-cors', cache: 'no-store'}); return 'SENT'; }
  catch (e) { return 'BLOCKED'; }
}"""
"""Run with `PROBE_URL`. A GET first proves the network works, so 'BLOCKED' means the POST was stopped, not that the page
was offline. 'BLOCKED' is the only answer that lets a run go on; 'SENT' means layer 1 is not active."""

MANIFEST: Final[dict[str, Any]] = {
    "manifest_version": 3,
    "name": "Tayyari guard",
    "version": "1.0.0",
    "description": "Blocks writes and CAPTCHA providers so nothing is sent before the person approves it.",
    "permissions": ["declarativeNetRequest"],
    "declarative_net_request": {"rule_resources": [{"id": "guard", "enabled": True, "path": "rules.json"}]},
}

RULE_WRITES: Final = 1
RULE_CAPTCHA_DOMAINS: Final = 2
RULE_CAPTCHA_PATHS_FROM: Final = 3
RULE_SOCKETS: Final = 20
RULE_READ_ONLY_FROM: Final = 50
SOCKET_TYPES: Final[tuple[str, ...]] = ("websocket", "webtransport")
PRIORITY_WRITES: Final = 1
PRIORITY_READ_ONLY: Final = 2
PRIORITY_CAPTCHA: Final = 3
"""A higher priority wins in declarativeNetRequest: CAPTCHA blocks beat the read-only allowances, which beat the write
block."""

_ZIP_DATE: Final = (2026, 1, 1, 0, 0, 0)
_HOST: Final = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_TWO_PART_SUFFIXES: Final = frozenset(
    {"co.uk", "org.uk", "ac.uk", "co.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in", "com.au", "net.au",
     "co.nz", "co.jp", "com.br", "com.sg", "com.mx", "co.za", "com.cn", "com.hk", "co.kr", "com.tr", "com.my"}
)  # fmt: skip


def _norm_hosts(hosts: Collection[str]) -> tuple[str, ...]:
    out: list[str] = []
    for raw in hosts:
        host = raw.strip().lower().rstrip(".")
        if not _HOST.match(host):
            raise ValueError("a write host must be a bare host name")
        if is_captcha_host(host) or host == _host(PROBE_URL):
            raise ValueError("a CAPTCHA provider or the probe host can never receive writes")
        out.append(host)
    return tuple(sorted(set(out)))


def rules(*, allow_write_hosts: Sequence[str] = ()) -> list[dict[str, Any]]:
    """The declarativeNetRequest rules: the preview variant with no hosts, the submit variant of one ATS with its
    submit and upload hosts (subdomains included, as declarativeNetRequest matches them)."""
    hosts = _norm_hosts(allow_write_hosts)
    writes: dict[str, Any] = {
        "excludedRequestMethods": ["get", "head", "options"],
        "resourceTypes": list(ALL_TYPES),
    }
    if hosts:
        writes["excludedRequestDomains"] = list(hosts)
    out: list[dict[str, Any]] = [
        {"id": RULE_WRITES, "priority": PRIORITY_WRITES, "action": {"type": "block"}, "condition": writes},
        {"id": RULE_CAPTCHA_DOMAINS, "priority": PRIORITY_CAPTCHA, "action": {"type": "block"},
         "condition": {"requestDomains": list(CAPTCHA_DOMAINS), "resourceTypes": list(ALL_TYPES)}},
    ]  # fmt: skip
    sockets: dict[str, Any] = {"resourceTypes": list(SOCKET_TYPES)}
    if hosts:
        sockets["excludedRequestDomains"] = list(hosts)
    out.append(
        {"id": RULE_SOCKETS, "priority": PRIORITY_CAPTCHA, "action": {"type": "block"}, "condition": sockets}
    )
    for number, path in enumerate(CAPTCHA_PATHS, start=RULE_CAPTCHA_PATHS_FROM):
        out.append({"id": number, "priority": PRIORITY_CAPTCHA, "action": {"type": "block"},
                    "condition": {"urlFilter": path, "resourceTypes": list(ALL_TYPES)}})  # fmt: skip
    for number, regex in enumerate(READ_ONLY_POSTS, start=RULE_READ_ONLY_FROM):
        out.append({"id": number, "priority": PRIORITY_READ_ONLY, "action": {"type": "allow"},
                    "condition": {"regexFilter": regex, "requestMethods": ["post"],
                                  "resourceTypes": ["xmlhttprequest", "other"]}})  # fmt: skip
    return out


def extension_zip(*, allow_write_hosts: Sequence[str] = ()) -> tuple[bytes, str]:
    """The extension as a ZIP (``manifest.json`` at its root) and a short hash of its content, which keys the upload
    cache. The bytes are deterministic (fixed file dates), so the same variant always gives the same ZIP."""
    manifest = json.dumps(MANIFEST, indent=2, sort_keys=True).encode()
    rule_file = json.dumps(rules(allow_write_hosts=allow_write_hosts), indent=2, sort_keys=True).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in (("manifest.json", manifest), ("rules.json", rule_file)):
            zf.writestr(zipfile.ZipInfo(name, date_time=_ZIP_DATE), data, compress_type=zipfile.ZIP_DEFLATED)
    digest = hashlib.sha256(manifest + b"\0" + rule_file).hexdigest()[:16]
    return buf.getvalue(), digest


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _matches(host: str, domains: Collection[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_captcha_host(host: str) -> bool:
    return _matches(host.lower(), CAPTCHA_DOMAINS)


def is_captcha_url(url: str) -> bool:
    """True for a CAPTCHA or bot-challenge provider's URL (Google's reCAPTCHA paths included)."""
    host = _host(url)
    if is_captcha_host(host):
        return True
    path = urlsplit(url).path if host else ""
    return path.startswith("/recaptcha") and _matches(host, ("google.com", "gstatic.com"))


def read_only_post(url: str, extra: Sequence[str] = ()) -> bool:
    """True for a POST a form needs to render: `READ_ONLY_POSTS` plus a playbook's own (``extra``, full-match regexes)."""
    return any(re.match(rx, url) for rx in (*READ_ONLY_POSTS, *extra))


def site_of(url_or_host: str) -> str:
    """The registrable part of a host (``boards.greenhouse.io`` -> ``greenhouse.io``; ``careers.acme.co.in`` ->
    ``acme.co.in``), a small built-in suffix list standing in for the public suffix list."""
    host = _host(url_or_host) if "/" in url_or_host else url_or_host.lower().rstrip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    take = 3 if ".".join(labels[-2:]) in _TWO_PART_SUFFIXES else 2
    return ".".join(labels[-take:])


def decide(
    method: str,
    url: str,
    *,
    is_navigation: bool,
    site: str | None,
    write_hosts: Collection[str],
    read_only: Sequence[str] = (),
) -> Decision:
    """Layer 2's verdict on one request.

    ``site`` is the form's registrable site (`site_of` of the URL the code opened; None before the first open);
    ``write_hosts`` are the hosts writes may go to now (empty until the submit path lifts the guard); ``read_only`` are
    a playbook's extra read-only POST patterns. Order: page-internal schemes, the probe, CAPTCHA providers, off-site
    main-frame navigation, then reads, read-only POSTs and writes.
    """
    verb = method.upper()
    if url.startswith(("data:", "blob:", "about:")):
        return "allow" if verb in READ_METHODS else "block"
    scheme = urlsplit(url).scheme.lower() if "://" in url else ""
    host = _host(url)
    if scheme not in ("http", "https") or not host:
        return "block"
    if url == PROBE_URL and verb in ("GET", "POST"):
        return "allow"  # only layer 1 may stop the probe, so a 'BLOCKED' probe proves the extension is live
    if is_captcha_url(url):
        return "block"
    hosts = tuple(h.lower() for h in write_hosts)
    if is_navigation and site is not None and site_of(host) != site and not _matches(host, hosts):
        return "block"
    if verb in READ_METHODS:
        return "allow"
    if verb == "POST" and read_only_post(url, read_only):
        return "allow"
    return "allow" if hosts and _matches(host, hosts) else "block"


def socket_decision(url: str, *, write_hosts: Collection[str]) -> Decision:
    """Layer 2's verdict on a WebSocket: every socket is closed, except one to a lifted write host (the submit path,
    after ``allow_submit_hosts``) that is not a CAPTCHA provider."""
    scheme = urlsplit(url).scheme.lower() if "://" in url else ""
    host = _host(url)
    if scheme not in ("ws", "wss") or not host or is_captcha_host(host):
        return "block"
    hosts = tuple(h.lower() for h in write_hosts)
    return "allow" if hosts and _matches(host, hosts) else "block"


def norm_write_hosts(hosts: Collection[str]) -> tuple[str, ...]:
    """Write hosts checked as layer 1 checks them: bare host names, never a CAPTCHA provider or the probe host, and
    never a bare public suffix (``com``, ``co.uk``), which would lift writes for a whole top-level domain."""
    out = _norm_hosts(hosts)
    if any(h in _TWO_PART_SUFFIXES for h in out):
        raise ValueError("a write host must be a bare host name")
    return out


__all__ = [
    "ALL_TYPES",
    "CAPTCHA_DOMAINS",
    "CAPTCHA_PATHS",
    "MANIFEST",
    "PRIORITY_CAPTCHA",
    "PRIORITY_READ_ONLY",
    "PRIORITY_WRITES",
    "PROBE_JS",
    "PROBE_PAGE",
    "PROBE_URL",
    "READ_METHODS",
    "READ_ONLY_POSTS",
    "RULE_CAPTCHA_DOMAINS",
    "RULE_CAPTCHA_PATHS_FROM",
    "RULE_READ_ONLY_FROM",
    "RULE_SOCKETS",
    "RULE_WRITES",
    "SOCKET_TYPES",
    "Decision",
    "decide",
    "extension_zip",
    "is_captcha_host",
    "is_captcha_url",
    "norm_write_hosts",
    "read_only_post",
    "rules",
    "site_of",
    "socket_decision",
]
