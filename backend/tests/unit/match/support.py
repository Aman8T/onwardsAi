"""Shared helpers for the match tests: the two personas, and a scripted "good Haiku" for each of the seven calls.

`meera()` and `riya()` load a persona (profile, stored context, posting, the posting answer a careful model gives,
and the portfolio facts that the real extractor verification yields from the scripted extraction of the portfolio
tests). The responders (`posting_answer`, `gate_responder`, `coverage_responder`, `areas_responder`,
`relevance_responder`, `phrase_responder`) read the prompt of each call the way the real model would and answer from tables written next to
the personas, so the service tests run the whole pipeline with no network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from tayyari.contract.match import Fact
from tayyari.contract.models import Profile
from tayyari.contract.portfolio import Fact as PortfolioFact
from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.core.paths import SAMPLE_DIR
from tayyari.llm.types import CallContext
from tayyari.match.facts import build_facts
from tayyari.match.models import FactBook
from tayyari.portfolio.facts import verify_facts
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.unit.portfolio._fixtures_a import scripted_facts

DATA = Path(__file__).resolve().parents[2] / "data" / "match"
HAIKU = "claude-haiku-5-5"
CTX = CallContext(purpose="match.posting", profile_id="p1", request_id="req-1")


def text(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


def js(name: str) -> Any:
    return json.loads(text(name))


@dataclass(frozen=True)
class Persona:
    name: str
    profile: Profile
    context: StoredContext
    posting_text: str
    posting_answer: dict[str, Any]
    portfolio_facts: list[PortfolioFact]
    coverage: dict[str, tuple[str, int, list[str]]] = field(default_factory=dict)
    """Requirement text prefix -> (status, level, fact ids) of the careful model's answer."""
    gates: dict[str, tuple[str, list[str]]] = field(default_factory=dict)
    """Gate question prefix -> (verdict, fact ids)."""

    def book(self, user_facts: tuple[Fact, ...] = ()) -> FactBook:
        return build_facts(self.profile, self.context, self.portfolio_facts, user_facts)


@cache
def meera() -> Persona:
    profile = Profile.from_dict(js("meera.profile.json"))
    context = StoredContext(
        sources=[
            StoredSource(id="resume", label="Resume", kind="resume", content=text("meera.resume.txt")),
            StoredSource(
                id="link-1",
                label="Growth notes",
                kind="link",
                url="https://meeranair.example/growth-notes",
                content=text("meera.link.txt"),
            ),
        ]
    )
    facts = verify_facts(scripted_facts(context), context, profile=profile).facts
    return Persona(
        name="meera",
        profile=profile,
        context=context,
        posting_text=text("meera.posting.txt"),
        posting_answer=js("meera.posting.answer.json"),
        portfolio_facts=facts,
        coverage={
            "5+ years": ("covered", 4, ["exp-1", "exp-2", "exp-3"]),
            "Owns a paid media budget": (
                "covered",
                4,
                ["exp-1.2", "skill-meta-ads", "skill-paid-budget-ownership"],
            ),
            "Lifecycle marketing on email": (
                "covered",
                4,
                ["exp-1.1", "skill-clevertap", "skill-whatsapp-marketing"],
            ),
            "A proven A/B testing": ("covered", 4, ["exp-1.3", "skill-a-b-testing"]),
            "Experience leading a team": ("partial", 3, ["exp-1.4", "skill-team-leadership"]),
            "Fluent in English and Hindi": ("unknown", 0, []),
            "Has worked with a customer data platform": ("missing_gap", 0, []),
            "Builds dashboards": ("missing_have_it", 3, ["skill-looker-studio", "clip-2"]),
            "Marketing mix modelling": ("missing_gap", 0, []),
            "Shopify Plus": ("missing_gap", 0, []),
            "MBA or postgraduate": ("missing_gap", 0, []),
            "Familiarity with SQL": ("missing_gap", 0, []),
            "Owns acquisition and retention": ("covered", 4, ["exp-1.1", "exp-1.2"]),
            "Runs a disciplined experimentation": ("covered", 4, ["exp-1.3", "skill-geo-holdout-testing"]),
            "Builds the weekly performance view": ("covered", 3, ["exp-2.2"]),
        },
        gates={
            "Are you authorised to work in India": ("uncertain", []),
            "Can you be in the Mumbai office": ("uncertain", ["exp-1"]),
            "Do you hold a bachelor": ("pass", ["edu-1"]),
        },
    )


