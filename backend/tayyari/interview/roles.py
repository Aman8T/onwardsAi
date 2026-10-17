"""The one thing the interview needs from the job search: a role it found, with the posting it read."""

from __future__ import annotations

from typing import Protocol

from tayyari.contract.jobsearch import AiJob


class RoleLookup(Protocol):
    """`JobSearchService` satisfies this (its public ``role``). Practice never imports the service itself."""

    async def role(self, profile_id: str, run_id: str, job_id: str) -> tuple[AiJob, str] | None:
        """A shown role of one of this profile's searches and the posting text the search read, or None when the run
        or the role is not this profile's (or is not stored any more)."""
        ...


__all__ = ["RoleLookup"]
