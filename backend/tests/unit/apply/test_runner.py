"""The playbook runner against saved Greenhouse and Lever pages: what is filled from where, what goes to the person, that
nothing is submitted while filling, and that submit needs the approved digest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tayyari.apply.answers import FillContext, Resume, question_key
from tayyari.apply.forms import scan_form
from tayyari.apply.playbook import GREENHOUSE, LEVER
from tayyari.apply.runner import BOT_CHECK_WARNING, fill, submit
from tests.fakes.apply_browser import FakeDriver

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"
GH = (DATA / "greenhouse_job.html").read_text(encoding="utf-8")
LEVER_PAGE = (DATA / "lever_apply.html").read_text(encoding="utf-8")
DETAILS = {"firstName": "Riya", "lastName": "Kapoor", "email": "riya@example.com", "phone": "+91 98765 43210",
           "linkedin": "https://linkedin.com/in/riya", "github": "https://github.com/riya", "needsSponsorship": "No",
           "noticePeriod": "30 days", "currentCompany": "Fintech Co"}  # fmt: skip
CV = Resume("Riya-Kapoor.pdf", b"%PDF-1.7 fake")


def ctx(**kw: Any) -> FillContext:
    return FillContext(details=dict(DETAILS), resume=CV, **kw)


def test_the_form_scan_reads_labels_requirements_and_options() -> None:
    scan = scan_form(GH)
    by_key = {f.key: f for f in scan.fields}
    assert by_key["first_name"].label == "First Name" and by_key["first_name"].required
    assert by_key["phone"].required is False and by_key["resume"].kind == "file"
    assert by_key["question_103"].options == [("1", "Yes"), ("0", "No")]
    assert scan.submit_label == "Submit application"
    lever = {f.key: f for f in scan_form(LEVER_PAGE).fields}
    assert lever["name"].label == "Full name" and lever["name"].required
    assert lever["cards[7a1b][field0]"].label == "What is your notice period?"


async def test_greenhouse_fills_from_details_and_asks_the_custom_question() -> None:
    driver = FakeDriver(GH)
    result = await fill(driver, GREENHOUSE, "https://job-boards.greenhouse.io/acme/jobs/4012345", ctx(), None)
    assert result.status == "needs_user" and result.plan is not None
    sent = {(op, sel): val for op, sel, val in driver.actions}
    assert (
        sent[("fill", '[id="first_name"]')] == "Riya" and sent[("fill", '[id="email"]')] == "riya@example.com"
    )
    assert sent[("fill", '[id="question_101"]')] == "https://linkedin.com/in/riya"
    assert sent[("select", '[id="question_103"]')] == "0"  # "No" picked by its option value
    assert sent[("upload", '[id="resume"]')] == "Riya-Kapoor.pdf"
    assert ("select", '[id="gender"]') not in sent  # never guessed: no saved answer, optional
    assert [n.key for n in result.plan.needs] == ["question_102"]
    assert BOT_CHECK_WARNING in result.warnings and result.screenshot
    assert not driver.submitted and driver.blocked_submits == 0


async def test_a_typed_answer_completes_the_form_and_the_digest_is_stable() -> None:
    typed = {"question_102": "I build payment APIs and want to work on Acme's platform."}
    first = await fill(FakeDriver(GH), GREENHOUSE, "https://x/apply", ctx(typed=typed), None)
    again = await fill(FakeDriver(GH), GREENHOUSE, "https://x/apply", ctx(typed=typed), None)
    assert first.status == "ready" and first.plan is not None and again.plan is not None
    assert first.plan.digest() == again.plan.digest()
    changed = await fill(
        FakeDriver(GH), GREENHOUSE, "https://x/apply", ctx(typed={"question_102": "Other"}), None
    )
    assert changed.plan is not None and changed.plan.digest() != first.plan.digest()


async def test_kept_answers_are_reused() -> None:
    kept = {question_key("Why do you want to work at Acme?"): {"answer": "Kept answer"}}
    result = await fill(FakeDriver(GH), GREENHOUSE, "https://x/apply", ctx(kept=kept), None)
    assert result.status == "ready" and result.plan is not None
    assert any(p.source == "answers" and p.value == "Kept answer" for p in result.plan.filled)


async def test_lever_builds_the_full_name_and_fills_the_notice_period() -> None:
    driver = FakeDriver(LEVER_PAGE)
    result = await fill(driver, LEVER, "https://jobs.lever.co/acme/x/apply", ctx(), None)
    assert result.status == "ready", result.plan and result.plan.needs
    sent = {(op, sel): val for op, sel, val in driver.actions}
    assert sent[("fill", '[name="name"]')] == "Riya Kapoor" and sent[("fill", '[name="org"]')] == "Fintech Co"
    assert sent[("fill", '[name="cards[7a1b][field0]"]')] == "30 days"
    assert sent[("fill", '[name="urls[GitHub]"]')] == "https://github.com/riya"


async def test_a_captcha_or_login_wall_stops_the_run() -> None:
    blocked = await fill(
        FakeDriver(GH.replace('class="g-recaptcha" data-size="invisible"', 'class="h-captcha"')),
        GREENHOUSE,
        "https://x",
        ctx(),
        None,
    )
    assert blocked.status == "blocked" and blocked.reason == "captcha"
    login = await fill(
        FakeDriver('<form><input type="password" name="pw"></form>'), GREENHOUSE, "https://x", ctx(), None
    )
    assert login.status == "blocked" and login.reason == "login_wall"


class Drafts:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def answer(self, questions: Any, ctx: FillContext) -> dict[str, dict[str, Any]]:
        return self.rows


async def test_model_answers_must_cite_real_facts_and_invent_no_numbers() -> None:
    facts = [{"id": "f1", "text": "Built a payments API serving 2 million users at Fintech Co."}]
    good = Drafts(
        {
            "question_102": {
                "answer": "I built a payments API for 2 million users.",
                "factIds": ["f1"],
                "confidence": "high",
            }
        }
    )
    result = await fill(FakeDriver(GH), GREENHOUSE, "https://x", ctx(facts=facts), good)
    assert result.status == "ready" and result.plan is not None and result.plan.model_fields == 1
    invented = Drafts(
        {"question_102": {"answer": "I led 40 engineers.", "factIds": ["f1"], "confidence": "high"}}
    )
    result = await fill(FakeDriver(GH), GREENHOUSE, "https://x", ctx(facts=facts), invented)
    assert (
        result.status == "needs_user" and result.plan is not None and result.plan.needs[0].suggestion is None
    )
    uncited = Drafts({"question_102": {"answer": "I like Acme.", "factIds": ["nope"], "confidence": "high"}})
    result = await fill(FakeDriver(GH), GREENHOUSE, "https://x", ctx(facts=facts), uncited)
    assert result.status == "needs_user"


async def test_submit_needs_the_approved_digest() -> None:
    typed = {"question_102": "Because of the platform."}
    review = await fill(FakeDriver(GH), GREENHOUSE, "https://x", ctx(typed=typed), None)
    assert review.plan is not None
    wrong = FakeDriver(GH)
    refused = await submit(wrong, GREENHOUSE, "https://x", ctx(typed=typed), "0" * 64, wait_s=0.1)
    assert refused.status == "needs_user" and refused.reason == "form_changed" and not wrong.submitted
    right = FakeDriver(GH)
    done = await submit(right, GREENHOUSE, "https://x", ctx(typed=typed), review.plan.digest(), wait_s=0.1)
    assert done.status == "applied" and right.submitted
