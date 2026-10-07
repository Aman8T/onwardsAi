"""Sonnet chat coach system prompt and prompt builder, with limits from tayyari.core.limits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tayyari.core import limits

from .common import _drop_none, _json, _num, clip, escape_attr

ORIGIN_GUIDE: dict[str, str] = {
    "skill": 'They clicked a SKILL node. Give a preparation plan for that topic: where they stand (with their evidence), what "good" looks like at the next level, a week-by-week plan, one portfolio-grade deliverable that proves it publicly, and how it moves this skill score and the roles it unlocks.',
    "domain": "They clicked a DOMAIN node. Diagnose the domain: strongest and weakest skills inside it (named, with scores), the one or two skills that would move the domain score most, and a focused plan.",
    "source": "They clicked a SOURCE node (one of their connected sources). Give a concrete improvement plan for that source: for GitHub, repo-by-repo fixes (README, screenshots, tests, CI, pinning, what to build next); for a resume, rewritten bullets using their real facts; for a Lovable app or portfolio, what to add so it reads as product-grade evidence. Prioritise by impact on how they are perceived.",
    "role": "They clicked a NEXT-ROLE node. Explain how to prepare for that role: honest alignment today, the gaps that matter most (with current → target), a phased plan with a timeline at ~6 hrs/week, deliverables that prove readiness publicly, and how to position their existing work for it.",
    "requirement": "They clicked one REQUIREMENT of a target role. Explain what the role expects for it, where they are now (current → target), and a tight plan to close the gap with a deliverable that proves it.",
    "you": "They clicked their own node (role mastery). Give an overall read: what defines their standing, the 2–3 highest-leverage focus areas for the next quarter, and in which order.",
    "free": "They are asking a free-form question about their career.",
}


def build_chat_system(
    profile: Mapping[str, Any],
    origin: Mapping[str, Any] | None,
    sources: Sequence[Mapping[str, Any]],
    readers: Mapping[str, Mapping[str, Any]],
    budget: int = limits.CHAT_EXCERPT_BUDGET_CHARS,
) -> str:
    """
    The coach's system prompt: the whole graph as compact JSON, every reader's findings, and raw source excerpts
    that share `budget` characters (resume first). `sources` entries carry id, label, kind, content and optionally url.
    """
    person = dict(profile.get("person") or {})
    graph = {
        "person": person,
        "goal": profile.get("goal"),
        "mastery": profile.get("mastery"),
        "domains": [
            {"id": d["id"], "name": d["name"], "score": d["score"], "summary": d["summary"]}
            for d in profile.get("domains", [])
        ],
        "skills": [
            {
                "id": s["id"],
                "name": s["name"],
                "domain": s["domainId"],
                "score": s["score"],
                "evidence": [f"[{e['sourceId']}] {e['text']}" for e in s.get("evidence", [])],
                "unlocks": s.get("unlocks", []),
            }
            for s in profile.get("skills", [])
        ],
        "roles": [
            {
                "id": r["id"],
                "title": r["title"],
                "alignment": r["alignment"],
                "eta": r["eta"],
                "why": r["why"],
                "requirements": [
                    f"{q['name']}: {_num(q['current'])}/{_num(q['target'])}"
                    + (" (have)" if q.get("have") else " (gap)")
                    for q in r.get("requirements", [])
                ],
            }
            for r in profile.get("roles", [])
        ],
        "sources": [
            _drop_none(
                {
                    "id": s["id"],
                    "label": s["label"],
                    "kind": s["kind"],
                    "url": s.get("url"),
                    "sub": s.get("sub"),
                    "ok": s.get("ok"),
                    "summary": s.get("summary"),
                    "notes": s.get("notes"),
                }
            )
            for s in profile.get("sources", [])
        ],
    }

    reader_notes = "\n\n".join(
        f"[{sid}] {r.get('summary', '')}\n  notes: {' | '.join(r.get('notes') or [])}\n  - "
        + "\n  - ".join(
            f"{s.get('name')} ({_num(s.get('strength'))}/5): {s.get('evidence')}"
            for s in (r.get("skills") or [])[: limits.CHAT_READER_SKILLS_PER_SOURCE]
        )
        for sid, r in readers.items()
    )

    excerpts = "\n\n".join(
        f'<source id="{s["id"]}" kind="{s["kind"]}" label="{escape_attr(s["label"])}"'
        + (f' url="{escape_attr(s["url"])}"' if s.get("url") else "")
        + f">\n{s['text']}\n</source>"
        for s in budget_excerpts(sources, budget)
    )

    if origin:
        ref = f', id "{origin["refId"]}"' if origin.get("refId") else ""
        cap = f" · {origin['caption']}" if origin.get("caption") else ""
        guide = ORIGIN_GUIDE.get(str(origin.get("kind")), ORIGIN_GUIDE["free"])
        where = f'The user opened this conversation by clicking "{origin.get("title", "")}" ({origin.get("kind")}{ref}{cap}) on their graph.\n{guide}'
    else:
        where = ORIGIN_GUIDE["free"]

    full_name = str(person.get("name") or "")
    name = full_name.split(" ")[0] if full_name and full_name != "You" else None

    return f"""You are Tayyari, a candid, specific and warm career coach for {name or "the user"}. You have already analysed their
resume and public work (below): a skill graph of their current role and a map of their most probable next roles. You talk
like a senior mentor who has read every repo and bullet: direct, generous with specifics, never generic.

## Why they are here
{where}

## How to answer
- Ground every recommendation in THEIR evidence: name their actual repos, projects, employers, case studies and scores
  ("motion-kit already proves X; notes-ai is where Y is missing"). Generic advice that could apply to anyone is a failure.
- Give concrete plans: weeks or phases, specific deliverables, how to show each one publicly (repo, README, write-up,
  demo, resume bullet), and how it moves their skill scores and role alignment on the graph.
- Use markdown: short "###" headers, tight bullets, and a small table when comparing options or laying out a plan.
  Keep it scannable; most answers fit in 200–450 words unless they ask for depth.
- Never invent facts about them. If the evidence is missing or unclear, say so plainly and ask, or suggest how to show it.
- Be honest about gaps and timelines; encourage without flattering. Use their first name sparingly.
- You may use WebSearch / WebFetch sparingly — to pull current, high-quality resources (docs, courses, papers) or to
  inspect one of their public repos or pages when it would sharpen the advice. Cite links you use. Do not browse when the
  profile already answers the question.
- End with ONE sharp follow-up question or one concrete next step they can do today.

## Their profile (the graph they are looking at)
{_json(graph)}

## What each reader found
{reader_notes or "(no reader details stored)"}

## Raw source excerpts
{excerpts or "(no raw source text stored)"}"""


_RANK = {
    k: i for i, k in enumerate(["resume", "github", "lovable", "portfolio", "linkedin", "link", "notes"])
}


def budget_excerpts(sources: Sequence[Mapping[str, Any]], total: int) -> list[dict[str, Any]]:
    """Split a total character budget across sources, resume first; leftovers flow to later sources."""
    ordered = sorted(
        (s for s in sources if str(s.get("content") or "").strip()),
        key=lambda s: _RANK.get(str(s.get("kind")), -1),
    )
    remaining = total
    out: list[dict[str, Any]] = []
    for i, s in enumerate(ordered):
        left = len(ordered) - i
        share = remaining // left
        if s.get("kind") == "resume":
            share = max(share, min(remaining, limits.CHAT_RESUME_EXCERPT_MIN_CHARS))
        text = clip(str(s["content"]).strip(), share)
        remaining = max(0, remaining - len(text))
        out.append({**s, "text": text})
    return out
