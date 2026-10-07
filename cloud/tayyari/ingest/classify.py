"""Source data model and link classification.

`SourceDoc` is what deterministic ingestion produces for one source (a resume, a GitHub profile, a
web page, pasted notes) before any AI reads it. `classify_url` turns whatever the user pasted into
a normalised http(s) URL plus the kind of source it is (github / lovable / linkedin / portfolio /
link), and `usable_links` classifies, de-duplicates and caps a list of pasted links.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

MAX_LINKS = 8

IngestLog = Callable[[str], None]
"""Progress callback for one source: one short, human line per step ("Fetched profile, 34 public repos")."""


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    """"1 repo", "2 repos"."""
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"


def _fallback_sub(meta: dict[str, Any], kind_label: str, error: str | None) -> str:
    """Stat line shown under a source when no reader refined it: "34 repos · 9 languages"."""
    if error:
        return "Could not be read"
    parts: list[str] = []
    repos = meta.get("repos", meta.get("publicRepos"))
    if isinstance(repos, int):
        parts.append(plural(repos, "repo"))
    langs = meta.get("languages")
    if isinstance(langs, list) and langs:
        parts.append(plural(len(langs), "language"))
    pages = meta.get("pages")
    if not parts and isinstance(pages, int):
        parts.append(plural(pages, "page"))
    cases = meta.get("caseStudies")
    if isinstance(cases, int):
        parts.append(plural(cases, "case study", "case studies"))
    return " · ".join(parts) or kind_label


@dataclass
class SourceDoc:
    """One source after deterministic fetching/extraction, before any AI reads it."""

    id: str
    """Stable id used everywhere downstream: "resume", "github", "lovable-1", "portfolio-1", "link-2", "notes"."""
    kind: str
    """One of resume, github, lovable, linkedin, portfolio, link, notes."""
    label: str
    """Display label: "Resume", "GitHub", "Lovable", "Portfolio", "Kaggle", "LinkedIn", "Notes"."""
    sub: str = ""
    """Short stat line shown under the label ("34 repos · 9 languages"); derived from `meta` when left empty."""
    url: str | None = None
    ok: bool = True
    text: str = ""
    """Text prepared for the reader agent, already trimmed to a sane budget (at most ~40k chars)."""
    notes: list[str] = field(default_factory=list)
    """Caveats found while fetching (partial data, rate limits...)."""
    meta: dict[str, Any] = field(default_factory=dict)
    """Deterministic facts gathered while fetching, e.g. {"repos": 34, "languages": [...]}. JSON-serialisable."""
    error: str | None = None
    src: str = ""
    """What to show as the source: file name, or URL without protocol."""
    kind_label: str = ""
    """"Resume · PDF", "GitHub profile", "Lovable app", "Portfolio site", "Web page"."""
    quick_signals: list[str] = field(default_factory=list)
    """Cheap signals found without AI (languages, libraries...), streamed to the UI early."""
    pdf_bytes: bytes | None = field(default=None, repr=False)
    """The original PDF when its text could not be extracted (scanned resume): attach it to the reader call."""

    def __post_init__(self) -> None:
        if self.error:
            self.ok = False
        if not self.sub:
            self.sub = _fallback_sub(self.meta, self.kind_label, self.error)

    @classmethod
    def failure(
        cls, id: str, kind: str, label: str, kind_label: str, url: str | None, src: str, error: str, **extra: Any
    ) -> SourceDoc:
        """A source that could not be read; the pipeline still shows it, marked as failed."""
        return cls(id=id, kind=kind, label=label, url=url, src=src, kind_label=kind_label, error=error, **extra)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form for the stored chat context.

        `text` is the canonical key; `content` repeats it because the bundled sample context (and the
        original Node store) call it that, so one reader handles both.
        """
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "sub": self.sub,
            "ok": self.ok,
            "text": self.text,
            "content": self.text,
            "notes": list(self.notes),
            "meta": self.meta,
            "src": self.src,
            "kind_label": self.kind_label,
        }
        if self.url:
            out["url"] = self.url
        if self.error:
            out["error"] = self.error
        return out


# --------------------------------------------------------------------------- URL parsing

_WRAP_START = frozenset("<(\"'`")
_WRAP_END = frozenset(">)\"'`,;.")
_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.\-]{0,30}://", re.IGNORECASE)
_NON_WEB_RE = re.compile(r"(?:mailto|tel|javascript|data|file|ftp|sms|blob):", re.IGNORECASE)
_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = "/%:@!$&'()*+,;=-._~?[]"


