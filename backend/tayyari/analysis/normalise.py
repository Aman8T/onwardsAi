"""Pure normalisation of model output: reader answers, the synthesizer's graph, activity lines.

Ported from ``legacy/pipeline.py`` with identical outputs (the unit tests replay the legacy golden baselines in
``tests/golden/legacy``). What changed is only where things come from: every limit is read from
`tayyari.core.limits`, the string helpers from `tayyari.core.text`, and `to_profile` returns a validated
`contract.Profile` with a random profile id instead of the old base-36 timestamp id.

    clean_reader     trim and clamp one reader's answer to the sizes the UI expects
    to_profile       synthesizer output + reader results -> a valid `Profile` (stable ids, deduped names,
                     clamped scores, resolved references)
    activity_lines   the rotating "what the reader is doing" lines shown while a reader works
    result_line      the crisp "6 roles · 4 projects · 23 skills extracted" line when a reader is done

Nothing in this module does I/O, reads the clock (unless `created_at` is left out) or touches the network.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict, TypeGuard

from tayyari.contract import ModelsUsed, Profile, SourceKind, is_source_kind
from tayyari.core import limits
from tayyari.core.ids import new_profile_id
from tayyari.core.text import clip_words, js_round, loose_key, plural, short_label, slug, tidy
from tayyari.ingest import PlannedSource, SourceDoc

# ───────────────────────── numbers and ids ─────────────────────────


def clamp(
    n: Any, lo: int = limits.SCORE_MIN, hi: int = limits.SCORE_MAX, fallback: int = limits.SCORE_MIN
) -> int:
    """A whole number between ``lo`` and ``hi`` (halves round up, like JavaScript); ``fallback`` when ``n`` is not a number.

    Numeric strings are accepted (``"72"``), booleans count as 0 or 1, NaN and infinity fall back.
    """
    if isinstance(n, bool):
        v: float = float(n)
    elif isinstance(n, int | float):
        v = float(n)
    elif isinstance(n, str):
        try:
            v = float(n.strip() or "nan")
        except ValueError:
            return fallback
    else:
        return fallback
    if not math.isfinite(v):
        return fallback
    return js_round(min(hi, max(lo, v)))


def round1(v: float) -> float:
    """Round to one decimal place, halves up (``2.25`` becomes ``2.3``)."""
    return js_round(v * 10) / 10


def is_num(v: Any) -> TypeGuard[int | float]:
    """A finite number (not a bool, not NaN, not infinity)."""
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def unique_id(base: str, used: set[str]) -> str:
    """``base`` or ``base-2``, ``base-3`` ... whichever is not in ``used`` yet; the winner is added to ``used``."""
    pid = base
    n = 2
    while pid in used:
        pid = f"{base}-{n}"
        n += 1
    used.add(pid)
    return pid


_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def base36(n: int) -> str:
    """``n`` in base 36 (``0`` for zero): the legacy profile ids were this timestamp plus random hex."""
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = _BASE36[r] + out
    return out or "0"


def fact_strings(facts: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    """Strings from the first of ``keys`` that holds a usable list: plain strings, or objects with a name/title/label."""
    for k in keys:
        v = facts.get(k)
        if not isinstance(v, list):
            continue
        out: list[str] = []
        for x in v:
            if isinstance(x, str):
                out.append(tidy(x))
            elif isinstance(x, dict):
                out.append(
                    tidy(x.get("name") or x.get("title") or x.get("label") or x.get("full_name") or "")
                )
        out = [x for x in out if x]
        if out:
            return out
    return []


# ───────────────────────── headings, signals, activity lines ─────────────────────────

# Section names the ingesters add themselves; never shown as "what the reader is reading".
_GENERIC_HEADING = re.compile(
    r"^(headings|page text|profile|totals|structured data|ui copy|routes|backend hints|external services|libraries|notes on this data|forks|other original|recent public activity|technologies mentioned|top repositories|profile readme|github:)",
    re.IGNORECASE,
)
_PAGE_HEADING = re.compile(r"^- h[1-3]: (.+)$", re.MULTILINE)
_MD_HEADING = re.compile(r"^#{2,3}[ \t]+(?:\d+\.[ \t]+)?(.+)$", re.MULTILINE)
_HEADING_QUOTES = re.compile(r"^[\"“]|[\"”]$")


def content_headings(content: str) -> list[str]:
    """Page headings (``- h2: Checkout, rebuilt`` lines from the web ingester) or markdown sub-headings, at most ten."""
    page = [tidy(m.group(1)) for m in _PAGE_HEADING.finditer(content)]
    md = [
        h for h in (tidy(m.group(1)) for m in _MD_HEADING.finditer(content)) if not _GENERIC_HEADING.search(h)
    ]
    out: list[str] = []
    for h in page + md:
        h = _HEADING_QUOTES.sub("", h)
        if 2 < len(h) < 70 and h not in out:
            out.append(h)
    return out[:10]


class SignalItem(TypedDict):
    """One chip: a short label and where or how much."""

    label: str
    detail: str


def split_signal(s: str, fallback: str) -> SignalItem:
    """``"TypeScript · 17 repos"`` becomes label ``TypeScript`` and detail ``17 repos``; no separator uses ``fallback``."""
    i = s.find(" · ")
    if i > 0:
        return {
            "label": clip_words(s[:i], limits.READER_SIGNAL_LABEL_MAX_CHARS),
            "detail": clip_words(s[i + 3 :], limits.READER_SIGNAL_DETAIL_MAX_CHARS),
        }
    return {"label": clip_words(s, limits.READER_SIGNAL_LABEL_MAX_CHARS), "detail": fallback}


def quick_detail(raw: SourceDoc) -> str:
    """The ``detail`` of a chip that has none of its own: the source's short name."""
    if raw.kind in ("resume", "portfolio", "notes"):
        return raw.kind
    return raw.label


