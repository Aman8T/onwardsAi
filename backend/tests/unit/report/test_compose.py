"""What the report shows, worked out in code: the credibility bar, the counts and the stamp, the quotes, the redaction of
employer names (known-bad fixtures first), the owner's choices, and the version."""

from __future__ import annotations

import json

from tayyari.contract.report import ReportContent
from tayyari.report.compose import (
    EMPLOYER,
    compose,
    is_credible,
    org_names,
    org_variants,
    person_line,
    quote_id,
    redact,
    short_name,
    version_of,
)
from tests.unit.report.support import ORG, choices, profile


def text_of(content: ReportContent) -> str:
    return json.dumps(content.model_dump(mode="json"), ensure_ascii=False)


# ───────────────────────── known-bad: what must never reach a public page ─────────────────────────


def test_an_employer_name_never_reaches_the_public_report_while_hidden() -> None:
    offered, public = compose(profile(), choices(), [ORG])
    for content in (offered, public):
        assert "zentrix" not in text_of(content).casefold()
    quotes = [q.text for r in public.roles for q in r.quotes]
    assert any(EMPLOYER in q for q in quotes)


def test_with_employer_names_on_the_lines_stay_as_written() -> None:
    _, public = compose(profile(), choices(showOrgs=True), [ORG])
    assert "Zentrix Systems" in text_of(public)


def test_with_no_employer_known_and_names_hidden_the_public_page_carries_no_quotes() -> None:
    """Nothing could be redacted, so nothing is quoted: "off" is never a promise the code cannot keep."""
    offered, public = compose(profile(), choices(), [])
    assert all(r.quotes == [] for r in public.roles)
    assert any(r.quotes for r in offered.roles)
    assert "zentrix" not in text_of(public).casefold()
    _, shown = compose(profile(), choices(showOrgs=True), [])
    assert any(r.quotes for r in shown.roles)


def test_a_line_about_a_layoff_is_never_quoted() -> None:
    offered, _ = compose(profile(), choices(showOrgs=True), [ORG])
    assert "laid off" not in text_of(offered).casefold()


def test_near_misses_and_hidden_quotes_stay_off_the_public_page() -> None:
    offered, _ = compose(profile(), choices(), [ORG])
    first = offered.roles[0].quotes[0]
    _, public = compose(profile(), choices(hiddenQuotes=[first.id]), [ORG])
    blob = text_of(public)
    assert first.text not in blob
    assert public.nearMisses == []
    assert "Data Engineer" not in blob and "Product Manager" not in blob
    offered_again, _ = compose(profile(), choices(hiddenQuotes=[first.id]), [ORG])
    assert offered_again.roles[0].quotes[0].hidden is True


def test_no_percentage_rank_or_layoff_word_in_the_report_text_it_writes() -> None:
    """The page's own words (titles, counts, the person line) never score anyone; quotes are the person's facts."""
    _, public = compose(profile(), choices(showQuotes=False), [ORG])
    blob = text_of(public).casefold()
    for word in ("%", "rank", "percentile", "top ", "laid off"):
        assert word not in blob


# ───────────────────────── which roles, in what order ─────────────────────────


def test_the_bar_is_half_the_asks_and_one_cited_line() -> None:
    assert is_credible(3, 6, True)
    assert not is_credible(2, 6, True)
    assert not is_credible(3, 6, False)
    assert is_credible(2, 3, True) and not is_credible(1, 3, True)
    assert not is_credible(0, 0, True)


def test_three_roles_at_most_most_evidenced_first() -> None:
    offered, _ = compose(profile(), choices(), [ORG])
    assert [r.id for r in offered.roles] == ["qa-lead", "backend-gcc", "platform"]
    assert [(r.have, r.total) for r in offered.roles] == [(5, 6), (4, 6), (3, 6)]


def test_a_role_with_no_cited_line_behind_it_is_a_near_miss() -> None:
    """``pm`` has 3 of 6 asks, but its evidenced asks map to a skill with no evidence; ``data-eng`` has 2 of 6."""
    offered, _ = compose(profile(), choices(), [ORG])
    assert "pm" not in {r.id for r in offered.roles}
    assert [n.id for n in offered.nearMisses] == ["pm", "data-eng"]
    assert all(n.missing for n in offered.nearMisses)


def test_a_role_above_the_bar_but_past_the_limit_is_not_called_a_near_miss() -> None:
    """``sre`` passes the bar (3 of 6, backed) but is fourth: it is left out, never shown as "close, not yet"."""
    offered, _ = compose(profile(), choices(), [ORG])
    assert "sre" not in {r.id for r in offered.roles} | {n.id for n in offered.nearMisses}


