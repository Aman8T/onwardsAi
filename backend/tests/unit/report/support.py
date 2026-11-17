"""Shared set-up for the report tests: a small profile built in code (so every count is known), the fakes, accounts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tayyari.contract.models import Profile
from tayyari.contract.report import DEFAULT_CHOICES, ReportChoices
from tayyari.contract.stored import StoredContext
from tayyari.report.service import ReportService
from tayyari.storage.ports import StoredResume
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
PUBLIC = "https://app.leafout.in"
ORG = "Zentrix Systems Pvt Ltd"
"""The employer in the fixture: its name appears in evidence lines, and must never reach a public page while hidden."""


def skill(
    sid: str, name: str, domain: str, score: int, *lines: str, source: str = "resume"
) -> dict[str, Any]:
    return {
        "id": sid,
        "name": name,
        "domainId": domain,
        "score": score,
        "evidence": [{"sourceId": source, "text": t} for t in lines],
        "unlocks": [],
    }


def req(name: str, have: bool, skill_id: str | None = None) -> dict[str, Any]:
    return {"name": name, "skillId": skill_id, "have": have, "current": 60 if have else 20, "target": 70}


def role(
    rid: str, title: str, alignment: int, reqs: list[dict[str, Any]], eta: str = "~2 months"
) -> dict[str, Any]:
    return {
        "id": rid,
        "title": title,
        "alignment": alignment,
        "eta": eta,
        "why": "Because.",
        "requirements": reqs,
    }


def profile_doc(pid: str = "p" * 32, **over: Any) -> dict[str, Any]:
    """Ravi: Java backend and test automation, seven years, Pune. Roles, from strongest:

    - ``qa-lead`` 5 of 6 asks evidenced (ready now);
    - ``backend-gcc`` 4 of 6, backed by Java;
    - ``platform`` 3 of 6, backed by CI;
    - ``sre`` 3 of 6, backed by CI, lower alignment (fourth credible: cut by the limit of three);
    - ``data-eng`` 2 of 6 (below the bar: a near miss);
    - ``pm`` 3 of 6 but every evidenced ask maps to a skill with no evidence (not backed: a near miss).
    """
    six = ["A", "B", "C", "D", "E", "F"]
    doc: dict[str, Any] = {
        "id": pid,
        "createdAt": "2026-11-17T09:00:00Z",
        "person": {
            "name": "Ravi Kumar Sharma",
            "headline": "Senior Software Engineer · 7 yrs · Pune",
            "currentRole": "Senior Software Engineer",
            "location": "Pune",
            "yearsExperience": 7.2,
            "summary": "Backend and testing.",
        },
        "mastery": {"score": 70, "insight": "Solid."},
        "sources": [
            {
                "id": "resume",
                "kind": "resume",
                "label": "Resume",
                "sub": "2 pages",
                "summary": "A resume.",
                "notes": [],
                "ok": True,
            },
            {
                "id": "github",
                "kind": "github",
                "label": "GitHub",
                "sub": "9 repos",
                "summary": "Code.",
                "notes": [],
                "ok": True,
            },
        ],
        "domains": [
            {"id": "backend", "name": "Java backend", "short": "Java backend", "score": 82, "summary": "."},
            {"id": "testing", "name": "Test automation", "short": "Testing", "score": 70, "summary": "."},
            {
                "id": "cloud",
                "name": "Cloud and infrastructure",
                "short": "Cloud and infrastructure",
                "score": 40,
                "summary": ".",
            },
        ],
        "skills": [
            skill(
                "java",
                "Java",
                "backend",
                85,
                "Built and ran the Spring Boot services behind a bank client's loan portal at Zentrix Systems.",
                "Wrote the SQL behind the monthly settlement reports.",
            ),
            skill(
                "playwright",
                "Playwright",
                "testing",
                78,
                "Led the move of the payments regression suite from Selenium to Playwright at zentrix.",
                "Was laid off in the March restructuring after the suite shipped.",
                "Trained four new joiners on the test framework.",
            ),
            skill(
                "ci",
                "CI pipelines",
                "cloud",
                55,
                "Moved the team's Jenkins builds to GitHub Actions.",
                source="github",
            ),
            skill("roadmaps", "Roadmaps", "backend", 30),
        ],
        "roles": [
            role(
                "data-eng",
                "Data Engineer",
                60,
                [req(x, i < 2, "java" if i == 0 else None) for i, x in enumerate(six)],
            ),
            role(
                "platform",
                "Platform Engineer",
                70,
                [req(x, i < 3, "ci" if i == 0 else None) for i, x in enumerate(six)],
            ),
            role(
                "qa-lead",
                "QA Automation Lead",
                80,
                [req("Perf testing", False)] + [req(f"Q{x}", True, "playwright") for x in "ABCDE"],
                eta="now",
            ),
            role(
                "backend-gcc",
                "Backend Engineer, at a GCC",
                75,
                [
                    req(x, i < 4, "java" if i == 0 else None)
                    for i, x in enumerate(["Java", "SQL", "APIs", "Spring", "Kubernetes", "System design"])
                ],
            ),
            role(
                "sre",
                "Site Reliability Engineer",
                50,
                [req(x, i < 3, "ci" if i == 0 else None) for i, x in enumerate(six)],
            ),
            role(
                "pm",
                "Product Manager",
                90,
                [
                    req(x, i < 3, "roadmaps")
                    for i, x in enumerate(["Discovery", "Roadmaps", "Users", "Metrics", "Pricing", "Launch"])
                ],
            ),
        ],
        "models": {"reader": "haiku", "synth": "haiku"},
    }
    doc.update(over)
    return doc


def profile(**over: Any) -> Profile:
    return Profile.model_validate(profile_doc(**over))


def choices(**over: Any) -> ReportChoices:
    return DEFAULT_CHOICES.model_copy(update=over)


class Slugs:
    """Predictable slugs: ``rrrrrrrrrr01``, ``rrrrrrrrrr02`` ..."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"rrrrrrrrrr{self.n:02d}"


