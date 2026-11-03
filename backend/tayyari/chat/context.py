"""The coach's system prompt, rendered as three blocks laid out for prompt caching.

The legacy prompt was one string that started with a per-person name and a per-thread "why they are
here" section, so no two requests shared a prefix. Prompt caching is a prefix match (tools, then system,
then messages), so the same text is split by how often it changes:

    1. ``rules``    `CHAT_RULES`: the static coach rules. No name, no date: identical for everyone.
    2. ``profile``  the person's graph as JSON, what each reader found, the raw source excerpts, and their
                    first name. Identical for every thread and turn of one profile. The Claude call puts
                    the cache breakpoint here, which caches the tool definitions, the rules and this block.
    3. ``origin``   which node the user clicked and the matching guide. Per thread, after the breakpoint.

Rendering is a pure function of its inputs: no timestamps, ids or randomness, and every collection keeps
the order it has in the stored profile. The same input twice gives byte-identical strings, so a changed
byte can only come from a changed profile. `ChatPromptRenderer` memoises (rules, profile) per profile in a
small in-process LRU keyed by a hash of the canonical profile and context JSON.

Differences from `tayyari.prompts.chat.build_chat_system` (the legacy wording, kept line for line otherwise):

    * the opening line says "career coach for the user" and the name moves to an "About the user" section
      at the top of the profile block, as a sentence that also says how to address them;
    * the web tools are called "web search and web fetch" (the UI names WebSearch / WebFetch are
      the client's business, and the model sees the tools as web_search / web_fetch);
    * "## Why they are here" comes after the profile instead of before the rules.

There is deliberately no date in any block: the legacy prompt had none, and one would change the cached
block every day.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from tayyari.contract.stored import StoredContext
from tayyari.core import limits
from tayyari.llm.types import TextBlock
from tayyari.prompts.chat import ORIGIN_GUIDE, budget_excerpts
from tayyari.prompts.common import _drop_none, _json, _num, escape_attr

CHAT_RULES: Final[
    str
] = """You are Leafout, a candid, specific and warm career coach for the user. You have already analysed their
resume and public work (below): a skill graph of their current role and a map of their most probable next roles. You talk
like a senior mentor who has read every repo and bullet: direct, generous with specifics, never generic.

## How to answer
- Ground every recommendation in THEIR evidence: name their actual repos, projects, employers, case studies and scores
  ("motion-kit already proves X; notes-ai is where Y is missing"). Generic advice that could apply to anyone is a failure.
- Give concrete plans: weeks or phases, specific deliverables, how to show each one publicly (repo, README, write-up,
  demo, resume bullet), and how it moves their skill scores and role alignment on the graph.
- Use markdown: short "###" headers, tight bullets, and a small table when comparing options or laying out a plan.
  Keep it scannable; most answers fit in 200–450 words unless they ask for depth.
- Never invent facts about them. If the evidence is missing or unclear, say so plainly and ask, or suggest how to show it.
- Be honest about gaps and timelines; encourage without flattering. Use their first name sparingly.
- You may use web search and web fetch sparingly — to pull current, high-quality resources (docs, courses, papers) or to
  inspect one of their public repos or pages when it would sharpen the advice. Cite links you use. Do not browse when the
  profile already answers the question.
- End with ONE sharp follow-up question or one concrete next step they can do today."""
"""Block 1: the static rules. Same text for every person and thread; the marker-free head of the cached prefix."""

MAX_FIRST_NAME_CHARS: Final[int] = 60
"""A first name longer than this is cut (a profile name comes from model output on untrusted text)."""

_EMPTY_READERS: Final[str] = "(no reader details stored)"
_EMPTY_EXCERPTS: Final[str] = "(no raw source text stored)"

# ───────────────────────── the pieces ─────────────────────────


def first_name(person: Mapping[str, Any]) -> str | None:
    """The first word of ``person.name``, or None when there is no usable name (``"You"`` is the placeholder)."""
    full_name = str(person.get("name") or "")
    name = full_name.split(" ")[0] if full_name and full_name != "You" else ""
    return name[:MAX_FIRST_NAME_CHARS] or None


def render_about_user(person: Mapping[str, Any]) -> str:
    """How to address the person; the only per-person wording outside the data sections."""
    name = first_name(person)
    if name is None:
        return '## About the user\nTheir name is not known: address them directly as "you".'
    return f'## About the user\nTheir first name is {name}: address them as "{name}" (sparingly).'


def build_graph(profile: Mapping[str, Any]) -> dict[str, Any]:
    """The whole graph the user is looking at, as the compact structure the coach reads (legacy shape and order)."""
    return {
        "person": dict(profile.get("person") or {}),
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


def render_reader_notes(readers: Mapping[str, Mapping[str, Any]]) -> str:
    """Every reader's summary, notes and strongest skills, one paragraph per source."""
    return "\n\n".join(
        f"[{sid}] {r.get('summary', '')}\n  notes: {' | '.join(r.get('notes') or [])}\n  - "
        + "\n  - ".join(
            f"{s.get('name')} ({_num(s.get('strength'))}/5): {s.get('evidence')}"
            for s in (r.get("skills") or [])[: limits.CHAT_READER_SKILLS_PER_SOURCE]
        )
        for sid, r in readers.items()
    )


