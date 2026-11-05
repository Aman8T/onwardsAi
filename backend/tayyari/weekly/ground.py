"""The news verifier: a model fills typed slots from search results, and code keeps only what the results and the graph back.

Each item the model returns (``url``, ``headline``, ``publication``, ``date``, ``skill``, ``why``) is dropped when

    url         it is not one of the addresses the search tool returned (compared after folding case, ``www.``, a fragment,
                tracking parameters and a trailing slash), or its site is not on the allow-list
    press       its address is a press release or paid content (``/press-releases-ani/``, ``/sponsored/``): a company's
                own words are not news, even on a newspaper's site
    skill       it is not the name of a skill on the reader's graph (the graph's own spelling is kept)
    why         it is empty, carries an address, text aimed at an AI, or a number that the headline does not have
    stale       its date (the search result's own age first, else the model's ``date``) is older than `STALE_DAYS` or
                more than a day ahead: old news must not read as this week's
    duplicate   the same address came before it

and what is kept is repaired, never trusted:

    headline    the article's own title as the search returned it when the model's headline shares too few words with it
    publication the site's name worked out from its address (a known name table, else the host's own label) unless the
                model's name is part of that host
    date        the search result's own date when it gives one (``page_age``: "October 30, 2026", "3 days ago",
                an ISO time), else the model's ``YYYY-MM-DD``; no date when neither can be read
    why         cut at a word at 200 characters

At most ``limit`` items are kept. `NewsCheck.dropped` counts every drop by reason (the known-bad fixtures prove each fires).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tayyari.contract.weekly import WeeklyNews
from tayyari.core.text import tidy
from tayyari.jobsearch.judge import has_injection

WHY_MAX_CHARS: Final = 200
HEADLINE_MAX_CHARS: Final = 160
STALE_DAYS: Final = 60
HEADLINE_OVERLAP: Final = 0.5
"""The share of the model's headline words that must be in the article's own title."""

PUBLICATIONS: Final[dict[str, str]] = {
    "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge",
    "arstechnica.com": "Ars Technica",
    "wired.com": "Wired",
    "technologyreview.com": "MIT Technology Review",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times",
    "cnbc.com": "CNBC",
    "hbr.org": "Harvard Business Review",
    "infoq.com": "InfoQ",
    "thenewstack.io": "The New Stack",
    "venturebeat.com": "VentureBeat",
    "zdnet.com": "ZDNet",
    "economictimes.indiatimes.com": "The Economic Times",
    "livemint.com": "Mint",
    "business-standard.com": "Business Standard",
    "thehindu.com": "The Hindu",
    "yourstory.com": "YourStory",
    "inc42.com": "Inc42",
    "entrackr.com": "Entrackr",
    "medianama.com": "MediaNama",
}
"""Names of the sites on the default allow-list; any other host gets its own label ("example.com" is "Example")."""

_TRACKING = re.compile(r"^(?:utm_.*|fbclid|gclid|mc_.*|ref|ref_src|cmpid|ocid|taid)$", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_PREFIX = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[T ][\d:.+\-Z]*)?", re.IGNORECASE)
_AGO = re.compile(r"(?:(\d+)|an?|one)\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)
_MONTHS: Final[dict[str, int]] = {
    name: number
    for number, names in enumerate(
        (("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
         ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
         ("december", "dec")),
        start=1,
    )
    for name in names
}  # fmt: skip
_MONTH_DAY_YEAR = re.compile(r"([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE)
_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})\s+([a-z]+)\.?,?\s+(\d{4})", re.IGNORECASE)
_DAYS_PER: Final[dict[str, int]] = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}
_PAID_PATH = re.compile(
    r"press[-_]?releases?|/pr[-_]?news/|/sponsored|/brand[-_]?(?:connect|post|studio)|/partner[-_]?content|/advertorial",
    re.IGNORECASE,
)
_ADDRESS = re.compile(r"https?://|www\.", re.IGNORECASE)
_SITE_SEPARATOR = re.compile(r"\s+[|\-–—]\s+")


@dataclass(frozen=True, slots=True)
class SearchedPage:
    """One result the search tool returned (never the model's retelling of it)."""

    url: str
    title: str
    age: str | None = None


@dataclass(slots=True)
class NewsCheck:
    items: list[WeeklyNews] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1


def normalise_url(url: object) -> str | None:
    """A result address in the form two spellings of one page share, or None when it is not an http(s) address."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    return urlunsplit(("https", host.lower().removeprefix("www."), parts.path.rstrip("/"), query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def on_allow_list(host: str, domains: Collection[str]) -> bool:
    """Whether ``host`` is one of ``domains`` or a subdomain of one (an empty list allows nothing)."""
    allowed = (item.lower().removeprefix("www.") for item in domains)
    return any(host == d or host.endswith(f".{d}") for d in allowed)


def publication_of(host: str, claimed: object) -> str:
    """The site's name: the known name for its domain; else the model's when it is part of the host; else the host's label."""
    for domain, name in PUBLICATIONS.items():
        if host == domain or host.endswith(f".{domain}"):
            return name
    text = tidy(claimed)
    squeezed = "".join(_WORD.findall(text.lower()))
    if squeezed and squeezed in "".join(_WORD.findall(host)):
        return text[:60]
    label = host.split(".")[-2] if host.count(".") >= 1 else host
    return label.capitalize()


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) >= 3]