@cache
def riya() -> Persona:
    profile = Profile.from_dict(json.loads((SAMPLE_DIR / "sample-profile.json").read_text(encoding="utf-8")))
    context = StoredContext.model_validate(
        json.loads((SAMPLE_DIR / "sample-context.json").read_text(encoding="utf-8"))
    )
    facts = verify_facts(scripted_facts(context), context, profile=profile).facts
    return Persona(
        name="riya",
        profile=profile,
        context=context,
        posting_text=text("riya.posting.txt"),
        posting_answer=js("riya.posting.answer.json"),
        portfolio_facts=facts,
        coverage={
            "5+ years building": ("partial", 2, ["exp-1", "exp-2", "skill-react"]),
            "Owns a component library": ("covered", 4, ["exp-1.2", "proj-4"]),
            "WCAG 2.1 AA accessibility": ("covered", 4, ["exp-1.4", "skill-accessibility-wcag"]),
            "Automated testing": ("partial", 2, ["proj-1"]),
            "Web performance": ("covered", 4, ["exp-1.1", "exp-3.3"]),
            "GraphQL APIs": ("partial", 2, ["exp-2.2", "skill-rest-graphql-apis"]),
            "Mentoring engineers": ("covered", 4, ["exp-1.6", "skill-mentoring-review"]),
            "Next.js and React server": ("partial", 3, ["skill-next-js", "exp-4.1"]),
            "Rust or WebAssembly": ("missing_gap", 0, []),
            "Open-source maintenance": ("covered", 4, ["exp-6.1", "proj-1"]),
            "Owns the design system": ("covered", 4, ["exp-1.2"]),
            "Partners with designers": ("covered", 4, ["exp-1.5", "skill-figma-to-code"]),
        },
        gates={"Are you legally able to work": ("uncertain", [])},
    )


# ───────────────────────── the scripted model ─────────────────────────

_REQ_LINE = re.compile(r"^(r\d+) \| (\w+) \| (\w+) \| (.*?) \| posting: ", re.MULTILINE)
_GATE_LINE = re.compile(r"^(g\d+): (.*)$", re.MULTILINE)
_FACT_LINE = re.compile(r"^(?:  fact )?(\S+) \| (\w+) \| (\S+) \| (\S+) \| (.*)$", re.MULTILINE)


def posting_answer(persona: Persona) -> dict[str, Any]:
    """A deep copy of the careful model's reading of the persona's posting."""
    return json.loads(json.dumps(persona.posting_answer))


def _lookup[V](table: Mapping[str, V], label: str) -> V | None:
    for prefix, value in table.items():
        if label.startswith(prefix) or prefix in label:
            return value
    return None


def gate_responder(persona: Persona) -> Callable[[StructuredCall], dict[str, Any]]:
    def respond(call: StructuredCall) -> dict[str, Any]:
        answers = []
        for gate_id, question in _GATE_LINE.findall(call.user_text):
            verdict, fact_ids = _lookup(persona.gates, question) or ("uncertain", [])
            answers.append({"gateId": gate_id, "factIds": fact_ids, "verdict": verdict, "note": "Scripted."})
        return {"answers": answers}

    return respond


def coverage_responder(persona: Persona) -> Callable[[StructuredCall], dict[str, Any]]:
    def respond(call: StructuredCall) -> dict[str, Any]:
        rows = []
        for req_id, tier, _kind, text_ in _REQ_LINE.findall(call.user_text):
            status, level, fact_ids = _lookup(persona.coverage, text_) or ("unknown", 0, [])
            gap = status != "covered" and tier in ("must", "required")
            rows.append(
                {
                    "requirementId": req_id,
                    "factIds": fact_ids,
                    "status": status,
                    "level": level,
                    "note": f"Scripted: {status}.",
                    "interviewRisk": "They will ask for a concrete example." if gap else "",
                    "mitigation": "Build one small, honest example." if gap else "",
                }
            )
        return {"rows": rows}

    return respond


