"""``replay`` on synthetic corpora with `FakeJev`: grouping, shuffling, dry run, failures and the output lines."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tayyari.cli import __main__ as cli
from tayyari.cli import replay
from tayyari.core.settings import Settings
from tayyari.llm.errors import JevError
from tayyari.llm.jev import JevClient, JevQuestion, JevResponse
from tayyari.llm.jev_http import HttpJevClient
from tayyari.llm.models import jev_cost_micro
from tests.fakes.jev import FakeJev, choice_answer, noul_answer, score_answer

NOW = datetime(2026, 10, 8, 9, 30, 15, tzinfo=UTC)
MODEL = "jev-9.9.9"
CALL_COST = jev_cost_micro(MODEL, 1_000_000)  # FakeJev(input_tokens=1_000_000): one call costs this much
FAMILY = "first_read.career_family"
DEPTH = "first_read.depth"
PROOF = "first_read.public_proof"
OPTIONS = {"design_ux": "Design", "engineering": "Engineering", "other": "Other", "ops": "Operations"}
FAMILY_Q = JevQuestion.choice("Which family?", OPTIONS).to_wire()
DEPTH_Q = JevQuestion.score("How deep?", ["none", "some", "deep", "expert"]).to_wire()
PROOF_Q = JevQuestion.noul("Is there public proof?").to_wire()
QUESTIONS = {FAMILY: FAMILY_Q, DEPTH: DEPTH_Q, PROOF: PROOF_Q}
STATE_A = {"resume": "Designed a checkout flow."}
STATE_B = {"resume": "Welded pipes for ten years."}
CorpusMaker = Callable[[list[dict[str, Any]]], Path]
Runner = Callable[..., tuple[int, list[dict[str, Any]]]]
OLD_ANSWER = {
    "kind": "choice",
    "choice": "engineering",
    "probabilities": {"engineering": 0.7},
    "confidence": 0.6,
}


def corpus_row(
    n: int, key: str = FAMILY, *, state: Any = STATE_A, pack: str = "first_read", **extra: Any
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": str(uuid.UUID(int=n)),
        "created_at": f"2026-10-07T12:00:{n:02d}+00:00",
        "spec_id": key.split("#")[0],
        "spec_version": "2",
        "pack": pack,
        "subject_ref": key,
        "mode": "primary",
        "jev_model": "jev-1.13.0",
        "state": state,
        "question": QUESTIONS.get(key, FAMILY_Q),
        "jev_answer": OLD_ANSWER,
        "jev_confidence": 0.6,
        "claude_answer": {"choice": "design_ux"},
        "incumbent": None,
        "used": True,
        "source_used": "jev",
    }
    values.update(extra)
    return values


def write_corpus(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def script() -> dict[str, Any]:
    return {
        FAMILY: choice_answer("design_ux", options=list(OPTIONS), p=0.9),
        DEPTH: score_answer(3, n_levels=4),
        PROOF: noul_answer(0.95),
    }


def fake_jev(answers: Any = None, **options: Any) -> FakeJev:
    return FakeJev(script() if answers is None else answers, input_tokens=1_000_000, **options)


async def no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def corpus(tmp_path: Path) -> Callable[[list[dict[str, Any]]], Path]:
    def make(rows: list[dict[str, Any]]) -> Path:
        return write_corpus(tmp_path / "corpus.jsonl", rows)

    return make


@pytest.fixture
def go(
    tmp_path: Path, make_settings: Callable[..., Settings]
) -> Callable[..., tuple[int, list[dict[str, Any]]]]:
    """Run a replay of ``path`` against ``jev`` and return (exit code, output lines)."""

    def run(path: Path, jev: JevClient, **options: Any) -> tuple[int, list[dict[str, Any]]]:
        out = tmp_path / "reports" / "replay.jsonl"
        values: dict[str, Any] = {
            "corpus": path,
            "model": MODEL,
            "out": out,
            "settings": make_settings(),
            "client_factory": lambda _settings: jev,
            "sleep": no_sleep,
            "now": lambda: NOW,
        }
        values.update(options)
        code = replay.run(**values)
        return code, read_lines(out) if out.exists() else []

    return run


# ───────────────────────── grouping ─────────────────────────


def test_questions_over_one_state_and_pack_go_in_one_call(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus(
        [
            corpus_row(1, FAMILY),
            corpus_row(2, DEPTH),
            corpus_row(3, PROOF),
            corpus_row(4, FAMILY, state=STATE_B),
        ]
    )
    jev = fake_jev()

    code, _lines = go(path, jev)

    assert code == 0
    assert len(jev.calls) == 2
    first, second = jev.calls
    assert first.state == STATE_A and set(first.question_ids) == {FAMILY, DEPTH, PROOF}
    assert second.state == STATE_B and second.question_ids == [FAMILY]
    assert {c.model for c in jev.calls} == {MODEL}
    assert {c.ctx.purpose for c in jev.calls} == {"eval"}
    assert jev.closed


def test_the_same_state_in_two_packs_is_two_calls(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus(
        [corpus_row(1, FAMILY), corpus_row(2, "skill.level#python", pack="shadow", question=DEPTH_Q)]
    )
    jev = fake_jev()
    go(path, jev)
    assert sorted(c.question_ids[0] for c in jev.calls) == [FAMILY, "skill.level#python"]


def test_identical_questions_are_asked_once_and_shared_by_their_rows(corpus: CorpusMaker, go: Runner) -> None:
    # an audited decision and its twin: same state, key and wording
    path = corpus([corpus_row(1, FAMILY), corpus_row(2, FAMILY)])
    jev = fake_jev()

    code, lines = go(path, jev)

    assert code == 0
    assert len(jev.calls) == 1 and jev.calls[0].question_ids == [FAMILY]
    assert [line["new"]["answer"]["choice"] for line in lines] == ["design_ux", "design_ux"]
    assert [line["cost_micro_usd"] for line in lines] == [CALL_COST // 2, CALL_COST // 2]


def test_two_wordings_under_one_key_are_never_in_the_same_call(corpus: CorpusMaker, go: Runner) -> None:
    other = JevQuestion.choice("Which family, really?", OPTIONS).to_wire()
    path = corpus([corpus_row(1, FAMILY), corpus_row(2, FAMILY, question=other)])
    jev = fake_jev()

    code, lines = go(path, jev)

    assert code == 0 and len(jev.calls) == 2
    assert all(call.question_ids == [FAMILY] for call in jev.calls)
    assert len(lines) == 2 and all(line["new"] is not None for line in lines)


def test_more_questions_than_a_call_may_hold_are_split_in_order(tmp_path: Path) -> None:
    rows = replay.load_corpus(
        write_corpus(
            tmp_path / "c.jsonl", [corpus_row(i, f"skill.level#s{i}", question=DEPTH_Q) for i in range(5)]
        )
    )

    calls, by_row = replay.plan_calls(rows, max_questions=2)

    assert [[u.key for u in call.units] for call in calls] == [
        ["skill.level#s0", "skill.level#s1"],
        ["skill.level#s2", "skill.level#s3"],
        ["skill.level#s4"],
    ]
    assert len(by_row) == 5


def test_rows_that_share_a_state_keep_one_copy_of_it(tmp_path: Path) -> None:
    rows = replay.load_corpus(write_corpus(tmp_path / "c.jsonl", [corpus_row(1), corpus_row(2, DEPTH)]))
    assert rows[0].raw["state"] is rows[1].raw["state"]
    assert rows[0].state_hash == rows[1].state_hash and len(rows[0].state_hash) == 64


def test_a_stored_state_hash_is_used_as_is(tmp_path: Path) -> None:
    rows = replay.load_corpus(write_corpus(tmp_path / "c.jsonl", [corpus_row(1, state_hash="abc123")]))
    assert rows[0].state_hash == "abc123"


# ───────────────────────── the output ─────────────────────────


def test_each_line_puts_the_new_answer_next_to_the_old_one(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus(
        [
            corpus_row(1, FAMILY, incumbent={"value": "x"}),
            corpus_row(2, DEPTH),
            corpus_row(3, PROOF),
            corpus_row(4, FAMILY, state=STATE_B),
        ]
    )
    code, lines = go(path, fake_jev())

    assert code == 0
    assert [line["id"] for line in lines] == [str(uuid.UUID(int=n)) for n in (1, 2, 3, 4)]  # corpus order
    family, depth, proof, _other = lines
    assert family["kind"] == "choice" and depth["kind"] == "score" and proof["kind"] == "noul"
    assert family["new"]["answer"]["choice"] == "design_ux"
    assert family["new"]["confidence"] == family["new"]["answer"]["confidence"] > 0.5
    assert family["new"]["model"] == MODEL
    assert depth["new"]["answer"]["score_level"] == 3
    assert proof["new"]["answer"]["noul"] == 0.95
    assert family["old"] == {"model": "jev-1.13.0", "answer": OLD_ANSWER, "confidence": 0.6}
    assert family["claude_answer"] == {"choice": "design_ux"}
    assert family["incumbent"] == {"value": "x"}
    assert (family["spec_id"], family["subject_ref"], family["key"], family["pack"]) == (
        FAMILY,
        FAMILY,
        FAMILY,
        "first_read",
    )
    assert (family["mode"], family["used"], family["source_used"], family["replay_model"]) == (
        "primary",
        True,
        "jev",
        MODEL,
    )
    assert family["shuffle"] is None and family["error"] is None
    assert family["state_hash"] and "state" not in family and "question" not in family


def test_the_replay_costs_add_up_to_what_the_calls_cost(
    corpus: CorpusMaker, go: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    path = corpus(
        [
            corpus_row(1, FAMILY),
            corpus_row(2, DEPTH),
            corpus_row(3, PROOF),
            corpus_row(4, FAMILY, state=STATE_B),
        ]
    )
    _code, lines = go(path, fake_jev())

    assert [line["cost_micro_usd"] for line in lines] == [CALL_COST // 3] * 3 + [CALL_COST]
    assert sum(line["cost_micro_usd"] for line in lines) == 2 * CALL_COST
    out = capsys.readouterr().out
    assert "4 answered, 0 failed" in out
    assert "in 2 Jev calls" in out


def test_the_output_file_has_unix_line_ends_and_a_default_name(
    corpus: CorpusMaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_settings: Callable[..., Settings],
) -> None:
    monkeypatch.setattr(replay, "REPORTS_DIR", tmp_path / "reports")
    path = corpus([corpus_row(1)])
    code = replay.run(
        corpus=path,
        model=MODEL,
        settings=make_settings(),
        client_factory=lambda _s: fake_jev(),
        now=lambda: NOW,
    )
    assert code == 0
    written = tmp_path / "reports" / "replay-jev-9.9.9-20261008T093015Z.jsonl"
    assert written.is_file()
    assert b"\r" not in written.read_bytes()


def test_the_default_name_is_safe_for_any_model_id() -> None:
    assert replay.default_out("jev-1.14.0", NOW).name == "replay-jev-1.14.0-20261008T093015Z.jsonl"
    assert replay.default_out("jev latest/../x", NOW).name == "replay-jev_latest_.._x-20261008T093015Z.jsonl"
    assert replay.default_out("///", NOW).name == "replay-model-20261008T093015Z.jsonl"


def test_limit_replays_only_the_first_rows(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus([corpus_row(1, FAMILY), corpus_row(2, DEPTH), corpus_row(3, FAMILY, state=STATE_B)])
    jev = fake_jev()

    code, lines = go(path, jev, limit=2)

    assert code == 0
    assert [line["key"] for line in lines] == [FAMILY, DEPTH]
    assert len(jev.calls) == 1


# ───────────────────────── shuffling ─────────────────────────


def first_option_wins(_state: Any, questions: Any) -> dict[str, Any]:
    """A biased model: it always answers the first option it is shown."""
    return {
        qid: choice_answer(q.option_labels[0], options=list(q.option_labels), p=0.9)
        for qid, q in questions.items()
        if q.kind == "choice"
    }


def test_shuffled_runs_resend_only_the_choice_questions_with_their_options_reordered(
    corpus: CorpusMaker, go: Runner
) -> None:
    path = corpus([corpus_row(1, FAMILY), corpus_row(2, DEPTH), corpus_row(3, PROOF)])
    jev = fake_jev()

    code, lines = go(path, jev, shuffle=3)

    assert code == 0
    assert len(jev.calls) == 4  # one base call and three shuffled ones
    base, *shuffled = jev.calls
    assert set(base.question_ids) == {FAMILY, DEPTH, PROOF}
    assert base.questions[FAMILY].option_labels == tuple(OPTIONS)  # the original order
    for call in shuffled:
        assert call.question_ids == [FAMILY]  # no score, no yes/no
        question = call.questions[FAMILY]
        assert sorted(question.option_labels) == sorted(OPTIONS)
        assert question.instructions == "Which family?"
        assert question.criteria == {
            label: OPTIONS[label] for label in question.option_labels
        }  # descriptions travel with their labels
    assert any(call.questions[FAMILY].option_labels != tuple(OPTIONS) for call in shuffled)
    family = lines[0]
    assert family["shuffle"]["runs"] == 3 and len(family["shuffle"]["answers"]) == 3
    assert lines[1]["shuffle"] is None and lines[2]["shuffle"] is None


def test_the_shuffle_is_seeded(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus([corpus_row(1, FAMILY)])

    def orders(seed: int) -> list[tuple[str, ...]]:
        jev = fake_jev()
        go(path, jev, shuffle=4, seed=seed)
        return [call.questions[FAMILY].option_labels for call in jev.calls[1:]]

    assert orders(7) == orders(7)
    assert orders(7) != orders(8)


def test_a_model_that_follows_the_option_order_is_unstable(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus([corpus_row(1, FAMILY), corpus_row(2, FAMILY, state=STATE_B)])
    jev = fake_jev(first_option_wins)

    code, lines = go(path, jev, shuffle=4)

    assert code == 0
    assert all(line["new"]["answer"]["choice"] == "design_ux" for line in lines)  # the original first option
    shuffled = jev.calls[2:]
    for state, line in zip((STATE_A, STATE_B), lines, strict=True):
        runs = [c.questions[FAMILY].option_labels[0] for c in shuffled if c.state == state]
        assert line["shuffle"]["answers"] == runs
        assert line["shuffle"]["flips"] == sum(1 for label in runs if label != "design_ux")
    assert sum(line["shuffle"]["flips"] for line in lines) > 0


def test_a_model_that_reads_the_content_does_not_flip(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus([corpus_row(1, FAMILY)])
    code, lines = go(path, fake_jev(), shuffle=3)  # always "design_ux", whatever the order
    assert code == 0
    assert lines[0]["shuffle"] == {"runs": 3, "answers": ["design_ux"] * 3, "flips": 0}


def test_shuffled_calls_are_paid_for_by_the_rows_they_asked_about(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus([corpus_row(1, FAMILY), corpus_row(2, DEPTH)])
    _code, lines = go(path, fake_jev(), shuffle=2)
    family, depth = lines
    assert (
        family["cost_micro_usd"] == CALL_COST // 2 + 2 * CALL_COST
    )  # its share of the base call, plus both shuffled calls
    assert depth["cost_micro_usd"] == CALL_COST // 2


def test_a_single_option_is_not_shuffled(corpus: CorpusMaker, go: Runner) -> None:
    single = JevQuestion.choice("Only one?", {"only": "the one"}).to_wire()
    jev = fake_jev({FAMILY: choice_answer("only")})
    code, lines = go(corpus([corpus_row(1, FAMILY, question=single)]), jev, shuffle=2)
    assert code == 0 and len(jev.calls) == 1
    assert lines[0]["shuffle"] is None


# ───────────────────────── dry run ─────────────────────────


def test_a_dry_run_sends_nothing_and_needs_no_key(
    corpus: CorpusMaker,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    make_settings: Callable[..., Settings],
) -> None:
    path = corpus([corpus_row(1, FAMILY), corpus_row(2, DEPTH), corpus_row(3, FAMILY, state=STATE_B)])

    def explode(_settings: Settings) -> JevClient:
        raise AssertionError("a dry run must not build a client")

    out = tmp_path / "never.jsonl"
    code = replay.run(
        corpus=path,
        model=MODEL,
        dry_run=True,
        shuffle=2,
        out=out,
        settings=make_settings(),
        client_factory=explode,
    )

    assert code == 0
    assert not out.exists()
    text = capsys.readouterr().out
    assert "dry run: nothing is sent" in text
    assert "3 rows, 3 can be asked" in text
    assert "3 distinct, over 2 states" in text
    assert "2 + 4 shuffled" in text  # two base calls; both have a choice question, two shuffled runs each
    assert "no known price" in text  # jev-9.9.9 is not in the price table


def test_the_plan_counts_rows_calls_and_estimates_the_cost(tmp_path: Path) -> None:
    rows = replay.load_corpus(
        write_corpus(tmp_path / "c.jsonl", [corpus_row(1, FAMILY), corpus_row(2, DEPTH)])
    )
    calls, _by_row = replay.plan_calls(rows)

    plan = replay.summarise_plan(rows, calls, "jev-1.13.0", shuffle=0)
    assert (plan.rows, plan.replayable_rows, plan.states, plan.questions, plan.calls, plan.shuffle_calls) == (
        2,
        2,
        1,
        2,
        1,
        0,
    )
    size = replay.request_size(calls[0], "jev-1.13.0", calls[0].questions())
    assert plan.input_tokens == size // replay.BYTES_PER_TOKEN
    assert plan.cost_micro_usd == jev_cost_micro("jev-1.13.0", plan.input_tokens)

    with_shuffle = replay.summarise_plan(rows, calls, "jev-1.13.0", shuffle=3)
    assert with_shuffle.shuffle_calls == 3  # the family choice only; the score is not reshuffled
    assert with_shuffle.cost_micro_usd > plan.cost_micro_usd


def test_an_unreadable_question_is_not_counted_as_askable(tmp_path: Path) -> None:
    rows = replay.load_corpus(
        write_corpus(tmp_path / "c.jsonl", [corpus_row(1), corpus_row(2, question="oops")])
    )
    calls, by_row = replay.plan_calls(rows)
    plan = replay.summarise_plan(rows, calls, MODEL, 0)
    assert (plan.rows, plan.replayable_rows, plan.questions) == (2, 1, 1)
    assert set(by_row) == {0}


# ───────────────────────── failures ─────────────────────────


def test_a_failed_call_marks_its_rows_and_the_run_goes_on(
    corpus: CorpusMaker, go: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    path = corpus([corpus_row(1, FAMILY), corpus_row(2, DEPTH), corpus_row(3, FAMILY, state=STATE_B)])
    jev = fake_jev()
    jev.fail_next(JevError("server", "boom"))

    code, lines = go(path, jev, concurrency=1)

    assert code == 0
    failed, ok_depth, ok_other = lines
    assert failed["new"] is None and failed["error"].startswith("server: ")
    assert failed["cost_micro_usd"] == 0
    assert ok_depth["new"] is None  # same call as the failed row: it failed too
    assert ok_other["new"]["answer"]["choice"] == "design_ux"
    assert "1 answered, 2 failed" in capsys.readouterr().out


def test_every_call_failing_is_exit_code_one(corpus: CorpusMaker, go: Runner) -> None:
    jev = fake_jev()
    jev.fail_with(JevError("server", "down"))
    code, lines = go(corpus([corpus_row(1)]), jev)
    assert code == 1
    assert lines[0]["error"].startswith("server: ")


def test_an_auth_error_stops_the_run_instead_of_spending_the_corpus_on_it(
    corpus: CorpusMaker, go: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    path = corpus(
        [corpus_row(1, state={"n": 1}), corpus_row(2, state={"n": 2}), corpus_row(3, state={"n": 3})]
    )
    jev = fake_jev()
    jev.fail_with(JevError("auth", "bad key"))

    code, lines = go(path, jev, concurrency=1, shuffle=2)

    assert code == 1
    assert len(jev.calls) == 1  # nothing was sent after the first 401
    assert lines[0]["error"].startswith("auth: ")
    assert all("skipped after a fatal error" in line["error"] for line in lines[1:])
    assert "stopped early: auth" in capsys.readouterr().err


def test_a_run_where_nothing_ever_succeeds_stops_early(
    corpus: CorpusMaker, go: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    # e.g. a misspelled model id: every call is a 400, and the other 40 states would only repeat it
    path = corpus([corpus_row(i, state={"n": i}) for i in range(50)])
    jev = fake_jev()
    jev.fail_with(JevError("bad_request", "unknown model"))

    code, lines = go(path, jev, concurrency=1)

    assert code == 1
    assert len(jev.calls) == replay.NO_SUCCESS_LIMIT
    assert len(lines) == 50
    assert (
        sum("skipped after a fatal error" in line["error"] for line in lines) == 50 - replay.NO_SUCCESS_LIMIT
    )
    captured = capsys.readouterr()
    assert "none succeeded" in captured.err
    assert "Next:" not in captured.out


def test_one_success_keeps_a_run_with_failures_going(corpus: CorpusMaker, go: Runner) -> None:
    path = corpus([corpus_row(i, state={"n": i}) for i in range(30)])
    jev = fake_jev()
    jev.fail_next(JevError("bad_request", "one bad call"), times=0)  # no scripted failures: all succeed

    code, lines = go(path, jev, concurrency=1)

    assert code == 0 and len(jev.calls) == 30 and all(line["error"] is None for line in lines)


def test_a_rate_limited_call_waits_and_tries_again(
    corpus: CorpusMaker, make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    path = corpus([corpus_row(1)])
    jev = fake_jev()
    jev.fail_next(JevError("rate_limit", "slow down", retry_after_s=5.0))
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    out = tmp_path / "out.jsonl"
    code = replay.run(
        corpus=path,
        model=MODEL,
        out=out,
        settings=make_settings(),
        client_factory=lambda _s: jev,
        sleep=record_sleep,
    )

    assert code == 0
    assert sleeps == [5.0]
    assert len(jev.calls) == 2  # the 429 and the retry
    assert read_lines(out)[0]["new"] is not None


def test_a_call_that_stays_rate_limited_gives_up_after_a_few_tries(
    corpus: CorpusMaker, make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    jev = fake_jev()
    jev.fail_with(JevError("rate_limit", "no", retry_after_s=1000.0))
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    out = tmp_path / "out.jsonl"
    code = replay.run(
        corpus=corpus([corpus_row(1)]), model=MODEL, out=out, settings=make_settings(), client_factory=lambda _s: jev,
        sleep=record_sleep,
    )  # fmt: skip

    assert code == 1
    assert len(jev.calls) == replay.RATE_LIMIT_RETRIES + 1
    assert sleeps == [replay.RATE_LIMIT_MAX_WAIT_S] * replay.RATE_LIMIT_RETRIES  # Retry-After is capped
    assert read_lines(out)[0]["error"].startswith("rate_limit: ")


def test_a_client_bug_fails_that_call_only(corpus: CorpusMaker, go: Runner) -> None:
    class Buggy(FakeJev):
        async def system_one(self, *args: Any, **kwargs: Any) -> JevResponse:
            if len(self.calls) == 0:
                self.calls.append(None)  # type: ignore[arg-type]
                raise RuntimeError("kaboom")
            return await super().system_one(*args, **kwargs)

    jev = Buggy(script(), input_tokens=1_000_000)
    path = corpus([corpus_row(1, state={"n": 1}), corpus_row(2, state={"n": 2})])
    code, lines = go(path, jev, concurrency=1)
    assert code == 0
    assert lines[0]["error"] == "RuntimeError: kaboom"
    assert lines[1]["new"] is not None


def test_a_response_without_an_answer_for_a_key_is_an_error_for_that_row(
    corpus: CorpusMaker, go: Runner
) -> None:
    class Dropping(FakeJev):
        async def system_one(self, *args: Any, **kwargs: Any) -> JevResponse:
            response = await super().system_one(*args, **kwargs)
            return dataclasses.replace(
                response, answers={k: v for k, v in response.answers.items() if k != DEPTH}
            )

    code, lines = go(
        corpus([corpus_row(1, FAMILY), corpus_row(2, DEPTH)]), Dropping(script(), input_tokens=1_000_000)
    )

    assert code == 0
    assert lines[0]["new"] is not None
    assert lines[1]["new"] is None and lines[1]["error"].startswith("no_answer")


def test_a_row_with_an_invalid_question_is_reported_and_the_rest_is_replayed(
    corpus: CorpusMaker, go: Runner
) -> None:
    broken = {"type": "choice", "instructions": "Pick", "criteria": {}}  # a choice needs options
    path = corpus(
        [
            corpus_row(1, FAMILY, question=broken),
            corpus_row(2, DEPTH),
            corpus_row(3, PROOF, question="not an object"),
        ]
    )
    jev = fake_jev()

    code, lines = go(path, jev)

    assert code == 0
    assert len(jev.calls) == 1 and jev.calls[0].question_ids == [DEPTH]
    assert lines[0]["new"] is None and lines[0]["error"].startswith("bad_question")
    assert lines[1]["new"] is not None
    assert lines[2]["error"].startswith("bad_question")


def test_the_requests_in_flight_never_exceed_the_concurrency(corpus: CorpusMaker, go: Runner) -> None:
    class Tracking(FakeJev):
        active = 0
        peak = 0

        async def system_one(self, *args: Any, **kwargs: Any) -> JevResponse:
            type(self).active += 1
            type(self).peak = max(type(self).peak, type(self).active)
            try:
                await asyncio.sleep(0.01)
                return await super().system_one(*args, **kwargs)
            finally:
                type(self).active -= 1

    path = corpus([corpus_row(i, FAMILY, state={"n": i}) for i in range(12)])
    jev = Tracking(script(), input_tokens=1_000_000)

    code, lines = go(path, jev, concurrency=3, shuffle=1)

    assert code == 0 and len(lines) == 12
    assert len(jev.calls) == 24
    assert Tracking.peak == 3


def test_an_interrupt_keeps_what_was_answered(
    corpus: CorpusMaker, make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    class Interrupted(FakeJev):
        async def system_one(self, *args: Any, **kwargs: Any) -> JevResponse:
            if len(self.calls) == 1:
                raise KeyboardInterrupt
            return await super().system_one(*args, **kwargs)

    path = corpus(
        [corpus_row(1, state={"n": 1}), corpus_row(2, state={"n": 2}), corpus_row(3, state={"n": 3})]
    )
    jev = Interrupted(script(), input_tokens=1_000_000)
    out = tmp_path / "partial.jsonl"

    with pytest.raises(KeyboardInterrupt):
        replay.run(
            corpus=path,
            model=MODEL,
            concurrency=1,
            out=out,
            settings=make_settings(),
            client_factory=lambda _s: jev,
        )

    kept = read_lines(out)
    assert [line["id"] for line in kept] == [str(uuid.UUID(int=1))]
    assert kept[0]["new"] is not None


# ───────────────────────── the key and the corpus file ─────────────────────────


def test_without_a_key_the_command_says_so(
    corpus: CorpusMaker, make_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    code = replay.run(corpus=corpus([corpus_row(1)]), model=MODEL, settings=make_settings())
    assert code == 1
    assert "JEV_API_KEY" in capsys.readouterr().err


def test_a_client_that_cannot_be_built_is_reported(
    corpus: CorpusMaker, make_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_settings: Settings) -> JevClient:
        raise ValueError("bad config")

    code = replay.run(
        corpus=corpus([corpus_row(1)]), model=MODEL, settings=make_settings(), client_factory=broken
    )
    assert code == 1
    assert "could not build the Jev client: ValueError: bad config" in capsys.readouterr().err


def test_the_default_client_uses_the_real_key_and_ignores_the_kill_switch(
    make_settings: Callable[..., Settings],
) -> None:
    assert replay.default_client(make_settings()) is None
    client = replay.default_client(make_settings(jev_api_key="a-key", jev={"enabled": False}))
    assert isinstance(client, HttpJevClient)
    assert client.enabled  # the switch guards the live app; a replay never touches it
    assert "a-key" not in repr(client)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{not json}\n", "c.jsonl:1: not valid JSON"),
        ('["a list"]\n', "c.jsonl:1: expected a JSON object"),
        ('{"id": "1", "spec_id": "x"}\n', "c.jsonl:1: missing field(s) pack, state, question"),
    ],
)
def test_a_bad_corpus_line_is_named(
    tmp_path: Path,
    text: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
    make_settings: Callable[..., Settings],
) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(text, encoding="utf-8")
    assert replay.run(corpus=path, model=MODEL, dry_run=True, settings=make_settings()) == 1
    assert message in capsys.readouterr().err


def test_a_missing_or_empty_corpus_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_settings: Callable[..., Settings]
) -> None:
    assert (
        replay.run(corpus=tmp_path / "nope.jsonl", model=MODEL, dry_run=True, settings=make_settings()) == 1
    )
    assert "cannot open" in capsys.readouterr().err
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    assert replay.run(corpus=empty, model=MODEL, dry_run=True, settings=make_settings()) == 1
    assert "has no rows" in capsys.readouterr().err


def test_blank_lines_in_the_corpus_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(
        json.dumps(corpus_row(1)) + "\n\n   \n" + json.dumps(corpus_row(2)) + "\n", encoding="utf-8"
    )
    assert [row.id for row in replay.load_corpus(path)] == [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]


@pytest.mark.parametrize(
    "options",
    [{"model": "  "}, {"concurrency": 0}, {"shuffle": -1}, {"limit": 0}, {"timeout_s": 0}],
)
def test_bad_options_are_usage_errors(tmp_path: Path, options: dict[str, Any]) -> None:
    path = write_corpus(tmp_path / "c.jsonl", [corpus_row(1)])
    values: dict[str, Any] = {"corpus": path, "model": MODEL, "dry_run": True}
    values.update(options)
    assert replay.run(**values) == replay.EXIT_USAGE


# ───────────────────────── registration ─────────────────────────


def test_the_replay_options_parse() -> None:
    args = cli.build_parser().parse_args(
        ["replay", "--corpus", "c.jsonl", "--model", "jev-1.14.0", "--concurrency", "4", "--shuffle", "2", "--limit", "9",
         "--out", "o.jsonl", "--seed", "5", "--timeout", "12.5", "--dry-run"]
    )  # fmt: skip
    assert (args.command, args.corpus, args.model) == ("replay", Path("c.jsonl"), "jev-1.14.0")
    assert (args.concurrency, args.shuffle, args.limit, args.out) == (4, 2, 9, Path("o.jsonl"))
    assert (args.seed, args.timeout, args.dry_run) == (5, 12.5, True)
    defaults = cli.build_parser().parse_args(["replay", "--corpus", "c.jsonl", "--model", "m"])
    assert (defaults.concurrency, defaults.shuffle, defaults.limit, defaults.out, defaults.dry_run) == (
        8,
        0,
        None,
        None,
        False,
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["replay", "--model", "m"],
        ["replay", "--corpus", "c"],
        ["replay", "--corpus", "c", "--model", "m", "--concurrency", "0"],
    ],
)
def test_replay_needs_a_corpus_and_a_model(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_replay_dispatch_passes_the_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_run(**kwargs: Any) -> int:
        seen.append(kwargs)
        return 0

    monkeypatch.setattr("tayyari.cli.replay.run", fake_run)
    assert cli.main(["replay", "--corpus", "c.jsonl", "--model", "jev-2", "--dry-run", "--shuffle", "3"]) == 0
    assert seen == [
        {
            "corpus": Path("c.jsonl"),
            "model": "jev-2",
            "concurrency": 8,
            "shuffle": 3,
            "limit": None,
            "out": None,
            "dry_run": True,
            "seed": 0,
            "timeout_s": 30.0,
        }
    ]
