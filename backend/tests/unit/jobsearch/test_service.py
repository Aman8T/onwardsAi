"""A search with AI end to end over the fakes: scripted Haiku scouts and reads, hand-written boards, Discover's store.

What it proves: the stream's order; that boards come only from the search tool's links (never a recruiter's, never
fetched twice in a while, Leafout's own employers from the store); that the read never sees a role of another field, a
leader's role or one elsewhere; that nothing a model invented reaches a job; that a search carries no name; and how a
failing scout, a failing read and an injected posting are handled."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tayyari.contract.jobsearch import (
    AvoidPrefs,
    JobSearchDoneEvent,
    JobSearchJobEvent,
    JobSearchStepEvent,
    ReferralLine,
    ReferralNote,
    RolePrep,
)
from tayyari.contract.models import Profile
from tayyari.core.settings import DiscoverSettings, JobSearchSettings
from tayyari.discover import places
from tayyari.discover.service import DiscoverService
from tayyari.jobsearch import service as service_module
from tayyari.jobsearch.boards import BoardReader, pretty_name
from tayyari.jobsearch.errors import JobSearchDisabled
from tayyari.jobsearch.harvest import ALLOWED_DOMAINS, BoardLead, BoardRef
from tayyari.jobsearch.service import JobSearchService
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.llm.types import CallContext
from tayyari.storage.ports import StoredSearchDoc
from tests.fakes.claude import FakeClaudeClient, search_run
from tests.fakes.clock import FakeClock
from tests.fakes.discover import NOW, FakeBoards, FakeDiscoverRepo, fixture_registry, profile_of, read_json
from tests.fakes.jobsearch import FakeJobSearchRepo
from tests.unit.discover.conftest import BOARD_FILES
from tests.unit.jobsearch.support import (
    INJECTED_HTML,
    WEB_COMPANY,
    WEB_TOKEN,
    frontend_answer,
    gh_job,
    item,
    listing,
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
    def __init__(
        self, service: JobSearchService, claude: FakeClaudeClient, boards: FakeBoards, runs: FakeJobSearchRepo
    ) -> None:
        self.service, self.claude, self.boards, self.runs = service, claude, boards, runs

    async def run(self, profile: Profile | None = None, **options: Any) -> list[Any]:
        found = self.service.run(profile or profile_of("frontend"), BENGALURU, CTX, **options)
        return [event async for event in found]

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
        runs = FakeJobSearchRepo()
        service = JobSearchService(
            claude=claude,
            settings=settings or JobSearchSettings(),
            repo=repo,
            runs=runs,
            registry=fixture_registry,
            reader=reader_,
            clock=clock,
        )
        return Rig(service, claude, boards, runs)

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

    stages = steps(events)
    assert stages[0] == "plan" and stages[-1] == "result" and stages.count("search") == 3
    assert stages.index("boards") < stages.index("narrow") < len(stages) - 1
    assert isinstance(events[-1], JobSearchDoneEvent) and isinstance(events[-2], JobSearchStepEvent)
    (job,) = [j for j in jobs(events) if j.foundVia == "web"]
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


async def test_when_every_scout_fails_and_nothing_near_fits_the_search_ends_with_that_error(
    make_rig: Any,
) -> None:
    rig = await make_rig()
    rig.claude.script_search(ClaudeOverloaded(), repeat=True)
    rig.claude.script_structured("jobsearch.judge", reader({}), repeat=True)
    with pytest.raises(ClaudeOverloaded):
        await rig.run()
    assert rig.runs.runs == {}


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


# ───────────────────────── runs, choices and what is built on a role ─────────────────────────


def _good(posting_id: str, ev: dict[str, str]) -> dict[str, Any]:
    """A read that meets one ask of a frontend posting whose text says React (the stored listings: the near half)."""
    return item(posting_id, [("React and TypeScript", [ev["React"]], "met")], kind="same")


def jobs_of_run(rig: Rig, run_id: str) -> list[dict[str, Any]]:
    return list(rig.runs.runs[run_id].result["jobs"])


async def test_a_finished_search_is_stored_and_read_back_as_the_last_results(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    events = await rig.run()
    finished = done(events)
    assert finished.runId in rig.runs.runs and finished.createdAt == NOW.isoformat()
    profile_id = profile_of("frontend").id
    latest = await rig.service.latest(profile_id, BENGALURU, True)
    assert latest is not None and latest.runId == finished.runId
    assert [j.id for j in latest.jobs] == [j.id for j in jobs(events)]
    assert [s.stage for s in latest.trail] == steps(events) and latest.funnel == finished.funnel
    assert await rig.service.latest(profile_id, BENGALURU, False) is None
    assert await rig.service.latest("someone-else", BENGALURU, True) is None
    (job,) = [j for j in latest.jobs if j.foundVia == "web"]
    posting = await rig.service.posting(profile_id, finished.runId, job.id)
    assert posting is not None and "Strong experience with React and TypeScript" in posting.postingText
    assert posting.applyUrl == HARBOUR_URL and posting.employer == WEB_COMPANY
    assert await rig.service.posting("someone-else", finished.runId, job.id) is None
    assert await rig.service.posting(profile_id, finished.runId, "f" * 32) is None


async def test_the_card_counts_every_checked_ask(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    (job,) = [j for j in jobs(await rig.run()) if j.foundVia == "web"]
    assert (job.asks.met, job.asks.partly, job.asks.notShown) == (2, 0, 1)
    assert job.openLong is None  # first published three days ago


async def test_without_remote_roles_there_is_no_remote_scout_and_no_remote_role(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    events = await rig.run(remote=False)
    assert len(rig.claude.search_calls) == 2
    assert all("remote" not in c.user_text.lower() for c in rig.claude.search_calls)
    assert all(j.place.label != "Remote, India" for j in jobs(events))
    read = "\n".join(c.user_text for c in rig.claude.calls_for("jobsearch.judge"))
    assert "Remote - India" not in read
    (stored,) = rig.runs.runs.values()
    assert stored.remote is False and done(events).runId == stored.id


async def test_not_for_me_keeps_hidden_roles_and_waved_off_employers_out(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    first = await rig.run()
    (harbour,) = [j for j in jobs(first) if j.employer == WEB_COMPANY]
    before = len(rig.claude.calls_for("jobsearch.judge"))
    avoid = AvoidPrefs(jobIds=[harbour.id], employers=["Kestrel Pay"], noStepUp=False)
    second = await rig.run(avoid=avoid)
    assert all(j.id != harbour.id for j in jobs(second))
    shown = "\n".join(c.user_text for c in rig.claude.calls_for("jobsearch.judge")[before:])
    assert "Employer: Kestrel Pay" not in shown and "Employer: Harbour Labs" not in shown


async def test_too_senior_leaves_out_roles_a_step_up(make_rig: Any) -> None:
    board = web_board(
        gh_job(
            107, "Senior Frontend Engineer", "Bengaluru, India", "&lt;p&gt;React and TypeScript.&lt;/p&gt;"
        )
    )
    rig = await make_rig(board=board)
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge",
        reader({"Frontend Engineer": frontend_answer, "Senior Frontend Engineer": _good}),
        repeat=True,
    )
    events = await rig.run(avoid=AvoidPrefs(jobIds=[], employers=[], noStepUp=True))
    assert all("Senior" not in j.title for j in jobs(events))
    read = "\n".join(c.user_text for c in rig.claude.calls_for("jobsearch.judge"))
    assert "Title: Senior Frontend Engineer" not in read


async def test_tayyaris_own_employers_are_read_while_the_web_search_runs(make_rig: Any) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    events = await rig.run()
    reads = [e.step.text for e in events if isinstance(e, JobSearchStepEvent) and e.step.stage == "read"]
    assert reads and "Leafout's own employers" in reads[0]
    ids = [j.id for j in jobs(events)]
    assert len(ids) == len(set(ids))  # a role is read once, whichever half found it
    judged = "\n".join(c.user_text for c in rig.claude.calls_for("jobsearch.judge"))
    assert judged.count("Employer: Harbour Labs\nLocation: Bengaluru") == 1


async def test_a_prep_page_is_written_once_kept_and_only_for_the_runs_own_profile(
    make_rig: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    finished = done(await rig.run())
    written: list[str] = []

    async def fake_prepare(**kwargs: Any) -> RolePrep:
        written.append(kwargs["posting_text"][:40])
        job = kwargs["job"]
        return RolePrep(
            jobId=job.id,
            title=job.title,
            employer=job.employer,
            lead=[],
            gaps=[],
            questions=[],
            createdAt="x",
        )

    monkeypatch.setattr(service_module, "prepare_role", fake_prepare)
    profile = profile_of("frontend")
    job_id = next(j for j in jobs_of_run(rig, finished.runId) if j["foundVia"] == "web")["id"]
    first = await rig.service.prepare(profile, finished.runId, job_id, CTX)
    again = await rig.service.prepare(profile, finished.runId, job_id, CTX)
    assert first is not None and again == first and len(written) == 1
    assert await rig.service.prepare(profile, finished.runId, job_id, CTX, fresh=True) is not None
    assert len(written) == 2
    other = Profile.from_dict({**profile.to_dict(), "id": "b" * 32})
    assert await rig.service.prepare(other, finished.runId, job_id, CTX) is None
    assert await rig.service.prepare(profile, "c" * 32, job_id, CTX) is None


async def test_a_referral_note_is_written_once_and_rewritten_on_request(
    make_rig: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    finished = done(await rig.run())
    calls: list[int] = []

    async def fake_referral(**kwargs: Any) -> ReferralNote:
        calls.append(1)
        job = kwargs["job"]
        return ReferralNote(
            jobId=job.id, lines=[], ask="Would you be open to referring me?", link=job.applyUrl, createdAt="x"
        )

    monkeypatch.setattr(service_module, "write_referral", fake_referral)
    profile = profile_of("frontend")
    job_id = next(j for j in jobs_of_run(rig, finished.runId) if j["foundVia"] == "web")["id"]
    note = await rig.service.referral(profile, finished.runId, job_id, CTX)
    assert note is not None and note.link == HARBOUR_URL
    await rig.service.referral(profile, finished.runId, job_id, CTX)
    await rig.service.referral(profile, finished.runId, job_id, CTX, fresh=True)
    assert len(calls) == 2


OLD_SHAPE = {
    "lines": [
        {
            "part": "company",
            "text": "Hi [name], Harbour Labs is hiring a Frontend Engineer in Bengaluru.",
            "sources": ["the posting"],
        },
        {"part": "you", "text": "I rebuilt the checkout in React.", "sources": ["your resume"]},
        {"part": "role", "text": "It is a Frontend Engineer role at my level.", "sources": ["the posting"]},
    ],
    "ask": "Would you be open to referring me?",
    "link": HARBOUR_URL,
    "createdAt": "2026-10-15T09:00:00+00:00",
}


async def test_a_referral_note_stored_in_the_old_shape_is_written_again_and_the_new_one_is_kept(
    make_rig: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_rig()
    rig.claude.script_search(search_run(RESULTS), repeat=True)
    rig.claude.script_structured(
        "jobsearch.judge", reader({"Frontend Engineer": frontend_answer}), repeat=True
    )
    finished = done(await rig.run())
    calls: list[int] = []

    async def fake_referral(**kwargs: Any) -> ReferralNote:
        calls.append(1)
        job = kwargs["job"]
        lines = [
            ReferralLine(
                part="intro", text="I'm a Frontend Engineer in Bengaluru.", sources=["your profile"]
            ),
            ReferralLine(
                part="fit", text="Harbour Labs wants React, and I rebuilt it.", sources=["the posting"]
            ),
            ReferralLine(part="draw", text="What draws me is the role.", sources=["the posting"]),
        ]
        return ReferralNote(
            jobId=job.id,
            lines=lines,
            ask="Would you be open to referring me?",
            link=job.applyUrl,
            createdAt="x",
        )

    monkeypatch.setattr(service_module, "write_referral", fake_referral)
    profile = profile_of("frontend")
    job_id = next(j for j in jobs_of_run(rig, finished.runId) if j["foundVia"] == "web")["id"]
    old = StoredSearchDoc(
        profile_id=profile.id,
        job_id=job_id,
        kind="referral",
        created_at=NOW,
        body={"jobId": job_id, **OLD_SHAPE},
    )
    await rig.runs.save_doc(old)

    note = await rig.service.referral(profile, finished.runId, job_id, CTX)
    assert note is not None and calls == [1]
    assert [line.part for line in note.lines] == ["intro", "fit", "draw"]
    kept = await rig.runs.get_doc(profile.id, job_id, "referral")
    assert kept is not None and [line["part"] for line in kept.body["lines"]] == ["intro", "fit", "draw"]
    again = await rig.service.referral(profile, finished.runId, job_id, CTX)
    assert again == note and calls == [1]


@pytest.mark.parametrize(
    ("slug", "name"),
    [
        ("appen-2", "Appen"),
        ("appen-2-3", "Appen"),
        ("kestrel-pay", "Kestrel Pay"),
        ("studio-54-labs", "Studio 54 Labs"),
        ("7-eleven", "7 Eleven"),
        ("acme_labs.io", "Acme Labs Io"),
        ("2024", "2024"),
        ("2024-2", "2024"),
    ],
)
def test_a_slug_gives_a_readable_employer_name_without_the_numbers_on_its_end(slug: str, name: str) -> None:
    assert pretty_name(slug) == name


async def test_an_employer_that_is_only_a_board_slug_is_shown_without_the_boards_number(
    make_rig: Any,
) -> None:
    rig = await make_rig()
    assert rig.service._employer_name(listing(employer_id="appen-2")) == "Appen"
    assert rig.service._employer_name(listing(employer_id="2024")) == "2024"


async def test_a_web_employer_nobody_names_is_called_by_its_slug_and_a_name_from_the_board_wins() -> None:
    boards = FakeBoards()
    web = BoardReader(
        clock=FakeClock(NOW),
        timeout_s=5,
        concurrency=1,
        cache_minutes=60,
        listing_cap=300,
        transport=boards.transport,
    )
    lead = BoardLead(ref=BoardRef("lever", "appen-2"), first_query="Frontend Engineer")
    assert web._employer(lead, None).name == "Appen"
    hinted = BoardLead(ref=BoardRef("lever", "appen-2"), first_query="x", name_hint="Appen Limited")
    assert web._employer(hinted, None).name == "Appen Limited"
    assert web._employer(lead, {"jobs": [{"company_name": "Appen Ltd"}]}).name == "Appen Ltd"
