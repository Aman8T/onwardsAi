"""The code checks on the three answers, with a known-bad answer for every rule so each is proven to fire: an id that does
not exist, a why or have item with no citation, a number from nowhere (in a why, a headline, a title, a body, a task, a
deliivery), a gap name the plan does not have, hours out of range, too many phases or tasks, overlong text, and the
headline's name check."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tayyari.prepare.verify import (
    BODY_CHARS,
    DELIVERABLE_CHARS,
    HEADLINE_CHARS,
    HEADLINE_FAILED_NOTE,
    HEADLINE_UNAVAILABLE_NOTE,
    MAX_PHASES,
    MAX_TASKS,
    PROPOSED_HEADLINE_CHARS,
    TASK_CHARS,
    TITLE_CHARS,
    WHY_CHARS,
    capitalised_words,
    check_headline,
    check_moves,
    check_standing,
    clip,
    gap_numbers,
    numbers_in,
    words_of,
)
from tests.unit.prepare.support import js, plan_for

PLAN = plan_for()
EVIDENCE = PLAN.evidence
GAPS = PLAN.gaps
ETA = PLAN.eta


def standing() -> dict[str, Any]:
    return copy.deepcopy(js("riya.standing.answer.json"))


def moves() -> dict[str, Any]:
    return copy.deepcopy(js("riya.moves.answer.json"))


def headline() -> dict[str, Any]:
    return copy.deepcopy(js("riya.headline.answer.json"))


def run_standing(data: dict[str, Any]) -> Any:
    return check_standing(data, gaps=GAPS, evidence=EVIDENCE, eta=ETA)


def run_moves(data: dict[str, Any]) -> Any:
    return check_moves(data, gaps=GAPS, evidence=EVIDENCE, eta=ETA)


def run_headline(data: dict[str, Any] | None) -> Any:
    return check_headline(
        data, evidence=EVIDENCE, role_title=PLAN.roleTitle, current="Frontend Engineer · 2.4 yrs · Bengaluru"
    )


# ───────────────────────── helpers ─────────────────────────


def test_numbers_in_finds_every_digit_sequence_as_the_person_wrote_it() -> None:
    assert numbers_in("LCP 4.1s to 1.9s, 10,000+ rows, 9.4%, 2026-09-21, 42-component") == {
        "4.1",
        "1.9",
        "10000",
        "9.4",
        "2026",
        "09",
        "21",
        "42",
    }
    assert numbers_in("no digits here") == set()


def test_gap_numbers_are_the_levels_and_the_eta() -> None:
    assert gap_numbers(GAPS, "~9 months") == {"12", "65", "32", "70", "38", "40", "52", "75", "9"}


def test_clip_cuts_at_a_word_and_collapses_whitespace() -> None:
    assert clip("  two\n words ", 20) == "two words"
    long = "alpha beta gamma delta epsilon"
    assert clip(long, 16) == "alpha beta…"
    assert clip(None, 10) == "" and clip(7, 10) == ""


def test_words_of_and_capitalised_words() -> None:
    assert {"kite", "ui", "storybook", "storybook's"} <= words_of(["Owns Kite UI, Storybook's docs"])
    assert capitalised_words("Frontend Engineer · motion-kit (212 stars) · Bengaluru") == [
        "Engineer",
        "Bengaluru",
    ]
    assert capitalised_words("Aspiring AI builder") == ["AI"]


# ───────────────────────── standing ─────────────────────────


def test_a_good_standing_answer_keeps_the_headline_and_every_why() -> None:
    out = run_standing(standing())
    assert out.headline is not None and out.headline.startswith("You ship AI features already")
    assert out.why_count == 5
    assert [g.name for g in out.gaps] == [g.name for g in GAPS]  # the plan's order, never the model's
    evals = out.gaps[0]
    assert evals.evidenceIds == ["e1", "e2"] and evals.why is not None and "34 repos" in evals.why
    assert evals.current == 12 and evals.target == 65 and evals.biggest is True  # the numbers are the graph's


def test_a_why_citing_an_id_that_does_not_exist_is_dropped() -> None:
    data = standing()
    data["gaps"][1]["evidenceIds"] = ["e99"]
    out = run_standing(data)
    assert out.gaps[1].why is None and out.gaps[1].evidenceIds is None
    assert out.why_count == 4


def test_a_why_with_no_citation_at_all_is_dropped() -> None:
    data = standing()
    data["gaps"][2]["evidenceIds"] = []
    assert run_standing(data).gaps[2].why is None


def test_a_why_with_a_number_from_nowhere_is_dropped() -> None:
    data = standing()
    data["gaps"][3]["why"] = "notes-ai indexes 5,000 notes already, so retrieval is close."
    out = run_standing(data)
    assert out.gaps[3].why is None
    # a number that is in the cited line passes, and so does a gap level
    data = standing()
    data["gaps"][0]["why"] = "No eval harness in 34 repos; you stand at 12 and the role wants 65."
    assert run_standing(data).gaps[0].why is not None


def test_a_why_may_use_a_number_only_from_the_lines_it_cites() -> None:
    data = standing()
    data["gaps"][0]["evidenceIds"] = ["e2"]  # e2 has no number; 34 is in e1
    assert run_standing(data).gaps[0].why is None


def test_a_row_for_a_gap_the_plan_does_not_have_is_ignored_and_the_first_row_per_gap_wins() -> None:
    data = standing()
    data["gaps"].append({"name": "Rust", "evidenceIds": ["e1"], "why": "Nothing."})
    data["gaps"].append({"name": "llm evals", "evidenceIds": ["e1"], "why": "A second opinion."})
    out = run_standing(data)
    assert out.why_count == 5 and "second opinion" not in (out.gaps[0].why or "")


def test_a_headline_with_a_number_from_nowhere_is_dropped_but_the_whys_stay() -> None:
    data = standing()
    data["headline"] = "You are 80% of the way there."
    out = run_standing(data)
    assert out.headline is None and out.why_count == 5


def test_a_headline_may_use_the_levels_the_eta_and_any_evidence_number() -> None:
    data = standing()
    data["headline"] = "From 12 to 65 on evals in about 9 months, with 212 stars behind you."
    assert run_standing(data).headline is not None


def test_overlong_whys_and_headlines_are_clipped() -> None:
    data = standing()
    data["headline"] = "word " * 80
    data["gaps"][0]["why"] = "why " * 100
    out = run_standing(data)
    assert out.headline is not None and len(out.headline) <= HEADLINE_CHARS and out.headline.endswith("…")
    assert out.gaps[0].why is not None and len(out.gaps[0].why) <= WHY_CHARS


def test_a_malformed_standing_answer_keeps_the_gaps_bare() -> None:
    out = run_standing({"headline": 7, "gaps": "nope"})
    assert out.headline is None and out.why_count == 0 and len(out.gaps) == 5
    out = run_standing({"gaps": [{"name": "LLM evals"}, 42, None]})
    assert out.why_count == 0


# ───────────────────────── moves ─────────────────────────


def test_a_good_moves_answer_keeps_three_moves_with_ids_in_order() -> None:
    out = run_moves(moves())
    assert [m.id for m in out] == ["m1", "m2", "m3"]
    big = out[0]
    assert big.title == "Add an eval harness and citations to notes-ai"
    assert big.works == ["LLM evals", "RAG & retrieval", "Prompt design"]
    assert [h.evidenceIds for h in big.have] == [["e5", "e7"], ["e35"]]
    assert [p.hours for p in big.phases] == [6, 10, 12, 6]
    assert [len(p.tasks) for p in big.phases] == [3, 3, 3, 3]
    assert big.phases[1].deliverable == "A passing eval run in CI with a score table"


def test_a_have_item_without_a_valid_citation_is_dropped_the_move_stays() -> None:
    data = moves()
    data["moves"][0]["have"][1]["evidenceIds"] = ["e404"]
    data["moves"][1]["have"][0]["evidenceIds"] = []
    out = run_moves(data)
    assert [h.evidenceIds for h in out[0].have] == [["e5", "e7"]]
    assert [h.evidenceIds for h in out[1].have] == [["e3"]]


def test_a_have_item_with_a_number_not_in_its_lines_is_dropped() -> None:
    data = moves()
    data["moves"][0]["have"][0]["text"] = "notes-ai already serves 400 users."
    out = run_moves(data)
    assert [h.text for h in out[0].have] == [
        "motion-kit shows you can keep a test suite going: 31 test files."
    ]


def test_a_move_whose_title_or_body_has_a_number_from_nowhere_is_dropped() -> None:
    data = moves()
    data["moves"][0]["title"] = "Write 20 evals for notes-ai"
    out = run_moves(data)
    assert [m.title for m in out] == [
        "Give Recipe Genie streaming, a retry and a thumbs-down",
        "Compare three prompt versions for Recipe Genie",
    ]
    assert [m.id for m in out] == ["m1", "m2"]  # ids are assigned after the checks
    data = moves()
    data["moves"][1]["body"] = "You reach 10x more people."
    assert len(run_moves(data)) == 2


def test_a_title_may_use_a_number_from_the_moves_own_citations_or_a_level() -> None:
    data = moves()
    data["moves"][0]["title"] = (
        "Bring notes-ai from 12 to 65 with 31 test files"  # 12, 65: levels; 31: e35 cited
    )
    assert run_moves(data)[0].title.startswith("Bring notes-ai")
    data = moves()
    data["moves"][2]["title"] = "Use the 31 test files"  # m3 cites only e4, which has no 31
    assert len(run_moves(data)) == 2


def test_works_is_filtered_to_the_plans_gaps_and_a_move_with_none_left_is_dropped() -> None:
    data = moves()
    data["moves"][0]["works"] = ["llm EVALS", "Rust", "LLM evals", "RAG & retrieval"]
    out = run_moves(data)
    assert out[0].works == ["LLM evals", "RAG & retrieval"]  # canonical names, no repeats
    data["moves"][1]["works"] = ["Rust", "Go"]
    out = run_moves(data)
    assert [m.title for m in out] == [
        "Add an eval harness and citations to notes-ai",
        "Compare three prompt versions for Recipe Genie",
    ]


@pytest.mark.parametrize("hours", [0, 41, -3])
def test_a_phase_with_hours_out_of_range_is_dropped(hours: int) -> None:
    data = moves()
    data["moves"][0]["phases"][0]["hours"] = hours
    out = run_moves(data)
    assert [p.title for p in out[0].phases] == [
        "Score every answer",
        "Add citations and fix chunking",
        "Version the prompts",
    ]


def test_a_move_whose_hours_are_not_whole_numbers_is_dropped_as_malformed() -> None:
    data = moves()
    data["moves"][0]["phases"][0]["hours"] = 6.5
    assert len(run_moves(data)) == 2
    data = moves()
    data["moves"][0]["phases"][0]["hours"] = "six"
    assert len(run_moves(data)) == 2


def test_at_most_five_phases_and_four_tasks_are_kept() -> None:
    data = moves()
    phase = data["moves"][0]["phases"][0]
    letters = "abcdefgh"
    data["moves"][0]["phases"] = [copy.deepcopy(phase) | {"title": f"Phase {c}"} for c in letters]
    data["moves"][0]["phases"][0]["tasks"] = [f"Do thing {c}" for c in letters[:7]]
    out = run_moves(data)
    assert len(out[0].phases) == MAX_PHASES == 5
    assert [p.title for p in out[0].phases] == ["Phase a", "Phase b", "Phase c", "Phase d", "Phase e"]
    assert out[0].phases[0].tasks == ["Do thing a", "Do thing b", "Do thing c", "Do thing d"]
    assert len(out[0].phases[0].tasks) == MAX_TASKS


def test_phase_titles_with_a_number_from_nowhere_fail_the_phase() -> None:
    data = moves()
    data["moves"][0]["phases"][0]["title"] = "Week 1: write the question set"
    out = run_moves(data)
    assert out[0].phases[0].title == "Score every answer"


def test_a_task_with_a_number_from_nowhere_is_dropped_and_the_phase_stays() -> None:
    data = moves()
    data["moves"][0]["phases"][0]["tasks"][1] = "Write 50 questions"
    out = run_moves(data)
    assert out[0].phases[0].tasks == [
        "Collect questions your notes can answer and the answer each should contain",
        "Commit the set as a fixture next to the prompts",
    ]


def test_a_phase_whose_every_task_fails_is_dropped() -> None:
    data = moves()
    data["moves"][2]["phases"][0]["tasks"] = ["Write 3 files", "Write 2 more"]
    out = run_moves(data)
    assert [p.title for p in out[2].phases] == ["Run and compare"]


def test_a_deliverable_with_a_number_from_nowhere_drops_the_phase() -> None:
    data = moves()
    data["moves"][2]["phases"][1]["deliverable"] = "A table of 3 versions"
    out = run_moves(data)
    assert [p.title for p in out[2].phases] == ["Write the versions"]


def test_a_move_with_no_phase_left_is_dropped() -> None:
    data = moves()
    for phase in data["moves"][2]["phases"]:
        phase["hours"] = 0
    out = run_moves(data)
    assert [m.id for m in out] == ["m1", "m2"]


def test_only_the_first_three_surviving_moves_are_kept() -> None:
    data = moves()
    data["moves"] = [*data["moves"], copy.deepcopy(data["moves"][2]) | {"title": "A fourth"}]
    out = run_moves(data)
    assert len(out) == 3 and all(m.title != "A fourth" for m in out)


def test_lengths_are_clipped_everywhere() -> None:
    data = moves()
    move = data["moves"][0]
    move["title"] = "t " * 100
    move["body"] = "b " * 200
    move["have"][0]["text"] = "h " * 200
    move["phases"][0]["title"] = "p " * 100
    move["phases"][0]["tasks"][0] = "k " * 200
    move["phases"][0]["deliverable"] = "d " * 100
    out = run_moves(data)[0]
    assert len(out.title) <= TITLE_CHARS and len(out.body) <= BODY_CHARS
    assert len(out.have[0].text) <= 160
    assert len(out.phases[0].title) <= TITLE_CHARS
    assert len(out.phases[0].tasks[0]) <= TASK_CHARS and len(out.phases[0].deliverable) <= DELIVERABLE_CHARS


def test_the_eta_number_is_allowed_in_moves() -> None:
    data = moves()
    data["moves"][0]["body"] = "You get there in about 9 months of evenings."
    assert run_moves(data)[0].body.startswith("You get there")


def test_a_malformed_moves_answer_gives_no_move() -> None:
    assert run_moves({"moves": "nope"}) == []
    assert run_moves({}) == []
    assert run_moves({"moves": [{"title": "x"}]}) == []


# ───────────────────────── headline ─────────────────────────


def test_a_good_headline_is_proposed_with_its_citations() -> None:
    out = run_headline(headline())
    assert out.proposed == "Frontend Engineer · two OpenAI apps, notes-ai and Recipe Genie · Bengaluru"
    assert out.evidenceIds == ["e6", "e7", "e5"] and out.note is None
    assert out.current == "Frontend Engineer · 2.4 yrs · Bengaluru"


def test_a_headline_naming_something_the_evidence_does_not_show_is_not_kept() -> None:
    data = headline()
    data["headline"] = (
        "Frontend Engineer · built Kitebird's Prisma pipeline · Bengaluru"  # Prisma is not in e5/e6/e7
    )
    out = run_headline(data)
    assert out.proposed is None and out.note == HEADLINE_FAILED_NOTE and out.evidenceIds == ["e6", "e7", "e5"]


def test_a_headline_with_a_number_from_nowhere_is_not_kept() -> None:
    data = headline()
    data["headline"] = "Frontend Engineer · 3 OpenAI apps · Bengaluru"
    assert run_headline(data).proposed is None


def test_a_headline_may_use_numbers_and_names_from_the_role_title_and_the_current_headline() -> None:
    data = headline()
    data["headline"] = "Frontend Engineer with 2.4 yrs moving toward AI Product Engineer · Bengaluru"
    assert run_headline(data).proposed is not None


def test_the_first_word_may_be_anything_capitalised() -> None:
    data = headline()
    data["headline"] = "Builder of two OpenAI apps · Bengaluru"
    assert run_headline(data).proposed is not None


def test_a_possessive_of_an_allowed_name_passes() -> None:
    data = headline()
    data["headline"] = "Frontend Engineer · Recipe Genie's OpenAI recipes · Bengaluru"
    assert run_headline(data).proposed is not None


def test_a_headline_over_120_characters_or_without_a_citation_is_not_kept() -> None:
    data = headline()
    data["headline"] = "Frontend Engineer · " + "OpenAI " * 20
    assert len(data["headline"]) > PROPOSED_HEADLINE_CHARS and run_headline(data).proposed is None
    data = headline()
    data["evidenceIds"] = ["e404"]
    out = run_headline(data)
    assert out.proposed is None and out.evidenceIds == []


def test_a_failed_call_says_so_and_a_malformed_answer_is_not_kept() -> None:
    out = run_headline(None)
    assert out.proposed is None and out.note == HEADLINE_UNAVAILABLE_NOTE and out.evidenceIds == []
    assert run_headline({"headline": 5}).proposed is None


# ───────────────────────── clip_sentence ─────────────────────────


def test_a_long_sentence_is_cut_at_a_clause_break_and_still_ends_as_a_sentence() -> None:
    from tayyari.prepare.verify import clip_sentence

    long = (
        "You already prove design systems, motion and React; the two gaps are AI UX patterns, where your work shows "
        "only loading text and a retry-less error toast, and Figma-to-code."
    )
    out = clip_sentence(long, 160)
    assert out == (
        "You already prove design systems, motion and React; the two gaps are AI UX patterns, where your work shows "
        "only loading text and a retry-less error toast."
    )  # the last clause break that fits, closed with a full stop, never "…" mid-thought
    assert len(out) <= 160
    assert clip_sentence("Short and whole.", 160) == "Short and whole."


def test_a_long_sentence_with_no_clause_break_falls_back_to_a_word_cut() -> None:
    from tayyari.prepare.verify import clip_sentence

    out = clip_sentence("word " * 60, 50)
    assert out.endswith("…") and len(out) <= 50 and not out.endswith(" …")
