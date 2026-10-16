"""Board links taken from what the web search tool returned: which employers' boards the scouts' searches led to.

Only the tool's result links are read, never a model's words, so a board here is one a search engine really returned.
A link counts when it is an employer's Greenhouse or Lever board (US-hosted, the API Discover reads), with a board
token Discover accepts (``[A-Za-z0-9._-]``, which also keeps the token safe to put in a URL):

    job-boards.greenhouse.io/{token}[/jobs/{id}]      boards.greenhouse.io/{token}[/jobs/{id}]
    boards.greenhouse.io/embed/job_app?for={token}    jobs.lever.co/{site}[/{posting id}[/apply]]

Boards that list roles for other companies (recruiters seen in the baseline runs) are skipped: their apply link is not
the employer's. Boards are ranked for reading by how well the best result title from them reads as the person's kind of
role (a job page titled "Frontend Engineer" before a board's front page), then by how many results came from them; the
first search that found a board is the one shown.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final, Literal
from urllib.parse import parse_qs, urlsplit

from tayyari.llm.types import SearchCall

Ats = Literal["greenhouse", "lever"]

GREENHOUSE_HOSTS: Final = frozenset({"job-boards.greenhouse.io", "boards.greenhouse.io"})
LEVER_HOSTS: Final = frozenset({"jobs.lever.co"})
ALLOWED_DOMAINS: Final = ("job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co")
"""What a scout's searches may reach (the search tool's ``allowed_domains``)."""
RECRUITER_BOARDS: Final = frozenset({"weekdayworks", "jobgether", "smart-working-solutions"})
"""Boards of recruiters and staffing firms that post other companies' roles (the baseline runs dropped them too)."""
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_NOT_TOKENS: Final = frozenset({"embed", "jobs", "job_app", "v1", "boards", "api"})
_GREENHOUSE_TITLE = re.compile(r"\bat\s+(.{2,80}?)\s*$", re.IGNORECASE)
_LEVER_TITLE = re.compile(r"^(.{2,80}?)\s+[-–—|]\s+")


@dataclass(frozen=True, slots=True)
class BoardRef:
    ats: Ats
    token: str

    @property
    def key(self) -> tuple[str, str]:
        """Board tokens are matched without regard to case."""
        return (self.ats, self.token.lower())


@dataclass(slots=True)
class BoardLead:
    """One board the searches led to."""

    ref: BoardRef
    first_query: str
    name_hint: str | None = None
    hits: int = 0
    relevance: int = 0
    """The best `relevance` of a result title from this board."""
    queries: list[str] = field(default_factory=list)


def board_of(url: str) -> BoardRef | None:
    """The board a result link belongs to, or None when it is not an employer's Greenhouse or Lever board."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("https", "http"):
        return None
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    token: str | None = None
    ats: Ats
    if host in GREENHOUSE_HOSTS:
        ats = "greenhouse"
        if segments[:2] == ["embed", "job_app"] or segments[:1] == ["embed"]:
            token = next(iter(parse_qs(parts.query).get("for", [])), None)
        elif segments:
            token = segments[0]
    elif host in LEVER_HOSTS:
        ats = "lever"
        token = segments[0] if segments else None
    else:
        return None
    if not token or token.lower() in _NOT_TOKENS or not _TOKEN.fullmatch(token):
        return None
    if token.lower() in RECRUITER_BOARDS:
        return None
    return BoardRef(ats, token)


def name_hint(ats: Ats, title: str) -> str | None:
    """The employer's name as a result title gives it: Greenhouse "Job Application for X at Acme", Lever "Acme - X"."""
    text = title.strip()
    if not text:
        return None
    found = (_GREENHOUSE_TITLE if ats == "greenhouse" else _LEVER_TITLE).search(text)
    return found.group(1).strip() if found else None


def harvest(searches: Iterable[SearchCall], relevance: Callable[[str], int] | None = None) -> list[BoardLead]:
    """Every board the searches led to, the most relevant first, then the most often found, then in the order found."""
    leads: dict[tuple[str, str], BoardLead] = {}
    for call in searches:
        for hit in call.hits:
            ref = board_of(hit.url)
            if ref is None:
                continue
            lead = leads.get(ref.key)
            if lead is None:
                lead = leads[ref.key] = BoardLead(ref=ref, first_query=call.query)
            lead.hits += 1
            if relevance is not None:
                lead.relevance = max(lead.relevance, relevance(hit.title))
            if call.query and call.query not in lead.queries:
                lead.queries.append(call.query)
            if lead.name_hint is None:
                lead.name_hint = name_hint(ref.ats, hit.title)
    order = {key: index for index, key in enumerate(leads)}
    return sorted(leads.values(), key=lambda lead: (-lead.relevance, -lead.hits, order[lead.ref.key]))


__all__ = [
    "ALLOWED_DOMAINS",
    "RECRUITER_BOARDS",
    "Ats",
    "BoardLead",
    "BoardRef",
    "board_of",
    "harvest",
    "name_hint",
]
