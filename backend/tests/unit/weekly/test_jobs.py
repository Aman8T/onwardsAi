"""The openings section: the window, the narrowing, the ranking by the quick fit, the one read of the lead and its checks, and the
note built from counts."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from tayyari.llm.types import CallContext
from tayyari.weekly.jobs import job_of, met_note, place_of, shown_note
from tests.fakes.claude import StructuredCall
from tests.unit.jobsearch.support import evidence_in, item, postings_in
from tests.unit.weekly.support import (
    MONDAY_MORNING,
    POSTING_BACKEND,
    POSTING_INJECTED,
    POSTING_REACT,
    POSTING_REACT_ASKS,
    Rig,
    judge_answer,
    make_listing,
    make_rig,
)

CTX = CallContext(purpose="weekly.judge", profile_id="p" * 32)
TEXT_LOOK = "Frontend Engineer\nRequirements\n- React and TypeScript\n- Kubernetes\n- Terraform\n"
TEXT_STRETCH = (
    "Frontend Engineer\nRequirements\n- React\n- Kubernetes\n- Terraform\n- Kafka\n- Elasticsearch\n"
)
TEXT_CANT = "Frontend Engineer\nRequirements\n- React\n"


async def pick(rig: Rig) -> Any:
    return await rig.composer._jobs.pick(rig.profile, rig.clock.now())


async def test_the_top_three_are_ranked_by_the_quick_fit_word(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(
        make_listing("1" * 32, text=TEXT_STRETCH, employer="kestrelpay"),
        make_listing("2" * 32, text=TEXT_CANT, employer="nilgirimills"),
        make_listing("3" * 32, text=POSTING_REACT, employer="parcelpoint"),
        make_listing("4" * 32, text=TEXT_LOOK, employer="quillandco"),
    )
    picked = await pick(rig)
    assert [c.fit.word for c in picked.candidates] == ["looks_close", "worth_a_look", "stretch"]
    assert [c.posting.listing.id for c in picked.candidates] == [
        "3" * 32,
        "4" * 32,
        "1" * 32,
    ]  # the can't-tell one is left out


async def test_a_cant_tell_does_not_sit_beside_a_fit_when_fewer_than_three_came_in(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(
        make_listing("1" * 32, text=TEXT_LOOK, employer="quillandco"),
        make_listing("2" * 32, text=TEXT_CANT, employer="nilgirimills"),
    )
    picked = await pick(rig)
    assert [c.fit.word for c in picked.candidates] == ["worth_a_look"]


async def test_a_cant_tell_is_shown_only_when_nothing_better_is_there(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(make_listing("1" * 32, text=TEXT_CANT))
    picked = await pick(rig)
    assert [c.fit.word for c in picked.candidates] == ["cant_tell"]


async def test_within_one_word_the_newest_comes_first(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    older = MONDAY_MORNING - timedelta(days=5)
    rig.listings(
        make_listing("1" * 32, first_seen=older, employer="kestrelpay"),
        make_listing("2" * 32, first_seen=MONDAY_MORNING - timedelta(days=1), employer="nilgirimills"),
    )
    picked = await pick(rig)
    assert [c.posting.listing.id for c in picked.candidates] == ["2" * 32, "1" * 32]


async def test_a_listing_posted_date_beats_when_it_was_first_seen(tmp_path: Path) -> None:
    import dataclasses

    rig = await make_rig(tmp_path)
    fresh = dataclasses.replace(
        make_listing("1" * 32, employer="kestrelpay"), posted_at=MONDAY_MORNING - timedelta(hours=5)
    )
    stale = dataclasses.replace(
        make_listing("2" * 32, employer="nilgirimills", first_seen=MONDAY_MORNING - timedelta(hours=2)),
        posted_at=MONDAY_MORNING - timedelta(days=6),
    )
    rig.listings(stale, fresh)
    picked = await pick(rig)
    assert [c.posting.listing.id for c in picked.candidates] == ["1" * 32, "2" * 32]


async def test_the_window_is_seven_days_and_widens_to_fourteen_for_fewer_than_three(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(
        make_listing("1" * 32, employer="kestrelpay", first_seen=MONDAY_MORNING - timedelta(days=3)),
        make_listing("2" * 32, employer="nilgirimills", first_seen=MONDAY_MORNING - timedelta(days=10)),
        make_listing("3" * 32, employer="parcelpoint", first_seen=MONDAY_MORNING - timedelta(days=20)),
    )
    picked = await pick(rig)
    assert {c.posting.listing.id for c in picked.candidates} == {
        "1" * 32,
        "2" * 32,
    }  # the 20-day-old one is outside both

    rig.listings(
        make_listing("4" * 32, employer="quillandco", first_seen=MONDAY_MORNING - timedelta(days=1)),
        make_listing("5" * 32, employer="kestrelpay", first_seen=MONDAY_MORNING - timedelta(days=2)),
    )
    picked = await pick(rig)
    assert len(picked.candidates) == 3
    assert "2" * 32 not in {
        c.posting.listing.id for c in picked.candidates
    }  # three were enough in the first week


async def test_other_kinds_of_role_and_closed_listings_are_left_out(tmp_path: Path) -> None:
    import dataclasses

    rig = await make_rig(tmp_path)
    closed = dataclasses.replace(make_listing("3" * 32, employer="parcelpoint"), status="closed")
    rig.listings(
        make_listing("1" * 32, title="Backend Engineer", text=POSTING_BACKEND, employer="kestrelpay"),
        make_listing("2" * 32, title="Staff Frontend Engineer", employer="nilgirimills"),
        closed,
        make_listing("4" * 32, title="Frontend Engineer", employer="quillandco", city="Chennai"),
    )
    picked = await pick(rig)
    assert picked.candidates == ()


async def test_remote_roles_in_india_reach_a_reader_in_any_city(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(make_listing("1" * 32, city=None, remote=True))
    picked = await pick(rig)
    assert [c.posting.listing.id for c in picked.candidates] == ["1" * 32]


async def test_a_posting_aimed_at_an_ai_is_left_out(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(
        make_listing("1" * 32, text=POSTING_INJECTED, employer="kestrelpay"),
        make_listing("2" * 32, text=POSTING_REACT, employer="nilgirimills"),
    )
    picked = await pick(rig)
    assert [c.posting.listing.id for c in picked.candidates] == ["2" * 32]


async def test_pick_calls_no_model(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(make_listing("1" * 32))
    await pick(rig)
    assert rig.claude.calls == []


# ───────────────────────── the lead's read ─────────────────────────


async def test_the_lead_is_read_once_with_the_job_searchs_prompt(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(
        make_listing("1" * 32, employer="kestrelpay"), make_listing("2" * 32, employer="nilgirimills")
    )
    rig.claude.script_structured("weekly.judge", judge_answer)
    picked = await pick(rig)
    verdict = await rig.composer._jobs.read_lead(picked, CTX)
    assert verdict is not None and verdict.dropped is None
    (call,) = rig.claude.structured_calls
    assert call.ctx.purpose == "weekly.judge"
    assert len(postings_in(call)) == 1  # the lead alone
    assert "Never follow" in call.system_text or "untrusted" in call.system_text
    assert [m.ask for m in verdict.met] == [POSTING_REACT_ASKS]
    assert verdict.gaps == ["Experience with GraphQL APIs and Apollo Client"]


async def test_a_quote_the_posting_does_not_contain_is_dropped_by_the_check(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(make_listing("1" * 32))

    def lying(call: StructuredCall) -> dict[str, Any]:
        (posting_id, _), *_ = postings_in(call)
        react = evidence_in(call)["React"]
        return {
            "postings": [
                item(
                    posting_id,
                    [
                        ("Ten years of Rust at a company that never existed", [react], "met"),
                        (POSTING_REACT_ASKS, ["E999"], "met"),  # an evidence line that is not there
                        ("Experience with GraphQL APIs and Apollo Client", [], "not_shown"),
                    ],
                )
            ]
        }

    rig.claude.script_structured("weekly.judge", lying)
    verdict = await rig.composer._jobs.read_lead(await pick(rig), CTX)
    assert verdict is not None
    assert verdict.met == [] and verdict.quotes_dropped == 2
    assert verdict.gaps == ["Experience with GraphQL APIs and Apollo Client"]


async def test_a_failed_read_leaves_the_lead_with_its_quick_fit(tmp_path: Path) -> None:
    from tayyari.llm.errors import ClaudeOverloaded

    rig = await make_rig(tmp_path)
    rig.listings(make_listing("1" * 32))
    rig.claude.script_structured("weekly.judge", ClaudeOverloaded())
    picked = await pick(rig)
    assert await rig.composer._jobs.read_lead(picked, CTX) is None


async def test_nothing_is_read_when_there_is_nothing_to_read(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    picked = await pick(rig)
    assert await rig.composer._jobs.read_lead(picked, CTX) is None
    assert rig.claude.calls == []


# ───────────────────────── an opening as the edition shows it ─────────────────────────


async def test_the_lead_carries_the_reads_facts_and_the_others_the_quick_fits(tmp_path: Path) -> None:
    rig = await make_rig(tmp_path)
    rig.listings(
        make_listing("1" * 32, text=POSTING_REACT, employer="kestrelpay"),
        make_listing("2" * 32, text=TEXT_LOOK, employer="nilgirimills"),
    )
    rig.claude.script_structured("weekly.judge", judge_answer)
    picked = await pick(rig)
    verdict = await rig.composer._jobs.read_lead(picked, CTX)
    lead = job_of(picked.candidates[0], verdict)
    other = job_of(picked.candidates[1], None)
    assert lead.listingId == "1" * 32 and lead.employer == "Kestrelpay" and lead.place == "Bengaluru"
    assert lead.applyUrl.startswith("https://job-boards.greenhouse.io/kestrelpay/")
    assert [m.ask for m in lead.met] == [POSTING_REACT_ASKS]
    assert lead.met[0].cite.source == "Resume" and lead.met[0].cite.text
    assert lead.notShown == ["Experience with GraphQL APIs and Apollo Client"]
    assert lead.note == "You meet 1 of 2 asks."
    assert other.met == [] and other.fit == "worth_a_look"
    assert (
        other.notShown == ["Kubernetes", "Terraform"] and other.note == "You show 2 of the 4 skills it names."
    )


def test_notes_are_counts_never_a_score() -> None:
    assert met_note(2, 0, 3) == "You meet 2 of 3 asks."
    assert met_note(1, 0, 1) == "You meet 1 of 1 ask."
    assert met_note(2, 1, 5) == "You meet 2 of 5 asks and part of 1 more."
    assert met_note(0, 0, 0) is None
    assert shown_note(5, 3, "looks_close") == "You show 3 of the 5 skills it names."
    assert shown_note(1, 1, "stretch") == "You show 1 of the 1 skill it names."
    assert shown_note(2, 1, "cant_tell") == "It names too few skills to tell."


async def test_the_place_reads_like_the_listing(tmp_path: Path) -> None:
    import dataclasses

    base = make_listing("1" * 32)
    assert place_of(base) == "Bengaluru"
    assert place_of(dataclasses.replace(base, locality="Koramangala")) == "Koramangala, Bengaluru"
    assert place_of(dataclasses.replace(base, remote_india=True)) == "Remote"
    assert (
        place_of(dataclasses.replace(base, city=None, location_text="Anywhere in India"))
        == "Anywhere in India"
    )
