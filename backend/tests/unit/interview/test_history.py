"""A person's practice: their sessions newest first, each claim at its best across sessions (Not assessed counts lowest, the best
counts, and the date is when it was first reached), the facts they kept, and how many interviews they have left today."""

from __future__ import annotations

from tayyari.contract.interview import ClaimMark, FactRequest
from tayyari.interview.history import best_mark
from tests.unit.interview.flows import A1, FACT, ROLLBACK, end, play_headout, say, start
from tests.unit.interview.support import CTX, EVIDENCE, OTHER, RUN, SAMPLE, judged, make_rig

DAY = 24 * 3600
E3 = EVIDENCE["E3"].cite.text
E5 = EVIDENCE["E5"].cite.text
E11 = EVIDENCE["E11"].cite.text
E14 = EVIDENCE["E14"].cite.text
E19 = EVIDENCE["E19"].cite.text


async def resume_session(rig, *, shaky_first: bool = True) -> str:
    """Defend your resume, short: s1 (E3) shaky then still shaky after the push, s2 (E5) defended, s3 (E19) skipped."""
    session = await start(rig, "resume", phrase="fails", length="short")
    assert [s.sources[0].text for s in session.slots] == [E3, E5, E19]
    await say(rig, session.id, A1, verdict=judged(A1, "shaky", quote=ROLLBACK), after=30)
    await say(rig, session.id, A1, verdict=judged(A1, "shaky", quote=ROLLBACK), after=30)
    await say(rig, session.id, A1, verdict=judged(A1, "defended", quote=ROLLBACK), after=30)
    await say(rig, session.id, action="skip", after=10)
    await end(rig, session.id, script={"slots": []})
    return session.id


async def two_sessions(rig):
    first = await resume_session(rig)
    rig.clock.advance(4 * DAY)
    second, _ = await play_headout(rig)
    await end(rig, second)
    return first, second


async def test_sessions_come_newest_first_with_their_verdicts_in_slot_order() -> None:
    rig = make_rig()
    first, second = await two_sessions(rig)
    history = await rig.service.history(SAMPLE)
    assert [s.id for s in history.sessions] == [second, first]
    newest, oldest = history.sessions
    assert (newest.kind, newest.title, newest.status, newest.role) == (
        "role",
        "Frontend Engineer at Headout",
        "done",
        RUN,
    )
    assert newest.verdicts == ["defended", "shaky", "defended", "couldnt", "couldnt"]
    assert newest.counts is not None and (
        newest.counts.defended,
        newest.counts.shaky,
        newest.counts.couldnt,
    ) == (2, 1, 2)
    assert newest.createdAt.startswith("2026-10-20")
    assert (oldest.kind, oldest.title, oldest.verdicts) == (
        "resume",
        "Defend your resume",
        ["shaky", "defended", "not_assessed"],
    )
    assert oldest.role is None and oldest.createdAt.startswith("2026-10-16")


async def test_an_open_interview_is_listed_with_no_verdicts_yet() -> None:
    rig = make_rig()
    open_one = await start(rig)
    (summary,) = (await rig.service.history(SAMPLE)).sessions
    assert (summary.id, summary.status, summary.verdicts, summary.counts) == (open_one.id, "active", [], None)
    assert summary.role == RUN and summary.title == "Frontend Engineer at Headout"


async def test_each_claim_is_shown_at_its_best_with_a_mark_for_every_time_it_was_asked() -> None:
    rig = make_rig()
    first, second = await two_sessions(rig)
    claims = {c.text: c for c in (await rig.service.history(SAMPLE)).claims}
    assert set(claims) == {E3, E5, E11, E14, E19}  # a gap's evidence is not a claim; nor is a posting's ask

    rebuilt = claims[E3]  # Shaky on the 16th, Defended on the 20th: the best counts
    assert (rebuilt.source, rebuilt.best, rebuilt.on) == ("Resume", "defended", "2026-10-20")
    assert [(m.sessionId, m.date, m.verdict) for m in rebuilt.marks] == [
        (first, "2026-10-16", "shaky"),
        (second, "2026-10-20", "defended"),
    ]
    kite = claims[
        E5
    ]  # Defended on the 16th and Couldn't defend on the 20th: a lower mark later does not take it back
    assert (kite.best, kite.on) == ("defended", "2026-10-16")
    assert [m.verdict for m in kite.marks] == ["defended", "couldnt"]
    assert (claims[E11].best, claims[E11].on, len(claims[E11].marks)) == ("shaky", "2026-10-20", 1)
    assert (claims[E14].best, claims[E14].on) == ("couldnt", "2026-10-20")
    skipped = claims[E19]  # skipped: Not assessed is the lowest and never counts against anyone
    assert (skipped.best, skipped.on, [m.verdict for m in skipped.marks]) == (
        "not_assessed",
        "2026-10-16",
        ["not_assessed"],
    )


