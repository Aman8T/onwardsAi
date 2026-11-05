"""Job intake: ATS fingerprints from real public job-link shapes, the page's own markers, the job read from JSON-LD or markup,
and sites that forbid automation never fetched."""

from __future__ import annotations

from pathlib import Path

import pytest

from tayyari.apply.intake import (
    Fingerprint,
    extract_job,
    fingerprint_html,
    fingerprint_url,
    intake,
    normalize_url,
)
from tayyari.core.settings import AUTO_APPLY_MANUAL_ONLY_DOMAINS

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"


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
