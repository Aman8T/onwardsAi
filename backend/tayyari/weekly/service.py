"""`WeeklyService`: subscriptions, editions, and the pass that builds and emails them.

What it owns
    subscription   read and change one account's subscription (`status` ``off`` to ``active`` mints a fresh unsubscribe token)
    editions       list, open (marks it read) and build one now for a reader ("Read the latest edition first")
    run_once       one pass of the scheduler (the container calls it every 15 minutes; ``weekly run`` calls it by hand)

One pass, for each reader day that is being sent (today's weekday, from 06:00 India time, `times.days_due`):

    compose    subscribers who want that day, are active (or paused until a date now past), and have no edition for the week.
               Each is claimed first (`WeeklyRepo.claim`: one kv row, a conditional upsert, so two replicas never both work on
               the same reader), composed, and stored; ``(user_id, week_of)`` is unique, so even a lost claim cannot make
               two. A failure is logged and left for a later tick; after ``max_tries`` the week is given up.
    send       editions of that week with no ``emailed_at`` whose subscriber has an email address, in batches of
               ``batch_size`` with a pause between batches; each is claimed the same way and marked ``emailed_at`` only
               after the mail provider accepted it. An account with no email (a phone-only sign-in) never reaches this step:
               its edition is read in the app.

With no mail provider configured (no ``RESEND_API_KEY``) nothing is sent: the pass says so, and on a machine that is not
production the email's HTML is written to ``<data dir>/weekly/<id>.html`` so it can be looked at.

Nothing here logs an email address, a name, an edition's text or a token.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from tayyari.auth.email import EmailError, EmailMessage, EmailSender
from tayyari.contract.weekly import (
    WeeklyDay,
    WeeklyEdition,
    WeeklyEditionList,
    WeeklyEditionSummary,
    WeeklySection,
    WeeklySubscription,
    WeeklySubscriptionUpdate,
)
from tayyari.core.clock import Clock
from tayyari.core.errors import BadRequest, NotFound
from tayyari.core.logging import get_logger
from tayyari.core.settings import Env, WeeklySettings
from tayyari.storage.ports import (
    StoredUser,
    StoredWeeklyEdition,
    StoredWeeklySubscription,
    UserRepo,
    WeeklyRepo,
)

from .compose import EditionComposer, payload_of
from .render import Links, links_for, list_unsubscribe_headers, render_email
from .times import days_due, week_of

log = get_logger(__name__)

ALL_SECTIONS: tuple[WeeklySection, ...] = ("jobs", "progress", "news", "tip")
DEFAULT_DAY: WeeklyDay = "mon"
PAUSE_DEFAULT_DAYS = 30
PAUSE_MIN_DAYS = 7
PAUSE_MAX_DAYS = 90
LIST_LIMIT = 60
PAGE_LIMIT = 50
"""Readers looked at per page of a pass; a pass keeps paging while pages make progress."""
MAX_PAGES = 100
EDITION_NOT_FOUND = "That edition was not found."
BAD_SECTIONS = "Choose at least one part for the paper."
BAD_PAUSE = "A pause lasts from 7 to 90 days."


@dataclass(slots=True)
class RunReport:
    """What one pass did, in counts."""

    composed: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    no_mailer: int = 0
    days: list[str] = field(default_factory=list)


def edition_of(row: StoredWeeklyEdition) -> WeeklyEdition:
    """The contract's `WeeklyEdition` for a stored row (the columns put back into the payload)."""
    return WeeklyEdition.model_validate(
        {
            **row.payload,
            "id": row.id,
            "createdAt": row.created_at.isoformat(),
            "emailedAt": None if row.emailed_at is None else row.emailed_at.isoformat(),
            "readAt": None if row.read_at is None else row.read_at.isoformat(),
        }
    )


def _sections(items: list[WeeklySection]) -> list[WeeklySection]:
    """The parts in the paper's own order, without repeats."""
    chosen = set(items)
    return [part for part in ALL_SECTIONS if part in chosen]


def _known(items: list[str]) -> list[WeeklySection]:
    """The stored parts that are still parts of the paper, in its order."""
    return [part for part in ALL_SECTIONS if part in items]


