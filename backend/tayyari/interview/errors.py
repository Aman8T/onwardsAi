"""Errors of the practice interview. The sentences are shown to people, so each says what happened and what to do."""

from __future__ import annotations

from tayyari.core.errors import BadRequest, Conflict, NotFound, ServiceUnavailable


class InterviewNotFound(NotFound):
    """No such session for this profile (an unknown id, another profile's id, or one past its 30 days)."""

    code = "interview_not_found"
    user_message = "This practice interview isn't here any more. Practice interviews are kept for 30 days."


class RoleGone(NotFound):
    """The role a plan was asked for is not in the person's searches any more."""

    code = "interview_role_gone"
    user_message = "That role is not in your searches any more. Search again, then practise it."


class GraphRoleGone(NotFound):
    """The graph role a plan was asked for is not among the profile's roles any more (the profile was analysed again)."""

    code = "interview_graph_role_gone"
    user_message = (
        "That role is not on your skill graph any more. Pick a role from your graph, then practise it."
    )


class InterviewMovedOn(Conflict):
    """Two tabs answered at once: the second save found a newer version."""

    code = "interview_moved_on"
    user_message = "This interview moved on in another tab. Reload to carry on."


class InterviewEnded(Conflict):
    """An answer, work or assistant message for a session that has ended."""

    code = "interview_ended"
    user_message = "This interview has ended. Open its debrief, or start a new one."


class NoTemplate(BadRequest):
    """No work sample for this kind of work yet."""

    code = "interview_no_template"
    user_message = "There's no work sample for this kind of role yet. Practise This role instead, built from the posting."


class BadPlan(BadRequest):
    """The plan asked for cannot be made: no role, no question left, nothing to ask about."""

    code = "interview_bad_plan"
    user_message = "That practice interview can't be set up."


class WorkFirst(BadRequest):
    """An answer to a work sample's questions before the work was handed in."""

    code = "interview_work_first"
    user_message = "Hand in your work first. Then the interviewer asks about it."


class ModelUnavailable(ServiceUnavailable):
    """The model failed while the person waited on it (an answer being read, an assistant reply). Nothing was recorded, so
    they can answer again. The sentence is the model client's own, written for people (busy, cut off, not available)."""

    code = "interview_model_unavailable"
    user_message = "Practice couldn't read that just now. Try again in a moment."


class AssistantUnavailable(BadRequest):
    """The assistant is not part of this practice, or its messages are used up."""

    code = "interview_assistant_unavailable"
    user_message = "The assistant isn't part of this practice."


class FixNotFound(NotFound):
    """No Fix step to place or tick: an unknown question, another profile's, a question that was Defended, or a step that
    was taken out."""

    code = "interview_fix_not_found"
    user_message = "That Fix step isn't here any more. Open the debrief again to see its steps."


class FixPlanNotFound(NotFound):
    """The plan a Fix step was to go into is not this profile's (or is gone)."""

    code = "interview_fix_plan_not_found"
    user_message = "That plan isn't here any more. Pick another plan, or keep the step in Practice."


__all__ = [
    "AssistantUnavailable",
    "BadPlan",
    "FixNotFound",
    "FixPlanNotFound",
    "GraphRoleGone",
    "InterviewEnded",
    "InterviewMovedOn",
    "InterviewNotFound",
    "ModelUnavailable",
    "NoTemplate",
    "RoleGone",
    "WorkFirst",
]
