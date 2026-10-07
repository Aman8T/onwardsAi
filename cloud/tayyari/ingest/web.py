"""Generic web-page ingestion (portfolios, Lovable apps, blogs, LinkedIn) with an SSRF guard.

Security model: only public http(s) hosts are fetched. Every hop (the first request and each
redirect) resolves the host with the system resolver, refuses loopback, private, link-local,
unique-local, IPv4-mapped/embedded and other non-global addresses, and then connects to the
address it just validated (the Host header and TLS server name stay the original host), so a DNS
answer cannot change between the check and the request. Requests have a hard timeout and a 2 MB
body cap. HTML is read by a small linear tokenizer (the stdlib `html.parser` is quadratic on
hostile pages) and every regular expression that touches untrusted text is bounded or possessive.
"""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import json
import os
import re
import socket
import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .classify import IngestLog, SourceDoc, is_known_platform, normalize_url, plural
from .terms import find_terms

# --------------------------------------------------------------------------- constants

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 10.0
"""Seconds for one request (including every redirect hop) before it is abandoned."""
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 6
MAX_BUNDLES = 2
MAX_UI_STRINGS = 250
PAGE_TEXT_CHARS = 25_000
CONTENT_CHARS = 40_000
THIN_TEXT_CHARS = 400
"""A page with less visible text than this, plus module scripts, is treated as a client-rendered SPA."""
MAX_LINKS = 3000
MAX_SCRIPT_URLS = 200
MAX_HEADINGS = 40
MAX_STRUCTURED = 6
MAX_LITERALS_SCANNED = 150_000
"""Bundle string literals examined (not kept) before extraction stops."""

LOGIN_WALL_LOVABLE = "That is a private Lovable editor link — share the published .lovable.app URL instead"
LINKEDIN_BLOCKED = "LinkedIn blocks automated reading — paste your About section into Extra context instead"
PRIVATE_MSG = "That address is private. Only public links can be read."
NOT_FOUND_MSG = "Could not find that site. Check the link for typos."


class FetchProblem(Exception):
    """A fetch that cannot proceed; the message is written for the person who pasted the link."""


# --------------------------------------------------------------------------- SSRF guard

_BLOCKED_V4 = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.168.0.0/16 "
        "198.18.0.0/15 224.0.0.0/4 240.0.0.0/4"
    ).split()
)
# Not global unicast, or embedding an IPv4 address (::a.b.c.d, ::ffff:0:a.b.c.d, 64:ff9b::a.b.c.d, 6to4, Teredo).
_BLOCKED_V6 = tuple(
    ipaddress.ip_network(n)
    for n in (
        "::/96 ::ffff:0:0:0/96 64:ff9b::/96 64:ff9b:1::/48 100::/64 fe80::/10 fec0::/10 fc00::/7 ff00::/8 "
        "2001::/32 2001:db8::/32 2002::/16"
    ).split()
)
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def is_private_address(address: str) -> bool:
    """True for an IP literal (either family) that must never be fetched; anything that is not an address too."""
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:a.b.c.d is judged by the IPv4 rules
    blocked = _BLOCKED_V4 if ip.version == 4 else _BLOCKED_V6
    return any(ip in net for net in blocked) or not ip.is_global


def _bare_host(hostname: str) -> str:
    return hostname.lower().removeprefix("[").removesuffix("]").removesuffix(".")


