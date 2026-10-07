"""Planning and running sources: ids and order, failure containment, and the GitHub token hand-off.

The pipeline's input object is duck-typed here (`Inp`), so these tests do not depend on the legacy package.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

import tayyari.ingest as ingest
from tayyari.ingest import (
    PlannedSource,
    SourceDoc,
    ingest_planned,
    ingest_sources,
    plan_sources,
)
from tests.unit.ingest.helpers import PUBLIC_IP, FakeDns, FakeWeb, make_docx, make_pdf


@dataclass
class Inp:
    resume_bytes: bytes | None = None
    resume_name: str | None = None
    links: list[str] = field(default_factory=list)
    goal: str | None = None
    notes: str | None = None
    sample: bool = False


# ----------------------------------------------------------------------------- plan_sources


def test_the_resume_comes_first_then_each_usable_link_then_the_notes() -> None:
    planned = plan_sources(
        Inp(
            resume_bytes=b"%PDF-",
            resume_name="Riya_CV.pdf",
            links=[
                "https://github.com/octocat",
                "https://recipe-genie.lovable.app/",
                "https://www.riya.vercel.app/work",
                "https://medium.com/@riya",
                "https://www.linkedin.com/in/riya",
            ],
            notes="  Led two interns.  ",
        )
    )
    assert [(p.id, p.kind) for p in planned] == [
        ("resume", "resume"),
        ("github", "github"),
        ("lovable-1", "lovable"),
        ("portfolio-1", "portfolio"),
        ("link-1", "link"),
        ("linkedin", "linkedin"),
        ("notes", "notes"),
    ]
    by_id = {p.id: p for p in planned}
    assert (by_id["resume"].label, by_id["resume"].kind_label, by_id["resume"].src, by_id["resume"].url) == (
        "Resume",
        "Resume · PDF",
        "Riya_CV.pdf",
        None,
    )
    assert by_id["github"].url == "https://github.com/octocat"
    assert by_id["github"].src == "github.com/octocat"
    assert by_id["lovable-1"].src == "recipe-genie.lovable.app"
    assert by_id["portfolio-1"].src == "riya.vercel.app/work"  # protocol and www are dropped
    assert by_id["link-1"].label == "Blog"
    assert by_id["link-1"].kind_label == "Web page"
    assert by_id["notes"].kind_label == "Extra context"
    assert by_id["notes"].src == "Pasted context"


def test_several_links_of_one_kind_are_numbered_and_only_github_and_linkedin_get_bare_first_ids() -> None:
    planned = plan_sources(
        Inp(
            links=[
                "https://github.com/a",
                "https://github.com/b",
                "https://www.linkedin.com/in/a",
                "https://www.linkedin.com/in/b",
                "https://one.example.com",
                "https://two.example.com",
                "https://x.vercel.app",
                "https://y.netlify.app",
            ]
        )
    )
    assert [p.id for p in planned] == [
        "github",
        "github-2",
        "linkedin",
        "linkedin-2",
        "link-1",
        "link-2",
        "portfolio-1",
        "portfolio-2",
    ]


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("cv.pdf", "Resume · PDF"),
        ("cv.PDF", "Resume · PDF"),
        ("cv.docx", "Resume · DOCX"),
        ("cv.txt", "Resume · TXT"),
        ("cv.md", "Resume · Markdown"),
        ("cv.rtf", "Resume"),
        ("cv", "Resume"),
        (None, "Resume"),
    ],
)
def test_the_resume_kind_label_follows_the_file_extension(name: str | None, label: str) -> None:
    [planned] = plan_sources(Inp(resume_bytes=b"x", resume_name=name))
    assert planned.kind_label == label
    assert planned.src == (name or "resume")


def test_nothing_to_read_means_nothing_planned() -> None:
    assert plan_sources(Inp()) == []
    assert (
        plan_sources(Inp(links=["", "not a url", "mailto:x@y.com", "file:///etc/passwd"], notes="  \n "))
        == []
    )


def test_an_empty_resume_upload_is_still_planned_so_the_user_hears_about_it() -> None:
    [planned] = plan_sources(Inp(resume_bytes=b"", resume_name="cv.pdf"))
    assert planned.id == "resume"


def test_at_most_max_links_links_are_planned() -> None:
    planned = plan_sources(Inp(links=[f"https://site{i}.example.com" for i in range(30)]))
    assert len(planned) == ingest.MAX_LINKS == 8


async def test_sample_mode_replays_the_bundled_sources() -> None:
    pytest.importorskip("tayyari.sample.sample")
    planned = plan_sources(Inp(sample=True))
    assert planned[0].id == "resume"
    assert len(planned) >= 3
    doc = await planned[0].ingest(lambda _line: None)
    assert doc.id == "resume"
    assert doc.text


# ----------------------------------------------------------------------------- ingest_planned


def planned_source(ingest_fn: Any, id: str = "link-1") -> PlannedSource:
    return PlannedSource(id, "link", "Example", "Web page", "example.com", "https://example.com", ingest_fn)


async def test_a_planned_source_keeps_its_planned_identity_and_fills_gaps() -> None:
    async def ingest_fn(log: Any) -> SourceDoc:
        log("step one")
        return SourceDoc(id="wrong", kind="link", label="Example", text="hello")

    lines: list[str] = []
    doc = await ingest_planned(planned_source(ingest_fn, "link-3"), lines.append)
    assert doc.id == "link-3"
    assert doc.url == "https://example.com"
    assert doc.src == "example.com"
    assert doc.kind_label == "Web page"
    assert doc.ok is True
    assert lines == ["step one"]


async def test_an_unexpected_exception_becomes_a_failed_source_and_is_logged_without_its_message(
    record_logs: Any,
) -> None:
    async def ingest_fn(log: Any) -> SourceDoc:
        raise ValueError("SECRET-DOCUMENT-TEXT-IN-AN-ERROR-MESSAGE")

    doc = await ingest_planned(planned_source(ingest_fn), lambda _line: None)
    assert doc.ok is False
    assert doc.error == "Could not read this source."
    assert (doc.id, doc.kind, doc.label, doc.kind_label, doc.url, doc.src) == (
        "link-1",
        "link",
        "Example",
        "Web page",
        "https://example.com",
        "example.com",
    )
    [event] = record_logs.named("ingest_source_crashed")
    assert event["level"] == "warning"
    assert event["source_id"] == "link-1"
    assert event["kind"] == "link"
    assert event["error_type"] == "ValueError"
    assert event["crash_site"].startswith("test_planning.py:")
    assert "in ingest_fn" in event["crash_site"]
    assert "SECRET-DOCUMENT-TEXT" not in " ".join(str(v) for v in event.values())


async def test_cancellation_is_not_swallowed() -> None:
    async def ingest_fn(log: Any) -> SourceDoc:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ingest_planned(planned_source(ingest_fn), lambda _line: None)


async def test_a_resume_is_ingested_through_its_planned_source() -> None:
    [planned] = plan_sources(
        Inp(resume_bytes=make_docx(["Experience", "Built React apps. " * 20]), resume_name="cv.docx")
    )
    lines: list[str] = []
    doc = await ingest_planned(planned, lines.append)
    assert doc.ok is True
    assert doc.id == "resume"
    assert doc.kind_label == "Resume · DOCX"
    assert lines[0].endswith("words from the document")


# ----------------------------------------------------------------------------- ingest_sources


async def test_sources_are_read_concurrently_and_returned_in_reading_order(
    dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><head><title>Portfolio</title></head><body><p>I build things with Python.</p></body></html>",
        ),
    )
    lines: list[tuple[str, str]] = []
    docs = await ingest_sources(
        Inp(
            resume_bytes=make_pdf([["Riya " + "x" * 300]]),
            resume_name="cv.pdf",
            links=["https://example.test/me"],
            notes="Some notes about React.",
        ),
        lambda source_id, text: lines.append((source_id, text)),
    )
    assert [d.id for d in docs] == ["resume", "link-1", "notes"]
    assert all(d.ok for d in docs)
    assert docs[1].meta["title"] == "Portfolio"
    assert ("link-1", "Opened example.test/me") in lines
    assert {source_id for source_id, _ in lines} >= {"resume", "link-1"}


async def test_one_failing_source_does_not_stop_the_others(dns: FakeDns, web: FakeWeb) -> None:
    dns.missing("gone.test")
    docs = await ingest_sources(
        Inp(resume_bytes=b"garbage", resume_name="cv.pdf", links=["https://gone.test/"], notes="Still here."),
        lambda source_id, text: None,
    )
    assert [(d.id, d.ok) for d in docs] == [("resume", False), ("link-1", False), ("notes", True)]
    assert docs[0].error.startswith("Could not read that PDF")
    assert docs[1].error == "Could not find that site. Check the link for typos."


# ----------------------------------------------------------------------------- the GitHub token hand-off


@pytest.fixture
def github_calls(net: Any) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404, json={"message": "Not Found"})

    net.route(host="api.github.com").mock(side_effect=handler)
    return calls


async def test_plan_sources_hands_the_explicit_token_to_the_github_ingester(
    github_calls: list[httpx.Request],
) -> None:
    [planned] = plan_sources(Inp(links=["https://github.com/octocat"]), github_token="ghp_from_settings")
    doc = await ingest_planned(planned, lambda _line: None)
    assert doc.error == 'GitHub user "octocat" was not found. Check the link.'
    assert [r.headers.get("authorization") for r in github_calls] == ["Bearer ghp_from_settings"]


async def test_ingest_sources_hands_the_token_on_too(github_calls: list[httpx.Request]) -> None:
    await ingest_sources(Inp(links=["https://github.com/octocat"]), lambda *_: None, github_token="ghp_other")
    assert [r.headers.get("authorization") for r in github_calls] == ["Bearer ghp_other"]


async def test_without_a_token_argument_the_environment_variable_is_still_used(
    github_calls: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
    [planned] = plan_sources(Inp(links=["https://github.com/octocat"]))
    await ingest_planned(planned, lambda _line: None)
    assert [r.headers.get("authorization") for r in github_calls] == ["Bearer ghp_from_env"]


async def test_the_token_is_keyword_only_so_old_call_sites_cannot_pass_it_by_accident() -> None:
    with pytest.raises(TypeError):
        plan_sources(Inp(), "ghp_x")  # type: ignore[misc]
    with pytest.raises(TypeError):
        await ingest_sources(Inp(), lambda *_: None, "ghp_x")  # type: ignore[misc]


async def test_a_token_is_only_ever_sent_for_github_links(
    github_calls: list[httpx.Request], dns: FakeDns, web: FakeWeb
) -> None:
    dns.answer("example.test", [PUBLIC_IP])
    web.serve(
        PUBLIC_IP,
        lambda request: httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"I know Python."
        ),
    )
    await ingest_sources(
        Inp(links=["https://example.test/about.txt", "https://github.com/octocat"]),
        lambda *_: None,
        github_token="ghp_secret_token",
    )
    assert all("authorization" not in r.headers for r in web.requests)
    assert [r.headers.get("authorization") for r in github_calls] == ["Bearer ghp_secret_token"]
