"""Job intake: a pasted link -> the ATS behind it, the application form's address, and the job (title, company, location).

Fingerprinting is by URL first (exact hosts and path shapes), then by the page's HTML (script and form markers). Sites whose
terms forbid automation are never fetched: the application is tracked with the link for a manual application. A link that
redirects onto such a site is treated the same way: its page is fetched by the redirect but never parsed or used. Pages come through the SSRF-guarded fetch
(`ingest.web.fetch_text`) behind the `PageFetcher` Protocol, so tests use saved HTML. The page is data, never
instructions: nothing in it is followed, and links inside it are not opened.

The start checks (`apply.checks.evaluate_start`) use three more things from here:

    check_live      is the posting still open? One GET of the ATS's own public read endpoint (Greenhouse boards-api,
                    Lever postings v0, Ashby posting-api) through the `StatusFetcher` Protocol. 404 means closed;
                    anything unclear (a fetch problem, a timeout, a 5xx, an answer of an unexpected shape) is
                    ``unknown``, which offers the employer's site and is never shown as closed.
    posting_key     one key per posting, whatever link reached it (job page, /apply, the embed): the de-duplication
                    key of `applications` (unique per person).
    same_posting    does the job behind a pasted application link look like the match's posting (title, company)?
"""

from __future__ import annotations

import hashlib
import html as htmllib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol
from urllib.parse import parse_qs, urlsplit, urlunsplit

from tayyari.contract.apply import AtsKind
from tayyari.core.logging import get_logger

log = get_logger(__name__)

MAX_PAGE_CHARS = 1_500_000
MAX_API_CHARS = 1_500_000
"""Cap on an ATS read endpoint's answer. An Ashby board lists every open job, so a big board can be cut short; the
check then looks for the job id in the text it got and says ``unknown`` rather than ``closed`` when it is not there."""
DESCRIPTION_MAX_CHARS = 4000
POSTING_KEY_MAX = 64
"""``applications.posting_key`` is ``String(64)``; a longer ``<ats>:<board>:<job id>`` is hashed (sha256 hex is 64)."""
JSON_ACCEPT: Final = "application/json"

Liveness = Literal["live", "closed", "unknown"]
"""``live``: the ATS lists the job. ``closed``: the ATS answered that it does not. ``unknown``: it could not be told."""


@dataclass(frozen=True, slots=True)
class Fingerprint:
    ats: AtsKind
    apply_url: str | None = None
    """Where the application form is (None: start from the job page and open the form from there)."""
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
    """The site forbids automation (or the link redirected onto one): never read, tracked with ``url`` as the manual
    apply link."""
    fetch_error: str | None = None
    fetch_status: int | None = None
    """The HTTP status when the page answered with an error (404 for a removed job page); None when it was read or
    could not be reached at all."""


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> tuple[str, str]:
        """``(final_url, html)`` of a public page; raises on anything that is not one."""
        ...


class PageAnswered(ValueError):
    """The page answered with an HTTP error. ``status`` is kept, so a removed job (404) is told apart from an
    unreachable one."""

    def __init__(self, status: int, final_url: str) -> None:
        super().__init__(f"the page answered {status}")
        self.status = status
        self.final_url = final_url


class StatusFetcher(Protocol):
    async def fetch(self, url: str, accept: str) -> tuple[int, str, str]:
        """``(status, final_url, text)`` of a public address. A 4xx or 5xx answer is returned, never raised; a fetch
        that cannot be made (refused, unreachable, timed out, a bad redirect) raises."""
        ...


class IngestFetcher:
    """The SSRF-guarded fetch the rest of the app uses (public addresses only, size cap, pinned DNS)."""

    async def fetch(self, url: str) -> tuple[str, str]:
        from tayyari.ingest.web import fetch_text

        got = await fetch_text(url, MAX_PAGE_CHARS, "text/html,application/xhtml+xml")
        if got.status >= 400:
            raise PageAnswered(got.status, got.url)
        return got.url, got.text


class IngestStatusFetcher:
    """`StatusFetcher` over the same SSRF-guarded fetch: every redirect hop checked, pinned DNS, a size cap, one
    timeout. ``ingest.web.fetch_text`` returns the status of an error answer instead of raising."""

    async def fetch(self, url: str, accept: str) -> tuple[int, str, str]:
        from tayyari.ingest.web import fetch_text

        got = await fetch_text(url, MAX_API_CHARS, accept)
        return got.status, got.url, got.text


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
    r"boards(\.eu)?\.greenhouse\.io/embed/job_(?:board|app)[^\"']*?for=([A-Za-z0-9_\-]+)", re.I
)
"""A Greenhouse embed script or form on a careers page: group 1 is ``.eu`` on the EU instance, group 2 the board."""
_BOARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")
"""A board or site slug as it can go into an API path (never ``..``)."""
_GH_JOB_ID = re.compile(r"\d+")
_UUID = re.compile(r"[0-9a-fA-F\-]{36}")


