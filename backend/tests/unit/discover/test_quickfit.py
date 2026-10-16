"""The quick fit: a word and counts from the skill names a posting uses against a profile. Never a score."""

from __future__ import annotations

import json
from typing import Any, get_args

import pytest

from tayyari.contract.discover import QuickFitWord
from tayyari.contract.models import Profile
from tayyari.discover.person import PersonIndex
from tayyari.discover.quickfit import FIT_ORDER, PostingIndex, quick_fit
from tayyari.discover.vocab import vocabulary
from tests.fakes.discover import profile_of

VOCAB = vocabulary()


def profile_with(
    skills: list[tuple[str, int]], *, years: float | None = 2.4, evidence: bool = True
) -> Profile:
    base = profile_of("frontend").to_dict()
    base["person"]["yearsExperience"] = years
    if years is None:
        del base["person"]["yearsExperience"]
    base["skills"] = [
        {
            "id": f"s{i}",
            "name": name,
            "domainId": "frontend",
            "score": score,
            "evidence": [{"sourceId": "resume", "text": f"Did {name} at work"}] if evidence else [],
            "unlocks": [],
        }
        for i, (name, score) in enumerate(skills)
    ]
    return Profile.from_dict(base)


def fit(text: str, profile: Profile, *, title: str = "Engineer", years: int | None = None):  # type: ignore[no-untyped-def]
    return quick_fit(PostingIndex(title, text, VOCAB), years, PersonIndex(profile, VOCAB), VOCAB)


SKILLS = [("React", 90), ("TypeScript", 86), ("CSS", 80), ("Git", 70)]


def test_a_posting_naming_skills_the_profile_shows_looks_close() -> None:
    result = fit("We use React, TypeScript, CSS and GraphQL.", profile_with(SKILLS))
    assert (result.word, result.named, result.matched) == ("looks_close", 4, 3)  # 3 of 4 is 0.75
    assert result.to_model().notFound == ["GraphQL"]


def test_the_share_decides_the_word_at_its_thresholds() -> None:
    # Git is a general skill (not counted); AWS, Kafka and MongoDB each stand for a group of alternatives.
    skills = [("React", 90), ("TypeScript", 86), ("CSS", 80), ("Git", 70), ("Jest", 60)]
    profile = profile_with(skills)
    assert fit("React TypeScript CSS Jest AWS", profile).word == "looks_close"  # 4 of 5 asks
    assert fit("React TypeScript CSS AWS Docker", profile).word == "looks_close"  # 3 of 5: the edge, 3 met
    assert fit("React TypeScript CSS Git GraphQL AWS Docker", profile).word == "worth_a_look"  # 3 of 6
    assert fit("React TypeScript CSS Jest AWS Docker GraphQL", profile).word == "worth_a_look"  # 4 of 7
    assert fit("React TypeScript AWS Docker Kafka", profile).word == "worth_a_look"  # 2 of 5: the edge, 2 met
    assert fit("React TypeScript AWS Docker Kafka Redis GraphQL", profile).word == "stretch"  # 2 of 7
    assert (
        fit("React TypeScript CSS GraphQL AWS Docker Kafka Redis MongoDB", profile).word == "stretch"
    )  # 3 of 9


def test_general_skills_are_neither_asked_nor_missing() -> None:
    result = fit("React TypeScript CSS Git Agile Scrum Jira code review", profile_with(SKILLS))
    assert (result.named, result.matched) == (3, 3)
    assert result.to_model().notFound == []


def test_named_alternatives_are_one_ask_met_by_any_of_them() -> None:
    met = fit("React, Angular or Vue.js. TypeScript and CSS.", profile_with(SKILLS))
    assert (met.word, met.named, met.matched) == ("looks_close", 3, 3)
    without_react = profile_with([("TypeScript", 86), ("CSS", 80), ("Jest", 60)])
    unmet = fit("Angular or Vue.js, TypeScript, CSS and Jest.", without_react)
    assert (unmet.named, unmet.matched) == (4, 3)
    assert unmet.to_model().notFound == ["Angular or Vue.js"]


def test_a_skill_that_plainly_includes_another_shows_it() -> None:
    profile = profile_with([("TypeScript", 86), ("CSS", 80), ("Jest", 60)])
    result = fit("JavaScript, TypeScript, CSS and unit testing.", profile)
    assert (result.named, result.matched) == (4, 4)  # TypeScript shows JavaScript; Jest shows unit testing
    reasons = [reason.text for reason in result.reasons]
    assert reasons == ["You show TypeScript", "You show CSS"]  # one reason per profile skill, strongest first


def test_naming_fewer_than_three_skills_cannot_be_told() -> None:
    assert fit("React and TypeScript.", profile_with(SKILLS)).word == "cant_tell"
    assert fit("A role about people.", profile_with(SKILLS)).word == "cant_tell"
    assert fit("React TypeScript CSS", profile_with(SKILLS)).word == "looks_close"  # exactly three is enough


def test_a_profile_with_fewer_than_three_skills_cannot_be_told() -> None:
    thin = profile_with([("React", 90), ("TypeScript", 86)])
    assert fit("React TypeScript CSS Git Jest AWS", thin).word == "cant_tell"


def test_years_three_or_more_beyond_the_profile_is_a_stretch() -> None:
    profile = profile_with(SKILLS, years=2.4)
    text = "React TypeScript CSS Git"
    assert fit(text, profile, years=5).word == "looks_close"  # 2.6 years short is not enough
    assert fit(text, profile, years=6).word == "stretch"  # 3.6
    stretched = fit(text, profile, years=8)
    assert (stretched.years_asked, stretched.years_you) == (8, 2.4)
    assert (
        fit(text, profile_with(SKILLS, years=None), years=8).word == "looks_close"
    )  # unknown years never stretch


