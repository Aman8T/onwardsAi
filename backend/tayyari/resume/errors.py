"""What the resume routes refuse, as `AppError`s. Unlike the plain ``{"error": sentence}`` body these also carry their
``code``, so the page can tell a resume that is not finalised from one that changed in another tab (both 409)."""

from __future__ import annotations

from tayyari.core.errors import AppError, NotFound


class ResumeError(AppError):
    """A refusal the page reacts to by its code: the body is ``{"error": sentence, "code": code}``."""

    def to_body(self) -> dict[str, str]:
        return {"error": self.user_message, "code": self.code}


class ResumeNotFound(NotFound):
    user_message = "Resume not found"


class RoleNotFound(NotFound):
    """The role is not one of the next roles of this profile's graph."""

    user_message = "That role is not one of this profile's next roles."


class OpRefused(ResumeError):
    """One operation of a PATCH does not fit the draft; nothing of the request was applied."""

    status = 422
    code = "op_refused"
    user_message = "That change does not fit this resume."

    def __init__(self, index: int, reason: str) -> None:
        self.index = index
        self.reason = reason
        super().__init__(f"Change {index + 1} does not fit: {reason}.")


class NotFinalised(ResumeError):
    """The resume has no finalised version yet, so it cannot be downloaded or made the default."""

    status = 409
    code = "not_finalised"
    user_message = (
        "Finalise this resume first: only a finalised resume can be downloaded or made the default."
    )


class ResumeChanged(ResumeError):
    """The draft was written by another request since this one read it."""

    status = 409
    code = "resume_changed"
    user_message = "This resume changed in another tab. Reload it and try again."


__all__ = ["NotFinalised", "OpRefused", "ResumeChanged", "ResumeError", "ResumeNotFound", "RoleNotFound"]
