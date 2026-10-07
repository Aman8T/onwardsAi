"""``export`` on a SQLite database filled through `DecisionLogRepo.record_batch`: the corpus lines, what is left out and why."""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tayyari.cli import __main__ as cli
from tayyari.cli import export
from tayyari.contract.records import DecisionRecord, JevCallRecord, StateBlob
from tayyari.core.dburl import normalise_database_url
from tayyari.core.settings import Settings
from tayyari.decisions.recorder import make_state_blob
from tayyari.storage.db import Database
from tayyari.storage.migrate import migrate_to_head
from tayyari.storage.repos import build_repositories

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 8, 0, 0, 0, tzinfo=UTC)

FAMILY = "first_read.career_family"
DEPTH = "first_read.depth"
SKILL_PYTHON = "skill.level#python"
SKILL_SQL = "skill.level#sql"
GATE = "chat.web_gate"

FAMILY_Q: dict[str, Any] = {
    "type": "choice",
    "instructions": "Which career family does the document show?",
    "criteria": {"design_ux": "Design and UX", "engineering": None, "other_or_unclear": "None of these"},
}
DEPTH_Q: dict[str, Any] = {"type": "score", "instructions": "How deep?", "criteria": ["none", "some", "deep"]}
SKILL_Q: dict[str, Any] = {
    "type": "score",
    "instructions": {"question": "How strong?", "skill": "Python"},
    "criteria": ["none", "named only", "used once", "used repeatedly", "owned", "standout"],
}
FAMILY_ANSWER = {
    "kind": "choice",
    "choice": "design_ux",
    "probabilities": {"design_ux": 0.9},
    "confidence": 0.8,
}
DEPTH_ANSWER = {"kind": "score", "score_level": 2, "score_expected": 1.9, "confidence": 0.7}

URL_SECRET = "pa55w0rd-in-url"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


async def open_db(path: Path) -> Database:
    db = Database(normalise_database_url(sqlite_url(path), data_dir=path.parent, env="test"))
    await migrate_to_head(db)
    return db


def call(state: StateBlob, pack: str, questions: dict[str, Any], **overrides: Any) -> JevCallRecord:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "created_at": T0,
        "pack": pack,
        "model_requested": "jev-1.13.0",
        "state_hash": state.hash,
        "questions": questions,
        "status": "ok",
        "model_served": "jev-1.13.0",
        "response": {"answers": {}},
        "input_tokens": 3000,
        "cost_micro_usd": 126,
    }
    values.update(overrides)
    return JevCallRecord(**values)


def decision(
    n: int, spec_id: str, key: str | None, call_id: uuid.UUID | None, **overrides: Any
) -> DecisionRecord:
    values: dict[str, Any] = {
        "id": uuid.UUID(int=n),
        "created_at": T0 + timedelta(seconds=n),
        "spec_id": spec_id,
        "spec_version": "2",
        "pack": spec_id.split(".")[0],
        "mode": "primary",
        "used": True,
        "source_used": "jev",
        "call_id": call_id,
        "subject_ref": key,
        "jev_answer": FAMILY_ANSWER,
        "jev_confidence": 0.8,
        "extra": {"key": key, "jev_model": "jev-1.13.0"},
    }
    values.update(overrides)
    return DecisionRecord(**values)


