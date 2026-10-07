"""GitHub ingestion against a fake REST API (respx): the 14-call budget, rate limits, tokens and the output.

No test reaches the real API: every request goes to `GitHubApi`, which records it, and an unmocked request
raises. The explicit `github_token` argument is covered next to the old `GITHUB_TOKEN` fallback.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from tayyari.ingest import github as gh
from tayyari.ingest.github import (
    API,
    MAX_CALLS,
    RATE_LIMIT_MSG,
    _clean_readme,
    _GitHubClient,
    _parse_target,
    ingest_github,
    resolve_token,
)

LOGIN = "octocat"
Handler = Callable[[httpx.Request], httpx.Response]


def iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo(name: str, *, stars: int = 0, days: float = 5, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "full_name": f"{LOGIN}/{name}",
        "description": f"The {name} project",
        "language": "Python",
        "stargazers_count": stars,
        "forks_count": 1,
        "pushed_at": iso(days),
        "created_at": iso(days + 200),
        "size": 400,
        "default_branch": "main",
        "topics": ["cli"],
        "fork": False,
        "archived": False,
        **extra,
    }


def user(**extra: Any) -> dict[str, Any]:
    return {
        "login": LOGIN,
        "name": "The Octocat",
        "type": "User",
        "bio": "Builds   developer\ntools",
        "company": "@github",
        "location": "San Francisco",
        "blog": "https://octocat.dev",
        "followers": 1234,
        "following": 9,
        "public_repos": 40,
        "created_at": iso(900),
        **extra,
    }


def event(kind: str, **payload: Any) -> dict[str, Any]:
    return {"type": kind, "created_at": iso(1), "repo": {"name": f"{LOGIN}/repo0"}, "payload": payload}


class GitHubApi:
    """The slice of the GitHub REST API the ingester uses, with a recorded request log."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.requests: list[httpx.Request] = []
        self.user: dict[str, Any] = user()
        self.repos: list[dict[str, Any]] = [repo(f"repo{i}", stars=30 - i) for i in range(30)]
        self.extra_repos: dict[str, dict[str, Any]] = {}
        self.readmes: dict[str, str] = {}
        self.trees: dict[str, list[str]] = {}
        self.events: list[dict[str, Any]] = []
        self.overrides: dict[str, Handler] = {}
        router.route(host="api.github.com").mock(side_effect=self._handle)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path](request)
        if path == f"/users/{LOGIN}":
            return httpx.Response(200, json=self.user)
        if path == f"/users/{LOGIN}/repos":
            return httpx.Response(200, json=self.repos)
        if path == f"/users/{LOGIN}/events/public":
            return httpx.Response(200, json=self.events)
        if m := re.fullmatch(rf"/repos/{LOGIN}/([^/]+)/readme", path):
            text = self.readmes.get(m[1])
            return (
                httpx.Response(200, text=text)
                if text is not None
                else httpx.Response(404, json={"message": "Not Found"})
            )
        if m := re.fullmatch(rf"/repos/{LOGIN}/([^/]+)/git/trees/[^/]+", path):
            files = self.trees.get(m[1])
            if files is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(
                200, json={"tree": [{"path": f, "type": "blob"} for f in files], "truncated": False}
            )
        if m := re.fullmatch(rf"/repos/{LOGIN}/([^/]+)", path):
            found = self.extra_repos.get(m[1])
            return (
                httpx.Response(200, json=found)
                if found
                else httpx.Response(404, json={"message": "Not Found"})
            )
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def api(net: respx.MockRouter) -> GitHubApi:
    return GitHubApi(net)


async def run(url: str = f"https://github.com/{LOGIN}", token: str | None = None) -> tuple[Any, list[str]]:
    lines: list[str] = []
    doc = (
        await ingest_github(url, lines.append, token)
        if token is not None
        else await ingest_github(url, lines.append)
    )
    return doc, lines


# ----------------------------------------------------------------------------- the call budget


def fully_loaded(api: GitHubApi) -> None:
    """Every optional call has something to fetch: READMEs, trees, events and the profile README."""
    api.repos.append(repo(LOGIN, stars=0))  # the profile-README repo
    for r in api.repos[:8]:
        api.readmes[r["name"]] = (
            f"# {r['name']}\n\nThis repository shows {r['name']} in action with Docker and pytest."
        )
        api.trees[r["name"]] = [
            "README.md",
            "tests/test_core.py",
            ".github/workflows/ci.yml",
            "Dockerfile",
            "pyproject.toml",
        ]
    api.readmes[LOGIN] = "Hi, I am the octocat. I write Python and Rust."
    api.events = [event("PushEvent", size=3), event("PullRequestEvent", action="opened"), event("WatchEvent")]


