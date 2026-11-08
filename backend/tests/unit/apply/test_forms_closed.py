"""`forms.closed_of`: a page reads as closed only on a definite signal (404 or 410, or the employer's own words)."""

from __future__ import annotations

import pytest

from tayyari.apply.forms import closed_of


@pytest.mark.parametrize(
    ("text", "status", "reason"),
    [
        ("Not found", 404, "gone"),
        ("", 410, "gone"),
        ("Welcome! Apply below.", 404, "gone"),
        ("Sorry, this job is no longer accepting applications.", 200, "not_accepting"),
        ("ACME is no longer accepting applications for this role", 200, "not_accepting"),
        ("Thanks for your interest. This position has been filled.", 200, "filled"),
        ("The role has already been filled", None, "filled"),
        ("This job is no longer available.", 200, "unavailable"),
        ("The job you are looking for is no longer available", 200, "unavailable"),
        ("This posting is no longer open", 200, "unavailable"),
        ("Job no longer available", 200, "unavailable"),
    ],
)
def test_closed_pages(text: str, status: int | None, reason: str) -> None:
    assert closed_of(text, status) == reason


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("Senior Engineer. We are accepting applications until 30 November. Apply now.", 200),
        ("Please fill in the position you are applying for.", 200),
        ("Available positions: 12. Remote available.", 200),
        ("Your application will be reviewed once the job is filled in by you.", 200),
        ("", None),
        ("Server error", 500),
        ("Rate limited", 429),
        ("Forbidden", 403),  # unclear: never called closed
    ],
)
def test_open_or_unclear_pages(text: str, status: int | None) -> None:
    assert closed_of(text, status) is None