@dataclass(frozen=True)
class ParsedUrl:
    """A normalised http(s) URL (the parts of a WHATWG `URL` the ingesters use)."""

    scheme: str
    hostname: str
    """Lower-cased, punycode for international names; IPv6 literals keep their brackets."""
    port: int | None
    path: str
    query: str
    """Empty, or including the leading "?"."""

    @property
    def host(self) -> str:
        """`hostname[:port]` with default ports dropped."""
        return self.hostname + (f":{self.port}" if self.port is not None else "")

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}"

    @property
    def url(self) -> str:
        return f"{self.origin}{self.path}{self.query}"


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 section 5.2.4 for an absolute path ("/a/b/../c" -> "/a/c")."""
    if "/." not in path:
        return path
    segments = path.split("/")[1:]
    out: list[str] = []
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg == "..":
            if out:
                out.pop()
            if last:
                out.append("")
        elif seg == ".":
            if last:
                out.append("")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def _strip_wrapping(s: str) -> str:
    """Remove punctuation people paste around links: <url>, "url", (url), a trailing "," or "." (linear time)."""
    i, j = 0, len(s)
    while i < j and (s[i] in _WRAP_START or s[i].isspace()):
        i += 1
    while j > i and (s[j - 1] in _WRAP_END or s[j - 1].isspace()):
        j -= 1
    return s[i:j]


def _valid_hostname(host: str) -> bool:
    """A real dot-separated host ("hello" or "localhost" is not a link). Bounded checks, no backtracking."""
    if len(host) > 253:
        return False
    labels = host.split(".")
    return len(labels) >= 2 and all(_HOST_LABEL_RE.fullmatch(label) for label in labels)


def normalize_url(raw: str) -> ParsedUrl | None:
    """Normalise user input into a URL, or None when it is not a plausible http(s) address."""
    s = _strip_wrapping(raw or "")
    if not s or any(c.isspace() for c in s):
        return None

    if _SCHEME_RE.match(s):
        pass  # explicit scheme, validated below
    elif _NON_WEB_RE.match(s):
        return None
    else:
        s = "https://" + s.removeprefix("//")

    try:
        parts = urlsplit(s)
        scheme = parts.scheme.lower()
        port = parts.port
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS:
        return None
    userinfo, _, hostport = parts.netloc.rpartition("@")
    if userinfo:
        return None

    if hostport.startswith("["):
        try:
            host = f"[{ipaddress.IPv6Address(parts.hostname or '')}]"
        except ValueError:
            return None
    else:
        try:
            host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
        if not _valid_hostname(host):
            return None

    path = quote(_remove_dot_segments(parts.path or "/"), safe=_PATH_SAFE)
    query = ("?" + quote(parts.query, safe=_QUERY_SAFE)) if parts.query else ""
    return ParsedUrl(scheme, host, None if port == _DEFAULT_PORTS[scheme] else port, path, query)


# --------------------------------------------------------------------------- classification

def strip_www(host: str) -> str:
    return host.removeprefix("www.")


def host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


_PORTFOLIO_DOMAINS = (
    "behance.net", "dribbble.com", "github.io", "vercel.app", "netlify.app", "framer.website", "framer.ai",
    "read.cv", "notion.site", "carrd.co", "webflow.io", "pages.dev", "wixsite.com", "squarespace.com",
    "about.me", "bento.me", "super.so",
)  # fmt: skip

_FRIENDLY_LABELS = (
    ("kaggle.com", "Kaggle"), ("leetcode.com", "LeetCode"), ("stackoverflow.com", "Stack Overflow"),
    ("stackexchange.com", "Stack Exchange"), ("huggingface.co", "Hugging Face"), ("gitlab.com", "GitLab"),
    ("bitbucket.org", "Bitbucket"), ("npmjs.com", "npm"), ("pypi.org", "PyPI"), ("youtube.com", "YouTube"),
    ("youtu.be", "YouTube"), ("twitter.com", "X"), ("x.com", "X"), ("codepen.io", "CodePen"),
    ("codesandbox.io", "CodeSandbox"), ("hackerrank.com", "HackerRank"), ("codechef.com", "CodeChef"),
    ("codeforces.com", "Codeforces"), ("geeksforgeeks.org", "GeeksforGeeks"), ("wellfound.com", "Wellfound"),
    ("producthunt.com", "Product Hunt"), ("figma.com", "Figma"), ("medium.com", "Blog"), ("dev.to", "Blog"),
    ("hashnode.com", "Blog"), ("hashnode.dev", "Blog"), ("substack.com", "Blog"), ("wordpress.com", "Blog"),
    ("blogspot.com", "Blog"), ("ghost.io", "Blog"),
)  # fmt: skip

_MULTI_PART_TLDS = frozenset((
    "co.uk org.uk ac.uk gov.uk co.in org.in ac.in net.in gov.in com.au net.au org.au edu.au co.nz co.jp com.br "
    "com.mx com.sg com.hk com.tr co.za com.cn co.kr com.ar com.ng co.il com.pk com.bd"
).split())

_GITHUB_RESERVED = frozenset(("orgs", "topics", "sponsors", "marketplace", "features", "about", "pricing", "settings", "login"))
_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


def _second_level_label(host: str) -> str:
    """"docs.kaggle.com" -> "Kaggle", "my-site.co.uk" -> "My-site"."""
    parts = strip_www(host).split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_PART_TLDS:
        sld = parts[-3]
    elif len(parts) >= 2:
        sld = parts[-2]
    else:
        sld = parts[0]
    return sld[:1].upper() + sld[1:]


def _friendly_label(host: str) -> str:
    if _IPV4_RE.fullmatch(host) or host.startswith("["):
        return host  # IP literal
    for domain, label in _FRIENDLY_LABELS:
        if host_is(host, domain):
            return label
    return _second_level_label(host)


def is_known_platform(hostname: str) -> bool:
    """True for sites where the person is a user of someone else's platform (Medium, Kaggle, LinkedIn...)."""
    host = strip_www(hostname.lower())
    return (
        host_is(host, "linkedin.com")
        or host_is(host, "github.com")
        or any(host_is(host, domain) for domain, _ in _FRIENDLY_LABELS)
    )


