"""Skill-term matching: word boundaries, the "Go" special case, case-sensitive aliases and ordering."""

from __future__ import annotations

import time

import pytest

from tayyari.ingest.terms import TERM_COUNT, TERM_DEFS, TermHit, count_terms, find_terms


def counts(text: str) -> dict[str, int]:
    return {hit.term: hit.count for hit in count_terms(text)}


# ----------------------------------------------------------------------------- the "Go" special case


@pytest.mark.parametrize(
    "text",
    [
        "Languages: Python, Go, Rust",
        "Go, Python",
        "Python, Go",
        "Skills\nGo\nPython",
        "Python | Go | Rust",
        "Python • Go • Rust",
        "Python · Go",
        "Python/Go/Rust",
        "Python; Go; Rust",
        "Backend (Go)",
        "Go",
        "  Go  ",
        "Languages:   Go   ,Python",
        "Python,\nGo\n",
        "Python,\n\n\n   Go   \nRust",
        "Golang",
        "I write Golang services",
    ],
)
def test_go_counts_when_it_stands_in_a_list_of_skills(text: str) -> None:
    assert "Go" in find_terms(text)


@pytest.mark.parametrize(
    "text",
    [
        "I want to Go home now",
        "Let's go to the market",
        "Go to market strategy for the launch",
        "We will Go live next week.",
        "Ready, Set, Go!",
        "go",
        "GO",
        "Google Cloud and Gone with the wind",
        "Cargo and Mango",
        "MongoDB",
        "Go-lang is not a thing",
        "Please Go\nPlease Go",
    ],
)
def test_go_does_not_count_as_an_english_word(text: str) -> None:
    assert "Go" not in find_terms(text)


def test_go_to_market_is_its_own_term() -> None:
    result = counts("Go-to-market strategy and GTM")
    assert result["Go-to-market"] == 2
    assert "Go" not in result


def test_go_in_a_list_is_counted_once_per_occurrence() -> None:
    assert counts("Go, Python, Go, Rust")["Go"] == 2
    assert counts("Golang, Go, Python")["Go"] == 2  # the alias and the listed Go are two occurrences


# ----------------------------------------------------------------------------- word boundaries


def test_java_is_not_found_inside_javascript_and_sql_is_not_found_inside_nosql() -> None:
    assert counts("JavaScript developer") == {"JavaScript": 1}
    assert counts("Java and JavaScript") == {"Java": 1, "JavaScript": 1}
    assert counts("NoSQL only") == {"NoSQL": 1}
    assert "SQL" not in counts("MySQL, PostgreSQL, SQLite and NoSQL")
    assert counts("SQL, PostgreSQL") == {"SQL": 1, "PostgreSQL": 1}


def test_terms_ending_in_symbols_keep_their_boundaries() -> None:
    assert counts("C++ and C# developer") == {"C++": 1, "C#": 1}
    assert "C++" in counts("Skills: C++, Python")
    assert "C++" not in counts("C++11 code")
    assert "C#" in counts("We use C#.")
    assert ".NET" in counts("We use .NET daily")
    assert counts("ASP.NET Core").get(".NET") == 1
    assert "Node.js" in counts("Built with Node.js.")
    assert "Python" not in counts("Python3 scripts")
    assert "Python" in counts("python, scripts")


def test_separators_inside_names_are_flexible() -> None:
    for text in ("Machine Learning", "machine-learning", "Machine   Learning", "MACHINE\tLEARNING"):
        assert "Machine Learning" in find_terms(text), text
    for text in ("CI/CD", "ci / cd", "CI /CD"):
        assert "CI/CD" in find_terms(text), text
    for text in ("Next.js", "NextJS", "Next js", "Next-js", "nextjs"):
        assert "Next.js" in find_terms(text), text
    assert "A/B Testing" in find_terms("ran A/B tests and ab testing")
    assert "Fine-tuning" in find_terms("fine tuning, finetuning and Fine-tuning")


def test_aliases_report_the_canonical_name_and_count_each_position_once() -> None:
    assert counts("React.js") == {"React": 1}
    assert counts("React and ReactJS and React.js") == {"React": 3}
    assert counts("Postgres, PostgreSQL") == {"PostgreSQL": 2}
    assert counts("Led a team of five") == {"Leadership": 1}


# ----------------------------------------------------------------------------- case sensitivity


@pytest.mark.parametrize(
    "word",
    [
        "Swift",
        "Rust",
        "Ruby",
        "Scala",
        "Dart",
        "Lua",
        "Julia",
        "Flutter",
        "Android",
        "iOS",
        "RAG",
        "AI",
        "Claude",
        "Spark",
        "dbt",
        "Excel",
        "Jest",
        "Sketch",
        "Blender",
        "Framer",
        "Notion",
        "Linear",
        "Slack",
        "Operations",
        "Sales",
        "Growth",
        "Lovable",
        "Stripe",
        "GitHub",
        "Lambda",
        "Remix",
        "Astro",
        "Express",
        "Rails",
    ],
)
def test_words_that_are_also_plain_english_need_their_exact_case(word: str) -> None:
    assert word in find_terms(f"We use {word} every day.")
    if word.lower() != word:  # "dbt" is lower case already
        assert word not in find_terms(f"we use {word.lower()} every day.")


