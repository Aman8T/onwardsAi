"""The two small alias tables of the match: skill terms that are the same thing written differently, and the
domain words a posting and a skill graph use for the same area.

Both are deliberately short and controlled. A term alias is only ever an abbreviation or a spelling of the SAME
thing (``JS`` / ``JavaScript``, ``Postgres`` / ``PostgreSQL``); a conceptual near-match (``design system`` and
``component library``) is not an alias: it is a question for the person (`confirm_term`).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

TERM_GROUPS: Final[tuple[tuple[str, ...], ...]] = (
    ("javascript", "js", "ecmascript"),
    ("typescript", "ts"),
    ("node.js", "nodejs", "node js"),
    ("react", "react.js", "reactjs"),
    ("next.js", "nextjs", "next js"),
    ("vue", "vue.js", "vuejs"),
    ("angular", "angularjs"),
    ("postgresql", "postgres", "psql"),
    ("mongodb", "mongo"),
    ("kubernetes", "k8s"),
    ("ci/cd", "ci cd", "cicd"),
    ("a/b testing", "ab testing", "a b testing", "split testing", "a/b tests", "a/b test"),
    ("search engine optimization", "search engine optimisation", "seo"),
    ("search engine marketing", "sem"),
    ("pay per click", "pay-per-click", "ppc"),
    ("customer relationship management", "crm"),
    ("key performance indicators", "key performance indicator", "kpis", "kpi"),
    ("return on ad spend", "roas"),
    ("customer acquisition cost", "cac"),
    ("customer lifetime value", "ltv", "clv"),
    ("machine learning", "ml"),
    ("artificial intelligence", "ai"),
    ("user experience", "ux"),
    ("user interface", "ui"),
    ("rest", "rest api", "rest apis", "restful"),
    ("tailwind css", "tailwind", "tailwindcss"),
    ("golang", "go language"),
    ("amazon web services", "aws"),
    ("google cloud platform", "gcp"),
    ("google analytics", "ga4"),
    ("microsoft excel", "excel", "ms excel"),
    ("microsoft powerpoint", "powerpoint", "ms powerpoint"),
    ("business intelligence", "bi"),
    ("email marketing", "email campaigns"),
    ("social media marketing", "smm"),
    ("project management", "program management"),
)
"""Spellings of one thing, as folded text. A term matches any member of its group."""

DOMAIN_GROUPS: Final[tuple[tuple[str, ...], ...]] = (
    ("frontend", "front end", "front-end", "ui", "web", "client"),
    ("backend", "back end", "back-end", "api", "apis", "server", "services"),
    ("design", "ux", "product design", "design systems", "visual"),
    ("data", "analytics", "analysis", "insights", "bi", "reporting", "measurement", "experimentation"),
    ("ai", "ml", "machine learning", "llm", "genai"),
    ("marketing", "growth", "acquisition", "demand generation", "performance", "campaigns", "paid"),
    ("lifecycle", "crm", "retention", "email", "engagement", "loyalty"),
    ("content", "brand", "copywriting", "social", "storytelling", "editorial"),
    (
        "leadership",
        "management",
        "people",
        "team",
        "collaboration",
        "stakeholder",
        "stakeholders",
        "communication",
    ),
    ("product", "product management", "roadmap", "strategy"),
    ("practice", "testing", "devops", "quality", "reliability", "delivery"),
    ("sales", "revenue", "commercial", "partnerships"),
    ("finance", "accounting", "budgeting", "budget"),
    ("operations", "ops", "logistics", "supply chain", "process"),
    ("research", "user research", "insight"),
)
"""Words that name one area, as folded text: two domain names that share a group are the same area."""

_NONWORD: Final = re.compile(r"[^\w.+#/&-]+")


def _fold(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(_NONWORD.sub(" ", plain.casefold()).split())


_TERM_INDEX: Final[dict[str, frozenset[str]]] = {
    _fold(form): frozenset(_fold(f) for f in group) for group in TERM_GROUPS for form in group
}


def term_variants(term: str) -> frozenset[str]:
    """``term`` and every spelling in its alias group, folded (just the term itself when it has none)."""
    key = _fold(term)
    return _TERM_INDEX.get(key, frozenset({key})) | {key}


def same_term(a: str, b: str) -> bool:
    """Are ``a`` and ``b`` the same term, spelled possibly differently?"""
    return _fold(b) in term_variants(a)


_DOMAIN_INDEX: Final[dict[str, int]] = {
    _fold(word): i for i, group in enumerate(DOMAIN_GROUPS) for word in group
}
_STOP: Final = frozenset(["and", "of", "the", "for", "in", "a", "an", "to", "with", "&"])


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> list[str]:
    return [_stem(w) for w in _fold(text).replace("&", " ").split() if w not in _STOP]


def _groups(text: str) -> set[int]:
    folded = _fold(text)
    found = {i for word, i in _DOMAIN_INDEX.items() if " " in word and word in folded}
    found.update(_DOMAIN_INDEX[w] for w in folded.replace("&", " ").split() if w in _DOMAIN_INDEX)
    found.update(_DOMAIN_INDEX[_stem(w)] for w in folded.split() if _stem(w) in _DOMAIN_INDEX)
    return found


def domain_score(hint: str, domain_name: str) -> int:
    """How well a posting's domain hint names a skill-graph domain: 0 (no relation) .. 4 (the same words).

    4: the same words after folding and plural stripping; 3: one contains the other; 2: they share an alias
    group (``front end`` and ``UI``); 1: they share a significant word. Only 2 and above counts as a match.
    """
    a, b = _words(hint), _words(domain_name)
    if not a or not b:
        return 0
    if a == b:
        return 4
    if set(a) <= set(b) or set(b) <= set(a):
        return 3
    if _groups(hint) & _groups(domain_name):
        return 2
    return 1 if set(a) & set(b) else 0


__all__ = ["DOMAIN_GROUPS", "TERM_GROUPS", "domain_score", "same_term", "term_variants"]
