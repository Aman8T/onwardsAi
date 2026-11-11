"""A resume as the coach is shown it, and the check a rewrite it proposes must pass: the very check Finalise runs on a typed
line, against the facts the line cites (a known-bad fixture for each way new words can claim more than the facts say)."""

from __future__ import annotations

from tayyari.chat.context import render_resume_block
from tayyari.contract.match import ResumeVariant
from tayyari.match.models import FactBook
from tayyari.resume.coach import coach_view
from tests.unit.resume.support import rig

LINE = "b-exp-1.3"
GOOD = "Ran 38 A/B tests across landing pages and checkout nudges; five winners shipped, adding 6.4% to site conversion."


async def view_of(terms: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    r = rig()
    stored = await r.stored((await r.listing()).resumes[0].id)
    return r.service.coach_view(stored, terms), stored


async def test_the_view_has_each_line_under_its_job_with_the_quotes_of_the_facts_it_rests_on() -> None:
    view, stored = await view_of()

    assert view.resume_id == stored.id and view.title == stored.title
    ids = [line.id for line in view.lines]
    assert ids[:3] == ["b-exp-1.1", "b-exp-1.2", "b-exp-1.3"] and len(ids) == 10
    line = next(line for line in view.lines if line.id == LINE)
    assert line.text.startswith("Ran 38 A/B tests") and line.heading  # the job's title, employer and dates
    assert line.quotes and all(q.strip() for q in line.quotes)
    assert render_resume_block(view).ids["l3"] == LINE


async def test_new_words_resting_on_the_same_facts_pass() -> None:
    view, _ = await view_of()
    assert view.check(LINE, GOOD) is None
    assert view.check(LINE, "Ran 38 A/B tests on landing pages.") is None  # less is not more


async def test_known_bad_new_words_are_refused_with_the_kind_of_claim() -> None:
    view, _ = await view_of()
    assert (
        view.check(LINE, "Ran 38 A/B tests on landing pages, lifting conversion by 40%.")
        == "unsupported_number"
    )
    assert view.check(LINE, "Ran 83 A/B tests on landing pages.") == "unsupported_number"
    assert view.check(LINE, "Led 38 A/B tests on landing pages.") == "unsupported_scope"
    assert view.check(LINE, "Ran 38 A/B tests on landing pages with Salesforce.") == "unsupported_name"
    assert view.check("b-no-such-line", GOOD) == "unknown_line"


async def test_a_word_of_the_role_that_no_fact_supports_is_refused_for_a_resume_for_that_role() -> None:
    view, _ = await view_of(terms=("lifecycle marketing",))
    assert (
        view.check(LINE, "Ran 38 A/B tests on landing pages for lifecycle marketing.") == "unsupported_term"
    )
    plain, _ = await view_of()
    assert (
        plain.check(LINE, "Ran 38 A/B tests on landing pages for lifecycle marketing.") is None
    )  # no role, no such term


async def test_a_dropped_or_empty_line_is_not_shown() -> None:
    r = rig()
    stored = await r.stored((await r.listing()).resumes[0].id)
    draft = ResumeVariant.model_validate(stored.draft)
    first = draft.experience[0].bullets[0]
    first.status = "dropped"
    draft.experience[0].bullets[1].text = "  "
    view = coach_view(stored.id, "t", draft, FactBook.model_validate(stored.facts))
    assert first.id not in [line.id for line in view.lines] and len(view.lines) == 8
