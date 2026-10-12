"""Folding, hashing, terms and the two small alias tables."""

from __future__ import annotations

import pytest

from tayyari.match.aliases import domain_score, same_term, term_variants
from tayyari.match.text import (
    clip,
    fold_text,
    posting_hash,
    quote_in,
    term_in_corpus,
    terms_in_text,
    urls_in,
    word_count,
)
from tayyari.portfolio.ground import Corpus


def test_fold_text_makes_quotes_dashes_and_whitespace_plain() -> None:
    assert (
        fold_text("  \u201cSenior\u201d \u2013 Growth\u00a0 Manager\n\u2022 Lead  ")
        == '"senior" - growth manager lead'
    )
    assert fold_text("It\u2019s\u200b here") == "it's here"


def test_a_quote_is_found_across_wrapped_lines_and_list_markers() -> None:
    posting = fold_text("- Must have 5+ years of\n  growth \u2014 performance\n- Required: A/B testing")
    assert quote_in("Must have 5+ years of growth - performance", posting)
    assert quote_in("\u201cRequired: A/B testing\u201d", posting)
    assert not quote_in("Must have 7+ years", posting)
    assert not quote_in("ab", posting)  # too short to prove anything


def test_posting_hash_ignores_layout_not_content() -> None:
    assert posting_hash("Senior  Manager\n\nLumen") == posting_hash(" senior manager lumen ")
    assert posting_hash("Senior Manager") != posting_hash("Junior Manager")
    assert len(posting_hash("x")) == 64


def test_urls_are_listed_without_trailing_punctuation_or_repeats() -> None:
    found = urls_in("Apply at https://a.example/x). Or (https://b.example/y), again https://a.example/x.")
    assert found == ["https://a.example/x", "https://b.example/y"]


def test_word_count_counts_words_not_symbols() -> None:
    assert word_count("Led a team of 5 - and it worked, didn't it?") == 10


def test_clip_cuts_at_a_word() -> None:
    assert clip("one two three four", 12).endswith("\u2026")
    assert clip("short", 12) == "short"


@pytest.mark.parametrize(
    ("term", "text", "expected"),
    [
        ("JavaScript", "Built it with JS and Vite", True),
        ("Postgres", "PostgreSQL tuning", True),
        ("Kubernetes", "Ran k8s clusters", True),
        ("React", "Reacted quickly", False),
        ("Java", "JavaScript only", False),
        ("A/B testing", "Ran 38 A/B tests", True),
        ("R", "R and Python", True),
        ("R", "R&D budget", False),
    ],
)
def test_terms_match_whole_words_and_spellings_not_substrings(term: str, text: str, expected: bool) -> None:
    assert term_in_corpus(term, Corpus.of(text), text) is expected


def test_terms_in_text_lists_each_used_term_once() -> None:
    assert terms_in_text("Shipped React and TypeScript, then more React", ["React", "Vue", "TypeScript"]) == [
        "React",
        "TypeScript",
    ]


def test_term_aliases_are_spellings_of_the_same_thing_never_neighbours() -> None:
    assert same_term("JS", "javascript")
    assert same_term("Node.js", "nodejs")
    assert "k8s" in term_variants("Kubernetes")
    assert not same_term("design system", "component library")
    assert not same_term("React", "React Native")


@pytest.mark.parametrize(
    ("hint", "domain", "at_least"),
    [
        ("Frontend", "Frontend Engineering", 3),
        ("front end", "Frontend Engineering", 2),
        ("Lifecycle marketing", "Lifecycle & CRM", 2),
        ("Growth marketing", "Paid Acquisition", 2),
        ("Design systems", "Design Systems & Motion", 3),
        ("Data and analytics", "Experimentation & Analytics", 2),
    ],
)
def test_domain_hints_find_their_domain(hint: str, domain: str, at_least: int) -> None:
    assert domain_score(hint, domain) >= at_least


@pytest.mark.parametrize(
    ("hint", "domain"),
    [("Retail", "Backend & APIs"), ("Languages", "Paid Acquisition"), ("Systems", "Frontend Engineering")],
)
def test_unrelated_hints_do_not_match(hint: str, domain: str) -> None:
    assert domain_score(hint, domain) < 2