def test_cant_tell_comes_before_the_years_rule() -> None:
    assert fit("React and CSS.", profile_with(SKILLS), years=9).word == "cant_tell"


def test_a_skill_counts_as_shown_from_a_score_of_thirty() -> None:
    profile = profile_with([("React", 90), ("TypeScript", 30), ("GraphQL", 29), ("Jest", 70)])
    result = fit("React TypeScript GraphQL Jest", profile)
    assert result.matched == 3 and result.to_model().notFound == ["GraphQL"]


def test_the_profile_is_read_alias_aware_and_a_persons_own_words_are_extra_terms() -> None:
    profile = profile_with(
        [
            ("Postgres & Prisma", 55),
            ("Accessibility (WCAG)", 76),
            ("Motion design", 80),
            ("Quantum gardening", 50),
        ]
    )
    result = fit("PostgreSQL, Prisma, a11y, Motion design and quantum gardening wanted.", profile)
    assert (result.named, result.matched) == (5, 5)
    assert result.missing == ()
    extra_only = fit("We value quantum gardening, React and CSS.", profile)
    assert extra_only.named == 3 and extra_only.matched == 1
    assert "Quantum gardening" not in [name for _, name in extra_only.missing]


def test_not_found_is_most_prominent_first_and_at_most_five() -> None:
    title_hit = PostingIndex(
        "Kubernetes Engineer",
        "The role\nWe like Redis. MongoDB MongoDB MongoDB AWS Docker\n\nRequirements:\n- Terraform and Kafka",
        VOCAB,
    )
    result = quick_fit(title_hit, None, PersonIndex(profile_with(SKILLS), VOCAB), VOCAB)
    names = [name for _, name in result.missing]
    assert names[0] == "Kubernetes"  # named in the title
    assert set(names[1:3]) == {"Terraform", "Kafka"}  # under a requirements heading
    assert len(names) == 7 and len(result.to_model().notFound) == 5


def test_reasons_are_the_two_strongest_matched_skills_citing_their_evidence() -> None:
    result = fit("TypeScript React CSS Git GraphQL", profile_with(SKILLS))
    texts = [reason.text for reason in result.reasons]
    assert texts == ["You show React", "You show TypeScript"]
    cite = result.reasons[0].cite
    assert cite is not None and cite.source == "Resume" and cite.text == "Did React at work"


def test_a_skill_without_evidence_gives_no_reason() -> None:
    result = fit("React TypeScript CSS", profile_with(SKILLS, evidence=False))
    assert result.reasons == () and result.word == "looks_close"


def test_a_cite_is_cut_to_ninety_characters() -> None:
    base = profile_with(SKILLS).to_dict()
    base["skills"][0]["evidence"] = [{"sourceId": "github", "text": "x " * 100}]
    result = fit("React TypeScript CSS", Profile.from_dict(base))
    cite = result.reasons[0].cite
    assert cite is not None and cite.source == "GitHub" and len(cite.text) <= 90


def test_nothing_in_a_quick_fit_is_a_percentage() -> None:
    result = fit("React TypeScript CSS GraphQL AWS", profile_with(SKILLS), years=5)
    dumped = json.dumps(result.to_model().to_dict())
    assert "%" not in dumped and "percent" not in dumped.lower()
    assert set(result.to_model().to_dict()) <= {
        "word", "named", "matched", "notFound", "yearsAsked", "yearsYou", "reasons",
    }  # fmt: skip


def test_the_words_are_ordered_from_closest_to_least_known() -> None:
    assert sorted(FIT_ORDER, key=FIT_ORDER.__getitem__) == [
        "looks_close",
        "worth_a_look",
        "stretch",
        "cant_tell",
    ]
    assert set(FIT_ORDER) == set(get_args(QuickFitWord))


@pytest.mark.parametrize("name", ["frontend", "fresher"])
def test_the_fixture_profiles_are_read_without_surprises(name: str) -> None:
    person = PersonIndex(profile_of(name), VOCAB)
    assert person.skill_count >= 5 and not person.thin
    assert person.shows("react" if name == "frontend" else "tally") is not None


def test_the_fixture_profile_shows_only_what_it_holds() -> None:
    person = PersonIndex(profile_of("frontend"), VOCAB)
    assert person.shows("graphql") is None
    assert person.shows("llm-evals") is None  # held at 12: not shown
    covered: dict[str, Any] = person.covered
    assert "x:bff" in covered  # a piece of "Node.js / BFF" the vocabulary does not know


def test_a_skill_named_only_in_the_company_or_benefits_parts_is_not_a_skill_the_job_asks_for() -> None:
    text = (
        "About us\nWe use Kubernetes, AWS and Terraform across the company. Artificial intelligence is our focus.\n\n"
        "The role\nBuild React apps in TypeScript.\n\nBenefits\nA budget for Docker courses and talent acquisition.\n\n"
        "Requirements:\n- CSS\n- GraphQL\n"
    )
    result = quick_fit(
        PostingIndex("Engineer", text, VOCAB), None, PersonIndex(profile_with(SKILLS), VOCAB), VOCAB
    )
    assert result.named == 4 and result.matched == 3  # React, TypeScript, CSS and GraphQL
    assert [name for _, name in result.missing] == ["GraphQL"]