def clean_title(title: str, publication: str) -> str:
    """The article's own title without a site name on the end ("Headline | TechCrunch")."""
    parts = _SITE_SEPARATOR.split(title.strip())
    if len(parts) > 1:
        last = "".join(_WORD.findall(parts[-1].lower()))
        name = "".join(_WORD.findall(publication.lower()))
        if last and (last in name or name in last or len(parts[-1]) <= 24):
            parts = parts[:-1]
    return tidy(" - ".join(parts))[:HEADLINE_MAX_CHARS]


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    space = head.rfind(" ")
    return f"{(head[:space] if space > limit * 0.5 else head).rstrip(' ,;:-')}…"


def _headline(claimed: object, page_title: str, publication: str) -> str:
    own = clean_title(page_title, publication)
    said = tidy(claimed)[:HEADLINE_MAX_CHARS]
    if not said:
        return own
    wanted = _words(said)
    have = set(_words(page_title))
    if not wanted or sum(1 for w in wanted if w in have) / len(wanted) < HEADLINE_OVERLAP:
        return own
    return said


def _claimed_date(claimed: object) -> date | None:
    """The model's ``date`` when it is a real ``YYYY-MM-DD``, else None."""
    if not isinstance(claimed, str) or not _ISO_DATE.fullmatch(claimed.strip()):
        return None
    try:
        return date.fromisoformat(claimed.strip())
    except ValueError:
        return None


def _calendar(year: str, month: str, day: str) -> date | None:
    number = _MONTHS.get(month.lower())
    if number is None:
        return None
    try:
        return date(int(year), number, int(day))
    except ValueError:
        return None


def page_date(age: str | None, today: date) -> date | None:
    """The date a search result's ``page_age`` gives, or None when it cannot be read.

    ``2026-10-30T08:00:00``, ``October 30, 2026``, ``Oct 30, 2026``, ``30 October 2026``, ``3 days ago``, ``a week ago``
    (a month counts as 30 days, a year as 365).
    """
    text = (age or "").strip()
    if not text:
        return None
    if m := _ISO_PREFIX.fullmatch(text):
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    if m := _AGO.fullmatch(text):
        count = int(m.group(1)) if m.group(1) else 1
        return today - timedelta(days=count * _DAYS_PER[m.group(2).lower()])
    if m := _MONTH_DAY_YEAR.fullmatch(text):
        return _calendar(m.group(3), m.group(1), m.group(2))
    if m := _DAY_MONTH_YEAR.fullmatch(text):
        return _calendar(m.group(3), m.group(2), m.group(1))
    return None


def _in_range(when: date, today: date) -> bool:
    return today - timedelta(days=STALE_DAYS) <= when <= today + timedelta(days=1)


def check_news(
    raw: object,
    *,
    pages: Sequence[SearchedPage],
    skills: Collection[str],
    domains: Collection[str],
    limit: int,
    today: date,
) -> NewsCheck:
    """Keep the items of ``raw`` (the model's ``items`` list) that the search results and the graph back; see the module
    docstring for each check."""
    result = NewsCheck()
    by_url: dict[str, SearchedPage] = {}
    for searched in pages:
        searched_key = normalise_url(searched.url)
        if searched_key is not None:
            by_url.setdefault(searched_key, searched)
    graph = {tidy(name).casefold(): tidy(name) for name in skills if tidy(name)}
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            result.drop("shape")
            continue
        entry: Mapping[str, Any] = item
        key = normalise_url(entry.get("url"))
        page = by_url.get(key) if key is not None else None
        if key is None or page is None:
            result.drop("url_unknown")
            continue
        host = host_of(page.url)
        if not on_allow_list(host, domains):
            result.drop("domain")
            continue
        if _PAID_PATH.search(urlsplit(page.url).path):
            result.drop("press")
            continue
        skill = graph.get(tidy(entry.get("skill")).casefold())
        if skill is None:
            result.drop("skill_unknown")
            continue
        if key in seen:
            result.drop("duplicate")
            continue
        why = tidy(entry.get("why"))
        if not why:
            result.drop("why_empty")
            continue
        if _ADDRESS.search(why) or has_injection(why):
            result.drop("why_unsafe")
            continue
        publication = publication_of(host, entry.get("publication"))
        headline = _headline(entry.get("headline"), page.title, publication)
        if not headline:
            result.drop("headline_empty")
            continue
        allowed_numbers = set(_NUMBER.findall(f"{headline} {page.title}"))
        if any(number not in allowed_numbers for number in _NUMBER.findall(why)):
            result.drop("number")
            continue
        when = page_date(page.age, today) or _claimed_date(entry.get("date"))
        if when is not None and not _in_range(when, today):
            result.drop("stale")
            continue
        seen.add(key)
        result.items.append(
            WeeklyNews(
                headline=headline,
                publication=publication,
                date=when.isoformat() if when is not None else None,
                url=page.url,
                why=_cut(why, WHY_MAX_CHARS),
                skill=skill,
            )
        )
        if len(result.items) == limit:
            break
    return result


__all__ = [
    "HEADLINE_MAX_CHARS",
    "PUBLICATIONS",
    "STALE_DAYS",
    "WHY_MAX_CHARS",
    "NewsCheck",
    "SearchedPage",
    "check_news",
    "clean_title",
    "host_of",
    "normalise_url",
    "on_allow_list",
    "page_date",
    "publication_of",
]
