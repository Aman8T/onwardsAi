"""Reading an employer's board into listings. Pure functions over parsed JSON: no network, no clock of their own.

Two public APIs are read, both the employer's own applicant tracking system, both open and keyless:

    Greenhouse  ``jobs[] {id, title, updated_at, first_published?, location.name, absolute_url, content (HTML, escaped),
                offices[] {name, location}, departments[]}``
    Lever       ``[] {id, text, createdAt (ms), hostedUrl, categories {location, allLocations, team, commitment},
                workplaceType, country, salaryRange?, descriptionPlain, lists[] {text, content}, additionalPlain}``

Only listings in India are kept (country ``IN``, a location that names India or one of our cities, or a remote role that
names India). A listing is placed by its location text: the city by name or alias, the locality only when its name
appears in that text, else the one locality the registry lists for the employer in that city, else the city alone.
A locality is never guessed. A salary is shown only when Lever's ``salaryRange`` states one. The apply link is the
employer's own posting with tracking parameters taken out. A listing with no usable link, id or title is skipped.

When a board has more India listings than the cap, those in our known cities are kept first, then remote roles open to
India, then the rest, newest first within each, and the number dropped is reported.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from tayyari.storage.ports import DiscoverListing

from . import places
from .errors import BoardError
from .normalise import (
    apply_host,
    clean_apply_url,
    content_hash,
    is_entry_level_title,
    listing_id,
    normalise_title,
)
from .registry import Employer
from .textutil import MAX_TEXT_CHARS, clean_text, html_to_text, strip_control, years_asked

TITLE_MAX_CHARS: Final = 300
LOCATION_MAX_CHARS: Final = 300
EXTERNAL_ID_MAX_CHARS: Final = 128
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_REMOTE = re.compile(r"\bremote\b|work\s+from\s+home|\bwfh\b|anywhere in india", re.IGNORECASE)
_HYBRID = re.compile(r"\bhybrid\b", re.IGNORECASE)
_INDIA_WORD = re.compile(r"\bindia\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParsedBoard:
    """What one board came to: the listings to store, and how the count got there."""

    listings: list[DiscoverListing]
    seen: int
    """Jobs on the board."""
    india: int
    """Of those, listings in India that could be shown (after skipping unusable ones and removing duplicates)."""
    dropped: int
    """India listings left out because the board is over the cap."""
    skipped: int
    """Jobs without a usable id, title or link."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    listing: DiscoverListing
    tier: int
    sort_time: datetime


# ───────────────────────── small readers ─────────────────────────


def _text(value: object, limit: int) -> str:
    """A value as one line of text, at most ``limit`` characters (non-text becomes empty)."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        return ""
    return " ".join(strip_control(str(value)).split())[:limit]


def parse_time(value: object) -> datetime | None:
    """An ISO time (with or without an offset, ``Z`` allowed) or epoch milliseconds, as aware UTC; None if neither."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(float(value) / 1000.0, UTC)
        if isinstance(value, str) and value.strip():
            moment = datetime.fromisoformat(value.strip())
            return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def work_mode(workplace_type: object, *texts: str) -> str:
    """``onsite`` / ``hybrid`` / ``remote`` from Lever's ``workplaceType`` when it says, else from the words "Hybrid" and
    "Remote" in the location or title, else ``unknown``."""
    declared = _text(workplace_type, 30).lower().replace("_", "-")
    if declared in ("on-site", "onsite", "on site"):
        return "onsite"
    if declared in ("hybrid", "remote"):
        return declared
    joined = " ".join(texts)
    if _HYBRID.search(joined):
        return "hybrid"
    if _REMOTE.search(joined):
        return "remote"
    return "unknown"


# ───────────────────────── salary ─────────────────────────

_SYMBOLS: Final = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
_UNITS: Final = (
    ("year", "a year"),
    ("annual", "a year"),
    ("month", "a month"),
    ("week", "a week"),
    ("day", "a day"),
    ("hour", "an hour"),
)


def _number(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _indian_grouping(value: int) -> str:
    digits = str(abs(value))
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail])


