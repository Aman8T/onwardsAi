"""Job search with AI: Claude Haiku 5.5 scouts find employers hiring the person's kind of role, code reads those employers'
own boards, narrows the open roles, and Haiku reads the shortlist against the person's evidence.

Built from two baseline runs by larger models (Opus and Sonnet, 2026-10-16, the sample resume, "find jobs in
Bengaluru"): both found that a web search is good at finding *employers* and bad at knowing what is *open* (11 of 17 and
14 of 19 job links they opened were closed), and that an employer's own board is the reliable sign. So the model searches
and reads; code decides what is open, what is near, what is at the person's level, and what the fit is called.

    brief     the profile read in code: role, years, city, next roles, skills, evidence lines, the scouts' angles
    harvest   board links (Greenhouse, Lever) taken from the search tool's results, never from a model's words
    boards    the boards read through Discover's polite fetcher, cached for a while; Leafout's own employers included
    narrow    code keeps roles in the city or remote in India, of the person's kind, at their level, and ranks them
    judge     Haiku reads batches of the shortlist; every quote is checked against the posting and the profile
    service   the run, streamed as trail steps and jobs

Layering: like ``match`` reuses ``portfolio``, this package reuses ``discover``'s pure parts (board parsing, places,
the skill vocabulary, the quick fit) and its board fetcher. It never imports ``match`` or ``analysis``.
"""

from tayyari.jobsearch.errors import JobSearchDisabled
from tayyari.jobsearch.service import JobSearchService

__all__ = ["JobSearchDisabled", "JobSearchService"]
