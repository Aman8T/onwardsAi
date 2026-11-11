"""The merge of a second resume file into the person's blocks, by code: each section, the known-bad pairs, and ids that do
not move when another file arrives."""

from __future__ import annotations

from typing import Any

from tayyari.resume.arrange import upload_variant
from tayyari.resume.merge import MergeResult, merge, relate
from tests.unit.resume.merge_support import (
    FILE_ID,
    LIFECYCLE,
    MENTORED,
    OTHER_ID,
    REWORDED,
    TESTS,
    TESTS_40,
    UNRELATED,
    book_of,
    existing_book,
    resume_text,
)
from tests.unit.resume.support import lines_of


def _sections(result: MergeResult) -> dict[str, list[Any]]:
    r = result.report
    return {"same": r.same, "new": r.new, "near": r.near, "disagree": r.disagree}


def _merge(content: str, into: Any = None, file_id: str = FILE_ID) -> MergeResult:
    return merge(into or existing_book(), book_of(content, file_id), file_id=file_id, filename="b.txt")


def test_the_same_file_is_all_same() -> None:
    new = book_of(resume_text())
    got = _sections(merge(existing_book(), new, file_id=FILE_ID, filename="again.txt"))
    assert len(got["same"]) == len(new.facts)
    assert not got["new"] and not got["near"] and not got["disagree"]


def test_every_fact_is_in_exactly_one_section() -> None:
    new = book_of(resume_text({LIFECYCLE: REWORDED, TESTS: TESTS_40, MENTORED: UNRELATED}))
    got = _sections(merge(existing_book(), new, file_id=FILE_ID, filename="b.txt"))
    assert sum(len(v) for v in got.values()) == len(new.facts)
    texts = [i.text for v in got.values() for i in v]
    assert sorted(texts) == sorted(f.text for f in new.facts)


def test_a_bullet_that_differs_only_in_a_number_is_a_disagreement_never_same() -> None:
    got = _sections(_merge(resume_text({TESTS: TESTS_40})))
    assert [i.text for i in got["disagree"]] == [TESTS_40]
    item = got["disagree"][0]
    assert item.differs == ["figure"]
    assert item.existing == TESTS
    assert item.blockId == "exp-1.3"
    assert all(i.text != TESTS_40 for i in got["same"] + got["near"] + got["new"])


def test_a_reworded_bullet_is_near_never_new() -> None:
    got = _sections(_merge(resume_text({LIFECYCLE: REWORDED})))
    assert [i.text for i in got["near"]] == [REWORDED]
    assert got["near"][0].existing == LIFECYCLE and got["near"][0].blockId == "exp-1.1"
    assert not got["new"] and not got["disagree"]


def test_an_unrelated_bullet_is_new_under_its_entry() -> None:
    result = _merge(resume_text({MENTORED: UNRELATED}))
    [item] = result.report.new
    assert item.text == UNRELATED and item.blockId.startswith("m-")
    entry = next(e for e in result.merged.entries if e.factId == "exp-1")
    assert item.blockId in entry.bulletIds
    block = result.merged.by_id()[item.blockId]
    assert block.text == UNRELATED and block.refs[0].docId == FILE_ID


def test_a_changed_title_employer_or_period_is_a_disagreement_of_the_entry() -> None:
    title = "Growth Marketing Manager                                   Pune"
    got = _sections(_merge(resume_text({title: "Head of Growth                                   Pune"})))
    assert [(i.blockId, i.differs) for i in got["disagree"]] == [("exp-1", ["title"])]
    assert any(i.blockId == "exp-1.1" for i in got["same"])  # its lines still meet their blocks

    got = _sections(_merge(resume_text({"Pinecrest Media — ": "Pinecrest Studios — "})))
    assert [(i.blockId, i.differs) for i in got["disagree"]] == [("exp-2", ["employer"])]

    got = _sections(_merge(resume_text({"Mumbai · Jun 2020 – Feb 2023": "Mumbai · Jun 2021 – Feb 2023"})))
    assert [(i.blockId, i.differs) for i in got["disagree"]] == [("exp-2", ["date"])]