@dataclass(frozen=True)
class ClassifiedUrl:
    url: str
    """Normalised absolute URL (https:// added, fragment removed)."""
    kind: str
    label: str
    """Short label: "GitHub", "Lovable", "Kaggle"..."""
    kind_label: str
    """What the link is: "GitHub profile", "Lovable app", "Web page"..."""


def classify_url(raw: str) -> ClassifiedUrl | None:
    """Classify a pasted link, or None when it is not a plausible http(s) address."""
    u = normalize_url(raw)
    if u is None:
        return None
    host = strip_www(u.hostname.lower())
    segments = [s for s in u.path.split("/") if s]
    # Drop a bare trailing slash so "https://overreacted.io" stays tidy.
    url = u.origin if u.path == "/" and not u.query else u.url

    def make(kind: str, label: str, kind_label: str) -> ClassifiedUrl:
        return ClassifiedUrl(url, kind, label, kind_label)

    if host == "github.com":
        is_repo = len(segments) >= 2 and segments[0].lower() not in _GITHUB_RESERVED
        return make("github", "GitHub", "GitHub repo" if is_repo else "GitHub profile")
    if host_is(host, "lovable.app") or host_is(host, "lovable.dev") or host_is(host, "lovableproject.com"):
        return make("lovable", "Lovable", "Lovable app")
    if host_is(host, "linkedin.com") or host == "lnkd.in":
        return make("linkedin", "LinkedIn", "LinkedIn profile")
    if any(host_is(host, d) for d in _PORTFOLIO_DOMAINS) or "portfolio" in (u.hostname + u.path).lower():
        return make("portfolio", "Portfolio", "Portfolio site")
    return make("link", _friendly_label(host), "Web page")


def classify_link(url: str) -> tuple[str, str]:
    """(kind, normalised url) for a pasted link; unparsable input comes back as ("link", stripped input)."""
    c = classify_url(url)
    return (c.kind, c.url) if c else ("link", (url or "").strip())


def usable_links(links: list[str]) -> list[ClassifiedUrl]:
    """Classify, de-duplicate and cap (MAX_LINKS) the user's links; invalid ones are dropped."""
    seen: set[str] = set()
    out: list[ClassifiedUrl] = []
    for raw in links:
        if len(out) >= MAX_LINKS:
            break
        if not isinstance(raw, str) or not raw.strip():
            continue
        c = classify_url(raw)
        if c is None:
            continue
        key = c.url.lower().removesuffix("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