def greenhouse_embed(board: str, job_id: str, *, eu: bool = False) -> str | None:
    """The Greenhouse embed form for ``board`` and ``job_id``: the one URL built from a page's content (CLAUDE.md
    rule 3). Built on a fixed Greenhouse host (the EU instance's when the page embeds an EU board) from a slug and a
    number only (None otherwise), and checked to stay on a greenhouse.io host."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", board) or not _GH_JOB_ID.fullmatch(job_id):
        return None
    host = "boards.eu.greenhouse.io" if eu else "boards.greenhouse.io"
    url = f"https://{host}/embed/job_app?for={board}&token={job_id}"
    return url if (urlsplit(url).hostname or "") in _GH_HOSTS else None


def fingerprint_html(page: str, url: str, known: Fingerprint) -> Fingerprint:
    """Refine ``known`` (from the URL) with the page's own markers; the URL wins when it was already specific."""
    if known.ats != "unknown" and known.apply_url:
        return known
    low = page.lower()
    if known.ats == "greenhouse" or "greenhouse.io" in low or "grnhse" in low or "job_application[" in low:
        m = _GH_EMBED.search(page)
        board = known.board or (m[2] if m else None)
        eu = bool(m and m[1]) if not known.board else False
        embed = greenhouse_embed(board, known.job_id, eu=eu) if board and known.job_id else None
        if embed:
            return Fingerprint("greenhouse", embed, board, known.job_id)
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
        if isinstance(exc, PageAnswered) and is_manual_only(exc.final_url, manual_only_domains):
            return Intake(clean, domain, Fingerprint("unknown"), manual_only=True)
        reason = str(exc).strip()[:200] or type(exc).__name__
        status = exc.status if isinstance(exc, PageAnswered) else None
        return Intake(clean, domain, known, fetch_error=reason, fetch_status=status)
    if is_manual_only(
        final_url, manual_only_domains
    ):  # redirected onto a board that forbids automation: not parsed
        return Intake(clean, domain, Fingerprint("unknown"), manual_only=True)
    found = fingerprint_html(page, final_url, known)
    return Intake(clean, domain, found, extract_job(page, found.ats))


# ───────────────────────── is the posting still open ─────────────────────────


def _api_host(apply_url: str | None, hosts: tuple[str, ...], eu: str, default: str) -> str | None:
    """The API host of the instance the form is on: the EU one for an EU board (``*.eu.greenhouse.io``,
    ``jobs.eu.lever.co``). Decided from the ATS's own form host only, never from an employer's domain; None when the
    form is not on one of ``hosts``, since the region cannot be known and a guess could say 'closed' wrongly."""
    host = (urlsplit(apply_url or "").hostname or "").lower()
    if host not in hosts:
        return None
    return eu if ".eu." in f".{host}" else default


def liveness_url(fp: Fingerprint) -> str | None:
    """The ATS's public read endpoint for the posting; None when the ATS has none we use or the board or job id is not
    known. Built only from the fingerprint's board and job id on the ATS's own API host, never from page content."""
    board, job_id = fp.board, fp.job_id
    if not board or not job_id or not _BOARD.fullmatch(board):
        return None
    if fp.ats == "greenhouse" and _GH_JOB_ID.fullmatch(job_id):
        host = _api_host(fp.apply_url, _GH_HOSTS, "boards-api.eu.greenhouse.io", "boards-api.greenhouse.io")
        return f"https://{host}/v1/boards/{board}/jobs/{job_id}" if host else None
    if fp.ats == "lever" and _UUID.fullmatch(job_id):
        host = _api_host(fp.apply_url, _LEVER_HOSTS, "api.eu.lever.co", "api.lever.co")
        return f"https://{host}/v0/postings/{board}/{job_id}" if host else None
    if fp.ats == "ashby" and _UUID.fullmatch(job_id):
        return f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    return None


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _read_answer(fp: Fingerprint, status: int, text: str) -> Liveness:
    """The verdict from one answer of the read endpoint. Only a definite answer is ``closed``."""
    job_id = (fp.job_id or "").lower()
    if status in (404, 410):
        return "closed"
    if status != 200:
        return "unknown"  # a 5xx, a 429, a 403: the ATS did not say
    data = _json(text)
    if fp.ats == "ashby":
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if isinstance(jobs, list):
            ids = {str(job.get("id", "")).lower() for job in jobs if isinstance(job, dict)}
            return "live" if job_id in ids else "closed"
        # cut short by the size cap, or not the documented shape: the id seen in the text is still an answer, but its
        # absence is not
        seen = re.search(rf'"id"\s*:\s*"{re.escape(job_id)}"', text, re.I)
        return "live" if seen else "unknown"
    found = data.get("id") if isinstance(data, dict) else None
    return "live" if found is not None and str(found).lower() == job_id else "unknown"