def is_private_host(hostname: str) -> bool:
    """Private by name (localhost, .local...) or an IP literal in a private range. Does not touch DNS."""
    h = _bare_host(hostname)
    if h == "localhost" or h.endswith(_PRIVATE_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(h)
    except ValueError:
        return False
    return is_private_address(h)


async def resolve_public(hostname: str) -> list[str]:
    """Addresses of `hostname`, all verified public (IPv4 first). Raises FetchProblem for anything else."""
    h = _bare_host(hostname)
    if is_private_host(h):
        raise FetchProblem(PRIVATE_MSG)
    try:
        ipaddress.ip_address(h)
        return [h]
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(h, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchProblem(NOT_FOUND_MSG) from exc
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise FetchProblem(NOT_FOUND_MSG)
    # Every answer must be public (localtest.me, 127.0.0.1.nip.io, a name pointing at 169.254.169.254...).
    if any(is_private_address(a) for a in addresses):
        raise FetchProblem(PRIVATE_MSG)
    return sorted(addresses, key=lambda a: ":" in a)


# --------------------------------------------------------------------------- fetching


@dataclass
class Fetched:
    status: int
    url: str
    content_type: str
    text: str
    truncated: bool


async def read_capped(res: httpx.Response, cap: int) -> tuple[bytes, bool]:
    """The (decoded) body of a streaming response, at most `cap` bytes; the flag says it was cut."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in res.aiter_bytes():
        if total + len(chunk) > cap:
            chunks.append(chunk[: cap - total])
            return b"".join(chunks), True
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), False


def _decode_body(data: bytes, content_type: str) -> str:
    m = re.search(r"charset=[\"']?([\w-]{1,40})", content_type, re.IGNORECASE)
    label = m.group(1) if m else None
    if not label and re.search(r"html|xml", content_type, re.IGNORECASE):
        head = data[:4096].decode("latin-1")
        m = re.search(r"<meta[^<>]{1,512}charset=[\"']?([\w-]{1,40})", head, re.IGNORECASE)
        label = m.group(1) if m else None
    try:
        return data.decode(codecs.lookup(label or "utf-8").name, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _friendly_network_error(exc: BaseException) -> str:
    if os.environ.get("INGEST_DEBUG"):
        print(f"[ingest/web] fetch failed: {exc!r}", flush=True)
    if isinstance(exc, FetchProblem):
        return str(exc)
    if isinstance(exc, httpx.ConnectTimeout):
        return "Could not connect to that site (the connection timed out). Check the link."
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return f"The page took too long to respond (over {int(REQUEST_TIMEOUT)} seconds)."
    chain: list[BaseException] = []
    e: BaseException | None = exc
    while e is not None and len(chain) < 8:
        chain.append(e)
        e = e.__cause__ or e.__context__
    if any(isinstance(c, ssl.SSLError) for c in chain):
        return "That site's security certificate is not valid."
    if any(isinstance(c, ConnectionRefusedError) for c in chain):
        return "That site refused the connection."
    return "Could not reach that site."


def _format_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


async def _get_pinned(
    client: httpx.AsyncClient, scheme: str, host: str, port: int | None, target: str, accept: str, cap: int
) -> tuple[httpx.Headers, int, bytes, bool]:
    """GET `target` from one validated address of `host`; returns (headers, status, body, truncated)."""
    bare = _bare_host(host)
    is_literal = ":" in bare or bare.replace(".", "").isdigit()
    addresses = await resolve_public(host)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Host": host + (f":{port}" if port else ""),
    }
    extensions = {} if scheme == "http" or is_literal else {"sni_hostname": bare}
    last: Exception | None = None
    for address in addresses[:3]:
        netloc = _format_host(address) + (f":{port}" if port else "")
        try:
            async with client.stream(
                "GET", f"{scheme}://{netloc}{target}", headers=headers, extensions=extensions
            ) as res:
                if 300 <= res.status_code < 400 and res.headers.get("location"):
                    return res.headers, res.status_code, b"", False  # a redirect: its body is never read
                body, truncated = await read_capped(res, cap)
                return res.headers, res.status_code, body, truncated
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # try the next address
            last = exc
    assert last is not None
    raise last


async def _fetch_text(start_url: str, cap: int, accept: str) -> Fetched:
    """GET with manual redirects (every hop is resolved and checked), a hard timeout and a body cap."""
    url = start_url
    async with asyncio.timeout(REQUEST_TIMEOUT):
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=REQUEST_TIMEOUT) as client:
            for _ in range(MAX_REDIRECTS + 1):
                u = normalize_url(url)
                if u is None:
                    raise FetchProblem("That link redirects somewhere that cannot be read.")
                target = (u.path or "/") + u.query
                headers, status, body, truncated = await _get_pinned(
                    client, u.scheme, u.hostname, u.port, target, accept, cap
                )
                location = headers.get("location")
                if 300 <= status < 400 and location:
                    url = urljoin(u.url, location)
                    continue
                content_type = headers.get("content-type", "")
                return Fetched(status, u.url, content_type, _decode_body(body, content_type), truncated)
    raise FetchProblem("That link redirects too many times.")


async def fetch_text(start_url: str, cap: int, accept: str) -> Fetched:
    """`_fetch_text` with one retry for dropped connections (never for timeouts, DNS or HTTP errors)."""
    try:
        return await _fetch_text(start_url, cap, accept)
    except (httpx.RemoteProtocolError, httpx.ReadError):
        await asyncio.sleep(0.4)
        return await _fetch_text(start_url, cap, accept)


# --------------------------------------------------------------------------- HTML parsing
#
# The stdlib `html.parser` is quadratic on hostile input (2 MB of "<a " or "<!--" keeps a CPU busy for
# minutes, and a worker thread cannot be cancelled), so pages are read by a small tokenizer instead.
# Its patterns are possessive or bounded and never run past the next "<" outside a quoted attribute
# value, so one pass is linear; MAX_TOKENS caps the work on a page that is nothing but tags.

MAX_TOKENS = 400_000

_NAMED_BLOCK_END = frozenset(
    "p div section article aside li ul ol tr table blockquote pre h1 h2 h3 h4 h5 h6 figure figcaption dd dt dl form "
    "fieldset main details summary address".split()
)
_VOID = frozenset("area base br col embed hr img input link meta source track wbr".split())
_SKIP_TAGS = frozenset("noscript svg template iframe canvas".split())
"""Blocks whose content is never page text (script and style are skipped by the tokenizer itself)."""
_HEAD_TAGS = frozenset("title meta link script style base noscript template".split())
_ATTR_TAGS = frozenset(("meta", "link", "script", "a"))
"""The only tags whose attributes are read."""
_CHROME_TAGS = ("nav", "footer", "header")
_HEADING_RE = re.compile(r"h([1-6])")
_LINE_NOISE = re.compile(r"[-|•·]+")
_LD_TYPE_RE = re.compile(
    r"person|profilepage|website|organization|softwareapplication|webapplication|creativework", re.IGNORECASE
)

_TAG_RE = re.compile(
    r"<(?:/(?P<end>[A-Za-z][^\s/<>]{0,30}+)[^<>]{0,300}>"
    r"|(?P<start>[A-Za-z][^\s/<>]{0,30}+)(?P<attrs>(?:[^<>\"']++|\"[^\"]*+\"|'[^']*+')*+)>"
    r"|[!?][^<>]{0,500}>)"
)
"""A tag, an end tag or a declaration (<!doctype>, <?xml?>) at the current position; ">" may sit inside quotes."""
_ATTR_RE = re.compile(r"""([^\s"'=<>/]++)(?:\s*+=\s*+(?:"([^"]*+)"|'([^']*+)'|([^\s>]*+)))?""")
_RAW_END = {
    "script": re.compile(r"</script\b[^<>]{0,100}>", re.IGNORECASE),
    "style": re.compile(r"</style\b[^<>]{0,100}>", re.IGNORECASE),
}


def _squash(s: str) -> str:
    return " ".join(s.split())


def _strip_comments(s: str) -> str:
    """Remove closed HTML comments in linear time; an unterminated one is left in place."""
    out: list[str] = []
    i = 0
    while True:
        start = s.find("<!--", i)
        end = s.find("-->", start + 4) if start >= 0 else -1
        if end < 0:
            out.append(s[i:])
            return "".join(out)
        out.append(s[i:start])
        i = end + 3


def _to_text(pieces: list[str]) -> str:
    lines = [_squash(line) for line in "".join(pieces).split("\n")]
    out: list[str] = []
    for line in lines:
        if line and not _LINE_NOISE.fullmatch(line) and (not out or out[-1] != line):
            out.append(line)
    return "\n".join(out)


def _parse_attrs(s: str) -> dict[str, str]:
    """Attributes of one tag, first occurrence wins, entities decoded."""
    attrs: dict[str, str] = {}
    for m in _ATTR_RE.finditer(s):
        key = m[1].lower()
        if key not in attrs:
            value = next((v for v in (m[2], m[3], m[4]) if v is not None), "")
            attrs[key] = unescape(value) if "&" in value else value
    return attrs


def _absolute_link(base: str, href: str) -> str | None:
    """`href` resolved against the page as an http(s) URL with a path (like WHATWG `URL`), or None."""
    try:
        parts = urlsplit(urljoin(base, href))
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))
    except ValueError:
        return None


@dataclass
class PageInfo:
    title: str = ""
    description: str = ""
    og_title: str = ""
    og_description: str = ""
    site_name: str = ""
    author: str = ""
    generator: str = ""
    """<meta name="generator"> ("Astro v4", "Lovable"...)."""
    headings: list[tuple[int, str]] = field(default_factory=list)
    text: str = ""
    links: list[str] = field(default_factory=list)
    structured: list[str] = field(default_factory=list)
    module_scripts: list[str] = field(default_factory=list)
    plain_scripts: list[str] = field(default_factory=list)
    html: str = ""


class _PageParser:
    """One pass over the page: title, meta, JSON-LD, scripts, headings, links and readable text."""

    def __init__(self, page_url: str) -> None:
        self.page_url = page_url
        self.info = PageInfo()
        self.meta: dict[str, str] = {}
        self._preloads: list[str] = []
        self._title_buf: list[str] | None = None
        self._title_done = False
        self._strict: list[str] = []  # text without nav/footer/header
        self._lenient: list[str] = []  # all text
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._in_head = False
        self._chrome = dict.fromkeys(_CHROME_TAGS, 0)
        self._heading_level = 0
        self._heading_buf: list[str] = []
        self._seen_headings: set[str] = set()

    # -- tokenizer

    def feed(self, html: str) -> None:
        """Walk the page tag by tag; stops after MAX_TOKENS tokens."""
        n, pos, budget = len(html), 0, MAX_TOKENS
        find, match = html.find, _TAG_RE.match
        while pos < n and budget > 0:
            budget -= 2
            lt = find("<", pos)
            if lt < 0:
                self._text(html[pos:])
                break
            if lt > pos:
                self._text(html[pos:lt])
            m = match(html, lt)
            if m is None:  # a stray "<" is text
                nxt = find("<", lt + 1)
                pos = n if nxt < 0 else nxt
                self._text(html[lt:pos])
            elif m["end"] is not None:
                pos = m.end()
                self._end(m["end"].lower())
            elif m["start"] is not None:
                pos = self._start(html, m.end(), m["start"].lower(), m["attrs"])
            else:
                pos = m.end()

    def finish(self) -> PageInfo:
        """Close what the page left open (a title or heading that never ended) and return what was collected."""
        if self._title_buf is not None:
            self._finish_title(closed=False)
        self._end_heading()
        info, meta = self.info, self.meta
        info.module_scripts += self._preloads
        info.description = meta.get("description", "")
        info.og_title = meta.get("og:title", "")
        info.og_description = meta.get("og:description", "")
        info.site_name = meta.get("og:site_name", "")
        info.author = meta.get("author", "")
        info.generator = meta.get("generator", "")
        info.text = _to_text(self._strict)
        if len(info.text) < 300:
            # Some sites wrap everything in <header>; fall back to the lenient version.
            lenient = _to_text(self._lenient)
            if len(lenient) > len(info.text):
                info.text = lenient
        return info

    # -- helpers

    def _emit(self, s: str) -> None:
        self._lenient.append(s)
        if not any(self._chrome.values()):
            self._strict.append(s)

    def _break(self, s: str) -> None:
        """Structural text from a tag (newline, bullet, space); headings see it as a space."""
        self._emit(s)
        if self._heading_level:
            self._heading_buf.append(" ")

    def _end_heading(self) -> None:
        if not self._heading_level:
            return
        text = _squash("".join(self._heading_buf))
        if text and len(text) <= 200 and text not in self._seen_headings and len(self.info.headings) < MAX_HEADINGS:
            self._seen_headings.add(text)
            self.info.headings.append((self._heading_level, text))
        self._heading_level, self._heading_buf = 0, []

    def _finish_title(self, closed: bool) -> None:
        """End the <title>; one that never closes (tags cannot appear inside a real one) stays page text."""
        text = "".join(self._title_buf or [])
        self._title_buf, self._title_done = None, True
        if closed:
            self.info.title = _squash(text)
        else:
            self._emit(text)

    def _structured(self, raw: str) -> None:
        """Descriptions of the people, products and sites named in one JSON-LD block."""
        if len(self.info.structured) >= MAX_STRUCTURED:
            return
        try:
            data = json.loads(raw)
            nodes = data if isinstance(data, list) else [data, *((data.get("@graph") or []) if isinstance(data, dict) else [])]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                kind = node.get("@type") or ""
                kind = "/".join(str(k) for k in kind) if isinstance(kind, list) else str(kind)
                if not _LD_TYPE_RE.search(kind):
                    continue
                bits = [
                    f"{k}: {_squash(node[k])[:240]}"
                    for k in ("name", "jobTitle", "description", "url")
                    if isinstance(node.get(k), str)
                ]
                same_as = node.get("sameAs")
                if isinstance(same_as, list):
                    bits.append("sameAs: " + ", ".join([x for x in same_as if isinstance(x, str)][:8]))
                if bits:
                    self.info.structured.append(f"{kind or 'Thing'}: {' | '.join(bits)}")
                if len(self.info.structured) >= MAX_STRUCTURED:
                    break
        except Exception:  # noqa: BLE001, S110 - ignore malformed JSON-LD
            pass

    # -- tags

    def _start(self, html: str, pos: int, tag: str, attrs: str) -> int:
        """Handle a start tag; returns where tokenizing resumes (past the body of a script or style)."""
        a = _parse_attrs(attrs) if tag in _ATTR_TAGS else {}
        self_closing = attrs.endswith("/")
        if self._title_buf is not None and not self._skip_tag:
            self._finish_title(closed=False)

        # Collected wherever they appear, even inside skipped blocks.
        scripts = len(self.info.module_scripts) + len(self.info.plain_scripts) + len(self._preloads)
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = _squash(a["content"])
        elif tag == "link":
            if "modulepreload" in a.get("rel", "").lower().split() and a.get("href") and scripts < MAX_SCRIPT_URLS:
                self._preloads.append(a["href"])
        elif tag == "script":
            src, kind = a.get("src"), a.get("type", "")
            if src and scripts < MAX_SCRIPT_URLS:
                if kind.lower() == "module":
                    self.info.module_scripts.append(src)
                elif not kind or "javascript" in kind.lower():
                    self.info.plain_scripts.append(src)

        if tag in _RAW_END and not self_closing:  # script and style: their text is never markup
            if not self._skip_tag:
                self._break(" ")  # a removed block still separates the words around it
            closer = _RAW_END[tag].search(html, pos)
            end = closer.start() if closer else len(html)
            if tag == "script" and a.get("type", "").strip().lower() == "application/ld+json":
                self._structured(html[pos:end])
            return closer.end() if closer else len(html)

        if self._skip_tag:
            if tag == self._skip_tag and not self_closing:
                self._skip_depth += 1
            return pos
        if tag in _SKIP_TAGS:
            if not self_closing:
                self._break(" ")
                self._skip_tag, self._skip_depth = tag, 1
            return pos
        if tag == "head":
            self._in_head = True
            return pos
        if self._in_head and tag not in _HEAD_TAGS:
            self._in_head = False  # browsers close <head> implicitly at the first body element
        if tag == "title" and not self._title_done:
            self._title_buf = []
            return pos
        if self._in_head:
            return pos

        if tag in self._chrome:
            self._chrome[tag] += 1
        m = _HEADING_RE.fullmatch(tag)
        if m:
            self._end_heading()
            level = int(m.group(1))
            if level <= 3:
                self._heading_level = level
            self._emit(f"\n{'#' * min(level, 4)} ")
        elif tag in ("br", "hr"):
            self._emit("\n")
        elif tag == "li":
            self._break("\n- ")
        elif tag in ("td", "th"):
            self._break(" | ")
        else:
            self._break(" ")
            if tag == "a" and a.get("href") and len(self.info.links) < MAX_LINKS:
                href = a["href"].strip()
                if href and not href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
                    link = _absolute_link(self.page_url, href)
                    if link:
                        self.info.links.append(link)
        return pos

    def _end(self, tag: str) -> None:
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag in _VOID:
            return
        if tag == "title" and self._title_buf is not None:
            self._finish_title(closed=True)
            return
        if tag == "head":
            self._in_head = False
            return
        if self._in_head:
            return
        if tag in self._chrome and self._chrome[tag] > 0:
            self._chrome[tag] -= 1
        m = _HEADING_RE.fullmatch(tag)
        if m and int(m.group(1)) == self._heading_level:
            self._end_heading()
        self._break("\n" if tag in _NAMED_BLOCK_END else " ")

    def _text(self, raw: str) -> None:
        if self._skip_tag:
            return
        data = unescape(raw) if "&" in raw else raw
        if self._title_buf is not None:
            self._title_buf.append(data)
            return
        if self._in_head:
            return
        self._emit(data)
        if self._heading_level:
            self._heading_buf.append(data)


def parse_page(raw_html: str, page_url: str) -> PageInfo:
    """Title, meta tags, structured data, scripts, headings, outbound links and readable text of an HTML page."""
    html = _strip_comments(raw_html)
    parser = _PageParser(page_url)
    parser.feed(html)
    info = parser.finish()
    info.html = html
    return info


# --------------------------------------------------------------------------- SPA bundle analysis


class _Signature:
    """A named fingerprint: plain substrings (fast) and regexes, any of which is a hit."""

    def __init__(self, name: str, literals: Sequence[str] = (), patterns: Sequence[str] = (), ignore_case: bool = False):
        self.name = name
        self.ignore_case = ignore_case
        self.literals = tuple(s.lower() if ignore_case else s for s in literals)
        self.patterns = tuple(re.compile(p, re.IGNORECASE if ignore_case else 0) for p in patterns)

    def matches(self, hay: str, hay_lower: str) -> bool:
        text = hay_lower if self.ignore_case else hay
        return any(s in text for s in self.literals) or any(p.search(hay) for p in self.patterns)


_S = _Signature
LIBRARY_SIGNATURES: tuple[_Signature, ...] = (
    _S("React", ["react.element", "react.transitional.element", "__reactFiber", "__reactProps",
                 "reactjs.org/docs/error-decoder", "react-dom"]),
    _S("Vite", ["__vite", "vite/modulepreload-polyfill", "vite-legacy"], [r"/assets/index-[\w-]{1,100}\.js"]),
    _S("Supabase", ["supabase.co", "@supabase/", "supabase-js", "GoTrueClient"], [r"sb-[a-z0-9]{1,60}-auth-token"]),
    _S("Firebase", ["firebaseio.com", "firebaseapp.com", "identitytoolkit.googleapis", "FirebaseError", "@firebase/"]),
    _S("Stripe", ["js.stripe.com", "stripe.com/v3", "@stripe/", "loadStripe"], [r"\bpk_(?:live|test)_"]),
    _S("OpenAI", ["api.openai.com", "openai.com/v1"], [r'"gpt-(?:4|5|3\.5)[\w.-]{0,40}"']),
    _S("Anthropic", ["api.anthropic.com", "anthropic-version"], [r'"claude-(?:3|opus|sonnet|haiku)[\w.-]{0,40}"']),
    _S("Gemini", ["generativelanguage.googleapis.com", "google/gemini"], [r'"gemini-[\w.-]{1,40}"']),
    _S("Clerk", ["clerk.com", "clerk.accounts.dev", "@clerk/", "ClerkProvider", "clerk-js"]),
    _S("Auth0", ["auth0.com", "@auth0/", "Auth0Provider"]),
    _S("React Router", ["react-router", "@remix-run/router", "BrowserRouter", "createBrowserRouter",
                        "useNavigate() may be used"]),
    _S("TanStack Query", ["@tanstack/", "QueryClientProvider"], [r"\bqueryFn:"]),
    _S("Framer Motion", ["framer-motion", "AnimatePresence", "data-projection-id"], [r"\bwhile(?:Hover|Tap|InView):"]),
    _S("Three.js", ["WebGLRenderer", "three.js", "@react-three/", "THREE."]),
    _S("Recharts", ["recharts"]),
    _S("Radix UI", ["@radix-ui", "data-radix-"]),
    _S("shadcn/ui", ["ring-offset-background", "text-primary-foreground", "text-muted-foreground", "bg-muted/50"]),
    _S("Lucide", ["lucide-react"], [r"\blucide\b"]),
    _S("Zod", ["invalid_type", "ZodError", "invalid_string", "too_small"]),
    _S("React Hook Form", ["react-hook-form", "useFormContext", "FormProvider"]),
    _S("Mapbox", ["mapbox"], ignore_case=True),
    _S("Google Maps", ["maps.googleapis.com", "google.maps.", "@react-google-maps", "googlemaps"]),
    _S("Leaflet", ["leaflet-container", "react-leaflet"]),
    _S("Chart.js", ["chart.js", "chartjs"], ignore_case=True),
    _S("Vercel Analytics", ["vercel-insights", "_vercel/insights", "va.vercel-scripts", "@vercel/analytics"]),
    _S("PostHog", ["posthog"], ignore_case=True),
    _S("Sentry", ["sentry.io", "@sentry/", "Sentry.init"]),
    _S("Google Analytics", ["googletagmanager.com", "google-analytics.com", "gtag("]),
    _S("Resend", ["api.resend.com", "resend.com/emails"]),
    _S("ElevenLabs", ["elevenlabs"], ignore_case=True),
    _S("Twilio", ["twilio.com"]),
    _S("Plaid", ["plaid.com", "react-plaid-link"]),
    _S("i18next", ["i18next", "react-i18next"]),
    _S("date-fns", ["date-fns"]),
    _S("Sonner", ["sonner"]),
    _S("Axios", ["axios.", "isAxiosError", "AxiosError"]),
    _S("GSAP", ["gsap.", "GreenSock"]),
    _S("Lottie", ["lottie"], ignore_case=True),
)  # fmt: skip

# Frameworks and site builders that leave a fingerprint in the served HTML itself.
HTML_SIGNATURES: tuple[_Signature, ...] = (
    _S("Next.js", ["/_next/static", "__NEXT_DATA__", 'id="__next"']),
    _S("Gatsby", ["___gatsby", "/page-data/", "gatsby-image"]),
    _S("Nuxt", ["__NUXT__", "/_nuxt/"]),
    _S("Astro", ["astro-island"], [r"astro-[a-z0-9]{8}\b", r'name="generator"[^<>]{0,160}content="Astro'], ignore_case=True),
    _S("Remix", ["__remixContext"]),
    _S("SvelteKit", ["__sveltekit", "data-sveltekit"]),
    _S("Docusaurus", ["docusaurus"], ignore_case=True),
    _S("WordPress", ["wp-content/", "wp-includes/"]),
    _S("Webflow", ["assets.website-files.com", "webflow.com"], [r"data-wf-(?:page|site)"]),
    _S("Framer", ["framerusercontent.com", "data-framer-", "framer.com/m/"]),
    _S("Wix", ["wixstatic.com"]),
    _S("Squarespace", ["static1.squarespace.com", "squarespace-cdn"]),
    _S("Shopify", ["cdn.shopify.com"]),
)  # fmt: skip

# Analytics and trackers say little about a person's skills, so they stay out of the quick signals.
NON_SKILL_SIGNALS = frozenset(("Google Analytics", "Vercel Analytics", "PostHog", "Sentry", "Stripe", "Sonner"))

# Hosts that appear in every bundle because of library internals, not because the app calls them.
NOISE_HOSTS = (
    "w3.org reactjs.org react.dev mozilla.org github.com fb.me facebook.com schema.org npmjs.com stackoverflow.com "
    "tailwindcss.com radix-ui.com ui.shadcn.com wikipedia.org unpkg.com jsdelivr.net googleapis.com/css "
    "fonts.googleapis.com fonts.gstatic.com example.com localhost lovable.dev xmlns.com tanstack.com zod.dev "
    "redux.js.org reactrouter.com remix.run developer.chrome.com web.dev ecma-international.org unicode.org "
    "iana.org ietf.org whatwg.org caniuse.com sonner.emilkowal.ski lucide.dev recharts.org"
).split()

_COPY_NOISE = re.compile(
    r"\b(?:React|hooks?|props?|prototype|undefined|null|NaN|callback|constructor|Symbol|iterable|invariant|"
    r"arguments?|Invalid|Cannot|Unexpected|deprecated|Uncaught|Minified|Radix|Warning|TypeError|RangeError|"
    r"ReferenceError|Provider|Context|DOM|SVG|HTML|JSON|Promise|WebGL|Failed to execute|authenticators?|"
    r"credentials?|ceremony|pubKeyCredParams|assertion|attestation|cryptographic|ArrayBuffer|Uint8Array|"
    r"Iterator|Observable|Proxy|Reflect|WeakMap)\b"
)
_CODE_CHARS = re.compile(r"[{}();=<>\\^~$`@\[\]_]|::|://")
_SENTENCE_CODE_CHARS = re.compile(r"[{}();=<>\\^~$`@\[\]_]|::|://|\.(?:js|css|tsx?|json)\b")
_PUNCT = re.compile(r"[.,!?'’:]")
_PROP_RE = re.compile(
    r"(?:children|placeholder|title|label|alt|description|subtitle|heading|tagline|text|question|answer|quote"
    r'|"aria-label"|ariaLabel)\s{0,8}:\s{0,8}(["' + r"'`])((?:\\.|(?!\1)[^\\\n]){2,200}?)\1"
)
# A literal cannot start at an escaped quote: that keeps `"\"\"\"...` from re-scanning every escape pair.
_STR_RE = re.compile(
    r'(?<!\\)"((?:[^"\\\n]|\\.){3,160})"'
    r"|(?<!\\)'((?:[^'\\\n]|\\.){3,160})'"
    r"|(?<!\\)`((?:[^`\\\n$]|\\.){3,160})`"
)
_ROUTE_RE = re.compile(r'\bpath:\s{0,8}"(/[A-Za-z0-9_\-/:*]{0,60}|\*)"')
_TABLE_RE = re.compile(r'\.from\(\s{0,8}"([a-z_][a-z0-9_-]{1,40})"\s{0,8}\)')
_RPC_RE = re.compile(r'\.(?:rpc|invoke)\(\s{0,8}"([A-Za-z_][\w-]{1,40})"')
_HOST_RE = re.compile(r"""https?://([a-z0-9][a-z0-9.\-]{0,252}\.[a-z]{2,})(?=[/"'`?])""", re.IGNORECASE)
_UTILITY_RE = re.compile(r"\b(?:bg|px|py|text|rounded|border|gap|items|justify|space-[xy])-[a-z0-9\[\]/.\-]{1,60}")
_HTML_UTILITY_RE = re.compile(r"\b(?:px|py|mt|mb|bg|text|rounded|gap)-(?:\d|\[|[a-z]{1,20}-\d)")
_FLEX_RE = re.compile(r"\bflex\b")
_CLASSY_RE = re.compile(r"[a-z0-9]+(?:[-:/][a-z0-9\[\].%#]+)+")
_JS_ESCAPE_U = re.compile(r"\\u([0-9a-fA-F]{4})")
_JS_ESCAPE_X = re.compile(r"\\x([0-9a-fA-F]{2})")


def _unescape_js(raw: str, quote: str) -> str:
    if "\\" not in raw:
        return raw
    if quote == '"':
        try:
            return str(json.loads(f'"{raw}"'))
        except ValueError:
            pass
    s = _JS_ESCAPE_U.sub(lambda m: chr(int(m.group(1), 16)), raw)
    s = _JS_ESCAPE_X.sub(lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace("\\n", " ").replace("\\t", " ")
    s = re.sub(r"""\\(['"`\\/])""", r"\1", s)
    return s.encode("utf-8", "ignore").decode("utf-8")  # drop lone surrogates from broken \u escapes


def _looks_like_prop_copy(s: str) -> bool:
    """Short strings handed straight to a UI prop: children, placeholder, title... High confidence."""
    if len(s) < 2 or len(s) > 200:
        return False
    if not re.match(r"[A-Za-z0-9]", s) or _CODE_CHARS.search(s) or not re.search(r"[A-Za-z]{2}", s):
        return False
    if _COPY_NOISE.search(s):
        return False
    if " " not in s:
        # single token: only keep it when it reads like a label ("Sign In", "Next", "Dashboard")
        return bool(re.fullmatch(r"[A-Z][a-z][A-Za-z'’!?.,&-]*", s)) and len(s) >= 3
    # Tailwind/class lists and identifier-ish word lists: lowercase start and no sentence punctuation.
    return bool(re.match(r"[A-Z0-9]", s) or _PUNCT.search(s))


def _looks_like_sentence(s: str) -> bool:
    """Any quoted string that reads like a sentence: 3-140 chars, has a space, starts with a letter."""
    if len(s) < 3 or len(s) > 140 or not re.match(r"[A-Za-z]", s) or " " not in s:
        return False
    if _SENTENCE_CODE_CHARS.search(s) or _COPY_NOISE.search(s):
        return False
    if not re.match(r"[A-Z]", s) and not _PUNCT.search(s):
        return False
    tokens = s.split()
    if sum(1 for t in tokens if _CLASSY_RE.fullmatch(t)) / len(tokens) >= 0.4:
        return False
    # camelCase identifiers
    return not any(re.search(r"[a-z][A-Z]", t) and not re.match(r"[A-Z]", t) for t in tokens)


@dataclass
class BundleFindings:
    prop_strings: list[str] = field(default_factory=list)
    sentences: list[tuple[str, float]] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    hosts: list[tuple[str, int]] = field(default_factory=list)
    libraries: list[str] = field(default_factory=list)


def _detect(signatures: Sequence[_Signature], hay: str, skip: Sequence[str] = ()) -> list[str]:
    lowered = hay.lower()
    return [s.name for s in signatures if s.name not in skip and s.matches(hay, lowered)]


def _count_up_to(pattern: re.Pattern[str], text: str, limit: int) -> int:
    n = 0
    for _ in pattern.finditer(text):
        n += 1
        if n >= limit:
            break
    return n


def analyze_bundle(js: str, extra_html: str) -> BundleFindings:
    """Human UI copy, routes, backend hints and libraries found in a minified SPA bundle."""
    f = BundleFindings()
    prop_seen: set[str] = set()
    for scanned, m in enumerate(_PROP_RE.finditer(js), 1):
        if scanned > MAX_LITERALS_SCANNED:
            break
        quote, body = m.group(1), m.group(2)
        if quote == "`" and "${" in body:
            continue
        s = _squash(_unescape_js(body, quote))
        if s in prop_seen or not _looks_like_prop_copy(s):
            continue
        prop_seen.add(s)
        f.prop_strings.append(s)

    sentence_seen: set[str] = set()
    length = len(js) or 1
    for scanned, m in enumerate(_STR_RE.finditer(js), 1):
        if scanned > MAX_LITERALS_SCANNED:
            break
        if m.group(1) is not None:
            quote, raw = '"', m.group(1)
        elif m.group(2) is not None:
            quote, raw = "'", m.group(2)
        else:
            quote, raw = "`", m.group(3) or ""
        s = _squash(_unescape_js(raw, quote))
        if s in sentence_seen or s in prop_seen or not _looks_like_sentence(s):
            continue
        sentence_seen.add(s)
        words = len(s.split())
        score = (
            (2 if words >= 3 else 0)
            + (1 if words >= 6 else 0)
            + (2 if s[-1] in ".!?" else 0)
            + (1 if s[:1].isupper() else 0)
            + 1.5 * (m.start() / length)
        )
        f.sentences.append((s, score))

    routes: dict[str, None] = {}
    for m in _ROUTE_RE.finditer(js):
        if m.group(1) not in ("*", "/"):
            routes[m.group(1)] = None
        if len(routes) >= 25:
            break
    f.routes = list(routes)
    f.tables = _unique_matches(_TABLE_RE, js, 20)
    f.functions = _unique_matches(_RPC_RE, js, 15)

    host_counts: dict[str, int] = {}
    for m in _HOST_RE.finditer(js):
        host = m.group(1).lower()
        if any(host == n or host.endswith("." + n) for n in NOISE_HOSTS):
            continue
        host_counts[host] = host_counts.get(host, 0) + 1
    f.hosts = sorted(host_counts.items(), key=lambda kv: -kv[1])[:12]

    f.libraries = _detect(LIBRARY_SIGNATURES, js + "\n" + extra_html)
    # Tailwind: lots of utility class strings.
    if _count_up_to(_UTILITY_RE, js, 80) >= 80 and _FLEX_RE.search(js):
        f.libraries.append("Tailwind CSS")
    return f


def _unique_matches(pattern: re.Pattern[str], text: str, limit: int) -> list[str]:
    found: dict[str, None] = {}
    for m in pattern.finditer(text):
        found[m.group(1)] = None
        if len(found) >= limit:
            break
    return list(found)


def _merge_unique(into: list[Any], items: Sequence[Any], key: Callable[[Any], str] = lambda x: x) -> None:
    seen = {key(x) for x in into}
    for item in items:
        k = key(item)
        if k not in seen:
            seen.add(k)
            into.append(item)


# --------------------------------------------------------------------------- helpers


def _display_src(url: str) -> str:
    return re.sub(r"^https?://", "", url, flags=re.IGNORECASE).removesuffix("/")


def _same_origin(a: str, b: str) -> bool:
    try:
        pa, pb = urlsplit(a), urlsplit(b)
        return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)
    except ValueError:
        return False


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _is_blocked_status(status: int) -> bool:
    return status in (401, 403, 999, 451)


_BOT_TITLE_RE = re.compile(
    r"^(just a moment|attention required|access denied|are you a robot|robot or human|verify you are human|"
    r"security check|ddos protection|checking your browser|one more step|pardon our interruption|please verify|"
    r"bot verification)"
)
_BOT_HTML = ("cf-chl", "challenge-platform", "_cf_chl_opt", "g-recaptcha-response", "hcaptcha.com/1/api")


def _looks_like_bot_wall(page: PageInfo) -> bool:
    t = page.title.lower()
    return bool(
        _BOT_TITLE_RE.match(t)
        or ("captcha" in t and len(page.text) < 600)
        or (any(s in page.html[:20_000] for s in _BOT_HTML) and len(page.text) < 600)
    )


_LOGIN_PATH_RE = re.compile(r"authwall|/login|/signin|/sign-in|/signup|/uas/|auth-bridge|/auth\b", re.IGNORECASE)
_NOTABLE_LINK_RE = re.compile(
    r"github\.com|linkedin\.com|twitter\.com|x\.com|medium\.com|dev\.to|kaggle\.com|youtube\.com|substack\.com|"
    r"behance\.net|dribbble\.com|lovable\.app|vercel\.app|netlify\.app|huggingface\.co",
    re.IGNORECASE,
)
_LOVABLE_HTML = ("lovable.dev/opengraph-image", "lovable-tagger", "gptengineer")
_PLATFORM_BASICS = frozenset(("React", "Vite", "Tailwind CSS", "Radix UI", "shadcn/ui", "Lucide", "Sonner", "Zod"))


# --------------------------------------------------------------------------- the ingester


async def ingest_web(url: str, kind: str, label: str, kind_label: str, log: IngestLog) -> SourceDoc:
    """Read a web page. Expected problems come back as a failed `SourceDoc`, never an exception."""
    parsed = normalize_url(url)
    full_url = parsed.url if parsed else url
    src = _display_src(full_url)

    def fail(error: str, **extra: Any) -> SourceDoc:
        return SourceDoc.failure(kind, kind, label, kind_label, full_url, src, error, **extra)

    if parsed is None:
        return fail("That does not look like a valid web link.")
    host = parsed.hostname.lower().removeprefix("www.")
    is_linkedin = host == "linkedin.com" or host.endswith(".linkedin.com") or host == "lnkd.in"
    is_lovable_app = host.endswith((".lovable.app", ".lovableproject.com"))

    # Lovable editor links (lovable.dev/projects/...) are the Lovable app itself, not the user's published app.
    if (host == "lovable.dev" or host.endswith(".lovable.dev")) and re.match(r"/projects?\b", parsed.path, re.IGNORECASE):
        log(f"Opened {src}")
        return fail(LOGIN_WALL_LOVABLE)

    try:
        page = await fetch_text(
            full_url, MAX_BODY_BYTES, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )
    except Exception as exc:  # noqa: BLE001 - every network failure becomes a readable message
        return fail(LINKEDIN_BLOCKED if is_linkedin else _friendly_network_error(exc))

    final_host = _hostname(page.url)
    final = urlsplit(page.url)
    login_redirect = bool(_LOGIN_PATH_RE.search(final.path + ("?" + final.query if final.query else "")))

    if is_linkedin and (
        _is_blocked_status(page.status) or page.status >= 400 or login_redirect or not final_host.endswith("linkedin.com")
    ):
        return fail(LINKEDIN_BLOCKED)
    if is_lovable_app and (final_host == "lovable.dev" or final_host.endswith(".lovable.dev")):
        return fail(LOGIN_WALL_LOVABLE)
    if page.status in (404, 410) and is_lovable_app:
        return fail("That Lovable app was not found. It may be unpublished or private. Publish it and share the .lovable.app URL.")
    if page.status in (404, 410):
        return fail(f"That page was not found (HTTP {page.status}). Check the link.")
    if _is_blocked_status(page.status):
        return fail(f"That site blocks automated reading or needs a login (HTTP {page.status}).")
    if page.status == 429:
        return fail("That site is rate limiting requests. Try again in a few minutes.")
    if page.status >= 500:
        return fail(f"That site returned a server error (HTTP {page.status}).")
    if page.status >= 400:
        return fail(f"That page could not be opened (HTTP {page.status}).")

    log(f"Opened {src}")

    ct = page.content_type.lower()
    is_html = not ct or bool(re.search(r"html|xhtml|xml", ct)) or bool(
        re.match(r"\s*<(?:!doctype|html)", page.text[:200], re.IGNORECASE)
    )
    if not is_html:
        if "application/pdf" in ct:
            return fail("That link points to a PDF, which cannot be read here. Paste its text into Extra context instead.")
        if re.match(r"text/", ct) or "json" in ct:
            text = page.text.strip()[:PAGE_TEXT_CHARS]
            return SourceDoc(
                id=kind, kind=kind, label=label, url=full_url, src=src, kind_label=kind_label,
                text=f"# {src}\n\n{text}",
                meta={"title": "", "description": "", "spa": False, "libraries": [], "words": len(text.split()),
                      "status": page.status},
                quick_signals=find_terms(text, 6),
            )  # fmt: skip
        return fail(f"That link is not a web page ({ct.split(';')[0]}).")

    info = await asyncio.to_thread(parse_page, page.text, page.url)
    if _looks_like_bot_wall(info):
        return fail("That site shows a bot check, so it could not be read.")
    if is_linkedin and re.match(r"(sign up|log in|sign in|join linkedin)", info.title, re.IGNORECASE):
        return fail(LINKEDIN_BLOCKED)

    has_scripts = bool(info.module_scripts or info.plain_scripts)
    thin = len(info.text) < THIN_TEXT_CHARS
    spa = thin and bool(info.module_scripts)

    # SPA: read the JS bundle for human UI copy and the libraries it ships.
    findings: BundleFindings | None = None
    bundle_notes: list[str] = []
    if thin and has_scripts:
        candidates: list[str] = []
        for s in info.module_scripts or info.plain_scripts:
            try:
                absolute = urljoin(page.url, s)
            except ValueError:
                continue
            if _same_origin(absolute, page.url) and re.search(r"\.m?js(?:$|\?)", absolute, re.IGNORECASE) and absolute not in candidates:
                candidates.append(absolute)
        # The main entry bundle holds the app; keep the order of appearance but prefer "index" files first.
        candidates.sort(key=lambda c: not re.search(r"/index[-.]", c, re.IGNORECASE))
        if candidates:
            log("SPA detected, reading app bundle" if spa else "Thin page, reading its scripts")
            merged = BundleFindings()
            read = 0
            for bundle_url in candidates[:MAX_BUNDLES]:
                try:
                    js = await fetch_text(bundle_url, MAX_BODY_BYTES, "*/*")
                except Exception:  # noqa: BLE001, S112 - a bundle that fails to load is not fatal
                    continue
                if js.status >= 400 or not js.text:
                    continue
                read += 1
                f = await asyncio.to_thread(analyze_bundle, js.text, info.html)
                _merge_unique(merged.prop_strings, f.prop_strings)
                _merge_unique(merged.sentences, f.sentences, lambda x: x[0])
                _merge_unique(merged.routes, f.routes)
                _merge_unique(merged.tables, f.tables)
                _merge_unique(merged.functions, f.functions)
                _merge_unique(merged.hosts, f.hosts, lambda x: x[0])
                _merge_unique(merged.libraries, f.libraries)
                if js.truncated:
                    bundle_notes.append(
                        f"{bundle_url.rsplit('/', 1)[-1]} was larger than {MAX_BODY_BYTES // 1024 // 1024} MB and was read partially."
                    )
            if read > 0:
                findings = merged
            else:
                bundle_notes.append("The app bundle could not be downloaded, so only the page shell was read.")

    # Libraries visible in the HTML itself (works for non-SPA pages too).
    html_libraries = _detect(LIBRARY_SIGNATURES, info.html, skip=("Vite", "React"))
    html_frameworks = _detect(HTML_SIGNATURES, info.html)
    if _count_up_to(_HTML_UTILITY_RE, info.html, 15) >= 15:
        html_frameworks.append("Tailwind CSS")
    libraries: list[str] = []
    _merge_unique(libraries, html_frameworks)
    _merge_unique(libraries, findings.libraries if findings else [])
    _merge_unique(libraries, html_libraries)
    # On Medium, Kaggle, LinkedIn... the stack belongs to the platform, not to the person.
    if is_known_platform(final_host) or is_known_platform(host):
        libraries.clear()
    html_lower = info.html.lower()
    built_with_lovable = (
        any(s in html_lower for s in _LOVABLE_HTML) or "lovable" in info.generator.lower() or host.endswith(".lovable.app")
    )

    # Rank sentence-like strings: prop copy first, then the most sentence-like leftovers.
    ui_strings: list[str] = []
    if findings:
        ui_strings.extend(findings.prop_strings)
        known = set(ui_strings)
        for s, _score in sorted(findings.sentences, key=lambda x: -x[1]):
            if len(ui_strings) >= MAX_UI_STRINGS:
                break
            if s not in known:
                known.add(s)
                ui_strings.append(s)
        del ui_strings[MAX_UI_STRINGS:]

    # Markdown content. Prefer the social title when the <title> is only a fragment of it.
    if info.og_title and info.title and len(info.og_title) > len(info.title) and info.title in info.og_title:
        title = info.og_title
    else:
        title = info.title or info.og_title or info.site_name or host
    description = info.description or info.og_description
    out: list[str] = [f"# {title}", "", f"URL: {page.url}"]
    if page.url != full_url and final_host != host:
        out.append(f"(redirected from {src})")
    if description:
        out.append(f"Description: {description}")
    if info.og_title and info.og_title != info.title:
        out.append(f"Social title: {info.og_title}")
    if info.og_description and info.og_description != description:
        out.append(f"Social description: {info.og_description}")
    if info.site_name:
        out.append(f"Site name: {info.site_name}")
    if info.author:
        out.append(f"Author: {info.author}")
    if built_with_lovable:
        out.append("Built with: Lovable (Vite + React)")

    if info.structured:
        out += ["", "## Structured data", *(f"- {s}" for s in info.structured)]

    # The page text already carries headings as "## ..." lines; only list them separately when it does not.
    headings_in_text = sum(1 for _, h in info.headings if h in info.text)
    if info.headings and headings_in_text < len(info.headings) * 0.8:
        out += ["", "## Headings", *(f"- h{level}: {h}" for level, h in info.headings)]

    page_text = info.text[:PAGE_TEXT_CHARS] + "\n[…truncated]" if len(info.text) > PAGE_TEXT_CHARS else info.text
    if page_text:
        out += ["", "## Page text", page_text]
    elif spa:
        out += ["", "## Page text", "(The page is rendered by JavaScript, so its HTML has no visible text.)"]

    if ui_strings:
        out += ["", "## UI copy found in the app bundle", *(f"- {s}" for s in ui_strings)]
    if findings and findings.routes:
        out += ["", "## Routes in the app", ", ".join(findings.routes)]
    if findings and (findings.tables or findings.functions):
        out += ["", "## Backend hints"]
        if findings.tables:
            out.append(f"- Tables or storage buckets queried: {', '.join(findings.tables)}")
        if findings.functions:
            out.append(f"- Functions or RPCs called: {', '.join(findings.functions)}")
    if findings and findings.hosts:
        out += ["", "## External services referenced in the bundle", ", ".join(h for h, _ in findings.hosts)]
    if libraries:
        out += ["", "## Libraries and services detected", ", ".join(libraries)]

    # Outbound links
    outbound = [link for link in info.links if _hostname(link) != final_host]
    outbound_hosts: dict[str, int] = {}
    for link in outbound:
        h = _hostname(link)
        outbound_hosts[h] = outbound_hosts.get(h, 0) + 1
    if outbound:
        notable = list(dict.fromkeys(link for link in outbound if _NOTABLE_LINK_RE.search(link)))[:12]
        top = sorted(outbound_hosts.items(), key=lambda kv: -kv[1])[:8]
        out += [
            "",
            "## Links",
            f"- {plural(len(outbound), 'outbound link')} to {plural(len(outbound_hosts), 'site')}: "
            + ", ".join(f"{h} ({n})" if n > 1 else h for h, n in top),
        ]
        if notable:
            out.append(f"- Notable: {', '.join(notable)}")

    notes: list[str] = []
    if is_linkedin:
        notes.append("This is the public, signed-out view of the LinkedIn profile, so it can be partial. Ignore the sign-in form text.")
    if page.truncated:
        notes.append(f"The page was larger than {MAX_BODY_BYTES // 1024 // 1024} MB and was read partially.")
    notes.extend(bundle_notes)
    if notes:
        out += ["", "## Notes on this data", *(f"- {n}" for n in notes)]

    content = "\n".join(out)[:CONTENT_CHARS]
    words = len(f"{page_text} {' '.join(ui_strings)}".split())

    if not description and not info.title and not page_text and not ui_strings:
        return fail("No readable content was found on that page.", meta={"spa": spa, "status": page.status})

    quick: list[str] = []
    if kind == "lovable":
        quick.append("Live app")
    lib_cap = 8 if spa else len(quick) + 3  # static pages: a few frameworks, then skills found in the text
    for lib in libraries:
        if lib == "Vite" or len(quick) >= lib_cap:
            continue
        if not spa and lib in NON_SKILL_SIGNALS:
            continue
        quick.append(lib)
    if not spa:
        # On LinkedIn the page text is mostly other people's names and chrome, so only trust the description.
        term_source = description if is_linkedin else f"{description}\n{page_text}"
        for term in await asyncio.to_thread(find_terms, term_source, 8):
            if len(quick) >= 8:
                break
            if term not in quick:
                quick.append(term)

    top_services = [lib for lib in libraries if lib not in _PLATFORM_BASICS][:3]
    if spa and findings:
        log(", ".join([f"Found {plural(len(ui_strings), 'UI string')}", *top_services]))
    else:
        log(f"Read {words:,} words" + (f", {plural(len(info.headings), 'heading')}" if info.headings else ""))

    return SourceDoc(
        id=kind,
        kind=kind,
        label=label,
        url=full_url,
        src=src,
        kind_label=kind_label,
        text=content,
        notes=notes,
        meta={
            "title": title,
            "description": description,
            "spa": spa,
            "libraries": libraries,
            "words": words,
            "status": page.status,
            "finalUrl": page.url,
            "headings": len(info.headings),
            "outboundLinks": len(outbound),
            "uiStrings": len(ui_strings),
            "routes": findings.routes if findings else [],
            "builtWithLovable": built_with_lovable,
        },
        quick_signals=quick[:8],
    )
