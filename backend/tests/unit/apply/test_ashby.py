"""Ashby's built-in playbook on a saved page, through the new preview: the fields are filled from the person's details, and
the resume is planned beside them but never attached (Ashby uploads it through its API the moment it is chosen)."""

from __future__ import annotations

from pathlib import Path

from tayyari.apply.answers import BankEntry, FillContext, Resume
from tayyari.apply.playbook import ASHBY
from tayyari.apply.runner import PERSONAL_GROUP, preview, review_of
from tests.fakes.apply_browser import FakeDriver

ASHBY_PAGE = (Path(__file__).resolve().parents[2] / "data" / "apply" / "ashby_application.html").read_text(
    encoding="utf-8"
)
URL = "https://jobs.ashbyhq.com/acme/0a1b2c3d/application"
DETAILS = {"firstName": "Riya", "lastName": "Kapoor", "email": "riya@example.com", "phone": "+91 98765 43210",
           "linkedin": "https://linkedin.com/in/riya"}  # fmt: skip


async def test_an_ashby_preview_fills_the_details_and_leaves_the_resume_for_the_submit() -> None:
    driver = FakeDriver(ASHBY_PAGE, url=URL)
    bank = {key: BankEntry(value) for key, value in DETAILS.items()}
    ctx = FillContext(bank=bank, resume=Resume("Riya-Kapoor-CV.pdf", b"%PDF-1.7 fake"))
    result = await preview(driver, ASHBY, URL, ctx, None)
    assert result.status == "ready" and result.submit_label == "Submit Application"
    sent = {(op, sel): val for op, sel, val in driver.actions}
    assert sent[("fill", '[id="_systemfield_name"]')] == "Riya Kapoor"
    assert sent[("fill", '[id="_systemfield_email"]')] == "riya@example.com"
    assert sent[("fill", '[id="9c1d2e3f-0000-4000-8000-000000000001"]')] == "+91 98765 43210"
    assert sent[("fill", '[id="9c1d2e3f-0000-4000-8000-000000000002"]')] == "https://linkedin.com/in/riya"
    # Never attached in the preview, never even reached for: the guard stays down and the file input stays empty.
    assert not any(op == "upload" for op, _, _ in driver.actions)
    assert driver.values.get('[id="_systemfield_resume"]') is None and driver.allow_calls == []
    assert driver.sent_writes() == []
    assert result.resume is not None
    assert (result.resume.name, result.resume.selector) == (
        "Riya-Kapoor-CV.pdf",
        '[id="_systemfield_resume"]',
    )
    assert all("resume" not in step.selector for step in result.script)  # no step touches the file input
    review = review_of(result, captured_at="2026-11-08T10:00:00Z", resume_choice="tailored")
    assert review.resume is not None and review.resume.choice == "tailored" and review.digest == result.digest
    name_row = next(r for r in review.fields if r.key == "_systemfield_name")
    assert name_row.group == PERSONAL_GROUP and name_row.selector == '[id="_systemfield_name"]'
