"""Practice the interview (I1): plans, the interviewer loop, the judge and the debrief. See the I1 section of
docs/roadmap.md and shared/interview.ts.

    plan      code only: the slots of an interview from the person's evidence, a role's asks and gaps (a search's), a
              graph role's requirements, or a work sample
    phrase    one model call phrases the questions; code checks every name and number and falls back to templates
    router    code decides what a turn is (repeat, skip, don't know, dispute, too short) without a model
    judge     one small call per answer: the model quotes the answer first, code checks the quote and picks the verdict
    flow      fixed rules pick the next question; verdicts are words, never a score
    debrief   counts, the first thing to work on and the summary in code; Say, Avoid and Fix checked against the person's words
    service   the public API; a session is one JSON document saved with a version number

Layering: reuses the job search's evidence lines and quote checks and the portfolio's sentence anchors; the job search's
role lookup comes in through a Protocol (`RoleLookup`). Nothing imports this package except the API and the container.
"""

from tayyari.interview.errors import (
    AssistantUnavailable,
    BadPlan,
    GraphRoleGone,
    InterviewEnded,
    InterviewMovedOn,
    InterviewNotFound,
    NoTemplate,
    RoleGone,
    WorkFirst,
)
from tayyari.interview.roles import RoleLookup
from tayyari.interview.service import InterviewService

__all__ = [
    "AssistantUnavailable",
    "BadPlan",
    "GraphRoleGone",
    "InterviewEnded",
    "InterviewMovedOn",
    "InterviewNotFound",
    "InterviewService",
    "NoTemplate",
    "RoleGone",
    "RoleLookup",
    "WorkFirst",
]
