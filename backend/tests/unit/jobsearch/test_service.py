"""A search with AI end to end over the fakes: scripted Haiku scouts and reads, hand-written boards, Discover's store.

What it proves: the stream's order; that boards come only from the search tool's links (never a recruiter's, never
fetched twice in a while, Tayyari's own employers from the store); that the read never sees a role of another field, a
leader's role or one elsewhere; that nothing a model invented reaches a job; that a search carries no name; and how a
failing scout, a failing read and an injected posting are handled."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tayyari.contract.jobsearch import (
    JobSearchDoneEvent,
    JobSearchJobEvent,
    JobSearchStepEvent,
)
from tayyari.contract.models import Profile
from tayyari.core.settings import DiscoverSettings, JobSearchSettings
from tayyari.discover import places
from tayyari.discover.service import DiscoverService
from tayyari.jobsearch.boards import BoardReader
from tayyari.jobsearch.errors import JobSearchDisabled
from tayyari.jobsearch.harvest import ALLOWED_DOMAINS
from tayyari.jobsearch.service import JobSearchService
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.llm.types import CallContext
from tests.fakes.claude import FakeClaudeClient, search_run
from tests.fakes.clock import FakeClock
from tests.fakes.discover import NOW, FakeBoards, FakeDiscoverRepo, fixture_registry, profile_of, read_json
from tests.unit.discover.conftest import BOARD_FILES
from tests.unit.jobsearch.support import (
    INJECTED_HTML,
    WEB_COMPANY,
    WEB_TOKEN,
    frontend_answer,
    gh_job,
    item,
    reader,
    web_board,
)

BENGALURU = places.city_by_name("Bengaluru")
CTX = CallContext(purpose="jobsearch.scout", request_id="req-1", profile_id="p1")
HARBOUR_URL = f"https://job-boards.greenhouse.io/{WEB_TOKEN}/jobs/101"
QUERY = "Frontend Engineer Bengaluru"
RESULTS = {
    QUERY: [
        (HARBOUR_URL, f"Job Application for Frontend Engineer at {WEB_COMPANY}"),
        ("https://jobs.lever.co/jobgether/abc", "Jobgether - Frontend Engineer"),
        (
            "https://job-boards.greenhouse.io/kestrelpay/jobs/1",
            "Job Application for Senior Frontend Engineer at Kestrel Pay",
        ),
        ("https://www.linkedin.com/jobs/view/1", "Frontend Engineer"),
    ]
}


class Rig:
    def __init__(self, service: JobSearchService, claude: FakeClaudeClient, boards: FakeBoards) -> None:
        self.service, self.claude, self.boards = service, claude, boards

    async def run(self, profile: Profile | None = None) -> list[Any]:
        return [event async for event in self.service.run(profile or profile_of("frontend"), BENGALURU, CTX)]

    def fetched(self, token: str) -> int:
        return sum(token in url for url in self.boards.urls)


@pytest.fixture
async def make_rig() -> Callable[..., Any]:
    async def build(*, settings: JobSearchSettings | None = None, board: dict[str, Any] | None = None) -> Rig:
        clock = FakeClock(NOW)
        repo = FakeDiscoverRepo()
        boards = FakeBoards()
        for token, name in BOARD_FILES.items():
            boards.serve(token, read_json(name))
        boards.serve(WEB_TOKEN, board or web_board())
        discover = DiscoverService(
            settings=DiscoverSettings(),
            repo=repo,
            clock=clock,
            registry=fixture_registry(),
            transport=boards.transport,
        )
        report = await discover.refresh()
        assert report is not None and report.status == "ok"
        claude = FakeClaudeClient()
        reader_ = BoardReader(
            clock=clock,
            timeout_s=5,
            concurrency=4,
            cache_minutes=60,
            listing_cap=300,
            transport=boards.transport,
        )
        service = JobSearchService(
            claude=claude,
            settings=settings or JobSearchSettings(),
            repo=repo,
            registry=fixture_registry,
            reader=reader_,
            clock=clock,
        )
        return Rig(service, claude, boards)

    return build


def steps(events: list[Any]) -> list[str]:
    return [e.step.stage for e in events if isinstance(e, JobSearchStepEvent)]


def jobs(events: list[Any]) -> list[Any]:
    return [e.job for e in events if isinstance(e, JobSearchJobEvent)]


def done(events: list[Any]) -> JobSearchDoneEvent:
    last = events[-1]
    assert isinstance(last, JobSearchDoneEvent)
    return last


async def test_a_search_streams_its_trail_then_the_roles_that_survive_the_checks(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    events = await rig.run()

    assert steps(events) == ["plan", "search", "search", "search", "boards", "narrow", "read", "result"]
    assert isinstance(events[-1], JobSearchDoneEvent) and isinstance(events[-2], JobSearchStepEvent)
    (job,) = jobs(events)
    assert (job.title, job.employer, job.place.label) == ("Frontend Engineer", WEB_COMPANY, "Bengaluru")
    assert (job.foundVia, job.foundBy, job.applyUrl) == ("web", QUERY, HARBOUR_URL)
    assert job.applyHost == "job-boards.greenhouse.io" and job.checkedAt == NOW.isoformat()
    assert job.fit == "good" and job.level == "at" and job.yearsAsked == 1
    assert job.levelQuote == "1-3 years of experience building web applications"
    assert [m.ask for m in job.met] == [
        "Strong experience with React and TypeScript in production",
        "Hands-on accessibility work against WCAG 2.1 AA",
    ]
    assert all(m.cite.source == "Resume" and m.cite.text for m in job.met)
    assert job.gaps == ["Experience with GraphQL APIs and Apollo Client"]
    assert job.quickFit.named >= 2
    finished = done(events)
    counts = [step.count for step in finished.funnel]
    assert counts == sorted(counts, reverse=True) and counts[-1] == finished.jobs == 1


async def test_the_scouts_search_only_employers_sites_and_never_carry_the_persons_name(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured("jobsearch.judge", reader({}), repeat=True)
    await rig.run()
    name = profile_of("frontend").person.name
    calls = rig.claude.search_calls
    assert len(calls) == 3
    assert all(c.allowed_domains == ALLOWED_DOMAINS and c.blocked_domains == () for c in calls)
    assert all(
        c.max_searches == 3 and c.model == "claude-haiku-5-5" and c.ctx.purpose == "jobsearch.scout"
        for c in calls
    )
    assert all(name not in c.user_text and name not in c.system_text for c in calls)
    places_sent = sorted(str(c.user_location) for c in calls)
    assert any("Bengaluru" in p for p in places_sent) and any("Bengaluru" not in p for p in places_sent)
    reads = rig.claude.calls_for("jobsearch.judge")
    assert reads and all(name not in c.user_text and c.model == "claude-haiku-5-5" for c in reads)


async def test_boards_come_from_the_search_links_only_and_are_read_once_in_a_while(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    before = rig.fetched("kestrelpay")
    await rig.run()
    assert rig.fetched(WEB_TOKEN) == 1
    assert rig.fetched("jobgether") == 0 and not any("linkedin" in url for url in rig.boards.urls)
    assert rig.fetched("kestrelpay") == before  # a registry employer is read from Discover's store
    await rig.run()
    assert rig.fetched(WEB_TOKEN) == 1  # the second search a minute later reuses the read


async def test_the_read_never_sees_another_field_a_leaders_role_or_a_role_elsewhere(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured("jobsearch.judge", reader({}), repeat=True)
    events = await rig.run()
    shown = "\n".join(c.user_text for c in rig.claude.calls_for("jobsearch.judge"))
    assert "Title: Frontend Engineer\nEmployer: Harbour Labs\nLocation: Bengaluru" in shown
    for title in ("UI Tester", "Staff Frontend Engineer", "Backend Engineer"):
        assert f"Title: {title}\n" not in shown
    assert "Berlin" not in shown
    assert jobs(events) == []  # every read said "another kind of role"
    assert done(events).note is not None


async def test_a_posting_that_talks_to_the_ai_is_left_out_and_said_so(make_rig: Any) -> None:
    rig = await make_rig(
        board=web_board(gh_job(106, "Frontend Engineer II", "Bengaluru, India", INJECTED_HTML))
    )
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge",
        reader(
            {
                "Frontend Engineer II": lambda pid, ev: item(
                    pid, [("React and TypeScript, 2+ years", [ev["React"]], "met")]
                )
            }
        ),
        repeat=True,
    )
    events = await rig.run()
    assert all(job.title != "Frontend Engineer II" for job in jobs(events))
    (result,) = [e.step for e in events if isinstance(e, JobSearchStepEvent) and e.step.stage == "result"]
    assert "aimed at an AI" in result.text


async def test_a_scout_that_fails_is_reported_and_the_others_go_on(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(ClaudeOverloaded(), when=lambda call: "remote" in call.user_text)
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    events = await rig.run()
    texts = [e.step.text for e in events if isinstance(e, JobSearchStepEvent) and e.step.stage == "search"]
    assert sum("did not finish" in text for text in texts) == 1
    assert len(jobs(events)) == 1


async def test_when_every_scout_fails_the_search_ends_with_that_error(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(ClaudeOverloaded(), repeat=True)
    with pytest.raises(ClaudeOverloaded):
        await rig.run()
    assert rig.claude.calls_for("jobsearch.judge") == []


async def test_a_read_that_fails_shows_nothing_for_its_postings_and_says_so(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured("jobsearch.judge", ClaudeOverloaded(), repeat=True)
    events = await rig.run()
    assert jobs(events) == []
    (result,) = [e.step for e in events if isinstance(e, JobSearchStepEvent) and e.step.stage == "result"]
    assert "could not be read" in result.text and done(events).jobs == 0


async def test_a_profile_with_no_role_searches_nothing(make_rig: Any) -> None:
    rig = await make_rig()
    data = profile_of("frontend").to_dict()
    data["person"]["currentRole"] = ""
    data["roles"] = []
    events = await rig.run(Profile.from_dict(data))
    assert len(events) == 1 and done(events).jobs == 0 and "no role" in (done(events).note or "")
    assert rig.claude.calls == []


async def test_switched_off_it_refuses_before_anything_runs(make_rig: Any) -> None:
    rig = await make_rig(settings=JobSearchSettings(enabled=False))
    with pytest.raises(JobSearchDisabled):
        rig.service.require_enabled()
