"""Finalise: a typed line is checked against the facts it cites. Known-bad lines prove each check fires; the two answers
settle them.

The typed lines here are the tests' own inventions (a figure, a verb, a tool, a term that no fact of the persona has):
that is the point of them.
"""

from __future__ import annotations

from tayyari.contract.resume import FindingAnswer, ResumeOp
from tayyari.match.bullets import export_blockers
from tayyari.match.verify import exact_wording
from tayyari.resume.arrange import path_variant, role_terms, upload_variant
from tayyari.resume.finalise import finalise
from tayyari.resume.ops import apply_ops
from tests.unit.resume.support import FIGURE_LINE, INVENTED_FIGURE, line, lines_of, meera, riya, role_of


def typed(variant, book, **texts: str):  # type: ignore[no-untyped-def]
    """``variant`` with the named lines (``exp_1_3="..."`` is the line ``b-exp-1.3``) set to what was typed."""
    ops = [
        ResumeOp(op="edit", bulletId="b-" + key.replace("_", "-", 1).replace("_", "."), text=text)
        for key, text in texts.items()
    ]
    return apply_ops(variant, book, ops)


def answer(line_id: str, choice: str) -> FindingAnswer:
    return FindingAnswer.model_validate({"bulletId": line_id, "choice": choice})


def found(outcome) -> list[tuple[str, str, str]]:  # type: ignore[no-untyped-def]
    return [(f.bulletId, f.kind, f.what) for f in outcome.findings]


# ───────────────────────── the checks fire ─────────────────────────


def test_a_figure_no_fact_has_is_a_finding() -> None:
    book = meera().book()
    draft = typed(upload_variant(book), book, exp_1_3=INVENTED_FIGURE)

    outcome = finalise(draft, book)

    assert not outcome.done
    [finding] = outcome.findings
    assert (finding.bulletId, finding.kind, finding.what) == (FIGURE_LINE, "number", "40%")
    assert finding.text == INVENTED_FIGURE and finding.what in finding.text
    assert finding.options == ["mine", "undo"]
    assert line(outcome.variant, FIGURE_LINE).status == "needs_user"
    assert export_blockers(outcome.variant)  # the draft still can not be exported
    assert outcome.new_facts == [] and outcome.book == book


def test_the_figures_the_fact_does_have_are_not_findings() -> None:
    book = meera().book()
    # the block says 38 A/B tests and five winners: both are its own
    draft = typed(upload_variant(book), book, exp_1_3="Ran 38 A/B tests; five winners shipped")
    outcome = finalise(draft, book)
    assert outcome.done and outcome.findings == []
    settled = line(outcome.variant, FIGURE_LINE)
    assert (settled.status, settled.verdict, settled.factIds) == ("verified", "reframed", ["exp-1.3"])


def test_a_scope_verb_stronger_than_the_fact_shows_is_a_finding() -> None:
    book = meera().book()
    # the block says she "Built the weekly acquisition report": leading it is more than the fact shows
    draft = typed(upload_variant(book), book, exp_2_2="Led the weekly acquisition report for the founders")
    assert found(finalise(draft, book)) == [("b-exp-2.2", "scope", "Led")]


def test_a_name_no_fact_has_is_a_finding() -> None:
    book = meera().book()
    draft = typed(
        upload_variant(book), book, exp_2_2="Built the weekly acquisition report for the founders in Tableau"
    )
    assert found(finalise(draft, book)) == [("b-exp-2.2", "name", "Tableau")]


def test_a_praise_word_no_fact_uses_is_a_finding() -> None:
    book = meera().book()
    draft = typed(upload_variant(book), book, exp_3_1="Ran world-class social media for the brand")
    assert found(finalise(draft, book)) == [("b-exp-3.1", "name", "world-class")]


def test_a_word_of_the_roles_requirements_needs_a_fact_that_supports_it() -> None:
    persona = riya()
    book, role = persona.book(), role_of(persona, "ai-product-engineer")
    draft = typed(
        path_variant(book, role, skills=persona.profile.skills),
        book,
        exp_1_1="Rebuilt the checkout in React with prompt design for the assistant",
    )

    with_terms = finalise(draft, book, terms=role_terms(role))
    without = finalise(draft, book)

    assert ("b-exp-1.1", "term", "prompt design") in found(with_terms)
    assert not any(kind == "term" for _, kind, _ in found(without))  # the uploaded resume has no role


def test_every_unsupported_part_of_a_line_is_reported_in_reading_order() -> None:
    book = meera().book()
    draft = typed(
        upload_variant(book),
        book,
        exp_2_2="Led the weekly acquisition report in Tableau for 12 founders",
    )
    assert found(finalise(draft, book)) == [
        ("b-exp-2.2", "scope", "Led"),
        ("b-exp-2.2", "name", "Tableau"),
        ("b-exp-2.2", "number", "12"),
    ]


