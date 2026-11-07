"""A dict-backed `ApplyRepo` that behaves like the SQL one (the apply repo contract tests run both).

What it keeps exact: owner scoping (another owner's id reads as unknown), `update_application`'s column check and its
``expected_stage`` guard, `claim_next`'s rule (queued, or approved and not submitted; a free lease; oldest first; excluded
domains skipped), screenshots kept apart from the row, and copies on the way in and out.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tayyari.storage.ports import (
    APPLICATION_COLUMNS,
    StoredApplication,
    StoredApplicationEvent,
    StoredApplyProfile,
    StoredPlaybook,
    StoredRunMetric,
)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _runnable(app: StoredApplication, now: datetime) -> bool:
    free = app.lease_until is None or _utc(app.lease_until) < _utc(now)
    to_fill = app.stage == "queued"
    to_submit = app.stage == "ready_to_submit" and app.approved_at is not None and app.submitted_at is None
    return free and (to_fill or to_submit)


class FakeApplyRepo:
    def __init__(self) -> None:
        self.applications: dict[str, StoredApplication] = {}
        self.screenshots: dict[str, bytes] = {}
        self.events: list[StoredApplicationEvent] = []
        self.playbooks: dict[str, StoredPlaybook] = {}
        self.metrics: list[StoredRunMetric] = []
        self.profiles: dict[str, StoredApplyProfile] = {}

    def _out(self, app: StoredApplication) -> StoredApplication:
        return replace(copy.deepcopy(app), has_screenshot=app.id in self.screenshots)

    # ── applications ──

    async def create_application(self, item: StoredApplication) -> None:
        if item.id in self.applications:
            raise ValueError(f"application {item.id!r} already exists")
        self.applications[item.id] = replace(
            copy.deepcopy(item), created_at=_utc(item.created_at), updated_at=_utc(item.updated_at)
        )

    async def get_application(self, application_id: str, owner_key: str | None) -> StoredApplication | None:
        found = self.applications.get(application_id)
        if found is None or (owner_key is not None and found.owner_key != owner_key):
            return None
        return self._out(found)

    async def list_applications(self, owner_key: str, limit: int = 200) -> list[StoredApplication]:
        mine = [app for app in self.applications.values() if app.owner_key == owner_key]
        mine.sort(key=lambda app: (app.created_at, app.id), reverse=True)
        return [self._out(app) for app in mine[:limit]]

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
        found = self.applications.get(application_id)
        if found is None or (expected_stage is not None and found.stage != expected_stage):
            return None
        updated = replace(found, **copy.deepcopy(dict(changes)), updated_at=_utc(now))
        self.applications[application_id] = updated
        return self._out(updated)

    async def delete_application(self, application_id: str, owner_key: str) -> bool:
        found = self.applications.get(application_id)
        if found is None or found.owner_key != owner_key:
            return False
        del self.applications[application_id]
        self.screenshots.pop(application_id, None)
        self.events = [e for e in self.events if e.application_id != application_id]
        return True

    async def count_created_since(self, owner_key: str, since: datetime) -> int:
        return sum(
            1
            for app in self.applications.values()
            if app.owner_key == owner_key and app.created_at >= _utc(since)
        )

    async def claim_next(
        self, *, now: datetime, lease_owner: str, lease_until: datetime, exclude_domains: Collection[str] = ()
    ) -> StoredApplication | None:
        excluded = set(exclude_domains)
        ready = [
            app for app in self.applications.values() if _runnable(app, now) and app.domain not in excluded
        ]
        if not ready:
            return None
        ready.sort(key=lambda app: (app.updated_at, app.id))
        taken = replace(ready[0], lease_owner=lease_owner, lease_until=_utc(lease_until))
        self.applications[taken.id] = taken
        return self._out(taken)

    async def release(self, application_id: str, lease_owner: str) -> None:
        found = self.applications.get(application_id)
        if found is not None and found.lease_owner == lease_owner:
            self.applications[application_id] = replace(found, lease_owner=None, lease_until=None)

    async def get_screenshot(self, application_id: str, owner_key: str | None) -> bytes | None:
        found = self.applications.get(application_id)
        if found is None or (owner_key is not None and found.owner_key != owner_key):
            return None
        return self.screenshots.get(application_id)

    async def put_screenshot(self, application_id: str, data: bytes | None) -> None:
        if application_id not in self.applications:
            return
        if data is None:
            self.screenshots.pop(application_id, None)
        else:
            self.screenshots[application_id] = bytes(data)

    async def purge_screenshots(self, before: datetime) -> int:
        gone = [
            key
            for key in self.screenshots
            if key in self.applications and self.applications[key].updated_at < _utc(before)
        ]
        for key in gone:
            del self.screenshots[key]
        return len(gone)

    # ── events ──

    async def add_event(self, event: StoredApplicationEvent) -> None:
        self.events.append(replace(copy.deepcopy(event), at=_utc(event.at)))

    async def list_events(self, application_id: str, limit: int = 200) -> list[StoredApplicationEvent]:
        mine = [copy.deepcopy(e) for e in self.events if e.application_id == application_id]
        mine.sort(key=lambda e: (e.at, e.id))
        return mine[:limit]

    # ── playbooks ──

    async def list_playbooks(
        self, *, ats: str | None = None, domain: str | None = None, statuses: Collection[str] | None = None
    ) -> list[StoredPlaybook]:
        found = [
            copy.deepcopy(pb)
            for pb in self.playbooks.values()
            if (ats is None or pb.ats == ats)
            and (domain is None or pb.domain == domain)
            and (statuses is None or pb.status in statuses)
        ]
        found.sort(key=lambda pb: (pb.updated_at, pb.id), reverse=True)
        return found

    async def get_playbook(self, playbook_id: str) -> StoredPlaybook | None:
        found = self.playbooks.get(playbook_id)
        return None if found is None else copy.deepcopy(found)

    async def save_playbook(self, item: StoredPlaybook) -> None:
        existing = self.playbooks.get(item.id)
        created = existing.created_at if existing is not None else _utc(item.created_at)
        self.playbooks[item.id] = replace(
            copy.deepcopy(item), created_at=created, updated_at=_utc(item.updated_at)
        )

    async def record_playbook_result(
        self, playbook_id: str, *, success: bool, now: datetime
    ) -> StoredPlaybook | None:
        found = self.playbooks.get(playbook_id)
        if found is None:
            return None
        updated = replace(
            found,
            successes=found.successes + (1 if success else 0),
            failures=found.failures + (0 if success else 1),
            updated_at=_utc(now),
        )
        self.playbooks[playbook_id] = updated
        return copy.deepcopy(updated)

    async def set_playbook_status(
        self, playbook_id: str, status: str, *, now: datetime
    ) -> StoredPlaybook | None:
        found = self.playbooks.get(playbook_id)
        if found is None:
            return None
        updated = replace(
            found,
            status=status,
            updated_at=_utc(now),
            promoted_at=_utc(now) if status == "active" else found.promoted_at,
        )
        self.playbooks[playbook_id] = updated
        return copy.deepcopy(updated)

    # ── run metrics ──

    async def add_run_metric(self, item: StoredRunMetric) -> None:
        self.metrics.append(replace(copy.deepcopy(item), created_at=_utc(item.created_at)))

    async def list_run_metrics(self, since: datetime, limit: int = 20000) -> list[StoredRunMetric]:
        found = [copy.deepcopy(m) for m in self.metrics if m.created_at >= _utc(since)]
        found.sort(key=lambda m: (m.created_at, m.id), reverse=True)
        return found[:limit]

    async def count_domain_runs_since(self, domain: str, since: datetime) -> int:
        return sum(1 for m in self.metrics if m.domain == domain and m.created_at >= _utc(since))

    async def last_domain_run_at(self, domain: str) -> datetime | None:
        times = [m.created_at for m in self.metrics if m.domain == domain]
        return max(times) if times else None

    # ── profiles ──

    async def get_profile(self, owner_key: str) -> StoredApplyProfile | None:
        found = self.profiles.get(owner_key)
        return None if found is None else copy.deepcopy(found)

    def _ensure(self, owner_key: str, user_id: str | None, now: datetime) -> StoredApplyProfile:
        found = self.profiles.get(owner_key)
        if found is None:
            found = StoredApplyProfile(
                owner_key=owner_key,
                user_id=user_id,
                details={},
                answers={},
                created_at=_utc(now),
                updated_at=_utc(now),
            )
            self.profiles[owner_key] = found
        return found

    async def put_details(
        self, owner_key: str, *, user_id: str | None, details: dict[str, Any], now: datetime
    ) -> StoredApplyProfile:
        current = self._ensure(owner_key, user_id, now)
        updated = replace(current, details=copy.deepcopy(dict(details)), updated_at=_utc(now))
        self.profiles[owner_key] = updated
        return copy.deepcopy(updated)

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
        current = self._ensure(owner_key, user_id, now)
        keep = data is not None
        updated = replace(
            current,
            resume_name=name if keep else None,
            resume_type=media_type if keep else None,
            resume_bytes=None if data is None else bytes(data),
            updated_at=_utc(now),
        )
        self.profiles[owner_key] = updated
        return copy.deepcopy(updated)

    async def merge_answers(
        self, owner_key: str, *, user_id: str | None, answers: Mapping[str, Any], now: datetime
    ) -> StoredApplyProfile:
        current = self._ensure(owner_key, user_id, now)
        updated = replace(
            current, answers={**current.answers, **copy.deepcopy(dict(answers))}, updated_at=_utc(now)
        )
        self.profiles[owner_key] = updated
        return copy.deepcopy(updated)


__all__ = ["FakeApplyRepo"]