class WeeklyService:
    def __init__(
        self,
        *,
        settings: WeeklySettings,
        repo: WeeklyRepo,
        users: UserRepo,
        composer: EditionComposer,
        mailer: EmailSender | None,
        clock: Clock,
        env: Env,
        data_dir: Path,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._users = users
        self._composer = composer
        self._mailer = mailer
        self._clock = clock
        self._env = env
        self._data_dir = data_dir
        self._paid_lock = asyncio.Lock()

    @property
    def can_send(self) -> bool:
        return self._mailer is not None

    @property
    def app_url(self) -> str:
        return self._settings.app_url

    # ───────────────────────── subscription ─────────────────────────

    def _effective(self, row: StoredWeeklySubscription, now: datetime) -> tuple[str, datetime | None]:
        """The status a reader sees: a pause whose date has passed is over."""
        if row.status == "paused" and (row.paused_until is None or row.paused_until <= now):
            return "active", None
        return row.status, row.paused_until

    async def subscription(self, user_id: str) -> WeeklySubscription:
        row = await self._repo.get_subscription(user_id)
        unread = await self._repo.unread_count(user_id)
        if row is None:
            user = await self._users.get(user_id)
            return WeeklySubscription(
                status="off",
                email=user.email if user is not None else None,
                day=DEFAULT_DAY,
                sections=list(ALL_SECTIONS),
                unread=unread,
            )
        status, until = self._effective(row, self._clock.now())
        return WeeklySubscription.model_validate(
            {
                "status": status,
                "email": row.email,
                "day": row.day,
                "sections": row.sections,
                "pausedUntil": None if until is None else until.date().isoformat(),
                "unread": unread,
            }
        )

    async def update(self, user_id: str, body: WeeklySubscriptionUpdate) -> WeeklySubscription:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFound("Account not found")
        now = self._clock.now()
        sections = _sections(body.sections)
        if body.status != "off" and not sections:
            raise BadRequest(BAD_SECTIONS)
        if body.pauseDays is not None and body.status != "paused":
            raise BadRequest(BAD_PAUSE)
        if body.pauseDays is not None and not PAUSE_MIN_DAYS <= body.pauseDays <= PAUSE_MAX_DAYS:
            raise BadRequest(BAD_PAUSE)
        before = await self._repo.get_subscription(user_id)
        paused_until: datetime | None = None
        if body.status == "paused":
            days = body.pauseDays if body.pauseDays is not None else PAUSE_DEFAULT_DAYS
            paused_until = now + timedelta(days=days)
        was_running = before is not None and before.status != "off"
        token = (
            before.unsubscribe_token if before is not None and (was_running or body.status == "off") else None
        )
        row = StoredWeeklySubscription(
            user_id=user_id,
            email=user.email,
            day=body.day,
            sections=list(sections) or (before.sections if before is not None else list(ALL_SECTIONS)),
            status=body.status,
            paused_until=paused_until,
            unsubscribe_token=token or secrets.token_urlsafe(32),
            created_at=before.created_at if before is not None else now,
            updated_at=now,
        )
        await self._repo.save_subscription(row)
        log.info("weekly_subscription", status=row.status, day=row.day, sections=len(row.sections))
        return await self.subscription(user_id)

    async def unsubscribe(self, token: str) -> bool:
        """Switch the subscription with this token off. False when no subscription has it. Safe to repeat."""
        if not token or len(token) > 128:
            return False
        done = await self._repo.unsubscribe_by_token(token, self._clock.now())
        log.info("weekly_unsubscribe", found=done)
        return done

    async def token_known(self, token: str) -> bool:
        return (
            bool(token)
            and len(token) <= 128
            and await self._repo.get_subscription_by_token(token) is not None
        )

    # ───────────────────────── editions ─────────────────────────

    async def editions(self, user_id: str) -> WeeklyEditionList:
        rows = await self._repo.list_editions(user_id, LIST_LIMIT)
        return WeeklyEditionList(
            editions=[
                WeeklyEditionSummary(
                    id=row.id,
                    issue=row.issue,
                    weekOf=row.week_of.isoformat(),
                    headline=str(row.payload.get("headline", "")),
                    read=row.read_at is not None,
                )
                for row in rows
            ]
        )

    async def edition(self, user_id: str, edition_id: str) -> WeeklyEdition:
        """The reader's edition; opening it marks it read. Another reader's id is the same 404 as an unknown one."""
        row = await self._repo.get_edition(user_id, edition_id)
        if row is None:
            raise NotFound(EDITION_NOT_FOUND)
        if row.read_at is None:
            now = self._clock.now()
            await self._repo.mark_read(user_id, edition_id, now)
            row = replace(row, read_at=now)
        return edition_of(row)

    async def current(self, user_id: str) -> StoredWeeklyEdition | None:
        """This week's edition if it is already built (the reader's day, else Monday)."""
        row = await self._repo.get_subscription(user_id)
        day = row.day if row is not None else DEFAULT_DAY
        return await self._repo.edition_for_week(user_id, week_of(self._clock.now(), day))

    async def preview(self, user_id: str) -> WeeklyEdition:
        """This week's edition for the reader, built now if it is not built yet, and marked read."""
        built = await self.build(user_id)
        if built is None:
            raise NotFound("Account not found")
        return await self.edition(user_id, built.id)

    async def build(self, user_id: str) -> StoredWeeklyEdition | None:
        """This week's edition for the reader: the stored one when there is one, else composed and stored. None when the
        account is gone. Uses the reader's subscription for the day and the parts, else Monday and all four."""
        user = await self._users.get(user_id)
        if user is None:
            return None
        sub = await self._repo.get_subscription(user_id)
        return await self._build_for(
            user,
            sub.day if sub is not None else DEFAULT_DAY,
            _known(sub.sections) if sub is not None else list(ALL_SECTIONS),
        )

    async def _take_paid_slot(self, week: date, now: datetime) -> bool:
        """Count this edition against the week's cap on editions that may spend on a model, before it is composed (so editions
        composed at the same moment cannot all pass the check). A slot taken by an edition whose paid part then did not run
        (the spend ceiling) is not given back: the cap is a soft limit and errs on the side of spending less."""
        cap = self._settings.max_paid_editions_per_week
        if cap <= 0:
            return False
        async with self._paid_lock:
            if await self._repo.paid_this_week(week) >= cap:
                return False
            await self._repo.add_paid(week, now)
            return True

    async def _build_for(
        self, user: StoredUser, day: str, sections: list[WeeklySection]
    ) -> StoredWeeklyEdition:
        now = self._clock.now()
        week = week_of(now, day)
        existing = await self._repo.edition_for_week(user.id, week)
        if existing is not None:
            return existing
        allow_paid = await self._take_paid_slot(week, now)
        issue = await self._repo.next_issue(user.id)
        edition_id = secrets.token_hex(16)
        composed = await self._composer.compose(
            user,
            sections,
            edition_id=edition_id,
            issue=issue,
            week_of=week,
            now=now,
            allow_paid=allow_paid,
        )
        row = StoredWeeklyEdition(
            id=edition_id,
            user_id=user.id,
            issue=issue,
            week_of=week,
            payload=payload_of(composed.edition),
            created_at=now,
        )
        if not await self._repo.create_edition(row):
            # Another caller stored this reader's week first; theirs stands.
            found = await self._repo.edition_for_week(user.id, week)
            if found is None:  # pragma: no cover - the row that blocked ours cannot vanish
                raise RuntimeError("a weekly edition vanished under a unique constraint")
            return found
        log.info(
            "weekly_composed",
            issue=issue,
            jobs=len(composed.edition.jobs),
            news=len(composed.edition.news),
            paid=composed.paid,
        )
        return row

    # ───────────────────────── email ─────────────────────────

    def message_for(
        self, sub: StoredWeeklySubscription, row: StoredWeeklyEdition, to: str
    ) -> tuple[EmailMessage, str]:
        """The email for one stored edition, addressed to ``to``, and its HTML (for the local file)."""
        links: Links = links_for(self._settings.app_url, row.id, sub.unsubscribe_token)
        rendered = render_email(edition_of(row), links)
        message = EmailMessage(
            to=to,
            subject=rendered.subject,
            text=rendered.text,
            html=rendered.html,
            headers=list_unsubscribe_headers(links.unsubscribe, self._settings.unsubscribe_mailto),
        )
        return message, rendered.html

    def _write_local_copy(self, edition_id: str, html: str, *, force: bool = False) -> Path | None:
        if self._env == "prod" and not force:
            return None
        try:
            folder = self._data_dir / "weekly"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{edition_id}.html"
            path.write_text(html, encoding="utf-8")
        except OSError:
            log.warning("weekly_local_copy_failed")
            return None
        return path

    async def local_copy(self, user_id: str, row: StoredWeeklyEdition) -> Path | None:
        """Write the email of a stored edition to ``<data dir>/weekly/<id>.html`` and return the path (for ``weekly preview``;
        written on production too, because someone asked for it). A reader with no subscription gets placeholder links."""
        now = self._clock.now()
        sub = await self._repo.get_subscription(user_id) or StoredWeeklySubscription(
            user_id=user_id,
            email=None,
            day=DEFAULT_DAY,
            sections=list(ALL_SECTIONS),
            status="off",
            paused_until=None,
            unsubscribe_token="preview",  # noqa: S106  (a placeholder link, not a credential)
            created_at=now,
            updated_at=now,
        )
        _, html = self.message_for(sub, row, sub.email or "reader@example.com")
        return self._write_local_copy(row.id, html, force=True)

    def local_path(self, edition_id: str) -> Path:
        return self._data_dir / "weekly" / f"{edition_id}.html"

    async def deliver(self, sub: StoredWeeklySubscription, row: StoredWeeklyEdition) -> bool:
        """Email one stored edition. True once the provider accepted it (``emailed_at`` is then set). False when there is
        nowhere to send it (no address, no provider) or the provider refused; the caller decides whether to try again."""
        if sub.email is None:
            return False
        message, html = self.message_for(sub, row, sub.email)
        if self._mailer is None:
            self._write_local_copy(row.id, html)
            log.info("weekly_email_skipped", reason="no_mail_provider")
            return False
        try:
            await self._mailer.send(message)
        except EmailError as exc:
            log.warning("weekly_email_failed", error=str(exc)[:120])
            return False
        await self._repo.mark_emailed(sub.user_id, row.id, self._clock.now())
        return True

    # ───────────────────────── the pass ─────────────────────────

    async def run_once(self) -> RunReport:
        """One pass of the scheduler: compose what is due, then email what is built. Never raises (but cancellation)."""
        report = RunReport()
        now = self._clock.now()
        for day in days_due(now):
            report.days.append(day)
            week = week_of(now, day)
            try:
                await self._compose_due(day, week, report)
                await self._send_built(day, week, report)
            except asyncio.CancelledError:
                raise
            except Exception:
                report.failed += 1
                log.error("weekly_pass_failed", day=day, exc_info=True)
        if report.days:
            log.info(
                "weekly_pass",
                composed=report.composed,
                sent=report.sent,
                failed=report.failed,
                skipped=report.skipped,
            )
        return report

    async def _compose_due(self, day: str, week: date, report: RunReport) -> None:
        now = self._clock.now()
        hold = timedelta(seconds=self._settings.claim_hold_s)
        gate = asyncio.Semaphore(self._settings.concurrency)

        async def one(sub: StoredWeeklySubscription) -> bool:
            attempt = await self._repo.claim(
                f"compose:{sub.user_id}", week, now=now, hold=hold, max_tries=self._settings.max_tries
            )
            if attempt is None:
                report.skipped += 1
                return False
            async with gate:
                try:
                    user = await self._users.get(sub.user_id)
                    if user is None:
                        return False
                    if sub.status == "paused":
                        await self._repo.save_subscription(
                            replace(sub, status="active", paused_until=None, updated_at=now)
                        )
                    await self._build_for(user, sub.day, _known(sub.sections))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    report.failed += 1
                    log.error("weekly_compose_failed", attempt=attempt, exc_info=True)
                    return False
            report.composed += 1
            return True

        for _ in range(MAX_PAGES):
            due = await self._repo.due_subscriptions(day=day, week_of=week, now=now, limit=PAGE_LIMIT)
            if not due:
                return
            results = await asyncio.gather(*(one(sub) for sub in due))
            if not any(results):
                return

    async def _send_built(self, day: str, week: date, report: RunReport) -> None:
        if self._mailer is None:
            # Nothing can be sent. Count what waits, write the local copies (not on production), and leave the rows alone.
            waiting = await self._repo.unsent_editions(
                day=day, week_of=week, now=self._clock.now(), limit=PAGE_LIMIT
            )
            for sub, row in waiting:
                await self.deliver(sub, row)
            report.no_mailer += len(waiting)
            return
        hold = timedelta(seconds=self._settings.claim_hold_s)
        for _ in range(MAX_PAGES):
            now = self._clock.now()
            batch = await self._repo.unsent_editions(
                day=day, week_of=week, now=now, limit=self._settings.batch_size
            )
            progressed = False
            for sub, row in batch:
                attempt = await self._repo.claim(
                    f"send:{sub.user_id}", week, now=now, hold=hold, max_tries=self._settings.max_tries
                )
                if attempt is None:
                    report.skipped += 1
                    continue
                progressed = True
                if await self.deliver(sub, row):
                    report.sent += 1
                else:
                    report.failed += 1
            if not progressed:
                return
            await self._clock.sleep(self._settings.batch_pause_s)


__all__ = ["ALL_SECTIONS", "RunReport", "WeeklyService", "edition_of"]
