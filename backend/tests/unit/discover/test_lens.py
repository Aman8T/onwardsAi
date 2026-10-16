"""The lenses: how far, how wide, and in what order."""

from __future__ import annotations

from tayyari.discover.groups import arrange, role_groups, static_claims
from tayyari.discover.lens import (
    BREADTH_LABELS,
    BREADTH_ORDER,
    REACH_LABELS,
    REACH_ORDER,
    is_visible,
    km_text,
    near_pool,
    order_candidates,
    stops,
    visible,
)
from tayyari.discover.models import Candidate
from tayyari.discover.person import PersonIndex
from tayyari.discover.vocab import vocabulary
from tests.fakes.discover import profile_of
from tests.unit.discover.support import cand

PERSON = PersonIndex(profile_of("frontend"), vocabulary())


def arranged(pool: list[Candidate], person: PersonIndex = PERSON):  # type: ignore[no-untyped-def]
    roles = role_groups(person)
    return arrange(pool, person, {c.row.id: static_claims(c, person, roles) for c in pool})


def ids(items: list[Candidate]) -> list[str]:
    return [c.row.id for c in items]


def test_the_stops_and_their_labels_are_the_contract_values() -> None:
    assert REACH_ORDER == ("3", "5", "10", "25", "city")
    assert [REACH_LABELS[r] for r in REACH_ORDER] == ["3 km", "5 km", "10 km", "25 km", "Whole city"]
    assert BREADTH_ORDER == ("role", "next", "stretch")
    assert [BREADTH_LABELS[b] for b in BREADTH_ORDER] == ["Your role", "+ next steps", "+ stretch"]
    assert km_text("city") == "the whole city" and km_text("25") == "25 km"


def test_a_reach_keeps_the_listings_within_that_many_straight_line_kilometres() -> None:
    city = [
        cand("a", "A", km=0.0),
        cand("b", "B", km=4.9),
        cand("c", "C", km=5.0),
        cand("d", "D", km=12.0),
        cand("e", "E"),
    ]
    assert ids(near_pool(city, "3")) == ["a"]
    assert ids(near_pool(city, "5")) == ["a", "b", "c"]
    assert ids(near_pool(city, "10")) == ["a", "b", "c"]
    assert ids(near_pool(city, "25")) == ["a", "b", "c", "d"]
    assert ids(near_pool(city, "city")) == [
        "a",
        "b",
        "c",
        "d",
        "e",
    ]  # the whole city includes a listing with no distance


def test_without_a_locality_no_distance_stop_counts_anything_and_the_whole_city_still_does() -> None:
    city = [cand("a", "A"), cand("b", "B")]
    assert [len(near_pool(city, r)) for r in REACH_ORDER] == [0, 0, 0, 0, 2]


def test_stops_carry_the_counts_the_person_would_see() -> None:
    made = stops(REACH_ORDER, REACH_LABELS, lambda r: {"3": 1, "city": 9}.get(r, 0))
    assert next((s.value, s.label, s.count) for s in made) == ("3", "3 km", 1)
    assert made[-1].count == 9 and len(made) == 5


def test_how_wide_role_next_and_stretch() -> None:
    pool = [
        cand("mine", "Senior Frontend Engineer", word="looks_close"),
        cand("far", "Senior Frontend Engineer", word="stretch"),  # the years are too many: a stretch
        cand("drift", "Warehouse Supervisor", word="worth_a_look"),  # fits no group but looks close enough
        cand("blind", "Warehouse Supervisor", word="cant_tell"),  # fits no group and cannot be told
        cand("blind-role", "Frontend Engineer II", word="cant_tell"),  # cannot be told but fits a group
    ]
    arrangement = arranged(pool)
    seen = {b: ids(visible(pool, arrangement, b, has_current_role=True)) for b in BREADTH_ORDER}
    assert seen["role"] == ["mine", "blind-role"]
    assert seen["next"] == ["mine", "drift", "blind-role"]
    assert seen["stretch"] == ["mine", "far", "drift", "blind", "blind-role"]
    assert set(seen["role"]) <= set(seen["next"]) <= set(seen["stretch"])


def test_without_a_current_role_your_role_means_one_of_your_next_roles() -> None:
    pool = [cand("a", "Senior Frontend Engineer"), cand("b", "Warehouse Supervisor")]
    arrangement = arranged(pool)
    assert ids(visible(pool, arrangement, "role", has_current_role=False)) == ["a"]
    assert is_visible(pool[1], arrangement, "next", has_current_role=False)


def test_ranked_order_is_the_word_then_the_group_then_distance_then_freshness_then_title() -> None:
    pool = [
        cand("t1", "Zeta Engineer", word="worth_a_look", km=1.0, age_days=1),
        cand("t2", "Senior Frontend Engineer", word="worth_a_look", km=9.0, age_days=1),  # a next-role group
        cand(
            "t3", "Senior Frontend Engineer", word="looks_close", km=30.0, age_days=50
        ),  # a better word wins
        cand("t4", "Senior Frontend Engineer", word="worth_a_look", km=2.0, age_days=3),
        cand("t5", "Senior Frontend Engineer", word="worth_a_look", km=2.0, age_days=1),  # newer than t4
        cand(
            "t6", "Senior Frontend Engineer", word="worth_a_look", age_days=0
        ),  # no distance: after the known ones
        cand(
            "t7", "Senior Frontend Developer", word="worth_a_look", km=2.0, age_days=1
        ),  # same distance and age as t5: the title breaks the tie
        cand("t8", "Warehouse Staff", word="cant_tell", km=0.5, age_days=0),
    ]
    arrangement = arranged(pool)
    assert ids(order_candidates(pool, arrangement, "ranked")) == [
        "t3",
        "t7",
        "t5",
        "t4",
        "t2",
        "t6",
        "t1",
        "t8",
    ]


def test_posted_order_is_newest_first_with_no_ranking() -> None:
    pool = [
        cand("old", "Senior Frontend Engineer", word="looks_close", age_days=40),
        cand("new", "Warehouse Staff", word="cant_tell", age_days=1),
        cand("unknown", "Senior Frontend Engineer", word="looks_close"),
        cand("mid", "Warehouse Staff", word="stretch", age_days=10),
    ]
    arrangement = arranged(pool)
    assert ids(order_candidates(pool, arrangement, "posted")) == ["new", "mid", "old", "unknown"]
