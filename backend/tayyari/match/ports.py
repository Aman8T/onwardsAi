"""What the match needs from storage, as Protocols. The persistence wave (M2) implements them; here the service takes
its inputs as arguments and every port is optional, so every unit runs with the in-memory fakes of the tests.

    PostingCache      a read posting by the hash of its text (a posting pasted twice is read once)
    FactsCache        the portfolio's extracted facts for a profile, keyed by `portfolio.facts.facts_hash`
    UserFactStore     what the person said (answers to questions, chat), as `user` facts
    MatchStore        the stored match result, resume and cover note of one match

Nothing here imports `storage`: ports are defined where they are used, and `storage.repos` conforms to them.
"""

from __future__ import annotations

from typing import Protocol

from tayyari.contract.match import CoverNote, Fact, MatchResult, Posting, ResumeVariant
from tayyari.contract.portfolio import Fact as PortfolioFact


class PostingCache(Protocol):
    async def get(self, posting_hash: str) -> Posting | None:
        """The stored reading of the posting with this hash, or None."""
        ...

    async def put(self, posting: Posting) -> None:
        """Remember a reading (idempotent on ``posting.hash``)."""
        ...


class FactsCache(Protocol):
    async def get(self, profile_id: str, facts_hash: str) -> list[PortfolioFact] | None:
        """The portfolio facts of this profile extracted from sources with this hash, or None."""
        ...

    async def put(self, profile_id: str, facts_hash: str, facts: list[PortfolioFact]) -> None: ...


class UserFactStore(Protocol):
    async def list_for_profile(self, profile_id: str) -> list[Fact]:
        """Every `user` fact the person has given for this profile, oldest first."""
        ...

    async def add(
        self, profile_id: str, fact: Fact, *, question_id: str | None = None, match_id: str | None = None
    ) -> None:
        """Store one fact in the person's own words (idempotent on ``fact.id``)."""
        ...


class MatchStore(Protocol):
    async def save_result(self, match_id: str, result: MatchResult) -> None: ...

    async def load_result(self, match_id: str) -> MatchResult | None: ...

    async def save_resume(self, match_id: str, variant: ResumeVariant) -> None: ...

    async def load_resume(self, match_id: str) -> ResumeVariant | None: ...

    async def save_cover(self, match_id: str, note: CoverNote) -> None: ...

    async def load_cover(self, match_id: str) -> CoverNote | None: ...


__all__ = ["FactsCache", "MatchStore", "PostingCache", "UserFactStore"]