def activity_lines(raw: SourceDoc) -> list[str]:
    """Rotating, specific activity lines shown while a reader works (never empty)."""
    facts = raw.meta or {}
    heads = fact_strings(facts, ["headings", "sections", "caseStudies", "pages", "titles", "projects"])
    headings = heads or content_headings(raw.text or "")
    lines: list[str] = []
    if raw.kind == "github":
        repos = fact_strings(facts, ["topRepos", "repos", "repositories", "pinned"])
        langs = fact_strings(facts, ["languages"])
        verbs = ["Reading README", "Scanning commits", "Checking tests and CI", "Reading package.json"]
        for i, r in enumerate(repos[:6]):
            lines.append(f"{verbs[i % len(verbs)]} · {r}")
        if langs:
            lines.insert(2, f"Mapping {plural(len(langs), 'language')} across repos")
        if not lines:
            lines += ["Reading repositories", "Scanning READMEs", "Checking tests and CI"]
    elif raw.kind == "resume":
        lines += [f"Reading {h.lower()} · {clip_words(raw.src, 28)}" for h in headings[:5]]
        lines += ["Weighing impact in each bullet", "Separating listed skills from used ones"]
    elif raw.kind == "lovable":
        routes = [r for r in fact_strings(facts, ["routes"]) if r != "/"]
        lines += [f"Opening route · {clip_words(r, 30)}" for r in routes[:2]]
        lines += [f"Walking through · {clip_words(h, 32)}" for h in headings[:4]]
        lines += ["Checking auth and data wiring", "Reviewing the AI features"]
    elif raw.kind == "portfolio":
        lines += [f"Parsing case study: “{clip_words(h, 30)}”" for h in headings[:5]]
        lines.append("Looking for outcomes and numbers")
    elif raw.kind == "linkedin":
        lines += ["Reading headline and about", "Reading experience entries", "Checking featured work"]
    elif raw.kind == "notes":
        lines += ["Reading your notes", "Separating claims from goals"]
    else:
        lines += [f"Reading · {clip_words(h, 34)}" for h in headings[:4]]
        lines.append(f"Extracting evidence · {clip_words(raw.src, 30)}")
    return lines or [f"Reading {clip_words(raw.src, 34)}"]


