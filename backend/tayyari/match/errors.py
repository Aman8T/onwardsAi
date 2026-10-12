"""Failures the match services raise on purpose, each with a sentence a person may read."""

from __future__ import annotations

from tayyari.core.errors import AppError, BadRequest


class PostingTextError(BadRequest):
    """The pasted text cannot be read as a posting (empty, far too short or far too long)."""

    code = "posting_text"
    user_message = "Paste the job posting as text, including its requirements."


class NoRequirementsFound(AppError):
    """The posting was read but no requirement survived the checks (nothing to match against)."""

    status = 422
    code = "no_requirements"
    user_message = (
        "We couldn't find any requirements in that text. Paste the full posting, including the "
        "requirements or qualifications section."
    )


class ExportBlocked(AppError):
    """The resume still has decisions waiting for the person (a figure, a stretch or a term to confirm)."""

    status = 409
    code = "export_blocked"
    user_message = "Settle the open questions on your resume before you download it."


class CoverUnavailable(AppError):
    """The cover note could not be written from the evidence (nothing to say that the facts support)."""

    status = 422
    code = "cover_unavailable"
    user_message = "There is not enough supported evidence yet to write a cover note for this posting."


class PostingLinkBlocked(AppError):
    """The link is on a job board that forbids automated reading (LinkedIn, Naukri ...): it is never fetched."""

    status = 422
    code = "posting_link_blocked"
    user_message = "That site does not allow automated reading. Open the posting there, copy its text and paste it here instead."


class PostingLinkUnreadable(AppError):
    """The link could not be read as a page (not found, private, a login wall, a bot check ...)."""

    status = 422
    code = "posting_link_unreadable"
    user_message = "That link could not be read. Paste the text of the posting instead."


__all__ = [
    "CoverUnavailable",
    "ExportBlocked",
    "NoRequirementsFound",
    "PostingLinkBlocked",
    "PostingLinkUnreadable",
    "PostingTextError",
]
