"""Auto-apply storage (`applications`, `application_events`, `ats_playbooks`, `run_metrics`, `apply_profiles`,
`answer_bank`).

Reads of applications never load the two screenshot columns (each can be hundreds of kilobytes): `has_screenshot` and
`has_confirmation` are computed in SQL and the bytes come from `get_screenshot` / `get_confirmation` alone. `claim_next` is
a candidate SELECT followed by a conditional UPDATE per candidate (``WHERE id = ? AND lease is free AND stage is still
runnable``): the UPDATE that matches one row wins, so two workers never hold one application, on SQLite and on Postgres
alike. A row in ``submitting`` is never runnable: the click may have happened, so only the worker's sweep
(`list_expired`) moves it on, to ``check_this_one``.

One application per posting per person: the unique index on ``(user_id, posting_key)`` makes a second insert (or a
double-clicked start racing the first) raise `IntegrityError`. The answer bank refuses never-stored keys
(`ports.is_never_stored`) before anything is written.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Any, Final

from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from ..db import Database, dialect_insert, rowcount
from ..ports import (
    APPLICATION_COLUMNS,
    FILLED_TERMINAL_STAGES,
    NOT_ON_SITE_OUTCOME,
    StoredAnswer,
    StoredApplication,
    StoredApplicationEvent,
    StoredApplyProfile,
    StoredPlaybook,
    StoredRunMetric,
    account_owner_key,
    is_never_stored,
)
from ..tables import (
    AnswerBankRow,
    ApplicationEventRow,
    ApplicationRow,
    ApplyProfileRow,
    AtsPlaybookRow,
    RunMetricRow,
    as_utc,
)

RUNNABLE_STAGES: Final = ("queued", "approved")
"""Stages a worker may claim: ``queued`` to fill, ``approved`` to submit (never ``submitting``)."""

_SCREENSHOT_COLUMNS: Final = frozenset({"review_screenshot", "confirmation_screenshot"})
_APP_COLUMNS = [c for c in ApplicationRow.__table__.columns if c.name not in _SCREENSHOT_COLUMNS]
_HAS_SCREENSHOT = ApplicationRow.review_screenshot.is_not(None).label("has_screenshot")
_HAS_CONFIRMATION = ApplicationRow.confirmation_screenshot.is_not(None).label("has_confirmation")
_APP_SELECT = (*_APP_COLUMNS, _HAS_SCREENSHOT, _HAS_CONFIRMATION)


def _app(row: Any) -> StoredApplication:
    return StoredApplication(
        id=row.id,
        owner_key=row.owner_key,
        user_id=row.user_id,
        profile_id=row.profile_id,
        match_id=row.match_id,
        source=row.source,
        job_url=row.job_url,
        apply_url=row.apply_url,
        manual_url=row.manual_url,
        domain=row.domain,
        ats=row.ats,
        company=row.company,
        title=row.title,
        location=row.location,
        job=None if row.job is None else dict(row.job),
        stage=row.stage,
        stage_reason=row.stage_reason,
        needs=None if row.needs is None else [dict(n) for n in row.needs],
        review=None if row.review is None else dict(row.review),
        review_digest=row.review_digest,
        has_screenshot=bool(row.has_screenshot),
        approved_at=row.approved_at,
        approved_digest=row.approved_digest,
        submitted_at=row.submitted_at,
        attempts=int(row.attempts or 0),
        lease_owner=row.lease_owner,
        lease_until=row.lease_until,
        created_at=row.created_at,
        updated_at=row.updated_at,
        posting_key=row.posting_key,
        resume_choice=row.resume_choice,
        listing_id=row.listing_id,
        submitting_at=row.submitting_at,
        credit_ref=row.credit_ref,
        credit_state=row.credit_state,
        stop_reason=row.stop_reason,
        has_confirmation=bool(row.has_confirmation),
        progress=None if row.progress is None else dict(row.progress),
    )


def _event(row: Any) -> StoredApplicationEvent:
    return StoredApplicationEvent(
        id=row.id,
        application_id=row.application_id,
        at=row.at,
        kind=row.kind,
        source=row.source,
        from_stage=row.from_stage,
        to_stage=row.to_stage,
        detail=None if row.detail is None else dict(row.detail),
    )


def _playbook(row: Any) -> StoredPlaybook:
    return StoredPlaybook(
        id=row.id,
        ats=row.ats,
        domain=row.domain,
        status=row.status,
        origin=row.origin,
        version=int(row.version),
        spec=dict(row.spec or {}),
        successes=int(row.successes or 0),
        failures=int(row.failures or 0),
        created_at=row.created_at,
        updated_at=row.updated_at,
        promoted_at=row.promoted_at,
    )


def _metric(row: Any) -> StoredRunMetric:
    return StoredRunMetric(
        id=row.id,
        application_id=row.application_id,
        created_at=row.created_at,
        run_kind=row.run_kind,
        ats=row.ats,
        domain=row.domain,
        mode=row.mode,
        playbook_id=row.playbook_id,
        playbook_status=row.playbook_status,
        outcome=row.outcome,
        failure_reason=row.failure_reason,
        steps=int(row.steps or 0),
        duration_ms=int(row.duration_ms or 0),
        cost_micro_usd=int(row.cost_micro_usd or 0),
        fields_total=int(row.fields_total or 0),
        fields_filled=int(row.fields_filled or 0),
        fields_model=int(row.fields_model or 0),
    )


def _profile(row: Any) -> StoredApplyProfile:
    return StoredApplyProfile(
        owner_key=row.owner_key,
        user_id=row.user_id,
        resume_name=row.resume_name,
        resume_type=row.resume_type,
        resume_bytes=None if row.resume_bytes is None else bytes(row.resume_bytes),
        resume_sha256=row.resume_sha256,
        self_id_on_forms=bool(row.self_id_on_forms),
        first_check_at=row.first_check_at,
        consent_at=row.consent_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _answer(row: Any) -> StoredAnswer:
    return StoredAnswer(
        user_id=row.user_id,
        question_key=row.question_key,
        question=row.question,
        value=row.value,
        source=row.source,
        answered_on=row.answered_on,
        application_id=row.application_id,
        ask_each_time=bool(row.ask_each_time),
        answered_at=row.answered_at,
        confirmed_at=row.confirmed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _free_lease(now: datetime) -> ColumnElement[bool]:
    return or_(ApplicationRow.lease_until.is_(None), ApplicationRow.lease_until < now)


def _runnable(now: datetime) -> ColumnElement[bool]:
    """Free lease, and queued (to fill) or approved (to submit). Never ``submitting``."""
    return and_(_free_lease(now), ApplicationRow.stage.in_(RUNNABLE_STAGES))


def _without_typed(job: Any) -> dict[str, Any] | None:
    if job is None:
        return None
    return {key: value for key, value in dict(job).items() if key != "typed"}


class SqlApplyRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ── applications ──

    async def create_application(self, item: StoredApplication) -> None:
        async with self._db.session() as s:
            await s.execute(
                insert(ApplicationRow).values(
                    id=item.id,
                    owner_key=item.owner_key,
                    user_id=item.user_id,
                    profile_id=item.profile_id,
                    match_id=item.match_id,
                    source=item.source,
                    job_url=item.job_url,
                    apply_url=item.apply_url,
                    manual_url=item.manual_url,
                    domain=item.domain,
                    ats=item.ats,
                    company=item.company,
                    title=item.title,
                    location=item.location,
                    job=item.job,
                    stage=item.stage,
                    stage_reason=item.stage_reason,
                    needs=item.needs,
                    review=item.review,
                    review_digest=item.review_digest,
                    review_screenshot=None,
                    approved_at=item.approved_at,
                    approved_digest=item.approved_digest,
                    submitted_at=item.submitted_at,
                    attempts=item.attempts,
                    lease_owner=item.lease_owner,
                    lease_until=item.lease_until,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    posting_key=item.posting_key,
                    resume_choice=item.resume_choice,
                    listing_id=item.listing_id,
                    submitting_at=item.submitting_at,
                    credit_ref=item.credit_ref,
                    credit_state=item.credit_state,
                    stop_reason=item.stop_reason,
                    confirmation_screenshot=None,
                    progress=item.progress,
                )
            )

    async def find_by_posting(self, user_id: str, posting_key: str) -> StoredApplication | None:
        stmt = select(*_APP_SELECT).where(
            ApplicationRow.user_id == user_id, ApplicationRow.posting_key == posting_key
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _app(row)

    async def get_application(self, application_id: str, owner_key: str | None) -> StoredApplication | None:
        stmt = select(*_APP_SELECT).where(ApplicationRow.id == application_id)
        if owner_key is not None:
            stmt = stmt.where(ApplicationRow.owner_key == owner_key)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _app(row)

    async def list_applications(self, owner_key: str, limit: int = 200) -> list[StoredApplication]:
        stmt = (
            select(*_APP_SELECT)
            .where(ApplicationRow.owner_key == owner_key)
            .order_by(ApplicationRow.created_at.desc(), ApplicationRow.id.desc())
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        return [_app(row) for row in rows]

    async def update_application(
        self,
        application_id: str,
        changes: Mapping[str, Any],
        *,
        now: datetime,
        expected_stage: str | None = None,
    ) -> StoredApplication | None:
        unknown = set(changes) - APPLICATION_COLUMNS
        if unknown:
            raise ValueError(f"not an updatable application column: {sorted(unknown)}")
        stmt = update(ApplicationRow).where(ApplicationRow.id == application_id)
        if expected_stage is not None:
            stmt = stmt.where(ApplicationRow.stage == expected_stage)
        stmt = stmt.values(**dict(changes), updated_at=now)
        async with self._db.session() as s:
            if rowcount(await s.execute(stmt)) == 0:
                return None
            row = (
                await s.execute(select(*_APP_SELECT).where(ApplicationRow.id == application_id))
            ).one_or_none()
        return None if row is None else _app(row)

    async def delete_application(self, application_id: str, owner_key: str) -> bool:
        async with self._db.session() as s:
            await s.execute(
                delete(ApplicationEventRow).where(ApplicationEventRow.application_id == application_id)
            )
            gone = await s.execute(
                delete(ApplicationRow).where(
                    ApplicationRow.id == application_id, ApplicationRow.owner_key == owner_key
                )
            )
            return rowcount(gone) > 0

    async def count_created_since(self, owner_key: str, since: datetime) -> int:
        stmt = select(func.count()).where(
            ApplicationRow.owner_key == owner_key, ApplicationRow.created_at >= since
        )
        async with self._db.session() as s:
            return int((await s.execute(stmt)).scalar_one())

    async def claim_next(
        self, *, now: datetime, lease_owner: str, lease_until: datetime, exclude_domains: Collection[str] = ()
    ) -> StoredApplication | None:
        candidates = select(ApplicationRow.id).where(_runnable(now))
        if exclude_domains:
            candidates = candidates.where(ApplicationRow.domain.not_in(list(exclude_domains)))
        candidates = candidates.order_by(ApplicationRow.updated_at.asc(), ApplicationRow.id.asc()).limit(5)
        async with self._db.session() as s:
            ids = [row[0] for row in (await s.execute(candidates)).all()]
        for candidate in ids:
            stmt = (
                update(ApplicationRow)
                .where(ApplicationRow.id == candidate, _runnable(now))
                .values(lease_owner=lease_owner, lease_until=lease_until)
            )
            async with self._db.session() as s:
                if rowcount(await s.execute(stmt)) == 0:
                    continue  # another worker took it between the SELECT and here
                row = (await s.execute(select(*_APP_SELECT).where(ApplicationRow.id == candidate))).one()
            return _app(row)
        return None

    async def list_expired(
        self, stages: Collection[str], *, now: datetime, limit: int = 50
    ) -> list[StoredApplication]:
        if not stages:
            return []
        stmt = (
            select(*_APP_SELECT)
            .where(ApplicationRow.stage.in_(list(stages)), _free_lease(now))
            .order_by(ApplicationRow.updated_at.asc(), ApplicationRow.id.asc())
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        return [_app(row) for row in rows]

    async def list_waiting(
        self, user_id: str, stages: Collection[str] = ("waiting_for_you",)
    ) -> list[StoredApplication]:
        if not stages:
            return []
        stmt = (
            select(*_APP_SELECT)
            .where(ApplicationRow.user_id == user_id, ApplicationRow.stage.in_(list(stages)))
            .order_by(ApplicationRow.created_at.asc(), ApplicationRow.id.asc())
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).all()
        return [_app(row) for row in rows]

    async def release(self, application_id: str, lease_owner: str) -> None:
        stmt = (
            update(ApplicationRow)
            .where(ApplicationRow.id == application_id, ApplicationRow.lease_owner == lease_owner)
            .values(lease_owner=None, lease_until=None)
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def get_screenshot(self, application_id: str, owner_key: str | None) -> bytes | None:
        stmt = select(ApplicationRow.review_screenshot).where(ApplicationRow.id == application_id)
        if owner_key is not None:
            stmt = stmt.where(ApplicationRow.owner_key == owner_key)
        async with self._db.session() as s:
            data = (await s.execute(stmt)).scalar_one_or_none()
        return None if data is None else bytes(data)

    async def put_screenshot(self, application_id: str, data: bytes | None) -> None:
        stmt = (
            update(ApplicationRow).where(ApplicationRow.id == application_id).values(review_screenshot=data)
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def get_confirmation(self, application_id: str, owner_key: str | None) -> bytes | None:
        stmt = select(ApplicationRow.confirmation_screenshot).where(ApplicationRow.id == application_id)
        if owner_key is not None:
            stmt = stmt.where(ApplicationRow.owner_key == owner_key)
        async with self._db.session() as s:
            data = (await s.execute(stmt)).scalar_one_or_none()
        return None if data is None else bytes(data)

    async def put_confirmation(self, application_id: str, data: bytes | None) -> None:
        stmt = (
            update(ApplicationRow)
            .where(ApplicationRow.id == application_id)
            .values(confirmation_screenshot=data)
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def purge_filled(self, before: datetime) -> int:
        candidates = select(
            ApplicationRow.id,
            ApplicationRow.job,
            ApplicationRow.review_screenshot.is_not(None).label("has_screenshot"),
            ApplicationRow.confirmation_screenshot.is_not(None).label("has_confirmation"),
            ApplicationRow.review.is_not(None).label("has_review"),
            ApplicationRow.needs.is_not(None).label("has_needs"),
        ).where(ApplicationRow.stage.in_(sorted(FILLED_TERMINAL_STAGES)), ApplicationRow.updated_at < before)
        changed = 0
        async with self._db.session() as s:
            for row in (await s.execute(candidates)).all():
                typed = isinstance(row.job, dict) and "typed" in row.job
                if not (
                    row.has_screenshot or row.has_confirmation or row.has_review or row.has_needs or typed
                ):
                    continue
                await s.execute(
                    update(ApplicationRow)
                    .where(ApplicationRow.id == row.id)
                    .values(
                        review_screenshot=None,
                        confirmation_screenshot=None,
                        review=None,
                        needs=None,
                        job=_without_typed(row.job),
                    )
                )
                changed += 1
        return changed

    # ── events ──

    async def add_event(self, event: StoredApplicationEvent) -> None:
        async with self._db.session() as s:
            await s.execute(
                insert(ApplicationEventRow).values(
                    id=event.id,
                    application_id=event.application_id,
                    at=event.at,
                    kind=event.kind,
                    source=event.source,
                    from_stage=event.from_stage,
                    to_stage=event.to_stage,
                    detail=event.detail,
                )
            )

    async def list_events(self, application_id: str, limit: int = 200) -> list[StoredApplicationEvent]:
        stmt = (
            select(ApplicationEventRow)
            .where(ApplicationEventRow.application_id == application_id)
            .order_by(ApplicationEventRow.at.asc(), ApplicationEventRow.id.asc())
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_event(row) for row in rows]

    # ── playbooks ──

    async def list_playbooks(
        self, *, ats: str | None = None, domain: str | None = None, statuses: Collection[str] | None = None
    ) -> list[StoredPlaybook]:
        stmt = select(AtsPlaybookRow)
        if ats is not None:
            stmt = stmt.where(AtsPlaybookRow.ats == ats)
        if domain is not None:
            stmt = stmt.where(AtsPlaybookRow.domain == domain)
        if statuses is not None:
            stmt = stmt.where(AtsPlaybookRow.status.in_(list(statuses)))
        stmt = stmt.order_by(AtsPlaybookRow.updated_at.desc(), AtsPlaybookRow.id.desc())
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_playbook(row) for row in rows]

    async def get_playbook(self, playbook_id: str) -> StoredPlaybook | None:
        async with self._db.session() as s:
            row = (
                await s.execute(select(AtsPlaybookRow).where(AtsPlaybookRow.id == playbook_id))
            ).scalar_one_or_none()
        return None if row is None else _playbook(row)

    async def save_playbook(self, item: StoredPlaybook) -> None:
        values = {
            "id": item.id,
            "ats": item.ats,
            "domain": item.domain,
            "status": item.status,
            "origin": item.origin,
            "version": item.version,
            "spec": dict(item.spec),
            "successes": item.successes,
            "failures": item.failures,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "promoted_at": item.promoted_at,
        }
        stmt = dialect_insert(self._db.dialect, AtsPlaybookRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[AtsPlaybookRow.id],
            set_={name: getattr(stmt.excluded, name) for name in values if name not in ("id", "created_at")},
        )
        async with self._db.session() as s:
            await s.execute(stmt)

    async def record_playbook_result(
        self, playbook_id: str, *, success: bool, now: datetime
    ) -> StoredPlaybook | None:
        counter = AtsPlaybookRow.successes if success else AtsPlaybookRow.failures
        stmt = (
            update(AtsPlaybookRow)
            .where(AtsPlaybookRow.id == playbook_id)
            .values({counter: counter + 1, AtsPlaybookRow.updated_at: now})
        )
        async with self._db.session() as s:
            if rowcount(await s.execute(stmt)) == 0:
                return None
            row = (
                await s.execute(select(AtsPlaybookRow).where(AtsPlaybookRow.id == playbook_id))
            ).scalar_one()
        return _playbook(row)

    async def set_playbook_status(
        self, playbook_id: str, status: str, *, now: datetime
    ) -> StoredPlaybook | None:
        values: dict[str, Any] = {"status": status, "updated_at": now}
        if status == "active":
            values["promoted_at"] = now
        async with self._db.session() as s:
            done = await s.execute(
                update(AtsPlaybookRow).where(AtsPlaybookRow.id == playbook_id).values(**values)
            )
            if rowcount(done) == 0:
                return None
            row = (
                await s.execute(select(AtsPlaybookRow).where(AtsPlaybookRow.id == playbook_id))
            ).scalar_one()
        return _playbook(row)

    # ── run metrics ──

    async def add_run_metric(self, item: StoredRunMetric) -> None:
        async with self._db.session() as s:
            await s.execute(
                insert(RunMetricRow).values(
                    id=item.id,
                    application_id=item.application_id,
                    created_at=item.created_at,
                    run_kind=item.run_kind,
                    ats=item.ats,
                    domain=item.domain,
                    mode=item.mode,
                    playbook_id=item.playbook_id,
                    playbook_status=item.playbook_status,
                    outcome=item.outcome,
                    failure_reason=item.failure_reason,
                    steps=item.steps,
                    duration_ms=item.duration_ms,
                    cost_micro_usd=item.cost_micro_usd,
                    fields_total=item.fields_total,
                    fields_filled=item.fields_filled,
                    fields_model=item.fields_model,
                )
            )

    async def list_run_metrics(self, since: datetime, limit: int = 20000) -> list[StoredRunMetric]:
        stmt = (
            select(RunMetricRow)
            .where(RunMetricRow.created_at >= since)
            .order_by(RunMetricRow.created_at.desc(), RunMetricRow.id.desc())
            .limit(limit)
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_metric(row) for row in rows]

    async def count_domain_runs_since(self, domain: str, since: datetime) -> int:
        stmt = select(func.count()).where(
            RunMetricRow.domain == domain,
            RunMetricRow.created_at >= since,
            RunMetricRow.outcome != NOT_ON_SITE_OUTCOME,
        )
        async with self._db.session() as s:
            return int((await s.execute(stmt)).scalar_one())

    async def last_domain_run_at(self, domain: str) -> datetime | None:
        stmt = select(func.max(RunMetricRow.created_at)).where(
            RunMetricRow.domain == domain, RunMetricRow.outcome != NOT_ON_SITE_OUTCOME
        )
        async with self._db.session() as s:
            value = (await s.execute(stmt)).scalar_one_or_none()
        return None if value is None else as_utc(value)

    # ── profiles ──

    async def get_profile(self, owner_key: str) -> StoredApplyProfile | None:
        async with self._db.session() as s:
            row = (
                await s.execute(select(ApplyProfileRow).where(ApplyProfileRow.owner_key == owner_key))
            ).scalar_one_or_none()
        return None if row is None else _profile(row)

    async def _ensure(self, owner_key: str, user_id: str, now: datetime) -> StoredApplyProfile:
        found = await self.get_profile(owner_key)
        if found is not None:
            return found
        stmt = (
            dialect_insert(self._db.dialect, ApplyProfileRow)
            .values(
                owner_key=owner_key,
                user_id=user_id,
                resume_name=None,
                resume_type=None,
                resume_bytes=None,
                resume_sha256=None,
                self_id_on_forms=False,
                first_check_at=None,
                consent_at=None,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[ApplyProfileRow.owner_key])
        )
        async with self._db.session() as s:
            await s.execute(stmt)
        made = await self.get_profile(owner_key)
        assert made is not None  # noqa: S101 - just inserted (or a concurrent insert won)
        return made

    async def _set_profile(
        self, owner_key: str, user_id: str, values: Mapping[str, Any], now: datetime
    ) -> StoredApplyProfile:
        await self._ensure(owner_key, user_id, now)
        stmt = (
            update(ApplyProfileRow)
            .where(ApplyProfileRow.owner_key == owner_key)
            .values(**dict(values), updated_at=now)
        )
        async with self._db.session() as s:
            await s.execute(stmt)
        found = await self.get_profile(owner_key)
        assert found is not None  # noqa: S101
        return found

    async def put_resume(
        self,
        owner_key: str,
        *,
        user_id: str,
        name: str | None,
        media_type: str | None,
        data: bytes | None,
        now: datetime,
        sha256: str | None = None,
    ) -> StoredApplyProfile:
        keep = data is not None
        values = {
            "resume_name": name if keep else None,
            "resume_type": media_type if keep else None,
            "resume_bytes": data,
            "resume_sha256": sha256 if keep else None,
        }
        return await self._set_profile(owner_key, user_id, values, now)

    async def set_self_id(self, user_id: str, on: bool, *, now: datetime) -> StoredApplyProfile:
        return await self._set_profile(account_owner_key(user_id), user_id, {"self_id_on_forms": on}, now)

    async def mark_first_check(self, user_id: str, *, now: datetime) -> StoredApplyProfile:
        return await self._set_profile(account_owner_key(user_id), user_id, {"first_check_at": now}, now)

    async def set_consent(self, user_id: str, *, now: datetime) -> StoredApplyProfile:
        return await self._set_profile(account_owner_key(user_id), user_id, {"consent_at": now}, now)

    # ── answer bank ──

    async def list_answers(self, user_id: str) -> list[StoredAnswer]:
        stmt = (
            select(AnswerBankRow)
            .where(AnswerBankRow.user_id == user_id)
            .order_by(AnswerBankRow.question_key.asc())
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_answer(row) for row in rows]

    async def get_answers(self, user_id: str, keys: Collection[str]) -> dict[str, StoredAnswer]:
        if not keys:
            return {}
        stmt = select(AnswerBankRow).where(
            AnswerBankRow.user_id == user_id, AnswerBankRow.question_key.in_(sorted(set(keys)))
        )
        async with self._db.session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return {row.question_key: _answer(row) for row in rows}

    async def _get_answer(self, user_id: str, question_key: str) -> StoredAnswer | None:
        stmt = select(AnswerBankRow).where(
            AnswerBankRow.user_id == user_id, AnswerBankRow.question_key == question_key
        )
        async with self._db.session() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
        return None if row is None else _answer(row)

    async def put_answer(self, item: StoredAnswer) -> StoredAnswer:
        if is_never_stored(item.question_key):
            raise ValueError(f"{item.question_key!r} is never stored in the answer bank")
        values = {
            "user_id": item.user_id,
            "question_key": item.question_key,
            "question": item.question,
            "value": item.value,
            "source": item.source,
            "answered_on": item.answered_on,
            "application_id": item.application_id,
            "ask_each_time": item.ask_each_time,
            "answered_at": item.answered_at,
            "confirmed_at": item.confirmed_at,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
        stmt = dialect_insert(self._db.dialect, AnswerBankRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[AnswerBankRow.user_id, AnswerBankRow.question_key],
            set_={
                name: getattr(stmt.excluded, name)
                for name in values
                if name not in ("user_id", "question_key", "created_at")
            },
        )
        async with self._db.session() as s:
            await s.execute(stmt)
        stored = await self._get_answer(item.user_id, item.question_key)
        assert stored is not None  # noqa: S101 - just written
        return stored

    async def confirm_answer(self, user_id: str, question_key: str, *, now: datetime) -> StoredAnswer | None:
        stmt = (
            update(AnswerBankRow)
            .where(AnswerBankRow.user_id == user_id, AnswerBankRow.question_key == question_key)
            .values(confirmed_at=now, updated_at=now)
        )
        async with self._db.session() as s:
            if rowcount(await s.execute(stmt)) == 0:
                return None
        return await self._get_answer(user_id, question_key)

    async def delete_answer(self, user_id: str, question_key: str) -> bool:
        stmt = delete(AnswerBankRow).where(
            AnswerBankRow.user_id == user_id, AnswerBankRow.question_key == question_key
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt)) > 0


__all__ = ["RUNNABLE_STAGES", "SqlApplyRepo"]
