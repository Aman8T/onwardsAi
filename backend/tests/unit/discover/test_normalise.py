"""Titles, apply links, ids."""

from __future__ import annotations

import pytest

from tayyari.discover.normalise import (
    apply_host,
    clean_apply_url,
    content_hash,
    is_entry_level_title,
    is_listing_id,
    listing_id,
    normalise_title,
    sentence_case,
    title_similarity,
    title_tokens,
)


def tokens(title: str) -> frozenset[str]:
    return title_tokens(normalise_title(title))


@pytest.mark.parametrize(
    ("title", "norm"),
    [
        ("Senior Frontend Engineer", "senior frontend engineer"),
        ("Sr. Front-End Engineer (React) ", "senior frontend engineer"),
        ("Full Stack Developer [Remote]", "fullstack developer"),
        ("Product Manager & Owner", "product manager and owner"),
        ("Engineer – Platform", "engineer platform"),
    ],
)
def test_a_title_is_normalised_for_comparison(title: str, norm: str) -> None:
    assert normalise_title(title) == norm


def test_similar_titles_are_close_and_seniority_is_kept() -> None:
    senior = tokens("Senior Frontend Engineer")
    assert title_similarity(senior, tokens("Senior Front-end Developer")) == 1.0  # a developer is an engineer
    assert title_similarity(senior, tokens("Frontend Engineer II")) >= 0.5  # a level numeral is dropped
    assert title_similarity(senior, tokens("Staff Frontend Engineer")) == 0.5  # staff is not senior
    assert title_similarity(tokens("Frontend Engineer"), tokens("UI Engineer")) == 1.0
    assert title_similarity(senior, tokens("Accounts Executive")) == 0.0
    assert title_similarity(frozenset(), senior) == 0.0


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer Intern",
        "Graduate Engineer Trainee",
        "Junior Accountant",
        "Jr. Developer",
        "Accounts Trainee",
        "Entry-Level Analyst",
        "Data Analyst I",
        "Fresher - Customer Support",
        "Early Career Designer",
    ],
)
def test_entry_level_titles(title: str) -> None:
    assert is_entry_level_title(title)


@pytest.mark.parametrize(
    "title", ["Senior Engineer", "Engineering Manager", "Data Analyst II", "Internal Auditor"]
)
def test_other_titles_are_not_entry_level(title: str) -> None:
    assert not is_entry_level_title(title)


def test_sentence_case_keeps_acronyms() -> None:
    assert sentence_case("Senior Frontend Engineer") == "Senior frontend engineer"
    assert sentence_case("UI/UX Designer") == "UI/UX designer"
    assert sentence_case("full-stack ENGINEER") == "Full-stack ENGINEER"


def test_tracking_parameters_are_stripped_and_everything_else_is_kept() -> None:
    url = (
        "https://boards.example.com/acme/jobs/9?gh_jid=9&utm_source=linkedin&UTM_Medium=x&gclid=1&fbclid=2"
        "&mc_cid=3&mc_eid=4&lever-source=Naukri&lever-source[]=x&ref=friend#apply"
    )
    assert clean_apply_url(url) == "https://boards.example.com/acme/jobs/9?gh_jid=9&ref=friend#apply"
    assert (
        clean_apply_url("https://jobs.lever.co/x/abc?lever-source=Linkedin") == "https://jobs.lever.co/x/abc"
    )
    assert clean_apply_url("https://a.example/p?utm_source=x") == "https://a.example/p"
    assert clean_apply_url("https://a.example/p?gh_jid=7") == "https://a.example/p?gh_jid=7"


@pytest.mark.parametrize(
    "url", [None, "", "ftp://a.example/x", "javascript:alert(1)", "/relative/path", "https://"]
)
def test_a_link_that_is_not_a_web_link_is_refused(url: str | None) -> None:
    assert clean_apply_url(url) is None


def test_the_apply_host_drops_www() -> None:
    assert apply_host("https://www.Acme.com/jobs/1") == "acme.com"
    assert apply_host("https://jobs.lever.co/x") == "jobs.lever.co"


def test_listing_ids_are_stable_and_shaped() -> None:
    first = listing_id("greenhouse", "acme", "1001")
    assert first == listing_id("greenhouse", "acme", "1001")
    assert first != listing_id("lever", "acme", "1001") != listing_id("greenhouse", "other", "1001")
    assert is_listing_id(first) and len(first) == 32
    assert not is_listing_id("nope") and not is_listing_id(None) and not is_listing_id("A" * 32)


def test_a_content_hash_changes_with_any_part() -> None:
    assert content_hash("a", None, 1) == content_hash("a", None, 1)
    assert content_hash("a", None, 1) != content_hash("a", "x", 1) != content_hash("a", None, 2)
