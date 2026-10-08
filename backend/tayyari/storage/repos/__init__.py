"""SQLAlchemy implementations of the repository ports, and the bundle the container hands to services."""

from __future__ import annotations

from dataclasses import dataclass

from tayyari.core.clock import Clock

from ..db import Database
from ..ports import DecisionLogRepo, KvRepo, LimitRepo, LlmCallRepo, PortfolioRepo, ProfileRepo, SessionRepo
from .decisions import SqlDecisionLogRepo
from .kv import SqlKvRepo
from .limits import SqlLimitRepo
from .llm_calls import SqlLlmCallRepo
from .portfolios import SqlPortfolioRepo
from .profiles import SqlProfileRepo
from .sessions import SqlSessionRepo


@dataclass(frozen=True, slots=True)
class Repositories:
    """Every repository, typed by its Protocol (so the bundle can hold real repos or fakes)."""

    profiles: ProfileRepo
    sessions: SessionRepo
    llm_calls: LlmCallRepo
    decisions: DecisionLogRepo
    limits: LimitRepo
    kv: KvRepo
    portfolios: PortfolioRepo


def build_repositories(db: Database, clock: Clock | None = None) -> Repositories:
    """The SQL repositories over ``db``; ``clock`` (default: the system clock) stamps rows and throttles touches."""
    return Repositories(
        profiles=SqlProfileRepo(db, clock),
        sessions=SqlSessionRepo(db, clock),
        llm_calls=SqlLlmCallRepo(db),
        decisions=SqlDecisionLogRepo(db, clock),
        limits=SqlLimitRepo(db),
        kv=SqlKvRepo(db, clock),
        portfolios=SqlPortfolioRepo(db, clock),
    )


__all__ = [
    "Repositories",
    "SqlDecisionLogRepo",
    "SqlKvRepo",
    "SqlLimitRepo",
    "SqlLlmCallRepo",
    "SqlPortfolioRepo",
    "SqlProfileRepo",
    "SqlSessionRepo",
    "build_repositories",
]
