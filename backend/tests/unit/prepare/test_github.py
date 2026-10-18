"""The repositories to start from, ranked in code from the stored GitHub digest: the parser over the bundled sample digest
(`tayyari/sample/github.md`), the owner from the source url, the ranking by shared words with the plan, the reasons, the one
suggestion, and the answer when the graph had no GitHub source. Nothing here touches the network."""

from __future__ import annotations

from tayyari.contract.prepare import PrepareCited, PrepareMove, PreparePhase, PrepareRepos
from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.core.paths import SAMPLE_DIR
from tayyari.prepare.github import (
    MAX_REPOS,
    RepoDigest,
    github_source,
    owner_of,
    parse_digest,
    plan_terms,
    rank_repos,
    repos_for,
    tokens,
)
from tests.unit.prepare.support import plan_for, riya

DIGEST = (SAMPLE_DIR / "github.md").read_text(encoding="utf-8")


def test_the_sample_digest_parses_into_its_ten_top_repositories() -> None:
    repos = parse_digest(DIGEST)
    assert [r.name for r in repos] == [
        "motion-kit",
        "design-tokens",
        "notes-ai",
        "checkout-lab",
        "recipe-genie-web",
        "a11y-checklist",
        "trpc-starter",
        "habit-grid",
        "gql-playground",
        "portfolio-site",
    ]
    motion = repos[0]
    assert motion.language == "TypeScript" and motion.tests and motion.ci and motion.readme
    assert motion.topics == ("react", "animation", "springs", "gestures", "framer-motion")
    assert motion.description is not None and motion.description.startswith(
        "Spring-physics animation primitives"
    )
    assert "interruptible by default" in motion.excerpt
    notes = repos[2]
    assert (notes.tests, notes.ci, notes.readme) == (False, False, True)
    genie = repos[4]
    assert genie.readme is False  # "README: default Lovable template"
    assert repos[5].language == "MDX" and repos[5].tests is False  # no Detected line at all


def test_the_other_sections_of_the_digest_are_not_read_as_repositories() -> None:
    repos = parse_digest(DIGEST)
    assert "spring-sandbox" not in {
        r.name for r in repos
    }  # "Other original repositories" is bullets, not blocks
    assert len(repos) <= MAX_REPOS


def test_a_block_with_no_description_language_or_readme_is_read_plainly() -> None:
    text = (
        "## Top repositories (2)\n\n### 1. thing (fork)\n- Description: (none)\n- Language: n/a, stars: 0, forks: 0\n"
        "- README: none found\n\n### 2. other (archived)\n- Detected: tests no, CI yes (CircleCI), Docker no, docs no\n"
        "- README: not fetched (GitHub rate limit or network error); do not treat it as missing\n"
    )
    first, second = parse_digest(text)
    assert first == RepoDigest("thing", None, None, (), False, False, False, "")
    assert (second.name, second.tests, second.ci, second.readme) == ("other", False, True, True)


def test_the_owner_comes_from_a_github_profile_or_repository_url() -> None:
    assert owner_of("https://github.com/riyakapoor") == "riyakapoor"
    assert owner_of("https://www.github.com/riyakapoor/motion-kit") == "riyakapoor"
    assert owner_of("github.com/riyakapoor") == "riyakapoor"
    assert owner_of("https://gitlab.com/riyakapoor") is None
    assert owner_of(None) is None and owner_of("") is None and owner_of("https://github.com/") is None


def test_tokens_drop_stopwords_short_words_and_numbers() -> None:
    assert tokens("Add 20 tests to the notes-ai app and ship it") == {"tests", "notes", "ai"} - {"ai"}
    assert "embeddings" in tokens("OpenAI embeddings + a local vector index") and "openai" in tokens("OpenAI")


def _move(title: str, body: str = "", tasks: tuple[str, ...] = ("Do it",)) -> PrepareMove:
    return PrepareMove(
        id="m1",
        title=title,
        body=body,
        have=[PrepareCited(text="have", evidenceIds=["e1"])],
        works=["LLM evals"],
        phases=[PreparePhase(title="Phase", hours=4, tasks=list(tasks), deliverable="Done")],
    )


def test_the_plans_terms_come_from_the_moves_and_the_gap_names() -> None:
    plan = plan_for().model_copy(
        update={"moves": [_move("Add an eval harness to notes-ai", "embeddings and citations")]}
    )
    terms = plan_terms(plan)
    assert {"eval", "harness", "notes", "embeddings", "citations", "evals", "llm", "retrieval"} <= terms


