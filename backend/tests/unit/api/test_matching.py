"""The small pieces between the match routes and the services: the words of an error event, the names of downloads, the
conversion of a service event to the line the web app reads, and where a posting text comes from."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tayyari.api.matching import (
    BOARDS_THAT_FORBID_READING,
    code_of,
    download_name,
    error_event,
    fetch_posting_text,
    posting_text_of,
    to_wire,
)
from tayyari.api.routes.match import _sentence
from tayyari.contract.match import Coverage, GateAnswer, Posting, Question, Radar, RadarAxis
from tayyari.contract.match_api import MatchRequest
from tayyari.core.errors import BadRequest, NotFound, PaymentRequired, RateLimited, Unauthorized
from tayyari.ingest import SourceDoc
from tayyari.llm.errors import ClaudeOverloaded, SpendCeilingReached
from tayyari.match.errors import (
    CoverUnavailable,
    ExportBlocked,
    NoRequirementsFound,
    PostingLinkBlocked,
    PostingLinkUnreadable,
    PostingTextError,
)
from tayyari.match.service import CoverageBatch, GatesReady, PostingReady, QuestionsReady, RadarReady
from tests.unit.match.support import js, text

# ───────────────────────── error events ─────────────────────────


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PaymentRequired(), "payment_required"),
        (NoRequirementsFound(), "no_requirements"),
        (ExportBlocked(), "export_blocked"),
        (RateLimited(), "rate_limited"),
        (Unauthorized(), "unauthorized"),
        (CoverUnavailable(), "failed"),
        (PostingTextError(), "failed"),
        (NotFound(), "failed"),
        (ClaudeOverloaded(), "failed"),
        (SpendCeilingReached(), "failed"),
        (RuntimeError("disk on fire at /srv/secret"), "failed"),
    ],
)
def test_every_failure_has_one_of_the_codes_the_web_app_knows(error: Exception, code: str) -> None:
    assert code_of(error) == code
    event = error_event(error)
    assert event.type == "error" and event.code == code


def test_an_error_event_carries_the_sentence_never_the_exception_text() -> None:
    assert error_event(NoRequirementsFound()).message == NoRequirementsFound.user_message
    assert error_event(ClaudeOverloaded()).message == ClaudeOverloaded().user_message
    crash = error_event(RuntimeError("password=hunter2 in /home/x"))
    assert crash.message == "Something went wrong on the server." and "hunter2" not in crash.model_dump_json()


# ───────────────────────── downloads and refusals ─────────────────────────


@pytest.mark.parametrize(
    ("person", "expected"),
    [
        ("Meera Nair", "meera-nair-resume.pdf"),
        ("  Zoë Müller-Åberg ", "zoe-muller-aberg-resume.pdf"),
        ("राहुल शर्मा", "leafout-resume.pdf"),  # nothing ASCII left: a plain name, never an empty one
        ('Rob "Bobby" <b>../x', "rob-bobby-b-x-resume.pdf"),
        ("A" * 200, f"{'a' * 40}-resume.pdf"),
    ],
)
def test_a_download_name_is_ascii_and_cannot_break_the_header(person: str, expected: str) -> None:
    name = download_name(person, "resume", "pdf")
    assert name == expected
    assert all(c.isalnum() or c in "-." for c in name) and name.isascii()


def test_a_refusal_is_said_as_a_sentence_without_the_line_it_was_about() -> None:
    assert _sentence("b3: that is not a figure we can use") == "That is not a figure we can use."
    assert _sentence("b-exp-1.3: no fact to fall back on.") == "No fact to fall back on."
    assert _sentence("") == "That answer does not fit this line."


# ───────────────────────── the stream's lines ─────────────────────────


def test_service_events_become_the_lines_the_web_app_reads() -> None:
    fixture = js("meera.match.json")
    posting = Posting.model_validate(fixture["posting"])
    rows = [Coverage.model_validate(c) for c in fixture["result"]["coverage"][:3]]
    gates = [GateAnswer.model_validate(g) for g in fixture["result"]["gates"]]
    questions = [Question.model_validate(q) for q in fixture["result"]["questions"]]
    radar = Radar(axes=[RadarAxis(domain="Paid", you=60, role=90)])

    posting_line = to_wire(PostingReady(posting, []))
    assert (posting_line.type, posting_line.model_dump()["facts"]) == ("posting", [])
    assert to_wire(GatesReady(gates, True)).model_dump() == {
        "type": "gates",
        "gates": [g.model_dump() for g in gates],
        "blocked": True,
    }
    assert to_wire(CoverageBatch(rows)).model_dump()["coverage"] == [
        r.model_dump() for r in rows
    ]  # `rows` is `coverage`
    assert to_wire(RadarReady(radar)).model_dump() == {"type": "radar", "radar": radar.model_dump()}
    assert to_wire(QuestionsReady(questions)).model_dump()["questions"] == [q.model_dump() for q in questions]


# ───────────────────────── where the text comes from ─────────────────────────


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every address the guarded ingest was asked for."""
    asked: list[str] = []

    async def ingest_web(url: str, kind: str, label: str, kind_label: str, log: Any) -> SourceDoc:
        asked.append(url)
        return SourceDoc(
            id=kind, kind=kind, label=label, kind_label=kind_label, url=url, text="A role. " * 30
        )

    monkeypatch.setattr("tayyari.ingest.ingest_web", ingest_web)
    return asked


