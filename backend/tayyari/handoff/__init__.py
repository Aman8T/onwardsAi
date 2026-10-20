"""The hand-off from the marketing site: a resume, links and a goal posted once, taken once by the app.

    service    `HandoffService`: the origin allow-list, the checks on what was posted, the store and the one-time take.
               `parse_handoff_links` turns the pasted links into a short list of http(s) addresses.

Sits between `contract` and `api` like `auth`: it reads storage only through `storage.ports`. The routes (`api/routes/handoff.py`)
own HTTP: the redirects, the rate limit and reading the form.
"""

from tayyari.handoff.service import HandoffRejected, HandoffService, ResumeFile, parse_handoff_links

__all__ = ["HandoffRejected", "HandoffService", "ResumeFile", "parse_handoff_links"]
