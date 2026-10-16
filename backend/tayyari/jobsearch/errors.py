"""Errors of the job search with AI."""

from __future__ import annotations

from tayyari.core.errors import AppError


class JobSearchDisabled(AppError):
    """The search is switched off (``TAYYARI_JOBSEARCH__ENABLED=false``, or Discover is off): 503."""

    status = 503
    code = "jobsearch_disabled"
    user_message = "Search with AI is switched off on this server."


__all__ = ["JobSearchDisabled"]
