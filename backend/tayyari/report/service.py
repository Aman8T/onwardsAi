"""The report's owner side (N1): what the owner sheet shows, the choices it saves, the link, and keeping the snapshot current.
No model is called; `report.compose` works everything out in code from the account's newest owned profile.

The rules (docs/roadmap.md N1), the same as the radar card's where they overlap:

- Only an account that owns the profile can share. The shared sample and an anonymous graph get a view with ``blocked`` and
  no switch; another account's profile is "not found", exactly as `require_profile_access` answers it.
- One report per account, drawn from its newest owned profile (analysing again makes a new profile).
- The link is 12 random characters of ``[a-z0-9]``, new each time sharing is turned on; "Make a new link" replaces it and the
  old one stops at once. It is the only key: never a username or a profile id.
- What is stored is the public content, already redacted by the owner's choices; the version changes only when it does.
- The employer names to hide come from the person's own records: the ``org`` of their resume drafts and fact books and of
  their own-words facts. ``orgsFound`` says how many, so the sheet can warn when none were found.
"""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Final

from pydantic import ValidationError

from tayyari.contract.models import Profile
from tayyari.contract.report import (
    DEFAULT_CHOICES,
    SLUG_LEN,
    ReportBlocked,
    ReportChoices,
    ReportContent,
    ReportNewLink,
    ReportUpdate,
    ReportView,
)
from tayyari.core.clock import Clock
from tayyari.core.errors import AppError, BadRequest, NotFound
from tayyari.core.logging import get_logger
from tayyari.report.compose import compose, org_names, version_of
from tayyari.storage.ports import (
    ProfileRepo,
    ReportRepo,
    ResumeRepo,
    StoredReportSnapshot,
    UserFactRepo,
    UserRepo,
)

log = get_logger(__name__)

SLUG_ALPHABET: Final = string.ascii_lowercase + string.digits
SLUG_RE: Final = re.compile(rf"^[a-z0-9]{{{SLUG_LEN}}}$")

PROFILE_NOT_FOUND: Final = "Profile not found"
BLOCKED_SAMPLE: Final = "Sharing needs your own graph. This one is the sample everyone sees."
BLOCKED_ANONYMOUS: Final = "Sign in first, so the report is yours and you can turn it off later."
NO_GRAPH: Final = "Your account has no graph to make a report from yet. Analyse your resume first."
QUOTE_NOT_OFFERED: Final = (
    "One of those lines isn't on your report any more. Open it again and choose from there."
)
NOT_SHARED: Final = "Turn sharing on first; then there is a link to replace."


class ReportProfileNotFound(NotFound):
    code = "profile_not_found"
    user_message = PROFILE_NOT_FOUND


class ReportSharingBlocked(AppError):
    """The sample, or a graph no account owns: there is no report to share."""

    status = 403
    code = "report_blocked"
    user_message = BLOCKED_SAMPLE


class ReportNoGraph(NotFound):
    code = "no_graph"
    user_message = NO_GRAPH


class QuoteNotOffered(BadRequest):
    code = "quote_not_offered"
    user_message = QUOTE_NOT_OFFERED


class ReportNotShared(BadRequest):
    code = "not_shared"
    user_message = NOT_SHARED


def new_slug() -> str:
    """12 random characters of ``[a-z0-9]`` (36^12 links: walking them is not a way in)."""
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(SLUG_LEN))


