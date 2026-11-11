"""A second resume file through the real app over a real SQLite file: `POST /api/resumes/upload`, `GET /api/resumes/files`,
`GET /api/resumes/{id}/original`, the blocks that carry what the file said, who may call, the limits, and the analysis
keeping the file it was made from. Only the network edges are faked (Claude reads the resume it is shown)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest

from tayyari.core.limits import MAX_RESUME_BYTES
from tayyari.core.settings import Settings
from tests.integration.scenarios import run_composite
from tests.integration.support import FakeWeb, RecordingClaude, running
from tests.integration.test_resume_routes import PID, ROLE, Account, Rig
from tests.unit.match.support import meera, script_pipeline
from tests.unit.resume.file_rig import scripted
from tests.unit.resume.merge_support import (
    LIFECYCLE,
    MENTORED,
    REWORDED,
    TESTS,
    TESTS_40,
    UNRELATED,
    resume_text,
)

MakeSettings = Callable[..., Settings]
SECOND = resume_text({LIFECYCLE: REWORDED, TESTS: TESTS_40, MENTORED: UNRELATED}).encode()
RELEVANCE: dict[str, Any] = {"scores": [{"factId": "exp-1.3", "score": 96, "requirementIds": ["r1"]}]}


@pytest.fixture(autouse=True)
def plain_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scripted extraction reads resumes laid out like the personas' fixtures (wide gaps in a job's header), which the
    real reader's whitespace clean-up narrows: text files are handed over as they are."""
    from tayyari.ingest.resume import parse_resume as real

    async def read(data: bytes, name: str) -> str:
        return data.decode("utf-8") if name.endswith(".txt") else await real(data, name)

    monkeypatch.setattr("tayyari.resume.service.parse_resume", read)


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**overrides: Any) -> AsyncIterator[Rig]:
        persona = meera()
        claude = RecordingClaude()
        script_pipeline(claude, persona)
        claude.script_structured("match.facts", scripted, repeat=True)  # type: ignore[arg-type]
        claude.script_structured("match.relevance", RELEVANCE, repeat=True)
        values: dict[str, Any] = {
            "auth": {"public_url": "https://tayyari.example"},
            "match": {"allow_anonymous": "false"},
            **overrides,
        }
        async with running(build_settings(**values), claude=claude) as env:
            yield Rig(env, claude, persona)

    return run


def post_file(
    rig: Rig,
    who: Account,
    name: str = "v2.txt",
    data: bytes = SECOND,
    kind: str | None = "text/plain",
    pid: str | None = None,
) -> Any:
    return rig.post(
        who, f"/api/resumes/upload?profile={pid or who.profile_id}", files={"resume": (name, data, kind)}
    )


async def test_a_second_file_merges_and_answers_resume_and_report(start: Any) -> None:
    async with start() as rig:
        who = await rig.account()
        first = await rig.upload(who)
        response = await post_file(rig, who)
        assert response.status_code == 200, response.text
        body = response.json()
        assert response.headers["cache-control"] == "no-store"
        assert set(body) == {"resume", "merge"}
        merge = body["merge"]
        assert merge["filename"] == "v2.txt"
        assert [i["text"] for i in merge["disagree"]] == [TESTS_40]
        assert merge["disagree"][0]["differs"] == ["figure"] and merge["disagree"][0]["existing"] == TESTS
        assert [i["text"] for i in merge["near"]] == [REWORDED]
        assert [i["text"] for i in merge["new"]] == [UNRELATED] and merge["new"][0]["blockId"].startswith(
            "m-"
        )
        assert len(merge["same"]) > 20
        summary = body["resume"]["resume"]
        assert summary["kind"] == "upload" and summary["title"] == "v2" and summary["isDefault"] is False
        assert summary["id"] != first["id"]
        texts = [b["text"] for b in body["resume"]["variant"]["experience"][0]["bullets"]]
        assert TESTS_40 in texts and TESTS not in texts
        assert rig.calls("match.facts") >= 1

        listing = await rig.listing(who)
        assert [(r["kind"], r["isDefault"]) for r in listing["resumes"]] == [
            ("upload", True),
            ("upload", False),
        ]
        assert listing["blocks"] == 15


async def test_the_blocks_carry_other_wordings_and_disagreements(start: Any) -> None:
    async with start() as rig:
        who = await rig.account()
        await rig.upload(who)
        await post_file(rig, who)
        response = await rig.get(who, f"/api/resumes/blocks?profile={who.profile_id}")
        assert response.status_code == 200
        blocks = {b["fact"]["id"]: b for b in response.json()["blocks"]}
        assert blocks["exp-1.1"]["wordings"][0]["text"] == REWORDED
        assert blocks["exp-1.1"]["wordings"][0]["filename"] == "v2.txt"
        assert blocks["exp-1.3"]["disagreements"][0]["differs"] == ["figure"]
        assert blocks["exp-1.3"]["fact"]["text"] == TESTS
        assert "wordings" not in blocks["exp-1.2"] and "disagreements" not in blocks["exp-1.2"]


