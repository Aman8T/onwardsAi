"""GitHub ingestion through the REST API: profile, repositories, READMEs, file trees and activity.

One profile costs about a dozen API calls (the unauthenticated limit is 60 per hour), so every
ingest has a hard call budget. Set GITHUB_TOKEN (no scopes needed) to raise the limit.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from .classify import IngestLog, SourceDoc, normalize_url, plural
from .terms import count_terms, find_terms
from .web import MAX_BODY_BYTES, REQUEST_TIMEOUT, read_capped

API = "https://api.github.com"
MAX_CALLS = 14
"""Hard budget of API calls per profile."""
README_CHARS = 2500
README_RAW_CHARS = 100_000
"""Only this much of a raw README is cleaned (the result is cut to README_CHARS anyway)."""
DETAILED_REPOS = 8
README_REPOS = 5
TREE_REPOS = 4
RATE_LIMIT_MSG = "GitHub rate limit reached — set GITHUB_TOKEN to raise it"

_OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
_RESERVED_OWNERS = frozenset(
    ("orgs", "topics", "sponsors", "marketplace", "features", "about", "pricing", "settings", "login", "explore")
)
_RATE_LIMIT_BODY = re.compile(r"rate limit|abuse", re.IGNORECASE)

# --------------------------------------------------------------------------- HTTP client with a call budget


@dataclass
class _GhResult:
    status: int
    ok: bool
    json: Any = None
    text: str | None = None
    rate_limited: bool = False
    failed: bool = False
    """The request never produced a response (offline, DNS, timeout), was over budget or returned bad JSON."""


class _GitHubClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.calls = 0
        self.rate_limited = False
        self.token = os.environ.get("GITHUB_TOKEN", "").strip() or None

    async def get(self, path: str, accept: str = "application/vnd.github+json") -> _GhResult:
        if self.calls >= MAX_CALLS:
            return _GhResult(0, False, failed=True)
        self.calls += 1
        headers = {"User-Agent": "tayyari-poc", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self.http.stream("GET", API + path, headers=headers) as res:
                    raw, _ = await read_capped(res, MAX_BODY_BYTES)
                    status, remaining = res.status_code, res.headers.get("x-ratelimit-remaining")
        except (httpx.HTTPError, TimeoutError):
            return _GhResult(0, False, failed=True)

        body = raw.decode("utf-8", errors="replace")
        limited = status == 429 or (status == 403 and (remaining == "0" or bool(_RATE_LIMIT_BODY.search(body))))
        if limited:
            self.rate_limited = True
        result = _GhResult(status, 200 <= status < 300, rate_limited=limited)
        if result.ok:
            if "raw" in accept:
                result.text = body
            else:
                try:
                    result.json = json.loads(body)
                except ValueError:
                    result.ok, result.failed = False, True
        return result


# --------------------------------------------------------------------------- small helpers


def _fmt_int(n: Any) -> str:
    return f"{int(n or 0):,}"


def _js_round(x: float) -> int:
    """JavaScript's Math.round (halves round up), unlike Python's banker's rounding."""
    return math.floor(x + 0.5)


def _parse_ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _date_only(iso: str | None) -> str:
    return iso[:10] if iso else "unknown"


def _ago(iso: str | None, now: float) -> str:
    t = _parse_ts(iso)
    if t is None:
        return "unknown"
    days = max(0, math.floor((now - t) / 86_400))
    if days < 1:
        return "today"
    if days < 14:
        return f"{plural(days, 'day')} ago"
    if days < 60:
        return f"{plural(_js_round(days / 7), 'week')} ago"
    if days < 730:
        return f"{plural(_js_round(days / 30.4), 'month')} ago"
    return f"{plural(_js_round(days / 365.25), 'year')} ago"


def _months_since(iso: str | None, now: float) -> float:
    t = _parse_ts(iso)
    return 120.0 if t is None else max(0.0, (now - t) / (86_400 * 30.4))


