"""Job intake: ATS fingerprints from real public job-link shapes, the page's own markers, the job read from JSON-LD or markup,
sites that forbid automation never fetched (nor parsed after a redirect onto one), the liveness check against the ATS read
endpoints (hand-made fixtures in tests/data/apply/live), one posting key whatever link reached it, and the same-posting
check for a pasted application link."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tayyari.apply.intake import (
    Fingerprint,
    JobInfo,
    PageAnswered,
    check_live,
    extract_job,
    fingerprint_html,
    fingerprint_url,
    greenhouse_embed,
    intake,
    liveness_url,
    normalize_url,
    posting_key,
    same_posting,
)
from tayyari.core.settings import AUTO_APPLY_MANUAL_ONLY_DOMAINS
from tayyari.ingest.web import FetchProblem

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"
LIVE = DATA / "live"


@pytest.mark.parametrize(
    ("url", "ats", "apply_url"),
    [
        (
            "https://job-boards.greenhouse.io/anthropic/jobs/4020305008",
            "greenhouse",
            "https://job-boards.greenhouse.io/anthropic/jobs/4020305008",
        ),
        (
            "https://boards.greenhouse.io/airbnb/jobs/7010000002?gh_src=abc",
            "greenhouse",
            "https://boards.greenhouse.io/airbnb/jobs/7010000002",
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=stripe&token=6123456",
            "greenhouse",
            "https://boards.greenhouse.io/embed/job_app?for=stripe&token=6123456",
        ),
        (
            "https://jobs.lever.co/leverdemo/5ac21346-8e0c-4494-8e7a-3eb92ff77902",
            "lever",
            "https://jobs.lever.co/leverdemo/5ac21346-8e0c-4494-8e7a-3eb92ff77902/apply",
        ),
        (
            "https://jobs.lever.co/leverdemo/5ac21346-8e0c-4494-8e7a-3eb92ff77902/apply",
            "lever",
            "https://jobs.lever.co/leverdemo/5ac21346-8e0c-4494-8e7a-3eb92ff77902/apply",
        ),
        (
            "https://jobs.ashbyhq.com/ashby/2b8e1a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b",
            "ashby",
            "https://jobs.ashbyhq.com/ashby/2b8e1a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b/application",
        ),
        (
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/India-Bengaluru/Engineer_JR123",
            "workday",
            None,
        ),
        ("https://careers.example.com/jobs/123?gh_jid=4567", "greenhouse", None),
        ("https://www.acme.dev/careers/analyst", "unknown", None),
    ],
)
def test_fingerprint_by_url(url: str, ats: str, apply_url: str | None) -> None:
    clean = normalize_url(url)
    assert clean is not None
    found = fingerprint_url(clean)
    assert found.ats == ats and found.apply_url == apply_url


def test_fingerprint_by_page_markers() -> None:
    gh = (DATA / "greenhouse_job.html").read_text(encoding="utf-8")
    lever = (DATA / "lever_apply.html").read_text(encoding="utf-8")
    ashby = (DATA / "ashby_application.html").read_text(encoding="utf-8")
    unknown = (DATA / "unknown_form.html").read_text(encoding="utf-8")
    url = "https://careers.acme.com/role"
    assert fingerprint_html(gh, url, Fingerprint("unknown")).ats == "greenhouse"
    assert fingerprint_html(gh, url, Fingerprint("unknown")).apply_url == url
    assert fingerprint_html(lever, url, Fingerprint("unknown")).ats == "lever"
    assert fingerprint_html(ashby, url, Fingerprint("unknown")).ats == "ashby"
    assert fingerprint_html(unknown, url, Fingerprint("unknown")).ats == "unknown"
    embed = '<div id="grnhse_app"></div><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
    found = fingerprint_html(embed, url, Fingerprint("greenhouse", None, None, "4567"))
    assert found.apply_url == "https://boards.greenhouse.io/embed/job_app?for=acme&token=4567"


def test_the_job_is_read_from_json_ld_then_markup() -> None:
    gh = extract_job((DATA / "greenhouse_job.html").read_text(encoding="utf-8"), "greenhouse")
    assert (gh.title, gh.company, gh.location) == ("Backend Engineer", "Acme", "Bengaluru, IN")
    assert gh.description == "Build APIs in Python."
    lever = extract_job((DATA / "lever_apply.html").read_text(encoding="utf-8"), "lever")
    assert lever.title == "Platform Engineer" and lever.location == "Remote, India"


class Pages:
    def __init__(self, page: str | None) -> None:
        self.page, self.calls = page, 0

    async def fetch(self, url: str) -> tuple[str, str]:
        self.calls += 1
        if self.page is None:
            raise ValueError("unreachable")
        return url, self.page


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/123",
        "https://www.naukri.com/job-listings-x",
        "https://in.indeed.com/viewjob?jk=1",
    ],
)
async def test_sites_that_forbid_automation_are_never_fetched(url: str) -> None:
    pages = Pages("<html></html>")
    got = await intake(url, pages, AUTO_APPLY_MANUAL_ONLY_DOMAINS)
    assert got.manual_only and pages.calls == 0


async def test_intake_reads_the_page_and_survives_a_failed_fetch() -> None:
    page = (DATA / "lever_apply.html").read_text(encoding="utf-8")
    got = await intake("jobs.lever.co/acme/0f8f8b3c-1d2e-4c5b-9a7f-123456789abc", Pages(page), ())
    assert (
        got.fingerprint.ats == "lever"
        and got.job.title == "Platform Engineer"
        and got.domain == "jobs.lever.co"
    )
    failed = await intake("https://jobs.lever.co/acme/0f8f8b3c-1d2e-4c5b-9a7f-123456789abc", Pages(None), ())
    assert failed.fetch_error == "unreachable" and failed.fingerprint.apply_url is not None
    with pytest.raises(ValueError):
        await intake("not a link", Pages(page), ())


class Redirecting:
    """A `PageFetcher` whose link redirected to ``final`` (answering ``status``); counts what it was asked."""

    def __init__(self, final: str, page: str = "<html><h1>Sign in</h1></html>", status: int = 200) -> None:
        self.final, self.page, self.status, self.calls = final, page, status, 0

    async def fetch(self, url: str) -> tuple[str, str]:
        self.calls += 1
        if self.status >= 400:
            raise PageAnswered(self.status, self.final)
        return self.final, self.page


async def test_a_redirect_onto_a_board_that_forbids_automation_is_refused() -> None:
    page = (DATA / "lever_apply.html").read_text(encoding="utf-8")
    fetcher = Redirecting("https://www.linkedin.com/jobs/view/123", page)
    got = await intake("https://careers.acme.com/jobs/7", fetcher, AUTO_APPLY_MANUAL_ONLY_DOMAINS)
    assert got.manual_only and got.fingerprint == Fingerprint("unknown") and got.job == JobInfo()
    bounced = Redirecting("https://www.naukri.com/login", status=403)
    got = await intake("https://careers.acme.com/jobs/7", bounced, AUTO_APPLY_MANUAL_ONLY_DOMAINS)
    assert got.manual_only and got.fetch_error is None


async def test_intake_keeps_the_status_of_a_page_that_answered_with_an_error() -> None:
    url = "https://jobs.lever.co/acme/0f8f8b3c-1d2e-4c5b-9a7f-123456789abc"
    gone = await intake(url, Redirecting(url, status=404), AUTO_APPLY_MANUAL_ONLY_DOMAINS)
    assert gone.fetch_status == 404 and gone.fetch_error == "the page answered 404"
    assert not gone.manual_only
    unreachable = await intake(url, Pages(None), AUTO_APPLY_MANUAL_ONLY_DOMAINS)
    assert unreachable.fetch_status is None and unreachable.fetch_error == "unreachable"


# ───────────────────────── liveness ─────────────────────────

GH = Fingerprint("greenhouse", "https://job-boards.greenhouse.io/acme/jobs/4020305008", "acme", "4020305008")
LEVER = Fingerprint(
    "lever",
    "https://jobs.lever.co/acme/0f8f8b3c-1d2e-4c5b-9a7f-123456789abc/apply",
    "acme",
    "0f8f8b3c-1d2e-4c5b-9a7f-123456789abc",
)
ASHBY = Fingerprint(
    "ashby",
    "https://jobs.ashbyhq.com/acme/2b8e1a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b/application",
    "acme",
    "2b8e1a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b",
)
ASHBY_GONE = Fingerprint(
    "ashby",
    "https://jobs.ashbyhq.com/acme/11111111-2222-4333-8444-555555555555/application",
    "acme",
    "11111111-2222-4333-8444-555555555555",
)


def fixture(name: str) -> str:
    return (LIVE / name).read_text(encoding="utf-8")


class Api:
    """A `StatusFetcher` with one canned answer (or one exception); records every URL it was asked for."""

    def __init__(
        self,
        status: int = 200,
        text: str = "",
        *,
        final: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.status, self.text, self.final, self.error = status, text, final, error
        self.asked: list[tuple[str, str]] = []

    async def fetch(self, url: str, accept: str) -> tuple[int, str, str]:
        self.asked.append((url, accept))
        if self.error is not None:
            raise self.error
        return self.status, self.final or url, self.text


@pytest.mark.parametrize(
    ("fp", "status", "body", "verdict"),
    [
        (GH, 200, "greenhouse_job_open.json", "live"),
        (GH, 404, "greenhouse_job_closed.json", "closed"),
        (LEVER, 200, "lever_posting_open.json", "live"),
        (LEVER, 404, "lever_posting_closed.json", "closed"),
        (ASHBY, 200, "ashby_board.json", "live"),
        (ASHBY_GONE, 200, "ashby_board.json", "closed"),
        (GH, 503, "greenhouse_job_closed.json", "unknown"),
        (LEVER, 502, "lever_posting_closed.json", "unknown"),
        (ASHBY, 500, "ashby_board.json", "unknown"),
        (GH, 429, "greenhouse_job_closed.json", "unknown"),
        (LEVER, 200, "greenhouse_job_open.json", "unknown"),  # another job's answer, not this one
    ],
)
async def test_liveness_from_the_ats_read_endpoints(
    fp: Fingerprint, status: int, body: str, verdict: str
) -> None:
    api = Api(status, fixture(body))
    assert await check_live(fp, api) == verdict
    assert len(api.asked) == 1 and api.asked[0][1] == "application/json"


def test_the_read_endpoints_are_the_ats_public_apis() -> None:
    assert liveness_url(GH) == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4020305008"
    assert liveness_url(LEVER) == "https://api.lever.co/v0/postings/acme/0f8f8b3c-1d2e-4c5b-9a7f-123456789abc"
    assert liveness_url(ASHBY) == "https://api.ashbyhq.com/posting-api/job-board/acme"
    eu = Fingerprint("greenhouse", "https://job-boards.eu.greenhouse.io/acme/jobs/1", "acme", "1")
    assert liveness_url(eu) == "https://boards-api.eu.greenhouse.io/v1/boards/acme/jobs/1"
    for fp in (
        Fingerprint("workday"),
        Fingerprint("unknown", "https://acme.dev/apply"),
        Fingerprint("greenhouse", None, None, "4567"),  # gh_jid with no board
        Fingerprint("greenhouse", None, "acme", "12&for=evil"),
        Fingerprint("lever", None, "..", "0f8f8b3c-1d2e-4c5b-9a7f-123456789abc"),
        Fingerprint("ashby", None, "acme", "not-a-uuid"),
    ):
        assert liveness_url(fp) is None


@pytest.mark.parametrize(
    "error", [FetchProblem("That site could not be reached."), TimeoutError(), OSError("refused")]
)
async def test_an_unreachable_check_is_unknown_never_closed(error: Exception) -> None:
    for fp in (GH, LEVER, ASHBY):
        assert await check_live(fp, Api(error=error)) == "unknown"


async def test_nothing_is_fetched_without_a_read_endpoint() -> None:
    api = Api(200, "{}")
    assert await check_live(Fingerprint("workday"), api) == "unknown"
    assert await check_live(Fingerprint("greenhouse", None, None, "4567"), api) == "unknown"
    assert api.asked == []


async def test_an_answer_that_redirected_off_the_api_host_is_unknown() -> None:
    redirected = Api(404, "{}", final="https://www.example.com/careers")
    assert await check_live(GH, redirected) == "unknown"


async def test_an_ashby_board_cut_short_is_read_for_the_id_but_never_says_closed() -> None:
    whole = fixture("ashby_board.json")
    cut = whole[: whole.index("7c6d5e4f")]  # the size cap stopped inside the second job
    assert await check_live(ASHBY, Api(200, cut)) == "live"
    assert await check_live(ASHBY_GONE, Api(200, cut)) == "unknown"
    assert await check_live(ASHBY, Api(200, "<html>maintenance</html>")) == "unknown"


# ───────────────────────── one posting, whatever link reached it ─────────────────────────


@pytest.mark.parametrize(
    "links",
    [
        (
            "https://boards.greenhouse.io/stripe/jobs/6123456",
            "https://job-boards.greenhouse.io/stripe/jobs/6123456?gh_src=abc",
            "https://boards.greenhouse.io/embed/job_app?for=stripe&token=6123456",
        ),
        (
            "https://jobs.lever.co/acme/0f8f8b3c-1d2e-4c5b-9a7f-123456789abc",
            "https://jobs.lever.co/acme/0f8f8b3c-1d2e-4c5b-9a7f-123456789abc/apply",
            "https://jobs.lever.co/Acme/0F8F8B3C-1D2E-4C5B-9A7F-123456789ABC/",
        ),
        (
            "https://jobs.ashbyhq.com/acme/2b8e1a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b",
            "https://jobs.ashbyhq.com/acme/2b8e1a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b/application",
        ),
    ],
)
def test_the_posting_key_is_the_same_for_the_job_page_apply_and_embed(
    links: tuple[str, ...],
) -> None:
    keys = set()
    for link in links:
        clean = normalize_url(link)
        assert clean is not None
        keys.add(posting_key(fingerprint_url(clean), clean))
    assert len(keys) == 1
    (key,) = keys
    assert key.count(":") == 2 and len(key) <= 64


def test_the_posting_key_falls_back_to_the_normalised_url() -> None:
    a = posting_key(Fingerprint("workday"), "https://acme.wd5.myworkdayjobs.com/job/1#apply")
    b = posting_key(Fingerprint("workday"), "acme.wd5.myworkdayjobs.com/job/1")
    assert a == b and len(a) == 64 and ":" not in a
    other = posting_key(Fingerprint("unknown"), "https://acme.dev/b")
    assert posting_key(Fingerprint("unknown"), "https://acme.dev/a") != other
    long_board = Fingerprint("ashby", None, "b" * 40, "2b8e1a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b")
    assert len(posting_key(long_board, "https://jobs.ashbyhq.com/x")) == 64
    with pytest.raises(ValueError):
        posting_key(Fingerprint("unknown"), "not a link")


def test_a_derived_greenhouse_url_stays_on_greenhouse() -> None:
    page = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
    site = "https://careers.acme.com/role"
    found = fingerprint_html(page, site, Fingerprint("greenhouse", None, None, "4567"))
    assert found.apply_url is not None
    assert urlsplit(found.apply_url).hostname == "boards.greenhouse.io"
    # a gh_jid that is not a number never becomes part of a derived URL
    odd = fingerprint_html(page, site, Fingerprint("greenhouse", None, None, "1&for=x"))
    assert odd.apply_url is None
    embed = "https://boards.greenhouse.io/embed/job_app?for=acme&token=4567"
    assert greenhouse_embed("acme", "4567") == embed
    assert greenhouse_embed("acme/../x", "4567") is None


def test_an_eu_greenhouse_embed_stays_on_the_eu_instance() -> None:
    page = '<script src="https://boards.eu.greenhouse.io/embed/job_board/js?for=acme"></script>'
    found = fingerprint_html(
        page, "https://careers.acme.eu/role?gh_jid=4567", Fingerprint("greenhouse", None, None, "4567")
    )
    assert found.apply_url == "https://boards.eu.greenhouse.io/embed/job_app?for=acme&token=4567"
    assert liveness_url(found) == "https://boards-api.eu.greenhouse.io/v1/boards/acme/jobs/4567"
    us = fingerprint_html(
        page.replace(".eu.", "."),
        "https://careers.eu.acme.com/role",
        Fingerprint("greenhouse", None, None, "4567"),
    )
    assert liveness_url(us) == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4567"


def test_an_employer_domain_never_picks_the_api_instance() -> None:
    # a form on the employer's own page: the region cannot be known, so nothing is checked (unknown, never closed)
    own = Fingerprint("greenhouse", "https://careers.eu.acme.com/apply", "acme", "4567")
    assert liveness_url(own) is None
    lever_own = Fingerprint(
        "lever", "https://jobs.eu.acme.com/x", "acme", "2b8e1a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b"
    )
    assert liveness_url(lever_own) is None


@pytest.mark.parametrize(
    ("job", "title", "company", "same"),
    [
        (JobInfo("Backend Engineer", "Acme"), "Backend Engineer", "Acme", True),
        (
            JobInfo("Senior Backend Engineer (Remote)", "Acme Technologies Pvt. Ltd."),
            "Backend Engineer",
            "Acme Technologies",
            True,
        ),
        (
            JobInfo("Ingénieur Logiciel", "Société Générale"),
            "ingenieur logiciel",
            "Societe Generale",
            True,
        ),
        (JobInfo("Product Analyst", None), "Product Analyst - Growth", "Acme", True),
        (JobInfo("Analyst", "Acme"), "Analyst", "Acme Inc", True),
        (JobInfo("Data Engineer", "Acme"), "Software Engineer", "Acme", False),
        (JobInfo("Software Engineer", "Acme"), "Senior Software Engineer, Payments Platform", "Acme", False),
        (JobInfo("Engineer", "Acme"), "Software Engineer", "Acme", False),
        (JobInfo("Backend Engineer", "Globex"), "Backend Engineer", "Acme", False),
        (JobInfo(None, "Acme"), "Backend Engineer", "Acme", False),
        (JobInfo("Backend Engineer", "Acme"), None, "Acme", False),
    ],
)
def test_same_posting(job: JobInfo, title: str | None, company: str | None, same: bool) -> None:
    assert same_posting(job, title, company) is same
