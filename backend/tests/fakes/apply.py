"""A dict-backed `ApplyRepo` that behaves like the SQL one (the apply repo contract tests run both).

What it keeps exact: owner scoping (another owner's id reads as unknown), `update_application`'s column check and its
``expected_stage`` guard, `claim_next`'s rule (queued or approved, never ``submitting``; a free lease; oldest first;
excluded domains skipped), an account on every application and profile (NOT NULL ``user_id``), one application per
``(user_id, posting_key)`` (ValueError where SQL raises IntegrityError), both screenshots kept apart from the row, the
purge of filled values, the answer bank's upsert and its refusal of never-stored keys, and copies on the way in and out.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tayyari.storage.ports import (
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

RUNNABLE_STAGES = ("queued", "approved")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _free(app: StoredApplication, now: datetime) -> bool:
    return app.lease_until is None or _utc(app.lease_until) < _utc(now)


def _runnable(app: StoredApplication, now: datetime) -> bool:
    return _free(app, now) and app.stage in RUNNABLE_STAGES


class FakeApplyRepo:
    def __init__(self) -> None:
        self.applications: dict[str, StoredApplication] = {}
        self.screenshots: dict[str, bytes] = {}
        self.confirmations: dict[str, bytes] = {}
        self.events: list[StoredApplicationEvent] = []
        self.playbooks: dict[str, StoredPlaybook] = {}
        self.metrics: list[StoredRunMetric] = []
        self.profiles: dict[str, StoredApplyProfile] = {}
        self.answers: dict[tuple[str, str], StoredAnswer] = {}

    def _out(self, app: StoredApplication) -> StoredApplication:
        return replace(
            copy.deepcopy(app),
            has_screenshot=app.id in self.screenshots,
            has_confirmation=app.id in self.confirmations,
        )

    def _posting_taken(self, user_id: str, posting_key: str | None, *, other_than: str) -> bool:
        if posting_key is None:
            return False  # NULLs are distinct in a unique index
        return any(
            app.id != other_than and app.user_id == user_id and app.posting_key == posting_key
            for app in self.applications.values()
        )

    # ── applications ──

    async def create_application(self, item: StoredApplication) -> None:
        if item.id in self.applications:
            raise ValueError(f"application {item.id!r} already exists")
        if not item.user_id:
            raise ValueError("an application belongs to an account (user_id is NOT NULL)")
        if self._posting_taken(item.user_id, item.posting_key, other_than=item.id):
            raise ValueError(f"an application for posting {item.posting_key!r} already exists")
        self.applications[item.id] = replace(
            copy.deepcopy(item),
            created_at=_utc(item.created_at),
            updated_at=_utc(item.updated_at),
            has_screenshot=False,
            has_confirmation=False,
        )

    async def find_by_posting(self, user_id: str, posting_key: str) -> StoredApplication | None:
        for app in self.applications.values():
            if app.user_id == user_id and app.posting_key == posting_key:
                return self._out(app)
        return None

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
        if "posting_key" in changes and self._posting_taken(
            found.user_id, changes["posting_key"], other_than=application_id
        ):
            raise ValueError(f"an application for posting {changes['posting_key']!r} already exists")
        updated = replace(found, **copy.deepcopy(dict(changes)), updated_at=_utc(now))
        self.applications[application_id] = updated
        return self._out(updated)

    async def delete_application(
        self, application_id: str, owner_key: str, *, expected_stage: str | None = None
    ) -> bool:
        found = self.applications.get(application_id)
        if found is None or found.owner_key != owner_key:
            return False
        if expected_stage is not None and found.stage != expected_stage:
            return False
        del self.applications[application_id]
        self.screenshots.pop(application_id, None)
        self.confirmations.pop(application_id, None)
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

    async def list_expired(
        self, stages: Collection[str], *, now: datetime, limit: int = 50
    ) -> list[StoredApplication]:
        wanted = set(stages)
        found = [app for app in self.applications.values() if app.stage in wanted and _free(app, now)]
        found.sort(key=lambda app: (app.updated_at, app.id))
        return [self._out(app) for app in found[:limit]]

    async def list_waiting(
        self, user_id: str, stages: Collection[str] = ("waiting_for_you",)
    ) -> list[StoredApplication]:
        wanted = set(stages)
        found = [app for app in self.applications.values() if app.user_id == user_id and app.stage in wanted]
        found.sort(key=lambda app: (app.created_at, app.id))
        return [self._out(app) for app in found]

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

    async def get_confirmation(self, application_id: str, owner_key: str | None) -> bytes | None:
        found = self.applications.get(application_id)
        if found is None or (owner_key is not None and found.owner_key != owner_key):
            return None
        return self.confirmations.get(application_id)

    async def put_confirmation(self, application_id: str, data: bytes | None) -> None:
        if application_id not in self.applications:
            return
        if data is None:
            self.confirmations.pop(application_id, None)
        else:
            self.confirmations[application_id] = bytes(data)

    async def purge_filled(self, before: datetime) -> int:
        changed = 0
        for app_id, app in list(self.applications.items()):
            if app.stage not in FILLED_TERMINAL_STAGES or app.updated_at >= _utc(before):
                continue
            typed = app.job is not None and "typed" in app.job
            if not (
                app_id in self.screenshots
                or app_id in self.confirmations
                or app.review is not None
                or app.needs is not None
                or typed
            ):
                continue
            self.screenshots.pop(app_id, None)
            self.confirmations.pop(app_id, None)
            job = None if app.job is None else {k: v for k, v in app.job.items() if k != "typed"}
            self.applications[app_id] = replace(app, review=None, needs=None, job=job)
            changed += 1
        return changed

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
        return sum(
            1
            for m in self.metrics
            if m.domain == domain and m.created_at >= _utc(since) and m.outcome != NOT_ON_SITE_OUTCOME
        )

    async def last_domain_run_at(self, domain: str) -> datetime | None:
        times = [
            m.created_at for m in self.metrics if m.domain == domain and m.outcome != NOT_ON_SITE_OUTCOME
        ]
        return max(times) if times else None

    # ── profiles ──

    async def get_profile(self, owner_key: str) -> StoredApplyProfile | None:
        found = self.profiles.get(owner_key)
        return None if found is None else copy.deepcopy(found)

    def _ensure(self, owner_key: str, user_id: str, now: datetime) -> StoredApplyProfile:
        found = self.profiles.get(owner_key)
        if found is None:
            if not user_id:
                raise ValueError("a profile belongs to an account (user_id is NOT NULL)")
            found = StoredApplyProfile(
                owner_key=owner_key,
                user_id=user_id,
                created_at=_utc(now),
                updated_at=_utc(now),
            )
            self.profiles[owner_key] = found
        return found

    def _set(self, owner_key: str, user_id: str, now: datetime, **values: Any) -> StoredApplyProfile:
        current = self._ensure(owner_key, user_id, now)
        updated = replace(current, **copy.deepcopy(values), updated_at=_utc(now))
        self.profiles[owner_key] = updated
        return copy.deepcopy(updated)

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
        return self._set(
            owner_key,
            user_id,
            now,
            resume_name=name if keep else None,
            resume_type=media_type if keep else None,
            resume_bytes=None if data is None else bytes(data),
            resume_sha256=sha256 if keep else None,
        )

    async def set_self_id(self, user_id: str, on: bool, *, now: datetime) -> StoredApplyProfile:
        return self._set(account_owner_key(user_id), user_id, now, self_id_on_forms=on)

    async def mark_first_check(self, user_id: str, *, now: datetime) -> StoredApplyProfile:
        return self._set(account_owner_key(user_id), user_id, now, first_check_at=_utc(now))

    async def set_consent(self, user_id: str, *, now: datetime) -> StoredApplyProfile:
        return self._set(account_owner_key(user_id), user_id, now, consent_at=_utc(now))

    # ── answer bank ──

    async def list_answers(self, user_id: str) -> list[StoredAnswer]:
        mine = [copy.deepcopy(a) for (uid, _), a in self.answers.items() if uid == user_id]
        mine.sort(key=lambda a: a.question_key)
        return mine

    async def get_answers(self, user_id: str, keys: Collection[str]) -> dict[str, StoredAnswer]:
        return {
            key: copy.deepcopy(self.answers[(user_id, key)])
            for key in set(keys)
            if (user_id, key) in self.answers
        }

    async def put_answer(self, item: StoredAnswer) -> StoredAnswer:
        if is_never_stored(item.question_key):
            raise ValueError(f"{item.question_key!r} is never stored in the answer bank")
        existing = self.answers.get((item.user_id, item.question_key))
        stored = replace(
            copy.deepcopy(item),
            answered_at=_utc(item.answered_at),
            confirmed_at=None if item.confirmed_at is None else _utc(item.confirmed_at),
            created_at=existing.created_at if existing is not None else _utc(item.created_at),
            updated_at=_utc(item.updated_at),
        )
        self.answers[(item.user_id, item.question_key)] = stored
        return copy.deepcopy(stored)

    async def confirm_answer(self, user_id: str, question_key: str, *, now: datetime) -> StoredAnswer | None:
        found = self.answers.get((user_id, question_key))
        if found is None:
            return None
        updated = replace(found, confirmed_at=_utc(now), updated_at=_utc(now))
        self.answers[(user_id, question_key)] = updated
        return copy.deepcopy(updated)

    async def delete_answer(self, user_id: str, question_key: str) -> bool:
        return self.answers.pop((user_id, question_key), None) is not None


__all__ = ["FakeApplyRepo"]
