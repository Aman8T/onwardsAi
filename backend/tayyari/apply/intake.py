"""Job intake: a pasted link -> the ATS behind it, the application form's address, and the job (title, company, location).

Fingerprinting is by URL first (exact hosts and path shapes), then by the page's HTML (script and form markers). Sites whose
terms forbid automation are never fetched: the application is tracked with the link for a manual application. Pages come
through the SSRF-guarded fetch (`ingest.web.fetch_text`) behind the `PageFetcher` Protocol, so tests use saved HTML.
The page is data, never instructions: nothing in it is followed, and links inside it are not opened.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit, urlunsplit

from tayyari.contract.apply import AtsKind

MAX_PAGE_CHARS = 1_500_000
DESCRIPTION_MAX_CHARS = 4000


@dataclass(frozen=True, slots=True)
class Fingerprint:
    ats: AtsKind
    apply_url: str | None = None
    """Where the application form is (None: start from the job page; the AI fallback finds the form)."""
    board: str | None = None
    job_id: str | None = None


@dataclass(frozen=True, slots=True)
class JobInfo:
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Intake:
    url: str
    domain: str
    fingerprint: Fingerprint
    job: JobInfo = field(default_factory=JobInfo)
    manual_only: bool = False
    """The site forbids automation: never opened, tracked with ``url`` as the manual apply link."""
    fetch_error: str | None = None


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> tuple[str, str]:
        """``(final_url, html)`` of a public page; raises on anything that is not one."""
        ...


class IngestFetcher:
    """The SSRF-guarded fetch the rest of the app uses (public addresses only, size cap, pinned DNS)."""

    async def fetch(self, url: str) -> tuple[str, str]:
        from tayyari.ingest.web import fetch_text

        got = await fetch_text(url, MAX_PAGE_CHARS, "text/html,application/xhtml+xml")
        if got.status >= 400:
            raise ValueError(f"the page answered {got.status}")
        return got.url, got.text


# ───────────────────────── urls ─────────────────────────


def normalize_url(raw: str) -> str | None:
    """An http(s) URL with a host, without its fragment; None for anything else."""
    text = (raw or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or " " in text:
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_manual_only(url: str, domains: list[str] | tuple[str, ...]) -> bool:
    host = host_of(url)
    return any(host_is(host, d) for d in domains)


_GH_HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "job-boards.eu.greenhouse.io",
)
_LEVER_HOSTS = ("jobs.lever.co", "jobs.eu.lever.co")
_ASHBY_HOST = "jobs.ashbyhq.com"
_SEG = r"[A-Za-z0-9_.\-]+"


def fingerprint_url(url: str) -> Fingerprint:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    query = parse_qs(parts.query)
    if host in _GH_HOSTS:
        if path.startswith("/embed/job_app"):
            board, token = next(iter(query.get("for", [])), None), next(iter(query.get("token", [])), None)
            if board and token:
                return Fingerprint("greenhouse", url, board, token)
        m = re.fullmatch(rf"/({_SEG})/jobs/(\d+)", path)
        if m:
            return Fingerprint("greenhouse", urlunsplit(("https", host, path, "", "")), m[1], m[2])
        return Fingerprint("greenhouse")
    if host in _LEVER_HOSTS:
        m = re.fullmatch(rf"/({_SEG})/([0-9a-fA-F\-]{{36}})(?:/apply)?", path)
        if m:
            return Fingerprint("lever", f"https://{host}/{m[1]}/{m[2]}/apply", m[1], m[2])
        return Fingerprint("lever")
    if host == _ASHBY_HOST:
        m = re.fullmatch(rf"/({_SEG})/([0-9a-fA-F\-]{{36}})(?:/application)?", path)
        if m:
            return Fingerprint("ashby", f"https://{host}/{m[1]}/{m[2]}/application", m[1], m[2])
        return Fingerprint("ashby")
    if (
        host.endswith(".myworkdayjobs.com")
        or host.endswith(".myworkdaysite.com")
        or host == "myworkdaysite.com"
    ):
        return Fingerprint("workday")
    if "gh_jid" in query:
        return Fingerprint("greenhouse", None, None, query["gh_jid"][0])
    if "ashby_jid" in query:
        return Fingerprint("ashby", None, None, query["ashby_jid"][0])
    if "lever-origin" in query or "lever-source" in query:
        return Fingerprint("lever")
    return Fingerprint("unknown")


_GH_EMBED = re.compile(
    r"boards(?:\.eu)?\.greenhouse\.io/embed/job_(?:board|app)[^\"']*?for=([A-Za-z0-9_\-]+)", re.I
)


def fingerprint_html(page: str, url: str, known: Fingerprint) -> Fingerprint:
    """Refine ``known`` (from the URL) with the page's own markers; the URL wins when it was already specific."""
    if known.ats != "unknown" and known.apply_url:
        return known
    low = page.lower()
    if known.ats == "greenhouse" or "greenhouse.io" in low or "grnhse" in low or "job_application[" in low:
        board = known.board or (m[1] if (m := _GH_EMBED.search(page)) else None)
        if board and known.job_id:
            return Fingerprint(
                "greenhouse",
                f"https://boards.greenhouse.io/embed/job_app?for={board}&token={known.job_id}",
                board,
                known.job_id,
            )
        if 'id="application_form"' in low or 'id="application-form"' in low:
            return Fingerprint("greenhouse", url, board, known.job_id)
        return Fingerprint("greenhouse", None, board, known.job_id)
    if "jobs.lever.co" in low or "lever-job" in low or 'name="urls[linkedin]"' in low:
        apply = url if 'id="application-form"' in low or 'name="resume"' in low else None
        return Fingerprint("lever", apply)
    if "ashbyhq.com" in low or "_systemfield_" in low:
        return Fingerprint("ashby", url if "_systemfield_" in low else None)
    if "myworkdayjobs.com" in low or 'data-automation-id="jobpostingheader"' in low:
        return Fingerprint("workday")
    return known


