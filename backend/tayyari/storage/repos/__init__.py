"""SQLAlchemy implementations of the repository ports, and the bundle the container hands to services."""

from __future__ import annotations

from dataclasses import dataclass

from tayyari.core.clock import Clock

from ..db import Database
from ..ports import (
    ApplyRepo,
    BillingRepo,
    CardRepo,
    ClaimMarkRepo,
    DecisionLogRepo,
    DeskNoteRepo,
    DiscoverRepo,
    HandoffRepo,
    InterviewRepo,
    JobSearchRepo,
    KvRepo,
    LimitRepo,
    LlmCallRepo,
    MatchRepo,
    PortfolioRepo,
    PostingRepo,
    PrepareRepo,
    ProfileCarryRepo,
    ProfileFactsRepo,
    ProfileRepo,
    ResumeFileRepo,
    ResumeRepo,
    SessionRepo,
    UserFactRepo,
    UserRepo,
    WeeklyRepo,
)
from .apply import SqlApplyRepo
from .billing import SqlBillingRepo
from .card import SqlCardRepo
from .carry import SqlProfileCarryRepo
from .claim_marks import SqlClaimMarkRepo
from .decisions import SqlDecisionLogRepo
from .desk_notes import SqlDeskNoteRepo
from .discover import SqlDiscoverRepo
from .handoff import SqlHandoffRepo
from .interview import SqlInterviewRepo
from .jobsearch import SqlJobSearchRepo
from .kv import SqlKvRepo
from .limits import SqlLimitRepo
from .llm_calls import SqlLlmCallRepo
from .match import SqlMatchRepo, SqlPostingRepo, SqlProfileFactsRepo, SqlUserFactRepo
from .portfolios import SqlPortfolioRepo
from .prepare import SqlPrepareRepo
from .profiles import SqlProfileRepo
from .resume_files import SqlResumeFileRepo
from .resumes import SqlResumeRepo
from .sessions import SqlSessionRepo
from .users import SqlUserRepo
from .weekly import SqlWeeklyRepo


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
    discover: DiscoverRepo
    jobsearch: JobSearchRepo
    interview: InterviewRepo
    prepare: PrepareRepo
    claim_marks: ClaimMarkRepo
    handoff: HandoffRepo
    weekly: WeeklyRepo
    card: CardRepo
    apply: ApplyRepo
    resumes: ResumeRepo
    resume_files: ResumeFileRepo
    desk_notes: DeskNoteRepo
    carry: ProfileCarryRepo


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
        discover=SqlDiscoverRepo(db, clock),
        jobsearch=SqlJobSearchRepo(db),
        interview=SqlInterviewRepo(db),
        prepare=SqlPrepareRepo(db),
        claim_marks=SqlClaimMarkRepo(db),
        handoff=SqlHandoffRepo(db),
        weekly=SqlWeeklyRepo(db),
        card=SqlCardRepo(db),
        apply=SqlApplyRepo(db),
        resumes=SqlResumeRepo(db),
        resume_files=SqlResumeFileRepo(db),
        desk_notes=SqlDeskNoteRepo(db),
        carry=SqlProfileCarryRepo(db),
    )


__all__ = [
    "Repositories",
    "SqlApplyRepo",
    "SqlBillingRepo",
    "SqlCardRepo",
    "SqlClaimMarkRepo",
    "SqlDecisionLogRepo",
    "SqlDeskNoteRepo",
    "SqlDiscoverRepo",
    "SqlHandoffRepo",
    "SqlInterviewRepo",
    "SqlJobSearchRepo",
    "SqlKvRepo",
    "SqlLimitRepo",
    "SqlLlmCallRepo",
    "SqlMatchRepo",
    "SqlPortfolioRepo",
    "SqlPostingRepo",
    "SqlPrepareRepo",
    "SqlProfileCarryRepo",
    "SqlProfileFactsRepo",
    "SqlProfileRepo",
    "SqlResumeFileRepo",
    "SqlResumeRepo",
    "SqlSessionRepo",
    "SqlUserFactRepo",
    "SqlUserRepo",
    "SqlWeeklyRepo",
    "build_repositories",
]
