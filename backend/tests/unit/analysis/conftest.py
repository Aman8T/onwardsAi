"""Fixtures for the analysis tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from tayyari.core.settings import Settings
from tayyari.ingest import SourceDoc
from tests.unit.analysis.support import Harness


@pytest.fixture
def harness(settings: Settings) -> Harness:
    """The service wired to fakes: no pacing, a frozen clock, an empty profile store."""
    return Harness(settings)


@dataclass
class FakeWeb:
    """Stands in for the network ingesters (`ingest_web`, `ingest_github`): nothing here touches the network."""

    docs: dict[str, SourceDoc] = field(default_factory=dict)
    """Documents by url; a url that is not listed gets a plain readable page."""
    web_calls: list[str] = field(default_factory=list)
    github_calls: list[tuple[str, str | None]] = field(default_factory=list)
    """``(url, token)`` per GitHub fetch."""

    def page(self, url: str, **overrides: object) -> SourceDoc:
        """Register (and return) the document served for ``url``."""
        doc = SourceDoc(id="x", kind="link", label="Web", text="PAGE TEXT " * 40, **overrides)  # type: ignore[arg-type]
        self.docs[url] = doc
        return doc


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    fake = FakeWeb()

    async def ingest_web(
        url: str, kind: str, label: str, kind_label: str, log: Callable[[str], None]
    ) -> SourceDoc:
        fake.web_calls.append(url)
        log(f"Fetched {url}")
        if url in fake.docs:
            doc = fake.docs[url]
            doc.id, doc.kind, doc.label, doc.kind_label = doc.id, kind, doc.label or label, kind_label
            doc.src = doc.src or url
            return doc
        return SourceDoc(
            id="x",
            kind=kind,
            label=label,
            kind_label=kind_label,
            url=url,
            src=url,
            text="PAGE TEXT " * 40,
            meta={"title": f"Title of {url}"},
            quick_signals=["Web chip"],
        )

    async def ingest_github(
        url: str, log: Callable[[str], None], github_token: str | None = None
    ) -> SourceDoc:
        fake.github_calls.append((url, github_token))
        log("Fetched GitHub profile")
        return SourceDoc(
            id="x",
            kind="github",
            label="GitHub",
            kind_label="GitHub profile",
            url=url,
            src=url,
            text="GITHUB TEXT " * 40,
            meta={"repos": 3, "languages": ["Python"]},
        )

    monkeypatch.setattr("tayyari.ingest.ingest_web", ingest_web)
    monkeypatch.setattr("tayyari.ingest.ingest_github", ingest_github)
    return fake
