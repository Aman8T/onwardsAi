"""The application inbox: one tracking address per person, the mail Resend receives for it, and what we do with each email.

    receive(event)    a verified ``email.received`` webhook -> one metadata row: ``pending`` for an active address,
                      ``ignored`` for one that is off, ``quarantined`` for an unknown address (never fetched). A repeated
                      delivery writes nothing (unique Resend id). Returns the ids to process off the request path.
    process(id)       claim the pending row (a lease, so one replica works on it), check the per-address daily cap, fetch
                      the email, then either keep a verification code (status ``verification``; no classification, no
                      copy) or hand it to the existing classifier (`apply.email.EmailTracker`, through the `Classify`
                      callable the container builds, under the spend reservation) and send the person a copy.
    reconcile()       process ``pending`` rows older than `RECONCILE_AFTER`, and, when asked, list the newest received
                      emails from the provider to catch a webhook that never came.
    latest_code / mark_used   the seam for the auto-apply worker (not wired into the fill loop yet: docs/auto-apply.md §3.4).

The body and attachments are never stored or logged: the text lives in memory while it is classified and copied. Logs carry
ids and statuses only (no subject, no sender, never a code). A failed copy marks ``copy_status`` and never fails the rest.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from tayyari.contract.apply import EmailClassification, EmailClassifyRequest
from tayyari.contract.inbox import (
    INBOX_CONSENT_VERSION,
    INBOX_EMAILS_DEFAULT_LIMIT,
    INBOX_EMAILS_MAX_LIMIT,
    INBOX_SUBJECT_MAX_CHARS,
    InboxAddress,
    InboxEmail,
    InboxEmailList,
    InboxStatus,
)
from tayyari.core.clock import Clock
from tayyari.core.errors import NotFound
from tayyari.core.logging import get_logger
from tayyari.inbox import codes
from tayyari.inbox.address import address_of, make_local_part, name_parts
from tayyari.inbox.mail import domain_of, recipients_from, sender_of
from tayyari.inbox.provider import (
    COPY_MARKER_HEADER,
    CopyMessage,
    InboundMailError,
    InboundMailProvider,
    parse_time,
)
from tayyari.storage.ports import (
    ApplyRepo,
    InboxRepo,
    StoredInboundEmail,
    StoredInboxAddress,
    StoredVerificationCode,
    UserRepo,
    account_owner_key,
)

log = get_logger(__name__)

Classify = Callable[[str, EmailClassifyRequest], Awaitable[EmailClassification]]
"""``classify(owner_key, request)``: the existing email classifier (it moves the matching application's stage)."""

DAILY_CAP: Final = 200
"""Emails read per address per UTC day; beyond it a row is ``capped``."""
CODE_TTL: Final = timedelta(minutes=15)
LEASE: Final = timedelta(minutes=10)
"""How long one worker holds a pending email. Longer than the worst case of one run (the fetch and the copy time out after
10 s each, the classifier after `CLASSIFY_TIMEOUT`), so the reconcile pass never picks up an email that is still being
read; within one process an email being read is also never started twice (`InboxService._inflight`)."""
CLASSIFY_TIMEOUT: Final = timedelta(minutes=5)
RECONCILE_AFTER: Final = timedelta(minutes=2)
RECONCILE_BATCH: Final = 50
LIST_LIMIT: Final = 100
MAX_ATTEMPTS: Final = 5
"""A fetch that keeps failing (an id the provider no longer knows) is ``failed`` after this many tries."""
ADDRESS_DRAWS: Final = 10
COPY_TEXT_MAX: Final = 100_000
CLASSIFY_SUBJECT_MAX: Final = 300
CLASSIFY_BODY_MAX: Final = 20_000
WEBHOOK_TYPE: Final = "email.received"

STAGE_LABELS: Final[dict[str, str]] = {
    "applied": "Applied",
    "viewed": "Viewed",
    "interviewing": "Interviewing",
    "offer": "Offer",
    "rejected": "Not moving forward",
}


@dataclass(frozen=True, slots=True)
class InboxConfig:
    active: bool
    """``INBOUND_EMAIL_ENABLED`` and both Resend secrets (`Settings.inbound_email_active`)."""
    domain: str = "apply.leafout.in"
    copy_from: str = "Leafout <inbox@apply.leafout.in>"
    app_url: str = "http://localhost:5173"
    """The web app's address: the copy links to ``<app_url>/apply``."""


def _iso(value: datetime) -> str:
    return value.isoformat()


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _subject(value: str) -> str:
    return " ".join((value or "").split())[:INBOX_SUBJECT_MAX_CHARS]


def _stored_subject(value: str) -> str:
    """The subject as we keep it: tidied, cut, and with anything that looks like a code hidden (`codes.scrub`), on every
    row whatever its status, so a code never reaches the database or the web app."""
    return codes.scrub(_subject(value))


def same_site(a: str, b: str) -> bool:
    """Do two domains share their last two labels (``mail.greenhouse.io`` and ``greenhouse.io``)?"""
    tail_a, tail_b = a.lower().strip(".").split(".")[-2:], b.lower().strip(".").split(".")[-2:]
    return bool(a and b) and tail_a == tail_b


def copy_subject(label: str, subject: str) -> str:
    return f"[Leafout] {label}: {subject or '(no subject)'}"


def copy_note(result: EmailClassification, company: str | None) -> str:
    """The one line above the original text: what the email did to the person's applications."""
    where = f"at {company}" if company else "it matches"
    if result.applicationId and result.applied:
        label = STAGE_LABELS.get(result.stage, result.stage)
        return f"Leafout set your application {where} to {label}."
    if result.applicationId:
        return f"Leafout matched this email to your application {where}; its stage did not change."
    return "Leafout could not match this email to one of your applications."


def copy_text(note: str, app_url: str, sender: str, subject: str, text: str) -> str:
    return (
        f"{note}\nYour applications: {app_url.rstrip('/')}/apply\n\n"
        f"---------- Original message ----------\nFrom: {sender}\nSubject: {subject}\n\n"
        f"{text[:COPY_TEXT_MAX]}"
    )


class InboxService:
    def __init__(
        self,
        *,
        repo: InboxRepo,
        users: UserRepo,
        applications: ApplyRepo,
        provider: InboundMailProvider | None,
        classify: Classify | None,
        config: InboxConfig,
        clock: Clock,
    ) -> None:
        self.repo, self.users, self.applications = repo, users, applications
        self.provider, self.classify, self.config, self.clock = provider, classify, config, clock
        self._inflight: set[str] = set()
        """Rows this process is reading now: a reconcile pass never starts one of them again."""
        own = domain_of(sender_of(config.copy_from))
        self._own_domains = {d for d in (config.domain.lower(), own) if d}

    def _from_us(self, sender_domain: str) -> bool:
        """Mail from our own domain (our copy coming back through a forwarding rule): never read, never copied."""
        domain = sender_domain.lower()
        return any(domain == d or domain.endswith("." + d) for d in self._own_domains)

    @property
    def enabled(self) -> bool:
        return self.config.active and self.provider is not None

    def _require_on(self) -> None:
        if not self.enabled:
            raise NotFound("Not found")

    # ───────────────────────── the person's side ─────────────────────────

    def _view(self, address: StoredInboxAddress) -> InboxAddress:
        return InboxAddress(
            email=address_of(address.local_part, self.config.domain),
            active=address.status == "active",
            copies=address.copies,
            createdAt=_iso(address.created_at),
        )

    async def status(self, user_id: str | None) -> InboxStatus:
        if not self.enabled or user_id is None:
            return InboxStatus(enabled=self.enabled)
        found = await self.repo.get_address_for_user(user_id)
        return InboxStatus(enabled=True, address=self._view(found) if found is not None else None)

    async def create_address(self, user_id: str, full_name: str | None) -> InboxAddress:
        """The person's address, made now (with their consent) or the one they have already: idempotent."""
        self._require_on()
        existing = await self.repo.get_address_for_user(user_id)
        if existing is not None:
            return self._view(existing)
        first, last = name_parts(full_name)
        now = self.clock.now()
        for _ in range(ADDRESS_DRAWS):
            item = StoredInboxAddress(
                id=uuid.uuid4().hex, user_id=user_id, local_part=make_local_part(first, last), status="active",
                copies=True, consent_at=now, consent_version=INBOX_CONSENT_VERSION, created_at=now,
            )  # fmt: skip
            made = await self.repo.create_address(item)
            if made is not None:
                log.info("inbox_address_created", address_id=made.id)
                return self._view(made)
            raced = await self.repo.get_address_for_user(user_id)  # a double click that won meanwhile
            if raced is not None:
                return self._view(raced)
        raise RuntimeError("no free tracking address after several draws")

    async def update_address(self, user_id: str, *, active: bool | None, copies: bool | None) -> InboxAddress:
        self._require_on()
        found = await self.repo.get_address_for_user(user_id)
        if found is None:
            raise NotFound("You have no tracking address yet.")
        status = None if active is None else ("active" if active else "off")
        updated = await self.repo.update_address(found.id, status=status, copies=copies)
        if updated is None:  # pragma: no cover - the row was just read
            raise NotFound("You have no tracking address yet.")
        log.info("inbox_address_updated", address_id=found.id, status=updated.status, copies=updated.copies)
        return self._view(updated)

    async def list_emails(self, user_id: str, limit: int = INBOX_EMAILS_DEFAULT_LIMIT) -> InboxEmailList:
        self._require_on()
        size = min(max(limit, 1), INBOX_EMAILS_MAX_LIMIT)
        rows = await self.repo.list_emails(user_id, size)
        return InboxEmailList(emails=[self._email_view(r) for r in rows])

    @staticmethod
    def _email_view(row: StoredInboundEmail) -> InboxEmail:
        verification = row.status == "verification"
        return InboxEmail(
            id=str(row.id), receivedAt=_iso(row.received_at), sender=row.sender, senderDomain=row.sender_domain,
            subject=row.subject, status=row.status,  # type: ignore[arg-type]
            stage=None if verification else row.stage,  # type: ignore[arg-type]
            applicationId=None if verification else row.application_id,
            quote=None if verification else row.quote,
            copyStatus=row.copy_status,  # type: ignore[arg-type]
        )  # fmt: skip

    async def form_email(self, user_id: str) -> str | None:
        """The address auto-apply puts on forms instead of the person's own: their active tracking address while the inbox
        is on, else None (the saved email stays)."""
        if not self.enabled:
            return None
        found = await self.repo.get_address_for_user(user_id)
        if found is None or not found.active:
            return None
        return address_of(found.local_part, self.config.domain)

    # ───────────────────────── receiving ─────────────────────────

    async def _pick_address(self, local_parts: list[str]) -> tuple[StoredInboxAddress | None, str | None]:
        """The address the mail is for (an active one first) and the local part it was sent to."""
        found: list[tuple[StoredInboxAddress, str]] = []
        for local in local_parts:
            address = await self.repo.get_address_by_local_part(local)
            if address is not None:
                found.append((address, local))
        found.sort(key=lambda pair: not pair[0].active)
        if found:
            return found[0]
        return None, (local_parts[0] if local_parts else None)

    async def receive(self, event: dict[str, Any]) -> list[str]:
        """Record one webhook event; the ids of rows to process now (empty for anything else). Database errors propagate
        (the route answers 503 so the provider retries)."""
        if event.get("type") != WEBHOOK_TYPE:
            return []
        data = event.get("data")
        if not isinstance(data, dict):
            return []
        provider_id = data.get("email_id") or data.get("id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            return []
        provider_id = provider_id.strip()[:128]
        now = self.clock.now()
        address, local = await self._pick_address(recipients_from(data, self.config.domain))
        if address is None:
            status = "quarantined"
        elif not address.active:
            status = "ignored" if address.user_id is not None else "quarantined"
        else:
            status = "pending"
        keep_owner = address is not None and address.user_id is not None
        sender = sender_of(data.get("from") or data.get("sender"))
        if status == "pending" and self._from_us(domain_of(sender)):
            status, keep_owner = "ignored", False  # a mail loop: our own copy came back
        received = parse_time(data.get("created_at")) or parse_time(event.get("created_at")) or now
        raw_id, raw_subject = data.get("message_id"), data.get("subject")
        message_id = raw_id if isinstance(raw_id, str) else None
        row = StoredInboundEmail(
            id=uuid.uuid4().hex, provider_email_id=provider_id, received_at=received, status=status,
            sender=sender[:320], sender_domain=domain_of(sender)[:255],
            subject=_stored_subject(raw_subject if isinstance(raw_subject, str) else ""),
            created_at=now, recipient=local[:64] if local else None,
            address_id=address.id if address is not None else None,
            user_id=address.user_id if keep_owner and address is not None else None, message_id=message_id,
        )  # fmt: skip
        if not await self.repo.insert_email(row):
            log.info("inbox_email_duplicate", provider_email_id=provider_id)
            return []
        log.info("inbox_email_received", email_id=row.id, provider_email_id=provider_id, status=status)
        return [row.id] if status == "pending" else []

    # ───────────────────────── processing ─────────────────────────

    async def process_many(self, ids: list[str]) -> None:
        """Process each id; never raises (it runs as a background task after the webhook answered)."""
        for email_id in ids:
            try:
                await self.process(email_id)
            except Exception as exc:  # logged by type only; the reconcile pass tries again
                log.error("inbox_process_crashed", email_id=email_id, error=type(exc).__name__)

    async def process(self, email_id: str) -> str | None:
        """Read one pending email; the status it ended in, or None when it was not ours to process (not pending, or
        another replica holds it, or the fetch failed and will be tried again)."""
        if self.provider is None or email_id in self._inflight:
            return None
        self._inflight.add(email_id)
        try:
            return await self._claim_and_process(email_id, self.provider)
        finally:
            self._inflight.discard(email_id)

    async def _claim_and_process(self, email_id: str, provider: InboundMailProvider) -> str | None:
        now = self.clock.now()
        row = await self.repo.claim_email(email_id, now=now, until=now + LEASE)
        if row is None:
            return None
        try:
            return await self._process(row, provider)
        except InboundMailError as exc:
            if row.attempts >= MAX_ATTEMPTS:
                await self._finish(row.id, status="failed")
                log.warning("inbox_fetch_failed", email_id=row.id, error=str(exc), attempts=row.attempts)
                return "failed"
            log.warning("inbox_fetch_retry", email_id=row.id, error=str(exc), attempts=row.attempts)
            return None
        except Exception:
            if row.attempts >= MAX_ATTEMPTS:  # something keeps breaking on this email: stop trying it
                await self._finish(row.id, status="failed")
            raise

    async def _finish(self, email_id: str, *, status: str, **changes: Any) -> None:
        await self.repo.update_email(
            email_id, {"status": status, "claimed_until": None, "processed_at": self.clock.now(), **changes}
        )

    async def _process(self, row: StoredInboundEmail, provider: InboundMailProvider) -> str:
        address = await self.repo.get_address(row.address_id) if row.address_id else None
        if address is None or not address.active or row.user_id is None:
            await self._finish(row.id, status="ignored")
            log.info("inbox_email_done", email_id=row.id, status="ignored")
            return "ignored"
        since = _day_start(row.received_at)
        if await self.repo.count_fetched_since(address.id, since, exclude_id=row.id) >= DAILY_CAP:
            await self._finish(row.id, status="capped")
            log.warning("inbox_email_done", email_id=row.id, status="capped", address_id=address.id)
            return "capped"

        fetched = await provider.get_received(row.provider_email_id)
        sender = fetched.sender or row.sender
        subject = _subject(fetched.subject) or row.subject
        stored_subject = codes.scrub(subject)
        meta: dict[str, Any] = {
            "sender": sender[:320],
            "sender_domain": domain_of(sender)[:255],
            "message_id": fetched.message_id or row.message_id,
        }
        if COPY_MARKER_HEADER.lower() in fetched.headers or self._from_us(meta["sender_domain"]):
            await self._finish(
                row.id, status="ignored", subject=stored_subject, copy_status="skipped", **meta
            )
            log.info("inbox_email_done", email_id=row.id, status="ignored", reason="our_own_copy")
            return "ignored"

        found = codes.extract(subject, fetched.text, fetched.html)
        if found is not None:
            await self.repo.add_code(
                StoredVerificationCode(
                    id=uuid.uuid4().hex, user_id=row.user_id, sender_domain=meta["sender_domain"],
                    received_at=row.received_at, expires_at=row.received_at + CODE_TTL, code=found.code,
                    link=found.link,
                )
            )  # fmt: skip
            await self._finish(
                row.id,
                status="verification",
                subject=codes.scrub(codes.redact(subject, found.code)),
                copy_status="skipped",
                **meta,
            )
            log.info("inbox_email_done", email_id=row.id, status="verification")
            return "verification"

        if self.classify is None:
            await self._finish(row.id, status="failed", subject=stored_subject, **meta)
            return "failed"
        request = EmailClassifyRequest(
            subject=subject[:CLASSIFY_SUBJECT_MAX], sender=sender, body=fetched.text[:CLASSIFY_BODY_MAX]
        )
        owner = account_owner_key(row.user_id)
        try:
            async with asyncio.timeout(CLASSIFY_TIMEOUT.total_seconds()):  # well inside the lease
                result = await self.classify(owner, request)
        except Exception as exc:  # never retried (a timeout neither): a second try could spend twice
            log.error("inbox_classify_failed", email_id=row.id, error=type(exc).__name__)
            await self._finish(row.id, status="failed", subject=stored_subject, **meta)
            return "failed"
        status = "processed" if result.applicationId else "unmatched"
        # the stage it set: only when the classifier moved the application, never a guess that changed nothing
        stage_set = result.stage if result.applied and result.stage != "none" else None
        await self._finish(
            row.id, status=status, subject=stored_subject, stage=stage_set, application_id=result.applicationId,
            quote=result.quote, **meta,
        )  # fmt: skip
        copy_status = await self._copy(row, address, result, owner, sender, subject, fetched.text)
        await self.repo.update_email(row.id, {"copy_status": copy_status})
        log.info(
            "inbox_email_done",
            email_id=row.id,
            status=status,
            stage=result.stage,
            moved=result.applied,
            application_id=result.applicationId,
            copy_status=copy_status,
        )
        return status

    async def _copy(
        self,
        row: StoredInboundEmail,
        address: StoredInboxAddress,
        result: EmailClassification,
        owner: str,
        sender: str,
        subject: str,
        text: str,
    ) -> str:
        """Send the person a copy; ``sent``, ``skipped`` (copies off, or no email on the account) or ``failed``."""
        if not address.copies or self.provider is None or row.user_id is None:
            return "skipped"
        try:
            user = await self.users.get(row.user_id)
            to = user.email if user is not None else None
            if not to:
                return "skipped"
            company = None
            if result.applicationId:
                app = await self.applications.get_application(result.applicationId, owner)
                company = app.company if app is not None else None
            label = company or domain_of(sender) or "Employer"
            await self.provider.send_copy(
                CopyMessage(
                    sender=self.config.copy_from, to=to, reply_to=sender or None,
                    subject=copy_subject(label, subject),
                    text=copy_text(copy_note(result, company), self.config.app_url, sender, subject, text),
                )
            )  # fmt: skip
        except Exception as exc:
            log.warning("inbox_copy_failed", email_id=row.id, error=type(exc).__name__)
            return "failed"
        return "sent"

    # ───────────────────────── reconcile ─────────────────────────

    async def reconcile(self, *, list_provider: bool = False) -> dict[str, int]:
        """Process pending rows older than `RECONCILE_AFTER`; with ``list_provider``, also record and process received
        emails the webhook never brought. Counts per step; never raises for one email."""
        counts = {"pending": 0, "listed": 0, "recorded": 0}
        if not self.enabled:
            return counts
        now = self.clock.now()
        stale = await self.repo.list_pending(before=now - RECONCILE_AFTER, now=now, limit=RECONCILE_BATCH)
        counts["pending"] = len(stale)
        await self.process_many([r.id for r in stale])
        if not list_provider or self.provider is None:
            return counts
        try:
            listed = await self.provider.list_received(LIST_LIMIT)
        except InboundMailError as exc:
            log.warning("inbox_list_failed", error=str(exc))
            return counts
        counts["listed"] = len(listed)
        known = await self.repo.known_provider_ids([s.id for s in listed])
        for summary in listed:
            if summary.id in known:
                continue
            event = {
                "type": WEBHOOK_TYPE,
                "data": {
                    "email_id": summary.id, "from": summary.sender, "to": summary.to, "subject": summary.subject,
                    "created_at": summary.received_at.isoformat() if summary.received_at else None,
                },
            }  # fmt: skip
            ids = await self.receive(event)
            counts["recorded"] += 1
            await self.process_many(ids)
        if counts["recorded"]:
            log.info("inbox_reconciled", **counts)
        return counts

    # ───────────────────────── the auto-apply seam ─────────────────────────

    async def latest_code(
        self, user_id: str, since: datetime, sender_domain_hint: str | None = None
    ) -> StoredVerificationCode | None:
        """The newest unexpired, unused code (or link) that arrived at or after ``since``; one from the hinted domain's
        site first when there is one. Never logged."""
        found = await self.repo.list_codes(user_id, since=since, now=self.clock.now())
        if sender_domain_hint:
            near = [c for c in found if same_site(c.sender_domain, sender_domain_hint)]
            if near:
                return near[0]
        return found[0] if found else None

    async def mark_used(self, code_id: str) -> bool:
        return await self.repo.use_code(code_id, now=self.clock.now())

    async def purge_codes(self) -> int:
        return await self.repo.purge_codes(self.clock.now())


__all__ = [
    "CLASSIFY_TIMEOUT",
    "CODE_TTL",
    "DAILY_CAP",
    "LEASE",
    "MAX_ATTEMPTS",
    "RECONCILE_AFTER",
    "STAGE_LABELS",
    "Classify",
    "InboxConfig",
    "InboxService",
    "copy_note",
    "copy_subject",
    "copy_text",
    "same_site",
]