class ReportService:
    """The owner's report. ``public_url`` is the app's origin the links are made from (``settings.auth.public_url``);
    ``offer_url`` is the paid step's link (``settings.report.offer_url``), empty for none."""

    def __init__(
        self,
        *,
        profiles: ProfileRepo,
        users: UserRepo,
        reports: ReportRepo,
        resumes: ResumeRepo,
        user_facts: UserFactRepo,
        clock: Clock,
        public_url: str,
        offer_url: str = "",
        slugs: Callable[[], str] = new_slug,
    ) -> None:
        self._profiles = profiles
        self._users = users
        self._reports = reports
        self._resumes = resumes
        self._user_facts = user_facts
        self._clock = clock
        self._public = public_url.rstrip("/")
        self._offer = offer_url.strip()
        self._slugs = slugs

    # ───────────────────────── the owner's routes ─────────────────────────

    async def view(self, user_id: str | None, profile_id: str) -> ReportView:
        """The owner sheet: the choices, what the report offers and what a visitor sees, and its links while shared. The
        sample and an anonymous graph get ``blocked`` (and nothing is stored)."""
        blocked = await self._check(user_id, profile_id)
        if blocked is not None or user_id is None:
            profile = await self._load(profile_id)
            if profile is None:
                raise ReportProfileNotFound()
            orgs = await self._orgs(profile.id)
            return self._view(profile, DEFAULT_CHOICES, orgs, None, blocked=blocked or "anonymous")
        stored = await self.refresh(user_id)
        profile = await self._newest(user_id)
        choices = _choices(stored.choices) if stored is not None else DEFAULT_CHOICES
        return self._view(profile, choices, await self._orgs(profile.id), stored)

    async def update(self, user_id: str | None, body: ReportUpdate) -> ReportView:
        """Save the choices and redraw. A hidden quote must be one the report offers; turning sharing on makes a link,
        turning it off removes it."""
        blocked = await self._check(user_id, body.profileId)
        if blocked is not None or user_id is None:
            raise _blocked(blocked or "anonymous")
        profile = await self._newest(user_id)
        orgs = await self._orgs(profile.id)
        offered, _ = compose(profile, body.choices, orgs)
        quote_ids = {q.id for r in offered.roles for q in r.quotes}
        hidden = list(dict.fromkeys(body.choices.hiddenQuotes))
        if any(q not in quote_ids for q in hidden):
            raise QuoteNotOffered()
        choices = body.choices.model_copy(update={"hiddenQuotes": hidden})
        before = await self._reports.get(user_id)
        if not body.shared:
            slug = None
        elif before is not None and before.shared and before.slug is not None:
            slug = before.slug
        else:
            slug = self._slugs()
        stored = self._snapshot(user_id, profile, choices, orgs, before, body.shared, slug)
        await self._reports.save(stored)
        if before is None or before.shared != body.shared:
            log.info("report_sharing", shared=body.shared)
        return self._view(profile, choices, orgs, stored)

    async def new_link(self, user_id: str | None, body: ReportNewLink) -> ReportView:
        """A new slug for a shared report; the old link stops at once."""
        blocked = await self._check(user_id, body.profileId)
        if blocked is not None or user_id is None:
            raise _blocked(blocked or "anonymous")
        before = await self.refresh(user_id)
        if before is None or not before.shared:
            raise ReportNotShared()
        stored = replace(before, slug=self._slugs())
        await self._reports.save(stored)
        log.info("report_new_link")
        profile = await self._newest(user_id)
        return self._view(profile, _choices(stored.choices), await self._orgs(profile.id), stored)

    # ───────────────────────── keeping it current ─────────────────────────

    async def refresh(self, user_id: str) -> StoredReportSnapshot | None:
        """Redraw the account's report from its newest profile, keeping its choices (a hidden quote the new graph no longer
        has is dropped). Writes only when something changed; None when the account has no report yet."""
        before = await self._reports.get(user_id)
        if before is None:
            return None
        owned = await self._users.list_profiles(user_id, 1)
        if not owned:
            return before
        profile = await self._load(owned[0].id)
        if profile is None:
            return before
        orgs = await self._orgs(profile.id)
        choices = _choices(before.choices)
        offered, _ = compose(profile, choices, orgs)
        quote_ids = {q.id for r in offered.roles for q in r.quotes}
        choices = choices.model_copy(
            update={"hiddenQuotes": [q for q in choices.hiddenQuotes if q in quote_ids]}
        )
        after = self._snapshot(user_id, profile, choices, orgs, before, before.shared, before.slug)
        if after == before:
            return before
        await self._reports.save(after)
        log.info(
            "report_refreshed",
            new_profile=after.profile_id != before.profile_id,
            redrawn=after.version != before.version,
        )
        return after

    # ───────────────────────── the public side (read by the routes) ─────────────────────────

    async def shared(self, slug: str) -> StoredReportSnapshot | None:
        """The shared report behind a link, or None for a slug of the wrong shape, an unknown one or one turned off."""
        if not SLUG_RE.fullmatch(slug):
            return None
        return await self._reports.get_by_slug(slug)

    def content(self, stored: StoredReportSnapshot) -> ReportContent:
        """The public content of a stored report, exactly as its page shows it."""
        return ReportContent.model_validate(stored.content)

    def links(self, stored: StoredReportSnapshot) -> dict[str, str]:
        """``pageUrl`` and ``ogUrl`` of a shared report."""
        slug = stored.slug
        if slug is None:
            raise ValueError("a report that is not shared has no links")
        return {
            "pageUrl": f"{self._public}/next/{slug}",
            "ogUrl": f"{self._public}/og/next/{slug}-{stored.version}.png",
        }

    @property
    def offer_url(self) -> str | None:
        return self._offer or None

    # ───────────────────────── inside ─────────────────────────

    async def _check(self, user_id: str | None, profile_id: str) -> ReportBlocked | None:
        """None when ``user_id`` owns the profile; the reason when there is nothing to share; `ReportProfileNotFound` for
        an unknown profile or another account's (the two cannot be told apart)."""
        access = await self._profiles.get_access(profile_id)
        if access is None:
            raise ReportProfileNotFound()
        if access.is_sample:
            return "sample"
        if access.user_id is None:
            return "anonymous"
        if access.user_id != user_id:
            raise ReportProfileNotFound()
        return None

    async def _load(self, profile_id: str) -> Profile | None:
        doc = await self._profiles.get_profile(profile_id)
        if doc is None:
            return None
        try:
            return Profile.model_validate(doc)
        except ValidationError:
            log.warning("report_profile_unreadable")
            return None

    async def _newest(self, user_id: str) -> Profile:
        owned = await self._users.list_profiles(user_id, 1)
        profile = await self._load(owned[0].id) if owned else None
        if profile is None:
            raise ReportNoGraph()
        return profile

    async def _orgs(self, profile_id: str) -> list[str]:
        """The employer names the person's own records hold: their resume drafts, finals and fact books, and their
        own-words facts."""
        blobs: list[Any] = []
        for resume in await self._resumes.list_for_profile(profile_id):
            blobs.extend([resume.draft, resume.final or {}, resume.facts])
        for fact in await self._user_facts.list_for_profile(profile_id):
            blobs.append(fact.model_dump(mode="json"))
        return org_names(blobs)

    def _snapshot(
        self,
        user_id: str,
        profile: Profile,
        choices: ReportChoices,
        orgs: list[str],
        before: StoredReportSnapshot | None,
        shared: bool,
        slug: str | None,
    ) -> StoredReportSnapshot:
        _, public = compose(profile, choices, orgs)
        version = version_of(public)
        now = self._clock.now()
        changed = before is None or before.version != version
        return StoredReportSnapshot(
            user_id=user_id,
            profile_id=profile.id,
            slug=slug if shared else None,
            shared=shared,
            choices=choices.model_dump(mode="json"),
            content=public.model_dump(mode="json"),
            version=version,
            created_at=before.created_at if before is not None else now,
            updated_at=now if changed or before is None else before.updated_at,
        )

    def _view(
        self,
        profile: Profile,
        choices: ReportChoices,
        orgs: list[str],
        stored: StoredReportSnapshot | None,
        *,
        blocked: ReportBlocked | None = None,
    ) -> ReportView:
        offered, public = compose(profile, choices, orgs)
        view = ReportView(
            choices=choices,
            offered=offered,
            public=public,
            orgsFound=len(orgs),
            blocked=blocked,
            offerUrl=self.offer_url,
        )
        if stored is None or not stored.shared or stored.slug is None:
            return view
        return view.model_copy(
            update={**self.links(stored), "slug": stored.slug, "updatedAt": stored.updated_at.isoformat()}
        )


def _choices(doc: dict[str, Any]) -> ReportChoices:
    """Stored choices, or the defaults when they no longer validate (a field added later starts at its default)."""
    try:
        return ReportChoices.model_validate({**DEFAULT_CHOICES.model_dump(), **doc})
    except ValidationError:
        log.warning("report_choices_unreadable")
        return DEFAULT_CHOICES


def _blocked(reason: ReportBlocked) -> ReportSharingBlocked:
    return ReportSharingBlocked(BLOCKED_ANONYMOUS if reason == "anonymous" else BLOCKED_SAMPLE)


__all__ = [
    "BLOCKED_ANONYMOUS",
    "BLOCKED_SAMPLE",
    "SLUG_ALPHABET",
    "SLUG_RE",
    "QuoteNotOffered",
    "ReportNoGraph",
    "ReportNotShared",
    "ReportProfileNotFound",
    "ReportService",
    "ReportSharingBlocked",
    "new_slug",
]
