"""What the auto-apply routes call: the person's details, applications (create from a link or a match, list, read), answers to
open questions, the approval that alone lets the worker submit, manual stage changes and retries.

Every read and write is scoped by ``owner`` (``u:<user id>`` or ``a:<hash>``): another owner's application reads as unknown.
Starting applications is limited per owner and UTC day (`ApplySettings.max_per_day`).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tayyari.apply import stages
from tayyari.apply.answers import question_key
from tayyari.apply.intake import PageFetcher, intake
from tayyari.contract.apply import (
    APPLY_ANSWER_MAX_CHARS,
    APPLY_NOTE_MAX_CHARS,
    APPLY_URL_MAX_CHARS,
    AnswerNeedsRequest,
    ApplicantDetails,
    ApplicantProfile,
    ApplicationDetail,
    ApplicationEvent,
    ApplicationList,
    ApplicationReview,
    ApplicationSummary,
    CreateApplicationRequest,
    NeedsInput,
)
from tayyari.core.clock import Clock
from tayyari.core.errors import BadRequest, Conflict, NotFound, RateLimited
from tayyari.core.settings import Settings
from tayyari.storage.ports import ApplyRepo, StoredApplication, StoredApplyProfile

NOT_FOUND = "Application not found"
MANUAL_REASON = (
    "This site does not allow automated applications. Apply on the site; Leafout keeps track of it here."
)


@dataclass(frozen=True, slots=True)
class MatchInfo:
    """What a posting match lends a new application (read by the api layer through the match's own code)."""

    match_id: str
    profile_id: str
    title: str | None = None
    company: str | None = None
    location: str | None = None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def summary_of(app: StoredApplication) -> ApplicationSummary:
    return ApplicationSummary(
        id=app.id, stage=app.stage, source=app.source, ats=app.ats if app.ats in ("greenhouse", "lever", "ashby", "workday") else "unknown",  # type: ignore[arg-type]
        domain=app.domain, jobUrl=app.job_url, createdAt=app.created_at.isoformat(), updatedAt=app.updated_at.isoformat(),
        needsCount=sum(1 for n in app.needs or [] if n.get("required")), hasScreenshot=app.has_screenshot,
        company=app.company, title=app.title, location=app.location, matchId=app.match_id,
        approvedAt=_iso(app.approved_at), stageReason=app.stage_reason,
    )  # fmt: skip


def profile_of(stored: StoredApplyProfile | None) -> ApplicantProfile:
    if stored is None:
        return ApplicantProfile(details=ApplicantDetails(), hasResume=False, savedAnswers=0)
    return ApplicantProfile(
        details=ApplicantDetails.model_validate(stored.details), hasResume=stored.resume_bytes is not None,
        savedAnswers=len(stored.answers), resumeName=stored.resume_name, updatedAt=stored.updated_at.isoformat(),
    )  # fmt: skip


class ApplyService:
    def __init__(self, repo: ApplyRepo, settings: Settings, clock: Clock, fetcher: PageFetcher) -> None:
        self.repo, self.settings, self.clock, self.fetcher = repo, settings, clock, fetcher

    # ── the person's details ──

    async def profile(self, owner: str) -> ApplicantProfile:
        return profile_of(await self.repo.get_profile(owner))

    async def put_details(
        self, owner: str, user_id: str | None, details: ApplicantDetails
    ) -> ApplicantProfile:
        clean = {k: v.strip()[:300] for k, v in details.to_dict().items() if isinstance(v, str) and v.strip()}
        return profile_of(
            await self.repo.put_details(owner, user_id=user_id, details=clean, now=self.clock.now())
        )

    async def put_resume(
        self, owner: str, user_id: str | None, name: str | None, media_type: str | None, data: bytes | None
    ) -> ApplicantProfile:
        stored = await self.repo.put_resume(
            owner, user_id=user_id, name=name, media_type=media_type, data=data, now=self.clock.now()
        )
        return profile_of(stored)

    # ── applications ──

    async def _get(self, owner: str, application_id: str) -> StoredApplication:
        found = await self.repo.get_application(application_id, owner)
        if found is None:
            raise NotFound(NOT_FOUND)
        return found

    async def detail(self, owner: str, application_id: str) -> ApplicationDetail:
        return await self._detail(await self._get(owner, application_id))

    async def _detail(self, app: StoredApplication) -> ApplicationDetail:
        events = [
            ApplicationEvent(at=e.at.isoformat(), kind=e.kind, source=e.source, fromStage=e.from_stage, toStage=e.to_stage,  # type: ignore[arg-type]
                             note=(e.detail or {}).get("note"))
            for e in await self.repo.list_events(app.id)
        ]  # fmt: skip
        base = summary_of(app).model_dump()
        return ApplicationDetail(
            **base, needs=[NeedsInput.model_validate(n) for n in app.needs or []], events=events, applyUrl=app.apply_url,
            manualUrl=app.manual_url, description=(app.job or {}).get("description"), submittedAt=_iso(app.submitted_at),
            review=ApplicationReview.model_validate(app.review) if app.review else None,
        )  # fmt: skip

    async def list(self, owner: str) -> ApplicationList:
        return ApplicationList(applications=[summary_of(a) for a in await self.repo.list_applications(owner)])

    async def create(
        self, owner: str, user_id: str | None, body: CreateApplicationRequest, match: MatchInfo | None = None
    ) -> ApplicationDetail:
        if len(body.url) > APPLY_URL_MAX_CHARS:
            raise BadRequest("That link is too long.")
        now = self.clock.now()
        cap = self.settings.apply.max_per_day
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if cap and await self.repo.count_created_since(owner, day) >= cap:
            raise RateLimited(f"You can start {cap} applications a day. Try again tomorrow.")
        try:
            read = await intake(body.url, self.fetcher, self.settings.apply.manual_only_domains)
        except ValueError as exc:
            raise BadRequest(str(exc)) from None
        job: dict[str, Any] = {"description": read.job.description, "typed": {}}
        if read.fetch_error:
            job["fetchError"] = read.fetch_error
        app = StoredApplication(
            id="app_" + secrets.token_hex(12), owner_key=owner, user_id=user_id,
            profile_id=match.profile_id if match else body.profileId, match_id=match.match_id if match else None,
            source="match" if match else "link", job_url=read.url, domain=read.domain,
            ats="unknown" if read.manual_only else read.fingerprint.ats,
            stage="needs_user" if read.manual_only else "queued", created_at=now, updated_at=now,
            apply_url=None if read.manual_only else read.fingerprint.apply_url,
            manual_url=read.url if read.manual_only else None,
            company=(read.job.company or (match.company if match else None) or None),
            title=(read.job.title or (match.title if match else None) or None),
            location=read.job.location or (match.location if match else None), job=job,
            stage_reason=MANUAL_REASON if read.manual_only else None,
        )  # fmt: skip
        await self.repo.create_application(app)
        await stages.note(
            self.repo,
            app.id,
            "Started from a match." if match else "Started from a link.",
            source="user",
            now=now,
            kind="created",
        )
        return await self._detail(app)

    async def answer(
        self, owner: str, user_id: str | None, application_id: str, body: AnswerNeedsRequest
    ) -> ApplicationDetail:
        app = await self._get(owner, application_id)
        if app.stage not in ("needs_user", "ready_to_submit", "failed"):
            raise Conflict("This application is not waiting for answers.")
        labels = {str(n.get("key")): str(n.get("label", "")) for n in app.needs or []}
        typed = dict((app.job or {}).get("typed") or {})
        kept: dict[str, Any] = {}
        now = self.clock.now()
        for item in body.answers:
            value = item.value.strip()[:APPLY_ANSWER_MAX_CHARS]
            if not value:
                continue
            typed[item.key] = value
            if body.remember and item.key in labels:
                kept[question_key(labels[item.key])] = {
                    "question": labels[item.key][:300],
                    "answer": value,
                    "at": now.isoformat(),
                }
        if kept:
            await self.repo.merge_answers(owner, user_id=user_id, answers=kept, now=now)
        job = {**(app.job or {}), "typed": typed}
        moved = await stages.move(self.repo, app, "queued", source="user", now=now, note="Answered and queued again.",
                                  changes={"job": job, "approved_at": None, "approved_digest": None, "stage_reason": None})  # fmt: skip
        if moved is None:
            raise Conflict("The application changed; reload it.")
        return await self._detail(moved)

    async def approve(self, owner: str, application_id: str, digest: str) -> ApplicationDetail:
        """The person approves exactly the reviewed values: the digest must be the review's. Only this lets the worker submit."""
        app = await self._get(owner, application_id)
        if app.stage != "ready_to_submit" or not app.review_digest:
            raise Conflict("There is nothing to approve: the form is not filled and reviewed.")
        if digest != app.review_digest:
            raise Conflict("The form changed since you looked at it. Review it again.")
        if any(n.get("required") for n in app.needs or []):
            raise Conflict("Answer the open questions first.")
        now = self.clock.now()
        updated = await self.repo.update_application(
            app.id, {"approved_at": now, "approved_digest": digest}, now=now, expected_stage="ready_to_submit"
        )
        if updated is None:
            raise Conflict("The application changed; reload it.")
        await stages.note(
            self.repo, app.id, "Approved for submission.", source="user", now=now, kind="approved"
        )
        return await self._detail(updated)

    async def set_stage(
        self, owner: str, application_id: str, stage: str, note: str | None
    ) -> ApplicationDetail:
        app = await self._get(owner, application_id)
        if not stages.allowed(app.stage, stage, "user"):
            raise BadRequest(
                f"An application in {app.stage.replace('_', ' ')} cannot be moved to {stage.replace('_', ' ')}."
            )
        changes: dict[str, Any] = {"stage_reason": None}
        if stage in stages.TRACKING:
            changes.update(approved_at=None, approved_digest=None)
        moved = await stages.move(self.repo, app, stage, source="user", now=self.clock.now(),
                                  note=(note or "").strip()[:APPLY_NOTE_MAX_CHARS] or None, changes=changes)  # fmt: skip
        if moved is None:
            raise Conflict("The application changed; reload it.")
        return await self._detail(moved)

    async def retry(self, owner: str, application_id: str) -> ApplicationDetail:
        return await self.set_stage(owner, application_id, "queued", "Tried again.")

    async def delete(self, owner: str, application_id: str) -> None:
        if not await self.repo.delete_application(application_id, owner):
            raise NotFound(NOT_FOUND)

    async def screenshot(self, owner: str, application_id: str) -> bytes:
        data = await self.repo.get_screenshot(application_id, owner)
        if data is None:
            raise NotFound("No screenshot yet")
        return data


__all__ = ["MANUAL_REASON", "NOT_FOUND", "ApplyService", "MatchInfo", "profile_of", "summary_of"]
