"""``ingest-check``: the offline sections run for real (no network), the network sections against fakes."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import pytest

from tayyari.cli import ingest_check
from tayyari.core.settings import Settings
from tayyari.ingest import SourceDoc, parse_resume

OFFLINE = "classify,terms,resume,notes"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Anything that tries to resolve a host or call the live ingesters fails the test.

    (Sockets themselves stay usable: asyncio's event loop needs a socket pair on Windows.)
    """
    attempts: list[str] = []

    def refuse_dns(host: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(f"getaddrinfo({host!r})")
        raise AssertionError(f"the network was used: getaddrinfo({host!r})")

    async def refuse_ingest(*args: Any, **kwargs: Any) -> SourceDoc:
        attempts.append("ingest")
        raise AssertionError("a live ingester was called")

    monkeypatch.setattr(socket, "getaddrinfo", refuse_dns)
    monkeypatch.setattr(ingest_check, "ingest_web", refuse_ingest)
    monkeypatch.setattr(ingest_check, "ingest_github", refuse_ingest)
    return attempts


def run(only: str | None, url: str | None = None, **settings: Any) -> int:
    return ingest_check.run(only, url, settings=Settings(_env_file=None, env="test", **settings))


# ───────────────────────── the offline sections, for real ─────────────────────────


def test_the_offline_sections_pass_without_the_network(
    no_network: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(OFFLINE) == 0
    out = capsys.readouterr().out
    assert no_network == []
    for header in ("== classify (22 samples)", "== terms (", "== ingest_resume", "== ingest_notes"):
        assert header in out
    assert "FAIL" not in out.replace("0 failed", "")
    assert ", 0 failed" in out


def test_classify_shows_one_line_per_sample(capsys: pytest.CaptureFixture[str]) -> None:
    run("classify")
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  ok")]
    assert len(lines) == len(ingest_check.CLASSIFY_SAMPLES) == 22
    assert any("github 'GitHub' / 'GitHub profile' https://github.com/gaearon" in line for line in lines)
    assert any("-> None" in line for line in lines)


def test_terms_report_names_the_tricky_cases(capsys: pytest.CaptureFixture[str]) -> None:
    run("terms")
    out = capsys.readouterr().out
    assert "tricky sentence:" in out
    assert '"Go" counts once' in out
    assert "resume sample top 8:" in out


def test_resume_section_covers_every_file_type(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("resume") == 0
    out = capsys.readouterr().out
    for title in (
        "--- TXT resume",
        "--- PDF resume",
        "--- DOCX resume",
        "--- Scanned-style PDF (no text layer)",
        "--- Corrupt PDF",
        "--- Unsupported type (PNG)",
        "--- Legacy .doc",
    ):
        assert title in out
    assert "scanned PDF: still readable" in out
    assert "no text layer: the original PDF is kept for the reader" in out


def test_notes_section(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("notes") == 0
    out = capsys.readouterr().out
    assert "notes: leading whitespace trimmed" in out
    assert "notes: runs of blank lines collapsed" in out


def test_sections_run_in_a_fixed_order_whatever_the_order_given(capsys: pytest.CaptureFixture[str]) -> None:
    run("notes,terms,classify")
    out = capsys.readouterr().out
    assert out.index("== classify") < out.index("== terms") < out.index("== ingest_notes")
    assert "== ingest_resume" not in out  # not asked for


# ───────────────────────── choosing sections ─────────────────────────


def test_parse_only_defaults_to_every_default_section() -> None:
    assert ingest_check.parse_only(None) == list(ingest_check.SECTIONS)
    assert ingest_check.parse_only("") == list(ingest_check.SECTIONS)
    assert "github-repo" not in ingest_check.parse_only(None)  # opt-in only


def test_parse_only_trims_and_orders() -> None:
    assert ingest_check.parse_only(" web , classify,,") == ["classify", "web"]
    assert ingest_check.parse_only("github-repo,github") == ["github", "github-repo"]


def test_parse_only_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="bogus"):
        ingest_check.parse_only("classify,bogus")


def test_an_unknown_section_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("nonsense") == 2
    err = capsys.readouterr().err
    assert "unknown section(s): nonsense" in err
    assert "Valid sections:" in err


# ───────────────────────── fixtures built in memory ─────────────────────────


async def test_the_generated_pdf_is_readable() -> None:
    text = await parse_resume(ingest_check.make_pdf(ingest_check.RESUME_LINES), "resume.pdf")
    assert "Aanya Verma" in text
    assert "Orbit Labs" in text
    assert "B.Tech in Computer Science" in text


async def test_the_generated_docx_is_readable() -> None:
    text = await parse_resume(ingest_check.make_docx(ingest_check.RESUME_LINES), "resume.docx")
    assert "Aanya Verma" in text
    assert "pgvector" in text


async def test_a_long_line_wraps_inside_the_page_without_losing_words() -> None:
    sentence = " ".join(f"word{i}" for i in range(80))
    text = await parse_resume(ingest_check.make_pdf(["Heading", sentence]), "long.pdf")
    assert "word0" in text and "word79" in text


def test_a_pdf_without_lines_has_no_text_layer() -> None:
    pdf = ingest_check.make_pdf([])
    assert pdf.startswith(b"%PDF-1.4")
    assert b"BT" not in pdf
    assert pdf.rstrip().endswith(b"%%EOF")


def test_pdf_text_with_parentheses_and_backslashes_is_escaped() -> None:
    pdf = ingest_check.make_pdf([r"a (b) \ c"])
    assert rb"(a \(b\) \\ c) Tj" in pdf


def test_clip_makes_one_short_line() -> None:
    assert ingest_check.clip("a\n  b\tc", 50) == "a b c"
    assert ingest_check.clip("x" * 30, 10) == "xxxxxxx..."


# ───────────────────────── the checks really check ─────────────────────────


def test_a_wrong_classification_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ingest_check, "classify_url", lambda pasted: None)
    assert run("classify") == 1
    out = capsys.readouterr().out
    assert "  FAIL 'github.com/gaearon'" in out
    assert "expected 'github', got None" in out
    assert "FAIL" in out.splitlines()[-1] or "failed" in out


def test_broken_term_matching_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ingest_check, "count_terms", lambda text: [])
    monkeypatch.setattr(ingest_check, "find_terms", lambda text, limit=8: [])
    assert run("terms") == 1
    assert "FAIL the tricky sentence yields every expected term" in capsys.readouterr().out


def test_a_resume_that_cannot_be_read_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def unreadable(data: bytes, name: str | None, log: Callable[[str], None]) -> SourceDoc:
        return SourceDoc.failure("resume", "resume", "Resume", "Resume", None, name or "", "nope")

    monkeypatch.setattr(ingest_check, "ingest_resume", unreadable)
    assert run("resume") == 1
    out = capsys.readouterr().out
    assert "FAIL TXT: read, the name is in the text" in out
    assert "FAIL PDF: read" in out
    assert "FAIL DOCX: read" in out


def test_a_failing_run_lists_its_failures_at_the_end(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ingest_check, "classify_url", lambda pasted: None)
    run("classify")
    tail = capsys.readouterr().out.split("Done in")[1]
    assert " failed" in tail.splitlines()[0]
    assert "FAIL 'github.com/gaearon'" in tail


# ───────────────────────── the network sections, against fakes ─────────────────────────


def doc(kind: str = "link", *, ok: bool = True, text: str = "x" * 400, error: str | None = None) -> SourceDoc:
    if not ok:
        return SourceDoc.failure(kind, kind, kind.title(), "Web page", None, "src", error or "could not read")
    return SourceDoc(id=kind, kind=kind, label=kind.title(), kind_label="Web page", src="src", text=text)


class Calls:
    """Records the calls of the fake ingesters and answers from a script keyed by URL."""

    def __init__(self) -> None:
        self.github: list[tuple[str, str]] = []
        self.web: list[tuple[str, str]] = []
        self.answers: dict[str, SourceDoc | BaseException] = {}

    def answer(self, url: str, default: SourceDoc) -> SourceDoc:
        result = self.answers.get(url, default)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Calls:
    calls = Calls()

    async def fake_github(url: str, log: Callable[[str], None], github_token: str | None = None) -> SourceDoc:
        calls.github.append((url, github_token or ""))
        log("fetched")
        if "tayyari-no-such-user" in url or "example.com" in url:
            return calls.answer(url, doc("github", ok=False, error="GitHub user not found"))
        return calls.answer(url, doc("github", text="## Repositories\n" + "r" * 400))

    async def fake_web(
        url: str, kind: str, label: str, kind_label: str, log: Callable[[str], None]
    ) -> SourceDoc:
        calls.web.append((url, kind))
        log("fetched")
        if "127.0.0.1" in url:
            return calls.answer(url, doc(kind, ok=False, error="That address is not allowed"))
        return calls.answer(url, doc(kind))

    monkeypatch.setattr(ingest_check, "ingest_github", fake_github)
    monkeypatch.setattr(ingest_check, "ingest_web", fake_web)
    return calls


def test_github_section_reads_the_gaearon_profile(live: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("github") == 0
    assert live.github == [("https://github.com/gaearon", "")]  # no token: unauthenticated, explicitly
    out = capsys.readouterr().out
    assert "--- GitHub profile" in out
    assert "sections: Repositories" in out


def test_github_token_from_the_settings_is_passed_explicitly(live: Calls) -> None:
    run("github", github_token="ghp_example")
    assert live.github == [("https://github.com/gaearon", "ghp_example")]


def test_github_repo_runs_only_when_asked(live: Calls) -> None:
    run("github")
    assert [url for url, _ in live.github] == ["https://github.com/gaearon"]
    live.github.clear()
    run("github-repo")
    assert [url for url, _ in live.github] == ["https://github.com/gaearon/overreacted.io"]


def test_web_section_reads_overreacted(live: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("web") == 0
    assert live.web == [("https://overreacted.io", "link")]
    assert "--- Portfolio / blog (static HTML) [link]" in capsys.readouterr().out


def test_a_failed_live_read_fails_the_run(live: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    live.answers["https://overreacted.io"] = doc(ok=False, error="Could not reach that site")
    live.answers["https://github.com/gaearon"] = doc("github", ok=False, error="rate limited")
    assert run("github,web") == 1
    out = capsys.readouterr().out
    assert "FAIL github: read, with real content  -- rate limited" in out
    assert "FAIL web: read, with real content  -- Could not reach that site" in out


def test_the_errors_section_expects_errors_not_exceptions(
    live: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("errors") == 0
    assert [url for url, _ in live.web] == [
        "https://www.linkedin.com/in/williamhgates/",
        "http://127.0.0.1:9",
    ]
    assert [url for url, _ in live.github] == [
        "https://github.com/tayyari-no-such-user-98765",
        "https://example.com/foo",
    ]
    out = capsys.readouterr().out
    assert "private address: refused with an error" in out
    assert "unknown GitHub user: an error" in out
    assert "non-GitHub link: an error" in out


def test_an_ingester_that_raises_is_a_failure_not_a_crash(
    live: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    live.answers["https://www.linkedin.com/in/williamhgates/"] = RuntimeError("boom")
    assert run("errors") == 1
    out = capsys.readouterr().out
    assert "FAIL LinkedIn" in out
    assert "raised RuntimeError: boom" in out
    assert "--- Private address" in out  # the run carried on


def test_a_private_address_that_is_fetched_is_a_failure(
    live: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    live.answers["http://127.0.0.1:9"] = doc()
    assert run("errors") == 1
    assert "FAIL private address: refused with an error" in capsys.readouterr().out


def test_url_fetches_just_that_page(live: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(None, "https://some.site/page") == 0
    assert live.web == [("https://some.site/page", "link")]
    out = capsys.readouterr().out
    assert "== custom url: https://some.site/page" in out
    assert "== classify" not in out  # --url replaces the sections


def test_url_ignores_only(live: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    run("classify", "https://some.site/page")
    assert "== classify" not in capsys.readouterr().out


def test_url_for_a_github_profile_uses_the_github_ingester(live: Calls) -> None:
    assert run(None, "github.com/gaearon") == 0
    assert live.github == [("https://github.com/gaearon", "")]
    assert live.web == []


def test_url_that_cannot_be_read_exits_1(live: Calls) -> None:
    live.answers["https://down.example.org"] = doc(ok=False, error="Could not reach that site")
    assert run(None, "https://down.example.org") == 1


def test_url_that_is_not_a_link_exits_1(live: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(None, "not a url") == 1
    assert "classified as a link" in capsys.readouterr().out
    assert live.web == [] and live.github == []


def test_logging_is_quiet_so_the_report_stays_readable(
    live: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    run("notes")
    assert "[info" not in capsys.readouterr().out


# ───────────────────────── the two failure cases that need no network, with the real ingesters ─────────────────────────


async def test_the_real_ingesters_refuse_the_private_address_and_the_non_github_link(
    monkeypatch: pytest.MonkeyPatch, no_network: list[str]
) -> None:
    """`127.0.0.1:9` is refused by the SSRF guard before any connection, and a non-GitHub URL is refused before any
    request: both are errors that come back as documents, which is what the errors section expects."""
    from tayyari.ingest import ingest_github, ingest_web

    monkeypatch.setattr(ingest_check, "ingest_web", ingest_web)
    monkeypatch.setattr(ingest_check, "ingest_github", ingest_github)
    report = ingest_check.Report()

    private = await ingest_check._ingest_url(
        ingest_check.PRIVATE_URL, report, "Private address", github_token=""
    )
    assert private is not None and not private.ok and private.error

    not_github = await report.attempt(
        "Not a GitHub link", ingest_github(ingest_check.NOT_GITHUB_URL, report.logger("github"), "")
    )
    assert not_github is not None and not not_github.ok and not_github.error
    assert no_network == []