def test_only_lines_the_person_typed_are_checked() -> None:
    book = meera().book()
    upload = upload_variant(book)
    outcome = finalise(upload, book)
    assert outcome.done and outcome.variant == upload and outcome.new_facts == []


# ───────────────────────── the two answers ─────────────────────────


def test_mine_makes_the_typed_line_a_fact_in_the_persons_own_words() -> None:
    book = meera().book()
    draft = typed(upload_variant(book), book, exp_1_3=INVENTED_FIGURE)

    outcome = finalise(draft, book, [answer(FIGURE_LINE, "mine")], key="resume:r1")

    assert outcome.done and outcome.findings == []
    [said] = outcome.new_facts
    assert said.text == INVENTED_FIGURE and said.id.startswith("user-")
    [ref] = said.refs
    assert (ref.source, ref.docId, ref.quote) == ("user", f"resume:r1/{FIGURE_LINE}", INVENTED_FIGURE)
    settled = line(outcome.variant, FIGURE_LINE)
    assert settled.text == INVENTED_FIGURE  # the person's words stay as typed
    assert settled.factIds == ["exp-1.3", said.id] and settled.status == "verified"
    assert (
        outcome.book.get(said.id) == said and book.get(said.id) is None
    )  # the book handed in is not changed
    assert export_blockers(outcome.variant) == []


def test_mine_on_a_stronger_verb_keeps_the_line_marked_as_a_stretch() -> None:
    book = meera().book()
    draft = typed(upload_variant(book), book, exp_2_2="Led the weekly acquisition report for the founders")
    outcome = finalise(draft, book, [answer("b-exp-2.2", "mine")])
    assert outcome.done
    assert (line(outcome.variant, "b-exp-2.2").verdict, line(outcome.variant, "b-exp-2.2").status) == (
        "stretch",
        "verified",
    )


def test_undo_goes_back_to_the_facts_own_wording() -> None:
    book = meera().book()
    upload = upload_variant(book)
    draft = typed(upload, book, exp_1_3=INVENTED_FIGURE)

    outcome = finalise(draft, book, [answer(FIGURE_LINE, "undo")])

    assert outcome.done and outcome.new_facts == [] and outcome.book == book
    assert outcome.variant == upload
    assert line(outcome.variant, FIGURE_LINE).text == exact_wording(book.by_id()["exp-1.3"])


def test_a_finding_without_an_answer_keeps_waiting_while_the_others_settle() -> None:
    book = meera().book()
    draft = typed(
        upload_variant(book),
        book,
        exp_1_3=INVENTED_FIGURE,
        exp_2_2="Built the weekly acquisition report for the founders in Tableau",
        exp_3_1="Ran social media and newsletters for the brand",  # nothing in it the fact lacks
    )

    outcome = finalise(draft, book, [answer("b-exp-2.2", "undo")])

    assert found(outcome) == [(FIGURE_LINE, "number", "40%")] and not outcome.done
    assert line(outcome.variant, "b-exp-2.2").status == "verified"  # undone
    assert line(outcome.variant, "b-exp-3.1").status == "verified"  # checked, nothing found
    assert line(outcome.variant, FIGURE_LINE).status == "needs_user"
    assert [b.id for b in lines_of(outcome.variant) if b.status == "needs_user"] == [FIGURE_LINE]


def test_an_answer_for_a_line_with_nothing_to_find_is_ignored() -> None:
    book = meera().book()
    upload = upload_variant(book)
    outcome = finalise(upload, book, [answer("b-exp-1.1", "mine"), answer("b-nothing", "undo")])
    assert outcome.done and outcome.variant == upload and outcome.new_facts == []


def test_what_the_person_said_before_still_backs_the_line_when_it_is_edited_again() -> None:
    book = meera().book()
    first = finalise(
        typed(upload_variant(book), book, exp_1_3=INVENTED_FIGURE), book, [answer(FIGURE_LINE, "mine")]
    )
    # the same 40%, typed into other words: the person's earlier statement has that figure
    again = typed(first.variant, first.book, exp_1_3="Lifted conversion by 40% across 38 A/B tests")
    second = finalise(again, first.book)
    assert second.done and second.new_facts == []
    # a new figure is a new finding
    more = typed(first.variant, first.book, exp_1_3="Lifted conversion by 55% across 38 A/B tests")
    assert found(finalise(more, first.book)) == [(FIGURE_LINE, "number", "55%")]


def test_finalise_never_changes_the_draft_it_was_given() -> None:
    book = meera().book()
    draft = typed(upload_variant(book), book, exp_1_3=INVENTED_FIGURE)
    before = draft.model_copy(deep=True)
    finalise(draft, book, [answer(FIGURE_LINE, "mine")])
    assert draft == before