def test_repositories_are_ranked_by_shared_words_then_tests_and_ci_and_one_is_suggested() -> None:
    plan = plan_for().model_copy(
        update={
            "moves": [
                _move(
                    "Add an eval harness to notes-ai",
                    "Ask questions over your markdown notes with embeddings",
                )
            ]
        }
    )
    out = repos_for(plan, riya()[1])
    assert out.github is True
    names = [r.name for r in out.repos]
    assert names[0] == "notes-ai" and out.repos[0].suggested is True
    assert sum(1 for r in out.repos if r.suggested) == 1
    assert out.repos[0].url == "https://github.com/riyakapoor/notes-ai"
    assert out.repos[0].language == "TypeScript" and out.repos[0].description is not None
    assert (out.repos[0].tests, out.repos[0].ci, out.repos[0].readme) == (False, False, True)
    assert len(out.repos) == 10


def test_the_reasons_are_short_code_made_strings() -> None:
    plan = plan_for().model_copy(
        update={"moves": [_move("Add an eval harness to notes-ai", "embeddings and OpenAI answers")]}
    )
    by_name = {r.name: r for r in repos_for(plan, riya()[1]).repos}
    assert by_name["motion-kit"].reasons == ["has tests", "has CI"]
    assert by_name["recipe-genie-web"].reasons[-1] == "no README yet"
    assert by_name["a11y-checklist"].reasons == []
    allowed = {"has tests", "has CI", "no README yet"}
    for repo in by_name.values():
        for reason in repo.reasons:
            assert reason in allowed or reason.startswith("names ")


def test_a_repository_that_names_a_gap_word_says_so_in_the_gaps_own_spelling() -> None:
    plan = plan_for().model_copy(update={"moves": [_move("Ship it")]})
    gaps = [plan.gaps[0].model_copy(update={"name": "OpenAI embeddings"})]
    plan = plan.model_copy(update={"gaps": gaps})
    repos = [
        RepoDigest(
            "notes-ai",
            "OpenAI embeddings + a local vector index",
            "TypeScript",
            ("openai",),
            False,
            False,
            True,
            "",
        ),
        RepoDigest("dotfiles", "zsh and tmux", "Shell", (), False, False, True, ""),
    ]
    out = rank_repos(repos, plan, owner="riyakapoor")
    assert out.repos[0].name == "notes-ai" and out.repos[0].reasons == ["names OpenAI", "names embeddings"]
    assert out.repos[1].reasons == [] and out.repos[1].url == "https://github.com/riyakapoor/dotfiles"


def test_a_broad_word_of_a_gap_name_is_never_a_reason_on_its_own() -> None:
    plan = plan_for().model_copy(update={"moves": [_move("Ship it")]})
    gaps = [plan.gaps[0].model_copy(update={"name": "Web Performance"})]
    plan = plan.model_copy(update={"gaps": gaps})
    repos = [
        RepoDigest(
            "perf-lab", "Web performance budgets for a web app", "TypeScript", (), True, True, True, ""
        )
    ]
    out = rank_repos(repos, plan, owner="riyakapoor")
    assert "names Web" not in out.repos[0].reasons  # "Web" says nothing about what the repo is for
    assert "names Performance" in out.repos[0].reasons


def test_nothing_is_suggested_when_no_repository_shares_a_word_with_the_plan() -> None:
    plan = plan_for().model_copy(
        update={"moves": [_move("Zzz qqq")], "gaps": [plan_for().gaps[0].model_copy(update={"name": "Qqq"})]}
    )
    repos = [RepoDigest("motion-kit", "Springs for React", "TypeScript", (), True, True, True, "")]
    out = rank_repos(repos, plan, owner=None)
    assert out.repos[0].suggested is False and out.repos[0].url is None
    assert out.repos[0].reasons == ["has tests", "has CI"]


def test_tests_and_ci_break_ties_between_equally_matching_repositories() -> None:
    plan = plan_for().model_copy(update={"moves": [_move("Build a retry for the checkout")]})
    repos = [
        RepoDigest("checkout-a", "a checkout", None, (), False, False, True, ""),
        RepoDigest("checkout-b", "a checkout", None, (), True, True, True, ""),
    ]
    out = rank_repos(repos, plan, owner=None)
    assert [r.name for r in out.repos] == ["checkout-b", "checkout-a"] and out.repos[0].suggested


def test_without_a_github_source_the_answer_says_so() -> None:
    plan = plan_for()
    assert repos_for(plan, StoredContext()) == PrepareRepos(github=False, repos=[])
    empty = StoredContext(sources=[StoredSource(id="github", label="GitHub", kind="github", content="   ")])
    assert repos_for(plan, empty).github is False
    assert github_source(riya()[1]) is not None


def test_the_answer_is_valid_contract_json() -> None:
    plan = plan_for().model_copy(update={"moves": [_move("Add citations to notes-ai")]})
    out = repos_for(plan, riya()[1])
    assert PrepareRepos.from_dict(out.to_dict()) == out