async def test_a_profile_costs_thirteen_calls_at_most(api: GitHubApi) -> None:
    fully_loaded(api)
    doc, _ = await run()
    assert doc.ok is True
    # profile, repo list, 5 READMEs, 4 file trees, events, the profile README
    assert len(api.requests) == 13
    assert doc.meta["apiCalls"] == 13


async def test_a_repository_link_that_is_not_in_the_first_page_costs_the_fourteenth_call(
    api: GitHubApi,
) -> None:
    fully_loaded(api)
    api.extra_repos["old-project"] = repo("old-project", stars=2, days=900)
    doc, _ = await run(f"https://github.com/{LOGIN}/old-project")
    assert doc.ok is True
    assert doc.kind_label == "GitHub repo"
    assert len(api.requests) == MAX_CALLS == 14
    assert f"/repos/{LOGIN}/old-project" in api.paths()
    assert "The user linked one repository specifically: octocat/old-project" in doc.text
    assert doc.meta["topRepos"][0] == "old-project"


async def test_the_client_never_sends_more_than_the_budget(api: GitHubApi) -> None:
    async with httpx.AsyncClient() as http:
        client = _GitHubClient(http)
        results = [await client.get(f"/users/{LOGIN}") for _ in range(MAX_CALLS + 6)]
    assert len(api.requests) == MAX_CALLS
    assert client.calls == MAX_CALLS
    assert [r.ok for r in results] == [True] * MAX_CALLS + [False] * 6
    assert all(r.failed and r.status == 0 for r in results[MAX_CALLS:])


async def test_a_profile_with_no_repositories_makes_three_calls(api: GitHubApi) -> None:
    api.user = user(public_repos=0)
    api.repos = []
    doc, _ = await run()
    assert doc.ok is True
    assert len(api.requests) == 3  # profile, repo list, public events
    assert "## Totals" in doc.text


# ----------------------------------------------------------------------------- rate limits and failures


def limited(
    status: int = 403, *, remaining: str | None = "0", body: str = "API rate limit exceeded"
) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"x-ratelimit-remaining": remaining} if remaining is not None else {}
        return httpx.Response(status, headers=headers, json={"message": body})

    return handler


@pytest.mark.parametrize(
    "handler",
    [
        limited(403),
        limited(429, remaining=None, body="Too many requests"),
        limited(403, remaining="12", body="You have exceeded a secondary rate limit"),
        limited(403, remaining=None, body="You have triggered an abuse detection mechanism"),
    ],
    ids=["403-header", "429", "403-secondary-body", "403-abuse-body"],
)
async def test_a_rate_limited_profile_call_fails_with_the_token_hint(
    handler: Handler, api: GitHubApi
) -> None:
    api.overrides[f"/users/{LOGIN}"] = handler
    doc, lines = await run()
    assert doc.ok is False
    assert doc.error == RATE_LIMIT_MSG
    assert doc.text == ""
    assert len(api.requests) == 1
    assert lines == []


async def test_a_plain_403_is_not_mistaken_for_a_rate_limit(api: GitHubApi) -> None:
    api.overrides[f"/users/{LOGIN}"] = limited(403, remaining="57", body="Repository access blocked")
    doc, _ = await run()
    assert doc.error == "GitHub returned an unexpected response (403)."


async def test_a_rate_limited_repo_list_fails_the_source(api: GitHubApi) -> None:
    api.overrides[f"/users/{LOGIN}/repos"] = limited(429, remaining=None, body="slow down")
    doc, _ = await run()
    assert doc.ok is False
    assert doc.error == RATE_LIMIT_MSG
    assert len(api.requests) == 2


async def test_hitting_the_limit_part_way_keeps_what_was_fetched_and_says_so(api: GitHubApi) -> None:
    fully_loaded(api)
    for r in api.repos[:8]:
        api.overrides[f"/repos/{LOGIN}/{r['name']}/readme"] = limited(403)
    api.overrides[f"/users/{LOGIN}/events/public"] = limited(403)
    doc, lines = await run()
    assert doc.ok is True
    assert doc.meta["partial"] is True
    assert any("rate limit was reached part-way" in n for n in doc.notes)
    assert "Rate limit reached, continuing with what was fetched" in lines
    assert "README: not fetched (GitHub rate limit or network error); do not treat it as missing" in doc.text
    assert "README: none found" not in doc.text  # a rate-limited fetch must not read as "no README"
    assert "## Recent public activity\n- Not available." in doc.text