async def check_live(fp: Fingerprint, fetch: StatusFetcher) -> Liveness:
    """Is the posting behind ``fp`` still open? One GET of the ATS's public read endpoint.

    Greenhouse ``/v1/boards/{board}/jobs/{id}`` and Lever ``/v0/postings/{site}/{id}`` answer 404 for a closed job; an
    Ashby board lists its open jobs, so a job missing from the list is closed. A fetch problem, a timeout, a 5xx, an
    answer that redirected off the ATS's API host, an answer of an unexpected shape, or an ATS without a read endpoint
    gives ``unknown``.
    """
    url = liveness_url(fp)
    if url is None:
        return "unknown"
    try:
        status, final_url, text = await fetch.fetch(url, JSON_ACCEPT)
    except Exception as exc:  # FetchProblem, httpx errors, TimeoutError: unreachable is not closed
        log.info("apply_liveness", ats=fp.ats, verdict="unknown", error=type(exc).__name__)
        return "unknown"
    verdict: Liveness = (
        _read_answer(fp, status, text) if host_of(final_url) == host_of(url) else "unknown"
    )  # an answer from somewhere a redirect led is not the ATS's
    log.info("apply_liveness", ats=fp.ats, verdict=verdict, status=status)
    return verdict


# ───────────────────────── one posting, whatever link reached it ─────────────────────────


def posting_key(fp: Fingerprint, url: str) -> str:
    """``<ats>:<board>:<job id>`` (lower-cased) when the fingerprint knows both, so the job page, its /apply form and
    the Greenhouse embed share one key; else the sha256 of the normalised ``url``. At most 64 characters (a longer
    composed key is hashed). Raises ValueError when the fingerprint is not specific and ``url`` is not a web link."""
    if fp.ats in ("greenhouse", "lever", "ashby") and fp.board and fp.job_id:
        key = f"{fp.ats}:{fp.board}:{fp.job_id}".lower()
        return key if len(key) <= POSTING_KEY_MAX else hashlib.sha256(key.encode("utf-8")).hexdigest()
    clean = normalize_url(url)
    if clean is None:
        raise ValueError("That does not look like a web link.")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


_FOLD_SPLIT = re.compile(r"[^a-z0-9+#]+")
_TITLE_NOISE = frozenset(["a", "an", "and", "the", "of", "for", "at", "in", "on", "to", "with"])
_COMPANY_NOISE = frozenset(
    [
        "inc",
        "incorporated",
        "llc",
        "llp",
        "ltd",
        "limited",
        "pvt",
        "private",
        "plc",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "ag",
        "sa",
        "bv",
        "the",
    ]
)


def _tokens(text: str | None, noise: frozenset[str]) -> frozenset[str]:
    """Folded words: accents stripped (NFKD), case folded, split on everything but letters, digits, + and #."""
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").casefold()
    return frozenset(t for t in _FOLD_SPLIT.split(folded) if t and t not in noise)


def same_posting(job: JobInfo, title: str | None, company: str | None) -> bool:
    """Does the job behind an application link look like the match's posting (``title``, ``company``)?

        The titles must share most of their words after folding (case, accents, punctuation, small words): at least 3 in 4
        of the shorter title's words and at least half of the longer one's, and at least two words unless both titles are the
    same single word. When both
        companies are known, one name's words must contain the other's (legal suffixes dropped). A missing title is never
        the same posting.
    """
    mine, theirs = _tokens(job.title, _TITLE_NOISE), _tokens(title, _TITLE_NOISE)
    if not mine or not theirs:
        return False
    shared = len(mine & theirs)
    if shared / min(len(mine), len(theirs)) < 0.75 or (shared < 2 and mine != theirs):
        return False
    if shared / max(len(mine), len(theirs)) < 0.5:
        return False  # a short title inside a much longer one is a different, more specialised role
    a, b = _tokens(job.company, _COMPANY_NOISE), _tokens(company, _COMPANY_NOISE)
    return (a <= b or b <= a) if a and b else True


__all__ = [
    "Fingerprint",
    "IngestFetcher",
    "IngestStatusFetcher",
    "Intake",
    "JobInfo",
    "Liveness",
    "PageAnswered",
    "PageFetcher",
    "StatusFetcher",
    "check_live",
    "extract_job",
    "fingerprint_html",
    "fingerprint_url",
    "greenhouse_embed",
    "host_is",
    "host_of",
    "intake",
    "is_manual_only",
    "liveness_url",
    "normalize_url",
    "posting_key",
    "same_posting",
    "text_of",
]
