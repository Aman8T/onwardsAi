"""The skill vocabulary and its matcher."""

from __future__ import annotations

import pytest

from tayyari.discover.vocab import TermMatcher, Vocabulary, prep, prep_term, vocabulary


def named(text: str) -> set[str]:
    vocab = vocabulary()
    return {vocab.skills[skill_id].name for skill_id, _ in vocab.scan(prep(text))}


def test_the_vocabulary_covers_about_four_hundred_skills_across_professions() -> None:
    vocab = vocabulary()
    assert len(vocab.skills) >= 400
    families = {skill.family for skill in vocab.skills.values()}
    for family in (
        "engineering", "data and ai", "design", "product and project", "marketing", "sales", "finance and accounting",
        "people and hr", "operations and supply chain", "customer support", "healthcare", "education", "legal",
        "content and communications",
    ):  # fmt: skip
        assert family in families
    assert len(vocab.terms) > len(vocab.skills)  # aliases exist


def test_no_term_belongs_to_two_skills_and_the_text_form_is_stable() -> None:
    Vocabulary()  # building it raises on a duplicate skill or a term claimed twice
    vocab = vocabulary()
    for term, skill_id in vocab.terms.items():
        assert term == prep_term(term) and skill_id in vocab.skills


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We use React, TypeScript and Node.js.", {"React", "TypeScript", "Node.js"}),
        ("Built in C++ and C#", {"C++", "C#"}),
        ("CI/CD pipelines on AWS with Kubernetes (k8s)", {"CI/CD", "AWS", "Kubernetes"}),
        ("front-end, full-stack and e-commerce", set()),
        ("Experience with Microsoft Excel, Tally Prime and GST returns", {"Microsoft Excel", "Tally", "GST"}),
        ("accessibility, a11y and WCAG", {"Web accessibility"}),
        ("Postgres and MongoDB", {"PostgreSQL", "MongoDB"}),
        ("5 years of Golang", {"Go"}),
        ("Patient care, BLS and ACLS certified nurse", {"Patient care", "Nursing"}),
    ],
)
def test_a_posting_names_the_skills_it_uses(text: str, expected: set[str]) -> None:
    assert named(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "go ahead and go home",  # Go is only golang
        "We excel in a fast paced environment",  # Excel is only Microsoft Excel
        "He is a swift decision maker",  # Swift is only SwiftUI or Swift programming
        "reactive, javascripting, nodejsx tools",  # a term is matched on word boundaries
        "a rare c",  # a bare C is not a skill
        "the sales team",  # generic words are not skills
    ],
)
def test_everyday_words_are_not_skills(text: str) -> None:
    assert named(text) == set()


def test_javascript_is_not_java_and_react_native_is_not_react() -> None:
    assert named("JavaScript developer") == {"JavaScript"}
    assert named("Java developer") == {"Java"}
    assert named("React Native apps") == {"React Native"}
    assert named("react-native apps") == {"React Native"}  # a hyphen reads as a space


def test_hyphens_read_as_spaces_without_moving_a_position() -> None:
    text = "Full-Stack – React"
    prepared = prep(text)
    assert len(prepared) == len(text) and prepared == "full stack   react"
    assert [pos for _, pos in vocabulary().scan(prepared)] == [text.index("React")]


def test_lowering_never_changes_a_length() -> None:
    odd = "İstanbul React"  # a capital I with a dot lowers to two characters
    assert len(prep(odd)) == len(odd)


def test_a_term_matcher_finds_a_persons_own_words() -> None:
    matcher = TermMatcher({prep_term("Prisma"): "x:prisma", prep_term("motion & animation"): "x:motion"})
    found = list(matcher.scan(prep("We use Prisma. Motion & Animation is a plus.")))
    assert [skill_id for skill_id, _ in found] == ["x:prisma", "x:motion"]
    assert list(TermMatcher({}).scan("anything")) == []
    assert len(matcher) == 2


def test_the_bare_name_of_a_hard_skill_is_understood_in_a_profile_but_not_matched_in_a_posting() -> None:
    vocab = vocabulary()
    assert vocab.lookup("Go") == "go" and vocab.lookup("R") == "r"
    assert vocab.lookup("Postgres") == "postgresql"
    assert vocab.lookup("Underwater basket weaving") is None
    assert named("go and r") == set()
