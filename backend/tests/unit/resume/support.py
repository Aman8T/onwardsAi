"""Shared helpers for the resume tests: the two personas of the match tests (their golden fact books), a few lookups,
and a `ResumeService` over the fakes with a scripted model.

Every sentence the tests type into a resume is a test's own (a figure or a tool no fact has, to prove a check fires);
everything else is read from the personas' fact books.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tayyari.contract.match import Bullet, ExperienceEntry, ResumeVariant
from tayyari.contract.models import Role
from tayyari.match.service import MatchService
from tayyari.resume.service import Holder, ResumeService
from tests.fakes.claude import FakeClaudeClient
from tests.fakes.repos import FakeRepositories
from tests.unit.match.support import CTX, HAIKU, Persona, meera, riya
from tests.unit.portfolio._fixtures_a import scripted_facts

T0 = datetime(2026, 11, 10, 9, 0, 0, tzinfo=UTC)
PID = "a" * 32

INVENTED_FIGURE = "Ran 38 A/B tests on landing pages, lifting conversion by 40%"
"""A typed line whose 40% is in no fact (the block `exp-1.3` of Meera says 38 tests, five winners and 6.4%)."""
FIGURE_LINE = "b-exp-1.3"


class TickingClock:
    """A clock that moves a second every time it is read, so writes are told apart by their time."""

    def __init__(self, start: datetime = T0) -> None:
        self._now = start

    def now(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now

    def monotonic(self) -> float:
        return self._now.timestamp()

    async def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def entries(variant: ResumeVariant) -> list[ExperienceEntry]:
    return [*variant.experience, *variant.projects]


def lines_of(variant: ResumeVariant) -> list[Bullet]:
    return [line for entry in entries(variant) for line in entry.bullets]


def line_ids(variant: ResumeVariant, entry_fact_id: str) -> list[str]:
    return [line.id for line in entry_of(variant, entry_fact_id).bullets]


def entry_of(variant: ResumeVariant, entry_fact_id: str) -> ExperienceEntry:
    return next(e for e in entries(variant) if e.entryFactId == entry_fact_id)


def line(variant: ResumeVariant, line_id: str) -> Bullet:
    return next(b for b in lines_of(variant) if b.id == line_id)


def skill_names(variant: ResumeVariant) -> list[str]:
    return [item.name for group in variant.skills for item in group.items]


def role_of(persona: Persona, role_id: str) -> Role:
    return next(r for r in persona.profile.roles if r.id == role_id)


@dataclass
class Rig:
    """A `ResumeService` over the fakes, for one persona stored under `PID`."""

    persona: Persona
    service: ResumeService
    repos: FakeRepositories
    claude: FakeClaudeClient
    holder: Holder

    async def listing(self) -> Any:
        return await self.service.listing(self.holder, self.persona.profile, self.persona.context, CTX)

    async def path(self, role_id: str) -> Any:
        return await self.service.path(self.holder, self.persona.profile, self.persona.context, role_id, CTX)

    async def stored(self, resume_id: str) -> Any:
        found = await self.repos.resumes.get(resume_id)
        assert found is not None
        return found

    def calls(self, purpose: str) -> int:
        return len(self.claude.calls_for(purpose))  # type: ignore[arg-type]


def rig(
    persona: Persona | None = None,
    *,
    relevance: Any = None,
    user_id: str | None = "u1",
    shares_facts: bool = True,
) -> Rig:
    """The service with a model that extracts the persona's facts and answers the relevance call with ``relevance``
    (a dict, an exception, or a callable; nothing scripted when None, so a relevance call fails the test)."""
    who = persona or meera()
    claude = FakeClaudeClient()
    claude.script_structured("match.facts", lambda call: scripted_facts(who.context), repeat=True)
    if relevance is not None:
        claude.script_structured("match.relevance", relevance, repeat=True)
    repos = FakeRepositories()
    match = MatchService(claude=claude, model=HAIKU, facts_cache=repos.profile_facts)
    service = ResumeService(
        claude=claude,
        model=HAIKU,
        match=match,
        resumes=repos.resumes,
        user_facts=repos.user_facts,
        clock=TickingClock(),
    )
    return Rig(who, service, repos, claude, Holder(PID, user_id, shares_facts))


__all__ = [
    "CTX",
    "FIGURE_LINE",
    "HAIKU",
    "INVENTED_FIGURE",
    "PID",
    "T0",
    "Rig",
    "TickingClock",
    "entries",
    "entry_of",
    "line",
    "line_ids",
    "lines_of",
    "meera",
    "rig",
    "riya",
    "role_of",
    "skill_names",
]
