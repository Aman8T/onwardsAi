"""A second resume file in `ResumeService`: read by the analysis's own reader and extractor, merged by code, kept with its
bytes, made a resume of its own; blocks that carry other wordings and disagreements; the original; the texts."""

from __future__ import annotations

import pytest

from tayyari.resume.arrange import upload_variant
from tayyari.resume.errors import NoOriginal, TooManyFiles, UnreadableFile
from tests.unit.resume.file_rig import file_rig
from tests.unit.resume.merge_support import (
    LIFECYCLE,
    MENTORED,
    REWORDED,
    TESTS,
    TESTS_40,
    UNRELATED,
    resume_text,
)
from tests.unit.resume.support import lines_of


@pytest.fixture(autouse=True)
def plain_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scripted extraction reads resumes the way the personas' fixtures are laid out (wide gaps in a job's header),
    which the real reader's whitespace clean-up would narrow: text files are handed over as they are. A binary file still
    goes through the real reader."""
    from tayyari.ingest.resume import parse_resume as real

    async def read(data: bytes, name: str) -> str:
        return data.decode("utf-8") if name.endswith(".txt") else await real(data, name)

    monkeypatch.setattr("tayyari.resume.service.parse_resume", read)


SECOND = resume_text({LIFECYCLE: REWORDED, TESTS: TESTS_40, MENTORED: UNRELATED})
THIRD = resume_text({MENTORED: "Hosted a weekly growth clinic for the sales team."})


async def test_a_second_file_merges_and_becomes_its_own_resume() -> None:
    r = file_rig()
    await r.listing_first()
    made = r.extractions()
    got = await r.upload("meera-v2.txt", SECOND.encode())

    report = got.merge
    assert [i.text for i in report.disagree] == [TESTS_40]
    assert [i.text for i in report.near] == [REWORDED]
    assert [i.text for i in report.new] == [UNRELATED]
    assert report.filename == "meera-v2.txt" and len(report.fileId) == 32

    summary = got.resume.resume
    assert (summary.kind, summary.title, summary.isDefault, summary.state) == (
        "upload",
        "meera-v2",
        False,
        "final",
    )
    texts = [b.text for b in lines_of(got.resume.variant)]
    assert TESTS_40 in texts and REWORDED in texts and TESTS not in texts  # its own words
    stored = await r.repos.resumes.get(summary.id)
    assert stored is not None and stored.role_key == report.fileId
    # the default did not move
    listing = await r.service.listing(r.holder, r.persona.profile, r.persona.context, r.ctx())
    assert [s.isDefault for s in listing.resumes] == [True, False]
    assert r.extractions() == made + 1  # one call for the one source of the file


async def test_the_file_is_kept_with_its_bytes_text_facts_and_report() -> None:
    r = file_rig()
    got = await r.upload("v2.txt", SECOND.encode(), "text/plain")
    row = await r.repos.resume_files.get(got.merge.fileId)
    assert row is not None
    assert (row.origin, row.filename, row.mime, row.size, row.profile_id) == (
        "upload",
        "v2.txt",
        "text/plain",
        len(SECOND.encode()),
        "a" * 32,
    )
    assert len(row.sha256) == 64 and row.merge is not None and row.merged_book is not None
    assert row.facts["facts"] and UNRELATED in row.text
    assert await r.repos.resume_files.get_data(row.id) == SECOND.encode()


async def test_every_resume_function_sees_the_union_with_wordings_and_disagreements() -> None:
    r = file_rig()
    await r.upload("v2.txt", SECOND.encode())
    blocks = await r.service.blocks(r.holder, r.persona.profile, r.persona.context, r.ctx())
    by_id = {b.fact.id: b for b in blocks.blocks}
    assert by_id["exp-1.1"].wordings is not None and by_id["exp-1.1"].wordings[0].text == REWORDED
    assert by_id["exp-1.1"].wordings[0].filename == "v2.txt" and by_id["exp-1.1"].wordings[0].resumeId
    clash = by_id["exp-1.3"].disagreements
    assert clash is not None and clash[0].text == TESTS_40 and clash[0].differs == ["figure"]
    assert by_id["exp-1.3"].fact.text == TESTS  # the block keeps the words it had
    assert by_id["exp-1.2"].wordings is None and by_id["exp-1.2"].disagreements is None
    added = [b for b in blocks.blocks if b.fact.text == UNRELATED]
    assert len(added) == 1 and added[0].entryFactId == "exp-1" and added[0].fact.id.startswith("m-")
    # the list counts the same blocks
    listing = await r.service.listing(r.holder, r.persona.profile, r.persona.context, r.ctx())
    assert listing.blocks == len(blocks.blocks) == 15


async def test_block_ids_do_not_change_when_a_third_file_arrives() -> None:
    r = file_rig()
    await r.upload("v2.txt", SECOND.encode())
    before = await r.service.blocks(r.holder, r.persona.profile, r.persona.context, r.ctx())
    await r.upload("v3.txt", THIRD.encode())
    after = await r.service.blocks(r.holder, r.persona.profile, r.persona.context, r.ctx())
    ids_before = [b.fact.id for b in before.blocks]
    assert [b.fact.id for b in after.blocks if b.fact.id in ids_before] == ids_before
    assert len(after.blocks) == len(before.blocks) + 1  # only the new line


async def test_the_files_resume_is_upload_variant_over_its_facts_in_its_order() -> None:
    r = file_rig()
    got = await r.upload("v2.txt", SECOND.encode())
    stored = await r.repos.resumes.get(got.resume.resume.id)
    assert stored is not None
    from tayyari.match.models import FactBook

    book = FactBook.model_validate(stored.facts)
    assert [b.text for b in lines_of(upload_variant(book))] == [b.text for b in lines_of(got.resume.variant)]
    order = [b.text for b in lines_of(got.resume.variant) if b.text in (REWORDED, TESTS_40, UNRELATED)]
    assert order == [REWORDED, TESTS_40, UNRELATED]


async def test_the_same_bytes_again_answer_with_the_first_result_and_no_model_call() -> None:
    r = file_rig()
    first = await r.upload("v2.txt", SECOND.encode())
    calls = r.extractions()
    again = await r.upload("copy.txt", SECOND.encode())
    assert again.resume.resume.id == first.resume.resume.id and again.merge == first.merge
    assert r.extractions() == calls
    assert len(await r.repos.resume_files.list_for_profile("a" * 32)) == 1


async def test_the_original_of_an_uploaded_resume_and_no_original_for_the_rest() -> None:
    r = file_rig()
    first = (await r.listing_first()).resumes[0]
    got = await r.upload("v2.txt", SECOND.encode())
    stored = await r.repos.resumes.get(got.resume.resume.id)
    assert stored is not None
    original = await r.service.original(stored)
    assert (original.data, original.mime, original.filename) == (SECOND.encode(), "text/plain", "v2.txt")
    # the first resume has no stored bytes until the analysis kept them
    with pytest.raises(NoOriginal):
        await r.service.original(await r.repos.resumes.get(first.id))  # type: ignore[arg-type]
    path = await r.path()
    with pytest.raises(NoOriginal):
        await r.service.original(await r.repos.resumes.get(path.resume.id))  # type: ignore[arg-type]


async def test_all_resume_texts_newest_first_and_the_analysis_file_is_the_first_resumes_original() -> None:
    r = file_rig()
    await r.listing_first()
    await r.repos.resume_files.add(r.analysis_file(), b"%PDF-1.4 fake")
    await r.upload("v2.txt", SECOND.encode())
    await r.upload("v3.txt", THIRD.encode())
    texts = await r.service.all_resume_texts("a" * 32)
    assert [t.filename for t in texts] == ["v3.txt", "v2.txt", "analysis.pdf"]
    assert texts[0].text and texts[0].created_at > texts[1].created_at > texts[2].created_at
    first = await r.repos.resumes.find("a" * 32, "upload", "")
    assert first is not None
    original = await r.service.original(first)
    assert (original.filename, original.mime, original.data) == (
        "analysis.pdf",
        "application/pdf",
        b"%PDF-1.4 fake",
    )
    files = await r.service.file_list(r.holder)
    assert [(f.filename, f.origin, f.counts is None) for f in files.files] == [
        ("analysis.pdf", "analysis", True),
        ("v2.txt", "upload", False),
        ("v3.txt", "upload", False),
    ]
    assert files.files[1].counts is not None and files.files[1].counts.disagree == 1
    assert all(f.resumeId for f in files.files)


async def test_a_file_without_text_or_without_facts_is_refused_before_any_call() -> None:
    r = file_rig()
    with pytest.raises(UnreadableFile):
        await r.upload("blank.txt", b"too short")
    assert r.extractions() == 0
    with pytest.raises(UnreadableFile):
        await r.upload("x.pdf", b"%PDF-1.4 not really a pdf" + b" " * 300)
    assert r.extractions() == 0


async def test_a_profile_keeps_at_most_so_many_files() -> None:
    from tayyari.core.limits import MAX_RESUME_FILES_PER_PROFILE

    r = file_rig()
    for n in range(MAX_RESUME_FILES_PER_PROFILE):
        await r.upload(
            f"v{n}.txt", resume_text({MENTORED: f"Wrote guide number {n} for new hires."}).encode()
        )
    with pytest.raises(TooManyFiles):
        await r.upload("one-more.txt", SECOND.encode())
