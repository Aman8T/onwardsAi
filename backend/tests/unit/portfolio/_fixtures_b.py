"""Shared loaders for the composer / snapshot / public / edit tests (unit B).

The portfolio fixtures (`tests/data/portfolio/*.json`) hold a `Profile` and its `StoredContext`, not facts: the
facts are what unit A's extractor makes from the resume. The five canvas personas get hand-written facts that
mirror that extraction (same titles, dates and wording as the resume text); the stress fixtures get small
synthetic facts counted from their source summaries, which is enough to exercise every code path.
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from tayyari.contract.models import Profile
from tayyari.contract.portfolio import (
    CopySlot,
    Fact,
    FactKind,
    GraphDomain,
    GraphMastery,
    GraphSkill,
    GraphSnapshot,
    Portfolio,
    PortfolioContent,
    PortfolioPerson,
    PortfolioSpec,
    ProofStat,
    PublishInfo,
    SourceRef,
)
from tayyari.contract.stored import StoredContext
from tayyari.portfolio.compose import compose, person_seed
from tayyari.portfolio.features import Features, compute_features
from tayyari.portfolio.snapshot import build_person, graph_snapshot, open_to, proof_stats

DATA = Path(__file__).resolve().parents[2] / "data" / "portfolio"
AS_OF = date(2026, 10, 8)
PERSONAS = ("riya", "aarav", "meera", "vikram", "sana")
STRESS = (
    "stress-long-titles",
    "stress-many-projects",
    "stress-missing",
    "stress-one-word",
    "stress-scanned",
    "stress-two-domains",
    "stress-uneven",
    "stress-unicode",
)
ALL_FIXTURES = (*PERSONAS, *STRESS)


@cache
def _raw(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    return data


def load_profile(name: str) -> Profile:
    return Profile.from_dict(_raw(name)["profile"])


def load_context(name: str) -> StoredContext:
    return StoredContext.model_validate(_raw(name)["context"])


# ───────────────────────── facts ─────────────────────────


def fact(
    fid: str,
    kind: FactKind,
    title: str,
    *,
    org: str | None = None,
    location: str | None = None,
    start: str | None = None,
    end: str | None = None,
    current: bool | None = None,
    url: str | None = None,
    detail: str | None = None,
    grade: str | None = None,
    highlights: list[str] | None = None,
    tags: list[str] | None = None,
    quote: str | None = None,
    source: str = "resume",
) -> Fact:
    return Fact(
        id=fid,
        kind=kind,
        title=title,
        org=org,
        location=location,
        start=start,
        end=end,
        current=current,
        url=url,
        detail=detail,
        grade=grade,
        highlights=highlights or [],
        tags=tags or [],
        sourceRefs=[SourceRef(sourceId=source, quote=quote or title)],
    )


def _riya() -> list[Fact]:
    return [
        fact(
            "exp-1",
            "experience",
            "Frontend Engineer II",
            org="Kitebird",
            location="Bengaluru",
            start="2025-07",
            current=True,
            highlights=[
                "Rebuilt the three-step checkout; conversion rose 9.4% in the A/B test.",
                "Own Kite UI, the shared React component library (42 components) used by 3 product squads.",
                "Mentored 2 frontend interns through their 6-month internships.",
            ],
        ),
        fact(
            "exp-2",
            "experience",
            "Frontend Engineer",
            org="Kitebird",
            location="Bengaluru",
            start="2024-05",
            end="2025-06",
            highlights=["Built the backend-for-frontend (Node.js, Express) for the mobile web app."],
        ),
        fact(
            "exp-3",
            "experience",
            "Frontend Engineering Intern",
            org="Saffron Ledger",
            location="Bengaluru",
            start="2024-01",
            end="2024-04",
            highlights=["Fixed table rendering performance for accounts with 10,000+ invoices."],
        ),
        fact(
            "exp-4",
            "experience",
            "UI Developer",
            org="Freelance",
            location="Remote",
            start="2023-06",
            end="2023-12",
            highlights=["Designed and built marketing sites and a component kit for 3 early-stage clients."],
        ),
        fact(
            "exp-5",
            "experience",
            "Web Lead",
            org="Google Developer Student Club, Nandi Institute of Technology",
            location="Bengaluru",
            start="2022",
            end="2023",
            highlights=["Ran weekly React workshops for 60+ students."],
        ),
        fact(
            "exp-6",
            "experience",
            "Maintainer, motion-kit",
            org="Open source",
            start="2023",
            current=True,
            highlights=[
                "Maintain motion-kit, a spring-physics animation library for React (212 GitHub stars)."
            ],
        ),
        fact(
            "proj-1",
            "project",
            "motion-kit",
            url="https://github.com/riyakapoor/motion-kit",
            detail="Spring-physics animation primitives for React with gesture support.",
        ),
        fact(
            "proj-2",
            "project",
            "checkout-lab",
            detail="Experiments in checkout UX: optimistic updates, skeleton states.",
        ),
        fact(
            "proj-3",
            "project",
            "design-tokens",
            detail="A Style Dictionary pipeline from Figma variables to CSS tokens.",
        ),
        fact(
            "proj-4", "project", "Kite UI docs", detail="Public Storybook for Kitebird's component library."
        ),
        fact(
            "edu-1",
            "education",
            "B.Tech, Computer Science and Engineering",
            org="Nandi Institute of Technology",
            start="2020",
            end="2024",
            grade="CGPA 8.6 / 10",
        ),
        fact(
            "ach-1",
            "achievement",
            'Kitebird "Ship It" award, H1 2025, for the checkout rebuild.',
            start="2025",
        ),
        fact("ach-2", "achievement", "Speaker, ReactFoo Bengaluru community meetup (2025).", start="2025"),
        fact("link-1", "link", "Portfolio", url="https://riyakapoor.design"),
        fact("link-2", "link", "GitHub", url="https://github.com/riyakapoor"),
        fact(
            "contact-1",
            "contact",
            "Email",
            url="mailto:riya.kapoor.dev@example.com",
            quote="riya.kapoor.dev@example.com",
        ),
        fact(
            "quote-1",
            "quote",
            "I build interfaces that feel physical: springs, not durations.",
            source="portfolio-1",
        ),
    ]


def _aarav() -> list[Fact]:
    return [
        fact(
            "edu-1",
            "education",
            "B.Tech, Electronics and Communication Engineering",
            org="Kaveri Institute of Technology",
            location="Mysuru",
            start="2022",
            end="2026 (expected)",
            grade="CGPA 7.9 / 10",
        ),
        fact(
            "proj-1",
            "project",
            "Smart irrigation controller",
            url="https://github.com/aaravmehta/soil-sense",
            detail="Final-year mini project: an Arduino Uno reads two soil-moisture sensors and switches a 12V pump.",
            highlights=["Team of 3. I wrote the sensor firmware in C and built the dashboard."],
        ),
        fact("ach-1", "achievement", "Member, IEEE student branch (2023 - 2025).", start="2023", end="2025"),
        fact(
            "ach-2",
            "achievement",
            "Took part in the college round of Smart India Hackathon 2025.",
            start="2025",
        ),
        fact("link-1", "link", "GitHub", url="https://github.com/aaravmehta"),
        fact(
            "contact-1",
            "contact",
            "Email",
            url="mailto:aarav.mehta.ece@example.com",
            quote="aarav.mehta.ece@example.com",
        ),
        fact("contact-2", "contact", "+91 90000 12345", quote="+91 90000 12345"),
        fact(
            "quote-1",
            "quote",
            "I like building small devices that measure something real and show it on a screen.",
        ),
    ]


def _meera() -> list[Fact]:
    return [
        fact(
            "exp-1",
            "experience",
            "Junior Data Analyst",
            org="Lumen Learning Labs",
            location="Pune",
            start="2025-02",
            current=True,
            highlights=[
                "Built the weekly learner-engagement dashboard in Tableau, used by 4 course teams.",
                "Wrote the SQL that joins quiz attempts to course completions (12 tables).",
                "Cut a manual Monday report from 3 hours to 20 minutes.",
            ],
        ),
        fact(
            "exp-2",
            "experience",
            "Mathematics Teacher (grades 9-12)",
            org="Greenfield Public School",
            location="Pune",
            start="2018-06",
            end="2023-12",
            highlights=[
                "Taught 450+ students across six years.",
                "Built the school's Google Sheets grade tracker, used by 30 teachers.",
            ],
        ),
        fact(
            "proj-1",
            "project",
            "Does attendance predict board results?",
            start="2024",
            detail="Analysis of a public Maharashtra school dataset (9,800 rows) in Python and pandas.",
        ),
        fact(
            "proj-2",
            "project",
            "Board results explorer",
            start="2024",
            detail="A public Tableau dashboard of district-level pass rates.",
        ),
        fact(
            "proj-3",
            "project",
            "Quiz-to-completion SQL case study",
            start="2024",
            detail="A written walkthrough of the Lumen engagement query.",
        ),
        fact(
            "edu-1",
            "education",
            "M.Sc. Mathematics",
            org="Savitribai Phule Pune University",
            start="2016",
            end="2018",
        ),
        fact("edu-2", "education", "B.Ed.", end="2018"),
        fact(
            "ach-1",
            "achievement",
            "Google Data Analytics Professional Certificate (Coursera), 2024.",
            start="2024",
        ),
        fact("link-1", "link", "Tableau Public", url="https://public.tableau.com/app/profile/meera.iyer"),
        fact("link-2", "link", "Medium", url="https://medium.com/@meeraiyer"),
        fact(
            "contact-1",
            "contact",
            "Email",
            url="mailto:meera.iyer.data@example.com",
            quote="meera.iyer.data@example.com",
        ),
        fact("contact-2", "contact", "+91 90000 23456", quote="+91 90000 23456"),
    ]


def _vikram() -> list[Fact]:
    return [
        fact(
            "exp-1",
            "experience",
            "Staff Engineer",
            org="Parcelwise",
            location="Hyderabad",
            start="2021-04",
            current=True,
            highlights=[
                "Lead the routing platform team of 9 (Go, gRPC, Kubernetes).",
                "Cut p99 route-quote latency from 820 ms to 140 ms.",
                "Moved the shipment ledger (2.1 billion rows) to a sharded cluster with no downtime.",
            ],
        ),
        fact(
            "exp-2",
            "experience",
            "Senior Software Engineer",
            org="Finmint",
            location="Bengaluru",
            start="2017-07",
            end="2021-03",
            highlights=[
                "Built the idempotent payout service handling 1,200 crore a month.",
                "On-call lead for 2 years at 99.99% monthly availability.",
            ],
        ),
        fact(
            "exp-3",
            "experience",
            "Software Engineer",
            org="ShopLane",
            location="Bengaluru",
            start="2014-06",
            end="2017-06",
            highlights=[
                "Built the search indexing pipeline; cut catalog reindex time from 6 hours to 40 minutes."
            ],
        ),
        fact(
            "exp-4",
            "experience",
            "Associate Engineer",
            org="Infosys",
            location="Mysuru",
            start="2013-08",
            end="2014-05",
        ),
        fact("ach-1", "achievement", "Contributor to pgshard (12 merged PRs).", start="2021"),
        fact("ach-2", "achievement", '"Sharding without tears" - GopherCon India 2024', start="2024"),
        fact("ach-3", "achievement", '"Idempotency at 1,200 crore a month" - Rootconf 2022', start="2022"),
        fact(
            "edu-1",
            "education",
            "B.E. Computer Science",
            org="NIT Karnataka (Surathkal)",
            start="2009",
            end="2013",
        ),
        fact("link-1", "link", "GitHub", url="https://github.com/vikramrao"),
        fact("link-2", "link", "LinkedIn", url="https://linkedin.com/in/vikram-rao-eng"),
        fact(
            "contact-1",
            "contact",
            "Email",
            url="mailto:vikram.rao.eng@example.com",
            quote="vikram.rao.eng@example.com",
        ),
        fact("contact-2", "contact", "+91 90000 45678", quote="+91 90000 45678"),
    ]


def _sana() -> list[Fact]:
    return [
        fact(
            "exp-1",
            "experience",
            "Content Marketing Associate",
            org="Brightleaf",
            location="Mumbai",
            start="2025-03",
            current=True,
            highlights=[
                "Write the brand's long-form blog (2 posts a week) and the fortnightly newsletter (18,000 subscribers).",
                '"Why your sunscreen pills" was the blog\'s most-read post of 2025.',
            ],
        ),
        fact(
            "exp-2",
            "experience",
            "Content Intern",
            org="The Daily Bowl",
            location="Mumbai",
            start="2024-09",
            end="2025-02",
            highlights=["31 published recipes and features."],
        ),
        fact("clip-1", "clip", "Why your sunscreen pills", org="Brightleaf blog", start="2025"),
        fact("clip-2", "clip", "The case for boring breakfasts", org="The Daily Bowl", start="2024"),
        fact("clip-3", "clip", "A field guide to Mumbai's Irani cafes", org="The Daily Bowl", start="2024"),
        fact(
            "clip-4",
            "clip",
            "What 18,000 readers taught us about subject lines",
            org="Brightleaf newsletter",
            start="2026",
        ),
        fact(
            "edu-1",
            "education",
            "B.A. English Literature",
            org="St. Xavier's College, Mumbai",
            start="2021",
            end="2024",
        ),
        fact("link-1", "link", "Substack", url="https://sanasheikh.substack.com"),
        fact("link-2", "link", "Site", url="https://sanasheikh.in"),
        fact(
            "contact-1",
            "contact",
            "Email",
            url="mailto:sana.sheikh.writes@example.com",
            quote="sana.sheikh.writes@example.com",
        ),
        fact("contact-2", "contact", "+91 90000 34567", quote="+91 90000 34567"),
    ]


_PERSONA_FACTS = {"riya": _riya, "aarav": _aarav, "meera": _meera, "vikram": _vikram, "sana": _sana}


def synthetic_facts(profile: Profile) -> list[Fact]:
    """Small counted facts for a stress fixture: ``N projects`` / ``M roles`` from the resume summary line."""
    resume = next((s for s in profile.sources if s.kind == "resume"), None)
    sub = resume.sub if resume and resume.ok else ""
    n_proj = _count_in(sub, "project")
    n_roles = _count_in(sub, "role")
    facts: list[Fact] = []
    for i in range(n_roles):
        facts.append(
            fact(
                f"exp-{i + 1}",
                "experience",
                f"Engineer {i + 1}",
                org=f"Org {i + 1}",
                start=f"{2020 - i}-01",
                end=None if i == 0 else f"{2020 - i + 1}-01",
                current=True if i == 0 else None,
                highlights=[f"Delivered thing {i + 1} for 3 teams."],
            )
        )
    for i in range(n_proj):
        facts.append(fact(f"proj-{i + 1}", "project", f"Project {i + 1}", detail=f"Built project {i + 1}."))
    if sub:
        facts.append(fact("edu-1", "education", "B.Tech", org="Some Institute", start="2016", end="2020"))
    return facts


def _count_in(sub: str, word: str) -> int:
    for part in sub.split("·"):
        bits = part.strip().split()
        if len(bits) >= 2 and bits[0].isdigit() and bits[1].startswith(word):
            return int(bits[0])
    return 0


def facts_for(name: str) -> list[Fact]:
    builder = _PERSONA_FACTS.get(name)
    return builder() if builder else synthetic_facts(load_profile(name))


# ───────────────────────── content, graph, features, looks ─────────────────────────


def build_content(name: str, facts: list[Fact] | None = None) -> PortfolioContent:
    profile, ctx = load_profile(name), load_context(name)
    return PortfolioContent(
        person=build_person(profile),
        facts=facts if facts is not None else facts_for(name),
        slots=[],
        stats=proof_stats(profile, ctx),
        practice=None,
        openTo=open_to(profile),
    )


def build_graph(name: str) -> GraphSnapshot:
    return graph_snapshot(load_profile(name), AS_OF)


def features_for(name: str, first_read: dict[str, Any] | None = None) -> Features:
    return compute_features(load_profile(name), facts_for(name), first_read)


def looks_for(name: str, seed: int | None = None, n: int = 3) -> list[PortfolioSpec]:
    profile = load_profile(name)
    return compose(
        features_for(name),
        build_content(name),
        build_graph(name),
        person_seed(profile.id) if seed is None else seed,
        n,
    )


def make_portfolio(name: str, *, look_index: int = 0, slots: list[CopySlot] | None = None) -> Portfolio:
    """A complete `Portfolio` for a fixture: composed looks, the content with the given copy slots."""
    profile = load_profile(name)
    content = build_content(name)
    content = content.model_copy(update={"slots": slots or []})
    graph = build_graph(name)
    looks = compose(features_for(name), content, graph, person_seed(profile.id))
    return Portfolio(
        profileId=profile.id,
        publicId="a" * 32,
        version=1,
        kitVersion=1,
        createdAt="2026-10-08T09:00:00Z",
        updatedAt="2026-10-08T09:00:00Z",
        lookIndex=look_index,
        looks=looks,
        spec=looks[look_index],
        content=content,
        graph=graph,
        publish=PublishInfo(state="draft", defaultView="standard"),
    )


# ───────────────────────── small hand-made content ─────────────────────────


def blank_content(
    facts: list[Fact] | None = None,
    *,
    stats: int = 0,
    open_to_titles: list[str] | None = None,
    slots: list[CopySlot] | None = None,
) -> PortfolioContent:
    return PortfolioContent(
        person=PortfolioPerson(name="Test Person", headline="Engineer", role="Engineer", monogram="TP"),
        facts=facts or [],
        slots=slots or [],
        stats=[ProofStat(value=str(i + 1), label=f"thing {i}", sourceId="resume") for i in range(stats)],
        practice=None,
        openTo=open_to_titles or [],
    )


def blank_graph(scores: list[int] | None = None, *, skills_each: int = 3, sources: int = 2) -> GraphSnapshot:
    values = scores if scores is not None else [80, 70, 60, 50, 40, 30]
    domains = [
        GraphDomain(
            id=f"d{i}",
            name=f"Domain {i}",
            short=f"D{i}",
            score=v,
            skills=[
                GraphSkill(id=f"d{i}-s{j}", name=f"Skill {i}.{j}", score=max(0, v - 5 * j))
                for j in range(skills_each)
            ],
        )
        for i, v in enumerate(values)
    ]
    return GraphSnapshot(
        asOf="2026-10-08",
        domains=domains,
        skillCount=len(values) * skills_each,
        mastery=GraphMastery(score=60, role="Engineer"),
        sources=["Resume", "GitHub"][:sources],
    )
