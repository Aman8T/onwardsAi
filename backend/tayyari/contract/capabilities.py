"""What the Ask coach can hand the person as a button: the Python mirror of `shared/capabilities.ts`.

Same fields, same order. `scripts/capabilities-parity.ts` dumps the TypeScript list to
`backend/tests/data/capabilities.json`, and `tests/unit/contract/test_capabilities_parity.py` compares this list to
that file field for field, checks every `route` against the app's own routes (`api.static.APP_ROUTES`), and checks
that the coach's prompt names every destination. A button only opens a screen; no destination is a tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Destination = Literal[
    "open_role",
    "practice",
    "redrill",
    "match",
    "discover",
    "open_application",
    "apply",
    "open_plan",
    "portfolio",
    "resumes",
    "share_card",
    "weekly",
    "new_analysis",
    "pricing",
]

TargetKind = Literal["none", "role", "plan", "match", "tracked", "skill"]
"""What a button's target id names: nothing, a next role, a stored plan, a posting match, a tracked role (from the
person's browser) or a skill of the graph."""


@dataclass(frozen=True, slots=True)
class Capability:
    destination: Destination
    label: str
    target: TargetKind
    can: str
    route: str | None


CAPABILITIES: Final[tuple[Capability, ...]] = (
    Capability(
        "open_role", "Open this role", "role", "opens one of their next roles on the graph", "/dashboard"
    ),
    Capability(
        "practice",
        "Start Practice",
        "none",
        "opens Practice, where they choose an interview to take",
        "/practice",
    ),
    Capability(
        "redrill",
        "Re-drill weak claims",
        "none",
        "opens the claims that wobbled in Practice, to take again",
        "/practice",
    ),
    Capability(
        "match",
        "Match a posting",
        "none",
        "opens the place where they paste a job posting to see it against their evidence",
        "/match",
    ),
    Capability("discover", "Search roles", "none", "opens Discover, where roles are searched", "/discover"),
    Capability(
        "open_application",
        "Open application",
        "tracked",
        "opens one of their tracked roles in Your applications",
        "/discover",
    ),
    Capability(
        "apply",
        "Apply for me",
        "match",
        "opens Apply for me for one of their posting matches, where they check every value before anything is sent",
        "/apply",
    ),
    Capability("open_plan", "Open your plan", "plan", "opens one of their preparation plans", "/dashboard"),
    Capability("portfolio", "Open your portfolio", "none", "opens their portfolio page", None),
    Capability("resumes", "Open your resumes", "none", "opens their resumes", "/resume"),
    Capability(
        "share_card",
        "Share your radar card",
        "none",
        "opens the sheet where they choose whether to share their radar card",
        None,
    ),
    Capability("weekly", "Read the Weekly", "none", "opens the Weekly", "/weekly"),
    Capability(
        "new_analysis",
        "New analysis",
        "none",
        "opens the page where they upload a resume for a new analysis",
        None,
    ),
    Capability("pricing", "See pricing", "none", "opens the pricing page", "/pricing"),
)

CAN_SEE: Final[tuple[str, ...]] = (
    "Your skill graph, each score and the evidence under it",
    "Your five next roles and what each percentage is made of",
    "Your applications, their stages and your notes",
    "Your practice sessions and the claims that wobbled",
    "Your plan, its weeks and what is ticked",
    "Your posting matches and their open questions",
    "How any screen in the app works",
    "The public web, when a question needs it, with the source named",
)

CANNOT: Final[tuple[str, ...]] = (
    "Submit or approve an application",
    "Send an email or a message, or book a calendar slot",
    "Change your resume, portfolio or notes",
    "Change your plan's ticks or an application's stage",
    "Compare you with other people: there is no cohort",
    "Say anything about you that no fact supports",
)

BY_DESTINATION: Final[dict[str, Capability]] = {c.destination: c for c in CAPABILITIES}


__all__ = [
    "BY_DESTINATION",
    "CANNOT",
    "CAN_SEE",
    "CAPABILITIES",
    "Capability",
    "Destination",
    "TargetKind",
]
