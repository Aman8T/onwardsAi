"""Job boards whose terms forbid automated reading: a link on one of them is never fetched, by any path.

Non-negotiable 4 (`CLAUDE.md`): no scraping LinkedIn, Naukri or any board that forbids automation. The posting match
refused these links before anything was fetched, but the analysis read them like any other web page (the launch audit
of 2026-11-03 saw it open a Naukri link a person supplied). `ingest_web` now asks `board_that_forbids_reading` first,
so a link pasted for an analysis, a posting link and the CLI all stop at the same list.

The list follows `docs/research/job-discovery.md` §2.1: the boards whose terms ban scraping or most automation, the ones
that forbid linking past their home page, and Unstop, whose terms are unread (refused until a person reads them).
LinkedIn is not here: the analysis still reads the public, signed-out view of a profile a person gives it (a decision
for the owner, see `docs/roadmap.md`), and the posting match refuses LinkedIn links on its own
(`api.matching.BOARDS_THAT_FORBID_READING`).
"""

from __future__ import annotations

from typing import Final

from .classify import host_is, strip_www

JOB_BOARDS_THAT_FORBID_READING: Final[tuple[tuple[str, str], ...]] = (
    ("naukri.com", "Naukri"),
    ("indeed.com", "Indeed"),
    ("glassdoor.com", "Glassdoor"),
    ("glassdoor.co.in", "Glassdoor"),
    ("foundit.in", "foundit"),
    ("monsterindia.com", "foundit"),
    ("shine.com", "Shine"),
    ("timesjobs.com", "TimesJobs"),
    ("iimjobs.com", "iimjobs"),
    ("hirist.tech", "hirist"),
    ("hirist.com", "hirist"),
    ("instahyre.com", "Instahyre"),
    ("internshala.com", "Internshala"),
    ("unstop.com", "Unstop"),
)
"""``(domain, name)``: the domain and every subdomain of it (``in.indeed.com``, ``www.naukri.com``)."""


def board_that_forbids_reading(hostname: str) -> str | None:
    """The board's name when ``hostname`` belongs to a job board that forbids automated reading, else None."""
    host = strip_www(hostname.lower().rstrip("."))
    for domain, name in JOB_BOARDS_THAT_FORBID_READING:
        if host_is(host, domain):
            return name
    return None


def board_refusal(name: str) -> str:
    """What a person is told instead of the page: why it was not opened, and what to do."""
    return (
        f"{name} does not allow automated reading of its pages, so Leafout does not open that link. "
        "Copy the text you want read and paste it under Extra context instead."
    )


__all__ = ["JOB_BOARDS_THAT_FORBID_READING", "board_refusal", "board_that_forbids_reading"]