def format_salary(salary: object) -> str | None:
    """The salary exactly as the employer states it, from Lever's ``salaryRange`` (``{currency, interval, min, max}``).

    ``₹28–36 lakh a year``, ``$40–55k a year``, ``₹80,000–1,20,000 a month``, ``GBP 40–55k a year``. None when the
    range gives no number. Greenhouse states none in v1, so a Greenhouse listing never has one.
    """
    if not isinstance(salary, Mapping):
        return None
    low, high = salary.get("min"), salary.get("max")
    values = [
        float(v) for v in (low, high) if isinstance(v, int | float) and not isinstance(v, bool) and v > 0
    ]
    if not values:
        return None
    lo = float(low) if isinstance(low, int | float) and not isinstance(low, bool) and low > 0 else None
    hi = float(high) if isinstance(high, int | float) and not isinstance(high, bool) and high > 0 else None
    currency = _text(salary.get("currency"), 8).upper()
    interval = _text(salary.get("interval"), 40).lower()
    unit = next((words for key, words in _UNITS if key in interval), "")
    yearly = unit == "a year"
    symbol = _SYMBOLS.get(currency)
    prefix = symbol if symbol is not None else (f"{currency} " if currency else "")

    def body(parts: list[float]) -> str:
        if currency == "INR" and yearly:
            shown = [_number(v / 100_000) for v in parts]
            return f"{'–'.join(dict.fromkeys(shown))} lakh"
        if currency == "INR":
            shown = [_indian_grouping(round(v)) for v in parts]
            return "–".join(dict.fromkeys(shown))
        if yearly and all(v >= 1000 for v in parts):
            shown = [_number(v / 1000) for v in parts]
            return f"{'–'.join(dict.fromkeys(shown))}k"
        shown = [f"{round(v):,}" if v >= 1000 else _number(v) for v in parts]
        return "–".join(dict.fromkeys(shown))

    if lo is not None and hi is not None:
        text = f"{prefix}{body(sorted([lo, hi]))}"
    elif lo is not None:
        text = f"From {prefix}{body([lo])}"
    else:
        text = f"Up to {prefix}{body([hi or 0.0])}"
    return f"{text} {unit}".strip()[:120]


# ───────────────────────── places ─────────────────────────


@dataclass(frozen=True, slots=True)
class _Where:
    city: str | None
    locality: str | None
    lat: float | None
    lng: float | None
    precision: str
    remote_india: bool


def place_listing(employer: Employer, texts: list[str], *, remote_india: bool) -> _Where:
    """Where a listing in India is. A remote role open to India has no city (``Remote, India``). Otherwise the first
    location text that names a city decides it; its locality is the one named in that same text, else the one locality
    the registry lists for the employer in that city, else none. A listing with no city we know is placed in the country."""
    if remote_india:
        return _Where(None, None, None, None, "country", True)
    for text in texts:
        city = places.find_city(text)
        if city is None:
            continue
        locality = places.find_locality(text, city.name)
        if locality is None:
            sole = employer.sole_locality(city.name)
            locality = places.locality_in(city.name, sole) if sole else None
        if locality is not None:
            return _Where(city.name, locality.name, locality.lat, locality.lng, "locality", False)
        return _Where(city.name, None, city.lat, city.lng, "city", False)
    return _Where(None, None, None, None, "country", False)


# ───────────────────────── building a listing ─────────────────────────


def _in_india(texts: Iterable[str], country: object, title: str) -> bool:
    if _text(country, 8).upper() == "IN":
        return True
    return any(places.names_india(text) for text in texts) or _INDIA_WORD.search(title) is not None


def _candidate(
    employer: Employer,
    *,
    source: str,
    external_id: str,
    title: str,
    texts: list[str],
    workplace_type: object,
    country: object,
    raw_url: object,
    posted_at: datetime | None,
    sort_time: datetime,
    salary_text: str | None,
    body: str,
    now: datetime,
) -> _Candidate | None:
    url = clean_apply_url(raw_url if isinstance(raw_url, str) else None)
    if not external_id or not title or url is None:
        return None
    location_text = "; ".join(dict.fromkeys(t for t in texts if t))[:LOCATION_MAX_CHARS]
    if not _in_india(texts, country, title):
        return None
    mode = work_mode(workplace_type, location_text, title)
    # In India by the test above, so a remote role is open to India: the posting names India or one of our cities.
    where = place_listing(employer, texts, remote_india=mode == "remote")
    header = f"{title}\n{employer.name}" + (f" · {location_text}" if location_text else "")
    posting_text = clean_text(f"{header}\n\n{body}", limit=MAX_TEXT_CHARS)
    asked = years_asked(body)
    entry = is_entry_level_title(title) or (
        asked is not None and (asked.minimum <= 1 or (asked.upper is not None and asked.upper <= 2))
    )
    listing = DiscoverListing(
        id=listing_id(source, employer.board, external_id),
        employer_id=employer.id,
        source=source,
        external_id=external_id,
        title=title,
        title_norm=normalise_title(title),
        work_mode=mode,
        city=where.city,
        locality=where.locality,
        lat=where.lat,
        lng=where.lng,
        precision=where.precision,
        remote_india=where.remote_india,
        location_text=location_text,
        apply_url=url,
        apply_host=apply_host(url),
        posted_at=posted_at,
        first_seen_at=now,
        last_seen_at=now,
        status="open",
        closed_at=None,
        salary_text=salary_text,
        years_min=None if asked is None else asked.minimum,
        entry_level=entry,
        content_hash=content_hash(
            title, location_text, mode, where.city, where.locality, url, salary_text, posted_at, posting_text
        ),
        posting_text=posting_text,
    )
    tier = 1 if where.remote_india else (0 if where.city else 2)
    return _Candidate(listing, tier, sort_time)