def test_a_job_the_book_does_not_have_is_a_new_entry_with_new_lines() -> None:
    old = (
        "Kitchen Table Co — Marketing Associate                                       Pune · Jul 2018 – May 2020\n"
        "Home-baking startup.\n"
        "- Ran social media and email newsletters for the brand.\n"
        "- Planned and ran 12 Instagram giveaways that added 15,000 followers.\n"
    )
    new = (
        "Orchard Labs — Brand Strategist                                       Delhi · Jan 2016 – Jun 2018\n"
        "Agency.\n"
        "- Wrote positioning for 9 launches.\n"
    )
    result = _merge(resume_text({old: new}))
    heads = [i for i in result.report.new if i.kind == "experience"]
    assert len(heads) == 1 and "Orchard Labs" in heads[0].text
    entry = next(e for e in result.merged.entries if e.factId == heads[0].blockId)
    assert len(entry.bulletIds) == 1
    assert result.merged.by_id()[entry.bulletIds[0]].text.startswith("Wrote positioning")


def test_a_skill_is_same_by_its_term() -> None:
    result = _merge(resume_text())
    skills = [i for i in result.report.same if i.kind == "skill"]
    assert skills and all(i.blockId.startswith("skill-") for i in skills)


def test_the_files_own_book_keeps_its_words_under_the_blocks_ids() -> None:
    result = _merge(resume_text({TESTS: TESTS_40, LIFECYCLE: REWORDED, MENTORED: UNRELATED}))
    own = result.file_book
    assert own.by_id()["exp-1.3"].text == TESTS_40  # the existing block's id, the file's words
    assert existing_book().by_id()["exp-1.3"].text == TESTS
    lines = lines_of(upload_variant(own))
    texts = [b.text for b in lines]
    assert TESTS_40 in texts and REWORDED in texts and UNRELATED in texts and TESTS not in texts
    assert "b-exp-1.3" in [b.id for b in lines]
    assert [t for t in texts if t in (REWORDED, TESTS_40, UNRELATED)] == [REWORDED, TESTS_40, UNRELATED]


def test_merging_changes_no_old_block_and_never_its_id() -> None:
    before = existing_book()
    result = merge(
        before,
        book_of(resume_text({TESTS: TESTS_40, LIFECYCLE: REWORDED, MENTORED: UNRELATED})),
        file_id=FILE_ID,
        filename="b.txt",
    )
    assert before.model_dump() == existing_book().model_dump()  # pure
    old_ids = [f.id for f in before.facts]
    assert [f.id for f in result.merged.facts[: len(old_ids)]] == old_ids
    for fact in before.facts:
        merged = result.merged.by_id()[fact.id]
        assert merged.text == fact.text and merged.kind == fact.kind
    assert [e.factId for e in result.merged.entries[: len(before.entries)]] == [
        e.factId for e in before.entries
    ]


def test_ids_stay_put_across_a_second_and_a_third_file() -> None:
    first = _merge(resume_text({MENTORED: UNRELATED}))
    [added] = first.report.new
    third = _merge(
        resume_text({MENTORED: UNRELATED, TESTS: TESTS_40, LIFECYCLE: REWORDED}), first.merged, OTHER_ID
    )
    assert any(
        i.blockId == added.blockId for i in third.report.same
    )  # the line the first file brought is shared now
    first_ids = [f.id for f in first.merged.facts]
    assert [f.id for f in third.merged.facts[: len(first_ids)]] == first_ids
    assert third.merged.by_id()[added.blockId].text == UNRELATED
    assert {r.docId for r in third.merged.by_id()[added.blockId].refs} == {FILE_ID, OTHER_ID}
    # a new block's id depends on its content, not on which file or when
    again = _merge(resume_text({MENTORED: UNRELATED}), file_id=OTHER_ID)
    assert [i.blockId for i in again.report.new] == [added.blockId]


def test_relate_known_bad_pairs() -> None:
    one = relate(TESTS, TESTS_40)
    assert one is not None and one.section == "disagree" and one.differs == ("figure",)
    near = relate(LIFECYCLE, REWORDED)
    assert near is not None and near.section == "near"
    assert relate(TESTS, UNRELATED) is None
    same = relate(TESTS, TESTS.upper() + " ")
    assert same is not None and same.section == "same"
    year = relate(
        "Shipped the billing rebuild in FY24 for the finance team.",
        "Shipped the billing rebuild in FY25 for the finance team.",
    )
    assert year is not None and year.section == "disagree" and year.differs == ("date",)
    added = relate(
        "Ran A/B tests on landing pages and checkout.", "Ran 40 A/B tests on landing pages and checkout."
    )
    assert added is not None and added.section == "near"