def result_line(out: Mapping[str, Any]) -> str:
    """Crisp result line, e.g. ``6 roles · 4 projects · 23 skills extracted`` or ``34 repos · 9 languages · 18 skills extracted``."""
    parts = [p.strip() for p in re.split(r"\s*·\s*", str(out.get("sub") or "")) if p.strip()]
    if any(re.search(r"skill", p, re.IGNORECASE) for p in parts):
        return f"{' · '.join(parts[:3])} extracted"
    return " · ".join([*parts[:2], f"{plural(len(out.get('skills') or []), 'skill')} extracted"])


# ───────────────────────── one reader's answer ─────────────────────────


def clean_reader(r: Mapping[str, Any]) -> dict[str, Any]:
    """Trim and clamp a reader's output to the limits the UI expects (see `core.limits`, ``READER_*``)."""
    person_in = r.get("person") if isinstance(r.get("person"), Mapping) else None
    person: dict[str, Any] = {}
    if person_in:
        if tidy(person_in.get("name")):
            person["name"] = tidy(person_in.get("name"))
        if tidy(person_in.get("currentRole")):
            person["currentRole"] = tidy(person_in.get("currentRole"))
        yrs = person_in.get("yearsExperience")
        if is_num(yrs) and yrs >= 0:
            person["yearsExperience"] = round1(float(yrs))
        if tidy(person_in.get("location")):
            person["location"] = tidy(person_in.get("location"))
    out: dict[str, Any] = {
        "sub": clip_words(r.get("sub"), limits.READER_SUB_MAX_CHARS),
        "summary": tidy(r.get("summary")),
    }
    if person:
        out["person"] = person
    signals = [
        {
            "label": clip_words(s.get("label"), limits.READER_SIGNAL_LABEL_MAX_CHARS),
            "detail": clip_words(s.get("detail"), limits.READER_SIGNAL_DETAIL_MAX_CHARS),
        }
        for s in (r.get("signals") or [])
        if isinstance(s, Mapping)
    ]
    out["signals"] = [s for s in signals if s["label"]][: limits.READER_SIGNALS_MAX]
    skills = [
        {
            "name": clip_words(s.get("name"), limits.READER_SKILL_NAME_MAX_CHARS),
            "evidence": clip_words(s.get("evidence"), limits.READER_SKILL_EVIDENCE_MAX_CHARS),
            "strength": clamp(
                s.get("strength"),
                limits.READER_STRENGTH_MIN,
                limits.READER_STRENGTH_MAX,
                limits.READER_STRENGTH_DEFAULT,
            ),
        }
        for s in (r.get("skills") or [])
        if isinstance(s, Mapping)
    ]
    out["skills"] = [s for s in skills if s["name"]][: limits.READER_SKILLS_MAX]
    out["notes"] = [
        n for n in (clip_words(n, limits.READER_NOTE_MAX_CHARS) for n in (r.get("notes") or [])) if n
    ][: limits.READER_NOTES_MAX]
    return out


# ───────────────────────── reading results ─────────────────────────


@dataclass
class ReadResult:
    """What reading one planned source came to: the fetched document, the cleaned reader output, or why not."""

    planned: PlannedSource
    raw: SourceDoc | None = None
    reader: dict[str, Any] | None = None
    error: str | None = None
    ai_error: BaseException | None = None
    extra_notes: list[str] = field(default_factory=list)
    """Notes added by source triage (a caveat about the page). `to_profile` keeps them in the source's popover,
    ahead of the model's own notes, within the same cap. Not part of the legacy dataclass."""


# ───────────────────────── the graph ─────────────────────────


