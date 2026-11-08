"""The A1 runner over saved Greenhouse and Lever pages and a hand-made Greenhouse board scan.

`preview`: what is filled from where, the review rows (selector, source, group, order), the questions, the script and the
read-back digest; a posting that is gone, a login wall and a CAPTCHA stop it. `submit_approved`: the digest matches between
the preview and the replay; a changed form, a missing submit control or a refused marker means no click; the resume is
attached only after the marker and only once the guard is lifted; after the click only the success signature is "sent".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tayyari.apply.answers import BankEntry, FillContext, Resume, question_key
from tayyari.apply.forms import scan_form
from tayyari.apply.playbook import GREENHOUSE, LEVER
from tayyari.apply.runner import (
    BOT_CHECK_WARNING,
    ELIGIBILITY_GROUP,
    PERSONAL_GROUP,
    QUESTIONS_GROUP,
    SELF_ID_GROUP,
    SubmitResult,
    attach_name,
    group_of,
    preview,
    review_of,
    submit_approved,
)
from tayyari.apply.scan import LiveScan, ScanBlockers
from tayyari.contract.apply import ApplicationReview
from tests.fakes.apply_browser import FakeDriver

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"
GH = (DATA / "greenhouse_job.html").read_text(encoding="utf-8")
LEVER_PAGE = (DATA / "lever_apply.html").read_text(encoding="utf-8")
BOARD: dict[str, Any] = json.loads((DATA / "forms" / "greenhouse-react.json").read_text(encoding="utf-8"))
GH_URL = "https://job-boards.greenhouse.io/acme/jobs/4012345"
DETAILS = {"firstName": "Riya", "lastName": "Kapoor", "email": "riya@example.com", "phone": "+91 98765 43210",
           "linkedin": "https://linkedin.com/in/riya", "github": "https://github.com/riya", "location": "Pune, India",
           "noticePeriod": "30 days", "currentCompany": "Fintech Co"}  # fmt: skip
CV = Resume("Riya-Kapoor.pdf", b"%PDF-1.7 fake")
TYPED = {"question_102": "I build payment APIs and want to work on Acme's platform."}
NOW = "2026-11-08T10:00:00Z"


def ctx(**kw: Any) -> FillContext:
    """The person's answer bank (Your details, and India's sponsorship answer from the first check), plus ``bank``."""
    bank = {key: BankEntry(value) for key, value in DETAILS.items()}
    bank["needsSponsorship:IN"] = BankEntry("No", "first_check")
    bank.update(kw.pop("bank", {}))
    kw.setdefault("resume", CV)
    return FillContext(company="Acme", bank=bank, **kw)


class Marker:
    """The engine's ``mark_submitting``: records when it ran and what the driver had done by then."""

    def __init__(self, driver: FakeDriver, ok: bool = True) -> None:
        self.driver, self.ok, self.calls = driver, ok, 0
        self.lifted_before: list[frozenset[str]] = []

    async def __call__(self) -> bool:
        self.calls += 1
        self.lifted_before = list(self.driver.allow_calls)
        self.driver.actions.append(("mark", "", ""))
        return self.ok


async def no_sleep(_: float) -> None:
    return None


async def approved(page: Any = GH, url: str = GH_URL, **kw: Any) -> ApplicationReview:
    result = await preview(FakeDriver(page, url=url), GREENHOUSE, url, ctx(**kw), None)
    assert result.status == "ready", result.needs
    return review_of(result, captured_at=NOW)


async def send(
    review: ApplicationReview, driver: FakeDriver, *, ok: bool = True, resume: Resume = CV
) -> tuple[SubmitResult, Marker]:
    marker = Marker(driver, ok)
    result = await submit_approved(
        driver, review, ctx(typed=TYPED), url=GH_URL, playbook=GREENHOUSE, mark_submitting=marker, resume=resume,
        wait_s=0.0, attach_wait_s=0.0, sleep=no_sleep,
    )  # fmt: skip
    return result, marker


def ops(driver: FakeDriver) -> list[str]:
    return [op for op, _, _ in driver.actions]


class Drafts:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def answer(self, questions: Any, ctx: FillContext) -> dict[str, dict[str, Any]]:
        return self.rows


# ───────────────────────── preview ─────────────────────────