@dataclass
class World:
    fakes: FakeRepositories
    clock: FakeClock
    service: ReportService

    async def account(self, email: str = "ravi@example.com") -> str:
        user = await self.fakes.users.sign_in_email(email=email, now=self.clock.now())
        return user.id

    async def profile(
        self, pid: str, *, owner: str | None = None, sample: bool = False, doc: dict[str, Any] | None = None
    ) -> str:
        await self.fakes.profiles.save(
            pid,
            doc if doc is not None else profile_doc(pid),
            StoredContext(),
            goal=None,
            owner_hash=None,
            is_sample=sample,
        )
        if owner is not None:
            await self.fakes.users.claim_profile(owner, pid)
        self.clock.advance(60)
        return pid

    async def resume_with_employer(self, pid: str, org: str = ORG) -> None:
        """A /resume draft whose experience names the employer, as the resume service stores it."""
        await self.fakes.resumes.create(
            StoredResume(
                id=("r" + pid)[:32],
                user_id=None,
                profile_id=pid,
                kind="upload",
                role_key="",
                title="My resume",
                draft={
                    "experience": [
                        {"entryFactId": "e1", "title": "Senior Software Engineer", "org": org, "bullets": []}
                    ]
                },
                final=None,
                facts={},
                is_default=False,
                version=1,
                created_at=self.clock.now(),
                updated_at=self.clock.now(),
            )
        )


def world(offer_url: str = "") -> World:
    clock = FakeClock(T0)
    fakes = FakeRepositories(clock)
    service = ReportService(
        profiles=fakes.profiles,
        users=fakes.users,
        reports=fakes.report,
        resumes=fakes.resumes,
        user_facts=fakes.user_facts,
        clock=clock,
        public_url=PUBLIC + "/",
        offer_url=offer_url,
        slugs=Slugs(),
    )
    return World(fakes=fakes, clock=clock, service=service)


__all__ = [
    "ORG",
    "PUBLIC",
    "T0",
    "Slugs",
    "World",
    "choices",
    "profile",
    "profile_doc",
    "req",
    "role",
    "skill",
    "world",
]
