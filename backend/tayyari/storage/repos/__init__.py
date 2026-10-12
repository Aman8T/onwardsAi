"""SQLAlchemy implementations of the repository ports, and the bundle the container hands to services."""

from __future__ import annotations

from dataclasses import dataclass

from tayyari.core.clock import Clock

from ..db import Database
from ..ports import (
    BillingRepo,
    DecisionLogRepo,
    KvRepo,
    LimitRepo,
    LlmCallRepo,
    MatchRepo,
    PortfolioRepo,
    PostingRepo,
    ProfileFactsRepo,
    ProfileRepo,
    SessionRepo,
    UserFactRepo,
    UserRepo,
)
from .billing import SqlBillingRepo
from .decisions import SqlDecisionLogRepo
from .kv import SqlKvRepo
from .limits import SqlLimitRepo
from .llm_calls import SqlLlmCallRepo
from .match import SqlMatchRepo, SqlPostingRepo, SqlProfileFactsRepo, SqlUserFactRepo
from .portfolios import SqlPortfolioRepo
from .profiles import SqlProfileRepo
from .sessions import SqlSessionRepo
from .users import SqlUserRepo


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
    users: UserRepo
    billing: BillingRepo
    postings: PostingRepo
    profile_facts: ProfileFactsRepo
    user_facts: UserFactRepo
    matches: MatchRepo


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
        users=SqlUserRepo(db, clock),
        billing=SqlBillingRepo(db, clock),
        postings=SqlPostingRepo(db, clock),
        profile_facts=SqlProfileFactsRepo(db, clock),
        user_facts=SqlUserFactRepo(db, clock),
        matches=SqlMatchRepo(db, clock),
    )


__all__ = [
    "Repositories",
    "SqlBillingRepo",
    "SqlDecisionLogRepo",
    "SqlKvRepo",
    "SqlLimitRepo",
    "SqlLlmCallRepo",
    "SqlMatchRepo",
    "SqlPortfolioRepo",
    "SqlPostingRepo",
    "SqlProfileFactsRepo",
    "SqlProfileRepo",
    "SqlSessionRepo",
    "SqlUserFactRepo",
    "SqlUserRepo",
    "build_repositories",
]