async def populate(path: Path) -> None:
    """Ten decision rows: four that can be replayed and six that cannot, each for a different reason."""
    db = await open_db(path)
    try:
        resume = make_state_blob({"resume": "Designed a checkout flow."})
        profile = make_state_blob({"readers": {"cv": "Python and SQL"}, "resume": "x"})
        read_call = call(resume, "first_read", {FAMILY: FAMILY_Q, DEPTH: DEPTH_Q})
        shadow_call = call(
            profile, "shadow", {SKILL_PYTHON: SKILL_Q, SKILL_SQL: SKILL_Q}, model_served="jev-1.14.0"
        )
        lost_state_call = call(
            StateBlob(hash="f" * 64, payload=b"", raw_bytes=0), "first_read", {FAMILY: FAMILY_Q}
        )  # its state was never stored: the purge removed it
        purged_call = call(resume, "first_read", {})  # the purge empties the questions of a call
        rows = [
            decision(1, FAMILY, FAMILY, read_call.id),
            decision(
                2,
                DEPTH,
                DEPTH,
                read_call.id,
                jev_answer=DEPTH_ANSWER,
                jev_confidence=0.7,
                used=False,
                mode="shadow",
            ),
            decision(
                3,
                "skill.level",
                SKILL_PYTHON,
                shadow_call.id,
                mode="shadow",
                used=False,
                incumbent={"score": 62, "band": 2, "skill_id": "python"},
                claude_answer={"level": 3},
                source_used="default",
                pack="shadow",
                extra={"key": SKILL_PYTHON, "jev_model": "jev-1.13.0"},
            ),
            decision(
                4,
                "skill.level",
                SKILL_SQL,
                shadow_call.id,
                mode="shadow",
                pack="shadow",
                extra={"key": SKILL_SQL},
            ),
            decision(
                5, GATE, GATE, None, source_used="claude", jev_answer=None, jev_confidence=None, extra={}
            ),
            decision(6, FAMILY, FAMILY, lost_state_call.id),
            decision(7, FAMILY, FAMILY, purged_call.id),
            decision(8, FAMILY, FAMILY, read_call.id, is_sample=True),
            decision(9, FAMILY, FAMILY, read_call.id, created_at=T0 - timedelta(days=30)),
            decision(10, FAMILY, FAMILY, read_call.id, created_at=T0 + timedelta(hours=1)),
        ]
        await build_repositories(db).decisions.record_batch(
            [resume, profile], [read_call, shadow_call, lost_state_call, purged_call], rows
        )
    finally:
        await db.dispose()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "source" / "tayyari.sqlite3"
    asyncio.run(populate(path))
    return path


def export_lines(db_path: Path, **options: Any) -> tuple[list[dict[str, Any]], export.ExportStats]:
    """Run the async export over ``db_path`` and parse the JSONL it wrote."""

    async def go() -> tuple[str, export.ExportStats]:
        db = await open_db(db_path)
        try:
            sink = io.StringIO()
            settings: dict[str, Any] = {"since": NOW - timedelta(days=7), **options}
            stats = await export.export_corpus(build_repositories(db).decisions, sink, **settings)
            return sink.getvalue(), stats
        finally:
            await db.dispose()

    text, stats = asyncio.run(go())
    return [json.loads(line) for line in text.splitlines()], stats


# ───────────────────────── the lines ─────────────────────────