def test_the_form_scan_reads_labels_requirements_and_options() -> None:
    scan = scan_form(GH)
    by_key = {f.key: f for f in scan.fields}
    assert by_key["first_name"].label == "First Name" and by_key["first_name"].required
    assert by_key["question_103"].options == [("1", "Yes"), ("0", "No")]
    lever = {f.key: f for f in scan_form(LEVER_PAGE).fields}
    assert lever["cards[7a1b][field0]"].label == "What is your notice period?"


async def test_a_greenhouse_preview_carries_selector_source_group_and_order_and_asks_the_custom_question() -> (
    None
):
    driver = FakeDriver(GH, url=GH_URL)
    result = await preview(driver, GREENHOUSE, GH_URL, ctx(), None)
    assert result.status == "questions" and result.stop_reason == "questions"
    rows = {r.key: r for r in result.fields}
    first = rows["first_name"]
    assert (first.selector, first.source, first.group, first.order, first.value) == (
        '[id="first_name"]', "details", PERSONAL_GROUP, 0, "Riya",
    )  # fmt: skip
    sponsor = rows["question_103"]
    assert (sponsor.group, sponsor.value, sponsor.optionValue, sponsor.options) == (
        ELIGIBILITY_GROUP, "No", "0", ["Yes", "No"],
    )  # fmt: skip
    # Self-ID is never guessed: with "use on forms" off, the form's decline option is picked, as a default.
    assert (rows["gender"].source, rows["gender"].group) == ("default", SELF_ID_GROUP)
    orders = [r.order for r in result.fields]
    assert orders == sorted(o for o in orders if o is not None)
    assert [n.key for n in result.needs] == ["question_102"] and result.needs[0].requiredBy == "Acme"
    # The resume is planned beside the fields and never attached in the preview (Greenhouse uploads on attach).
    assert (
        "upload" not in ops(driver)
        and result.resume is not None
        and result.resume.selector == '[id="resume"]'
    )
    assert {s.op for s in result.script} <= {"fill", "select", "check", "choose", "group"}  # never an upload
    assert {s.selector for s in result.script} == {r.selector for r in result.fields}
    assert BOT_CHECK_WARNING in result.warnings and result.screenshot and result.digest
    assert result.mode == "playbook" and result.model is None and driver.allow_calls == []
    assert not driver.submitted and driver.sent_writes() == []


async def test_a_typed_answer_completes_the_review_and_the_read_back_digest_is_stable() -> None:
    first = await preview(FakeDriver(GH, url=GH_URL), GREENHOUSE, GH_URL, ctx(typed=TYPED), None)
    again = await preview(FakeDriver(GH, url=GH_URL), GREENHOUSE, GH_URL, ctx(typed=TYPED), None)
    assert first.status == "ready" and first.digest == again.digest
    assert next(r for r in first.fields if r.key == "question_102").group == QUESTIONS_GROUP
    other = await preview(
        FakeDriver(GH, url=GH_URL), GREENHOUSE, GH_URL, ctx(typed={"question_102": "Other"}), None
    )
    assert other.digest != first.digest
    edited = ctx(typed=TYPED, resume=Resume("Riya-Kapoor.pdf", b"%PDF v2"))
    edited_cv = await preview(FakeDriver(GH, url=GH_URL), GREENHOUSE, GH_URL, edited, None)
    assert edited_cv.digest != first.digest  # the resume's sha256 is signed too


async def test_kept_answers_and_cited_model_answers_are_used_with_their_facts() -> None:
    kept = {question_key("Why do you want to work at Acme?"): BankEntry("Kept answer", "form")}
    result = await preview(FakeDriver(GH, url=GH_URL), GREENHOUSE, GH_URL, ctx(bank=kept), None)
    assert result.status == "ready"
    assert any(r.source == "answers" and r.value == "Kept answer" for r in result.fields)

    facts = [{"id": "f1", "text": "Built a payments API serving 2 million users at Fintech Co."}]
    drafts = Drafts({"question_102": {"answer": "I built a payments API for 2 million users.", "factIds": ["f1"],
                                      "confidence": "high"}})  # fmt: skip
    result = await preview(FakeDriver(GH, url=GH_URL), GREENHOUSE, GH_URL, ctx(facts=facts), drafts)
    row = next(r for r in result.fields if r.key == "question_102")
    assert result.status == "ready" and row.source == "model" and row.factIds == ["f1"]
    assert [(f.id, f.text) for f in result.facts] == [("f1", facts[0]["text"])]
    invented = Drafts(
        {"question_102": {"answer": "I led 40 engineers.", "factIds": ["f1"], "confidence": "high"}}
    )
    result = await preview(FakeDriver(GH, url=GH_URL), GREENHOUSE, GH_URL, ctx(facts=facts), invented)
    assert result.status == "questions" and result.needs[0].suggestion is None