async def test_a_page_or_repo_line_is_a_claim_too_and_the_claims_come_most_recent_first() -> None:
    rig = make_rig()
    session = await start(
        rig, "resume", phrase="fails"
    )  # full: the fourth is a line from a repo, not the resume; it is never a skill the resume does not list
    page = session.slots[3].sources[0]
    assert (page.source, page.text) == ("GitHub", EVIDENCE["E1"].cite.text)
    await say(rig, session.id, action="skip")
    await end(rig, session.id, script={"slots": []})
    rig.clock.advance(2 * DAY)
    later, _ = await play_headout(rig)
    await end(rig, later)
    claims = (await rig.service.history(SAMPLE)).claims
    assert any(c.source == "GitHub" and c.text == page.text and c.best == "not_assessed" for c in claims)
    assert all(c.source != "Skills" and c.text != "RAG & retrieval" for c in claims)
    dates = [c.marks[-1].date for c in claims]
    assert dates == sorted(dates, reverse=True) and dates[0] == "2026-10-18"


async def test_the_best_mark_is_the_earliest_of_the_best() -> None:
    marks = [
        ClaimMark(sessionId="a", date="2026-10-01", verdict="shaky"),
        ClaimMark(sessionId="b", date="2026-10-05", verdict="defended"),
        ClaimMark(sessionId="c", date="2026-10-09", verdict="defended"),
        ClaimMark(sessionId="d", date="2026-10-12", verdict="couldnt"),
    ]
    assert best_mark(marks).sessionId == "b"
    assert best_mark(marks[:1]).sessionId == "a"
    only_skipped = [
        ClaimMark(sessionId="a", date="2026-10-01", verdict="not_assessed"),
        ClaimMark(sessionId="b", date="2026-10-02", verdict="couldnt"),
    ]
    assert best_mark(only_skipped).sessionId == "b"  # Couldn't defend beats Not assessed


async def test_facts_the_person_kept_are_listed_newest_first() -> None:
    rig = make_rig()
    await two_sessions(rig)
    assert (await rig.service.history(SAMPLE)).facts == []
    rig.clock.advance(DAY)
    third, _ = await play_headout(rig)
    await end(rig, third)
    await rig.service.set_fact(SAMPLE, third, FactRequest(profileId=SAMPLE.id, slotId="s3", add=True))
    history = await rig.service.history(SAMPLE)
    assert [(f.text, f.sessionId, f.slotId, f.at) for f in history.facts] == [(FACT, third, "s3", 312)]
    assert history.facts[0].date == "2026-10-21"


async def test_left_today_counts_the_interviews_started_since_midnight_utc_and_not_the_free_repeats() -> None:
    rig = make_rig()
    assert (await rig.service.history(SAMPLE)).leftToday is None  # only when the limit is given
    assert (await rig.service.history(SAMPLE, per_day=3)).leftToday == 3
    first = await start(rig)
    assert (await rig.service.history(SAMPLE, per_day=3)).leftToday == 2
    await rig.service.end(SAMPLE, first.id, CTX)
    await rig.service.again(SAMPLE, first.id, "s1")  # asking one question again is free
    await rig.service.again(SAMPLE, first.id, "s2")
    assert (await rig.service.history(SAMPLE, per_day=3)).leftToday == 2
    await start(rig)
    await start(rig)
    assert (await rig.service.history(SAMPLE, per_day=3)).leftToday == 0
    await start(rig)
    assert (await rig.service.history(SAMPLE, per_day=3)).leftToday == 0  # never below zero
    rig.clock.advance(DAY)  # the next day (UTC)
    assert (await rig.service.history(SAMPLE, per_day=3)).leftToday == 3


async def test_the_history_is_the_profiles_own_and_as_long_as_the_setting_allows() -> None:
    from tayyari.core.settings import InterviewSettings

    rig = make_rig(settings=InterviewSettings(history_limit=2))
    await two_sessions(rig)
    await start(rig)
    assert len((await rig.service.history(SAMPLE)).sessions) == 2  # the newest two
    other = await rig.service.history(OTHER)
    assert other.sessions == [] and other.claims == [] and other.facts == [] and other.leftToday is None


async def test_a_debrief_that_cannot_be_read_is_left_out_not_a_crash() -> None:
    from dataclasses import replace

    rig = make_rig()
    first, second = await two_sessions(rig)
    rig.repo.rows[first] = replace(rig.repo.rows[first], debrief={"sessionId": first})
    history = await rig.service.history(SAMPLE)
    assert [s.id for s in history.sessions] == [second]
    view = await rig.service.view(
        SAMPLE, first
    )  # the session itself still opens; its debrief is rebuilt from code
    assert view is not None and view.debrief is not None and view.debrief.sessionId == first
    assert view.debrief.results[0].verdict == "shaky"
