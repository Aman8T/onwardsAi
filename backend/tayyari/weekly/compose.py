"""Composing one edition of The Leafout Weekly: code chooses and computes, models only read and phrase.

    jobs       `weekly.jobs`: the reader's top 3 openings by the quick fit, the lead read once and checked
    progress   `weekly.progress`: the newest Prepare plan, the closest role, the last practice, one nudge; all code
    news       `weekly.news`: web searches, one slot-filling call, a verifier; any failure is a quiet section
    tip, quote `weekly.copy`: written in code, rotated by the issue number
    headline   `headline_for`: a sentence chosen by code from the counts

Only the lead's read and the news spend money. They run together, once, inside one spend reservation
(``GuardSettings.est_weekly_usd``); when the reservation is refused (the daily ceiling), or the caller says the week's paid
editions are used up, the edition is built without them. An edition is never refused for want of a model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from tayyari.contract.models import Profile
from tayyari.contract.weekly import (
    WeeklyEdition,
    WeeklyJob,
    WeeklyNews,
    WeeklyProgress,
    WeeklySection,
)
from tayyari.core.logging import get_logger
from tayyari.core.settings import WeeklySettings
from tayyari.core.text import tidy
from tayyari.jobsearch.judge import Verdict
from tayyari.llm.errors import SpendCeilingReached
from tayyari.llm.types import CallContext
from tayyari.storage.ports import (
    InterviewRepo,
    PrepareRepo,
    ProfileRepo,
    StoredInterview,
    StoredPlan,
    StoredUser,
    UserRepo,
)

from .copy import quote_for, tip_for
from .jobs import JobDesk, Picked, job_of
from .news import NewsDesk, NewsOutcome
from .progress import build_progress, closest_role

log = get_logger(__name__)

PROFILES_LOOKED_AT: Final = 3
"""A reader's newest profiles whose plans and practice count (they may have analysed again since a plan was written)."""
COUNT_WORDS: Final = {1: "One", 2: "Two", 3: "Three"}
NO_PROFILE_HEADLINE: Final = "Add your resume and The Leafout Weekly fills itself in"
PAYLOAD_KEYS_OUT: Final = ("id", "createdAt", "emailedAt", "readAt")
"""The fields of a `WeeklyEdition` that live in columns of the row, not in its ``payload``."""

Reserve = Callable[[float], AbstractAsyncContextManager[None]]


@dataclass(frozen=True, slots=True)
class Composed:
    edition: WeeklyEdition
    paid: bool
    """True when the paid part (the lead's read and the news) was allowed to run for this edition."""


def headline_for(
    *,
    jobs: list[WeeklyJob],
    progress: WeeklyProgress | None,
    news: list[WeeklyNews],
    sections: list[WeeklySection],
    has_profile: bool,
) -> str:
    """The lead headline, chosen by code from what the edition holds."""
    if not has_profile:
        return NO_PROFILE_HEADLINE
    fitting = [j for j in jobs if j.fit in ("looks_close", "worth_a_look")]
    if fitting:
        n = min(len(fitting), 3)
        return f"{COUNT_WORDS[n]} {'opening fits' if n == 1 else 'openings fit'} your graph this week"
    if jobs:
        return "New on the boards this week, each one a stretch"
    has_plan = progress is not None and progress.planRole is not None
    if "jobs" in sections:
        if has_plan:
            return "A quiet week on the boards: here's your progress"
        if news:
            return "A quiet week on the boards: news from your field"
        return "A quiet week on the boards"
    if has_plan or (progress is not None and progress.nudge):
        return "Your week in progress"
    if news:
        return "News from your field this week"
    return "Something to try this week"