def _top_counts(values: Any, limit: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def _one_line(s: str) -> str:
    return " ".join(s.split())


# bounded quantifiers everywhere: a README is untrusted input and Python's `re` can backtrack
_REF_DEFINITION = re.compile(r"^[ \t]*\[[^\]\n]{1,200}\]:[ \t]*\S.*$", re.MULTILINE)
_LINKED_BADGE = re.compile(
    r"\[!\[[^\]]{0,200}\](?:\([^)]{0,500}\)|\[[^\]]{0,200}\])\](?:\([^)]{0,500}\)|\[[^\]]{0,200}\])"
)
_IMAGE = re.compile(r"!\[[^\]]{0,200}\](?:\([^)]{0,500}\)|\[[^\]]{0,200}\])")
_IMG_TAG = re.compile(r"<img\b[^>]{0,1000}>", re.IGNORECASE)
_HTML_TAG = re.compile(
    r"</?(?:p|div|br|a|b|i|em|strong|picture|source|h[1-6]|center|span|details|summary|sup|sub|table|tr|td|th|thead"
    r"|tbody|kbd)\b[^>]{0,500}>",
    re.IGNORECASE,
)
_LINK = re.compile(r"\[([^\]]{1,300})\](?:\([^)]{0,1000}\)|\[[^\]]{0,300}\])")
_CODE_FENCE = re.compile(r"(```[\s\S]*?```)")


def _strip_html_comments(s: str) -> str:
    out: list[str] = []
    i = 0
    while True:
        start = s.find("<!--", i)
        end = s.find("-->", start + 4) if start >= 0 else -1
        if end < 0:  # none left, or an unterminated one (which has no closer after it either)
            out.append(s[i:])
            return "".join(out)
        out.append(s[i:start])
        i = end + 3


def _clean_prose(p: str) -> str:
    p = _strip_html_comments(p)
    p = _REF_DEFINITION.sub("", p)
    p = _LINKED_BADGE.sub("", p)
    p = _IMAGE.sub("", p)
    p = _IMG_TAG.sub("", p)
    p = _HTML_TAG.sub(" ", p)
    p = _LINK.sub(r"\1", p)
    p = re.sub(r"[ \t]+", " ", p)
    return re.sub(r" ?\n ?", "\n", p)


def _clean_readme(raw: str) -> str:
    """Strip badges, images and markup noise; fenced code blocks stay verbatim."""
    parts = _CODE_FENCE.split(raw[:README_RAW_CHARS].replace("\r\n", "\n").replace("\r", "\n"))
    s = "".join(part if i % 2 else _clean_prose(part) for i, part in enumerate(parts))
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if len(s) > README_CHARS:
        s = s[:README_CHARS]
        cut = s.rfind("\n")
        if cut > README_CHARS * 0.6:
            s = s[:cut]
        s += "\n[…truncated]"
    return s


def _quote_block(text: str) -> str:
    """Prefix each line so README headings never collide with our own markdown structure."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.split("\n"))


# --------------------------------------------------------------------------- repository file tree

_TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|e2e|__mocks__)/|\.(test|spec)\.[a-z0-9]+$|(^|/)test_[^/]+\.py$"
    r"|_test\.(go|py|rb|rs)$|Test\.java$",
    re.IGNORECASE,
)
_CI_PATTERNS = (
    ("GitHub Actions", re.compile(r"^\.github/workflows/[^/]+\.ya?ml$", re.IGNORECASE)),
    ("CircleCI", re.compile(r"^\.circleci/", re.IGNORECASE)),
    ("GitLab CI", re.compile(r"(^|/)\.gitlab-ci\.ya?ml$", re.IGNORECASE)),
    ("Travis CI", re.compile(r"(^|/)\.travis\.ya?ml$", re.IGNORECASE)),
    ("Jenkins", re.compile(r"(^|/)Jenkinsfile$", re.IGNORECASE)),
    ("Azure Pipelines", re.compile(r"(^|/)azure-pipelines\.ya?ml$", re.IGNORECASE)),
    ("Bitbucket Pipelines", re.compile(r"(^|/)bitbucket-pipelines\.ya?ml$", re.IGNORECASE)),
)
_MANIFEST_RE = re.compile(
    r"^(package\.json|pyproject\.toml|requirements\.txt|setup\.py|Pipfile|Cargo\.toml|go\.mod|pom\.xml"
    r"|build\.gradle(?:\.kts)?|Gemfile|composer\.json|pubspec\.yaml|deno\.json|Package\.swift)$"
)
_DOCKER_RE = re.compile(r"(^|/)(Dockerfile(\.[\w-]+)?|docker-compose\.ya?ml|compose\.ya?ml)$", re.IGNORECASE)
_DOCS_DIR_RE = re.compile(r"^docs?/|^(mkdocs\.yml|CONTRIBUTING\.md)$", re.IGNORECASE)
_MARKDOWN_RE = re.compile(r"\.mdx?$", re.IGNORECASE)


@dataclass
class _RepoInsights:
    files: int
    tests: bool
    test_files: int
    ci: list[str]
    docker: bool
    manifests: list[str]
    docs: bool
    truncated: bool


def _analyze_tree(tree: dict[str, Any]) -> _RepoInsights:
    blobs = [e.get("path", "") for e in tree.get("tree") or [] if e.get("type") == "blob"]
    test_files = sum(1 for p in blobs if _TEST_RE.search(p))
    manifests = list(dict.fromkeys(p for p in blobs if "/" not in p and _MANIFEST_RE.match(p)))
    return _RepoInsights(
        files=len(blobs),
        tests=test_files > 0,
        test_files=test_files,
        ci=[name for name, rx in _CI_PATTERNS if any(rx.search(p) for p in blobs)],
        docker=any(_DOCKER_RE.search(p) for p in blobs),
        manifests=manifests,
        docs=any(_DOCS_DIR_RE.search(p) for p in blobs) or sum(1 for p in blobs if _MARKDOWN_RE.search(p)) > 3,
        truncated=bool(tree.get("truncated")),
    )


def _parse_target(value: str) -> tuple[str, str | None] | None:
    """(owner, repo or None) from a profile/repo URL or a bare username."""
    trimmed = (value or "").strip()
    if _OWNER_RE.fullmatch(trimmed.removeprefix("@")):
        return trimmed.removeprefix("@"), None
    u = normalize_url(trimmed)
    if u is None or u.hostname.removeprefix("www.") != "github.com":
        return None
    segments = [s for s in u.path.split("/") if s]
    if not segments or not _OWNER_RE.fullmatch(segments[0]) or segments[0].lower() in _RESERVED_OWNERS:
        return None
    repo = segments[1] if len(segments) > 1 else None
    return segments[0], (re.sub(r"\.git$", "", repo, flags=re.IGNORECASE) if repo else None)


def _repo_score(r: dict[str, Any], now: float) -> float:
    recency = max(0.0, 24 - _months_since(r.get("pushed_at"), now))  # 0-24, fresh repos win
    size = min(10.0, math.log2((r.get("size") or 0) + 1))  # 0-10, tiny repos lose
    return (r.get("stargazers_count") or 0) * 3 + recency + (4 if r.get("description") else 0) + size - (
        6 if r.get("archived") else 0
    )


# --------------------------------------------------------------------------- recent activity


@dataclass
class _Activity:
    total: int
    span_days: int
    pushes: int
    commits: int | None
    prs_opened: int
    prs_merged: int
    external_prs: int
    issues_opened: int
    comments: int
    reviews: int
    repos_created: int
    stars: int
    active_repos: list[tuple[str, int]]
    push_languages: list[tuple[str, int]]
    latest: str | None


def _summarize_events(events: list[dict[str, Any]], login: str, language_by_repo: dict[str, str]) -> _Activity:
    owner = login.lower()
    times = [t for t in (_parse_ts(e.get("created_at")) for e in events) if t is not None]
    pushes = [e for e in events if e.get("type") == "PushEvent"]
    # Newer event payloads may no longer carry commit counts, so track whether any did.
    commits, has_commit_info = 0, False
    for p in pushes:
        payload = p.get("payload") or {}
        n = payload.get("size")
        if n is None and isinstance(payload.get("commits"), list):
            n = len(payload["commits"])
        if isinstance(n, int):
            commits += n
            has_commit_info = True
    pr_events = [e for e in events if e.get("type") == "PullRequestEvent"]
    opened = [e for e in pr_events if (e.get("payload") or {}).get("action") == "opened"]

    def repo_name(e: dict[str, Any]) -> str:
        return (e.get("repo") or {}).get("name", "")

    def payload_of(e: dict[str, Any]) -> dict[str, Any]:
        return e.get("payload") or {}

    push_langs = [lang for p in pushes if (lang := language_by_repo.get(repo_name(p).lower()))]
    return _Activity(
        total=len(events),
        span_days=max(1, _js_round((max(times) - min(times)) / 86_400)) if len(times) > 1 else 0,
        pushes=len(pushes),
        commits=commits if has_commit_info else None,
        prs_opened=len(opened),
        prs_merged=sum(
            1
            for e in pr_events
            if payload_of(e).get("action") == "closed" and (payload_of(e).get("pull_request") or {}).get("merged")
        ),
        external_prs=sum(1 for e in opened if repo_name(e).split("/")[0].lower() != owner),
        issues_opened=sum(1 for e in events if e.get("type") == "IssuesEvent" and payload_of(e).get("action") == "opened"),
        comments=sum(1 for e in events if e.get("type") in ("IssueCommentEvent", "CommitCommentEvent")),
        reviews=sum(1 for e in events if e.get("type") == "PullRequestReviewEvent"),
        repos_created=sum(
            1 for e in events if e.get("type") == "CreateEvent" and payload_of(e).get("ref_type") == "repository"
        ),
        stars=sum(1 for e in events if e.get("type") == "WatchEvent"),
        active_repos=_top_counts((repo_name(e) for e in events), 5),
        push_languages=_top_counts(push_langs, 4),
        latest=events[0].get("created_at") if events else None,
    )


# --------------------------------------------------------------------------- the ingester


@dataclass
class _State:
    login: str
    user: dict[str, Any] | None = None
    repos: list[dict[str, Any]] | None = None
    focus: dict[str, Any] | None = None
    profile_readme: str | None = None
    readmes: dict[str, str] = field(default_factory=dict)
    no_readme: set[str] = field(default_factory=set)
    """Repos where GitHub answered 404 or served an empty README (as opposed to a rate-limited fetch)."""
    insights: dict[str, _RepoInsights] = field(default_factory=dict)
    activity: _Activity | None = None
    notes: list[str] = field(default_factory=list)
    ranked: list[dict[str, Any]] = field(default_factory=list)
    """Original repos by rank (the profile-README repo excluded)."""
    detailed: list[dict[str, Any]] = field(default_factory=list)


async def ingest_github(url: str, log: IngestLog) -> SourceDoc:
    """Read a GitHub profile or repository link. Expected problems come back as a failed `SourceDoc`."""
    target = _parse_target(url)
    kind_label = "GitHub repo" if target and target[1] else "GitHub profile"
    page_url = f"https://github.com/{target[0]}{'/' + target[1] if target[1] else ''}" if target else url
    if target is None:
        return SourceDoc.failure(
            "github", "github", "GitHub", kind_label, page_url, "github.com",
            "That does not look like a GitHub profile or repository link.",
        )  # fmt: skip

    owner, repo_name = target
    src = f"github.com/{owner}"
    state = _State(login=owner)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as http:
        client = _GitHubClient(http)
        error = await _fetch(client, state, owner, repo_name, log)
    return _build_source(page_url, kind_label, src, state, client, error)


async def _fetch(client: _GitHubClient, state: _State, owner: str, repo_name: str | None, log: IngestLog) -> str | None:
    """Fill `state` from the API; returns an error message when the profile could not be read."""
    now = time.time()
    login_q = quote(owner, safe="")

    # 1. profile
    res = await client.get(f"/users/{login_q}")
    if res.rate_limited:
        return RATE_LIMIT_MSG
    if res.status == 404:
        return f'GitHub user "{owner}" was not found. Check the link.'
    if not res.ok or not isinstance(res.json, dict):
        if res.failed:
            return "Could not reach GitHub. Check the connection and try again."
        return f"GitHub returned an unexpected response ({res.status})."
    user: dict[str, Any] = res.json
    state.user = user
    state.login = user.get("login") or owner
    login = quote(state.login, safe="")
    log(f"Fetched profile, {plural(user.get('public_repos') or 0, 'public repo')}")

    # 2. repositories
    res = await client.get(f"/users/{login}/repos?per_page=100&sort=pushed")
    if res.rate_limited:
        return RATE_LIMIT_MSG
    if not res.ok or not isinstance(res.json, list):
        state.notes.append("The repository list could not be fetched.")
        if res.failed:
            return "Could not reach GitHub while listing repositories."
        return f"GitHub could not list repositories ({res.status})."
    repos: list[dict[str, Any]] = res.json
    state.repos = repos

    # A /owner/repo link always puts that repo in the detailed set.
    if repo_name:
        want = repo_name.lower()
        state.focus = next((r for r in repos if r.get("name", "").lower() == want), None)
        if state.focus is None:
            one = await client.get(f"/repos/{login}/{quote(repo_name, safe='')}")
            if one.ok and isinstance(one.json, dict):
                state.focus = one.json
            elif one.status == 404:
                state.notes.append(f"The repository {state.login}/{repo_name} was not found; showing the profile instead.")

    # 3. rank
    profile_repo = state.login.lower()
    ranked = sorted(
        (r for r in repos if not r.get("fork") and r.get("name", "").lower() != profile_repo),
        key=lambda r: -_repo_score(r, now),
    )
    detailed: list[dict[str, Any]] = [state.focus] if state.focus else []
    for r in ranked:
        if len(detailed) >= DETAILED_REPOS:
            break
        if all(d.get("name") != r.get("name") for d in detailed):
            detailed.append(r)
    state.ranked, state.detailed = ranked, detailed
    log("Ranked repos by recency and stars")

    # 4. READMEs, file trees, events and the profile README, all in parallel
    async def read_readme(repo: dict[str, Any]) -> None:
        name = repo["name"]
        res = await client.get(f"/repos/{login}/{quote(name, safe='')}/readme", "application/vnd.github.raw")
        if res.ok:
            cleaned = _clean_readme(res.text) if res.text and res.text.strip() else ""
            if cleaned:
                state.readmes[name] = cleaned
                log(f"{name}: README read")
            else:
                state.no_readme.add(name)  # empty or badge-only README
        elif res.status == 404:
            state.no_readme.add(name)
            log(f"{name}: no README")
        # Rate-limited or failed: leave no record, so the output says "not fetched" rather than "none found".

    async def inspect_trees() -> None:
        async def one(repo: dict[str, Any]) -> bool:
            branch = quote(repo.get("default_branch") or "main", safe="")
            res = await client.get(f"/repos/{login}/{quote(repo['name'], safe='')}/git/trees/{branch}?recursive=1")
            if res.ok and isinstance(res.json, dict):
                state.insights[repo["name"]] = _analyze_tree(res.json)
                return True
            return False

        checked = sum(await asyncio.gather(*(one(r) for r in detailed[:TREE_REPOS])))
        if checked > 0:
            log(f"Checked {plural(checked, 'repo')} for tests and CI")

    async def read_events() -> None:
        res = await client.get(f"/users/{login}/events/public?per_page=100")
        if res.ok and isinstance(res.json, list):
            by_repo = {r["full_name"].lower(): r["language"] for r in repos if r.get("language") and r.get("full_name")}
            state.activity = _summarize_events(res.json, state.login, by_repo)
            log(f"Read {plural(state.activity.total, 'recent public event')}")

    async def read_profile_readme(repo: dict[str, Any]) -> None:
        res = await client.get(f"/repos/{login}/{quote(repo['name'], safe='')}/readme", "application/vnd.github.raw")
        if res.ok and res.text and res.text.strip():
            state.profile_readme = _clean_readme(res.text)
            log("Profile README read")

    profile_readme_repo = next((r for r in repos if r.get("name", "").lower() == profile_repo and not r.get("fork")), None)
    tasks = [read_readme(r) for r in detailed[:README_REPOS]] + [inspect_trees(), read_events()]
    if profile_readme_repo:
        tasks.append(read_profile_readme(profile_readme_repo))
    await asyncio.gather(*tasks)

    if client.rate_limited:
        state.notes.append("GitHub rate limit was reached part-way, so some READMEs, file checks or activity are missing.")
        log("Rate limit reached, continuing with what was fetched")
    return None


# --------------------------------------------------------------------------- output


def _build_source(
    page_url: str, kind_label: str, src: str, state: _State, client: _GitHubClient, error: str | None
) -> SourceDoc:
    now = time.time()
    user, repos, detailed, ranked = state.user, state.repos, state.detailed, state.ranked

    originals = [r for r in repos or [] if not r.get("fork")]
    forks = [r for r in repos or [] if r.get("fork")]
    stars = sum(r.get("stargazers_count") or 0 for r in originals)
    lang_counts = _top_counts((r["language"] for r in originals if r.get("language")), 8)
    languages = [lang for lang, _ in lang_counts]
    insights = list(state.insights.values())
    with_tests = sum(1 for i in insights if i.tests)
    with_ci = sum(1 for i in insights if i.ci)
    with_docker = sum(1 for i in insights if i.docker)
    inspected = len(insights)

    out: list[str] = []
    display = f"{user['name']} (@{state.login})" if user and user.get("name") else f"@{state.login}"
    out.append(f"# GitHub: {display}")

    if user:
        out += ["", "## Profile"]
        if user.get("name"):
            out.append(f"- Name: {user['name']}")
        out.append(f"- Account type: {user.get('type') or 'User'}")
        if user.get("bio"):
            out.append(f"- Bio: {_one_line(user['bio'])}")
        if user.get("company"):
            out.append(f"- Company: {user['company']}")
        if user.get("location"):
            out.append(f"- Location: {user['location']}")
        if user.get("blog"):
            out.append(f"- Website: {user['blog']}")
        if user.get("twitter_username"):
            out.append(f"- X/Twitter: @{user['twitter_username']}")
        out.append(f"- Followers: {_fmt_int(user.get('followers'))}, following: {_fmt_int(user.get('following'))}")
        created = _parse_ts(user.get("created_at"))
        if created is not None:
            years = max(0.0, (now - created) / (86_400 * 365.25))
            span = "less than a year" if years < 1 else f"about {_js_round(years)} years"
            out.append(f"- On GitHub since {_date_only(user.get('created_at'))} ({span})")

    if state.focus:
        out += ["", f"The user linked one repository specifically: {state.focus.get('full_name')}. It is listed first below."]

    if repos is not None:
        out += ["", "## Totals"]
        total = (user or {}).get("public_repos")
        if total is None:
            total = len(repos)
        out.append(
            f"- Public repositories: {_fmt_int(total)}"
            + (f" (analysing the {len(repos)} most recently pushed)" if total > len(repos) else "")
        )
        out.append(f"- Original repos: {len(originals)}, forks: {len(forks)}")
        out.append(f"- Stars across original repos: {_fmt_int(stars)}")
        if lang_counts:
            out.append(f"- Languages by number of original repos: {', '.join(f'{lang} ({n})' for lang, n in lang_counts)}")
        with_desc = sum(1 for r in originals if r.get("description"))
        with_topics = sum(1 for r in originals if r.get("topics"))
        with_home = sum(1 for r in originals if (r.get("homepage") or "").strip())
        out.append(
            f"- Repos with a description: {with_desc}/{len(originals)}, with topics: {with_topics}, "
            f"with a live homepage link: {with_home}"
        )
        all_topics = _top_counts((t for r in originals for t in r.get("topics") or []), 12)
        if all_topics:
            out.append(f"- Common topics: {', '.join(f'{t} ({n})' if n > 1 else t for t, n in all_topics)}")
        if inspected > 0:
            out.append(
                f"- Engineering practice (checked {plural(inspected, 'top repo')}): "
                f"tests in {with_tests}, CI in {with_ci}, Docker in {with_docker}"
            )

    if state.profile_readme:
        out += ["", "## Profile README (the repo named after the user)", _quote_block(state.profile_readme)]

    if detailed:
        out += ["", f"## Top repositories ({len(detailed)}, ranked by stars, recency and substance)"]
        for i, r in enumerate(detailed):
            name = r.get("name", "")
            out += ["", f"### {i + 1}. {name}{' (fork)' if r.get('fork') else ''}{' (archived)' if r.get('archived') else ''}"]
            out.append(f"- Description: {_one_line(r['description']) if r.get('description') else '(none)'}")
            spdx = (r.get("license") or {}).get("spdx_id")
            out.append(
                f"- Language: {r.get('language') or 'n/a'}, stars: {_fmt_int(r.get('stargazers_count'))}, "
                f"forks: {_fmt_int(r.get('forks_count'))}" + (f", license: {spdx}" if spdx and spdx != "NOASSERTION" else "")
            )
            if r.get("topics"):
                out.append(f"- Topics: {', '.join(r['topics'])}")
            out.append(
                f"- Last push: {_date_only(r.get('pushed_at'))} ({_ago(r.get('pushed_at'), now)}), "
                f"created {_date_only(r.get('created_at'))}, size {_fmt_int(r.get('size'))} KB"
            )
            if (r.get("homepage") or "").strip():
                out.append(f"- Homepage: {r['homepage'].strip()}")
            ins = state.insights.get(name)
            if ins:
                tests = f"yes ({plural(ins.test_files, 'test file')})" if ins.tests else "no"
                ci = f"yes ({' + '.join(ins.ci)})" if ins.ci else "no"
                out.append(
                    f"- Detected: tests {tests}, CI {ci}, Docker {'yes' if ins.docker else 'no'}, "
                    f"docs {'yes' if ins.docs else 'no'}"
                    + (f", manifests: {', '.join(ins.manifests)}" if ins.manifests else "")
                    + (" (file tree truncated)" if ins.truncated else "")
                )
            if name in state.readmes:
                out += ["- README excerpt:", _quote_block(state.readmes[name])]
            elif name in state.no_readme:
                out.append("- README: none found")
            elif i < README_REPOS:
                out.append("- README: not fetched (GitHub rate limit or network error); do not treat it as missing")

    shown = {r.get("name") for r in detailed}
    others = [r for r in ranked if r.get("name") not in shown][:14]
    if others:
        out += ["", "## Other original repositories"]
        for r in others:
            lang = f" [{r['language']}]" if r.get("language") else ""
            star_count = f", {r['stargazers_count']} stars" if r.get("stargazers_count") else ""
            desc = f": {_one_line(r['description'])[:160]}" if r.get("description") else ""
            out.append(f"- {r.get('name')}{lang}{star_count}, pushed {_date_only(r.get('pushed_at'))}{desc}")
        remaining = len(ranked) - len(shown) - len(others)
        if remaining > 0:
            out.append(f"- …and {remaining} more")

    if forks:
        out += ["", f"## Forks ({len(forks)})", ", ".join(f.get("name", "") for f in forks[:10]) + (", …" if len(forks) > 10 else "")]

    a = state.activity
    if a and a.total > 0:
        out += ["", "## Recent public activity"]
        out.append(
            f"- {plural(a.total, 'public event')}"
            + (f" over about {plural(a.span_days, 'day')}" if a.span_days else "")
            + (f", most recent {_date_only(a.latest)} ({_ago(a.latest, now)})" if a.latest else "")
        )
        commit_info = f" ({plural(a.commits, 'commit')})" if a.commits is not None else ""
        parts = [
            f"{plural(a.pushes, 'push', 'pushes')}{commit_info}",
            f"{plural(a.prs_opened, 'pull request')} opened"
            + (f" ({a.external_prs} to other people's repos)" if a.external_prs else ""),
            f"{plural(a.prs_merged, 'PR')} merged" if a.prs_merged else "",
            f"{plural(a.issues_opened, 'issue')} opened" if a.issues_opened else "",
            plural(a.reviews, "PR review") if a.reviews else "",
            plural(a.comments, "comment") if a.comments else "",
            plural(a.repos_created, "new repo") if a.repos_created else "",
        ]
        out.append(f"- {', '.join(p for p in parts if p)}")
        if a.active_repos:
            out.append(f"- Most active repos: {', '.join(f'{n} ({c})' for n, c in a.active_repos)}")
        if a.push_languages:
            out.append(f"- Languages of recent pushes: {', '.join(f'{lang} ({c})' for lang, c in a.push_languages)}")
    elif repos is not None and a is None:
        out += ["", "## Recent public activity", "- Not available."]

    # Technologies named across descriptions, topics and READMEs.
    term_text = "\n".join(
        [part for r in originals for part in (r.get("description") or "", *(r.get("topics") or []))]
        + list(state.readmes.values())
        + [state.profile_readme or "", (user or {}).get("bio") or ""]
    )
    mentioned = count_terms(term_text)[:14]
    if mentioned:
        out += ["", "## Technologies mentioned across repos", ", ".join(m.term for m in mentioned)]

    if state.notes or error:
        out += ["", "## Notes on this data"]
        out += [f"- {n}" for n in state.notes]
        if error:
            out.append(f"- {error}")

    # Nothing was fetched (user not found, rate limited on the first call): no content, just the error.
    content = "\n".join(out)[:40_000] if user else ""

    quick: list[str] = [f"{lang} · {plural(n, 'repo')}" for lang, n in lang_counts[:2]]
    lang_set = {lang.lower() for lang in languages}
    for term in find_terms(term_text, 12):
        if len(quick) >= 5:
            break
        if term.lower() not in lang_set and term not in ("Git", "GitHub"):
            quick.append(term)
    if with_ci > 0:
        quick.append(f"CI in {plural(with_ci, 'repo')}")
    if with_tests > 0:
        quick.append(f"Tests in {plural(with_tests, 'repo')}")
    if stars > 0 and len(quick) < 8:
        quick.append(f"{_fmt_int(stars)} stars")

    meta: dict[str, Any] = {
        "username": state.login,
        "repos": user["public_repos"] if user and user.get("public_repos") is not None else len(repos or []),
        "originals": len(originals),
        "forks": len(forks),
        "stars": stars,
        "languages": languages,
        "topRepos": [r.get("name") for r in detailed],
        "withTests": with_tests,
        "withCI": with_ci,
        "inspected": inspected,
        "followers": (user or {}).get("followers") or 0,
        "since": (user or {}).get("created_at"),
        "apiCalls": client.calls,
        "authenticated": client.token is not None,
    }
    if state.notes:
        meta["partial"] = True

    return SourceDoc(
        id="github",
        kind="github",
        label="GitHub",
        url=page_url,
        src=src,
        kind_label=kind_label,
        text=content,
        notes=list(state.notes),
        meta=meta,
        quick_signals=quick[:8],
        error=error,
    )