# ───────────────────────── the job ─────────────────────────

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_META = re.compile(
    r"<meta\s+[^>]*(?:property|name)=[\"']([^\"']+)[\"'][^>]*content=[\"']([^\"']*)[\"']", re.I
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def text_of(fragment: str) -> str:
    return _WS.sub(" ", htmllib.unescape(_TAG.sub(" ", htmllib.unescape(fragment)))).strip()


def _job_posting(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list):
        for item in data:
            found = _job_posting(item)
            if found:
                return found
        return None
    if isinstance(data, dict):
        kind = data.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return data
        return _job_posting(data.get("@graph"))
    return None


def _location(ld: dict[str, Any]) -> str | None:
    loc = ld.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = [addr.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
            joined = ", ".join(str(p) for p in parts if isinstance(p, str) and p)
            return joined or None
        if isinstance(addr, str):
            return addr
    return None


def extract_job(page: str, ats: AtsKind) -> JobInfo:
    """Title, company, location and a description excerpt: JSON-LD JobPosting first, then the ATS's own markup, then meta."""
    for raw in _LD.findall(page):
        try:
            ld = _job_posting(json.loads(raw))
        except ValueError:
            continue
        if ld:
            org = ld.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else org if isinstance(org, str) else None
            desc = text_of(str(ld.get("description") or ""))[:DESCRIPTION_MAX_CHARS] or None
            return JobInfo(text_of(str(ld.get("title") or "")) or None, company, _location(ld), desc)
    meta = {k.lower(): htmllib.unescape(v) for k, v in _META.findall(page)}
    title = None
    for pattern in (r"<h1[^>]*class=[\"'][^\"']*(?:app-title|section-header|posting-headline)[^\"']*[\"'][^>]*>(.*?)</h1>",
                    r"<div class=[\"']posting-headline[\"'][^>]*>\s*<h2[^>]*>(.*?)</h2>", r"<h1[^>]*>(.*?)</h1>"):  # fmt: skip
        m = re.search(pattern, page, re.I | re.S)
        if m and text_of(m[1]):
            title = text_of(m[1])
            break
    title = title or meta.get("og:title") or (text_of(t[1]) if (t := _TITLE.search(page)) else None)
    company = meta.get("og:site_name")
    m = re.search(r"class=[\"'](?:company-name|company)[\"'][^>]*>(.*?)<", page, re.I | re.S)
    if m and text_of(m[1]):
        company = text_of(m[1]).removeprefix("at ").strip()
    location = None
    m = re.search(
        r"class=[\"'][^\"']*(?:location|sort-by-location)[^\"']*[\"'][^>]*>(.*?)</", page, re.I | re.S
    )
    if m and text_of(m[1]):
        location = text_of(m[1])
    desc = meta.get("og:description") or meta.get("description")
    return JobInfo(title, company, location, desc[:DESCRIPTION_MAX_CHARS] if desc else None)


async def intake(url: str, fetcher: PageFetcher, manual_only_domains: list[str] | tuple[str, ...]) -> Intake:
    """Read a pasted link. Raises ValueError for something that is not a web link."""
    clean = normalize_url(url)
    if clean is None:
        raise ValueError("That does not look like a web link.")
    domain = host_of(clean)
    known = fingerprint_url(clean)
    if is_manual_only(clean, manual_only_domains):
        return Intake(clean, domain, Fingerprint("unknown"), manual_only=True)
    try:
        final_url, page = await fetcher.fetch(clean)
    except (
        Exception
    ) as exc:  # the engine can still try the page in a browser; the person sees why the read failed
        reason = str(exc).strip()[:200] or type(exc).__name__
        return Intake(clean, domain, known, fetch_error=reason)
    found = fingerprint_html(page, final_url, known)
    return Intake(clean, domain, found, extract_job(page, found.ats))


__all__ = [
    "Fingerprint",
    "IngestFetcher",
    "Intake",
    "JobInfo",
    "PageFetcher",
    "extract_job",
    "fingerprint_html",
    "fingerprint_url",
    "host_is",
    "host_of",
    "intake",
    "is_manual_only",
    "normalize_url",
    "text_of",
]