class EditionComposer:
    def __init__(
        self,
        *,
        settings: WeeklySettings,
        jobs: JobDesk,
        news: NewsDesk,
        users: UserRepo,
        profiles: ProfileRepo,
        prepare: PrepareRepo,
        interview: InterviewRepo,
        reserve: Reserve,
        est_usd: float,
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._news = news
        self._users = users
        self._profiles = profiles
        self._prepare = prepare
        self._interview = interview
        self._reserve = reserve
        self._est_usd = est_usd

    async def profile_of(self, user_id: str) -> tuple[Profile | None, list[str]]:
        """The reader's newest profile (None when they have none) and the ids of their newest few."""
        owned = await self._users.list_profiles(user_id, PROFILES_LOOKED_AT)
        if not owned:
            return None, []
        loaded = await self._profiles.get_owned_for_chat(user_id, owned[0].id)
        profile = Profile.model_validate(loaded[0]) if loaded is not None else None
        return profile, [p.id for p in owned]

    async def _progress(
        self, profile: Profile, profile_ids: list[str], now: datetime
    ) -> WeeklyProgress | None:
        plans: list[StoredPlan] = []
        sessions: list[StoredInterview] = []
        for pid in profile_ids:
            plans.extend(await self._prepare.list_for_profile(pid, 1))
            sessions.extend(await self._interview.list(pid, 1))
        plan = max(plans, key=lambda p: p.created_at, default=None)
        return build_progress(plan=plan, roles=profile.roles, sessions=sessions, now=now)

    async def _paid(
        self, profile: Profile, picked: Picked | None, want_news: bool, closest: str | None, now: datetime
    ) -> tuple[Verdict | None, NewsOutcome, bool]:
        """The lead's read and the news, together, inside one reservation. ``(None, empty, False)`` when it is refused."""
        reading = picked is not None and bool(picked.candidates)
        if not reading and not want_news:
            return None, NewsOutcome(), False
        ctx = CallContext(purpose="weekly.judge", profile_id=profile.id)
        try:
            async with self._reserve(self._est_usd):
                verdict, outcome = await asyncio.gather(
                    self._jobs.read_lead(picked, ctx) if picked is not None and reading else _none(),
                    self._news.gather(profile, closest, ctx, now.date()) if want_news else _no_news(),
                )
        except SpendCeilingReached:
            log.info("weekly_paid_skipped", reason="spend_ceiling")
            return None, NewsOutcome(), False
        return verdict, outcome, True

    async def compose(
        self,
        user: StoredUser,
        sections: list[WeeklySection],
        *,
        edition_id: str,
        issue: int,
        week_of: date,
        now: datetime,
        allow_paid: bool,
    ) -> Composed:
        """One edition for ``user``. ``allow_paid=False`` builds it without the lead's read and the news."""
        profile, profile_ids = await self.profile_of(user.id)
        jobs: list[WeeklyJob] = []
        progress: WeeklyProgress | None = None
        news: list[WeeklyNews] = []
        paid = False
        if profile is not None:
            picked = await self._jobs.pick(profile, now) if "jobs" in sections else None
            if "progress" in sections:
                progress = await self._progress(profile, profile_ids, now)
            near = closest_role(profile.roles)
            want_news = "news" in sections and self._settings.news and allow_paid
            verdict: Verdict | None = None
            outcome = NewsOutcome()
            if allow_paid:
                verdict, outcome, paid = await self._paid(
                    profile, picked, want_news, near.title if near is not None else None, now
                )
            news = list(outcome.items)
            if picked is not None:
                candidates = list(picked.candidates)
                if verdict is not None and verdict.dropped == "instructions":
                    log.warning("weekly_anomaly", where="lead_read", listing=candidates[0].posting.listing.id)
                    candidates = candidates[1:]
                    verdict = None
                jobs = [job_of(c, verdict if i == 0 else None) for i, c in enumerate(candidates)]
        name = tidy(profile.person.name) if profile is not None else ""
        edition = WeeklyEdition(
            id=edition_id,
            issue=issue,
            weekOf=week_of.isoformat(),
            headline=headline_for(
                jobs=jobs,
                progress=progress,
                news=news,
                sections=sections,
                has_profile=profile is not None,
            ),
            reader=name or tidy(user.name) or "Reader",
            place=(tidy(profile.person.location) or None) if profile is not None else None,
            jobs=jobs,
            progress=progress,
            news=news,
            tip=tip_for(issue) if "tip" in sections else None,
            quote=quote_for(issue) if "tip" in sections else None,
            createdAt=now.isoformat(),
        )
        return Composed(edition=edition, paid=paid)


async def _none() -> None:
    return None


async def _no_news() -> NewsOutcome:
    return NewsOutcome()


def payload_of(edition: WeeklyEdition) -> dict[str, object]:
    """The edition as stored in the row's ``payload``: everything but the fields that have columns."""
    data = edition.to_dict()
    for key in PAYLOAD_KEYS_OUT:
        data.pop(key, None)
    return data


__all__ = ["Composed", "EditionComposer", "headline_for", "payload_of"]