def render_excerpts(sources: Sequence[Mapping[str, Any]], budget: int) -> str:
    """Raw source text that shares ``budget`` characters (resume first), each in a ``<source>`` element."""
    return "\n\n".join(
        f'<source id="{s["id"]}" kind="{s["kind"]}" label="{escape_attr(s["label"])}"'
        + (f' url="{escape_attr(s["url"])}"' if s.get("url") else "")
        + f">\n{s['text']}\n</source>"
        for s in budget_excerpts(sources, budget)
    )


def render_profile_block(
    profile: Mapping[str, Any],
    context: StoredContext,
    *,
    budget: int = limits.CHAT_EXCERPT_BUDGET_CHARS,
) -> str:
    """Block 2: the person's name and how to address them, then graph, reader findings and source excerpts."""
    sources = [s.model_dump() for s in context.sources]
    return f"""{render_about_user(profile.get("person") or {})}

## Their profile (the graph they are looking at)
{_json(build_graph(profile))}

## What each reader found
{render_reader_notes(context.readers) or _EMPTY_READERS}

## Raw source excerpts
{render_excerpts(sources, budget) or _EMPTY_EXCERPTS}"""


def render_origin_block(origin: Mapping[str, Any] | None) -> str:
    """Block 3: what the user clicked to open this thread, and the guide for that kind of question."""
    if origin:
        ref = f', id "{origin["refId"]}"' if origin.get("refId") else ""
        cap = f" · {origin['caption']}" if origin.get("caption") else ""
        guide = ORIGIN_GUIDE.get(str(origin.get("kind")), ORIGIN_GUIDE["free"])
        where = (
            f'The user opened this conversation by clicking "{origin.get("title", "")}" '
            f"({origin.get('kind')}{ref}{cap}) on their graph.\n{guide}"
        )
    else:
        where = ORIGIN_GUIDE["free"]
    return f"## Why they are here\n{where}"


# ───────────────────────── blocks and the renderer ─────────────────────────


@dataclass(frozen=True, slots=True)
class ChatPromptBlocks:
    """The three system blocks of one chat request, in request order."""

    rules: str
    profile: str
    origin: str

    def system_blocks(self) -> list[TextBlock]:
        """The blocks as `TextBlock`s: the cache breakpoint sits on the profile block, the only large stable one."""
        return [
            TextBlock(self.rules, cache=False),
            TextBlock(self.profile, cache=True),
            TextBlock(self.origin, cache=False),
        ]

    @property
    def text(self) -> str:
        """All three blocks joined the way the model reads them (for logs, tests and token estimates)."""
        return "\n\n".join((self.rules, self.profile, self.origin))


def profile_fingerprint(
    profile: Mapping[str, Any], context: StoredContext, budget: int = limits.CHAT_EXCERPT_BUDGET_CHARS
) -> str:
    """sha256 of the canonical JSON of everything the (rules, profile) blocks are rendered from.

    Keys are sorted and non-ASCII is escaped, so the value does not depend on dict order or on how a
    store spelled the document; two profiles with the same content share a key.
    """
    canonical = json.dumps(
        {"budget": budget, "context": context.to_dict(), "profile": profile},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class ChatPromptRenderer:
    """Renders `ChatPromptBlocks`, memoising the (rules, profile) pair per profile in a small LRU.

    One instance per process (the chat service owns it). Rendering is synchronous and touches no I/O, so
    the LRU needs no lock on the event loop. The origin block is cheap and is rendered every time.
    """

    def __init__(self, *, budget: int = limits.CHAT_EXCERPT_BUDGET_CHARS, max_entries: int = 16) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._budget = budget
        self._max_entries = max_entries
        self._lru: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def render(
        self,
        profile: Mapping[str, Any],
        context: StoredContext,
        origin: Mapping[str, Any] | None,
    ) -> ChatPromptBlocks:
        key = profile_fingerprint(profile, context, self._budget)
        cached = self._lru.get(key)
        if cached is None:
            self.misses += 1
            cached = (CHAT_RULES, render_profile_block(profile, context, budget=self._budget))
            self._lru[key] = cached
            while len(self._lru) > self._max_entries:
                self._lru.popitem(last=False)
        else:
            self.hits += 1
            self._lru.move_to_end(key)
        return ChatPromptBlocks(cached[0], cached[1], render_origin_block(origin))

    def __len__(self) -> int:
        return len(self._lru)

    def clear(self) -> None:
        self._lru.clear()


__all__ = [
    "CHAT_RULES",
    "MAX_FIRST_NAME_CHARS",
    "ChatPromptBlocks",
    "ChatPromptRenderer",
    "build_graph",
    "first_name",
    "profile_fingerprint",
    "render_about_user",
    "render_excerpts",
    "render_origin_block",
    "render_profile_block",
    "render_reader_notes",
]
