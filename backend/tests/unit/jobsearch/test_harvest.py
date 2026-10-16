"""Board links come from the search tool's result URLs only: which hosts count, which tokens, which boards are skipped,
and the order boards are read in."""

from __future__ import annotations

import pytest

from tayyari.jobsearch.harvest import ALLOWED_DOMAINS, BoardRef, board_of, harvest, name_hint
from tayyari.llm.types import SearchCall, SearchHit


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://job-boards.greenhouse.io/headoutcareers/jobs/4707747006",
            BoardRef("greenhouse", "headoutcareers"),
        ),
        ("https://boards.greenhouse.io/canonical/jobs/5150422", BoardRef("greenhouse", "canonical")),
        ("https://job-boards.greenhouse.io/sonicwall", BoardRef("greenhouse", "sonicwall")),
        (
            "https://boards.greenhouse.io/embed/job_app?for=razorpaysoftware&token=123",
            BoardRef("greenhouse", "razorpaysoftware"),
        ),
        (
            "https://jobs.lever.co/gohighlevel/b8dc97ec-a0b7-4195-9f3b-86440ff18f82",
            BoardRef("lever", "gohighlevel"),
        ),
        (
            "https://jobs.lever.co/acceldata/428eb3e3/apply?lever-source%5B%5D=x",
            BoardRef("lever", "acceldata"),
        ),
        ("https://jobs.lever.co/Pelago", BoardRef("lever", "Pelago")),
    ],
)
def test_an_employer_board_link_names_its_board(url: str, expected: BoardRef) -> None:
    assert board_of(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/123",
        "https://www.naukri.com/job-listings-frontend",
        "https://jobs.ashbyhq.com/aiprise/ade1484a",  # Ashby stays off until it gives written permission
        "https://job-boards.eu.greenhouse.io/acme/jobs/1",  # EU boards are another API
        "https://job-boards.greenhouse.io/",
        "https://boards.greenhouse.io/embed/job_app?token=123",
        "https://jobs.lever.co/weekdayworks/84b31854",  # a recruiter posting other companies' roles
        "https://jobs.lever.co/jobgether/c5f43fb9",
        "https://job-boards.greenhouse.io/bad%20token/jobs/1",
        "ftp://jobs.lever.co/acme",
        "not a url",
    ],
)
def test_anything_else_is_not_a_board(url: str) -> None:
    assert board_of(url) is None


def test_the_searches_may_reach_only_the_boards_we_can_read() -> None:
    assert ALLOWED_DOMAINS == ("job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co")


def test_a_result_title_gives_the_employer_name_when_the_board_does_not() -> None:
    assert name_hint("greenhouse", "Job Application for Frontend Engineer at Headout") == "Headout"
    assert name_hint("lever", "HighLevel - SDE II - Frontend") == "HighLevel"
    assert name_hint("lever", "Frontend Engineer") is None
    assert name_hint("greenhouse", "") is None


def _hit(url: str, title: str = "") -> SearchHit:
    return SearchHit(url=url, title=title)


def test_boards_whose_results_read_like_the_role_are_read_first_then_the_most_found() -> None:
    searches = [
        SearchCall(
            "Frontend Engineer Bengaluru",
            (
                _hit("https://job-boards.greenhouse.io/bigco", "Jobs at BigCo"),
                _hit(
                    "https://job-boards.greenhouse.io/bigco/jobs/1",
                    "Job Application for Platform Engineer at BigCo",
                ),
                _hit("https://jobs.lever.co/smallco/abc", "SmallCo - Frontend Engineer"),
                _hit("https://www.linkedin.com/jobs/view/9", "Frontend Engineer"),
            ),
        ),
        SearchCall(
            "SDE 2 frontend Bangalore",
            (_hit("https://job-boards.greenhouse.io/bigco/jobs/2", "Job at BigCo"),),
        ),
        SearchCall("broken search", error="too_many_requests"),
    ]
    relevance = {"SmallCo - Frontend Engineer": 2}
    leads = harvest(searches, lambda title: relevance.get(title, 0))
    assert [lead.ref.token for lead in leads] == ["smallco", "bigco"]
    small, big = leads
    assert (small.hits, small.relevance, small.name_hint, small.first_query) == (
        1,
        2,
        "SmallCo",
        "Frontend Engineer Bengaluru",
    )
    assert big.hits == 3 and big.queries == ["Frontend Engineer Bengaluru", "SDE 2 frontend Bangalore"]
    assert [lead.ref.token for lead in harvest(searches)] == [
        "bigco",
        "smallco",
    ]  # without a relevance: the most found


def test_one_board_is_one_lead_whatever_the_case_of_its_token() -> None:
    searches = [
        SearchCall("q", (_hit("https://jobs.lever.co/Pelago/1"), _hit("https://jobs.lever.co/pelago/2")))
    ]
    (lead,) = harvest(searches)
    assert lead.hits == 2 and lead.ref.key == ("lever", "pelago")
