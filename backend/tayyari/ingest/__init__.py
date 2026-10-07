"""Deterministic source ingestion (no AI): resume, GitHub, web pages and pasted notes become `SourceDoc`s.

The pipeline either calls `ingest_sources` (everything at once) or, to stream per-source progress,
`plan_sources` followed by `ingest_planned` for each planned source.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from .classify import (
    MAX_LINKS,
    ClassifiedUrl,
    IngestLog,
    SourceDoc,
    classify_link,
    classify_url,
    is_known_platform,
    normalize_url,
    usable_links,
)
from .github import ingest_github
from .notes import ingest_notes
from .resume import ResumeError, ingest_resume, parse_resume
from .terms import TERM_COUNT, TermHit, count_terms, find_terms
from .web import ingest_web

if TYPE_CHECKING:
    from ..legacy.types import AnalyzeInput

__all__ = [
    "MAX_LINKS",
    "TERM_COUNT",
    "ClassifiedUrl",
    "IngestLog",
    "PlannedSource",
    "ResumeError",
    "SourceDoc",
    "TermHit",
    "classify_link",
    "classify_url",
    "count_terms",
    "find_terms",
    "ingest_github",
    "ingest_notes",
    "ingest_planned",
    "ingest_resume",
    "ingest_sources",
    "ingest_web",
    "is_known_platform",
    "normalize_url",
    "parse_resume",
    "plan_sources",
    "usable_links",
]

_RESUME_FORMAT = {".pdf": "PDF", ".docx": "DOCX", ".txt": "TXT", ".md": "Markdown"}


@dataclass
class PlannedSource:
    """A source the pipeline is about to read: enough to announce its reader before anything is fetched."""

    id: str
    """"resume", "github", "lovable-1", "portfolio-1", "link-2", "notes"."""
    kind: str
    label: str
    kind_label: str
    src: str
    url: str | None
    ingest: Callable[[IngestLog], Awaitable[SourceDoc]]
    """Deterministic fetch/extract step; reports progress through the callback and never raises for expected failures."""


def _id_for(kind: str, n: int) -> str:
    if kind in ("github", "linkedin"):
        return kind if n == 1 else f"{kind}-{n}"
    return f"{kind}-{n}"


def _url_to_src(url: str) -> str:
    return url.removeprefix("https://").removeprefix("http://").removeprefix("www.").removesuffix("/")


def plan_sources(inp: AnalyzeInput) -> list[PlannedSource]:
    """The sources of an analysis in reading order: the resume, each usable link, then the notes."""
    if inp.sample:
        # lazy: the sample package imports this one
        from ..sample.sample import load_sample_sources

        def replay(doc: SourceDoc) -> Callable[[IngestLog], Awaitable[SourceDoc]]:
            async def ingest(_log: IngestLog) -> SourceDoc:
                return doc

            return ingest

        return [PlannedSource(d.id, d.kind, d.label, d.kind_label, d.src, d.url, replay(d)) for d in load_sample_sources()]

    planned: list[PlannedSource] = []
    if inp.resume_bytes is not None:
        data, name = inp.resume_bytes, inp.resume_name
        fmt = _RESUME_FORMAT.get(os.path.splitext(name or "")[1].lower())
        planned.append(
            PlannedSource(
                "resume", "resume", "Resume", f"Resume · {fmt}" if fmt else "Resume", name or "resume", None,
                lambda log: ingest_resume(data, name, log),
            )
        )  # fmt: skip

    counters: dict[str, int] = {}
    for link in usable_links(inp.links):
        n = counters[link.kind] = counters.get(link.kind, 0) + 1

        def read(log: IngestLog, link: ClassifiedUrl = link) -> Awaitable[SourceDoc]:
            if link.kind == "github":
                return ingest_github(link.url, log)
            return ingest_web(link.url, link.kind, link.label, link.kind_label, log)

        planned.append(PlannedSource(_id_for(link.kind, n), link.kind, link.label, link.kind_label, _url_to_src(link.url), link.url, read))

    notes = (inp.notes or "").strip()
    if notes:

        async def read_notes(_log: IngestLog) -> SourceDoc:
            return await asyncio.to_thread(ingest_notes, notes)

        planned.append(PlannedSource("notes", "notes", "Notes", "Extra context", "Pasted context", None, read_notes))
    return planned


async def ingest_planned(p: PlannedSource, log: IngestLog) -> SourceDoc:
    """Run one planned source. Failures (including unexpected ones) come back as a `SourceDoc` with `ok=False`."""
    try:
        doc = await p.ingest(log)
    except Exception as exc:  # noqa: BLE001 - the pipeline still shows the source, marked as failed
        print(f"[ingest {p.id}] {exc!r}", flush=True)
        doc = SourceDoc.failure(p.id, p.kind, p.label, p.kind_label, p.url, p.src, "Could not read this source.")
    doc.id = p.id
    doc.url = doc.url or p.url
    doc.src = doc.src or p.src
    doc.kind_label = doc.kind_label or p.kind_label
    return doc


async def ingest_sources(inp: AnalyzeInput, log: Callable[[str, str], None]) -> list[SourceDoc]:
    """Read every source of an analysis concurrently, in reading order; never raises for a bad source.

    `log(source_id, text)` receives one short, human progress line per step.
    """
    planned = plan_sources(inp)
    return list(await asyncio.gather(*(ingest_planned(p, partial(log, p.id)) for p in planned)))