_AREA_REQ_LINE = re.compile(r"^(r\d+) \| \w+ \| .*? \| field: ", re.MULTILINE)


def areas_responder(persona: Persona) -> Callable[[StructuredCall], dict[str, Any]]:
    """Places each requirement where the code fallback would (``none`` when it finds no area), naming no skill."""
    from tayyari.match.posting import parse_posting
    from tayyari.match.radar import map_requirement

    requirements = {
        r.id: r for r in parse_posting(posting_answer(persona), persona.posting_text).posting.requirements
    }

    def respond(call: StructuredCall) -> dict[str, Any]:
        rows = []
        for req_id in _AREA_REQ_LINE.findall(call.user_text):
            req = requirements.get(req_id)
            domain = map_requirement(req, persona.profile) if req is not None else None
            rows.append({"requirementId": req_id, "skill": "", "area": domain.id if domain else "none"})
        return {"rows": rows}

    return respond


def relevance_responder(persona: Persona) -> Callable[[StructuredCall], dict[str, Any]]:
    def respond(call: StructuredCall) -> dict[str, Any]:
        reqs = {t: (rid, tier) for rid, tier, _k, t in _REQ_LINE.findall(call.user_text)}
        links: dict[str, list[str]] = {}
        best: dict[str, int] = {}
        for text_, (rid, tier) in reqs.items():
            status, _level, fact_ids = _lookup(persona.coverage, text_) or ("unknown", 0, [])
            if status in ("missing_gap", "unknown"):
                continue
            for fid in fact_ids:
                links.setdefault(fid, []).append(rid)
                best[fid] = max(best.get(fid, 0), 92 if tier in ("must", "required") else 62)
        rows = []
        for fid, _kind, _tier, _src, _txt in _FACT_LINE.findall(call.user_text):
            rows.append({"factId": fid, "requirementIds": links.get(fid, [])[:3], "score": best.get(fid, 18)})
        return {"scores": rows}

    return respond


def phrase_responder(
    overrides: Mapping[str, dict[str, Any]] | None = None,
) -> Callable[[StructuredCall], dict[str, Any]]:
    """Each item gets its primary fact's own words (``exact``); ``overrides`` maps an item id to a row to use instead."""
    overrides = overrides or {}

    def respond(call: StructuredCall) -> dict[str, Any]:
        bullets = []
        blocks = re.split(r"^item ", call.user_text, flags=re.MULTILINE)[1:]
        for block in blocks:
            item_id = block.split(" ", 1)[0]
            first = _FACT_LINE.search(block)
            if first is None:
                continue
            row = {
                "itemId": item_id,
                "factIds": [first.group(1)],
                "text": first.group(5).split(" — ")[0],
                "verdict": "exact",
            }
            row.update(overrides.get(item_id, {}))
            bullets.append(row)
        return {"bullets": bullets}

    return respond


def script_pipeline(
    fake: FakeClaudeClient,
    persona: Persona,
    *,
    phrase: Mapping[str, dict[str, Any]] | None = None,
    posting: dict[str, Any] | None = None,
) -> FakeClaudeClient:
    """Script every call of a match for ``persona`` (repeatable)."""
    fake.script_structured(
        "match.posting", posting if posting is not None else posting_answer(persona), repeat=True
    )
    fake.script_structured("match.gates", gate_responder(persona), repeat=True)
    fake.script_structured("match.coverage", coverage_responder(persona), repeat=True)
    fake.script_structured("match.areas", areas_responder(persona), repeat=True)
    fake.script_structured("match.relevance", relevance_responder(persona), repeat=True)
    fake.script_structured("match.phrase", phrase_responder(phrase), repeat=True)
    return fake


def fact_id(book: FactBook, needle: str) -> str:
    """The id of the first fact whose text holds ``needle``."""
    return next(f.id for f in book.facts if needle in f.text)
