"""Narrowing in code: place, kind of role, level, and a shortlist that one big board cannot fill."""

from __future__ import annotations

import pytest

from tayyari.discover import places
from tayyari.discover.person import PersonIndex
from tayyari.discover.textutil import YearsAsked
from tayyari.discover.vocab import vocabulary
from tayyari.jobsearch.brief import build_brief
from tayyari.jobsearch.narrow import Posting, Targets, at_level, in_place, narrow, select
from tests.fakes.discover import profile_of
from tests.unit.jobsearch.support import listing

BENGALURU = places.city_by_name("Bengaluru")
VOCAB = vocabulary()
REACT_TEXT = "We want 2+ years of experience with React, TypeScript and CSS. Jest for tests."


def targets() -> Targets:
    profile = profile_of("frontend")
    return Targets(build_brief(profile, BENGALURU, scouts=3), PersonIndex(profile, VOCAB), VOCAB)


@pytest.mark.parametrize(
    ("title", "strength"),
    [
        ("Frontend Engineer", 2),
        ("Front-End Developer II", 2),
        ("Software Engineer II, Frontend", 2),
        ("SDE 2 - UI", 2),
        ("Design Engineer", 2),
        ("Senior Design Systems Engineer", 2),
        ("React Developer", 1),
        ("Software Engineer (TypeScript)", 1),
        ("Senior DevRel Engineer", 0),
        ("People Systems Engineer", 0),
        ("Systems Engineer", 0),
        ("Backend Engineer", 0),
        ("Software Engineer", 0),
        ("UI Tester", 0),
        ("UI/UX Designer", 0),
        ("Analog/Mixed-Signal IC Design Engineer", 0),
        ("Sales and Marketing Executive", 0),
        ("Git Administrator", 0),  # Git is general: naming it does not make a role the person's kind
    ],
)
def test_the_kind_of_role_is_read_from_distinctive_title_words_and_strongest_skills(
    title: str, strength: int
) -> None:
    assert targets().strength(title) == strength


def test_a_word_of_another_field_counts_when_the_persons_own_titles_use_it() -> None:
    profile = profile_of("frontend")
    data = profile.to_dict()
    data["person"]["currentRole"] = "Product Designer"
    designer = type(profile).from_dict(data)
    t = Targets(build_brief(designer, BENGALURU, scouts=3), PersonIndex(designer, VOCAB), VOCAB)
    assert t.strength("UI/UX Designer") > 0


@pytest.mark.parametrize(
    ("title", "asked", "years", "kept"),
    [
        ("Frontend Engineer", YearsAsked(1, 3), 2.4, True),
        ("Frontend Engineer", YearsAsked(4, None), 2.4, True),  # a step up, still shown as one
        ("Frontend Engineer", YearsAsked(5, 8), 2.4, False),
        ("Frontend Engineer", YearsAsked(0, 1), 2.4, False),  # a first job, for someone two years in
        ("Senior Frontend Engineer", None, 2.4, True),
        ("Senior Frontend Engineer", None, 1.5, False),
        ("Staff Frontend Engineer", None, 2.4, False),
        ("Lead UI Engineer", None, 7, True),
        ("Frontend Engineer Intern", None, 2.4, False),
        ("Frontend Engineer Intern", None, 0.5, True),
        ("Principal Engineer", YearsAsked(12, None), None, True),  # unknown years keep everything
    ],
)
def test_the_level_gate(title: str, asked: YearsAsked | None, years: float | None, kept: bool) -> None:
    assert at_level(title, asked, years) is kept


def test_a_role_is_in_place_in_the_city_or_remote_in_india() -> None:
    assert in_place(listing(city="Bengaluru"), "Bengaluru")
    assert not in_place(listing(city="Pune"), "Bengaluru")
    assert in_place(listing(city=None, remote_india=True), "Bengaluru")
    assert in_place(listing(city=None, remote_india=True), None) and not in_place(listing(city="Pune"), None)


def _posting(i: int, title: str, employer: str = "kestrel", **kw: object) -> Posting:
    return Posting(listing(f"{i:032x}", title=title, employer_id=employer, **kw), "Kestrel Pay", "tayyari")  # type: ignore[arg-type]


def test_the_funnel_counts_every_stage_and_texts_come_from_the_store_when_a_listing_has_none() -> None:
    profile = profile_of("frontend")
    brief = build_brief(profile, BENGALURU, scouts=3)
    person = PersonIndex(profile, VOCAB)
    pool = [
        _posting(1, "Frontend Engineer"),
        _posting(2, "Frontend Engineer", city="Pune"),
        _posting(3, "Backend Engineer"),
        _posting(4, "Staff Frontend Engineer"),
        _posting(5, "React Developer", employer="northwind"),
        _posting(6, "Frontend Engineer", employer="quill"),  # no text anywhere: left out
    ]
    selection = select(brief, Targets(brief, person, VOCAB), pool)
    assert selection.total == 6 and len(selection.placed) == 5 and len(selection.kinds) == 4
    texts = {f"{i:032x}": REACT_TEXT for i in (1, 4, 5)}
    result = narrow(brief, person, VOCAB, selection, texts, max_judged=10, per_employer=3)
    assert [step.count for step in result.funnel] == [6, 5, 4, 2, 2]
    assert [c.posting.listing.title for c in result.shortlist] == ["Frontend Engineer", "React Developer"]
    first = result.shortlist[0]
    assert first.text == REACT_TEXT and first.asked == YearsAsked(2, None) and first.fit.matched >= 3


def test_one_employer_cannot_fill_the_shortlist() -> None:
    profile = profile_of("frontend")
    brief = build_brief(profile, BENGALURU, scouts=3)
    person = PersonIndex(profile, VOCAB)
    pool = [Posting(listing(f"{i:032x}", text=REACT_TEXT), "Big", "web", "q", REACT_TEXT) for i in range(8)]
    pool.append(
        Posting(listing("f" * 32, employer_id="other", text=REACT_TEXT), "Other", "web", "q", REACT_TEXT)
    )
    selection = select(brief, Targets(brief, person, VOCAB), pool)
    result = narrow(brief, person, VOCAB, selection, {}, max_judged=5, per_employer=3)
    employers = [c.posting.listing.employer_id for c in result.shortlist]
    assert employers.count("kestrel") == 3 and "other" in employers and len(result.shortlist) == 4
