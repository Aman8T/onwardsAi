"""Which repository to start a move from: ranked in code from the stored GitHub digest, nothing fetched.

The digest is the text `ingest.github` wrote into the GitHub source of the profile's `StoredContext` (the sample's is
`tayyari/sample/github.md`). Its "Top repositories" section lists each repository as a ``### N. name`` block with
``- Description:``, ``- Language:``, ``- Topics:``, ``- Detected: tests yes/no ..., CI yes/no ...`` and a README excerpt
(or ``- README: none found``). `parse_digest` reads those blocks; `rank_repos` scores each repository by the words it
shares with the plan's moves and gap names, adds a small bonus for tests and CI, and suggests at most one, only when it
shares at least one word with the plan. The reasons are short strings made here ("has tests", "names evals").
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from tayyari.contract.prepare import PreparePlan, PrepareRepo, PrepareRepos
from tayyari.contract.stored import StoredContext

_HEADING: Final = re.compile(r"^### \d+\. (?P<name>\S+)(?P<flags>(?: \((?:fork|archived)\))*)\s*$")
_SECTION: Final = re.compile(r"^## ")
_DESCRIPTION: Final = re.compile(r"^- Description: (?P<text>.*)$")
_LANGUAGE: Final = re.compile(r"^- Language: (?P<text>[^,]+)")
_TOPICS: Final = re.compile(r"^- Topics: (?P<text>.*)$")
_DETECTED: Final = re.compile(r"^- Detected: (?P<text>.*)$")
_TESTS: Final = re.compile(r"\btests (yes|no)\b")
_CI: Final = re.compile(r"\bCI (yes|no)\b")
_README_EXCERPT: Final = re.compile(r"^- README excerpt:")
_README_ABSENT: Final = re.compile(r"^- README: (none found|default .* template)")
_TOKEN: Final = re.compile(r"[a-z0-9][a-z0-9+#.]*", re.IGNORECASE)
_OWNER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")

MAX_REPOS: Final = 12
"""Repositories the ranking answers with (the digest's top repositories are at most ten)."""
MAX_NAME_REASONS: Final = 2
TESTS_BONUS: Final = 1
CI_BONUS: Final = 1

_STOPWORD_TEXT: Final = (
    "a an and are as at be been but by can do does for from has have how in into is it its of on or that the their "
    "them then there these they this to use used using was we were what when where which while who will with without "
    "you your yours yourself yourselves add adds added build builds built make makes made new one own ship shipped "
    "ships small big medium write writes written set setup up out get gets got run runs ran via per each every all any "
    "some more most less than also just only not no yes done over under about after before again same other such "
    "onto across through app apps project projects repo repos repository repositories code codes file files page "
    "pages work works working tool tools thing things way ways part parts start starts first second third next last "
    "week weeks hour hours day days phase phases task tasks move moves plan plans gap gaps role roles"
)
STOPWORDS: Final = frozenset(_STOPWORD_TEXT.split(" "))
"""Words that say nothing about what a repository is for: dropped before the overlap is counted."""

WEAK_NAME_WORDS: Final = frozenset(
    [
        "web",
        "design",
        "development",
        "engineering",
        "patterns",
        "pattern",
        "systems",
        "system",
        "practice",
        "data",
    ]
)
"""Words of a gap's name too broad to be a reason on their own ("names Web" says nothing); they still count in the
overlap."""


@dataclass(frozen=True, slots=True)
class RepoDigest:
    """One repository as the digest describes it."""

    name: str
    description: str | None
    language: str | None
    topics: tuple[str, ...]
    tests: bool
    ci: bool
    readme: bool
    """False only when the digest says there is no README (none found, or a stock template); unknown counts as present."""
    excerpt: str

    @property
    def text(self) -> str:
        return " ".join(
            part
            for part in (self.name.replace("-", " "), self.description or "", *self.topics, self.excerpt)
            if part
        )


@dataclass(slots=True)
class _Block:
    """One ``### N. name`` block while it is being read."""

    name: str
    description: str | None = None
    language: str | None = None
    topics: list[str] | None = None
    tests: bool = False
    ci: bool = False
    readme: bool = True
    excerpt: list[str] | None = None

    def done(self) -> RepoDigest:
        return RepoDigest(
            name=self.name,
            description=self.description,
            language=self.language,
            topics=tuple(self.topics or ()),
            tests=self.tests,
            ci=self.ci,
            readme=self.readme,
            excerpt=" ".join(self.excerpt or ()),
        )


def parse_digest(text: str) -> list[RepoDigest]:
    """The ``### N. name`` blocks of a GitHub digest, in order (at most `MAX_REPOS`)."""
    repos: list[RepoDigest] = []
    current: _Block | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        heading = _HEADING.match(line)
        if heading is not None or _SECTION.match(line):
            if current is not None:
                repos.append(current.done())
            current = _Block(heading.group("name")) if heading is not None else None
            continue
        if current is None:
            continue
        if (m := _DESCRIPTION.match(line)) is not None:
            found = m.group("text").strip()
            current.description = None if found in ("", "(none)") else found
        elif (m := _LANGUAGE.match(line)) is not None:
            found = m.group("text").strip()
            current.language = None if found in ("", "n/a") else found
        elif (m := _TOPICS.match(line)) is not None:
            current.topics = [t.strip() for t in m.group("text").split(",") if t.strip()]
        elif (m := _DETECTED.match(line)) is not None:
            detected = m.group("text")
            tests, ci = _TESTS.search(detected), _CI.search(detected)
            current.tests = tests is not None and tests.group(1) == "yes"
            current.ci = ci is not None and ci.group(1) == "yes"
        elif _README_EXCERPT.match(line):
            current.readme = True
        elif _README_ABSENT.match(line):
            current.readme = False
        elif line.startswith(">"):
            current.excerpt = [*(current.excerpt or []), line.lstrip("> ").strip()]
    if current is not None:
        repos.append(current.done())
    return repos[:MAX_REPOS]


def tokens(text: str) -> set[str]:
    """The words of ``text`` that can carry meaning: case-folded, at least three characters, not a stopword or a number."""
    out: set[str] = set()
    for match in _TOKEN.finditer(text):
        word = match.group().casefold().rstrip(".")
        if len(word) >= 3 and word not in STOPWORDS and not word.replace(".", "").isdigit():
            out.add(word)
    return out


def plan_terms(plan: PreparePlan) -> set[str]:
    """What the plan talks about: the moves' titles, bodies, have items, phases and tasks, and the gap names."""
    texts: list[str] = [g.name for g in plan.gaps]
    for move in plan.moves:
        texts += [move.title, move.body, *(h.text for h in move.have)]
        for phase in move.phases:
            texts += [phase.title, phase.deliverable, *phase.tasks]
    return tokens(" ".join(texts))


def owner_of(url: str | None) -> str | None:
    """The GitHub account a source url names (``https://github.com/riyakapoor`` -> ``riyakapoor``), else None."""
    if not url:
        return None
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    if (parsed.hostname or "").removeprefix("www.").lower() != "github.com":
        return None
    first = next((s for s in parsed.path.split("/") if s), "")
    return first if _OWNER.fullmatch(first) else None


def github_source(context: StoredContext) -> tuple[str, str | None] | None:
    """``(digest text, source url)`` of the context's GitHub source, or None when the graph had none."""
    for source in context.sources:
        if source.kind == "github" and source.content.strip():
            return source.content, source.url
    return None


def _name_reasons(repo_words: set[str], gap_names: Iterable[str]) -> list[str]:
    """``names X`` for the gap-name words this repository mentions, in the gap's own spelling, at most two."""
    out: list[str] = []
    for name in gap_names:
        for match in _TOKEN.finditer(name):
            word = match.group()
            folded = word.casefold()
            if (
                folded in repo_words
                and folded not in STOPWORDS
                and folded not in WEAK_NAME_WORDS
                and len(word) >= 3
            ):
                reason = f"names {word}"
                if reason not in out:
                    out.append(reason)
                if len(out) >= MAX_NAME_REASONS:
                    return out
    return out


def rank_repos(repos: Sequence[RepoDigest], plan: PreparePlan, *, owner: str | None) -> PrepareRepos:
    """The repositories ranked for this plan: shared words first, then tests and CI; at most one ``suggested``."""
    wanted = plan_terms(plan)
    gap_names = [g.name for g in plan.gaps]
    scored: list[tuple[int, int, int, PrepareRepo]] = []
    for index, repo in enumerate(repos):
        words = tokens(repo.text)
        shared = words & wanted
        emphasis = tokens(" ".join((repo.name.replace("-", " "), *repo.topics))) & wanted
        overlap = len(shared) + len(emphasis)
        score = overlap + (TESTS_BONUS if repo.tests else 0) + (CI_BONUS if repo.ci else 0)
        reasons = _name_reasons(words, gap_names)
        if repo.tests:
            reasons.append("has tests")
        if repo.ci:
            reasons.append("has CI")
        if not repo.readme:
            reasons.append("no README yet")
        item = PrepareRepo(
            name=repo.name,
            url=f"https://github.com/{owner}/{repo.name}" if owner else None,
            language=repo.language,
            description=repo.description,
            tests=repo.tests,
            ci=repo.ci,
            readme=repo.readme,
            reasons=reasons,
            suggested=False,
        )
        scored.append((score, overlap, index, item))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
    ranked = [item for _score, _overlap, _index, item in scored]
    if ranked and scored[0][1] > 0:
        ranked[0] = ranked[0].model_copy(update={"suggested": True})
    return PrepareRepos(github=True, repos=ranked)


def repos_for(plan: PreparePlan, context: StoredContext) -> PrepareRepos:
    """`rank_repos` over the context's GitHub digest; ``github: false`` when the graph was built without one."""
    found = github_source(context)
    if found is None:
        return PrepareRepos(github=False, repos=[])
    text, url = found
    return rank_repos(parse_digest(text), plan, owner=owner_of(url))


__all__ = [
    "CI_BONUS",
    "MAX_NAME_REASONS",
    "MAX_REPOS",
    "STOPWORDS",
    "TESTS_BONUS",
    "WEAK_NAME_WORDS",
    "RepoDigest",
    "github_source",
    "owner_of",
    "parse_digest",
    "plan_terms",
    "rank_repos",
    "repos_for",
    "tokens",
]