@pytest.mark.parametrize(("domain", "name"), BOARDS_THAT_FORBID_READING)
async def test_each_board_that_forbids_reading_is_refused_on_its_own_domain_and_subdomains(
    pages: list[str], domain: str, name: str
) -> None:
    for url in (
        f"https://{domain}/jobs/1",
        f"http://www.{domain}/x",
        f"https://uk.{domain}/y?z=1",
        f"{domain.upper()}/a",
    ):
        with pytest.raises(PostingLinkBlocked) as refused:
            await fetch_posting_text(url)
        assert name in refused.value.user_message and "paste it here instead" in refused.value.user_message
    assert pages == []  # nothing was fetched


async def test_other_hosts_are_not_mistaken_for_a_board(pages: list[str]) -> None:
    for url in (
        "https://notlinkedin.com/job",
        "https://linkedin.com.example.org/job",
        "https://example.com/naukri.com/job",
        "https://careers.lumenretail.example/jobs/1",
    ):
        assert (await fetch_posting_text(url)).startswith("A role.")
    assert len(pages) == 4


async def test_a_link_that_is_not_a_web_address_is_a_400_and_nothing_is_fetched(pages: list[str]) -> None:
    for junk in (
        "not a link",
        "ftp://example.com/x",
        "mailto:a@b.co",
        "https://user:pw@example.com/x",
        "javascript:alert(1)",
    ):
        with pytest.raises(BadRequest):
            await fetch_posting_text(junk)
    assert pages == []


async def test_a_page_that_cannot_be_read_says_why_and_offers_the_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(
        [
            SourceDoc.failure(
                "link",
                "link",
                "Job posting",
                "Job posting",
                "https://x.co/a",
                "x.co/a",
                "That site shows a bot check, so it could not be read.",
            ),
            SourceDoc(id="link", kind="link", label="L", kind_label="L", url="https://x.co/a", text="   "),
            SourceDoc.failure("link", "link", "Job posting", "Job posting", "https://x.co/a", "x.co/a", ""),
        ]
    )

    async def ingest_web(*_args: Any) -> SourceDoc:
        return next(answers)

    monkeypatch.setattr("tayyari.ingest.ingest_web", ingest_web)
    with pytest.raises(PostingLinkUnreadable) as bot:
        await fetch_posting_text("https://x.co/a")
    assert (
        bot.value.user_message
        == "That site shows a bot check, so it could not be read. You can paste the text of the posting instead."
    )
    for _ in range(2):  # an empty page, and a failure with no reason of its own
        with pytest.raises(PostingLinkUnreadable) as plain:
            await fetch_posting_text("https://x.co/a")
        assert plain.value.user_message == PostingLinkUnreadable.user_message


async def test_pasted_text_wins_over_a_link_and_is_returned_cleaned(pages: list[str], settings: Any) -> None:
    container = SimpleNamespace(settings=settings)
    posting = text("meera.posting.txt")
    both = MatchRequest(profileId="p", text="\r\n" + posting + "\x00\r\n", url="https://example.com/job")
    cleaned = await posting_text_of(container, both)  # type: ignore[arg-type]
    assert cleaned == posting.replace("\r\n", "\n").strip() and "\x00" not in cleaned
    assert pages == []  # the link was never opened

    via_link = await posting_text_of(container, MatchRequest(profileId="p", url="https://example.com/job"))  # type: ignore[arg-type]
    assert via_link.startswith("A role.") and pages == ["https://example.com/job"]


@pytest.mark.parametrize(
    ("body", "words"),
    [
        (MatchRequest(profileId="p"), "Paste the job posting"),
        (MatchRequest(profileId="p", text="  ", url=" "), "Paste the job posting"),
        (MatchRequest(profileId="p", text="too short"), "too short"),
    ],
)
async def test_a_request_without_a_usable_text_is_a_400(
    settings: Any, body: MatchRequest, words: str
) -> None:
    with pytest.raises(BadRequest) as refused:
        await posting_text_of(SimpleNamespace(settings=settings), body)  # type: ignore[arg-type]
    assert words in refused.value.user_message


async def test_a_text_over_the_configured_limit_is_refused_with_that_number(make_settings: Any) -> None:
    small = make_settings(match={"max_posting_chars": 300})
    with pytest.raises(PostingTextError) as refused:
        await posting_text_of(SimpleNamespace(settings=small), MatchRequest(profileId="p", text="x " * 200))  # type: ignore[arg-type]
    assert "longer than 300 characters" in refused.value.user_message


# ───────────────────────── facts from apply answers ─────────────────────────


async def test_an_apply_answer_keeps_one_fact_per_question_and_a_withdrawn_one_leaves() -> None:
    from tayyari.api.matching import ApplyFactSink
    from tests.fakes.repos import FakeRepositories

    repos = FakeRepositories()
    sink = ApplyFactSink(SimpleNamespace(repos=repos))  # type: ignore[arg-type]
    await sink("u1", "p1", "workAuthorization:IN", "I am authorised to work in India.")
    await sink("u1", "p1", "workAuthorization:IN", "I am not authorised to work in India.")
    await sink("u1", "p1", "noticePeriod", "My notice period is 30 days.")
    texts = sorted(f.text for f in await repos.user_facts.list_for_profile("p1"))
    assert texts == ["I am not authorised to work in India.", "My notice period is 30 days."]
    await sink.retract("u1", "p1", "workAuthorization:IN")
    assert [f.text for f in await repos.user_facts.list_for_profile("p1")] == ["My notice period is 30 days."]
    await sink("u1", "p1", "ctc", "x")  # off the allowlist: never stored
    assert len(await repos.user_facts.list_for_profile("p1")) == 1