def first_by_ref(lines: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The first (oldest) line of each subject_ref."""
    found: dict[str, dict[str, Any]] = {}
    for line in lines:
        found.setdefault(line["subject_ref"], line)
    return found


def test_a_corpus_line_carries_everything_a_replay_needs(db_path: Path) -> None:
    lines, _stats = export_lines(db_path)
    by_ref = first_by_ref(lines)

    family = by_ref[FAMILY]
    assert set(family) == {
        "id", "created_at", "spec_id", "spec_version", "pack", "subject_ref", "mode", "jev_model", "state_hash",
        "state", "question", "jev_answer", "jev_confidence", "claude_answer", "incumbent", "used", "source_used",
    }  # fmt: skip
    assert family["id"] == str(uuid.UUID(int=1))
    assert family["created_at"] == (T0 + timedelta(seconds=1)).isoformat()
    assert (family["spec_id"], family["spec_version"], family["pack"]) == (FAMILY, "2", "first_read")
    assert family["state"] == {"resume": "Designed a checkout flow."}
    assert family["question"] == FAMILY_Q
    assert family["jev_answer"] == FAMILY_ANSWER
    assert family["jev_confidence"] == 0.8
    assert (family["mode"], family["used"], family["source_used"]) == ("primary", True, "jev")
    assert family["jev_model"] == "jev-1.13.0"
    assert len(family["state_hash"]) == 64


def test_the_question_is_the_one_of_the_decisions_key_not_of_its_neighbours(db_path: Path) -> None:
    lines, _stats = export_lines(db_path)
    by_ref = first_by_ref(lines)

    assert by_ref[DEPTH]["question"] == DEPTH_Q
    assert by_ref[DEPTH]["state"] == by_ref[FAMILY]["state"]  # one call, one state, two questions
    assert by_ref[SKILL_PYTHON]["question"] == SKILL_Q  # a bound key: spec_id differs from subject_ref
    assert by_ref[SKILL_PYTHON]["spec_id"] == "skill.level"


def test_the_shadow_row_keeps_its_incumbent_and_claude_answer_and_the_model_that_answered(
    db_path: Path,
) -> None:
    lines, _stats = export_lines(db_path)
    shadow = next(line for line in lines if line["subject_ref"] == SKILL_PYTHON)

    assert shadow["incumbent"] == {"score": 62, "band": 2, "skill_id": "python"}
    assert shadow["claude_answer"] == {"level": 3}
    assert shadow["mode"] == "shadow"
    assert shadow["used"] is False
    assert shadow["source_used"] == "default"
    # the log row says jev-1.13.0 in its extra, the call says jev-1.14.0: the row's own record of the model wins
    assert shadow["jev_model"] == "jev-1.13.0"


def test_the_model_falls_back_to_the_one_the_call_reports(db_path: Path) -> None:
    lines, _stats = export_lines(db_path)
    sql = next(
        line for line in lines if line["subject_ref"] == SKILL_SQL
    )  # no model in its extra: the call's
    assert sql["jev_model"] == "jev-1.14.0"
    raw = {
        "id": "x", "created_at": "t", "spec_id": FAMILY, "spec_version": "1", "pack": "first_read", "mode": "primary",
        "used": True, "source_used": "jev", "subject_ref": FAMILY, "extra": {}, "state": {"a": 1},
        "call": {"questions": {FAMILY: FAMILY_Q}, "model_served": "jev-2.0.0", "model_requested": "jev-1.13.0",
                 "state_hash": "h", "payload_purged_at": None},
    }  # fmt: skip
    line, reason = export.corpus_line(raw)
    assert reason is None and line is not None
    assert line["jev_model"] == "jev-2.0.0"
    raw["call"]["model_served"] = None
    line, _reason = export.corpus_line(raw)
    assert line is not None and line["jev_model"] == "jev-1.13.0"


def test_lines_come_oldest_first(db_path: Path) -> None:
    lines, _stats = export_lines(db_path)
    stamps = [line["created_at"] for line in lines]
    assert stamps == sorted(stamps)


# ───────────────────────── what is left out ─────────────────────────


def test_rows_that_cannot_be_asked_again_are_left_out_and_counted(db_path: Path) -> None:
    lines, stats = export_lines(db_path)

    refs = [line["subject_ref"] for line in lines]
    assert refs == [FAMILY, DEPTH, SKILL_PYTHON, SKILL_SQL, FAMILY]  # rows 1 to 4, then row 10 an hour later
    assert stats.written == 5
    assert stats.scanned == 9  # row 9 is older than the window
    assert dict(stats.skipped) == {"no_jev_call": 1, "purged": 1, "no_question": 1, "sample": 1}
    assert stats.by_spec == {FAMILY: 2, DEPTH: 1, "skill.level": 2}


def test_sample_rows_are_exported_only_when_asked_for(db_path: Path) -> None:
    lines, stats = export_lines(db_path, include_sample=True)
    assert str(uuid.UUID(int=8)) in {line["id"] for line in lines}
    assert "sample" not in stats.skipped


def test_the_window_has_an_inclusive_start_and_an_exclusive_end(db_path: Path) -> None:
    lines, _stats = export_lines(db_path, since=T0 + timedelta(seconds=1), until=T0 + timedelta(seconds=3))
    assert [line["id"] for line in lines] == [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        (["first_read.*"], {FAMILY, DEPTH}),
        (["skill.level"], {SKILL_PYTHON, SKILL_SQL}),
        (["first_read.depth", "skill.*"], {DEPTH, SKILL_PYTHON, SKILL_SQL}),
        (["nothing.*"], set()),
    ],
)
def test_spec_patterns_keep_matching_spec_ids_only(
    db_path: Path, patterns: list[str], expected: set[str]
) -> None:
    lines, _stats = export_lines(db_path, specs=patterns)
    assert {line["subject_ref"] for line in lines} == expected


def test_limit_stops_after_that_many_lines(db_path: Path) -> None:
    lines, stats = export_lines(db_path, limit=2)
    assert [line["subject_ref"] for line in lines] == [FAMILY, DEPTH]
    assert stats.written == 2


# ───────────────────────── arguments ─────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7d", NOW - timedelta(days=7)),
        ("12h", NOW - timedelta(hours=12)),
        ("90m", NOW - timedelta(minutes=90)),
        ("2w", NOW - timedelta(weeks=2)),
        ("1.5d", NOW - timedelta(hours=36)),
        ("now", NOW),
        ("2026-10-01", datetime(2026, 10, 1, tzinfo=UTC)),
        ("2026-10-01T06:30", datetime(2026, 10, 1, 6, 30, tzinfo=UTC)),
        ("2026-10-01T06:30:00+02:00", datetime(2026, 10, 1, 4, 30, tzinfo=UTC)),
    ],
)
def test_parse_when(text: str, expected: datetime) -> None:
    assert export.parse_when(text, now=NOW) == expected


@pytest.mark.parametrize("text", ["", "yesterday", "7", "d7", "7 days", "2026-13-01"])
def test_parse_when_rejects_anything_else(text: str) -> None:
    with pytest.raises(ValueError, match="cannot read"):
        export.parse_when(text, now=NOW)


def test_spec_patterns_split_commas_and_repeats() -> None:
    assert export.spec_patterns(None) == []
    assert export.spec_patterns(["a.*, b.c", "d"]) == ["a.*", "b.c", "d"]
    assert export.matches_spec("a.x", []) is True
    assert export.matches_spec("a.x", ["b.*"]) is False


# ───────────────────────── the command ─────────────────────────


def run_export(db_path: Path, out: Path | None, **options: Any) -> int:
    values: dict[str, Any] = {
        "since": "7d",
        "out": out,
        "database_url": sqlite_url(db_path),
        "now": lambda: NOW,
    }
    values.update(options)
    return export.run(**values)


def test_the_command_writes_a_utf8_jsonl_file_with_unix_line_ends(
    db_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "evals" / "corpus.jsonl"

    assert run_export(db_path, out) == 0

    raw = out.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert len(raw.decode("utf-8").splitlines()) == 5
    assert not out.with_name("corpus.jsonl.part").exists()
    text = capsys.readouterr().out
    assert "Wrote 5 rows" in text
    assert "first_read.career_family 2" in text
    assert (
        "1 had no Jev call" in text
        and "1 lost their state" in text
        and "1 belong to the sample persona" in text
    )
    assert "keep it out of git" in text


def test_the_default_output_is_dated_in_the_corpus_folder(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "CORPUS_DIR", tmp_path / "corpus")
    assert run_export(db_path, None) == 0
    assert (tmp_path / "corpus" / "2026-10-08.jsonl").is_file()


def test_an_existing_output_is_kept_unless_forced(
    db_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "corpus.jsonl"
    out.write_text("precious\n", encoding="utf-8")

    assert run_export(db_path, out) == 1
    assert out.read_text(encoding="utf-8") == "precious\n"
    assert "--force" in capsys.readouterr().err

    assert run_export(db_path, out, force=True) == 0
    assert out.read_text(encoding="utf-8").startswith('{"id"')


def test_a_limit_and_a_spec_filter_reach_the_export(db_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "corpus.jsonl"
    assert run_export(db_path, out, specs=["skill.*"], limit=1) == 0
    refs = [json.loads(line)["subject_ref"] for line in out.read_text(encoding="utf-8").splitlines()]
    assert refs == [SKILL_PYTHON]


def test_the_configured_database_is_used_without_a_url(
    db_path: Path, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings(database_url=sqlite_url(db_path))
    out = tmp_path / "corpus.jsonl"
    assert export.run(since="7d", out=out, settings=settings, now=lambda: NOW) == 0
    assert out.stat().st_size > 0


def test_a_missing_sqlite_file_is_reported_not_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope" / "tayyari.sqlite3"
    assert run_export(missing, tmp_path / "out.jsonl") == 1
    assert "no database file" in capsys.readouterr().err
    assert not missing.exists()
    assert not missing.parent.exists()
    assert not (tmp_path / "out.jsonl").exists()


@pytest.mark.parametrize("options", [{"since": "soon"}, {"until": "later"}, {"until": "30d"}])
def test_a_bad_window_is_a_usage_error(db_path: Path, tmp_path: Path, options: dict[str, Any]) -> None:
    assert run_export(db_path, tmp_path / "out.jsonl", **options) == export.EXIT_USAGE
    assert not (tmp_path / "out.jsonl").exists()


def test_an_unsupported_database_url_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = export.run(since="7d", out=tmp_path / "o.jsonl", database_url="mysql://u:p@h/db", now=lambda: NOW)
    assert code == 1
    assert "invalid database configuration" in capsys.readouterr().err


def test_a_failing_export_hides_the_password_and_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"could not connect: password {URL_SECRET} rejected")

    monkeypatch.setattr(export, "_export_database", explode)
    out = tmp_path / "out.jsonl"

    code = export.run(
        since="7d",
        out=out,
        database_url=f"postgresql://reader:{URL_SECRET}@db.example.com/tayyari",
        now=lambda: NOW,
    )

    assert code == 1
    captured = capsys.readouterr()
    assert URL_SECRET not in captured.out + captured.err
    assert "***" in captured.err
    assert "db.example.com" in captured.out  # the safe URL is shown
    assert not out.exists() and not out.with_name("out.jsonl.part").exists()


# ───────────────────────── registration ─────────────────────────


def test_the_export_options_parse() -> None:
    args = cli.build_parser().parse_args(
        [
            "export",
            "--since",
            "7d",
            "--spec",
            "first_read.*",
            "--spec",
            "a,b",
            "--include-sample",
            "--limit",
            "5",
        ]
    )
    assert (args.command, args.since, args.until) == ("export", "7d", None)
    assert args.spec == ["first_read.*", "a,b"]
    assert (args.include_sample, args.limit, args.force, args.database_url, args.out) == (
        True,
        5,
        False,
        None,
        None,
    )


def test_since_is_required_and_limit_must_be_positive(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["export"], ["export", "--since", "7d", "--limit", "0"]):
        with pytest.raises(SystemExit) as exit_info:
            cli.build_parser().parse_args(argv)
        assert exit_info.value.code == 2
    assert "--since" in capsys.readouterr().err


def test_export_dispatch_passes_the_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_run(**kwargs: Any) -> int:
        seen.append(kwargs)
        return 3

    monkeypatch.setattr("tayyari.cli.export.run", fake_run)
    code = cli.main(
        ["export", "--since", "2d", "--until", "1d", "--out", "x.jsonl", "--spec", "a.*", "--force"]
    )
    assert code == 3
    assert seen == [
        {
            "since": "2d",
            "until": "1d",
            "specs": ["a.*"],
            "out": Path("x.jsonl"),
            "include_sample": False,
            "database_url": None,
            "limit": None,
            "force": True,
        }
    ]