async def test_lever_builds_the_full_name_and_fills_the_notice_period() -> None:
    url = "https://jobs.lever.co/acme/x/apply"
    driver = FakeDriver(LEVER_PAGE, url=url)
    result = await preview(driver, LEVER, url, ctx(), None)
    assert result.status == "ready", result.needs
    sent = {(op, sel): val for op, sel, val in driver.actions}
    assert sent[("fill", '[name="name"]')] == "Riya Kapoor" and sent[("fill", '[name="org"]')] == "Fintech Co"
    assert sent[("fill", '[name="cards[7a1b][field0]"]')] == "30 days"
    assert sent[("select_label", '[name="eeo[gender]"]')] == "Decline to self-identify"


async def test_a_board_with_comboboxes_is_chosen_and_the_consent_tick_is_left_to_the_person() -> None:
    driver = FakeDriver(BOARD, url=BOARD["url"])
    result = await preview(driver, GREENHOUSE, BOARD["url"], ctx(), None)
    sent = {(op, sel): val for op, sel, val in driver.actions}
    assert sent[("choose", '[id="country"]')] == "India"  # the phone's dial code picks the country
    assert sent[("choose", '[id="candidate-location"]')] == "Pune, India"
    assert sent[("choose", '[id="question_1002"]')] == "No"
    assert sent[("choose", '[id="gender"]')] == "Decline To Self Identify"
    assert "check" not in ops(driver)
    (need,) = result.needs
    assert need.key == "question_1003[]_1" and need.perOffer and result.status == "questions"
    steps = {s.selector: s.op for s in result.script}
    assert steps['[id="country"]'] == "choose" and steps['[id="first_name"]'] == "fill"


