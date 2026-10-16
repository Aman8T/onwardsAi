"""Groups: why a listing fits, in a fixed order of precedence. The first group is the home; the rest are alsoFits."""

from __future__ import annotations

from tayyari.contract.models import Profile
from tayyari.discover.groups import arrange, group_reason, role_groups, static_claims
from tayyari.discover.models import Candidate
from tayyari.discover.person import PersonIndex
from tayyari.discover.vocab import vocabulary
from tests.fakes.discover import profile_of
from tests.unit.discover.support import cand

VOCAB = vocabulary()


def person_of(name: str = "frontend", **person: object) -> PersonIndex:
    profile = profile_of(name)
    if person:
        data = profile.to_dict()
        data["person"].update(person)
        profile = Profile.from_dict(data)
    return PersonIndex(profile, VOCAB)


def arranged(pool: list[Candidate], person: PersonIndex):  # type: ignore[no-untyped-def]
    roles = role_groups(person)
    static = {c.row.id: static_claims(c, person, roles) for c in pool}
    return arrange(pool, person, static)


def test_the_next_roles_are_the_top_three_by_alignment() -> None:
    person = person_of()
    assert [role.title for role in person.next_roles] == [
        "Design Engineer",
        "Senior Frontend Engineer",
        "Full-Stack Engineer",
    ]  # the fourth role (alignment 50) is left out
    assert [g.title for g in role_groups(person)] == [
        "Design engineer",
        "Senior frontend engineer",
        "Full-stack engineer",
    ]


def test_a_listing_close_to_a_next_role_lives_there_and_also_fits_the_current_work() -> None:
    person = person_of()
    senior = cand("a", "Senior Frontend Engineer")
    arrangement = arranged([senior], person)
    assert arrangement.home["a"].id == "next_role:senior-frontend-engineer"
    assert [g.id for g in arrangement.claims["a"]] == ["next_role:senior-frontend-engineer", "current"]


def test_a_listing_close_to_two_next_roles_goes_to_the_one_with_the_higher_alignment() -> None:
    person = person_of()
    design_systems = cand(
        "a", "Senior Design Frontend Engineer"
    )  # close to both Design Engineer and Senior FE
    claims = static_claims(design_systems, person, role_groups(person))
    assert [g.id for g in claims][:2] == ["next_role:design-engineer", "next_role:senior-frontend-engineer"]


def test_the_current_work_is_its_own_group() -> None:
    person = person_of(currentRole="Product Designer")
    arrangement = arranged([cand("a", "Product Designer"), cand("b", "Product Designer II")], person)
    assert {arrangement.home[i].id for i in ("a", "b")} == {"current"}
    assert arrangement.home["a"].title == "Roles like yours"


def test_listings_missing_the_same_skill_form_a_skill_bridge_when_there_are_two_or_more() -> None:
    person = person_of()
    pool = [
        cand("a", "Platform Engineer", matched=4, missing=("GraphQL",)),
        cand("b", "Backend Engineer", matched=5, missing=("GraphQL",)),
        cand("c", "Mobile Engineer", matched=3, missing=("GraphQL", "Kotlin")),
        cand("d", "Data Engineer", matched=4, missing=("Kafka",)),  # alone with Kafka: no group
        cand("e", "QA Engineer", matched=2, missing=("GraphQL",)),  # shows fewer than three
        cand("f", "Support Engineer", matched=4, missing=("A", "B", "C")),  # more than two missing
    ]
    arrangement = arranged(pool, person)
    bridge = arrangement.home["a"]
    assert (bridge.id, bridge.title) == ("skill_bridge:graphql", "One skill away: GraphQL")
    assert (
        arrangement.home["b"] == bridge and arrangement.home["c"] == bridge
    )  # c's most common missing skill
    for lonely in ("d", "e", "f"):
        assert arrangement.home[lonely].kind == "other"
    reason = group_reason(bridge, [pool[0], pool[1], pool[2]], person)
    assert reason.text == "GraphQL shows up in 3 of these. Your evidence doesn't show it yet."
    assert reason.cite is None


def test_first_steps_are_entry_level_roles_for_someone_with_under_two_years() -> None:
    fresher = person_of("fresher", currentRole="Student")
    pool = [
        cand("a", "Accounts Trainee", entry=True, city="Coimbatore"),
        cand("b", "Senior Auditor", city="Coimbatore"),
    ]
    arrangement = arranged(pool, fresher)
    assert arrangement.home["a"].id == "first_steps" and arrangement.home["a"].title == "First steps"
    assert arrangement.home["b"].kind == "other"
    experienced = person_of()  # 2.4 years
    assert arranged([cand("a", "Accounts Trainee", entry=True)], experienced).home["a"].kind == "other"
    unknown = person_of(yearsExperience=None)
    assert arranged([cand("a", "Accounts Trainee", entry=True)], unknown).home["a"].id == "first_steps"


def test_the_first_group_that_fits_is_home_and_the_others_are_also_fits() -> None:
    person = person_of("fresher")
    pool = [cand("a", "Junior Accountant", entry=True, city="Coimbatore")]
    arrangement = arranged(pool, person)
    assert arrangement.home["a"].id == "next_role:junior-accountant"
    assert [g.kind for g in arrangement.claims["a"]] == ["next_role", "first_steps"]
    assert arrangement.kinds("a") == {"next_role", "first_steps"}


def test_a_listing_that_fits_nothing_is_in_other_and_claims_no_group() -> None:
    person = person_of()
    arrangement = arranged([cand("a", "Warehouse Supervisor")], person)
    assert arrangement.home["a"].id == "other" and arrangement.claims["a"] == ()


def test_the_reasons_cite_the_evidence_and_never_invent_a_fact() -> None:
    person = person_of()
    senior_group = role_groups(person)[1]
    members = [cand("a", "Senior Frontend Engineer")]
    reason = group_reason(senior_group, members, person)
    assert reason.text == "Your next step. React and TypeScript show up in your evidence."
    assert (
        reason.cite is not None
        and reason.cite.source == "Resume"
        and reason.cite.text.startswith("Rebuilt the checkout")
    )
    assert (
        group_reason(senior_group, [cand("b", "Senior Frontend Engineer", matched=0)], person).text
        == "Your next step."
    )
    designer = person_of(currentRole="Product Designer")
    current = group_reason(arranged([cand("a", "Product Designer")], designer).home["a"], members, designer)
    assert current.text == "The work you do now."
    assert (
        current.cite is not None
        and current.cite.source == "Resume"
        and current.cite.text == "Product Designer"
    )
    other = group_reason(arranged([cand("a", "Warehouse Supervisor")], person).home["a"], members, person)
    assert "%" not in other.text
    first = group_reason(
        arranged([cand("a", "Intern", entry=True)], person_of("fresher")).home["a"], members, person
    )
    assert first.text == "Entry roles that ask for what your evidence shows."
