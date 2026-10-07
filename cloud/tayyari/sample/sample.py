"""The bundled sample persona behind "Try a sample profile": Riya Kapoor, a fictional frontend engineer.

The fixtures (resume.md, github.md, lovable.md, portfolio.md next to this file) are fed to the real
readers and synthesizer; only the deterministic fetching step is replaced, by replayed log lines.
Everything here is fictional: Kitebird and Saffron Ledger are made-up companies.

The pre-computed profile and chat context (sample-profile.json, sample-context.json) are served by
`tayyari.store`.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from ..ingest.classify import SourceDoc

SAMPLE_DIR = Path(__file__).parent

SAMPLE_GOAL = "Move into AI product engineering within a year"

# Fake ingest log lines replayed (with short delays) in sample mode, per source id.
SAMPLE_INGEST_LOGS: dict[str, list[str]] = {
    "resume": ["Parsed 2 pages", "Read 656 words of text", "Found sections: Summary, Experience, Projects, Skills, Education"],
    "github": [
        "Fetched profile, 34 public repos",
        "Ranked repos by recency and stars",
        "motion-kit: spring physics, 212 stars",
        "Detected tests in 6 of 34 repos, CI in 9",
        "notes-ai: embeddings + OpenAI API",
    ],
    "lovable-1": ["Opened recipe-genie.lovable.app", "Detected Supabase auth, OpenAI, Tailwind, shadcn/ui", "Mapped 3 user flows"],
    "portfolio-1": ["riyakapoor.design → 8 pages", "Found 5 case studies", "Found accessibility audit write-up"],
}  # fmt: skip


@cache
def _read(name: str) -> str:
    return (SAMPLE_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()


def load_sample_sources() -> list[SourceDoc]:
    """The four sample sources, in pipeline order (fresh objects on every call)."""
    return [
        SourceDoc(
            id="resume",
            kind="resume",
            label="Resume",
            src="Riya_Kapoor_Resume.pdf",
            kind_label="Resume · PDF",
            text=_read("resume.md"),
            meta={
                "pages": 2,
                "words": 656,
                "format": "PDF",
                "sections": ["Summary", "Experience", "Projects", "Skills", "Education", "Achievements"],
                "roles": ["Kitebird", "Saffron Ledger", "Freelance", "GDSC"],
            },
            quick_signals=["React", "TypeScript", "Figma", "Mentored 2 interns", "Node.js", "GraphQL", "Storybook", "Accessibility"],
        ),
        SourceDoc(
            id="github",
            kind="github",
            label="GitHub",
            url="https://github.com/riyakapoor",
            src="github.com/riyakapoor",
            kind_label="GitHub profile",
            text=_read("github.md"),
            meta={
                "username": "riyakapoor",
                "repos": 34,
                "originals": 31,
                "forks": 3,
                "stars": 389,
                "languages": ["TypeScript", "JavaScript", "CSS", "MDX", "Python", "Go", "Shell", "Lua", "HTML"],
                "topRepos": ["motion-kit", "design-tokens", "notes-ai", "checkout-lab", "recipe-genie-web", "a11y-checklist", "trpc-starter"],
                "withTests": 6,
                "withCI": 9,
                "inspected": 34,
                "followers": 318,
                "since": "2019-08-14T09:12:44Z",
            },
            quick_signals=[
                "TypeScript · 17 repos", "JavaScript · 6 repos", "React · 14 repos", "Framer Motion · motion-kit",
                "OpenAI API · notes-ai", "CI in 9 repos", "Tests in 6 repos", "389 stars",
            ],
        ),
        SourceDoc(
            id="lovable-1",
            kind="lovable",
            label="Lovable",
            url="https://recipe-genie.lovable.app",
            src="recipe-genie.lovable.app",
            kind_label="Lovable app",
            text=_read("lovable.md"),
            meta={
                "title": "Recipe Genie",
                "headings": ["Pantry in seconds", "Three good options", "Swap anything", "Save your favourites"],
                "libraries": ["React", "Vite", "Tailwind CSS", "shadcn/ui", "Supabase", "OpenAI API"],
                "flows": 3,
            },
            quick_signals=["Rapid prototyping", "Supabase", "OpenAI API", "Tailwind CSS", "shadcn/ui"],
        ),
        SourceDoc(
            id="portfolio-1",
            kind="portfolio",
            label="Portfolio",
            url="https://riyakapoor.design",
            src="riyakapoor.design",
            kind_label="Portfolio site",
            text=_read("portfolio.md"),
            meta={
                "pages": 8,
                "caseStudies": 5,
                "headings": [
                    "Checkout, rebuilt",
                    "An accessibility audit that actually shipped",
                    "Kite UI",
                    "motion-kit: designing with springs",
                    "Saffron Ledger dashboard",
                ],
            },
            quick_signals=["Design systems", "Accessibility audits", "Motion design", "Tech writing"],
        ),
    ]