def _fallback_sub(r: ReadResult) -> str:
    f: Mapping[str, Any] = r.raw.meta if r.raw and isinstance(r.raw.meta, Mapping) else {}

    def num(k: str) -> int | None:
        v = f.get(k)
        return int(v) if is_num(v) else None

    parts: list[str] = []
    repos = num("repos") if num("repos") is not None else num("publicRepos")
    if repos is not None:
        parts.append(plural(repos, "repo"))
    langs = len(f["languages"]) if isinstance(f.get("languages"), list) else 0
    if langs:
        parts.append(plural(langs, "language"))
    pages = num("pages")
    if not parts and pages is not None:
        parts.append(plural(pages, "page"))
    cases = num("caseStudies")
    if cases is not None:
        parts.append(plural(cases, "case study", "case studies"))
    if parts:
        return " · ".join(parts)
    if not r.reader and r.error:
        return "Could not be read"
    return (r.raw.kind_label if r.raw else "") or r.planned.kind_label


def _iso_z(moment: datetime) -> str:
    """UTC with milliseconds and a trailing ``Z`` (``2026-10-07T17:09:12.976Z``), as ``Date.toISOString``."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_profile(
    synth: Mapping[str, Any],
    results: Sequence[ReadResult],
    goal: str | None,
    sample: bool,
    *,
    models: ModelsUsed,
    profile_id: str | None = None,
    created_at: datetime | None = None,
) -> Profile:
    """Turn the synthesizer's output into a valid `Profile`: stable ids, deduped names, clamped scores, resolved references.

    ``profile_id`` defaults to a fresh `new_profile_id` (``sample-`` prefixed when ``sample``); ``created_at`` to now.
    ``models`` is what the profile's "built with" footnote shows.
    """
    ok_ids = [r.planned for r in results if r.reader]
    used: set[str] = {"you", *(r.planned.id for r in results)}

    # source id resolution: exact, case-insensitive, label, kind
    def resolve_source(raw: Any) -> str | None:
        s = tidy(raw)
        if not s:
            return None
        lower = s.lower()
        exact = next((p for p in ok_ids if p.id == s), None) or next(
            (p for p in ok_ids if p.id.lower() == lower), None
        )
        if exact:
            return exact.id
        by_label = [p for p in ok_ids if p.label.lower() == lower or loose_key(p.label) == loose_key(s)]
        if len(by_label) == 1:
            return by_label[0].id
        by_kind = [p for p in ok_ids if p.kind == lower]
        if len(by_kind) == 1:
            return by_kind[0].id
        return None

    # roles first (titles are needed for skill.unlocks)
    raw_roles: list[Mapping[str, Any]] = []
    candidate_roles = [r for r in synth.get("roles") or [] if isinstance(r, Mapping) and tidy(r.get("title"))]
    for r in sorted(candidate_roles, key=lambda r: -clamp(r.get("alignment"))):
        if all(loose_key(x.get("title")) != loose_key(r.get("title")) for x in raw_roles):
            raw_roles.append(r)
    raw_roles = raw_roles[: limits.PROFILE_ROLES_MAX]
    role_titles = [clip_words(r.get("title"), limits.ROLE_TITLE_MAX_CHARS) for r in raw_roles]

    def match_role(t: Any) -> str | None:
        k = loose_key(t)
        if not k:
            return None
        return next((title for title in role_titles if loose_key(title) == k), None) or next(
            (title for title in role_titles if k in loose_key(title) or loose_key(title) in k), None
        )

    # domains
    domain_alias: dict[str, str] = {}
    domains: list[dict[str, Any]] = []
    for d in synth.get("domains") or []:
        if len(domains) >= limits.PROFILE_DOMAINS_MAX:
            break
        if not isinstance(d, Mapping):
            continue
        name = tidy(d.get("name")) or tidy(d.get("short"))
        if not name or any(loose_key(x["name"]) == loose_key(name) for x in domains):
            continue
        did = unique_id(slug(tidy(d.get("id")) or name), used)
        for alias in (d.get("id"), slug(tidy(d.get("id"))), name, d.get("short")):
            if tidy(alias):
                domain_alias[loose_key(alias)] = did
        domains.append(
            {
                "id": did,
                "name": clip_words(name, limits.DOMAIN_NAME_MAX_CHARS),
                "short": short_label(tidy(d.get("short")) or name, limits.DOMAIN_SHORT_MAX_CHARS),
                "score": clamp(d.get("score")),
                "summary": tidy(d.get("summary")),
            }
        )

    mastery_in: Mapping[str, Any] = m if isinstance(m := synth.get("mastery"), Mapping) else {}
    if not domains:
        domains.append(
            {
                "id": unique_id("core", used),
                "name": "Core skills",
                "short": "Core",
                "score": clamp(mastery_in.get("score")),
                "summary": "",
            }
        )

    # skills
    skill_alias: dict[str, str] = {}
    skills: list[dict[str, Any]] = []
    for s in synth.get("skills") or []:
        if len(skills) >= limits.PROFILE_SKILLS_MAX:
            break
        if not isinstance(s, Mapping):
            continue
        name = tidy(s.get("name"))
        if not name or any(loose_key(x["name"]) == loose_key(name) for x in skills):
            continue
        sid = unique_id(slug(tidy(s.get("id")) or name), used)
        for alias in (s.get("id"), name):
            if tidy(alias):
                skill_alias[loose_key(alias)] = sid
        dk = loose_key(s.get("domainId"))
        domain_id = (
            domain_alias.get(dk)
            or next(
                (
                    v
                    for k, v in domain_alias.items()
                    if (len(dk) >= 4 and dk in k) or (len(k) >= 4 and k in dk)
                ),
                None,
            )
            or domains[0]["id"]
        )
        mapped = [
            {
                "sourceId": resolve_source(e.get("sourceId")) or "",
                "text": clip_words(e.get("text"), limits.SKILL_EVIDENCE_TEXT_MAX_CHARS),
            }
            for e in s.get("evidence") or []
            if isinstance(e, Mapping)
        ]
        # as in Node: keep the first occurrence of each text, even when that occurrence had no valid source
        first_text: dict[str, int] = {}
        for i, e in enumerate(mapped):
            first_text.setdefault(e["text"], i)
        evidence = [
            e for i, e in enumerate(mapped) if e["sourceId"] and e["text"] and first_text[e["text"]] == i
        ][: limits.SKILL_EVIDENCE_MAX]
        unlocks: list[str] = []
        for u in s.get("unlocks") or []:
            title = match_role(u) or clip_words(u, limits.ROLE_TITLE_MAX_CHARS)
            if title and title not in unlocks:
                unlocks.append(title)
        skills.append(
            {
                "id": sid,
                "name": clip_words(name, limits.SKILL_NAME_MAX_CHARS),
                "domainId": domain_id,
                "score": clamp(s.get("score")),
                "evidence": evidence,
                "unlocks": unlocks[: limits.SKILL_UNLOCKS_MAX],
            }
        )

    # Drop domains nobody points at (keep at least two), then group skills by domain order.
    populated = [d for d in domains if any(s["domainId"] == d["id"] for s in skills)]
    final_domains = populated if len(populated) >= 2 else domains
    order = {d["id"]: i for i, d in enumerate(final_domains)}
    skills = [s if s["domainId"] in order else {**s, "domainId": final_domains[0]["id"]} for s in skills]
    skills = [s for _, s in sorted(enumerate(skills), key=lambda t: (order[t[1]["domainId"]], t[0]))]

    skill_by_id = {s["id"]: s for s in skills}

    def resolve_skill(raw: Any, name: str) -> str | None:
        if isinstance(raw, str) and raw in skill_by_id:
            return raw
        for cand in (raw, name):
            if not tidy(cand):
                continue
            k = loose_key(cand)
            hit = skill_alias.get(k) or next(
                (s["id"] for s in skills if loose_key(s["name"]) in (k, re.sub(r"s$", "", k))), None
            )
            if hit:
                return hit
        return None

    # roles
    roles: list[dict[str, Any]] = []
    for ri, r in enumerate(raw_roles):
        requirements: list[dict[str, Any]] = []
        for q in r.get("requirements") or []:
            if len(requirements) >= limits.ROLE_REQUIREMENTS_MAX:
                break
            if not isinstance(q, Mapping):
                continue
            name = tidy(q.get("name"))
            if not name:
                continue
            skill_id = resolve_skill(q.get("skillId"), name)
            skill = skill_by_id.get(skill_id) if skill_id else None
            label = clip_words(skill["name"] if skill else name, limits.REQUIREMENT_NAME_MAX_CHARS)
            # dedupe on the resolved identity: two paraphrases of one skill must not both appear
            if any(
                (skill_id and x.get("skillId") == skill_id) or loose_key(x["name"]) == loose_key(label)
                for x in requirements
            ):
                continue
            current = skill["score"] if skill else clamp(q.get("current"))
            target = clamp(q.get("target"), limits.SCORE_MIN, limits.SCORE_MAX, 70)
            have = current >= 55 if q.get("have") else current >= target
            if not have and target <= current:
                target = min(limits.SCORE_MAX, current + 10)
            req: dict[str, Any] = {"name": label}
            if skill_id:
                req["skillId"] = skill_id
            req.update({"have": have, "current": current, "target": target})
            requirements.append(req)
        roles.append(
            {
                "id": unique_id(slug(tidy(r.get("id")) or tidy(r.get("title"))), used),
                "title": role_titles[ri],
                "alignment": clamp(r.get("alignment")),
                "eta": tidy(r.get("eta")) or "~6 months",
                "why": clip_words(r.get("why"), limits.ROLE_WHY_MAX_CHARS),
                "requirements": requirements,
            }
        )

    # sources
    synth_sources: dict[str, Mapping[str, Any]] = {}
    for s in synth.get("sources") or []:
        if not isinstance(s, Mapping):
            continue
        src_id = resolve_source(s.get("id"))
        if src_id and src_id not in synth_sources:
            synth_sources[src_id] = s
    sources: list[dict[str, Any]] = []
    for res in results:
        p = res.planned
        ss = synth_sources.get(p.id)
        raw_notes = (ss.get("notes") if ss and ss.get("notes") else (res.reader or {}).get("notes")) or []
        notes = [n for n in (clip_words(n, limits.PROFILE_SOURCE_NOTE_MAX_CHARS) for n in raw_notes) if n][
            : limits.PROFILE_SOURCE_NOTES_MAX
        ]
        if res.extra_notes:
            extra = [
                n for n in (clip_words(n, limits.PROFILE_SOURCE_NOTE_MAX_CHARS) for n in res.extra_notes) if n
            ]
            extra = extra[: limits.PROFILE_SOURCE_NOTES_MAX]
            notes = [*notes[: limits.PROFILE_SOURCE_NOTES_MAX - len(extra)], *extra]
        url = (res.raw.url if res.raw else None) or p.url
        entry: dict[str, Any] = {
            "id": p.id,
            "kind": p.kind,
            "label": (res.raw.label if res.raw else "") or p.label,
        }
        if url:
            entry["url"] = url
        entry.update(
            {
                "sub": clip_words(
                    tidy(ss.get("sub") if ss else "") or (res.reader or {}).get("sub") or _fallback_sub(res),
                    limits.PROFILE_SOURCE_SUB_MAX_CHARS,
                ),
                "summary": res.reader["summary"]
                if res.reader
                else (f"Could not be read: {res.error}" if res.error else ""),
                "notes": notes,
                "ok": bool(res.reader),
            }
        )
        if not res.reader and res.error:
            entry["error"] = res.error
        sources.append(entry)

    # person
    reader_people = [
        r.reader["person"]
        for r in sorted(results, key=lambda r: 0 if r.planned.kind == "resume" else 1)
        if r.reader and r.reader.get("person")
    ]

    def rp(k: str) -> str:
        return next((tidy(x.get(k)) for x in reader_people if tidy(x.get(k))), "")

    sp: Mapping[str, Any] = m if isinstance(m := synth.get("person"), Mapping) else {}
    synth_name = tidy(sp.get("name"))
    name = synth_name if synth_name and synth_name.lower() != "you" else rp("name") or "You"
    current_role = tidy(sp.get("currentRole")) or rp("currentRole") or "Professional"
    location = tidy(sp.get("location")) or rp("location") or None
    yrs_raw = (
        sp.get("yearsExperience")
        if is_num(sp.get("yearsExperience"))
        else next((x["yearsExperience"] for x in reader_people if is_num(x.get("yearsExperience"))), None)
    )
    years = round1(float(yrs_raw)) if yrs_raw is not None and yrs_raw >= 0 else None
    headline = tidy(sp.get("headline")) or " · ".join(
        part
        for part in [
            current_role,
            f"{years} yrs" if years is not None else "",
            location.split(",")[0].strip() if location else "",
        ]
        if part
    )
    person: dict[str, Any] = {
        "name": name,
        "headline": clip_words(headline, limits.PERSON_HEADLINE_MAX_CHARS),
        "currentRole": clip_words(current_role, limits.PERSON_ROLE_MAX_CHARS),
    }
    if location:
        person["location"] = location
    if years is not None:
        person["yearsExperience"] = years
    person["summary"] = tidy(sp.get("summary"))

    # the read: pieces of prose, those that rest on a skill resolved to a skill of the graph; too little of it is none
    read: list[dict[str, Any]] = []
    read_chars = 0
    for seg in synth.get("read") or []:
        if len(read) >= limits.READ_SEGMENTS_MAX:
            break
        if isinstance(seg, str):
            seg = {"text": seg}
        if not isinstance(seg, Mapping):
            continue
        text = seg.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        text = re.sub(r"\s+", " ", text)[: limits.READ_SEGMENT_MAX_CHARS]
        if read_chars + len(text) > limits.READ_MAX_CHARS:
            break
        read_chars += len(text)
        piece: dict[str, Any] = {"text": text}
        skill_ref = resolve_skill(seg.get("skillId"), "") if tidy(seg.get("skillId")) else None
        if skill_ref:
            piece["skillId"] = skill_ref
        read.append(piece)
    if len(read) < 2 or read_chars < 40:
        read = []

    profile: dict[str, Any] = {
        "id": profile_id or new_profile_id(sample=sample),
        "createdAt": _iso_z(created_at or datetime.now(UTC)),
    }
    if goal:
        profile["goal"] = goal
    profile.update(
        {
            "person": person,
            "mastery": {
                "score": clamp(mastery_in.get("score")),
                "insight": clip_words(mastery_in.get("insight"), limits.MASTERY_INSIGHT_MAX_CHARS),
            },
            "sources": sources,
            "domains": final_domains,
            "skills": skills,
            "roles": roles,
            "models": models.model_dump(),
        }
    )
    if read:
        profile["read"] = read
    return Profile.from_dict(profile)


def source_kind_or_link(kind: str) -> SourceKind:
    """``kind`` when it is a known `SourceKind`, else ``"link"`` (the generic web page)."""
    return kind if is_source_kind(kind) else "link"


__all__ = [
    "ReadResult",
    "SignalItem",
    "activity_lines",
    "base36",
    "clamp",
    "clean_reader",
    "content_headings",
    "fact_strings",
    "is_num",
    "quick_detail",
    "result_line",
    "round1",
    "source_kind_or_link",
    "split_signal",
    "to_profile",
    "unique_id",
]