async def test_the_files_are_listed_and_the_original_is_downloaded(start: Any) -> None:
    async with start() as rig:
        who = await rig.account()
        await rig.upload(who)
        uploaded = (await post_file(rig, who)).json()
        files = await rig.get(who, f"/api/resumes/files?profile={who.profile_id}")
        assert files.status_code == 200
        [row] = files.json()["files"]
        assert (row["filename"], row["origin"], row["mime"], row["size"]) == (
            "v2.txt",
            "upload",
            "text/plain",
            len(SECOND),
        )
        assert row["resumeId"] == uploaded["resume"]["resume"]["id"]
        assert row["counts"]["disagree"] == 1 and row["counts"]["near"] == 1 and row["counts"]["new"] == 1

        original = await rig.get(who, f"/api/resumes/{row['resumeId']}/original")
        assert original.status_code == 200
        assert original.content == SECOND
        assert original.headers["content-type"].startswith("text/plain")
        assert original.headers["content-disposition"].startswith('attachment; filename="v2.txt"')
        assert original.headers["x-content-type-options"] == "nosniff"


async def test_a_resume_with_no_stored_file_has_no_original(start: Any) -> None:
    async with start() as rig:
        who = await rig.account()
        first = await rig.upload(who)  # made from the profile's sources: nothing was stored for it
        path = await rig.post(who, f"/api/resumes/path?profile={who.profile_id}", json={"roleId": ROLE})
        for resume_id in (first["id"], path.json()["resume"]["id"]):
            response = await rig.get(who, f"/api/resumes/{resume_id}/original")
            assert response.status_code == 404
            assert response.json()["code"] == "no_original"


async def test_another_accounts_profile_is_a_404_and_a_visitor_a_401(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        bob = await rig.account("bob@example.com", "bob-profile")
        await rig.upload(ana)
        assert (await post_file(rig, bob, pid=ana.profile_id)).status_code == 404
        assert (await rig.get(bob, f"/api/resumes/files?profile={ana.profile_id}")).status_code == 404
        uploaded = (await post_file(rig, ana)).json()
        assert (
            await rig.get(bob, f"/api/resumes/{uploaded['resume']['resume']['id']}/original")
        ).status_code == 404
        anonymous = await rig.client.post(
            f"/api/resumes/upload?profile={PID}", files={"resume": ("v2.txt", SECOND, "text/plain")}
        )
        assert anonymous.status_code == 401
        assert (await rig.client.get(f"/api/resumes/files?profile={PID}")).status_code == 401
        assert (await rig.client.get("/api/resumes/abc/original")).status_code == 401


async def test_a_file_over_the_limit_a_wrong_type_and_no_file_are_refused_before_any_call(start: Any) -> None:
    async with start() as rig:
        who = await rig.account()
        await rig.upload(who)
        before = rig.calls("match.facts")
        big = await post_file(rig, who, "big.txt", b"x" * (MAX_RESUME_BYTES + 1))
        assert big.status_code == 413
        wrong = await post_file(rig, who, "notes.exe", b"MZ" + b"x" * 400, "application/octet-stream")
        assert wrong.status_code == 400
        empty = await post_file(rig, who, "empty.txt", b"")
        assert empty.status_code == 400
        nothing = await rig.post(who, f"/api/resumes/upload?profile={who.profile_id}", data={"x": "y"})
        assert nothing.status_code == 400
        short = await post_file(rig, who, "short.txt", b"too short to be a resume")
        assert short.status_code == 400 and short.json()["code"] == "unreadable_file"
        assert rig.calls("match.facts") == before


async def test_the_profile_must_be_named(start: Any) -> None:
    async with start() as rig:
        who = await rig.account()
        response = await rig.post(
            who, "/api/resumes/upload", files={"resume": ("v2.txt", SECOND, "text/plain")}
        )
        assert response.status_code == 400
        missing = await post_file(rig, who, pid="nope-not-a-profile")
        assert missing.status_code == 404


async def test_the_analysis_keeps_the_file_it_was_made_from(
    build_settings: MakeSettings, web: FakeWeb
) -> None:
    async with run_composite(build_settings(), web) as c:
        files = await c.env.container.repos.resume_files.list_for_profile(c.profile.id)
        [row] = files
        data = await c.env.container.repos.resume_files.get_data(row.id)
    assert (row.origin, row.filename, row.merge, row.merged_book) == ("analysis", "cv.docx", None, None)
    assert row.mime.endswith("wordprocessingml.document") and row.text
    assert data is not None and data[:2] == b"PK" and row.size == len(data)


async def test_a_failure_to_keep_the_file_never_fails_the_analysis(
    build_settings: MakeSettings, web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(self: Any, item: Any, data: bytes) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr("tayyari.storage.repos.resume_files.SqlResumeFileRepo.add", broken)
    async with run_composite(build_settings(), web) as c:
        files = await c.env.container.repos.resume_files.list_for_profile(c.profile.id)
        stored = await c.env.client.get(f"/api/profile/{c.profile.id}")
    assert c.response.status_code == 200 and stored.status_code == 200
    assert files == []
