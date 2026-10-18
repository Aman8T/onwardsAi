"""Prepare (P1): "How do I prepare for this role?" answered with typed blocks instead of the coach's prose.

    assemble    the plan's numbers and evidence, from the graph, in code (gaps, strong, rank, the evidence list)
    schemas     what the three calls are asked to return (citations first), and the JSON Schemas built from it
    verify      the code checks on what the calls return (ids, gap names, numbers, lengths, hours)
    github      the repositories to start a move from, ranked from the stored GitHub digest (nothing fetched)
    errors      what the service refuses, in the person's words
    service     `PrepareService`: the stream, the stored plans, the state, the repos, the headline

No routes, no tables. It sees storage only through `storage.ports` and reuses the posting match's schema helpers.
"""

from tayyari.prepare.errors import (
    AlreadyStrong,
    BadState,
    PlanChanged,
    PlanNotFound,
    RequirementNotFound,
    RoleNotFound,
)
from tayyari.prepare.service import PrepareService, new_plan_id

__all__ = [
    "AlreadyStrong",
    "BadState",
    "PlanChanged",
    "PlanNotFound",
    "PrepareService",
    "RequirementNotFound",
    "RoleNotFound",
    "new_plan_id",
]
