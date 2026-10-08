"""Shared helpers for the facts/copy/ground/fallback tests (unit A): fixture loaders and a scripted "good Haiku".

`scripted_facts(ctx)` reads the source texts of a fixture with a few regular expressions and returns the answer a
careful extraction model would give: facts that point at line numbers (`line`, `highlightLines`, `detailLine`) with
the short fields (title, employer, ISO dates ...) typed out. It exists so the tests can run the real verification
(`verify_facts`) and the real fallback over every fixture without a model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from tayyari.contract.models import Profile
from tayyari.contract.portfolio import (
    Fact,
    GraphDomain,
    GraphMastery,
    GraphSkill,
    GraphSnapshot,
    PortfolioPerson,
)
from tayyari.contract.stored import StoredContext
from tayyari.portfolio.facts import split_lines, verify_facts

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "portfolio"
FIXTURE_NAMES = sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))
PERSONAS = ["riya", "aarav", "meera", "vikram", "sana"]


@dataclass(frozen=True)
class PortfolioFixture:
    name: str
    about: str
    profile: Profile
    ctx: StoredContext


@cache
def load_fixture(name: str) -> PortfolioFixture:
    raw = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return PortfolioFixture(
        name=name,
        about=raw["about"],
        profile=Profile.from_dict(raw["profile"]),
        ctx=StoredContext.model_validate(raw["context"]),
    )


def person_for(profile: Profile) -> PortfolioPerson:
    p = profile.person
    words = p.name.split()
    monogram = "".join(w[0] for w in words[:2]).upper() or p.name[:1]
    return PortfolioPerson(
        name=p.name,
        headline=p.headline,
        role=p.currentRole,
        location=p.location,
        yearsExperience=p.yearsExperience,
        monogram=monogram,
    )


def graph_for(profile: Profile) -> GraphSnapshot:
    domains = []
    for d in profile.domains:
        skills = sorted((s for s in profile.skills if s.domainId == d.id), key=lambda s: -s.score)
        domains.append(
            GraphDomain(
                id=d.id,
                name=d.name,
                short=d.short,
                score=d.score,
                skills=[
                    GraphSkill(
                        id=s.id,
                        name=s.name,
                        score=s.score,
                        evidence=s.evidence[0].text if s.evidence else None,
                    )
                    for s in skills
                ],
            )
        )
    return GraphSnapshot(
        asOf="2026-10-08",
        domains=domains,
        skillCount=len(profile.skills),
        mastery=GraphMastery(score=profile.mastery.score, role=profile.person.currentRole),
        sources=[s.label for s in profile.sources if s.ok],
    )


@cache
def facts_for(name: str) -> tuple[Fact, ...]:
    """The facts of a fixture: the scripted extraction, run through the real verification."""
    fx = load_fixture(name)
    return tuple(verify_facts(scripted_facts(fx.ctx), fx.ctx).facts)


# ───────────────────────── the scripted extraction ─────────────────────────

SECTIONS = {
    "SUMMARY",
    "OBJECTIVE",
    "EXPERIENCE",
    "PROJECTS",
    "EDUCATION",
    "ACHIEVEMENTS",
    "ACTIVITIES",
    "SKILLS",
    "OPEN SOURCE",
    "TALKS",
    "WRITING SAMPLES",
    "CERTIFICATION",
}
MONTHS = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1
    )
}
TECH = (
    "React Native",
    "React",
    "TypeScript",
    "JavaScript",
    "Node.js",
    "Next.js",
    "Tailwind CSS",
    "Python",
    "FastAPI",
    "PostgreSQL",
    "Postgres",
    "SQLite",
    "GraphQL",
    "REST",
    "Express",
    "Storybook",
    "Figma",
    "Vite",
    "Vitest",
    "Jest",
    "GitHub Actions",
    "Vercel",
    "Recharts",
    "Go",
    "Rust",
    "Java",
    "Kafka",
    "Redis",
    "Kubernetes",
    "Terraform",
    "AWS",
    "Docker",
    "Arduino",
    "ESP8266",
    "HTML",
    "CSS",
    "Tableau",
    "SQL",
    "BigQuery",
    "pandas",
    "Firebase",
    "Flutter",
    "Streamlit",
    "Elasticsearch",
    "gRPC",
    "LLVM",
    "MLIR",
    "OCaml",
    "Stripe",
    "Electron",
    "WebSockets",
    "Style Dictionary",
)
HOST_LABELS = {
    "github.com": "GitHub",
    "linkedin.com": "LinkedIn",
    "medium.com": "Medium",
    "substack.com": "Substack",
    "tableau.com": "Tableau Public",
    "lovable.app": "Lovable app",
}
URL = re.compile(r"https?://[^\s)]+|(?:[\w-]+\.)+[a-z]{2,}/[^\s)]+")
HEADER = re.compile(
    r"^(?P<org>.+?) — (?P<title>.+?)\s{2,}(?:(?P<loc>[^·]+?) · )?"
    r"(?P<start>(?:[A-Z][a-z]{2} )?\d{4}) – (?P<end>(?:[A-Z][a-z]{2} )?\d{4}|Present)\s*$"
)
EDU = re.compile(r"^(?P<left>.+?)\s{2,}(?P<start>\d{4})(?: – (?P<end>\d{4})(?: \(expected\))?)?\s*$")


def _iso(text: str | None) -> str | None:
    if not text:
        return None
    m = re.fullmatch(r"([A-Z][a-z]{2}) (\d{4})", text.strip())
    if m:
        return f"{m.group(2)}-{MONTHS[m.group(1)]:02d}"
    return text.strip()


def _squash(text: str) -> str:
    return " ".join(text.split())


def _tags(text: str) -> list[str]:
    found: list[str] = []
    for name in TECH:
        present = re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text) is not None
        if present and name not in found and not any(name in other for other in found):
            found.append(name)
    return found[:8]


def _split_sections(text: str) -> tuple[list[str], dict[str, list[str]]]:
    head: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.strip() in SECTIONS and line == line.strip():
            current = sections.setdefault(line.strip(), [])
        elif current is None:
            head.append(line)
        else:
            current.append(line)
    return head, sections


def _bullets(lines: list[str]) -> list[list[str]]:
    """Bullet groups of a block: each group is the physical lines of one ``- `` bullet (continuations joined)."""
    groups: list[list[str]] = []
    for line in lines:
        if line.startswith("- "):
            groups.append([line[2:].rstrip()])
        elif groups and line.startswith("  ") and line.strip():
            groups[-1].append(line.strip())
    return groups


def _fact(kind: str, source_id: str, quote: str, title: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": kind,
        "sourceId": source_id,
        "quote": _squash(quote),
        "title": title,
        "highlights": extra.pop("highlights", []),
        "tags": extra.pop("tags", []),
    }
    out.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    return out


def _experiences(sid: str, lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        m = HEADER.match(lines[i])
        if not m:
            i += 1
            continue
        block: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip():
            block.append(lines[i])
            i += 1
        detail = next((ln.strip() for ln in block if not ln.startswith(("-", " "))), None)
        highlights = [_squash(" ".join(g)) for g in _bullets(block)]
        present = m.group("end") == "Present"
        out.append(
            _fact(
                "experience",
                sid,
                lines[i - len(block) - 1],
                m.group("title"),
                org=m.group("org"),
                location=(m.group("loc") or "").strip() or None,
                start=_iso(m.group("start")),
                end=None if present else _iso(m.group("end")),
                current=True if present else None,
                detail=detail,
                highlights=highlights,
                tags=_tags(" ".join(highlights)),
            )
        )
    return out


def _projects(sid: str, lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    header_form = [ln for ln in lines if ln.strip() and not ln.startswith(("-", " ")) and " — " in ln]
    if header_form:
        for idx, line in enumerate(lines):
            if line in header_form:
                body = []
                for follow in lines[idx + 1 :]:
                    if follow in header_form or (follow.strip() and not follow.startswith(("-", " "))):
                        break
                    body.append(follow)
                name, rest = line.split(" — ", 1)
                url = URL.search(rest)
                detail = _squash(URL.sub("", rest))
                highlights = [_squash(" ".join(g)) for g in _bullets(body)]
                out.append(
                    _fact(
                        "project",
                        sid,
                        line,
                        name.strip(),
                        detail=detail,
                        url=url.group() if url else None,
                        highlights=highlights,
                        tags=_tags(rest + " " + " ".join(highlights)),
                    )
                )
        return out
    for group in _bullets(lines):
        first = group[0]
        text = _squash(" ".join(group))
        if " — " not in text:
            continue
        name, rest = text.split(" — ", 1)
        year = re.search(r"\((\d{4})\)\s*$", name)
        name = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip()
        url = URL.search(rest)
        out.append(
            _fact(
                "project",
                sid,
                first,
                name,
                start=year.group(1) if year else None,
                detail=_squash(URL.sub("", rest)),
                url=url.group() if url else None,
                tags=_tags(rest),
            )
        )
    return out


def _education(sid: str, lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        m = EDU.match(line)
        if not m or line.startswith("CGPA"):
            continue
        left = m.group("left")
        title, _, org = left.partition(" — ")
        org, _, place = org.rpartition(", ") if ", " in org and not org.startswith("St.") else (org, "", "")
        if not org:
            org, place = title.partition(" — ")[2], ""
        extra = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        grade = re.match(r"CGPA \d+(?:\.\d+)? / \d+", extra)
        detail = extra.split(". ", 1)[1] if grade and ". " in extra else None
        out.append(
            _fact(
                "education",
                sid,
                line,
                title.strip(),
                org=(org or None) if org != title else None,
                location=place or None,
                start=m.group("start"),
                end=m.group("end"),
                grade=grade.group() if grade else None,
                detail=detail,
            )
        )
    return out


def _quote_of(text: str) -> str | None:
    for sentence in re.split(r"(?<=[.!?])\s+", _squash(text)):
        if re.match(r"I\b|I'm\b|My\b", sentence) and 8 <= len(sentence) <= 160:
            return sentence
    return None


def _from_resume(sid: str, text: str) -> list[dict[str, Any]]:
    head, sections = _split_sections(text)
    facts: list[dict[str, Any]] = []
    for key in ("SUMMARY", "OBJECTIVE"):
        if key in sections:
            quote = _quote_of(" ".join(sections[key]))
            if quote:
                facts.append(_fact("quote", sid, quote, quote))
    facts += _experiences(sid, sections.get("EXPERIENCE", []))
    facts += _projects(sid, sections.get("PROJECTS", []))
    facts += _education(sid, sections.get("EDUCATION", []))
    for key in ("ACHIEVEMENTS", "ACTIVITIES", "TALKS", "OPEN SOURCE"):
        for group in _bullets(sections.get(key, [])):
            full = _squash(" ".join(group))
            if key == "TALKS" or re.fullmatch(r'"[^"]+" — .+', full):
                m = re.fullmatch(r'"(?P<t>[^"]+)" — (?P<o>.+?)(?:, (?P<y>\d{4})| (?P<y2>\d{4}))?', full)
                if m:
                    facts.append(
                        _fact(
                            "achievement",
                            sid,
                            group[0],
                            m.group("t"),
                            org=m.group("o"),
                            start=m.group("y") or m.group("y2"),
                        )
                    )
                    continue
            year = re.findall(r"\b(\d{4})\b", full)
            facts.append(_fact("achievement", sid, group[0], full, start=year[-1] if year else None))
    for line in sections.get("CERTIFICATION", []):
        if line.strip():
            first = line.strip().split(". ")[0] + "."
            year = re.findall(r"\b(\d{4})\b", first)
            facts.append(_fact("achievement", sid, first, first, start=year[-1] if year else None))
    for group in _bullets(sections.get("WRITING SAMPLES", [])):
        m = re.fullmatch(r'"(?P<t>.+)" — (?P<o>.+?), (?P<y>\d{4})', _squash(" ".join(group)))
        if m:
            facts.append(_fact("clip", sid, group[0], m.group("t"), org=m.group("o"), start=m.group("y")))
    facts += _contacts(sid, head)
    return facts


def _contacts(sid: str, head: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in head[1:]:
        for token in (t.strip() for t in line.split(" · ")):
            if re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", token):
                out.append(_fact("contact", sid, token, "Email", detail=token))
            elif re.fullmatch(r"\+\d[\d ]{8,}", token):
                out.append(_fact("contact", sid, token, "Phone", detail=token))
            elif re.fullmatch(r"(?:[\w-]+\.)+[a-z]{2,}(?:/\S+)?", token):
                host = ".".join(token.split("/")[0].split(".")[-2:])
                out.append(_fact("link", sid, token, HOST_LABELS.get(host, "Website"), url=token))
    return out


def _from_other(sid: str, kind: str, url: str | None, label: str, text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    if url:
        host = ".".join(re.sub(r"^https?://", "", url).split("/")[0].split(".")[-2:])
        first_line = next(ln.strip() for ln in text.splitlines() if ln.strip())
        facts.append(_fact("link", sid, first_line, HOST_LABELS.get(host, label), url=url))
    repos = re.split(r"^### \d+\. ", text, flags=re.MULTILINE)[1:] if kind == "github" else []
    for block in repos[:4]:
        name = block.splitlines()[0].strip()
        desc = re.search(r"^- Description: (.+)$", block, flags=re.MULTILINE)
        lang = re.search(r"^- Language: ([^,\n]+)", block, flags=re.MULTILINE)
        if desc and url:
            facts.append(
                _fact(
                    "project",
                    sid,
                    f"Description: {desc.group(1)}",
                    name,
                    detail=desc.group(1),
                    url=f"{url.rstrip('/')}/{name}",
                    tags=[lang.group(1)] if lang else [],
                )
            )
    for m in re.finditer(r'^(?:\d+\.|-) "(?P<t>[^"]+)" — (?P<rest>.+)$', text, flags=re.MULTILINE):
        rest = m.group("rest")
        pub = re.match(r"(?P<o>[A-Za-z' ]+?), (?P<y>\d{4})$", rest)
        date = re.match(r"(?P<d>\d{4}-\d{2})-\d{2}", rest)
        line = m.group(0)
        if pub:
            facts.append(_fact("clip", sid, line, m.group("t"), org=pub.group("o"), start=pub.group("y")))
        elif date:
            facts.append(_fact("clip", sid, line, m.group("t"), org=label, start=date.group("d")))
    for m in re.finditer(r'"(I [^"]{8,140}[.!])"', text):
        facts.append(_fact("quote", sid, m.group(1), m.group(1)))
    for m in re.finditer(r"^> Hi, I'm \w+\. (I [^\n]+)$", text, flags=re.MULTILINE):
        facts.append(_fact("quote", sid, m.group(1), m.group(1)))
    return facts


def _line_containing(lines: list[str], text: str, start: int = 0) -> int | None:
    """The 1-based number of the first line (from index ``start`` on, then from the top) that holds the start of ``text``."""
    squashed = _squash(text)
    for width in (60, 30, 14):
        key = squashed[:width]
        if not key:
            return None
        for i in [*range(start, len(lines)), *range(start)]:
            if key in _squash(lines[i]):
                return i + 1
    # a sentence that starts at the end of one line and wraps onto the next: find it in the joined text
    joined, starts = "", []
    for line in lines:
        starts.append(len(joined))
        joined += _squash(line) + " "
    at = joined.find(squashed[:30])
    return max(i for i, offset in enumerate(starts) if offset <= at) + 1 if at >= 0 else None


def _bullet_starting(lines: list[str], text: str, start: int) -> int | None:
    """The 1-based number of the bullet line (``- ...``) whose text is the start of the highlight ``text``."""
    for i in [*range(start, len(lines)), *range(start)]:
        if not lines[i].startswith("- "):
            continue
        first = _squash(lines[i][2:])
        if len(first) >= 12 and text.startswith(first):
            return i + 1
    return None


def _to_lines(fact: dict[str, Any], text: str) -> dict[str, Any]:
    """A scripted fact with its quote, highlights and detail swapped for the numbers of the lines they come from."""
    lines = list(split_lines(text))
    out = {k: v for k, v in fact.items() if k not in ("quote", "highlights", "detail")}
    line = _line_containing(lines, fact["quote"])
    out["line"] = line if line is not None else 0
    start = (line or 1) - 1
    numbers = [n for h in fact.get("highlights", []) if (n := _bullet_starting(lines, h, start))]
    if numbers:
        out["highlightLines"] = numbers
    if fact.get("detail") and fact["kind"] not in ("contact", "link"):
        detail_line = _line_containing(lines, fact["detail"], start)
        if detail_line is not None:
            out["detailLine"] = detail_line
    return out


def scripted_facts(ctx: StoredContext) -> dict[str, Any]:
    """What a careful extraction model would return for ``ctx`` (resume first, then the other sources)."""
    facts: list[dict[str, Any]] = []
    ordered = sorted(ctx.sources, key=lambda s: s.kind != "resume")
    for src in ordered:
        if not src.content.strip():
            continue
        if src.kind == "resume":
            built = _from_resume(src.id, src.content)
        else:
            built = _from_other(src.id, src.kind, src.url, src.label, src.content)
        facts += [_to_lines(f, src.content) for f in built]
    return {"facts": facts}
