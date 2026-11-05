"""Auto-apply storage (`applications`, `application_events`, `ats_playbooks`, `run_metrics`, `apply_profiles`).

Reads of applications never load the screenshot column (it can be hundreds of kilobytes): `has_screenshot` is computed in
SQL and the bytes come from `get_screenshot` alone. `claim_next` is a candidate SELECT followed by a conditional UPDATE per
candidate (``WHERE id = ? AND lease is free AND stage is still runnable``): the UPDATE that matches one row wins, so two
workers never hold one application, on SQLite and on Postgres alike.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from ..db import Database, dialect_insert, rowcount
from ..ports import (
    APPLICATION_COLUMNS,
    StoredApplication,
    StoredApplicationEvent,
    StoredApplyProfile,
    StoredPlaybook,
    StoredRunMetric,
)
from ..tables import (
    ApplicationEventRow,
    ApplicationRow,
    ApplyProfileRow,
    AtsPlaybookRow,
    RunMetricRow,
    as_utc,
)

_APP_COLUMNS = [c for c in ApplicationRow.__table__.columns if c.name != "review_screenshot"]
_HAS_SCREENSHOT = ApplicationRow.review_screenshot.is_not(None).label("has_screenshot")


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
        agent=row.agent,
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
        details=dict(row.details or {}),
        answers=dict(row.answers or {}),
        resume_name=row.resume_name,
        resume_type=row.resume_type,
        resume_bytes=None if row.resume_bytes is None else bytes(row.resume_bytes),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _runnable(now: datetime) -> ColumnElement[bool]:
    """Free lease, and either queued (to fill) or approved and not yet submitted (to submit)."""
    free = or_(ApplicationRow.lease_until.is_(None), ApplicationRow.lease_until < now)
    to_fill = ApplicationRow.stage == "queued"
    to_submit = and_(
        ApplicationRow.stage == "ready_to_submit",
        ApplicationRow.approved_at.is_not(None),
        ApplicationRow.submitted_at.is_(None),
    )
    return and_(free, or_(to_fill, to_submit))


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
                )
            )

    async def get_application(self, application_id: str, owner_key: str | None) -> StoredApplication | None:
        stmt = select(*_APP_COLUMNS, _HAS_SCREENSHOT).where(ApplicationRow.id == application_id)
        if owner_key is not None:
            stmt = stmt.where(ApplicationRow.owner_key == owner_key)
        async with self._db.session() as s:
            row = (await s.execute(stmt)).one_or_none()
        return None if row is None else _app(row)

    async def list_applications(self, owner_key: str, limit: int = 200) -> list[StoredApplication]:
        stmt = (
            select(*_APP_COLUMNS, _HAS_SCREENSHOT)
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
                await s.execute(
                    select(*_APP_COLUMNS, _HAS_SCREENSHOT).where(ApplicationRow.id == application_id)
                )
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
                row = (
                    await s.execute(
                        select(*_APP_COLUMNS, _HAS_SCREENSHOT).where(ApplicationRow.id == candidate)
                    )
                ).one()
            return _app(row)
        return None

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

    async def purge_screenshots(self, before: datetime) -> int:
        stmt = (
            update(ApplicationRow)
            .where(ApplicationRow.updated_at < before, ApplicationRow.review_screenshot.is_not(None))
            .values(review_screenshot=None)
        )
        async with self._db.session() as s:
            return rowcount(await s.execute(stmt))

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
                    agent=item.agent,
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
        stmt = select(func.count()).where(RunMetricRow.domain == domain, RunMetricRow.created_at >= since)
        async with self._db.session() as s:
            return int((await s.execute(stmt)).scalar_one())

    async def last_domain_run_at(self, domain: str) -> datetime | None:
        stmt = select(func.max(RunMetricRow.created_at)).where(RunMetricRow.domain == domain)
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

    async def _ensure(self, owner_key: str, user_id: str | None, now: datetime) -> StoredApplyProfile:
        found = await self.get_profile(owner_key)
        if found is not None:
            return found
        stmt = (
            dialect_insert(self._db.dialect, ApplyProfileRow)
            .values(
                owner_key=owner_key,
                user_id=user_id,
                details={},
                answers={},
                resume_name=None,
                resume_type=None,
                resume_bytes=None,
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

    async def put_details(
        self, owner_key: str, *, user_id: str | None, details: dict[str, Any], now: datetime
    ) -> StoredApplyProfile:
        await self._ensure(owner_key, user_id, now)
        stmt = (
            update(ApplyProfileRow)
            .where(ApplyProfileRow.owner_key == owner_key)
            .values(details=dict(details), updated_at=now)
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
        user_id: str | None,
        name: str | None,
        media_type: str | None,
        data: bytes | None,
        now: datetime,
    ) -> StoredApplyProfile:
        await self._ensure(owner_key, user_id, now)
        keep = data is not None
        stmt = (
            update(ApplyProfileRow)
            .where(ApplyProfileRow.owner_key == owner_key)
            .values(
                resume_name=name if keep else None,
                resume_type=media_type if keep else None,
                resume_bytes=data,
                updated_at=now,
            )
        )
        async with self._db.session() as s:
            await s.execute(stmt)
        found = await self.get_profile(owner_key)
        assert found is not None  # noqa: S101
        return found

    async def merge_answers(
        self, owner_key: str, *, user_id: str | None, answers: Mapping[str, Any], now: datetime
    ) -> StoredApplyProfile:
        current = await self._ensure(owner_key, user_id, now)
        merged = {**current.answers, **{key: value for key, value in answers.items()}}
        stmt = (
            update(ApplyProfileRow)
            .where(ApplyProfileRow.owner_key == owner_key)
            .values(answers=merged, updated_at=now)
        )
        async with self._db.session() as s:
            await s.execute(stmt)
        return replace(current, answers=merged, updated_at=now)


__all__ = ["SqlApplyRepo"]
