"""Builders for Discover unit tests: candidates with a chosen quick fit, so grouping and lenses are tested on their own."""

from __future__ import annotations

from datetime import datetime, timedelta

from tayyari.contract.discover import QuickFitWord
from tayyari.discover.models import Candidate
from tayyari.discover.normalise import normalise_title, title_tokens
from tayyari.discover.quickfit import Fit
from tayyari.storage.ports import DiscoverListing
from tests.fakes.discover import NOW

REAL_IDS = ("react", "typescript", "css", "git", "jest", "design-systems")
"""Skill ids the fixture frontend profile holds, strongest first: a candidate's matched skills are real ones."""


def fit(
    word: QuickFitWord = "looks_close",
    *,
    matched: int = 4,
    missing: tuple[str, ...] = (),
    years_asked: int | None = None,
) -> Fit:
    ids = tuple([*REAL_IDS, *(f"skill{i}" for i in range(20))][:matched])
    return Fit(
        word=word,
        named=matched + len(missing),
        matched=matched,
        matched_ids=ids,
        missing=tuple((name.lower(), name) for name in missing),
        years_asked=years_asked,
        years_you=2.4,
        reasons=(),
    )


def cand(
    listing_id: str,
    title: str,
    *,
    word: QuickFitWord = "looks_close",
    matched: int = 4,
    missing: tuple[str, ...] = (),
    entry: bool = False,
    km: float | None = None,
    age_days: int | None = None,
    city: str | None = "Bengaluru",
    remote: bool = False,
    employer: str = "acme",
    years_asked: int | None = None,
) -> Candidate:
    row = DiscoverListing(
        id=listing_id,
        employer_id=employer,
        source="greenhouse",
        external_id=listing_id,
        title=title,
        title_norm=normalise_title(title),
        work_mode="remote" if remote else "hybrid",
        city=None if remote else city,
        locality=None,
        lat=None,
        lng=None,
        precision="country" if remote else "city",
        remote_india=remote,
        location_text=city or "",
        apply_url=f"https://acme.example/{listing_id}",
        apply_host="acme.example",
        posted_at=None,
        first_seen_at=NOW,
        last_seen_at=NOW,
        status="open",
        closed_at=None,
        salary_text=None,
        years_min=years_asked,
        entry_level=entry,
        content_hash="h" + listing_id,
    )
    return Candidate(
        row=row,
        tokens=title_tokens(row.title_norm),
        fit=fit(word, matched=matched, missing=missing, years_asked=years_asked),
        km=km,
        age_from=None if age_days is None else NOW - timedelta(days=age_days),
    )


def at(days: int) -> datetime:
    return NOW - timedelta(days=days)