@pytest.mark.parametrize(
    ("alias", "term", "text_hit", "text_miss"),
    [
        ("JS", "JavaScript", "We write JS daily", "we write js daily"),
        ("Cpp", "C++", "Modern Cpp code", "modern cpp code"),
        ("R programming", "R language", "R programming for stats", "r programming for stats"),
        ("Node", "Node.js", "Node services", "the node of a tree"),
        ("REST", "REST APIs", "REST services", "the rest of the team"),
        ("ML", "Machine Learning", "ML models", "5 ml of water"),
        ("K8s", "Kubernetes", "K8s clusters", "k8s clusters"),
    ],
)
def test_aliases_marked_with_an_equals_sign_are_case_sensitive(
    alias: str, term: str, text_hit: str, text_miss: str
) -> None:
    assert term in find_terms(text_hit), alias
    assert term not in find_terms(text_miss), alias


def test_ordinary_terms_ignore_case_and_so_do_their_plain_aliases() -> None:
    assert "JavaScript" in find_terms("javascript and ECMASCRIPT")
    assert "Python" in find_terms("PYTHON")
    assert "TypeScript" in find_terms("typescript and ts")
    assert "Docker" in find_terms("DOCKER")
    assert "RESTful" not in find_terms("RESTful")  # an alias, never a canonical name
    assert "REST APIs" in find_terms("restful services")


# ----------------------------------------------------------------------------- ordering and limits


def test_hits_are_ordered_by_count_then_by_first_position() -> None:
    assert count_terms("React React React Python Python Docker") == [
        TermHit("React", 3),
        TermHit("Python", 2),
        TermHit("Docker", 1),
    ]
    assert find_terms("Docker, then Python, then Redis, then Kafka") == ["Docker", "Python", "Redis", "Kafka"]
    assert find_terms("Kafka Redis Python Docker Kafka") == ["Kafka", "Redis", "Python", "Docker"]


def test_find_terms_applies_its_limit() -> None:
    text = "React Vue Angular Svelte Django Flask FastAPI Redis Docker Kubernetes"
    assert len(find_terms(text)) == 8
    assert len(find_terms(text, 3)) == 3
    assert find_terms(text, 0) == []
    assert len(count_terms(text)) == 10


def test_nothing_in_nothing_out() -> None:
    assert count_terms("") == []
    assert find_terms("") == []
    assert find_terms("   \n\t ") == []
    assert find_terms("lorem ipsum dolor sit amet") == []


# ----------------------------------------------------------------------------- the dictionary itself


def test_the_dictionary_has_unique_names_and_a_matching_count() -> None:
    names = [d.name for d in TERM_DEFS]
    assert len(names) == len(set(names)) == TERM_COUNT
    assert TERM_COUNT > 200


@pytest.mark.parametrize("definition", TERM_DEFS, ids=lambda d: d.name)
def test_every_term_and_alias_is_found_in_a_sentence(definition) -> None:
    spellings = [definition.name, *(a.removeprefix("=") for a in definition.aliases)]
    for spelling in spellings:
        if definition.name == "Go" and spelling == "Go":
            text = "Languages: Go, Python"
        else:
            text = f"We use {spelling} every day."
        assert definition.name in find_terms(text, 50), spelling


# ----------------------------------------------------------------------------- hostile input


@pytest.mark.parametrize(
    "text",
    [
        "go" + "\n" * 40_000,
        "go" + " \n" * 20_000,
        "go" + "\t\n" * 20_000,
        "Go" + " " * 100_000 + "x",
        "," * 200_000 + "Go",
        "Machine" + " " * 100_000 + "x",
        "React" + "-" * 100_000,
        "C" + "+" * 100_000,
        "Next" + " " * 100_000 + "js",
        ("Go " * 50_000),
        ("Python, " * 50_000),
    ],
    ids=[
        "newlines",
        "space-newlines",
        "tab-newlines",
        "go-spaces",
        "commas",
        "machine-spaces",
        "hyphens",
        "pluses",
        "next-spaces",
        "go-words",
        "python-list",
    ],
)
def test_hostile_text_is_matched_in_linear_time(text: str) -> None:
    started = time.perf_counter()
    find_terms(text)
    assert time.perf_counter() - started < 5.0


def test_a_large_realistic_text_is_matched_quickly() -> None:
    sentence = "Built React and TypeScript interfaces with Node.js, Go and PostgreSQL; led a team and mentored interns. "
    text = sentence * 6_000  # about 600 KB
    started = time.perf_counter()
    hits = counts(text)
    assert time.perf_counter() - started < 10
    assert hits["React"] == 6_000
    assert "Go" not in hits  # ", Go and" is a sentence, not a list of skills
