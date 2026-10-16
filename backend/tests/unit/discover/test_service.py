"""The Discover screen end to end over the fake store and the hand-written boards: area, lenses, lanes, groups, order,
suggestion, companies, coverage, states, one listing, and the background refresh that never blocks a request."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
import structlog.testing

from tayyari.contract.discover import DiscoverResponse, ListingCard
from tayyari.contract.models import Profile
from tayyari.core.errors import BadRequest
from tayyari.discover.errors import DiscoverDisabled
from tayyari.discover.service import (
    FIRST_READ_NOTE,
    PICK_CITY_NOTE,
    THIN_NOTE,
    UNREADABLE_NOTE,
    DiscoverService,
)
from tayyari.storage.ports import DiscoverListing, DiscoverRun
from tests.fakes.clock import FakeClock
from tests.fakes.discover import FakeBoards, FakeDiscoverRepo
from tests.unit.discover.conftest import query

PERCENT = re.compile(r"%|percent", re.IGNORECASE)


def titles(response: DiscoverResponse) -> list[str]:
    return [card.title for card in response.listings]


def card_of(response: DiscoverResponse, title: str, employer: str | None = None) -> ListingCard:
    (found,) = [
        c for c in response.listings if c.title == title and (employer is None or c.employerId == employer)
    ]
    return found


# ───────────────────────── the area ─────────────────────────


def test_the_city_comes_from_the_resume_until_the_person_picks_another(
    service: DiscoverService, frontend: Profile
) -> None:
    from_resume = service.resolve_area(frontend, city=None, locality=None)
    assert (from_resume.city_from, from_resume.city.name if from_resume.city else None) == (
        "resume",
        "Bengaluru",
    )
    assert from_resume.resume_text == "Bengaluru, India" and from_resume.locality is None
    same = service.resolve_area(frontend, city="bangalore", locality="hsr layout")
    assert (same.city_from, same.locality.name if same.locality else None) == ("resume", "HSR Layout")
    other = service.resolve_area(frontend, city="Pune", locality=None, other_city="Mumbai")
    assert other.city_from == "you" and other.other_city is not None and other.other_city.name == "Mumbai"
    assert other.model().cityFrom == "you" and other.model().resumeText == "Bengaluru, India"


def test_a_resume_without_a_city_leaves_the_city_to_the_person(
    service: DiscoverService, frontend: Profile
) -> None:
    data = frontend.to_dict()
    data["person"]["location"] = "Somewhere near the sea"
    nowhere = Profile.from_dict(data)
    none = service.resolve_area(nowhere, city=None, locality=None)
    assert (none.city, none.city_from, none.resume_text) == (None, "none", "Somewhere near the sea")
    picked = service.resolve_area(nowhere, city="Chennai", locality="Adyar")
    assert picked.city_from == "you" and picked.city is not None and picked.city.name == "Chennai"
    del data["person"]["location"]
    assert service.resolve_area(Profile.from_dict(data), city=None, locality=None).resume_text is None


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"city": "Atlantis"}, "That city is not one we know"),
        ({"city": "Pune", "locality": "HSR Layout"}, "That locality is not in Pune"),
        ({"locality": "Banjara Hills"}, "That locality is not in Bengaluru"),
        ({"otherCity": "Gotham"}, "That city is not one we know"),
    ],
)
async def test_names_that_are_not_ours_are_refused(
    loaded: DiscoverService, frontend: Profile, fields: dict[str, str], message: str
) -> None:
    with pytest.raises(BadRequest, match=message):
        await loaded.discover(frontend, query(frontend, **fields))


def test_a_locality_needs_a_city(service: DiscoverService, frontend: Profile) -> None:
    data = frontend.to_dict()
    del data["person"]["location"]
    with pytest.raises(BadRequest, match="Pick a city before a locality"):
        service.resolve_area(Profile.from_dict(data), city=None, locality="HSR Layout")


def test_the_places_are_listed_for_the_pickers(service: DiscoverService) -> None:
    cities = service.cities().cities
    assert [c.name for c in cities][:3] == ["Bengaluru", "Hyderabad", "Pune"] and len(cities) == 14
    assert "HSR Layout" in cities[0].localities and len(cities[0].localities) == 18


# ───────────────────────── states before there is anything to show ─────────────────────────


async def test_before_the_first_refresh_has_finished_the_answer_is_an_empty_list_and_a_plain_note(
    service: DiscoverService, frontend: Profile
) -> None:
    response = await service.discover(frontend, query(frontend))
    assert response.coverage.note == FIRST_READ_NOTE
    assert FIRST_READ_NOTE == "Reading employers' job boards for the first time. This takes about a minute."
    assert (response.total, response.listings, response.groups, response.companies) == (0, [], [], [])
    assert [s.count for s in response.reachStops + response.breadthStops] == [0] * 8
    assert response.laneCounts.model_dump() == {"near": 0, "remote": 0, "city": 0}
    assert response.refreshedAt is None and (response.coverage.employers, response.coverage.listings) == (
        0,
        0,
    )
    assert response.area.city == "Bengaluru"
    await service.aclose()


async def test_when_every_refresh_failed_the_note_says_the_boards_could_not_be_read(
    make_service: Callable[..., DiscoverService], repo: FakeDiscoverRepo, clock: FakeClock, frontend: Profile
) -> None:
    service = make_service()
    failing = FakeBoards()  # serves nothing: every board is a 404
    refresher_service = DiscoverService(
        settings=service._settings,
        repo=repo,
        clock=clock,
        registry=service.registry,
        transport=failing.transport,
    )
    report = await refresher_service.refresh()
    assert report is not None and report.status == "failed"
    clock.advance(11 * 60)  # past the pause between attempts
    response = await service.discover(frontend, query(frontend))
    assert response.coverage.note == UNREADABLE_NOTE and response.listings == []
    await service.aclose()


# ───────────────────────── the screen ─────────────────────────


async def test_the_default_screen_for_a_frontend_engineer_in_bengaluru(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    assert response.area.model_dump(exclude_none=True) == {
        "city": "Bengaluru",
        "cityFrom": "resume",
        "resumeText": "Bengaluru, India",
        "locality": "HSR Layout",
    }
    assert (response.lane, response.reach, response.breadth, response.order) == (
        "near",
        "city",
        "next",
        "ranked",
    )
    assert response.total == len(response.listings) == 5
    assert [g.id for g in response.groups] == [
        "next_role:senior-frontend-engineer",
        "next_role:full-stack-engineer",
    ]
    assert sum(g.count for g in response.groups) == response.total
    assert response.refreshedAt == "2026-10-16T12:00:00+00:00"
    assert (response.coverage.employers, response.coverage.listings, response.coverage.note) == (8, 17, None)


async def test_every_listing_has_one_home_and_a_group_that_exists(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout", breadth="stretch"))
    group_ids = {g.id for g in response.groups}
    placed = [i for g in response.groups for i in g.listingIds]
    assert sorted(placed) == sorted(c.id for c in response.listings)  # never counted twice
    for card in response.listings:
        assert (
            card.groupId in group_ids
            and set(card.alsoFits) <= group_ids
            and card.groupId not in card.alsoFits
        )
    for group in response.groups:
        assert group.count == len(group.listingIds) > 0
        assert group.reason.text


async def test_distances_are_straight_lines_and_only_between_two_localities(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    here = card_of(response, "Frontend Engineer II")
    assert (here.place.label, here.place.km) == ("HSR Layout, Bengaluru", 0.0)
    kora = card_of(response, "Senior Frontend Engineer", "kestrel")
    assert (
        kora.place.label == "Koramangala, Bengaluru" and kora.place.km is not None and 3 < kora.place.km < 4
    )
    white = card_of(response, "UI Engineer")
    assert white.place.km is not None and 12 < white.place.km < 14
    city_only = card_of(response, "Senior Frontend Engineer, Mobile web")
    assert (city_only.place.precision, city_only.place.km) == (
        "city",
        None,
    )  # the listing is known to a city only
    for card in response.listings:
        assert card.place.km is None or card.place.precision == "locality"


async def test_without_a_locality_there_are_no_distances_and_the_km_stops_count_nothing(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend))
    assert response.reach == "city" and response.total == 5
    assert [s.count for s in response.reachStops] == [0, 0, 0, 0, 5]
    assert all(card.place.km is None for card in response.listings)
    assert not any(signal.startswith("Distance") for signal in response.signals)
    assert response.suggestion is None


async def test_reach_keeps_the_listings_within_that_many_kilometres(
    loaded: DiscoverService, frontend: Profile
) -> None:
    labels = {
        s.value: s.label
        for s in (await loaded.discover(frontend, query(frontend, locality="HSR Layout"))).reachStops
    }
    assert labels == {"3": "3 km", "5": "5 km", "10": "10 km", "25": "25 km", "city": "Whole city"}
    counts: dict[str, int] = {}
    for reach in ("3", "5", "10", "25", "city"):
        response = await loaded.discover(frontend, query(frontend, locality="HSR Layout", reach=reach))
        counts[reach] = response.total
        assert all(
            (c.place.km is not None and (reach == "city" or c.place.km <= float(reach))) or reach == "city"
            for c in response.listings
        )
        assert [s.count for s in response.reachStops][-1] == 5  # the stops do not depend on the chosen reach
    assert counts == {"3": 1, "5": 3, "10": 3, "25": 4, "city": 5}


async def test_how_wide_is_role_then_next_steps_then_stretch(
    loaded: DiscoverService, frontend: Profile
) -> None:
    by_breadth = {
        b: await loaded.discover(frontend, query(frontend, locality="HSR Layout", breadth=b))
        for b in ("role", "next", "stretch")
    }
    assert [s.count for s in by_breadth["next"].breadthStops] == [3, 5, 7]
    assert [by_breadth[b].total for b in ("role", "next", "stretch")] == [3, 5, 7]
    assert "Staff Frontend Engineer" in titles(by_breadth["stretch"])
    assert "Staff Frontend Engineer" not in titles(by_breadth["next"])
    assert card_of(by_breadth["stretch"], "Staff Frontend Engineer").fit.word == "stretch"
    assert (
        set(titles(by_breadth["role"]))
        <= set(titles(by_breadth["next"]))
        <= set(titles(by_breadth["stretch"]))
    )
    assert [s.label for s in by_breadth["next"].breadthStops] == ["Your role", "+ next steps", "+ stretch"]


async def test_the_lanes_and_their_counts(loaded: DiscoverService, frontend: Profile) -> None:
    near = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    assert near.laneCounts.model_dump() == {"near": 5, "remote": 3, "city": 0}
    remote = await loaded.discover(frontend, query(frontend, lane="remote"))
    assert remote.lane == "remote" and remote.total == 3
    assert {c.place.label for c in remote.listings} == {"Remote, India"}
    assert {c.workMode for c in remote.listings} == {"remote"}
    assert all(
        c.place.city is None and c.place.km is None and c.place.precision == "country"
        for c in remote.listings
    )
    assert (
        remote.laneCounts.model_dump() == near.laneCounts.model_dump()
    )  # the tabs show the same counts anywhere


async def test_another_city_shows_only_the_roles_that_look_close(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(
        frontend, query(frontend, lane="city", otherCity="Pune", breadth="stretch")
    )
    assert response.lane == "city" and titles(response) == ["Frontend Engineer"]
    assert response.listings[0].employerId == "parcelpoint" and response.listings[0].fit.word == "looks_close"
    assert response.laneCounts.city == 1
    nothing = await loaded.discover(frontend, query(frontend, lane="city"))  # no city picked yet
    assert nothing.total == 0 and nothing.listings == []


async def test_a_person_in_another_city_sees_that_city(loaded: DiscoverService, frontend: Profile) -> None:
    response = await loaded.discover(frontend, query(frontend, city="Pune", breadth="stretch"))
    assert response.area.cityFrom == "you" and titles(response) == [
        "Frontend Engineer",
        "Software Engineer Intern",
    ]
    assert {c.place.label for c in response.listings} == {"Pune"}


async def test_the_listings_are_in_ranked_order_and_the_groups_list_them_in_that_order(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    words = [c.fit.word for c in response.listings]
    order = {"looks_close": 0, "worth_a_look": 1, "stretch": 2, "cant_tell": 3}
    assert words == sorted(words, key=order.__getitem__)
    ids = [c.id for c in response.listings]
    for group in response.groups:
        assert group.listingIds == [i for i in ids if i in group.listingIds]


async def test_posted_order_is_newest_first_and_says_so(loaded: DiscoverService, frontend: Profile) -> None:
    ranked = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    posted = await loaded.discover(frontend, query(frontend, locality="HSR Layout", order="posted"))
    assert posted.order == "posted" and set(titles(posted)) == set(titles(ranked))
    freshness = [c.freshness for c in posted.listings]
    assert freshness[0] == "Posted yesterday" and freshness[-1] == "On the employer's board now"
    assert "No ranking is applied" in posted.signals and posted.signals != ranked.signals
    assert [g.id for g in posted.groups] == [g.id for g in ranked.groups]


async def test_why_this_order_says_what_ranked_uses_with_the_persons_own_values(
    loaded: DiscoverService, frontend: Profile
) -> None:
    ranked = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    assert ranked.signals == [
        "Your next roles: Design engineer, Senior frontend engineer, Full-stack engineer",
        "Skills the postings name that your evidence shows",
        "Years asked against your 2.4",
        "Distance from HSR Layout, straight line",
        "How recently the employer listed it",
        "Only employers' own job boards",
    ]
    no_area = await loaded.discover(frontend, query(frontend))
    assert "Distance from HSR Layout, straight line" not in no_area.signals


# ───────────────────────── the card ─────────────────────────


async def test_a_card_is_the_employers_own_listing_with_a_clean_apply_link(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout", breadth="stretch"))
    senior = card_of(response, "Senior Frontend Engineer", "kestrel")
    assert senior.applyUrl == "https://kestrelpay.in/careers/jobs/1001?gh_jid=1001&ref=friend"
    assert (senior.applyHost, senior.confidence, senior.source) == ("kestrelpay.in", "employer", "greenhouse")
    assert senior.sourceLabel == "Employer's own listing, checked today"
    assert (senior.freshness, senior.postedAt) == ("Posted 3 days ago", "2026-10-13T12:00:00+00:00")
    assert senior.employer == "Kestrel Pay" and senior.workMode == "unknown" and senior.salary is None
    for card in response.listings:
        assert not re.search(r"utm_|gclid|fbclid|mc_cid|mc_eid|lever-source", card.applyUrl)
        assert card.applyUrl.startswith("https://") and card.applyHost and card.applyHost in card.applyUrl
        assert card.checkedAt and card.firstSeenAt and card.fit.reasons is not None


async def test_a_salary_appears_only_when_the_employer_states_it(
    loaded: DiscoverService, frontend: Profile
) -> None:
    near = await loaded.discover(frontend, query(frontend, locality="HSR Layout", breadth="stretch"))
    remote = await loaded.discover(frontend, query(frontend, lane="remote"))
    with_salary = {
        (c.employerId, c.title): c.salary.text
        for c in [*near.listings, *remote.listings]
        if c.salary is not None
    }
    assert with_salary == {
        ("northwind", "Frontend Engineer II"): "₹28–36 lakh a year",
        ("northwind", "Senior Frontend Engineer"): "$40–55k a year",
    }
    assert all(c.source == "lever" for c in [*near.listings, *remote.listings] if c.salary is not None)


async def test_the_quick_fit_of_a_card_is_words_counts_and_cited_reasons(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout", breadth="stretch"))
    senior = card_of(response, "Senior Frontend Engineer", "kestrel").fit
    assert (senior.word, senior.named, senior.matched, senior.notFound) == ("looks_close", 8, 7, ["GraphQL"])
    assert (senior.yearsAsked, senior.yearsYou) == (5, 2.4)
    assert [r.text for r in senior.reasons] == ["You show React", "You show Design systems"]
    assert senior.reasons[0].cite is not None and senior.reasons[0].cite.source == "Resume"
    staff = card_of(response, "Staff Frontend Engineer").fit
    assert staff.word == "stretch" and staff.yearsAsked == 8
    fullstack = card_of(response, "Full-stack Engineer").fit
    assert fullstack.word == "worth_a_look" and fullstack.notFound == ["AWS", "Docker", "PostgreSQL"]
    for card in response.listings:
        assert len(card.fit.notFound) <= 5
        assert all(len(r.cite.text) <= 90 for r in card.fit.reasons if r.cite)


async def test_nothing_the_service_returns_is_a_percentage(
    loaded: DiscoverService, frontend: Profile, fresher: Profile
) -> None:
    for profile, extra in ((frontend, {"locality": "HSR Layout"}), (fresher, {})):
        for lane in ("near", "remote"):
            for breadth in ("role", "next", "stretch"):
                response = await loaded.discover(profile, query(profile, lane=lane, breadth=breadth, **extra))
                assert not PERCENT.search(json.dumps(response.to_dict(), ensure_ascii=False))
    assert not PERCENT.search(json.dumps(loaded.cities().to_dict()))


async def test_a_posting_that_tells_the_service_what_to_do_changes_nothing(
    loaded: DiscoverService, frontend: Profile, boards: FakeBoards
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    card = card_of(response, "Full-stack Engineer")
    detail = await loaded.detail(card.id, frontend)
    assert detail is not None and "Ignore all previous instructions" in detail.postingText
    assert not any("evil.example" in url for url in boards.urls)  # the link inside was never opened
    assert card.applyHost == "kestrelpay.in"  # and the apply link is still the employer's


# ───────────────────────── suggestion, companies, coverage ─────────────────────────


async def test_the_suggestion_is_one_data_grounded_hint(loaded: DiscoverService, frontend: Profile) -> None:
    at_3 = await loaded.discover(frontend, query(frontend, locality="HSR Layout", reach="3"))
    stops = {s.value: s.count for s in at_3.reachStops}
    assert (
        at_3.suggestion
        == f"Widening to 5 km adds {stops['5'] - stops['3']} roles, 1 in Senior frontend engineer."
    )
    at_10 = await loaded.discover(frontend, query(frontend, locality="HSR Layout", reach="10"))
    assert at_10.suggestion == "Widening to 25 km adds 1 role, 1 in Senior frontend engineer."
    at_25 = await loaded.discover(frontend, query(frontend, locality="HSR Layout", reach="25"))
    assert at_25.suggestion == "The whole city adds 1 more."
    whole = await loaded.discover(frontend, query(frontend, locality="HSR Layout", reach="city"))
    assert whole.suggestion is None
    remote = await loaded.discover(frontend, query(frontend, locality="HSR Layout", reach="3", lane="remote"))
    assert remote.suggestion is None


async def test_the_companies_lane_names_employers_hiring_for_you_elsewhere_with_nothing_near(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    assert [(c.employerId, c.name, c.city) for c in response.companies] == [
        ("parcelpoint", "Parcelpoint", "Pune")
    ]
    lead = response.companies[0]
    assert lead.reason == "Hiring 1 role like yours in Pune, nothing near you now."
    assert lead.careersUrl == "https://parcelpoint.in/careers"
    shown = {c.employerId for c in response.listings}
    assert not shown & {
        c.employerId for c in response.companies
    }  # never an employer that already has a role in view


async def test_an_employer_with_a_role_in_view_or_within_reach_is_not_a_lead(
    loaded: DiscoverService, frontend: Profile
) -> None:
    pune = await loaded.discover(frontend, query(frontend, city="Pune", breadth="stretch"))
    assert {c.employerId for c in pune.listings} == {"parcelpoint", "kestrel"}
    # Parcelpoint and Kestrel have a role in view in Pune; the others are hiring in Bengaluru.
    assert [(c.employerId, c.city) for c in pune.companies] == [
        ("indigo", "Bengaluru"),
        ("northwind", "Bengaluru"),
        ("quill", "Bengaluru"),
    ]
    near = await loaded.discover(frontend, query(frontend, locality="HSR Layout", reach="3"))
    kestrel = next(c for c in near.companies if c.employerId == "kestrel")
    assert (
        kestrel.reason == "Hiring 2 roles like yours in Bengaluru, nothing near you now."
    )  # 3.5 km away: beyond reach
    assert "northwind" not in {c.employerId for c in near.companies}  # its role is in view
    assert len(near.companies) <= 3


async def test_coverage_is_honest_about_a_thin_city_not_a_small_radius(
    loaded: DiscoverService, frontend: Profile
) -> None:
    # Few employers state a neighbourhood, so a 3 km radius is nearly empty: that is not "few roles like yours", and the
    # suggestion says what widening adds. The thin note is judged on the whole city (the Coimbatore test shows it).
    small = await loaded.discover(frontend, query(frontend, locality="HSR Layout", reach="3"))
    assert small.coverage.note is None
    assert (
        THIN_NOTE.startswith("Few roles like yours are shared with us yet.")
        and "Paste it and we'll match it." in THIN_NOTE
    )
    assert "LinkedIn" not in THIN_NOTE
    full = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    assert full.coverage.note is None


async def test_without_a_city_the_note_asks_for_one(loaded: DiscoverService, frontend: Profile) -> None:
    data = frontend.to_dict()
    del data["person"]["location"]
    response = await loaded.discover(Profile.from_dict(data), query(frontend))
    assert response.area.city is None and response.area.cityFrom == "none"
    assert response.coverage.note == PICK_CITY_NOTE and response.total == 0 and response.companies == []


async def test_a_person_in_coimbatore_with_a_thin_but_real_profile(
    loaded: DiscoverService, fresher: Profile
) -> None:
    response = await loaded.discover(fresher, query(fresher, locality="Peelamedu"))
    assert response.area.city == "Coimbatore" and response.total == 3
    assert response.coverage.note == THIN_NOTE  # three roles near: few
    assert {c.title for c in response.listings} == {
        "Accounts Trainee",
        "Junior Accountant",
        "Audit Assistant",
    }
    trainee = card_of(response, "Accounts Trainee")
    assert (trainee.fit.word, trainee.fit.named, trainee.fit.matched, trainee.fit.yearsAsked) == (
        "looks_close",
        3,
        3,
        0,
    )
    assert trainee.place.label == "Peelamedu, Coimbatore"  # Nilgiri lists one Coimbatore office, in Peelamedu
    junior = card_of(response, "Junior Accountant").fit
    # three of five asks met: looks close since the 2026-10-16 calibration (asks, not raw terms)
    assert (junior.word, junior.notFound) == ("looks_close", ["Accounts payable", "Financial reporting"])
    audit = card_of(response, "Audit Assistant")
    assert audit.place.precision == "city" and audit.fit.notFound == ["Audit"]  # Kovai lists no locality
    assert {g.id for g in response.groups} == {
        "next_role:junior-accountant",
        "next_role:audit-assistant",
        "current",
    }
    assert response.signals[2] == "Years asked against your 0"


async def test_first_steps_are_the_entry_roles_for_someone_who_has_not_worked_yet(
    loaded: DiscoverService, fresher: Profile
) -> None:
    data = fresher.to_dict()
    data["person"]["currentRole"] = "Student"
    data["roles"] = [{**data["roles"][0], "title": "Company Secretary"}]
    response = await loaded.discover(Profile.from_dict(data), query(fresher, locality="Peelamedu"))
    first = [g for g in response.groups if g.kind == "first_steps"]
    assert len(first) == 1 and first[0].title == "First steps"
    assert {card_of(response, "Accounts Trainee").groupId, card_of(response, "Audit Assistant").groupId} == {
        "first_steps"
    }
    assert card_of(response, "Junior Accountant").groupId == "first_steps"  # "Junior" is an entry-level title
    assert first[0].reason.text == "Entry roles that ask for what your evidence shows."


async def test_a_profile_with_under_three_skills_cannot_be_told_but_is_still_shown_the_roles(
    loaded: DiscoverService, frontend: Profile
) -> None:
    data = frontend.to_dict()
    data["skills"] = data["skills"][:2]
    response = await loaded.discover(Profile.from_dict(data), query(frontend, breadth="stretch"))
    assert response.total > 0 and {c.fit.word for c in response.listings} == {"cant_tell"}


# ───────────────────────── one listing ─────────────────────────


async def test_a_listing_comes_with_its_posting_text_for_the_match(
    loaded: DiscoverService, frontend: Profile
) -> None:
    response = await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    card = card_of(response, "Frontend Engineer II")
    detail = await loaded.detail(card.id, frontend, city="Bengaluru", locality="HSR Layout")
    assert detail is not None
    assert detail.card.id == card.id and detail.card.place.km == 0.0 and detail.card.groupId == card.groupId
    assert detail.postingText.startswith("Frontend Engineer II\nNorthwind Analytics · HSR Layout, Bengaluru")
    assert "3+ years of experience with React and Jest" in detail.postingText
    without = await loaded.detail(card.id, frontend)
    assert without is not None and without.card.place.km is None


async def test_a_closed_or_unknown_listing_has_no_detail(
    loaded: DiscoverService, frontend: Profile, repo: FakeDiscoverRepo, clock: FakeClock
) -> None:
    assert await loaded.detail("0" * 32, frontend) is None
    victim = next(i for i in repo.listings.values() if i.title == "Audit Assistant")
    await repo.close_missing(victim.employer_id, [], now=clock.now())
    assert await loaded.detail(victim.id, frontend) is None


# ───────────────────────── switched off, and the area is not kept ─────────────────────────


async def test_when_discover_is_switched_off_nothing_is_read_or_answered(
    make_service: Callable[..., DiscoverService], frontend: Profile, boards: FakeBoards
) -> None:
    off = make_service(enabled=False)
    with pytest.raises(DiscoverDisabled) as raised:
        await off.discover(frontend, query(frontend))
    assert raised.value.status == 503 and raised.value.to_body() == {"error": "discover_disabled"}
    with pytest.raises(DiscoverDisabled):
        await off.detail("0" * 32, frontend)
    assert await off.maybe_refresh() is False and boards.requests == []


async def test_the_persons_area_is_used_for_one_answer_and_kept_nowhere(
    loaded: DiscoverService, frontend: Profile, repo: FakeDiscoverRepo
) -> None:
    before = (dict(repo.listings), list(repo.runs))
    with structlog.testing.capture_logs() as logs:
        await loaded.discover(
            frontend, query(frontend, city="Bengaluru", locality="Koramangala", otherCity="Pune")
        )
        await loaded.discover(frontend, query(frontend, city="Mumbai", locality="Powai"))
    assert (dict(repo.listings), list(repo.runs)) == before  # nothing was written
    logged = json.dumps(logs, default=str)
    for name in ("Koramangala", "Powai", "Mumbai", "Riya", "Kapoor"):
        assert name not in logged
    assert not any(isinstance(v, str) and "Powai" in v for v in vars(loaded).values())
    fields = {f.name for f in dataclasses.fields(DiscoverListing)} | {
        f.name for f in dataclasses.fields(DiscoverRun)
    }
    assert not {f for f in fields if "area" in f or "person" in f or "profile" in f or "user" in f}


# ───────────────────────── the background refresh ─────────────────────────


async def test_the_first_request_starts_a_refresh_in_the_background_and_does_not_wait_for_it(
    make_service: Callable[..., DiscoverService], clock: FakeClock, repo: FakeDiscoverRepo, frontend: Profile
) -> None:
    release = asyncio.Event()
    asked: list[str] = []

    async def slow(request: Any) -> Any:
        import httpx2

        asked.append(str(request.url))
        await release.wait()
        return httpx2.Response(200, json={"jobs": []})

    import httpx2

    service = DiscoverService(
        settings=make_service()._settings,
        repo=repo,
        clock=clock,
        registry=make_service().registry,
        transport=httpx2.MockTransport(slow),
    )
    answer = await asyncio.wait_for(service.discover(frontend, query(frontend)), timeout=5)
    assert answer.coverage.note == FIRST_READ_NOTE and answer.listings == []
    await asyncio.sleep(0.05)
    assert service._task is not None and not service._task.done()  # still reading: the request did not wait
    again = await service.discover(frontend, query(frontend))
    assert again.coverage.note == FIRST_READ_NOTE
    release.set()
    await service.wait_for_refresh()
    assert (await repo.last_ok_run()) is not None
    assert len(asked) == 9  # one read per board, and the second request did not start another refresh
    await service.aclose()


async def test_a_request_finds_the_data_and_starts_nothing_while_it_is_fresh(
    service: DiscoverService, boards: FakeBoards, frontend: Profile, clock: FakeClock
) -> None:
    await service.discover(frontend, query(frontend))  # the first request starts a refresh
    await service.wait_for_refresh()
    seen = len(boards.requests)
    assert seen == 9
    clock.advance(5 * 3600)
    answer = await service.discover(frontend, query(frontend, locality="HSR Layout"))
    await service.wait_for_refresh()
    assert len(boards.requests) == seen and answer.total == 5  # five hours old: nothing started


async def test_a_request_finds_old_data_and_refreshes_in_the_background_while_answering_from_what_it_has(
    service: DiscoverService, boards: FakeBoards, frontend: Profile, clock: FakeClock
) -> None:
    await service.discover(frontend, query(frontend))
    await service.wait_for_refresh()
    clock.advance(7 * 3600)
    answer = await service.discover(frontend, query(frontend, locality="HSR Layout"))
    assert answer.total == 5  # answered from the stored listings
    assert answer.refreshedAt == "2026-10-16T12:00:00+00:00"
    await service.wait_for_refresh()
    assert len(boards.requests) == 18  # and a second refresh read every board again
    fresh = await service.discover(frontend, query(frontend, locality="HSR Layout"))
    assert fresh.refreshedAt == "2026-10-16T19:00:00+00:00"


async def test_a_failing_refresh_is_not_retried_by_every_request(
    make_service: Callable[..., DiscoverService], frontend: Profile, clock: FakeClock, repo: FakeDiscoverRepo
) -> None:
    down = FakeBoards()
    service = DiscoverService(
        settings=make_service()._settings,
        repo=repo,
        clock=clock,
        registry=make_service().registry,
        transport=down.transport,
    )
    await service.discover(frontend, query(frontend))
    await service.wait_for_refresh()
    first = len(down.requests)
    assert first == 9
    for _ in range(3):
        await service.discover(frontend, query(frontend))
        await service.wait_for_refresh()
    assert len(down.requests) == first  # the pause between attempts holds
    clock.advance(11 * 60)
    await service.discover(frontend, query(frontend))
    await service.wait_for_refresh()
    assert len(down.requests) == 2 * first
    await service.aclose()


async def test_shutdown_cancels_a_refresh_that_is_still_reading(
    make_service: Callable[..., DiscoverService], clock: FakeClock, repo: FakeDiscoverRepo, frontend: Profile
) -> None:
    import httpx2

    started = asyncio.Event()

    async def never(request: Any) -> Any:
        started.set()
        await asyncio.Event().wait()
        return httpx2.Response(200)

    service = DiscoverService(
        settings=make_service()._settings,
        repo=repo,
        clock=clock,
        registry=make_service().registry,
        transport=httpx2.MockTransport(never),
    )
    assert await service.maybe_refresh() is True
    await asyncio.wait_for(started.wait(), timeout=5)
    task = service._task
    assert task is not None
    await asyncio.wait_for(service.aclose(), timeout=5)
    assert task.cancelled() or task.done()


async def test_the_posting_texts_are_read_once_not_on_every_request(
    loaded: DiscoverService, frontend: Profile, repo: FakeDiscoverRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original = repo.texts

    async def counting(ids: Any) -> Any:
        calls.append(len(ids))
        return await original(ids)

    monkeypatch.setattr(repo, "texts", counting)
    await loaded.discover(frontend, query(frontend, locality="HSR Layout"))
    first = len(calls)
    assert first >= 1 and sum(calls) == 17
    await loaded.discover(frontend, query(frontend, locality="Koramangala", reach="5"))
    await loaded.discover(frontend, query(frontend, breadth="stretch"))
    assert len(calls) == first


async def test_a_changed_listing_is_read_again(
    loaded: DiscoverService, frontend: Profile, repo: FakeDiscoverRepo, boards: FakeBoards, clock: FakeClock
) -> None:
    first = await loaded.discover(frontend, query(frontend, locality="HSR Layout", breadth="stretch"))
    assert card_of(first, "Staff Frontend Engineer").fit.word == "stretch"
    changed = json.loads(json.dumps(boards._payloads["kestrelpay"]))
    for job in changed["jobs"]:
        if job["id"] == 1002:
            job["content"] = "&lt;p&gt;React, TypeScript, CSS and Jest. 2+ years of experience.&lt;/p&gt;"
    boards.serve("kestrelpay", changed)
    clock.advance(timedelta(hours=7).total_seconds())
    await loaded.refresh()
    second = await loaded.discover(frontend, query(frontend, locality="HSR Layout", breadth="stretch"))
    staff = card_of(second, "Staff Frontend Engineer").fit
    assert staff.word == "looks_close" and staff.yearsAsked == 2