def test_ready_now_at_one_ask_missing_and_not_at_two() -> None:
    offered, _ = compose(profile(), choices(), [ORG])
    by_id = {r.id: r for r in offered.roles}
    assert by_id["qa-lead"].ready and by_id["qa-lead"].missing == ["Perf testing"]
    assert not by_id["backend-gcc"].ready
    assert by_id["backend-gcc"].missing == ["Kubernetes", "System design"]


def test_a_resume_that_backs_no_role_gives_an_empty_report_with_near_misses() -> None:
    doc_roles = [r for r in profile().model_dump()["roles"] if r["id"] in ("data-eng", "pm")]
    offered, public = compose(profile(roles=doc_roles), choices(showNearMisses=True), [ORG])
    assert offered.roles == [] and public.roles == []
    assert {n.id for n in public.nearMisses} == {"data-eng", "pm"}


# ───────────────────────── quotes ─────────────────────────


def test_two_quotes_a_role_strongest_skill_first_never_twice_on_a_page() -> None:
    offered, _ = compose(profile(), choices(), [ORG])
    texts = [q.text for r in offered.roles for q in r.quotes]
    assert len(texts) == len(set(texts))
    assert all(len(r.quotes) <= 2 for r in offered.roles)
    qa = offered.roles[0]
    assert qa.quotes[0].text.startswith("Led the move of the payments regression suite")
    assert qa.quotes[1].text == "Trained four new joiners on the test framework."


def test_a_quote_names_its_source_by_label() -> None:
    offered, _ = compose(profile(), choices(), [ORG])
    platform = next(r for r in offered.roles if r.id == "platform")
    assert [q.source for q in platform.quotes] == ["GitHub"]


def test_quote_ids_are_stable_and_ignore_spacing() -> None:
    assert quote_id("resume", "Built  the portal.") == quote_id("resume", "Built the portal.")
    assert quote_id("resume", "Built the portal.") != quote_id("github", "Built the portal.")
    assert len(quote_id("resume", "x")) == 12


# ───────────────────────── the person ─────────────────────────


def test_the_person_line_has_no_employer_and_the_name_can_go() -> None:
    p = profile()
    assert person_line(p) == "Senior Software Engineer · 7 years · Pune"
    assert (
        short_name("Ravi Kumar Sharma") == "Ravi S."
        and short_name("Ravi") == "Ravi"
        and short_name("  ") is None
    )
    _, public = compose(p, choices(showName=False), [ORG])
    assert public.person.name is None and "Ravi" not in text_of(public)
    _, named = compose(p, choices(), [ORG])
    assert named.person.name == "Ravi S."


def test_open_to_work_only_when_the_owner_says() -> None:
    assert compose(profile(), choices(), [ORG])[1].openToWork is False
    assert compose(profile(), choices(openToWork=True), [ORG])[1].openToWork is True


# ───────────────────────── employer names ─────────────────────────


def test_employer_names_are_found_under_org_keys_anywhere() -> None:
    blobs = [
        {
            "experience": [{"org": "Zentrix Systems Pvt Ltd"}, {"org": " zentrix systems pvt ltd "}],
            "projects": [],
        },
        {"education": [{"org": "IIT Bombay"}], "org": None},
        [{"nested": {"org": "Acme"}}],
        {"org": "x"},
    ]
    assert org_names(blobs) == ["Zentrix Systems Pvt Ltd", "IIT Bombay", "Acme"]


def test_a_name_is_hidden_with_and_without_its_company_words() -> None:
    assert org_variants("Zentrix Systems Pvt Ltd") == [
        "Zentrix Systems Pvt Ltd",
        "Zentrix Systems Pvt",
        "Zentrix Systems",
        "Zentrix",
    ]
    assert redact("At ZENTRIX's bank desk and Zentrix Systems.", [ORG]) == (
        f"At {EMPLOYER}'s bank desk and {EMPLOYER}."
    )
    assert redact("Zentrixology is a word.", [ORG]) == "Zentrixology is a word."


# ───────────────────────── the version ─────────────────────────


def test_the_version_moves_only_with_the_public_page() -> None:
    p = profile()
    base = version_of(compose(p, choices(), [ORG])[1])
    assert version_of(compose(p, choices(), [ORG])[1]) == base
    assert version_of(compose(p, choices(showName=False), [ORG])[1]) != base
    # a near miss's visibility is public only when shown: with near misses hidden, the public page is the same
    assert version_of(compose(p, choices(showNearMisses=True), [ORG])[1]) != base