def _greenhouse(employer: Employer, jobs: list[Any], now: datetime) -> tuple[list[_Candidate], int]:
    found: list[_Candidate] = []
    skipped = 0
    for job in jobs:
        if not isinstance(job, Mapping):
            skipped += 1
            continue
        location = job.get("location")
        primary = _text(location.get("name"), LOCATION_MAX_CHARS) if isinstance(location, Mapping) else ""
        texts = [primary]
        offices = job.get("offices")
        for office in offices if isinstance(offices, list) else []:
            if isinstance(office, Mapping):
                texts.extend([_text(office.get("location"), 200), _text(office.get("name"), 200)])
        posted = parse_time(job.get("first_published"))
        updated = parse_time(job.get("updated_at"))
        built = _candidate(
            employer,
            source="greenhouse",
            external_id=_text(job.get("id"), EXTERNAL_ID_MAX_CHARS),
            title=_text(job.get("title"), TITLE_MAX_CHARS),
            texts=[t for t in texts if t],
            workplace_type=None,
            country=None,
            raw_url=job.get("absolute_url"),
            posted_at=posted,
            sort_time=posted or updated or _EPOCH,
            salary_text=None,
            body=html_to_text(job.get("content") if isinstance(job.get("content"), str) else ""),
            now=now,
        )
        if built is None:
            if _in_india(texts, None, _text(job.get("title"), TITLE_MAX_CHARS)):
                skipped += 1
            continue
        found.append(built)
    return found, skipped


def _lever_body(job: Mapping[str, Any]) -> str:
    parts: list[str] = []
    description = job.get("descriptionPlain")
    if isinstance(description, str) and description.strip():
        parts.append(clean_text(description))
    elif isinstance(job.get("description"), str):
        parts.append(html_to_text(job["description"]))
    lists = job.get("lists")
    for item in lists if isinstance(lists, list) else []:
        if not isinstance(item, Mapping):
            continue
        heading = _text(item.get("text"), 120)
        content = html_to_text(item.get("content") if isinstance(item.get("content"), str) else "")
        if heading or content:
            parts.append("\n".join(part for part in (heading, content) if part))
    additional = job.get("additionalPlain")
    if isinstance(additional, str) and additional.strip():
        parts.append(clean_text(additional))
    return "\n\n".join(parts)


def _lever(employer: Employer, jobs: list[Any], now: datetime) -> tuple[list[_Candidate], int]:
    found: list[_Candidate] = []
    skipped = 0
    for job in jobs:
        if not isinstance(job, Mapping):
            skipped += 1
            continue
        categories = job.get("categories")
        cats: Mapping[str, Any] = categories if isinstance(categories, Mapping) else {}
        texts = [_text(cats.get("location"), LOCATION_MAX_CHARS)]
        all_locations = cats.get("allLocations")
        for item in all_locations if isinstance(all_locations, list) else []:
            texts.append(_text(item, 200))
        posted = parse_time(job.get("createdAt"))
        title = _text(job.get("text"), TITLE_MAX_CHARS)
        built = _candidate(
            employer,
            source="lever",
            external_id=_text(job.get("id"), EXTERNAL_ID_MAX_CHARS),
            title=title,
            texts=[t for t in texts if t],
            workplace_type=job.get("workplaceType"),
            country=job.get("country"),
            raw_url=job.get("hostedUrl"),
            posted_at=posted,
            sort_time=posted or _EPOCH,
            salary_text=format_salary(job.get("salaryRange")),
            body=_lever_body(job),
            now=now,
        )
        if built is None:
            if _in_india(texts, job.get("country"), title):
                skipped += 1
            continue
        found.append(built)
    return found, skipped


def dedupe(candidates: list[_Candidate], employer_id: str) -> list[_Candidate]:
    """One listing per employer, normalised title and city: the most recently posted stays."""
    best: dict[tuple[str, str, str], _Candidate] = {}
    for candidate in candidates:
        listing = candidate.listing
        key = (employer_id, listing.title_norm, listing.city or "")
        kept = best.get(key)
        if kept is None or (candidate.sort_time, listing.external_id) > (
            kept.sort_time,
            kept.listing.external_id,
        ):
            best[key] = candidate
    return list(best.values())


def parse_board(employer: Employer, payload: Any, *, now: datetime, cap: int) -> ParsedBoard:
    """Every listing in India on the employer's board, deduplicated and cut to ``cap`` (see the module docstring).

    ``payload`` is the parsed JSON of the board. Raises `BoardError` when it is not the shape the API promises.
    """
    if employer.ats == "greenhouse":
        jobs = payload.get("jobs") if isinstance(payload, Mapping) else None
        if not isinstance(jobs, list):
            raise BoardError("the board did not list jobs")
        found, skipped = _greenhouse(employer, jobs, now)
    else:
        if not isinstance(payload, list):
            raise BoardError("the board did not list postings")
        jobs = payload
        found, skipped = _lever(employer, jobs, now)
    unique = dedupe(found, employer.id)
    ordered = sorted(unique, key=lambda c: (c.tier, -c.sort_time.timestamp(), c.listing.external_id))
    kept = ordered[: max(cap, 0)]
    return ParsedBoard(
        listings=[c.listing for c in kept],
        seen=len(jobs),
        india=len(unique),
        dropped=len(unique) - len(kept),
        skipped=skipped,
    )


__all__ = [
    "ParsedBoard",
    "dedupe",
    "format_salary",
    "parse_board",
    "parse_time",
    "place_listing",
    "work_mode",
]
