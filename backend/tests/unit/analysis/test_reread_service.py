"""An analysis that starts from a profile (`AnalyzeRequest.reread`): the same pipeline, then the person's resumes, files and
own-words lines move to the new profile in one step. A failing move leaves the new profile standing, the old rows where they
were, and says so in the closing event (`carried: false`)."""

from __future__ import annotations

from datetime import UTC, datetime

from tayyari.analysis.service import PipelineAnalysisService
from tayyari.contract import AnalyzeRequest, DoneEvent, encode_event
from tayyari.contract.requests import Reread
from tayyari.contract.stored import StoredContext
from tayyari.match.facts import user_fact
from tayyari.resume.files import ResumeText
from tayyari.resume.reread import OWN_WORDS_HEADING, SOURCE_NAME, build_source
from tayyari.sample.provider import SampleProvider
from tayyari.storage.ports import StoredResume, StoredResumeFile
from tests.unit.analysis.support import Harness, assert_skeleton, log_texts, synth_output

OLD = "1" * 32
BODY = (
    "Test Person\nFrontend Engineer, Pune\n\nExperience\n"
    "Acme Labs, Frontend Engineer, 2023 to 2026. Built the checkout in React and TypeScript, shipped weekly, "
    "mentored two interns and cut the largest bundle by forty percent.\n\nSkills\nReact, TypeScript, Node.js.\n"
)
LINE = "Ran the paid-search budget alone for two quarters"


def wired(h: Harness) -> None:
    h.service = PipelineAnalysisService(
        settings=h.settings,
        claude=h.claude,
        decisions=h.decisions,
        profiles=h.repos.profiles,
        sample=SampleProvider(),
        clock=h.clock,
        resume_files=h.repos.resume_files,
        carry=h.repos.carry,
    )


async def seed(h: Harness) -> AnalyzeRequest:
    """An old profile with a resume file, its uploaded resume (the default) and one own-words line; the request a
    re-read of it makes."""
    repos = h.repos
    await repos.profiles.save(OLD, {"id": OLD}, StoredContext(), goal=None, owner_hash=None, is_sample=False)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    await repos.resume_files.add(
        StoredResumeFile(
            id="f" * 32, user_id=None, profile_id=OLD, origin="analysis", filename="meera.txt", mime="text/plain",
            size=len(BODY), sha256="0" * 64, text=BODY, facts={}, merge=None, merged_book=None, created_at=now,
        ),
        BODY.encode(),
    )  # fmt: skip
    await repos.resumes.create(
        StoredResume(
            id="a" * 32, user_id=None, profile_id=OLD, kind="upload", role_key="", title="Resume", draft={},
            final=None, facts={}, is_default=True, version=1, created_at=now, updated_at=now,
        )
    )  # fmt: skip
    fact = user_fact(LINE, key="resume:aaa/b-exp-1.1")
    await repos.user_facts.add(None, OLD, fact, question="q", answer=LINE, match_id=None)
    source = build_source([ResumeText("f" * 32, "meera.txt", BODY, now, "analysis")], [LINE], None)
    assert source is not None
    return AnalyzeRequest(
        resume_bytes=source.text.encode(),
        resume_name=SOURCE_NAME,
        reread=Reread(profile_id=OLD, user_id=None, note=source.note),
    )


def done(events: list) -> DoneEvent:  # type: ignore[type-arg]
    last = events[-1]
    assert isinstance(last, DoneEvent)
    return last


async def test_a_reread_reads_every_block_then_carries_the_persons_things(harness: Harness) -> None:
    wired(harness)
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    req = await seed(harness)

    events = await harness.run(req)

    assert_skeleton(events)
    assert done(events).carried is True
    new_id = done(events).profile.id
    assert new_id != OLD
    # the readers and the synthesizer were shown the heading of the file and the person's own-words section, as data
    (synth_call,) = harness.claude.calls_for("synth")
    assert "Resume file: meera.txt (2026-11-01)" in synth_call.user_text
    assert OWN_WORDS_HEADING in synth_call.user_text and LINE in synth_call.user_text
    assert "Reading 1 resume file and 1 line in your own words" in log_texts(events, "resume")
    # the resumes, the file and the line now hang off the new profile; nothing was made for it from an upload
    repos = harness.repos
    assert [r.id for r in await repos.resumes.list_for_profile(new_id)] == ["a" * 32]
    assert (await repos.resumes.list_for_profile(new_id))[0].is_default
    assert [f.id for f in await repos.resume_files.list_for_profile(new_id)] == ["f" * 32]
    assert [f.text for f in await repos.user_facts.list_for_profile(new_id)] == [LINE]
    assert await repos.resumes.list_for_profile(OLD) == []
    assert await repos.resume_files.list_for_profile(OLD) == []


async def test_a_failing_move_leaves_the_new_profile_standing_and_the_old_rows_where_they_were(
    harness: Harness,
) -> None:
    wired(harness)
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))
    req = await seed(harness)
    harness.repos.carry.fail_at = "files"

    events = await harness.run(req)

    assert_skeleton(events)
    assert done(events).carried is False
    new_id = done(events).profile.id
    repos = harness.repos
    assert await repos.profiles.get_profile(new_id) is not None  # the new profile stands
    assert [r.id for r in await repos.resumes.list_for_profile(OLD)] == ["a" * 32]
    assert [f.id for f in await repos.resume_files.list_for_profile(OLD)] == ["f" * 32]
    assert [f.text for f in await repos.user_facts.list_for_profile(OLD)] == [LINE]
    assert await repos.resumes.list_for_profile(new_id) == []
    assert await repos.resume_files.list_for_profile(new_id) == []


async def test_an_upload_run_is_unchanged_it_keeps_its_file_and_says_nothing_of_carrying(
    harness: Harness,
) -> None:
    wired(harness)
    harness.script_readers()
    harness.script_synth(synth_output(["resume"]))

    events = await harness.run(AnalyzeRequest(resume_bytes=BODY.encode(), resume_name="meera.txt"))

    assert done(events).carried is None
    new_id = done(events).profile.id
    [kept] = await harness.repos.resume_files.list_for_profile(new_id)
    assert kept.origin == "analysis" and kept.filename == "meera.txt"
    assert b"carried" not in encode_event(events[-1])
