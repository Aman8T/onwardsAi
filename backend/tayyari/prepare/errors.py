"""What Prepare refuses, in the person's words (`core.errors.AppError` subclasses the API turns into JSON)."""

from __future__ import annotations

from typing import Final

from tayyari.core.errors import AppError, BadRequest, Conflict, NotFound

PLAN_NOT_FOUND: Final = "This plan isn't here any more. Open the role on your graph and ask again."
ROLE_NOT_FOUND: Final = "That role isn't on this graph. Open the graph again and pick it from there."
REQUIREMENT_NOT_FOUND: Final = "That requirement isn't part of this role."
ALREADY_STRONG_REQUIREMENT: Final = "You already meet this requirement, so there's nothing to prepare here."
ALREADY_STRONG_ROLE: Final = "Your graph already meets every requirement of this role: nothing to prepare."
PLAN_CHANGED: Final = "This plan changed in another tab. Reload it and try again."
BAD_STATE: Final = "A done task or a link names something this plan doesn't have."
NO_MOVES: Final = "No move came back that the checks could keep. Try again in a moment."
NO_MOVES_CODE: Final = "no_moves"
FAILED_CODE: Final = "failed"


class PlanNotFound(NotFound):
    code = "plan_not_found"
    user_message = PLAN_NOT_FOUND


class RoleNotFound(NotFound):
    code = "role_not_found"
    user_message = ROLE_NOT_FOUND


class RequirementNotFound(NotFound):
    code = "requirement_not_found"
    user_message = REQUIREMENT_NOT_FOUND


class AlreadyStrong(AppError):
    """The requirement (or the whole role) is already met on the graph: there is nothing to plan."""

    status = 400
    code = "already_strong"
    user_message = ALREADY_STRONG_REQUIREMENT


class PlanChanged(Conflict):
    code = "plan_changed"
    user_message = PLAN_CHANGED


class BadState(BadRequest):
    code = "bad_state"
    user_message = BAD_STATE


__all__ = [
    "ALREADY_STRONG_REQUIREMENT",
    "ALREADY_STRONG_ROLE",
    "BAD_STATE",
    "FAILED_CODE",
    "NO_MOVES",
    "NO_MOVES_CODE",
    "PLAN_CHANGED",
    "PLAN_NOT_FOUND",
    "REQUIREMENT_NOT_FOUND",
    "ROLE_NOT_FOUND",
    "AlreadyStrong",
    "BadState",
    "PlanChanged",
    "PlanNotFound",
    "RequirementNotFound",
    "RoleNotFound",
]