class FlakyDriver(FakeDriver):
    """A combobox that keeps nothing on the first pick, or never."""

    def __init__(self, *args: Any, never: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.never = never
        self.tries: dict[str, int] = {}

    async def choose(self, selector: str, text: str, frame: int = 0) -> None:
        self.tries[selector] = self.tries.get(selector, 0) + 1
        self.actions.append(("choose", selector, text))
        if self.never or self.tries[selector] == 1:
            return  # the pick "worked", but the widget kept nothing
        self.values[selector] = text


async def test_a_value_that_did_not_stick_is_retried_once_then_asked() -> None:
    once = FlakyDriver(BOARD, url=BOARD["url"])
    result = await preview(once, GREENHOUSE, BOARD["url"], ctx(), None)
    assert once.tries['[id="candidate-location"]'] == 2
    assert any(r.key == "candidate-location" and r.value == "Pune, India" for r in result.fields)
    never = FlakyDriver(BOARD, url=BOARD["url"], never=True)
    result = await preview(never, GREENHOUSE, BOARD["url"], ctx(), None)
    assert never.tries['[id="candidate-location"]'] == 2  # one retry, not more
    need = next(n for n in result.needs if n.key == "candidate-location")
    assert "did not keep" in need.reason and need.questionKey == "location"
    assert not any(r.key == "candidate-location" for r in result.fields)


async def test_a_gone_posting_a_login_wall_and_a_captcha_stop_the_preview() -> None:
    gone = await preview(FakeDriver(GH, url=GH_URL, status=404), GREENHOUSE, GH_URL, ctx(), None)
    assert gone.status == "closed" and gone.reason == "gone"
    closed_page = (
        "<html><body><h1>Backend Engineer</h1><p>We are no longer accepting applications.</p></body></html>"
    )
    closed = await preview(FakeDriver(closed_page, url=GH_URL), GREENHOUSE, GH_URL, ctx(), None)
    assert closed.status == "closed" and closed.reason == "not_accepting"
    wall = FakeDriver('<form><input type="password" name="pw"></form>')
    login = await preview(wall, GREENHOUSE, GH_URL, ctx(), None)
    assert login.status == "needs_signin" and login.stop_reason == "signin"
    captcha = GH.replace('class="g-recaptcha" data-size="invisible"', 'class="h-captcha"')
    challenge = await preview(FakeDriver(captcha, url=GH_URL), GREENHOUSE, GH_URL, ctx(), None)
    assert (
        challenge.status == "needs_signin" and challenge.stop_reason == "challenge" and challenge.screenshot
    )
    empty = FakeDriver("<html><body><p>Hello</p></body></html>")
    nothing = await preview(empty, GREENHOUSE, GH_URL, ctx(), None)
    assert nothing.status == "failed" and nothing.reason == "no_form"


async def test_a_form_behind_the_playbooks_open_text_is_opened() -> None:
    posting = '<html><body><h1>Backend Engineer</h1><a href="#">Apply for this job</a></body></html>'
    driver = FakeDriver(posting, url=GH_URL, reveals={"Apply for this job": GH})
    result = await preview(driver, GREENHOUSE, GH_URL, ctx(typed=TYPED), None)
    assert driver.clicked_texts == ["Apply for this job"] and result.status == "ready"


def test_groups_follow_the_key_that_placed_the_field() -> None:
    assert group_of("email") == PERSONAL_GROUP and group_of("workAuthorization") == ELIGIBILITY_GROUP
    assert group_of("eeoGender") == SELF_ID_GROUP and group_of(None) == QUESTIONS_GROUP
    assert group_of("signature") == "Declaration" and group_of("expectedCtc") == QUESTIONS_GROUP


def test_a_generic_resume_name_is_attached_under_the_persons_name() -> None:
    assert attach_name(CV, ctx()) == "Riya-Kapoor.pdf"
    assert attach_name(Resume("resume.pdf", b"x"), ctx()) == "Riya-Kapoor-Resume.pdf"
    assert attach_name(Resume("CV.docx", b"x"), FillContext()) is None
    only_full = FillContext(bank={"fullName": BankEntry("Riya Kapoor")})
    assert attach_name(Resume("resume.pdf", b"x"), only_full) == "Riya-Kapoor-Resume.pdf"


# ───────────────────────── submit ─────────────────────────


async def test_the_digest_matches_between_the_preview_and_the_replay_and_success_is_sent() -> None:
    review = await approved(typed=TYPED)
    driver = FakeDriver(GH, url=GH_URL)
    result, marker = await send(review, driver)
    assert result.status == "sent" and result.clicked and driver.submitted and result.screenshot
    assert marker.calls == 1 and marker.lifted_before == []  # the guard was lifted only after the marker
    assert driver.allow_calls == [frozenset({"boards.greenhouse.io", "job-boards.greenhouse.io"})]
    order = ops(driver)
    assert order.index("mark") < order.index("upload") < order.index("click_submit")
    assert ("upload", '[id="resume"]', "Riya-Kapoor.pdf") in driver.actions
    assert "check" not in order and "click_text" not in order  # the replay ran the script, nothing else


async def test_a_changed_form_goes_back_to_the_person_and_nothing_is_clicked() -> None:
    review = await approved(typed=TYPED)
    extra = GH.replace(
        '<div class="g-recaptcha"',
        '<div class="field"><label for="question_104">Years of SQL<span class="required">*</span></label>'
        '<input id="question_104" name="question_104" type="text" aria-required="true"></div><div class="g-recaptcha"',
    )
    driver = FakeDriver(extra, url=GH_URL)
    result, marker = await send(review, driver)
    assert result.status == "form_changed" and not result.clicked and not driver.submitted
    assert marker.calls == 0 and driver.allow_calls == [] and "upload" not in ops(driver)
    assert result.review is not None and result.review.digest not in (None, review.digest)
    assert [n.key for n in result.review.needs] == ["question_104"]
    stored = review_of(result.review, captured_at=NOW)
    assert stored.digest == result.review.digest and any(r.key == "first_name" for r in stored.fields)
    # A different resume file is a changed form too: its sha256 is in the digest.
    other = FakeDriver(GH, url=GH_URL)
    changed, _ = await send(review, other, resume=Resume("Riya-Kapoor.pdf", b"%PDF edited"))
    assert changed.status == "form_changed" and not other.submitted


async def test_a_missing_submit_control_is_failed_before_any_click() -> None:
    review = await approved(typed=TYPED)
    no_button = GH.replace('<button type="submit" class="btn btn--pill">Submit application</button>', "")
    driver = FakeDriver(no_button, url=GH_URL)
    result, marker = await send(review, driver)
    assert (result.status, result.reason, result.clicked) == ("failed", "submit_missing", False)
    assert marker.calls == 0 and not driver.submitted and "upload" not in ops(driver)


async def test_a_refused_marker_means_no_attach_and_no_click() -> None:
    review = await approved(typed=TYPED)
    driver = FakeDriver(GH, url=GH_URL)
    result, marker = await send(review, driver, ok=False)
    assert (result.status, result.reason, result.clicked) == ("failed", "not_marked", False)
    assert marker.calls == 1 and driver.allow_calls == [] and "upload" not in ops(driver)
    assert not driver.submitted


class ShownEarly(FakeDriver):
    """A page that shows the resume's file name before anything was attached."""

    async def scan(self, resume_name: str = "") -> LiveScan:
        found = await super().scan(resume_name)
        if resume_name:
            found.blockers = ScanBlockers(resume_name_on_page=True)
        return found


async def test_the_resume_flag_must_go_from_false_to_true_across_the_attach() -> None:
    review = await approved(typed=TYPED)
    hidden = FakeDriver(GH, url=GH_URL, resume_shows=False)
    result, marker = await send(review, hidden)
    assert (result.status, result.reason, result.clicked) == ("failed", "resume_not_attached", False)
    assert marker.calls == 1 and "upload" in ops(hidden) and not hidden.submitted
    early = ShownEarly(GH, url=GH_URL)
    result, marker = await send(review, early)
    assert (result.status, result.reason, result.clicked) == ("failed", "resume_check", False)
    assert marker.calls == 0 and "upload" not in ops(early)


async def test_no_confirmation_a_code_prompt_an_error_or_a_dropped_click_is_check_this_one() -> None:
    review = await approved(typed=TYPED)
    quiet = "<html><body><p>Working on it</p></body></html>"
    nothing = FakeDriver(GH, url=GH_URL, after_submit=quiet, after_submit_url=GH_URL)
    result, _ = await send(review, nothing)
    assert (result.status, result.reason, result.clicked) == ("check_this_one", "no_confirmation", True)
    code_page = ('<html><body><form><label for="code">Enter the verification code we emailed you</label>'
                 '<input id="code" name="code" aria-required="true"></form></body></html>')  # fmt: skip
    code = FakeDriver(GH, url=GH_URL, after_submit=code_page, after_submit_url=GH_URL + "/verify")
    result, _ = await send(review, code)
    assert (result.status, result.reason, result.clicked) == ("check_this_one", "verification_code", True)
    oops = "<html><body><p>There were errors: Email is required</p></body></html>"
    error = FakeDriver(GH, url=GH_URL, after_submit=oops, after_submit_url=GH_URL)
    result, _ = await send(review, error)
    assert (result.status, result.reason, result.clicked) == ("check_this_one", "form_error", True)
    dropped = FakeDriver(GH, url=GH_URL, raise_after_click=ConnectionResetError("reset"))
    result, _ = await send(review, dropped)
    assert (result.status, result.reason, result.clicked) == ("check_this_one", "click_error", True)
    assert dropped.submitted  # it may well have gone: never retried, the person checks it


async def test_a_lever_review_replays_by_name_and_is_sent() -> None:
    url = "https://jobs.lever.co/acme/0f8f8b3c/apply"
    first = await preview(FakeDriver(LEVER_PAGE, url=url), LEVER, url, ctx(), None)
    review = review_of(first, captured_at=NOW)
    driver = FakeDriver(LEVER_PAGE, url=url)
    marker = Marker(driver)
    result = await submit_approved(
        driver, review, ctx(), url=url, playbook=LEVER, mark_submitting=marker, wait_s=0.0, attach_wait_s=0.0,
        sleep=no_sleep,
    )  # fmt: skip
    assert result.status == "sent" and driver.allow_calls == [
        frozenset({"jobs.lever.co", "jobs.eu.lever.co"})
    ]
    assert ("upload", '[id="resume-upload-input"]', "Riya-Kapoor.pdf") in driver.actions


async def test_a_form_without_a_resume_input_signs_no_resume_and_attaches_nothing() -> None:
    page = GH.replace(
        '<div class="field"><label for="resume">Resume/CV<span class="required">*</span></label><input id="resume" '
        'name="resume" type="file" accept=".pdf,.doc,.docx,.txt,.rtf" aria-required="true"></div>',
        "",
    )
    assert 'id="resume"' not in page
    review = await approved(page, typed=TYPED)
    assert review.resume is None
    driver = FakeDriver(page, url=GH_URL)
    result, marker = await send(
        review, driver
    )  # a saved resume is at hand, but there is nowhere to attach it
    assert result.status == "sent" and marker.calls == 1 and "upload" not in ops(driver)


class ParsesOnAttach(FakeDriver):
    """A resume parser: attaching the file rewrites the first name, as Lever's and Ashby's widgets may."""

    async def upload(self, selector: str, name: str, data: bytes, media_type: str, frame: int = 0) -> None:
        await super().upload(selector, name, data, media_type, frame)
        self.values['[id="first_name"]'] = "R. Kapoor"


async def test_the_digest_is_read_back_again_after_the_attach_and_a_change_is_never_clicked() -> None:
    review = await approved(typed=TYPED)
    driver = ParsesOnAttach(GH, url=GH_URL)
    result, marker = await send(review, driver)
    assert (result.status, result.reason, result.clicked) == ("failed", "form_changed_after_attach", False)
    assert marker.calls == 1 and "upload" in ops(driver) and "click_submit" not in ops(driver)
    assert not driver.submitted


async def test_new_fields_on_a_changed_form_follow_the_planners_rules() -> None:
    review = await approved(typed=TYPED)
    extra = GH.replace(
        '<div class="g-recaptcha"',
        '<div class="field"><label for="question_201">Aadhaar number<span class="required">*</span></label>'
        '<input id="question_201" name="question_201" type="text" aria-required="true"></div>'
        '<div class="field"><label for="question_202">Are you legally authorized to work in Germany?'
        '<span class="required">*</span></label><select id="question_202" name="question_202" aria-required="true">'
        '<option value="">Select...</option><option value="1">Yes</option><option value="0">No</option></select></div>'
        '<div class="field"><label for="veteran">Veteran status</label><select id="veteran" name="veteran">'
        '<option value="">Select...</option><option value="1">I am a veteran</option>'
        '<option value="2">Decline to self-identify</option></select></div><div class="g-recaptcha"',
    )
    driver = FakeDriver(extra, url=GH_URL)
    result, marker = await send(review, driver)
    assert result.status == "form_changed" and not result.clicked and marker.calls == 0
    assert result.review is not None
    needs = {n.key: n for n in result.review.needs}
    assert needs["question_201"].onSite is True and not needs["question_201"].perOffer
    assert needs["question_202"].questionKey == "workAuthorization:DE"
    veteran = next(r for r in result.review.fields if r.key == "veteran")
    assert (veteran.value, veteran.source) == ("Decline to self-identify", "default")
    assert any(s.selector == '[id="veteran"]' for s in result.review.script)
    # The new review's digest is the form as it now reads, the decline filled in
    assert driver.values.get('[id="veteran"]') == "Decline to self-identify"


async def test_a_playbook_with_no_submit_hosts_never_marks_or_clicks() -> None:
    review = await approved(typed=TYPED)
    driver = FakeDriver(GH, url=GH_URL)
    marker = Marker(driver)
    bare = GREENHOUSE.model_copy(update={"submit_hosts": [], "upload_hosts": []})
    result = await submit_approved(
        driver, review, ctx(typed=TYPED), url=GH_URL, playbook=bare, mark_submitting=marker, resume=CV,
        wait_s=0.0, attach_wait_s=0.0, sleep=no_sleep,
    )  # fmt: skip
    assert (result.status, result.reason, result.clicked) == ("failed", "no_submit_hosts", False)
    assert marker.calls == 0 and driver.allow_calls == [] and not driver.submitted


class GuardFails(FakeDriver):
    def allow_submit_hosts(self, hosts: Any) -> None:
        raise RuntimeError("guard")


async def test_an_error_between_the_marker_and_the_click_is_failed_and_not_clicked() -> None:
    review = await approved(typed=TYPED)
    driver = GuardFails(GH, url=GH_URL)
    result, marker = await send(review, driver)
    assert (result.status, result.reason, result.clicked) == ("failed", "pre_click_error", False)
    assert marker.calls == 1 and "upload" not in ops(driver) and not driver.submitted


async def test_a_value_the_page_set_itself_is_shown_on_its_skipped_row() -> None:
    page = GH.replace(
        '<div class="g-recaptcha"',
        '<div class="field"><label for="alerts">Send me job alerts</label>'
        '<input id="alerts" name="alerts" type="checkbox"></div><div class="g-recaptcha"',
    )
    driver = FakeDriver(page, url=GH_URL)
    driver.values['[id="alerts"]'] = "checked"  # the page ticks it by default
    result = await preview(driver, GREENHOUSE, GH_URL, ctx(typed=TYPED), None)
    alerts = next(s for s in result.skipped if s.key == "alerts")
    assert alerts.value == "checked"