async def test_a_missing_readme_is_reported_as_none_found(api: GitHubApi) -> None:
    api.user = user(public_repos=1)
    api.repos = [repo("only-repo", stars=3)]
    doc, lines = await run()
    assert "- README: none found" in doc.text
    assert "only-repo: no README" in lines


async def test_an_unknown_user_is_reported_by_name(api: GitHubApi) -> None:
    api.overrides[f"/users/{LOGIN}"] = lambda request: httpx.Response(404, json={"message": "Not Found"})
    doc, _ = await run()
    assert doc.ok is False
    assert doc.error == 'GitHub user "octocat" was not found. Check the link.'
    assert doc.text == ""


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (f"/users/{LOGIN}", "Could not reach GitHub. Check the connection and try again."),
        (f"/users/{LOGIN}/repos", "Could not reach GitHub while listing repositories."),
    ],
)
@pytest.mark.parametrize(
    "failure", [httpx.ConnectError("offline"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("bad")]
)
async def test_network_failures_are_reported(
    failure: Exception, path: str, message: str, api: GitHubApi
) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise failure

    api.overrides[path] = broken
    doc, _ = await run()
    assert doc.ok is False
    assert doc.error == message


async def test_a_response_that_is_not_json_is_treated_as_a_failed_call(api: GitHubApi) -> None:
    api.overrides[f"/users/{LOGIN}"] = lambda request: httpx.Response(200, text="<html>maintenance</html>")
    doc, _ = await run()
    assert doc.ok is False
    assert doc.error == "Could not reach GitHub. Check the connection and try again."


async def test_a_server_error_names_the_status(api: GitHubApi) -> None:
    api.overrides[f"/users/{LOGIN}"] = lambda request: httpx.Response(503, text="down")
    doc, _ = await run()
    assert doc.error == "GitHub returned an unexpected response (503)."


async def test_a_redirect_is_not_followed(api: GitHubApi) -> None:
    api.overrides[f"/users/{LOGIN}"] = lambda request: httpx.Response(
        301, headers={"location": "https://evil.test/steal"}
    )
    doc, _ = await run(token="ghp_secret")
    assert doc.ok is False
    assert [r.url.host for r in api.requests] == ["api.github.com"]  # the token never left for another host


async def test_an_oversized_response_is_cut_and_does_not_crash(api: GitHubApi) -> None:
    api.overrides[f"/users/{LOGIN}/repos"] = lambda request: httpx.Response(
        200, content=b"[" + b'{"name":"x"},' * 400_000
    )
    doc, _ = await run()
    assert doc.ok is False
    assert doc.error == "Could not reach GitHub while listing repositories."


# ----------------------------------------------------------------------------- tokens


def authorization(api: GitHubApi) -> set[str | None]:
    return {r.headers.get("authorization") for r in api.requests}


async def test_an_explicit_token_is_sent_as_a_bearer_token_to_the_api_only(api: GitHubApi) -> None:
    fully_loaded(api)
    doc, _ = await run(token="ghp_explicit")
    assert authorization(api) == {"Bearer ghp_explicit"}
    assert {r.url.host for r in api.requests} == {"api.github.com"}
    assert doc.meta["authenticated"] is True


async def test_without_a_token_no_authorization_header_is_sent(api: GitHubApi) -> None:
    doc, _ = await run()
    assert authorization(api) == {None}
    assert doc.meta["authenticated"] is False


async def test_the_environment_token_is_the_default(api: GitHubApi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "  ghp_from_env \n")
    doc, _ = await run()
    assert authorization(api) == {"Bearer ghp_from_env"}
    assert doc.meta["authenticated"] is True


async def test_an_explicit_token_wins_over_the_environment(
    api: GitHubApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
    await run(token="ghp_explicit")
    assert authorization(api) == {"Bearer ghp_explicit"}


async def test_an_empty_explicit_token_means_unauthenticated_even_with_an_environment_token(
    api: GitHubApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
    doc, _ = await run(token="")
    assert authorization(api) == {None}
    assert doc.meta["authenticated"] is False


@pytest.mark.parametrize(
    ("explicit", "env", "expected"),
    [
        (None, None, None),
        (None, "", None),
        (None, "  ", None),
        (None, " tok ", "tok"),
        ("explicit", "tok", "explicit"),
        (" explicit ", None, "explicit"),
        ("", "tok", None),
        ("   ", "tok", None),
    ],
)
def test_resolve_token(
    explicit: str | None, env: str | None, expected: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if env is None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GITHUB_TOKEN", env)
    assert resolve_token(explicit) == expected


async def test_the_token_never_appears_in_the_result_or_the_logs(api: GitHubApi, record_logs) -> None:
    fully_loaded(api)
    token = "ghp_VERY_SECRET_TOKEN_9917"
    doc, lines = await run(token=token)
    everything = (
        repr(doc)
        + repr(doc.to_dict())
        + " ".join(lines)
        + " ".join(str(v) for e in record_logs.events for v in e.values())
    )
    assert token not in everything
    [event] = record_logs.named("github_ingested")
    assert event["owner"] == LOGIN
    assert event["authenticated"] is True
    assert event["calls"] == 13
    assert event["ok"] is True


async def test_requests_identify_the_client_and_ask_for_the_right_formats(api: GitHubApi) -> None:
    api.user = user(public_repos=1)
    api.repos = [repo("only-repo", stars=3)]
    api.readmes["only-repo"] = "# only repo\n\nText."
    await run()
    by_path = {r.url.path: r for r in api.requests}
    assert by_path[f"/users/{LOGIN}"].headers["accept"] == "application/vnd.github+json"
    assert by_path[f"/repos/{LOGIN}/only-repo/readme"].headers["accept"] == "application/vnd.github.raw"
    for request in api.requests:
        assert request.headers["x-github-api-version"] == "2022-11-28"
        assert request.headers["user-agent"]
        assert request.headers["accept-encoding"] == "gzip, deflate"
    assert str(API) == "https://api.github.com"


# ----------------------------------------------------------------------------- the document that comes out


async def test_the_document_summarises_profile_repos_activity_and_practice(api: GitHubApi) -> None:
    fully_loaded(api)
    api.repos.append(repo("forked-lib", fork=True, language="Go"))
    doc, lines = await run()
    text = doc.text
    assert text.startswith("# GitHub: The Octocat (@octocat)")
    assert "- Bio: Builds developer tools" in text
    assert "- Followers: 1,234, following: 9" in text
    assert "- Public repositories: 40 (analysing the 32 most recently pushed)" in text
    assert "- Original repos: 31, forks: 1" in text
    assert "## Profile README (the repo named after the user)" in text
    assert "> Hi, I am the octocat. I write Python and Rust." in text
    assert "### 1. repo0" in text
    assert "- README excerpt:" in text
    assert "- Detected: tests yes (1 test file), CI yes (GitHub Actions), Docker yes" in text
    assert "## Recent public activity" in text
    assert "1 push (3 commits)" in text
    assert "## Technologies mentioned across repos" in text
    assert doc.meta["username"] == LOGIN
    assert doc.meta["repos"] == 40
    assert doc.meta["forks"] == 1
    assert doc.meta["withTests"] == 4 and doc.meta["withCI"] == 4 and doc.meta["inspected"] == 4
    assert doc.meta["topRepos"][:2] == ["repo0", "repo1"]
    assert "Python" in doc.meta["languages"]
    assert lines[0] == "Fetched profile, 40 public repos"
    assert "Ranked repos by recency and stars" in lines
    assert "Profile README read" in lines
    assert doc.src == "github.com/octocat"
    assert doc.url == "https://github.com/octocat"
    assert doc.quick_signals[0] == "Python · 31 repos"


async def test_a_bare_username_and_an_at_handle_are_accepted(api: GitHubApi) -> None:
    api.repos = []
    doc, _ = await run(LOGIN)
    assert doc.ok is True
    assert doc.url == f"https://github.com/{LOGIN}"
    doc, _ = await run(f"@{LOGIN}")
    assert doc.ok is True


@pytest.mark.parametrize(
    "bad",
    ["", "not a link!!", "https://gitlab.com/octocat", "https://github.com/orgs", "https://github.com/"],
)
async def test_something_that_is_not_a_github_target_fails_without_any_request(
    bad: str, api: GitHubApi
) -> None:
    doc, _ = await run(bad)
    assert doc.ok is False
    assert doc.error == "That does not look like a GitHub profile or repository link."
    assert api.requests == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("octocat", ("octocat", None)),
        ("@octocat", ("octocat", None)),
        ("https://github.com/octocat", ("octocat", None)),
        ("https://www.github.com/octocat/", ("octocat", None)),
        ("github.com/octocat/hello-world", ("octocat", "hello-world")),
        ("https://github.com/octocat/hello-world.git", ("octocat", "hello-world")),
        ("https://github.com/octocat/hello-world/tree/main/src", ("octocat", "hello-world")),
        ("https://github.com/a", ("a", None)),
        ("https://github.com/orgs/acme", None),
        ("https://github.com/topics/python", None),
        ("https://github.com/settings", None),
        ("https://github.com/-bad", None),
        ("https://github.com/bad_name", None),
        ("https://example.com/octocat", None),
        ("https://user:pw@github.com/octocat", None),
        ("", None),
    ],
)
def test_parse_target(value: str, expected: tuple[str, str | None] | None) -> None:
    assert _parse_target(value) == expected


# ----------------------------------------------------------------------------- README cleaning


def test_a_readme_loses_badges_images_comments_and_markup_but_keeps_code() -> None:
    raw = (
        "<!-- hidden note -->\n"
        "[![CI](https://ci/badge.svg)](https://ci) ![logo](logo.png)\n"
        '<p align="center"><img src="x.png" width="100"></p>\n'
        "# Title\n"
        "A [link](https://example.com) and <b>bold</b> text.\n"
        "[ref]: https://example.com/ref\n"
        "```python\n"
        "print('<!-- kept -->')  # [![not](a)](b)\n"
        "```\n"
    )
    cleaned = _clean_readme(raw)
    assert "hidden note" not in cleaned
    assert "badge.svg" not in cleaned
    assert "logo.png" not in cleaned
    assert "<img" not in cleaned
    assert "A link and bold text." in cleaned
    assert "[ref]:" not in cleaned
    assert "print('<!-- kept -->')  # [![not](a)](b)" in cleaned  # code fences stay verbatim


def test_a_long_readme_is_cut_to_the_budget_with_a_marker() -> None:
    cleaned = _clean_readme("\n".join(f"Line number {i} of a very long README." for i in range(2000)))
    assert len(cleaned) <= gh.README_CHARS + len("\n[…truncated]")
    assert cleaned.endswith("\n[…truncated]")


def _hostile_readmes() -> dict[str, str]:
    return {
        "unterminated comments": "<!--" * 400_000,
        "bracket flood": "[" * 1_000_000,
        "image openers": "![" * 500_000,
        "badge openers": "[![" * 300_000,
        "link openers": "[a](" * 300_000,
        "tag openers": "<a " * 500_000,
        "img openers": "<img " * 300_000,
        "unterminated fence": "```" + "x" * 1_000_000,
        "fences": "```x```" * 150_000,
        "reference definitions": "[a]:" + " " * 200_000 + "x",
        "newlines": "\n" * 1_000_000,
        "spaces and newlines": " \n" * 500_000,
    }


@pytest.mark.parametrize("name", list(_hostile_readmes()))
def test_a_hostile_readme_is_cleaned_in_linear_time(name: str) -> None:
    started = time.perf_counter()
    _clean_readme(_hostile_readmes()[name])
    assert time.perf_counter() - started < 8.0, name


# ----------------------------------------------------------------------------- aliases of the shared helpers


def test_shared_helpers_are_the_core_ones() -> None:
    from tayyari.core import limits, text

    assert gh._js_round is text.js_round
    assert gh._one_line is text.one_line
    assert gh.MAX_CALLS == limits.GITHUB_MAX_CALLS == 14
    assert gh.plural is text.plural


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0.2, "today"),
        (1, "1 day ago"),
        (13, "13 days ago"),
        (20, "3 weeks ago"),
        (100, "3 months ago"),
        (800, "2 years ago"),
    ],
)
def test_ago_wording(days: float, expected: str) -> None:
    now = datetime.now(UTC).timestamp()
    assert gh._ago(iso(days + 0.01), now) == expected


# ----------------------------------------------------------------------------- NUL characters


async def test_nul_characters_from_the_api_never_leave_the_ingester(api: GitHubApi) -> None:
    """Postgres refuses a NUL in TEXT and JSONB, so a bio such as "a\\u0000b" must not reach the stored row."""
    nul = chr(0)
    fully_loaded(api)
    api.user = user(name="The" + nul + " Octocat", bio="Builds" + nul + " tools", location="SF" + nul)
    api.repos[0] = repo("repo0", stars=99, description="A" + nul + " description")
    api.readmes["repo0"] = "# repo0" + nul + "\n\nThis repository shows repo0 in action with Docker."
    api.readmes[LOGIN] = "Hi, I write Python" + nul + " and Rust."
    doc, _ = await run()
    assert doc.ok is True
    assert "Builds tools" in doc.text
    everything = json.dumps(
        {"text": doc.text, "meta": doc.meta, "notes": doc.notes, "signals": doc.quick_signals}
    )
    assert nul not in doc.text
    assert nul not in everything
    assert "\\u0000" not in everything
