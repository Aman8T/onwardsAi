"""`InboxService` over the fakes: addresses (opt-in, idempotent, collisions, off), receiving (pending, quarantined, ignored,
duplicates, other events), processing (verification codes, classified with the real `EmailTracker` on keyword rules or a
stub, unmatched, copies sent, failed or skipped, the daily cap, fetch retries, a classifier that breaks), reconcile, the
auto-apply seam, and logs that never carry a subject, a sender, a body or a code."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest

from tayyari.apply.email import EmailTracker
from tayyari.contract.apply import EmailClassification, EmailClassifyRequest
from tayyari.core.errors import NotFound
from tayyari.inbox import address as address_module
from tayyari.inbox.provider import ReceivedEmail, ReceivedSummary
from tayyari.inbox.service import (
    DAILY_CAP,
    LEASE,
    MAX_ATTEMPTS,
    InboxConfig,
    InboxService,
    copy_note,
    same_site,
)
from tayyari.storage.ports import StoredApplication, StoredInboundEmail, StoredInboxAddress
from tests.fakes.billing import captured_events
from tests.fakes.clock import FakeClock
from tests.fakes.inbox_provider import FakeInboundMailProvider
from tests.fakes.repos import FakeRepositories

DOMAIN = "apply.leafout.in"
BODY = (
    "Hi Riya,\nWe would like to schedule an interview with you for the Growth role at Acme next week.\nBest"
)


@dataclass
class StubClassifier:
    result: EmailClassification = field(
        default_factory=lambda: EmailClassification(stage="none", confidence="low", applied=False)
    )
    calls: list[tuple[str, EmailClassifyRequest]] = field(default_factory=list)
    error: Exception | None = None

    async def __call__(self, owner: str, request: EmailClassifyRequest) -> EmailClassification:
        self.calls.append((owner, request))
        if self.error is not None:
            raise self.error
        return self.result


@dataclass
class Rig:
    fakes: FakeRepositories
    clock: FakeClock
    provider: FakeInboundMailProvider
    service: InboxService
    classify: Any

    async def person(self, email: str | None = "riya@gmail.com", name: str | None = "Riya Shah") -> str:
        if email is None:  # an account with no email (a phone-only Leafout sign-in)
            user = await self.fakes.users.sign_in_supabase(
                sub="sb-1", email=None, email_verified=False, name=name, now=self.clock.now()
            )
            return user.id
        user = await self.fakes.users.sign_in_email(email=email, now=self.clock.now())
        if name:
            await self.fakes.users.update(user.id, name=name)
        return user.id

    async def address(self, user_id: str) -> StoredInboxAddress:
        await self.service.create_address(user_id, "Riya Shah")
        found = await self.fakes.inbox.get_address_for_user(user_id)
        assert found is not None
        return found

    def event(self, email_id: str, to: Any, **data: Any) -> dict[str, Any]:
        payload = {"email_id": email_id, "from": "Acme Talent <talent@acme.com>", "to": to,
                   "subject": "Interview at Acme", "created_at": self.clock.now().isoformat(), **data}  # fmt: skip
        return {"type": "email.received", "created_at": self.clock.now().isoformat(), "data": payload}

    def mail(
        self, email_id: str, *, subject: str = "Interview at Acme", text: str = BODY, **extra: Any
    ) -> None:
        self.provider.add(
            ReceivedEmail(id=email_id, sender="talent@acme.com", to=[], subject=subject, text=text,
                          message_id=f"<{email_id}@acme.com>", **extra)
        )  # fmt: skip

    async def deliver(self, user_id: str, email_id: str = "e-1", **mail: Any) -> StoredInboundEmail:
        addr = await self.fakes.inbox.get_address_for_user(user_id)
        assert addr is not None
        self.mail(email_id, **mail)
        ids = await self.service.receive(self.event(email_id, [f"{addr.local_part}@{DOMAIN}"]))
        assert len(ids) == 1
        await self.service.process(ids[0])
        return await self.row(ids[0])

    async def row(self, row_id: str) -> StoredInboundEmail:
        found = await self.fakes.inbox.get_email(row_id)
        assert found is not None
        return found


def build(
    *, active: bool = True, classify: Any = None, provider: FakeInboundMailProvider | None = None
) -> Rig:
    clock = FakeClock()
    fakes = FakeRepositories(clock)
    mail = provider or FakeInboundMailProvider()
    if classify is None:
        tracker = EmailTracker(fakes.apply, clock, None, "test-model")
        classify = tracker.handle
    service = InboxService(
        repo=fakes.inbox, users=fakes.users, applications=fakes.apply, provider=mail, classify=classify,
        config=InboxConfig(active=active, domain=DOMAIN, app_url="https://app.leafout.in"), clock=clock,
    )  # fmt: skip
    return Rig(fakes, clock, mail, service, classify)


async def sent_application(rig: Rig, user_id: str, *, company: str = "Acme", stage: str = "sent") -> str:
    now = rig.clock.now()
    await rig.fakes.apply.create_application(
        StoredApplication(
            id="app-1", owner_key=f"u:{user_id}", user_id=user_id, source="match", job_url="https://x", domain="x",
            ats="greenhouse", stage=stage, created_at=now, updated_at=now, company=company, title="Growth",
        )
    )  # fmt: skip
    return "app-1"


# ───────────────────────── addresses ─────────────────────────


async def test_an_address_is_made_once_with_consent_and_given_back_after() -> None:
    rig = build()
    uid = await rig.person()
    first = await rig.service.create_address(uid, "Riya Shah")
    again = await rig.service.create_address(uid, "Somebody Else")
    assert first == again
    local = first.email.split("@")[0]
    assert (
        first.email.endswith("@" + DOMAIN) and local.startswith("riya.s") and len(local) == len("riya.s") + 5
    )
    assert first.active and first.copies
    stored = await rig.fakes.inbox.get_address_for_user(uid)
    assert (
        stored is not None and stored.consent_version == "inbox-v1" and stored.consent_at == rig.clock.now()
    )
    assert len(rig.fakes.inbox.addresses) == 1


async def test_a_taken_local_part_is_drawn_again(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = build()
    one, two = await rig.person("a@x.co"), await rig.person("b@x.co")
    draws = iter(["riya.sbbbbb", "riya.sbbbbb", "riya.scccc2"])
    monkeypatch.setattr("tayyari.inbox.service.make_local_part", lambda first, last: next(draws))
    assert (await rig.service.create_address(one, "Riya Shah")).email == f"riya.sbbbbb@{DOMAIN}"
    assert (await rig.service.create_address(two, "Riya Shah")).email == f"riya.scccc2@{DOMAIN}"


async def test_the_address_generator_is_the_real_one() -> None:
    assert address_module.make_local_part("Riya", "Shah").startswith("riya.s")


async def test_turning_off_and_copies_and_status() -> None:
    rig = build()
    uid = await rig.person()
    assert (await rig.service.status(uid)).address is None
    await rig.service.create_address(uid, "Riya Shah")
    off = await rig.service.update_address(uid, active=False, copies=None)
    assert off.active is False and off.copies is True
    quiet = await rig.service.update_address(uid, active=None, copies=False)
    assert quiet.active is False and quiet.copies is False
    status = await rig.service.status(uid)
    assert status.enabled and status.address == quiet
    assert (await rig.service.status(None)).to_dict() == {"enabled": True}
    other = await rig.person("b@x.co")
    with pytest.raises(NotFound):
        await rig.service.update_address(other, active=True, copies=None)


async def test_with_the_feature_off_nothing_works_but_the_status() -> None:
    rig = build(active=False)
    uid = await rig.person()
    assert (await rig.service.status(uid)).to_dict() == {"enabled": False}
    with pytest.raises(NotFound):
        await rig.service.create_address(uid, "Riya")
    with pytest.raises(NotFound):
        await rig.service.list_emails(uid)
    assert await rig.service.form_email(uid) is None
    assert await rig.service.reconcile(list_provider=True) == {"pending": 0, "listed": 0, "recorded": 0}


async def test_form_email_is_the_active_address_only() -> None:
    rig = build()
    uid = await rig.person()
    assert await rig.service.form_email(uid) is None
    made = await rig.service.create_address(uid, "Riya Shah")
    assert await rig.service.form_email(uid) == made.email
    await rig.service.update_address(uid, active=False, copies=None)
    assert await rig.service.form_email(uid) is None


# ───────────────────────── receiving ─────────────────────────


async def test_mail_to_an_active_address_is_pending_and_a_repeat_is_a_no_op() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    event = rig.event("e-1", [f"Riya <{addr.local_part.upper()}@APPLY.LEAFOUT.IN>"])
    ids = await rig.service.receive(event)
    assert len(ids) == 1
    row = await rig.row(ids[0])
    assert (row.status, row.user_id, row.address_id, row.recipient) == (
        "pending",
        uid,
        addr.id,
        addr.local_part,
    )
    assert (row.sender, row.sender_domain, row.subject) == (
        "talent@acme.com",
        "acme.com",
        "Interview at Acme",
    )
    assert await rig.service.receive(event) == []
    assert len(rig.fakes.inbox.emails) == 1


async def test_mail_to_an_unknown_address_is_quarantined_and_never_fetched() -> None:
    rig = build()
    assert await rig.service.receive(rig.event("e-2", [f"nobody.zzzzz@{DOMAIN}"])) == []
    (row,) = rig.fakes.inbox.emails.values()
    assert (row.status, row.user_id, row.address_id, row.recipient) == (
        "quarantined",
        None,
        None,
        "nobody.zzzzz",
    )
    assert rig.provider.fetched == []


async def test_mail_with_no_recipient_on_our_domain_is_quarantined() -> None:
    rig = build()
    assert await rig.service.receive(rig.event("e-3", ["someone@gmail.com"])) == []
    (row,) = rig.fakes.inbox.emails.values()
    assert row.status == "quarantined" and row.recipient is None


async def test_mail_to_an_address_that_is_off_is_ignored() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    await rig.service.update_address(uid, active=False, copies=None)
    assert await rig.service.receive(rig.event("e-4", f"{addr.local_part}@{DOMAIN}")) == []
    (row,) = rig.fakes.inbox.emails.values()
    assert row.status == "ignored" and row.user_id == uid
    assert rig.provider.fetched == []


async def test_mail_to_the_address_of_a_deleted_account_is_quarantined() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    rig.fakes.inbox.forget_user(uid)
    assert await rig.service.receive(rig.event("e-5", f"{addr.local_part}@{DOMAIN}")) == []
    (row,) = rig.fakes.inbox.emails.values()
    assert row.status == "quarantined" and row.user_id is None


async def test_the_received_for_field_is_read_too() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    event = rig.event("e-6", ["list@company.com"], received_for=[f"{addr.local_part}@{DOMAIN}"])
    assert len(await rig.service.receive(event)) == 1


@pytest.mark.parametrize(
    "event",
    [
        {"type": "email.sent", "data": {"email_id": "e-1"}},
        {"type": "email.received", "data": "nope"},
        {"type": "email.received", "data": {"to": ["a@apply.leafout.in"]}},
        {"data": {"email_id": "e-1"}},
    ],
)
async def test_other_events_record_nothing(event: dict[str, Any]) -> None:
    rig = build()
    assert await rig.service.receive(event) == []
    assert rig.fakes.inbox.emails == {}


# ───────────────────────── processing ─────────────────────────


async def test_a_verification_code_is_kept_and_nothing_else_happens() -> None:
    stub = StubClassifier()
    rig = build(classify=stub)
    uid = await rig.person()
    await rig.address(uid)
    row = await rig.deliver(
        uid, subject="Your Greenhouse code is 482913", text="Enter 482913 to verify your email."
    )
    assert (row.status, row.copy_status, row.stage) == ("verification", "skipped", None)
    assert "482913" not in row.subject and row.subject == "Your Greenhouse code is [code]"
    assert stub.calls == [] and rig.provider.sent == []
    code = await rig.service.latest_code(uid, rig.clock.now() - timedelta(minutes=1))
    assert code is not None and code.code == "482913" and code.sender_domain == "acme.com"
    assert code.expires_at == row.received_at + timedelta(minutes=15)
    listed = (await rig.service.list_emails(uid)).emails[0]
    assert listed.status == "verification" and listed.stage is None and listed.quote is None


async def test_a_reply_moves_the_application_and_the_person_gets_a_copy() -> None:
    rig = build()  # the real EmailTracker, keyword rules (no model key)
    uid = await rig.person()
    await rig.address(uid)
    app_id = await sent_application(rig, uid)
    row = await rig.deliver(uid)
    assert (row.status, row.stage, row.application_id, row.copy_status) == (
        "processed",
        "interviewing",
        app_id,
        "sent",
    )
    assert row.quote and "schedule an interview" in row.quote
    moved = await rig.fakes.apply.get_application(app_id, f"u:{uid}")
    assert moved is not None and moved.stage == "interviewing"
    (copy,) = rig.provider.sent
    assert copy.to == "riya@gmail.com" and copy.reply_to == "talent@acme.com"
    assert copy.sender == "Leafout <inbox@apply.leafout.in>"
    assert copy.subject == "[Leafout] Acme: Interview at Acme"
    first, second = copy.text.splitlines()[:2]
    assert first == "Leafout set your application at Acme to Interviewing."
    assert second == "Your applications: https://app.leafout.in/apply"
    assert BODY in copy.text
    stored = repr(row) + repr(rig.fakes.inbox.emails)
    assert "Hi Riya" not in stored and BODY not in stored  # only the short quote, never the body


async def test_a_reply_that_matches_nothing_is_unmatched_and_copied_under_the_sender_domain() -> None:
    rig = build()
    uid = await rig.person()
    await rig.address(uid)
    row = await rig.deliver(uid)
    assert (row.status, row.stage, row.application_id, row.copy_status) == (
        "unmatched",
        None,  # nothing moved, so no stage was set (the guess is not kept)
        None,
        "sent",
    )
    (copy,) = rig.provider.sent
    assert copy.subject == "[Leafout] acme.com: Interview at Acme"
    assert copy.text.startswith("Leafout could not match this email")


async def test_the_classifier_gets_the_account_as_owner_and_the_email() -> None:
    stub = StubClassifier()
    rig = build(classify=stub)
    uid = await rig.person()
    await rig.address(uid)
    row = await rig.deliver(uid)
    ((owner, request),) = stub.calls
    assert owner == f"u:{uid}" and request.sender == "talent@acme.com" and request.body == BODY
    assert row.status == "unmatched" and row.stage is None


async def test_a_failed_copy_never_fails_the_processing() -> None:
    rig = build(provider=FakeInboundMailProvider(fail_send=True))
    uid = await rig.person()
    await rig.address(uid)
    await sent_application(rig, uid)
    row = await rig.deliver(uid)
    assert row.status == "processed" and row.copy_status == "failed"


async def test_no_copy_when_copies_are_off_or_the_account_has_no_email() -> None:
    rig = build()
    uid = await rig.person()
    await rig.address(uid)
    await rig.service.update_address(uid, active=None, copies=False)
    assert (await rig.deliver(uid, "e-1")).copy_status == "skipped"
    other = await rig.person(None)
    await rig.address(other)
    assert (await rig.deliver(other, "e-2")).copy_status == "skipped"
    assert rig.provider.sent == []


async def test_over_the_daily_cap_an_email_is_capped_and_not_fetched() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    now = rig.clock.now()
    for i in range(DAILY_CAP):
        await rig.fakes.inbox.insert_email(
            StoredInboundEmail(id=f"old-{i}", provider_email_id=f"p-{i}", received_at=now, status="unmatched",
                               sender="a@b.co", sender_domain="b.co", subject="s", created_at=now,
                               address_id=addr.id, user_id=uid)
        )  # fmt: skip
    rig.mail("e-new")
    (row_id,) = await rig.service.receive(rig.event("e-new", f"{addr.local_part}@{DOMAIN}"))
    assert await rig.service.process(row_id) == "capped"
    assert rig.provider.fetched == []


async def test_turned_off_after_receipt_is_ignored_when_processed() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    rig.mail("e-1")
    (row_id,) = await rig.service.receive(rig.event("e-1", f"{addr.local_part}@{DOMAIN}"))
    await rig.service.update_address(uid, active=False, copies=None)
    assert await rig.service.process(row_id) == "ignored"
    assert rig.provider.fetched == []


async def test_a_fetch_that_fails_is_tried_again_then_marked_failed() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    (row_id,) = await rig.service.receive(rig.event("e-missing", f"{addr.local_part}@{DOMAIN}"))
    for _ in range(MAX_ATTEMPTS - 1):
        assert await rig.service.process(row_id) is None
        assert (await rig.row(row_id)).status == "pending"
        assert await rig.service.process(row_id) is None  # the lease is still held
        rig.clock.advance(LEASE.total_seconds() + 1)
    assert await rig.service.process(row_id) == "failed"
    assert (await rig.row(row_id)).attempts == MAX_ATTEMPTS


async def test_a_classifier_that_breaks_marks_failed_without_a_copy() -> None:
    rig = build(classify=StubClassifier(error=RuntimeError("boom")))
    uid = await rig.person()
    await rig.address(uid)
    row = await rig.deliver(uid)
    assert row.status == "failed" and rig.provider.sent == []


async def test_a_processed_email_is_not_processed_again() -> None:
    stub = StubClassifier()
    rig = build(classify=stub)
    uid = await rig.person()
    await rig.address(uid)
    done = await rig.deliver(uid)
    assert await rig.service.process(done.id) is None
    assert len(stub.calls) == 1


# ───────────────────────── reconcile ─────────────────────────


async def test_reconcile_processes_old_pending_rows_and_lists_missed_webhooks() -> None:
    stub = StubClassifier()
    rig = build(classify=stub)
    uid = await rig.person()
    addr = await rig.address(uid)
    rig.mail("e-old")
    rig.mail("e-new")
    (old_id,) = await rig.service.receive(rig.event("e-old", f"{addr.local_part}@{DOMAIN}"))
    rig.clock.advance(150)
    (new_id,) = await rig.service.receive(rig.event("e-new", f"{addr.local_part}@{DOMAIN}"))
    rig.mail("e-missed")
    rig.provider.listed = [
        ReceivedSummary(id="e-old", to=[f"{addr.local_part}@{DOMAIN}"]),
        ReceivedSummary(id="e-missed", sender="talent@acme.com", to=[f"{addr.local_part}@{DOMAIN}"], subject="Hi",
                        received_at=rig.clock.now()),
    ]  # fmt: skip
    counts = await rig.service.reconcile(list_provider=True)
    assert counts == {"pending": 1, "listed": 2, "recorded": 1}
    assert (await rig.row(old_id)).status == "unmatched"
    assert (await rig.row(new_id)).status == "pending"  # under two minutes old: the webhook's task has it
    missed = [e for e in rig.fakes.inbox.emails.values() if e.provider_email_id == "e-missed"]
    assert len(missed) == 1 and missed[0].status == "unmatched"


async def test_reconcile_survives_a_failing_list() -> None:
    rig = build(provider=FakeInboundMailProvider(fail_list=True))
    assert await rig.service.reconcile(list_provider=True) == {"pending": 0, "listed": 0, "recorded": 0}


# ───────────────────────── the auto-apply seam ─────────────────────────


async def test_latest_code_is_the_newest_unused_unexpired_and_prefers_the_hint() -> None:
    rig = build()
    uid = await rig.person()
    await rig.address(uid)
    start = rig.clock.now()
    await rig.deliver(uid, "e-1", subject="Workday verification code", text="Code: 111AAA")
    rig.clock.advance(60)
    rig.provider.add(
        ReceivedEmail(id="e-2", sender="no-reply@greenhouse.io", subject="Security code", text="222BBB")
    )
    addr = await rig.fakes.inbox.get_address_for_user(uid)
    assert addr is not None
    (row_id,) = await rig.service.receive(
        rig.event("e-2", f"{addr.local_part}@{DOMAIN}", **{"from": "no-reply@greenhouse.io"})
    )
    await rig.service.process(row_id)

    newest = await rig.service.latest_code(uid, start)
    assert newest is not None and newest.code == "222BBB"
    hinted = await rig.service.latest_code(uid, start, sender_domain_hint="acme.com")
    assert hinted is not None and hinted.code == "111AAA"
    fallback = await rig.service.latest_code(uid, start, sender_domain_hint="lever.co")
    assert fallback is not None and fallback.code == "222BBB"
    assert await rig.service.latest_code(uid, rig.clock.now() + timedelta(seconds=1)) is None

    assert await rig.service.mark_used(newest.id) is True
    assert await rig.service.mark_used(newest.id) is False
    after = await rig.service.latest_code(uid, start)
    assert after is not None and after.code == "111AAA"
    rig.clock.advance(15 * 60)
    assert await rig.service.latest_code(uid, start) is None
    assert await rig.service.purge_codes() == 2
    assert rig.fakes.inbox.codes == {}


def test_same_site() -> None:
    assert same_site("mail.greenhouse.io", "greenhouse.io")
    assert not same_site("greenhouse.io", "lever.co")
    assert not same_site("", "lever.co")


def test_copy_note_for_a_match_that_did_not_move() -> None:
    result = EmailClassification(stage="viewed", confidence="low", applied=False, applicationId="a")
    assert (
        copy_note(result, "Acme")
        == "Leafout matched this email to your application at Acme; its stage did not change."
    )


# ───────────────────────── logs ─────────────────────────


async def test_logs_carry_ids_and_statuses_never_the_email(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = build()
    uid = await rig.person()
    await rig.address(uid)
    await sent_application(rig, uid)
    with captured_events(monkeypatch, "tayyari.inbox.service") as logs:
        await rig.deliver(uid, "e-1")
        await rig.deliver(uid, "e-2", subject="Your verification code 482913", text="Use 482913")
        rig.provider.fail_send = True
        await rig.deliver(uid, "e-3")
    assert logs, "the service logs its steps"
    text = repr(logs)
    for secret in (
        "Interview at Acme",
        "talent@acme.com",
        "482913",
        "schedule an interview",
        "riya@gmail.com",
    ):
        assert secret not in text


async def test_an_email_that_keeps_breaking_is_failed_after_the_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    rig.mail("e-1")
    (row_id,) = await rig.service.receive(rig.event("e-1", f"{addr.local_part}@{DOMAIN}"))

    async def broken(address_id: str, since: Any, **_: Any) -> int:
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(rig.fakes.inbox, "count_fetched_since", broken)
    for _ in range(MAX_ATTEMPTS):
        await rig.service.process_many([row_id])  # never raises: it runs after the webhook answered
        rig.clock.advance(LEASE.total_seconds() + 1)
    assert (await rig.row(row_id)).status == "failed"


# ───────────────────────── review fixes: what must never happen ─────────────────────────


async def test_an_interview_invitation_that_says_verify_is_classified_and_copied() -> None:
    rig = build()
    uid = await rig.person()
    await rig.address(uid)
    app_id = await sent_application(rig, uid)
    text = (
        "Hi Riya,\nWe would like to schedule an interview with you. Please verify your availability for the 14th "
        "at 10am in Room B204.\nBest"
    )
    row = await rig.deliver(uid, subject="Interview at Acme: please verify your slot", text=text)
    assert (row.status, row.stage, row.application_id, row.copy_status) == (
        "processed",
        "interviewing",
        app_id,
        "sent",
    )
    assert rig.fakes.inbox.codes == {} and len(rig.provider.sent) == 1


async def test_a_low_confidence_match_keeps_no_stage() -> None:
    stub = StubClassifier(
        EmailClassification(stage="interviewing", confidence="low", applied=False, applicationId="app-1")
    )
    rig = build(classify=stub)
    uid = await rig.person()
    await rig.address(uid)
    row = await rig.deliver(uid)
    assert (row.status, row.stage, row.application_id) == ("processed", None, "app-1")
    listed = (await rig.service.list_emails(uid)).emails[0]
    assert listed.stage is None


@pytest.mark.parametrize("make_off", [False, True])
async def test_a_code_in_the_subject_is_never_stored_on_rows_that_are_not_read(make_off: bool) -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    if make_off:
        await rig.service.update_address(uid, active=False, copies=None)
    subject = "Your Greenhouse verification code is 482913"
    ids = await rig.service.receive(rig.event("e-1", f"{addr.local_part}@{DOMAIN}", subject=subject))
    (row,) = rig.fakes.inbox.emails.values()
    assert row.status == ("ignored" if make_off else "pending") and len(ids) == (0 if make_off else 1)
    assert "482913" not in row.subject and row.subject == "Your Greenhouse verification code is [code]"
    listed = (await rig.service.list_emails(uid)).emails[0]
    assert "482913" not in listed.subject


async def test_a_capped_or_failed_row_never_keeps_a_code_in_its_subject() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    now = rig.clock.now()
    for i in range(DAILY_CAP):
        await rig.fakes.inbox.insert_email(
            StoredInboundEmail(id=f"old-{i}", provider_email_id=f"p-{i}", received_at=now, status="unmatched",
                               sender="a@b.co", sender_domain="b.co", subject="s", created_at=now,
                               address_id=addr.id, user_id=uid)
        )  # fmt: skip
    (row_id,) = await rig.service.receive(
        rig.event("e-new", f"{addr.local_part}@{DOMAIN}", subject="Your code is 482913, use it to verify")
    )
    assert await rig.service.process(row_id) == "capped"
    assert "482913" not in (await rig.row(row_id)).subject


async def test_our_own_copy_coming_back_is_ignored_and_never_read() -> None:
    stub = StubClassifier()
    rig = build(classify=stub)
    uid = await rig.person()
    addr = await rig.address(uid)
    event = rig.event(
        "e-loop", f"{addr.local_part}@{DOMAIN}", subject="[Leafout] acme.com: Interview at Acme",
        **{"from": "Leafout <inbox@apply.leafout.in>"},
    )  # fmt: skip
    assert await rig.service.receive(event) == []
    (row,) = rig.fakes.inbox.emails.values()
    assert row.status == "ignored" and row.user_id is None
    assert rig.provider.fetched == [] and stub.calls == [] and rig.provider.sent == []


async def test_a_copy_forwarded_back_from_elsewhere_is_caught_by_its_marker_header() -> None:
    stub = StubClassifier()
    rig = build(classify=stub)
    uid = await rig.person()
    await rig.address(uid)
    row = await rig.deliver(uid, headers={"x-leafout-copy": "1"})
    assert row.status == "ignored" and row.copy_status == "skipped"
    assert stub.calls == [] and rig.provider.sent == []


async def test_a_slow_classifier_is_not_run_twice_when_the_lease_runs_out() -> None:
    gate, started = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def slow(owner: str, request: EmailClassifyRequest) -> EmailClassification:
        calls.append(owner)
        started.set()
        await gate.wait()
        return EmailClassification(stage="none", confidence="low", applied=False)

    rig = build(classify=slow)
    uid = await rig.person()
    addr = await rig.address(uid)
    rig.mail("e-1")
    (row_id,) = await rig.service.receive(rig.event("e-1", f"{addr.local_part}@{DOMAIN}"))
    first = asyncio.create_task(rig.service.process(row_id))
    await started.wait()
    rig.clock.advance(LEASE.total_seconds() + 60)  # the lease ran out while the model was still answering
    counts = await rig.service.reconcile()
    assert counts["pending"] == 1  # the row looks free in the database ...
    assert len(calls) == 1  # ... but this process is reading it, so it is not started again
    gate.set()
    assert await first == "unmatched"
    assert len(calls) == 1 and len(rig.provider.sent) == 1


async def test_the_lease_outlasts_the_classifier_timeout() -> None:
    from tayyari.inbox.service import CLASSIFY_TIMEOUT

    assert LEASE - CLASSIFY_TIMEOUT >= timedelta(minutes=2)


async def test_a_classifier_that_times_out_is_failed_and_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tayyari.inbox.service.CLASSIFY_TIMEOUT", timedelta(milliseconds=10))
    calls: list[str] = []

    async def hangs(owner: str, request: EmailClassifyRequest) -> EmailClassification:
        calls.append(owner)
        await asyncio.sleep(5)
        raise AssertionError("never reached")

    rig = build(classify=hangs)
    uid = await rig.person()
    await rig.address(uid)
    row = await rig.deliver(uid)
    assert row.status == "failed" and rig.provider.sent == []
    rig.clock.advance(LEASE.total_seconds() + 1)
    await rig.service.reconcile()
    assert len(calls) == 1


async def test_emails_being_read_count_towards_the_daily_cap() -> None:
    rig = build()
    uid = await rig.person()
    addr = await rig.address(uid)
    now = rig.clock.now()
    for i in range(DAILY_CAP):  # a burst: every one claimed by a worker, none finished yet
        await rig.fakes.inbox.insert_email(
            StoredInboundEmail(id=f"busy-{i}", provider_email_id=f"p-{i}", received_at=now, status="pending",
                               sender="a@b.co", sender_domain="b.co", subject="s", created_at=now,
                               address_id=addr.id, user_id=uid, attempts=1, claimed_until=now + LEASE)
        )  # fmt: skip
    rig.mail("e-new")
    (row_id,) = await rig.service.receive(rig.event("e-new", f"{addr.local_part}@{DOMAIN}"))
    assert await rig.service.process(row_id) == "capped"
    assert rig.provider.fetched == []
