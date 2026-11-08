"""The apply service with fakes (spec 3.1): every start check runs before any row or hold, one application per posting,
the shared answer bank and its fan-out, approve, cancel, resolve and "I applied there" moving stages by compare-and-set,
and Your details with the resume checked by its content. Nothing here fetches a page or calls a model."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tayyari.apply.service import (
    APPLY_REF_PREFIX,
    ApplyService,
    MatchInfo,
    StartRefused,
    fact_allowed,
    fact_sentence,
    id_shaped,
    own_email,
    sniff_resume,
)
from tayyari.billing import entitlements
from tayyari.contract.apply import (
    AnswerRequest,
    FirstCheckRequest,
    QuestionAnswer,
    SaveAnswerRequest,
    StartApplicationRequest,
    WorkAuthorization,
)
from tayyari.core.errors import BadRequest, Conflict, PayloadTooLarge, PaymentRequired, Unauthorized
from tayyari.core.settings import Settings
from tayyari.storage.ports import PostingTaken, StoredAnswer, StoredApplication
from tests.fakes.apply import FakeApplyRepo
from tests.fakes.clock import FakeClock

T0 = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
USER = "usr1"
OWNER = f"u:{USER}"
GH = "https://job-boards.greenhouse.io/acme/jobs/4012345"
GH_KEY = "greenhouse:acme:4012345"
LIVE_URL = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4012345"
MakeSettings = Callable[..., Settings]


# ───────────────────────── fakes ─────────────────────────


class Pages:
    """`PageFetcher` over saved pages; records every fetch."""

    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = pages or {}
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> tuple[str, str]:
        self.fetched.append(url)
        if url not in self.pages:
            raise RuntimeError("unreachable")
        return url, self.pages[url]


class Status:
    """`StatusFetcher`: the ATS read endpoint answers ``status`` (200 with the job's id by default)."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.fetched: list[str] = []

    async def fetch(self, url: str, accept: str) -> tuple[int, str, str]:
        self.fetched.append(url)
        return self.status, url, json.dumps({"id": 4012345}) if self.status == 200 else "{}"


class Credits:
    """`ApplyCredits` with a ledger in memory."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.state: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    async def hold(self, user_id: str, ref: str) -> None:
        self.calls.append(("hold", ref))
        if self.refuse:
            raise PaymentRequired("no_credits")
        self.state[ref] = "held"

    async def capture(self, user_id: str, ref: str) -> bool:
        self.calls.append(("capture", ref))
        if self.state.get(ref) in ("held", "charged"):
            self.state[ref] = "charged"
            return True
        return False

    async def release(self, user_id: str, ref: str) -> bool:
        self.calls.append(("release", ref))
        if self.state.get(ref) == "held":
            self.state[ref] = "released"
            return True
        return False

    @property
    def live(self) -> list[str]:
        return [ref for ref, state in self.state.items() if state == "held"]


class Facts:
    def __init__(self) -> None:
        self.written: list[tuple[str, str, str, str]] = []

    async def __call__(self, user_id: str, profile_id: str, question_key: str, sentence: str) -> None:
        self.written.append((user_id, profile_id, question_key, sentence))

    async def retract(self, user_id: str, profile_id: str, question_key: str) -> None:
        self.written.append((user_id, profile_id, question_key, ""))


class Rig:
    def __init__(
        self,
        settings: Settings,
        *,
        status: int = 200,
        refuse: bool = False,
        pages: dict[str, str] | None = None,
    ):
        self.repo = FakeApplyRepo()
        self.pages = Pages(pages)
        self.status = Status(status)
        self.credits = Credits(refuse=refuse)
        self.facts = Facts()
        self.clock = FakeClock(T0)
        self.svc = ApplyService(
            self.repo, settings, self.clock, self.pages, self.status, self.credits, self.facts
        )

    async def stage(self, app_id: str) -> str:
        found = await self.repo.get_application(app_id, OWNER)
        assert found is not None
        return found.stage

    async def get(self, app_id: str) -> StoredApplication:
        found = await self.repo.get_application(app_id, OWNER)
        assert found is not None
        return found


@pytest.fixture
def rig(make_settings: MakeSettings) -> Rig:
    return Rig(make_settings())


def match(url: str | None = GH, **changes: Any) -> MatchInfo:
    base = MatchInfo(
        match_id="m1", profile_id="p1", user_id=USER, title="Backend Engineer", company="Acme",
        location="Bengaluru, India", posting_url=url, description="Build APIs.",
    )  # fmt: skip
    return replace(base, **changes)


def start(resume: str = "saved") -> StartApplicationRequest:
    return StartApplicationRequest(matchId="m1", resume=resume)  # type: ignore[arg-type]


def need(field: str, key: str, *, required: bool = True, **extra: Any) -> dict[str, Any]:
    return {"key": field, "label": field.replace("_", " "), "kind": "text", "required": required,
            "reason": "A question only you can answer.", "questionKey": key, **extra}  # fmt: skip


def stored(app_id: str, stage: str, **extra: Any) -> StoredApplication:
    values: dict[str, Any] = {
        "id": app_id, "owner_key": OWNER, "user_id": USER, "source": "match", "job_url": GH,
        "domain": "job-boards.greenhouse.io", "ats": "greenhouse", "stage": stage, "created_at": T0, "updated_at": T0,
        "profile_id": "p1", "company": f"Co {app_id}",
    }  # fmt: skip
    values.update(extra)
    return StoredApplication(**values)


# ───────────────────────── the start ─────────────────────────


async def test_a_start_creates_a_queued_row_with_a_hold(rig: Rig) -> None:
    detail = await rig.svc.create(OWNER, start("tailored"), match())
    assert (detail.stage, detail.ats, detail.company, detail.resumeChoice) == (
        "queued",
        "greenhouse",
        "Acme",
        "tailored",
    )
    assert detail.creditState == "held"
    app = await rig.get(detail.id)
    assert app.posting_key == GH_KEY and app.credit_ref == f"apply:{app.id}" and app.match_id == "m1"
    assert rig.credits.live == [f"apply:{app.id}"]
    assert rig.status.fetched == [LIVE_URL] and rig.pages.fetched == []  # the form's address alone was enough


def test_the_ref_prefix_is_billings() -> None:
    assert entitlements.APPLY_REF_PREFIX == APPLY_REF_PREFIX


@pytest.mark.parametrize(
    ("url", "reason", "status"),
    [
        ("https://www.linkedin.com/jobs/view/1", "manual_only", 200),
        ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/123", "own_account_ats", 200),
        (None, "not_fillable", 200),
        (GH, "closed", 404),
        (GH, "unknown_liveness", 503),
    ],
)
async def test_each_failed_check_creates_no_row_and_no_hold(
    make_settings: MakeSettings, url: str | None, reason: str, status: int
) -> None:
    rig = Rig(make_settings(), status=status)
    with pytest.raises(StartRefused) as refused:
        await rig.svc.create(OWNER, start(), match(url))
    assert refused.value.check.reason == reason and refused.value.status == 409
    assert rig.repo.applications == {} and rig.credits.calls == []
    if reason in ("manual_only", "own_account_ats", "not_fillable"):
        assert rig.status.fetched == [] and rig.pages.fetched == []  # never fetched


async def test_a_blocked_match_is_refused_with_its_gate_questions(rig: Rig) -> None:
    blocked = match(blocked=True, gate_reasons=("Are you authorised to work in India?",))
    with pytest.raises(StartRefused) as refused:
        await rig.svc.create(OWNER, start(), blocked)
    assert refused.value.check.reason == "blocked_by_requirement"
    assert refused.value.check.gateReasons == ["Are you authorised to work in India?"]
    assert rig.repo.applications == {} and rig.credits.calls == []


async def test_no_credit_creates_no_row(make_settings: MakeSettings) -> None:
    rig = Rig(make_settings(), refuse=True)
    with pytest.raises(PaymentRequired):
        await rig.svc.create(OWNER, start(), match())
    assert rig.repo.applications == {} and rig.credits.live == []


async def test_a_daily_cap_is_checked_before_the_hold(make_settings: MakeSettings) -> None:
    rig = Rig(make_settings(apply={"max_per_day": 1}))
    await rig.repo.create_application(stored("old", "sent", posting_key="other"))
    from tayyari.core.errors import RateLimited

    with pytest.raises(RateLimited):
        await rig.svc.create(OWNER, start(), match())
    assert len(rig.repo.applications) == 1 and rig.credits.calls == []


async def test_a_duplicate_gives_the_existing_application(rig: Rig) -> None:
    await rig.repo.create_application(stored("ap0", "sent", posting_key=GH_KEY))
    with pytest.raises(StartRefused) as refused:
        await rig.svc.create(OWNER, start(), match())
    assert refused.value.check.reason == "already_applied" and refused.value.check.applicationId == "ap0"
    assert refused.value.to_body()["existingApplicationId"] == "ap0"
    assert len(rig.repo.applications) == 1 and rig.credits.calls == []


async def test_a_start_that_loses_the_race_gives_the_hold_back(rig: Rig) -> None:
    class Racy(FakeApplyRepo):
        """The duplicate lands between the check and the insert."""

        async def create_application(self, item: StoredApplication) -> None:
            await super().create_application(stored("winner", "queued", posting_key=item.posting_key))
            await super().create_application(item)

    rig.repo = Racy()
    rig.svc.repo = rig.repo
    with pytest.raises(PostingTaken) as taken:
        await rig.svc.create(OWNER, start(), match())
    assert taken.value.application_id == "winner"
    assert rig.credits.live == [] and ("release", rig.credits.calls[0][1]) in rig.credits.calls


async def test_a_start_reopens_a_cancelled_application(rig: Rig) -> None:
    await rig.repo.create_application(
        stored("ap0", "cancelled", posting_key=GH_KEY, review={"digest": "x"}, review_digest="x",
               credit_state="released", credit_ref="apply:ap0")
    )  # fmt: skip
    await rig.repo.put_screenshot("ap0", b"old")
    detail = await rig.svc.create(OWNER, start(), match())
    assert detail.id == "ap0" and detail.stage == "queued" and detail.creditState == "held"
    app = await rig.get("ap0")
    assert app.review is None and app.review_digest is None and not app.has_screenshot
    assert rig.credits.live == ["apply:ap0"]


async def test_an_unlimited_account_skips_the_hold(rig: Rig) -> None:
    detail = await rig.svc.create(OWNER, start(), match(), unlimited=True)
    assert detail.creditState is None and rig.credits.calls == []


async def test_only_an_account_may_start_and_only_from_its_own_match(rig: Rig) -> None:
    with pytest.raises(Unauthorized):
        await rig.svc.create("a:" + "5" * 40, start(), match())
    from tayyari.core.errors import NotFound

    with pytest.raises(NotFound):
        await rig.svc.create(OWNER, start(), match(user_id="someone-else"))
    assert rig.repo.applications == {}


async def test_the_check_has_no_side_effects(rig: Rig) -> None:
    check = await rig.svc.check(OWNER, match(), GH)
    assert check.offered and check.host == "job-boards.greenhouse.io" and check.ats == "greenhouse"
    assert rig.repo.applications == {} and rig.credits.calls == [] and rig.repo.events == []


async def test_a_pasted_link_must_be_the_same_posting(make_settings: MakeSettings) -> None:
    page = (
        '<script type="application/ld+json">{"@type": "JobPosting", "title": "Head of Sales", '
        '"hiringOrganization": {"name": "Acme"}}</script>'
    )
    rig = Rig(make_settings(), pages={GH: page})
    with pytest.raises(BadRequest):
        await rig.svc.check(OWNER, match(pasted=True), GH)
    same = page.replace("Head of Sales", "Backend Engineer")
    rig = Rig(make_settings(), pages={GH: same})
    assert (await rig.svc.check(OWNER, match(pasted=True), GH)).offered


# ───────────────────────── answers ─────────────────────────


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("q:abc", "ABCDE1234F"),
        ("q:abc", "2345 6789 0123"),
        ("pan", "anything"),
        ("aadhaar", "x"),
        ("q:x", "HDFC0001234"),
    ],
)
async def test_an_id_number_is_refused(rig: Rig, key: str, value: str) -> None:
    await rig.repo.create_application(stored("ap1", "waiting_for_you", needs=[need("f1", key)]))
    with pytest.raises(BadRequest):
        await rig.svc.answer(
            OWNER, "ap1", AnswerRequest(answers=[QuestionAnswer(questionKey=key, value=value, save=True)])
        )
    assert await rig.stage("ap1") == "waiting_for_you" and rig.repo.answers == {}


def test_id_shapes_spare_phone_numbers_and_ordinary_answers() -> None:
    assert not id_shaped("919876543210", "phone")
    assert id_shaped("919876543210", "q:x")
    assert not id_shaped("30 days") and not id_shaped("I built payment APIs for 3 years.")
    assert id_shaped("My PAN is abcde1234f")
    assert (
        id_shaped("Aadhaar: 2345 6789 0123")
        and id_shaped("Passport A1234567, valid")
        and id_shaped("hdfc0001234")
    )
    assert id_shaped("Account 123456789012 at my bank") and not id_shaped("Call me on 919876543210", "phone")


async def test_an_answer_requeues_every_other_waiting_application_that_asks_it(rig: Rig) -> None:
    ask = need("notice", "noticePeriod")
    await rig.repo.create_application(stored("ap1", "waiting_for_you", needs=[ask], created_at=T0))
    await rig.repo.create_application(
        stored("ap2", "waiting_for_you", needs=[ask], created_at=T0 + timedelta(minutes=1))
    )
    await rig.repo.create_application(
        stored("ap3", "waiting_for_you", needs=[ask], created_at=T0 + timedelta(minutes=2))
    )
    # asked per offer, typed on the employer's page, or not asked at all: left alone
    await rig.repo.create_application(
        stored("ap4", "waiting_for_you", needs=[need("n", "noticePeriod", perOffer=True)])
    )
    await rig.repo.create_application(stored("ap5", "waiting_for_you", needs=[need("x", "q:other")]))
    result = await rig.svc.answer(
        OWNER,
        "ap1",
        AnswerRequest(answers=[QuestionAnswer(questionKey="noticePeriod", value="30 days", save=True)]),
    )
    assert result.requeued == 2 and result.application.stage == "queued"
    assert [await rig.stage(a) for a in ("ap1", "ap2", "ap3", "ap4", "ap5")] == [
        "queued", "queued", "queued", "waiting_for_you", "waiting_for_you",
    ]  # fmt: skip
    assert (await rig.get("ap2")).job == {"typed": {"noticePeriod": "30 days"}}
    saved = rig.repo.answers[(USER, "noticePeriod")]
    assert (saved.value, saved.source, saved.answered_on) == ("30 days", "form", "Co ap1")
    assert rig.facts.written == [(USER, "p1", "noticePeriod", "My notice period is 30 days.")]


async def test_the_fan_out_reaches_every_waiting_form(make_settings: MakeSettings) -> None:
    """Spec 2.2: none is asked twice. The re-queue cap of an interrupted fill does not limit the fan-out."""
    rig = Rig(make_settings(apply={"requeue_cap": 1}))
    ask = need("notice", "noticePeriod")
    for i in range(6):
        await rig.repo.create_application(
            stored(f"ap{i}", "waiting_for_you", needs=[ask], created_at=T0 + timedelta(minutes=i))
        )
    result = await rig.svc.answer(
        OWNER,
        "ap0",
        AnswerRequest(answers=[QuestionAnswer(questionKey="noticePeriod", value="30 days", save=True)]),
    )
    assert result.requeued == 5
    assert {a.stage for a in rig.repo.applications.values()} == {"queued"}


async def test_an_unsaved_or_never_stored_answer_stays_on_its_form(rig: Rig) -> None:
    ask = need("bond", "bond")
    await rig.repo.create_application(
        stored("ap1", "waiting_for_you", needs=[ask, need("sig", "signature", perOffer=True)])
    )
    await rig.repo.create_application(stored("ap2", "waiting_for_you", needs=[ask]))
    result = await rig.svc.answer(OWNER, "ap1", AnswerRequest(answers=[
        QuestionAnswer(questionKey="bond", value="No bonds", save=False),
        QuestionAnswer(questionKey="signature", value="Riya Kapoor", save=True),
    ]))  # fmt: skip
    assert result.requeued == 0 and rig.repo.answers == {}
    typed = {"bond": "No bonds", "signature": "Riya Kapoor", "sig": "Riya Kapoor"}
    assert (await rig.get("ap1")).job == {"typed": typed}  # typed into this form only
    assert await rig.stage("ap2") == "waiting_for_you"


async def test_a_work_authorisation_answer_with_no_country_is_never_saved_or_shared(rig: Rig) -> None:
    unknown = need("auth", "q:1234", perOffer=True)
    await rig.repo.create_application(stored("ap1", "waiting_for_you", needs=[unknown]))
    await rig.repo.create_application(stored("ap2", "waiting_for_you", needs=[unknown]))
    result = await rig.svc.answer(
        OWNER, "ap1", AnswerRequest(answers=[QuestionAnswer(questionKey="q:1234", value="Yes", save=True)])
    )
    assert result.requeued == 0 and rig.repo.answers == {}
    with pytest.raises(BadRequest):
        await rig.svc.save_answer(
            OWNER, SaveAnswerRequest(questionKey="workAuthorization", askEachTime=False, value="Yes")
        )


async def test_the_email_must_be_the_accounts_own(rig: Rig) -> None:
    await rig.repo.create_application(stored("ap1", "waiting_for_you", needs=[need("email", "email")]))
    body = AnswerRequest(answers=[QuestionAnswer(questionKey="email", value="someone@else.com", save=True)])
    with pytest.raises(BadRequest):
        await rig.svc.answer(OWNER, "ap1", body, account_email="riya@example.com")
    ok = AnswerRequest(
        answers=[QuestionAnswer(questionKey="email", value="Riya+leafout@example.com", save=True)]
    )
    assert (
        await rig.svc.answer(OWNER, "ap1", ok, account_email="riya@example.com")
    ).application.stage == "queued"
    assert own_email("riya@example.com", "Riya@Example.com") and not own_email(
        "riya+@example.com", "riya@example.com"
    )


async def test_only_a_waiting_application_takes_answers(rig: Rig) -> None:
    await rig.repo.create_application(stored("ap1", "approved"))
    with pytest.raises(Conflict):
        await rig.svc.answer(
            OWNER, "ap1", AnswerRequest(answers=[QuestionAnswer(questionKey="q:1", value="x", save=False)])
        )


def test_facts_from_you_are_an_allowlist() -> None:
    assert fact_sentence("workAuthorization:IN", "Yes") == "I am authorised to work in India."
    assert (
        fact_sentence("needsSponsorship:US", "no")
        == "I do not need visa sponsorship to work in the United States."
    )
    assert fact_sentence("class12Score", "92%") == "I scored 92% in class 12."
    for key in (
        "currentCtc",
        "expectedCtc",
        "dob",
        "eeoGender",
        "pronouns",
        "pan",
        "workAuthorization",
        "q:abc",
    ):
        assert not fact_allowed(key) and fact_sentence(key, "x") is None


# ───────────────────────── approve, cancel, resolve ─────────────────────────


async def test_approve_needs_the_reviews_digest_and_no_open_question(rig: Rig) -> None:
    await rig.repo.create_application(stored("ap1", "waiting_for_you", review_digest="d" * 64))
    with pytest.raises(Conflict):
        await rig.svc.approve(OWNER, "ap1", "0" * 64)
    assert await rig.stage("ap1") == "waiting_for_you"
    on_site = need("pan", "pan", onSite=True)
    await rig.repo.create_application(
        stored("ap2", "waiting_for_you", review_digest="d" * 64, needs=[on_site])
    )
    with pytest.raises(Conflict) as refused:
        await rig.svc.approve(OWNER, "ap2", "d" * 64)
    assert "employer's page" in refused.value.user_message and await rig.stage("ap2") == "waiting_for_you"
    approved = await rig.svc.approve(OWNER, "ap1", "d" * 64)
    assert approved.stage == "approved" and approved.approvedAt
    assert (await rig.get("ap1")).approved_digest == "d" * 64


async def test_cancel_gives_the_hold_back(rig: Rig) -> None:
    await rig.repo.create_application(
        stored("ap1", "waiting_for_you", credit_state="held", credit_ref="apply:ap1")
    )
    rig.credits.state["apply:ap1"] = "held"
    detail = await rig.svc.cancel(OWNER, "ap1")
    assert detail.stage == "cancelled" and detail.creditState == "released" and rig.credits.live == []
    await rig.repo.create_application(stored("ap2", "submitting"))
    with pytest.raises(Conflict):
        await rig.svc.cancel(OWNER, "ap2")


async def test_resolve_sent_captures_and_not_sent_releases(rig: Rig) -> None:
    for app_id in ("ap1", "ap2"):
        await rig.repo.create_application(
            stored(
                app_id, "check_this_one", credit_state="held", credit_ref=f"apply:{app_id}", submitting_at=T0
            )
        )
        rig.credits.state[f"apply:{app_id}"] = "held"
    sent = await rig.svc.resolve(OWNER, "ap1", "sent")
    assert (sent.stage, sent.creditState, sent.submittedAt) == ("sent", "charged", T0.isoformat())
    assert rig.credits.state["apply:ap1"] == "charged"
    not_sent = await rig.svc.resolve(OWNER, "ap2", "not_sent")
    assert (not_sent.stage, not_sent.creditState) == ("failed", "released")
    assert rig.credits.state["apply:ap2"] == "released"
    with pytest.raises(Conflict):
        await rig.svc.resolve(OWNER, "ap1", "not_sent")  # already settled


async def test_i_applied_there_tracks_it_as_sent_without_a_charge(rig: Rig) -> None:
    await rig.repo.create_application(
        stored("ap1", "needs_signin", credit_state="held", credit_ref="apply:ap1")
    )
    rig.credits.state["apply:ap1"] = "held"
    detail = await rig.svc.applied_there(OWNER, "ap1")
    assert detail.stage == "sent" and rig.credits.state["apply:ap1"] == "released"
    await rig.repo.create_application(stored("ap2", "failed"))
    with pytest.raises(Conflict):
        await rig.svc.applied_there(OWNER, "ap2")


async def test_tracking_by_hand_only_from_sent_on(rig: Rig) -> None:
    await rig.repo.create_application(stored("ap1", "waiting_for_you"))
    with pytest.raises(BadRequest):
        await rig.svc.set_stage(OWNER, "ap1", "interviewing", None)
    await rig.repo.create_application(stored("ap2", "sent"))
    assert (await rig.svc.set_stage(OWNER, "ap2", "offer", "Signed!")).stage == "offer"


async def test_try_again_takes_a_new_hold_when_the_last_was_given_back(rig: Rig) -> None:
    await rig.repo.create_application(
        stored("ap1", "failed", credit_state="released", credit_ref="apply:ap1")
    )
    detail = await rig.svc.retry(OWNER, "ap1")
    assert detail.stage == "queued" and detail.creditState == "held" and rig.credits.live == ["apply:ap1"]


async def test_an_unclear_send_cannot_be_deleted(rig: Rig) -> None:
    await rig.repo.create_application(stored("ap1", "check_this_one"))
    with pytest.raises(Conflict):
        await rig.svc.delete(OWNER, "ap1")
    await rig.repo.create_application(stored("ap2", "queued", credit_state="held", credit_ref="apply:ap2"))
    rig.credits.state["apply:ap2"] = "held"
    await rig.svc.delete(OWNER, "ap2")
    assert "ap2" not in rig.repo.applications and rig.credits.live == []


async def test_deleting_a_sent_application_with_an_open_hold_charges_it(rig: Rig) -> None:
    """An email moved check_this_one -> sent (the tracker never captures); deleting it before the sweep never refunds
    an application that went to the employer."""
    await rig.repo.create_application(stored("ap1", "sent", credit_state="held", credit_ref="apply:ap1"))
    rig.credits.state["apply:ap1"] = "held"
    await rig.svc.delete(OWNER, "ap1")
    assert rig.credits.state["apply:ap1"] == "charged" and ("release", "apply:ap1") not in rig.credits.calls


class LosesOnce(FakeApplyRepo):
    """A repo where another click wins the first compare-and-set out of ``frm``: it lands just before ours."""

    def __init__(self, frm: str, winner: dict[str, Any]) -> None:
        super().__init__()
        self.frm, self.winner, self.raced = frm, winner, False

    async def update_application(self, application_id: str, changes: Any, *, now: datetime,
                                 expected_stage: str | None = None) -> StoredApplication | None:  # fmt: skip
        if not self.raced and expected_stage == self.frm:
            self.raced = True
            await super().update_application(
                application_id, self.winner, now=now, expected_stage=expected_stage
            )
        return await super().update_application(
            application_id, changes, now=now, expected_stage=expected_stage
        )


async def test_a_retry_that_loses_the_race_keeps_the_hold_the_winner_shares(rig: Rig) -> None:
    rig.repo = rig.svc.repo = LosesOnce(
        "failed", {"stage": "queued", "credit_state": "held", "credit_ref": "apply:ap1"}
    )
    await rig.repo.create_application(
        stored("ap1", "failed", credit_state="released", credit_ref="apply:ap1")
    )
    with pytest.raises(Conflict):
        await rig.svc.retry(OWNER, "ap1")
    assert rig.credits.live == ["apply:ap1"]  # the winner's row says held, and the ledger agrees


async def test_a_reopen_that_loses_the_race_keeps_the_hold_the_winner_shares(rig: Rig) -> None:
    winner = {"stage": "queued", "credit_state": "held", "credit_ref": "apply:ap0"}
    rig.repo = rig.svc.repo = LosesOnce("cancelled", winner)
    await rig.repo.create_application(
        stored("ap0", "cancelled", posting_key=GH_KEY, credit_state="released", credit_ref="apply:ap0")
    )
    with pytest.raises(Conflict):
        await rig.svc.create(OWNER, start(), match())
    assert isinstance(rig.repo, LosesOnce) and rig.repo.raced
    assert rig.credits.live == ["apply:ap0"]


async def test_finishing_the_tailored_resume_queues_the_form_again_and_saves_nothing(rig: Rig) -> None:
    ask = need("resume", "resume:tailored", kind="file", perOffer=True)
    await rig.repo.create_application(stored("ap1", "waiting_for_you", needs=[ask]))
    result = await rig.svc.answer(
        OWNER,
        "ap1",
        AnswerRequest(answers=[QuestionAnswer(questionKey="resume:tailored", value="done", save=True)]),
    )
    assert result.application.stage == "queued" and rig.repo.answers == {} and rig.facts.written == []


async def test_a_withdrawn_answer_takes_its_fact_with_it(rig: Rig) -> None:
    await rig.repo.create_application(stored("ap1", "sent"))
    await rig.repo.put_answer(
        StoredAnswer(user_id=USER, question_key="noticePeriod", question="Notice period", source="form",
                     ask_each_time=False, answered_at=T0, created_at=T0, updated_at=T0, value="30 days")
    )  # fmt: skip
    await rig.svc.delete_answer(OWNER, "noticePeriod")
    assert rig.facts.written == [(USER, "p1", "noticePeriod", "")]


async def test_a_need_shows_the_other_forms_that_ask_it(rig: Rig) -> None:
    ask = need("notice", "noticePeriod")
    await rig.repo.create_application(stored("ap1", "waiting_for_you", needs=[ask]))
    await rig.repo.create_application(stored("ap2", "waiting_for_you", needs=[ask], company="Hollis Health"))
    detail = await rig.svc.detail(OWNER, "ap1")
    (shown,) = detail.needs
    assert shown.alsoWaiting is not None and [a.company for a in shown.alsoWaiting] == ["Hollis Health"]


# ───────────────────────── Your details and the resume ─────────────────────────


def docx() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", "<w:document/>")
    return out.getvalue()


async def test_a_resume_is_checked_by_its_content(rig: Rig) -> None:
    with pytest.raises(BadRequest):
        await rig.svc.put_resume(OWNER, "cv.pdf", b"<html>not a pdf</html>")
    plain_zip = io.BytesIO()
    with zipfile.ZipFile(plain_zip, "w") as archive:
        archive.writestr("notes.txt", "hi")
    with pytest.raises(BadRequest):
        await rig.svc.put_resume(OWNER, "cv.docx", plain_zip.getvalue())
    with pytest.raises(PayloadTooLarge):
        await rig.svc.put_resume(OWNER, "cv.pdf", b"%PDF-1.7" + b"0" * (5 * 1024 * 1024))
    profile = await rig.svc.put_resume(OWNER, "My CV", docx())
    assert profile.hasResume and profile.resumeName == "My CV.docx"
    kept = await rig.repo.get_profile(OWNER)
    assert (
        kept is not None
        and kept.resume_type == sniff_resume(docx())
        and kept.resume_sha256
        and len(kept.resume_sha256) == 64
    )


async def test_your_details_lists_the_bank_what_is_not_asked_yet_and_stale_answers(rig: Rig) -> None:
    old = T0 - timedelta(days=120)
    for key, value in (("noticePeriod", "30 days"), ("currentCtc", "12"), ("workAuthorization:IN", "Yes")):
        await rig.repo.put_answer(StoredAnswer(user_id=USER, question_key=key, question=key, source="form",
                                               ask_each_time=False, answered_at=old, created_at=old, updated_at=old,
                                               value=value))  # fmt: skip
    details = await rig.svc.details(OWNER)
    by_key = {a.questionKey: a for a in details.answers}
    assert by_key["noticePeriod"].stale and by_key["noticePeriod"].value == "30 days"
    assert by_key["currentCtc"].private and by_key["currentCtc"].value is None
    assert by_key["workAuthorization:IN"].country == "IN"
    assert [w.country for w in details.workAuthorization] == ["IN"]
    asked = {q.questionKey for q in details.notAskedYet}
    assert "noticePeriod" not in asked and "languages" in asked
    assert "PAN" in details.neverStored and not details.firstCheckDone
    confirmed = await rig.svc.still_true(OWNER, "noticePeriod")
    assert not {a.questionKey: a for a in confirmed.answers}["noticePeriod"].stale


async def test_the_first_check_stores_india_only_and_the_safe_defaults(rig: Rig) -> None:
    with pytest.raises(BadRequest):
        await rig.svc.first_check(OWNER, FirstCheckRequest(
            skipped=False, workAuthorization=WorkAuthorization(country="US", authorised="Yes")))  # fmt: skip
    with pytest.raises(BadRequest):  # nor through its corrections
        await rig.svc.first_check(OWNER, FirstCheckRequest(
            skipped=False, edits=[QuestionAnswer(questionKey="workAuthorization:US", value="Yes", save=True)]))  # fmt: skip
    assert rig.repo.answers == {}
    lower = await rig.svc.save_answer(
        OWNER, SaveAnswerRequest(questionKey="workAuthorization:in", askEachTime=False, value="Yes")
    )
    assert "workAuthorization:IN" in {a.questionKey for a in lower.answers}
    rig.repo.answers.clear()
    details = await rig.svc.first_check(OWNER, FirstCheckRequest(
        skipped=False, noticePeriod="30 days",
        workAuthorization=WorkAuthorization(country="IN", authorised="Yes", needsSponsorship="No"),
    ))  # fmt: skip
    by_key = {a.questionKey: a for a in details.answers}
    assert by_key["noticePeriod"].source == "first_check" and by_key["needsSponsorship:IN"].value == "No"
    assert by_key["expectedCtc"].value == "Open to discussion" and by_key["expectedCtc"].source == "default"
    assert by_key["bond"].askEachTime and by_key["relocation"].askEachTime
    assert details.firstCheckDone


async def test_a_saved_answer_in_your_details_is_checked_like_any_other(rig: Rig) -> None:
    with pytest.raises(BadRequest):
        await rig.svc.save_answer(
            OWNER, SaveAnswerRequest(questionKey="aadhaar", askEachTime=False, value="x")
        )
    with pytest.raises(BadRequest):
        await rig.svc.save_answer(
            OWNER, SaveAnswerRequest(questionKey="q:x", askEachTime=False, value="ABCDE1234F")
        )
    details = await rig.svc.save_answer(OWNER, SaveAnswerRequest(questionKey="bond", askEachTime=True))
    assert {a.questionKey: a for a in details.answers}["bond"].askEachTime


async def test_consent_and_status(rig: Rig) -> None:
    before = await rig.svc.status(OWNER, enabled=True)
    assert before.signedIn and before.consentAt is None
    assert not {"anonymous", "agents"} & set(before.to_dict())  # the old branch's fields are gone
    after = await rig.svc.consent(OWNER)
    assert after.consentAt == T0.isoformat()


def test_a_row_the_sweep_has_claimed_still_reads_as_held() -> None:
    from tayyari.apply.service import summary_of
    from tayyari.storage.ports import StoredApplication

    now = datetime(2026, 11, 5, tzinfo=UTC)
    for claim in ("capturing", "releasing"):
        app = StoredApplication(
            id="ap9", owner_key=OWNER, user_id=USER, source="match", job_url="https://jobs.lever.co/acme/1",
            domain="jobs.lever.co", ats="lever", stage="cancelled", created_at=now, updated_at=now,
            credit_ref="apply:ap9", credit_state=claim,
        )  # fmt: skip
        assert summary_of(app).creditState == "held"


def test_the_tailored_resume_need_key_is_the_engines() -> None:
    from tayyari.apply import engine, service

    assert service.TAILORED_NEED_KEY == engine.TAILORED_NEED_KEY


# ───────────────────────── A1 review fixes ─────────────────────────


async def test_the_daily_cap_is_checked_before_any_page_or_ats_endpoint_is_read(
    make_settings: MakeSettings,
) -> None:
    from tayyari.core.errors import RateLimited

    rig = Rig(make_settings(apply={"max_per_day": 1}))
    await rig.repo.create_application(stored("old", "sent", posting_key="other"))
    with pytest.raises(RateLimited):
        await rig.svc.create(OWNER, start(), match(pasted=True))
    assert rig.pages.fetched == [] and rig.status.fetched == []


async def test_a_start_keys_the_site_on_the_employers_board(rig: Rig) -> None:
    """The per-site limit counts runs by ``domain``: on a shared ATS host it is the employer's board, so one Greenhouse
    employer never holds back another's runs."""
    detail = await rig.svc.create(OWNER, start(), match())
    assert (await rig.get(detail.id)).domain == "job-boards.greenhouse.io/acme"


class MarkerLandsFirst(FakeApplyRepo):
    """The worker's ``approved -> submitting`` compare-and-set commits between delete's read and its DELETE."""

    async def delete_application(
        self, application_id: str, owner_key: str, *, expected_stage: str | None = None
    ) -> bool:
        await super().update_application(
            application_id, {"stage": "submitting"}, now=T0, expected_stage="approved"
        )
        return await super().delete_application(application_id, owner_key, expected_stage=expected_stage)


async def test_deleting_an_approved_row_never_races_the_submitting_marker(rig: Rig) -> None:
    rig.repo = rig.svc.repo = MarkerLandsFirst()
    await rig.repo.create_application(stored("ap1", "approved", credit_state="held", credit_ref="apply:ap1"))
    rig.credits.state["apply:ap1"] = "held"
    with pytest.raises(Conflict):
        await rig.svc.delete(OWNER, "ap1")
    app = await rig.get("ap1")
    assert app.stage == "submitting" and rig.credits.live == [
        "apply:ap1"
    ]  # kept, and the hold not given back
    assert rig.credits.calls == []


async def test_a_delete_releases_the_hold_only_after_the_row_is_gone(rig: Rig) -> None:
    await rig.repo.create_application(stored("ap1", "approved", credit_state="held", credit_ref="apply:ap1"))
    rig.credits.state["apply:ap1"] = "held"
    await rig.svc.delete(OWNER, "ap1")
    assert "ap1" not in rig.repo.applications and rig.credits.state["apply:ap1"] == "released"


class FailingRelease(Credits):
    async def release(self, user_id: str, ref: str) -> bool:
        self.calls.append(("release", ref))
        raise RuntimeError("ledger down")


async def test_i_applied_there_gives_the_hold_back_before_the_row_says_sent(rig: Rig) -> None:
    """A release that fails leaves the row in needs_signin (still held): never a sent row with an open hold, which the
    sweep and delete would capture."""
    failing = FailingRelease()
    rig.svc.credits = failing
    await rig.repo.create_application(
        stored("ap1", "needs_signin", credit_state="held", credit_ref="apply:ap1")
    )
    failing.state["apply:ap1"] = "held"
    with pytest.raises(Conflict):
        await rig.svc.applied_there(OWNER, "ap1")
    app = await rig.get("ap1")
    assert (app.stage, app.credit_state) == ("needs_signin", "held")
    rig.svc.credits = rig.credits
    rig.credits.state["apply:ap1"] = "held"
    detail = await rig.svc.applied_there(OWNER, "ap1")
    assert (detail.stage, detail.creditState) == ("sent", "released")
    assert [e.kind for e in rig.repo.events if e.to_stage == "sent"] == ["applied_there"]


async def test_try_again_takes_the_hold_through_the_ledger_even_when_the_row_says_held(rig: Rig) -> None:
    """A release that crashed before its write-back leaves ``held`` on a row whose hold is gone: the retry holds again."""
    await rig.repo.create_application(stored("ap1", "closed", credit_state="held", credit_ref="apply:ap1"))
    rig.credits.state["apply:ap1"] = "released"
    detail = await rig.svc.retry(OWNER, "ap1")
    assert detail.stage == "queued" and rig.credits.live == ["apply:ap1"]


class ReopenedDuringRelease(FakeApplyRepo):
    """A fresh start reopens the cancelled row (replaying the still-live hold) while cancel's release runs."""

    async def update_application(self, application_id: str, changes: Any, *, now: datetime,
                                 expected_stage: str | None = None) -> StoredApplication | None:  # fmt: skip
        if dict(changes) == {"credit_state": "released"} and expected_stage == "cancelled":
            await super().update_application(
                application_id,
                {"stage": "queued", "credit_state": "held"},
                now=now,
                expected_stage="cancelled",
            )
        return await super().update_application(
            application_id, changes, now=now, expected_stage=expected_stage
        )


async def test_a_cancel_whose_release_races_a_reopen_takes_the_hold_again(rig: Rig) -> None:
    rig.repo = rig.svc.repo = ReopenedDuringRelease()
    await rig.repo.create_application(stored("ap1", "approved", credit_state="held", credit_ref="apply:ap1"))
    rig.credits.state["apply:ap1"] = "held"
    await rig.svc.cancel(OWNER, "ap1")
    app = await rig.get("ap1")
    assert (app.stage, app.credit_state) == ("queued", "held") and rig.credits.live == ["apply:ap1"]


async def test_a_reopen_whose_hold_cannot_be_taken_again_is_stopped_before_it_runs(rig: Rig) -> None:
    rig.repo = rig.svc.repo = ReopenedDuringRelease()
    await rig.repo.create_application(stored("ap1", "approved", credit_state="held", credit_ref="apply:ap1"))
    rig.credits.state["apply:ap1"] = "held"
    rig.credits.refuse = True  # the credits were spent meanwhile
    await rig.svc.cancel(OWNER, "ap1")
    app = await rig.get("ap1")
    assert (app.stage, app.credit_state) == ("failed", "released") and rig.credits.live == []


async def test_fill_it_again_queues_a_form_that_waits_only_for_a_file(rig: Rig) -> None:
    resume = {"key": "resume:tailored", "label": "Finish your resume first", "kind": "file", "required": True,
              "reason": "x", "questionKey": "resume:tailored", "perOffer": True}  # fmt: skip
    await rig.repo.create_application(
        stored("ap1", "waiting_for_you", needs=[resume], credit_state="held", credit_ref="apply:ap1")
    )
    rig.credits.state["apply:ap1"] = "held"
    assert (await rig.svc.retry(OWNER, "ap1")).stage == "queued"
    await rig.repo.create_application(
        stored("ap2", "waiting_for_you", needs=[resume, need("notice", "noticePeriod")])
    )
    with pytest.raises(BadRequest):
        await rig.svc.retry(OWNER, "ap2")  # a question is still open: answer it instead
    assert await rig.stage("ap2") == "waiting_for_you"


async def test_an_answer_must_be_to_a_question_the_form_asks(rig: Rig) -> None:
    review = {"fields": [{"key": "city", "questionKey": "location"}], "skipped": []}
    await rig.repo.create_application(
        stored("ap1", "waiting_for_you", needs=[need("notice", "noticePeriod")], review=review)
    )
    with pytest.raises(BadRequest):
        await rig.svc.answer(
            OWNER, "ap1", AnswerRequest(answers=[QuestionAnswer(questionKey="x", value="y", save=True)])
        )
    assert await rig.stage("ap1") == "waiting_for_you"
    edited = await rig.svc.answer(
        OWNER,
        "ap1",
        AnswerRequest(answers=[QuestionAnswer(questionKey="location", value="Pune", save=False)]),
    )
    assert edited.application.stage == "queued"


async def test_a_question_key_longer_than_the_bank_keeps_is_a_400(rig: Rig) -> None:
    with pytest.raises(BadRequest):
        await rig.svc.save_answer(
            OWNER, SaveAnswerRequest(questionKey="q:" + "x" * 63, value="v", askEachTime=False)
        )
    await rig.svc.save_answer(
        OWNER, SaveAnswerRequest(questionKey="q:" + "x" * 62, value="v", askEachTime=False)
    )


async def test_the_resume_fills_the_first_layer_of_the_bank_until_the_first_check(rig: Rig) -> None:
    await rig.repo.put_answer(
        StoredAnswer(user_id=USER, question_key="location", question="Current city", source="you",
                     ask_each_time=False, answered_at=T0, created_at=T0, updated_at=T0, value="Pune")
    )  # fmt: skip
    facts = {"fullName": "Riya Kapoor", "email": "riya@example.com", "phone": "+91 98450 12345",
             "location": "Bengaluru", "currentTitle": "Backend Engineer", "totalExperience": "4 years",
             "pan": "ABCDE1234F"}  # fmt: skip
    written = await rig.svc.read_resume(OWNER, facts, account_email="riya@example.com")
    assert written == 5
    details = await rig.svc.details(OWNER)
    by_key = {a.questionKey: (a.value, a.source) for a in details.answers}
    assert by_key["fullName"] == ("Riya Kapoor", "resume") and by_key["phone"] == (
        "+91 98450 12345",
        "resume",
    )
    assert by_key["location"] == ("Pune", "you")  # what the person wrote wins
    assert "pan" not in by_key
    assert (
        await rig.svc.read_resume(OWNER, {"email": "someone@else.com"}, account_email="riya@example.com") == 0
    )
    await rig.svc.first_check(OWNER, FirstCheckRequest(skipped=True))
    await rig.svc.delete_answer(OWNER, "currentTitle")
    assert (
        await rig.svc.read_resume(OWNER, facts, account_email="riya@example.com") == 0
    )  # never after the check
