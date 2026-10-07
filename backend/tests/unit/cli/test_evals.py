"""``eval report`` and ``eval promote`` on synthetic replays: references, agreement, bins, escalation, thresholds, the patch."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tayyari.cli import __main__ as cli
from tayyari.cli import evals, replay
from tayyari.core.settings import Settings
from tayyari.decisions.config import load_defaults
from tayyari.decisions.packs.service_impl import build_registry
from tayyari.decisions.registry import DecisionRegistry
from tayyari.llm.jev import JevAnswer, JevQuestion, normalise_answer
from tayyari.llm.models import jev_cost_micro
from tests.fakes.jev import FakeJev, choice_answer, noul_answer, score_answer

NOW = datetime(2026, 10, 8, 9, 30, 15, tzinfo=UTC)
FAMILY = "first_read.career_family"
PROOF = "first_read.public_proof"
DEPTH = "first_read.depth"
OPTIONS = ["a", "b", "c"]
DEFAULTS_TOML = """# test defaults, so the tests do not move when the real thresholds are promoted
version = 1

["first_read.career_family"]
mode = "primary"
threshold = 0.45        # normalised for about 18 options
fallback = "skip"

["first_read.public_proof"]
mode = "primary"
threshold = 0.6
fallback = "skip"

["chat.web_gate"]
mode = "primary"
threshold = 0.8         # a comment that must survive "quoted" words
fallback = "claude"
audit_rate = 0.05

["skill.level"]
mode = "shadow"
threshold = 1.0
fallback = "skip"
"""


@pytest.fixture(scope="module")
def registry() -> DecisionRegistry:
    return build_registry()


def norm(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    return normalise_answer(kind, raw).model_dump(exclude_none=True)  # type: ignore[arg-type]


def pick(label: str, confidence: float) -> dict[str, Any]:
    """A normalised choice answer with exactly this confidence."""
    return norm("choice", choice_answer(label, options=OPTIONS, confidence=confidence))


def noul_at(confidence: float, *, yes: bool = True) -> dict[str, Any]:
    return norm("noul", noul_answer(0.5 + confidence / 2 if yes else 0.5 - confidence / 2))


def line(
    n: int,
    spec_id: str = FAMILY,
    *,
    new: dict[str, Any] | None = None,
    old: dict[str, Any] | None = None,
    kind: str = "choice",
    claude: Any = None,
    incumbent: Any = None,
    key: str | None = None,
    error: str | None = None,
    shuffle: Any = None,
    cost: int = 0,
    state_hash: str | None = None,
) -> dict[str, Any]:
    """One replay line (what ``replay`` writes)."""
    return {
        "id": str(uuid.UUID(int=n)),
        "spec_id": spec_id,
        "spec_version": "2",
        "pack": spec_id.split(".")[0],
        "subject_ref": key or spec_id,
        "key": key or spec_id,
        "state_hash": state_hash or f"hash-{n}",
        "kind": kind,
        "mode": "primary",
        "used": True,
        "source_used": "jev",
        "replay_model": "jev-2.0.0",
        "old": {
            "model": "jev-1.13.0",
            "answer": old,
            "confidence": None if old is None else old["confidence"],
        },
        "new": None
        if new is None
        else {"model": "jev-2.0.0", "answer": new, "confidence": new["confidence"]},
        "claude_answer": claude,
        "incumbent": incumbent,
        "shuffle": shuffle,
        "cost_micro_usd": cost,
        "latency_ms": 100,
        "error": error,
    }


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def spec_of(registry: DecisionRegistry, spec_id: str) -> Any:
    return registry.spec(spec_id)


# ───────────────────────── the reference ─────────────────────────


def test_the_reference_is_the_label_then_claude_then_the_incumbent_then_the_old_jev(
    registry: DecisionRegistry,
) -> None:
    new = JevAnswer.model_validate(pick("a", 0.9))
    row = line(1, new=pick("a", 0.9), old=pick("b", 0.5), claude={"choice": "c"}, incumbent={"value": "d"})
    spec = spec_of(registry, FAMILY)

    by_label = evals.judge(row, new, spec, "e")
    assert by_label == evals.Judgement("label", "e", "a", "b")
    by_claude = evals.judge(row, new, spec, None)
    assert by_claude == evals.Judgement("claude", "c", "a", "b")
    by_incumbent = evals.judge({**row, "claude_answer": None}, new, spec, None)
    assert by_incumbent == evals.Judgement("incumbent", "d", "a", "b")
    by_old = evals.judge({**row, "claude_answer": None, "incumbent": None}, new, spec, None)
    assert by_old == evals.Judgement(
        "old_jev", "b", "a", None
    )  # the old answer is the reference: no baseline for it
    assert (
        evals.judge(
            {**row, "claude_answer": None, "incumbent": None, "old": {"answer": None}}, new, spec, None
        )
        is None
    )


def test_a_claude_answer_that_does_not_fit_the_question_is_skipped(registry: DecisionRegistry) -> None:
    new = JevAnswer.model_validate(pick("a", 0.9))
    row = line(
        1, new=pick("a", 0.9), old=pick("b", 0.5), claude={"answer": True, "confidence": "high"}
    )  # a noul reply
    verdict = evals.judge(row, new, spec_of(registry, FAMILY), None)
    assert verdict is not None and verdict.source == "old_jev"


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        ("noul", noul_answer(0.5), True),
        ("noul", noul_answer(0.49), False),
        ("noul", noul_answer(0.0), False),
        ("choice", choice_answer("b", options=OPTIONS), "b"),
        ("score", score_answer(3, n_levels=6), 3),
    ],
)
def test_native_values(kind: str, raw: Mapping[str, Any], expected: object) -> None:
    assert evals.native_value(JevAnswer.model_validate(norm(kind, raw))) == expected


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("noul", True, True),
        ("noul", False, False),
        ("noul", "Yes", True),
        ("noul", "no", False),
        ("noul", 1, True),
        ("noul", 0, False),
        ("noul", 2, None),
        ("noul", "maybe", None),
        ("score", 3, 3),
        ("score", 3.0, 3),
        ("score", "4", 4),
        ("score", True, None),
        ("score", 2.5, None),
        ("score", "high", None),
        ("choice", "design_ux", "design_ux"),
        ("choice", "", None),
        ("choice", 4, None),
    ],
)
def test_labels_are_read_in_the_terms_of_the_question(kind: str, value: object, expected: object) -> None:
    assert evals.coerce_label(kind, value) == expected


# ───────────────────────── the incumbent, in the spec's terms ─────────────────────────


def pair(registry: DecisionRegistry, spec_id: str, kind: str, raw: Mapping[str, Any], incumbent: Any) -> Any:
    return evals.incumbent_pair(
        spec_of(registry, spec_id), JevAnswer.model_validate(norm(kind, raw)), incumbent
    )


def test_a_skill_level_is_compared_with_sonnets_band(registry: DecisionRegistry) -> None:
    level_3 = score_answer(3, n_levels=6)  # level 3 is score 58, band 1 (40 to 59)
    assert pair(registry, "skill.level", "score", level_3, {"score": 45, "band": 1}) == (1, 1)
    assert pair(registry, "skill.level", "score", level_3, {"score": 62, "band": 2}) == (1, 2)
    assert pair(registry, "skill.level", "score", level_3, {"score": 62}) == (1, 2)  # no band: band_of(score)
    assert pair(registry, "skill.level", "score", score_answer(5, n_levels=6), {"score": 95, "band": 4}) == (
        4,
        4,
    )
    assert pair(registry, "skill.level", "score", level_3, {"score": "n/a"}) == (None, None)


def test_a_role_fit_is_compared_with_the_band_of_the_alignment(registry: DecisionRegistry) -> None:
    assert pair(registry, "role.fit", "score", score_answer(4, n_levels=6), {"score": 80, "band": 3}) == (
        3,
        3,
    )  # level 4 is 78
    assert pair(registry, "role.fit", "score", score_answer(1, n_levels=6), {"score": 80, "band": 3}) == (
        0,
        3,
    )


def test_a_requirement_is_compared_with_the_have_flag(registry: DecisionRegistry) -> None:
    incumbent = {"have": False, "current": 1, "target": 3, "skill_id": "sql"}
    assert pair(registry, "requirement.have", "noul", noul_answer(0.9), incumbent) == (True, False)
    assert pair(registry, "requirement.have", "noul", noul_answer(0.1), incumbent) == (False, False)


def test_the_closest_role_is_compared_with_the_role_id(registry: DecisionRegistry) -> None:
    incumbent = {"role_id": "r2", "ranking": ["r2", "r1"], "option_order": ["r1", "r2"]}
    raw = choice_answer("r2", options=["r1", "r2"], p=0.9)
    assert pair(registry, "role.closest", "choice", raw, incumbent) == ("r2", "r2")
    assert pair(registry, "role.closest", "choice", choice_answer("r1", options=["r1", "r2"]), incumbent) == (
        "r1",
        "r2",
    )


def test_a_plain_value_incumbent_and_unknown_specs(registry: DecisionRegistry) -> None:
    answer = JevAnswer.model_validate(pick("a", 0.9))
    assert evals.incumbent_pair(None, answer, {"value": "a"}) == ("a", "a")
    assert evals.incumbent_pair(None, answer, {"value": 3}) == ("a", None)  # not a label: no reference
    assert evals.incumbent_pair(None, answer, {"band": 2}) == (None, None)
    assert evals.incumbent_pair(None, answer, None) == (None, None)
    assert evals.incumbent_pair(None, answer, {}) == (None, None)


# ───────────────────────── counting ─────────────────────────


def ten_family_rows() -> list[dict[str, Any]]:
    """Confidences .2 to 1.0; Claude says "a" where the new answer ("a") is right and "b" where it is not."""
    confidences = [0.2, 0.4, 0.55, 0.6, 0.65, 0.75, 0.85, 0.9, 0.95, 1.0]
    right = [False, True, False, True, True, False, True, True, True, True]
    return [
        line(
            i + 1,
            new=pick("a", c),
            old=pick("b", 0.5),
            claude={"choice": "a" if ok else "b"},
            cost=100 * (i + 1),
        )
        for i, (c, ok) in enumerate(zip(confidences, right, strict=True))
    ]


def stats_of(
    rows: list[dict[str, Any]], registry: DecisionRegistry, spec_id: str = FAMILY, **kw: Any
) -> evals.SpecStats:
    defaults = kw.pop("defaults", {FAMILY: {"mode": "primary", "threshold": 0.6}})
    return evals.evaluate(rows, defaults=defaults, registry=registry, **kw).specs[spec_id]


def test_agreement_with_the_reference_and_the_baseline_of_the_old_answer(registry: DecisionRegistry) -> None:
    stats = stats_of(ten_family_rows(), registry)

    assert (stats.rows, stats.answered, stats.errors, stats.scored, stats.agree) == (10, 10, 0, 10, 7)
    assert stats.references == {"claude": 10}
    assert (stats.old_scored, stats.old_agree) == (10, 3)  # the old Jev always said "b"
    as_dict = evals.stats_dict(stats)
    assert as_dict["agreement"] == 0.7
    assert as_dict["old_agreement"] == 0.3
    assert as_dict["cost_micro_usd"] == 5_500
    assert as_dict["cost_per_row_micro_usd"] == 550


def test_accuracy_by_confidence_bin(registry: DecisionRegistry) -> None:
    bins = evals.stats_dict(stats_of(ten_family_rows(), registry))["bins"]

    assert [b["range"] for b in bins] == ["0.0-0.5", "0.5-0.7", "0.7-0.9", "0.9-1.0"]
    assert [(b["scored"], b["agree"]) for b in bins] == [(2, 1), (3, 2), (2, 1), (3, 3)]
    assert [b["accuracy"] for b in bins] == [0.5, 2 / 3, 0.5, 1.0]


@pytest.mark.parametrize(
    ("confidence", "index"),
    [(0.0, 0), (0.4999, 0), (0.5, 1), (0.6999, 1), (0.7, 2), (0.8999, 2), (0.9, 3), (0.9999, 3), (1.0, 3)],
)
def test_bin_edges(confidence: float, index: int) -> None:
    assert evals.bin_index(confidence) == index


def test_escalation_at_the_defaults_threshold_and_the_agreement_of_what_would_be_used(
    registry: DecisionRegistry,
) -> None:
    stats = stats_of(ten_family_rows(), registry)  # threshold 0.6: .2, .4 and .55 are below it

    assert stats.threshold == 0.6
    assert stats.escalated == 3
    assert (stats.used_scored, stats.used_agree) == (7, 6)
    as_dict = evals.stats_dict(stats)
    assert as_dict["escalation_rate"] == 0.3
    assert as_dict["used_agreement"] == pytest.approx(6 / 7)


def test_the_threshold_comes_from_defaults_toml_and_a_spec_without_one_keeps_its_own(
    registry: DecisionRegistry,
) -> None:
    packaged = load_defaults(DEFAULTS_TOML)
    rows = [
        line(1, new=pick("a", 0.4), claude={"choice": "a"}),
        line(2, PROOF, kind="noul", new=noul_at(0.5), claude={"answer": True, "confidence": "high"}),
    ]
    result = evals.evaluate(rows, defaults=packaged, registry=registry)
    assert result.specs[FAMILY].threshold == packaged[FAMILY]["threshold"] == 0.45
    assert result.specs[FAMILY].escalated == 1  # 0.4 < 0.45
    assert result.specs[FAMILY].mode == "primary"
    assert result.specs[PROOF].threshold == 0.6 and result.specs[PROOF].escalated == 1
    # no table in the defaults: the spec's own default is used; an unknown spec has no threshold at all
    own = evals.evaluate([line(1, new=pick("a", 0.4))], defaults={}, registry=registry).specs[FAMILY]
    assert own.threshold == spec_of(registry, FAMILY).default.threshold
    unknown = evals.evaluate([line(1, "made.up", new=pick("a", 0.4))], defaults={}, registry=registry).specs[
        "made.up"
    ]
    assert unknown.threshold is None
    assert evals.stats_dict(unknown)["escalation_rate"] is None


def test_failed_rows_are_counted_apart_and_stay_out_of_every_rate(registry: DecisionRegistry) -> None:
    rows = [
        line(1, new=pick("a", 0.9), claude={"choice": "a"}, cost=40),
        line(2, new=None, error="timeout: slow", cost=0),
        line(3, new=None, error=None),  # no answer and no message: still a failure
        line(4, new=pick("a", 0.9)),
    ]
    rows[3]["new"]["answer"] = {"kind": "choice"}  # a stored answer that does not parse (no confidence)
    stats = stats_of(rows, registry)
    assert (stats.rows, stats.answered, stats.errors, stats.scored) == (4, 1, 3, 1)
    assert evals.stats_dict(stats)["escalation_rate"] == 0.0
    assert evals.stats_dict(stats)["bins"][3]["scored"] == 1


def test_a_decision_with_no_reference_is_answered_but_not_scored(registry: DecisionRegistry) -> None:
    stats = stats_of([line(1, new=pick("a", 0.9))], registry)
    assert (stats.answered, stats.scored) == (1, 0)
    assert stats.references == {"none": 1}
    assert evals.stats_dict(stats)["agreement"] is None
    assert stats.points == []


def test_order_instability_counts_choice_rows_whose_answer_followed_the_options(
    registry: DecisionRegistry,
) -> None:
    rows = [
        line(1, new=pick("a", 0.9), shuffle={"runs": 3, "answers": ["a", "a", "a"], "flips": 0}),
        line(2, new=pick("a", 0.9), shuffle={"runs": 3, "answers": ["a", "b", "c"], "flips": 2}),
        line(
            3, new=pick("a", 0.9), shuffle={"runs": 3, "answers": ["a", None, "b"], "flips": 1}
        ),  # one run failed
        line(4, new=pick("a", 0.9)),  # not shuffled
    ]
    order = evals.stats_dict(stats_of(rows, registry))["order"]
    assert order == {
        "rows": 3,
        "unstable": 2,
        "instability": pytest.approx(2 / 3),
        "runs": 8,
        "flips": 3,
        "flip_rate": pytest.approx(3 / 8),
    }
    assert evals.stats_dict(stats_of([line(1, new=pick("a", 0.9))], registry))["order"] is None


def test_each_spec_is_counted_on_its_own_and_the_total_adds_them_up(registry: DecisionRegistry) -> None:
    rows = [
        line(1, new=pick("a", 0.9), claude={"choice": "a"}),
        line(2, PROOF, kind="noul", new=noul_at(0.95), claude={"answer": False, "confidence": "high"}),
        line(3, PROOF, kind="noul", new=noul_at(0.95), claude={"answer": True, "confidence": "high"}),
    ]
    result = evals.evaluate(rows, defaults={}, registry=registry)
    assert (result.specs[FAMILY].agree, result.specs[PROOF].agree, result.specs[PROOF].scored) == (1, 1, 2)
    total = result.total()
    assert (total.rows, total.scored, total.agree) == (3, 3, 2)
    assert total.bin_scored == [0, 0, 0, 3]


def test_the_incumbent_of_a_shadow_spec_is_the_reference_when_nothing_better_exists(
    registry: DecisionRegistry,
) -> None:
    right = line(1, "skill.level", kind="score", key="skill.level#sql", new=norm("score", score_answer(3, n_levels=6)),
                 incumbent={"score": 45, "band": 1})  # fmt: skip
    wrong = line(2, "skill.level", kind="score", key="skill.level#py", new=norm("score", score_answer(3, n_levels=6)),
                 incumbent={"score": 80, "band": 3})  # fmt: skip
    stats = stats_of([right, wrong], registry, "skill.level", defaults={})
    assert stats.references == {"incumbent": 2}
    assert (stats.scored, stats.agree) == (2, 1)
    assert stats.mode == "shadow" and stats.threshold == 1.0  # the code default of a shadow spec


# ───────────────────────── labels ─────────────────────────


def test_labels_are_read_from_files_and_globs(tmp_path: Path) -> None:
    folder = tmp_path / "labels"
    folder.mkdir()
    write_jsonl(folder / "one.jsonl", [{"id": "x1", "label": "a"}, {"id": "x2", "value": False}])
    write_jsonl(
        folder / "two.jsonl", [{"state_hash": "h", "key": "k", "label": 3}, {"id": "x1", "label": "b"}]
    )
    (folder / "notes.txt").write_text("not a label file", encoding="utf-8")

    labels = evals.load_labels([str(folder / "one.jsonl"), str(folder / "*.jsonl")])

    assert labels.by_id == {"x1": "b", "x2": False}  # the later file wins
    assert labels.by_state_key == {("h", "k"): 3}
    assert len(labels) == 3
    assert sorted(Path(f).name for f in labels.files) == ["one.jsonl", "one.jsonl", "two.jsonl"]


def test_a_glob_without_matches_is_fine_but_a_missing_file_is_not(tmp_path: Path) -> None:
    assert len(evals.load_labels([str(tmp_path / "labels" / "*.jsonl")])) == 0
    assert len(evals.load_labels([])) == 0
    with pytest.raises(evals.EvalError, match="label file not found"):
        evals.load_labels([str(tmp_path / "nope.jsonl")])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json\n", "labels.jsonl:1: not valid JSON"),
        ('["a"]\n', "labels.jsonl:1: expected a JSON object"),
        ('{"id": "x"}\n', "labels.jsonl:1: no 'label'"),
        ('{"label": "a"}\n', "labels.jsonl:1: a label needs an 'id'"),
    ],
)
def test_a_bad_label_line_is_named(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "labels.jsonl"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(evals.EvalError, match=message):
        evals.load_labels([str(path)])


def test_hand_labels_win_even_when_they_are_false_or_zero(registry: DecisionRegistry) -> None:
    rows = [
        line(1, new=pick("a", 0.9), claude={"choice": "b"}),  # Claude disagrees with the label below
        line(
            2, PROOF, kind="noul", new=noul_at(0.9, yes=False), claude={"answer": True, "confidence": "high"}
        ),
        line(3, DEPTH, kind="score", new=norm("score", score_answer(0, n_levels=4)), claude={"level": 2}),
        line(4, new=pick("a", 0.9), claude={"choice": "a"}),  # a label that does not fit a choice
    ]
    labels = evals.Labels(
        by_id={
            str(uuid.UUID(int=1)): "a",
            str(uuid.UUID(int=2)): False,
            str(uuid.UUID(int=3)): 0,
            str(uuid.UUID(int=4)): 7,
        },
    )

    result = evals.evaluate(rows, labels=labels, defaults={}, registry=registry)

    assert (result.labels_used, result.labels_invalid) == (3, 1)
    assert result.specs[FAMILY].references == {"label": 1, "claude": 1}  # row 4 fell through to Claude
    assert result.specs[FAMILY].agree == 2
    assert result.specs[PROOF].references == {"label": 1} and result.specs[PROOF].agree == 1
    assert result.specs[DEPTH].references == {"label": 1} and result.specs[DEPTH].agree == 1


def test_a_label_can_be_tied_to_a_state_and_key_instead_of_a_decision(registry: DecisionRegistry) -> None:
    rows = [
        line(1, new=pick("a", 0.9), state_hash="h1", key=FAMILY),
        line(2, new=pick("a", 0.9), state_hash="h2", key=FAMILY),
    ]
    labels = evals.Labels(by_state_key={("h1", FAMILY): "b"})
    result = evals.evaluate(rows, labels=labels, defaults={}, registry=registry)
    assert result.labels_used == 1
    assert result.specs[FAMILY].references == {"label": 1, "none": 1}
    assert result.specs[FAMILY].agree == 0


# ───────────────────────── the report ─────────────────────────


def test_the_report_prints_a_table_and_writes_json_next_to_the_replay(
    tmp_path: Path, registry: DecisionRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        *ten_family_rows(),
        line(20, PROOF, kind="noul", new=noul_at(0.95), claude={"answer": True, "confidence": "high"}),
    ]
    replay_file = write_jsonl(tmp_path / "replay-jev-2.0.0-x.jsonl", rows)
    toml = DEFAULTS_TOML.replace("threshold = 0.45", "threshold = 0.6")

    assert evals.run_report(replay=replay_file, defaults_toml=toml, registry=registry, now=NOW) == 0

    out = capsys.readouterr().out
    assert "new jev-2.0.0 against old jev-1.13.0: 11 decisions, 0 failed, 0 hand labels used" in out
    for expected in (
        FAMILY,
        PROOF,
        "TOTAL",
        "0.0-0.5",
        "0.5-0.7",
        "0.7-0.9",
        "0.9-1.0",
        "70.0%",
        "30.0%",
        "(3)",
    ):
        assert expected in out
    written = tmp_path / "replay-jev-2.0.0-x.report.json"
    assert f"Wrote {written}" in out
    report = json.loads(written.read_text(encoding="utf-8"))
    assert report["generated_at"] == NOW.isoformat()
    assert report["new_models"] == ["jev-2.0.0"] and report["old_models"] == ["jev-1.13.0"]
    assert set(report["specs"]) == {FAMILY, PROOF}
    assert report["specs"][FAMILY]["agreement"] == 0.7
    assert report["specs"][FAMILY]["escalation_rate"] == 0.3
    assert report["total"]["rows"] == 11 and report["total"]["scored"] == 11
    assert report["bins"] == ["0.0-0.5", "0.5-0.7", "0.7-0.9", "0.9-1.0"]
    assert b"\r" not in written.read_bytes()


def test_the_report_uses_the_hand_labels_it_is_given(tmp_path: Path, registry: DecisionRegistry) -> None:
    rows = ten_family_rows()
    replay_file = write_jsonl(tmp_path / "r.jsonl", rows)
    labels = write_jsonl(
        tmp_path / "labels.jsonl", [{"id": r["id"], "label": "a"} for r in rows]
    )  # "a" is right everywhere
    evals.run_report(replay=replay_file, labels=[str(labels)], registry=registry)
    report = json.loads((tmp_path / "r.report.json").read_text(encoding="utf-8"))
    assert report["specs"][FAMILY]["agreement"] == 1.0
    assert report["specs"][FAMILY]["references"]["label"] == 10
    assert report["labels"] == {"loaded": 10, "used": 10, "invalid": 0}


def test_the_report_says_where_a_file_is_unusable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert evals.run_report(replay=tmp_path / "missing.jsonl") == 1
    assert "cannot open" in capsys.readouterr().err
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"no": "spec"}\n', encoding="utf-8")
    assert evals.run_report(replay=bad) == 1
    assert "bad.jsonl:1: not a replay line" in capsys.readouterr().err
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    assert evals.run_report(replay=empty) == 1
    assert "has no rows" in capsys.readouterr().err
    assert (
        evals.run_report(
            replay=write_jsonl(tmp_path / "ok.jsonl", ten_family_rows()),
            labels=[str(tmp_path / "nope.jsonl")],
        )
        == 1
    )
    assert "label file not found" in capsys.readouterr().err
    assert not (tmp_path / "ok.report.json").exists()


# ───────────────────────── promote: the threshold search ─────────────────────────


def brute_force(points: list[tuple[float, bool]], target: float, min_rows: int) -> float | None:
    """The definition, spelled out: step through k/100 and keep the first that qualifies."""
    for k in range(101):
        threshold = k / 100
        above = [ok for confidence, ok in points if confidence >= threshold]
        if len(above) < min_rows:
            return None
        if sum(above) / len(above) + 1e-9 >= target:
            return threshold
    return None


def ramp() -> list[tuple[float, bool]]:
    """100 answers with confidence 0.00 to 0.99; those under 0.50 are wrong, the rest right."""
    return [(i / 100, i >= 50) for i in range(100)]


def test_the_lowest_threshold_that_meets_the_target() -> None:
    choice = evals.choose_threshold(ramp(), 0.9, 30)
    assert choice.threshold == 0.45  # 55 answers at or above it, 50 right: 90.9 percent; at 0.44 it is 89.3
    assert choice.rows == 55 and choice.agreement == pytest.approx(50 / 55)
    assert choice.threshold == brute_force(ramp(), 0.9, 30)


def test_a_perfect_ramp_goes_down_to_where_the_wrong_answers_stop() -> None:
    choice = evals.choose_threshold(ramp(), 1.0, 30)
    assert choice.threshold == 0.5 and choice.rows == 50 and choice.agreement == 1.0


def test_the_minimum_number_of_rows_limits_how_low_the_threshold_can_go_and_how_high() -> None:
    assert evals.choose_threshold(ramp(), 1.0, 51).threshold is None  # only 50 answers are all right
    scarce = evals.choose_threshold(ramp(), 0.9, 60)
    assert scarce.threshold is None
    assert scarce.best is not None and scarce.best[2] >= 60  # the best it saw still had enough rows
    assert evals.choose_threshold(ramp()[:20], 0.5, 30).threshold is None  # too few answers in all
    assert evals.choose_threshold([], 0.9, 30).threshold is None


def test_agreement_is_not_monotonic_so_every_step_is_tried() -> None:
    points = [(0.1, True)] * 40 + [(0.5, True)] * 20 + [(0.5, False)] * 20 + [(0.95, True)] * 40
    # at 0.0 to 0.1: 100 of 120; at 0.11 to 0.5: 60 of 80; from 0.51: 40 of 40
    choice = evals.choose_threshold(points, 0.9, 30)
    assert choice.threshold == 0.51 == brute_force(points, 0.9, 30)
    assert choice.rows == 40


def test_exactly_the_target_counts_as_meeting_it() -> None:
    points = [(0.8, True)] * 27 + [(0.8, False)] * 3
    assert evals.choose_threshold(points, 0.9, 30).threshold == 0.0  # 27 of 30 is exactly 90 percent


def test_the_best_agreement_seen_is_reported_when_the_target_is_out_of_reach() -> None:
    points = [(i / 100, i % 3 != 0) for i in range(100)]  # about two thirds right at every level
    choice = evals.choose_threshold(points, 0.99, 30)
    assert choice.threshold is None and choice.best is not None
    assert choice.best[1] < 0.99


def test_the_search_agrees_with_the_definition_on_random_data() -> None:
    import random

    rng = random.Random(1234)
    for _ in range(25):
        n = rng.randint(0, 150)
        points = [(round(rng.random(), 2), rng.random() < 0.8) for _ in range(n)]
        target = rng.choice([0.5, 0.75, 0.9, 0.95])
        floor = rng.choice([1, 10, 30])
        assert evals.choose_threshold(points, target, floor).threshold == brute_force(points, target, floor)


# ───────────────────────── promote: which specs, and the patch ─────────────────────────


def family_stats(points: list[tuple[float, bool]], **refs: int) -> evals.SpecStats:
    stats = evals.SpecStats(FAMILY, mode="primary", threshold=0.8)
    stats.points = points
    stats.scored = len(points)
    stats.references.update(refs or {"claude": len(points)})
    return stats


def test_only_primary_specs_get_a_proposal() -> None:
    evaluation = evals.Evaluation(specs={FAMILY: family_stats(ramp()), "skill.level": family_stats(ramp())})
    defaults = {
        FAMILY: {"mode": "primary", "threshold": 0.8},
        "skill.level": {"mode": "shadow", "threshold": 1.0},
        "chat.web_gate": {"mode": "primary", "threshold": 0.7},
    }

    proposals = evals.propose(evaluation, target=0.9, min_rows=30, defaults=defaults)

    assert [p.spec_id for p in proposals] == [FAMILY, "chat.web_gate"]  # shadow specs are never promoted
    family, gate = proposals
    assert (family.current, family.proposed, family.note) == (0.8, 0.45, "lower")
    assert family.changes and family.rows_above == 55 and family.scored == 100
    assert gate.proposed is None and gate.note == "not in the replay" and not gate.changes


def test_the_notes_say_why_a_spec_has_no_proposal() -> None:
    defaults = {FAMILY: {"mode": "primary", "threshold": 0.8}}

    def proposal(points: list[tuple[float, bool]], target: float = 0.9, floor: int = 30) -> evals.Proposal:
        evaluation = evals.Evaluation(specs={FAMILY: family_stats(points)})
        return evals.propose(evaluation, target=target, min_rows=floor, defaults=defaults)[0]

    few = proposal(ramp()[:20])
    assert few.proposed is None and few.note == "needs 30 scored rows, has 20"

    unreachable = proposal([(i / 100, i % 3 != 0) for i in range(100)])
    assert unreachable.proposed is None and unreachable.note.startswith("target out of reach (best ")
    assert unreachable.current == 0.8 and not unreachable.changes

    # nine right answers at 0.80, two wrong ones just under: the lowest threshold that works is the current one
    same = proposal([(0.8, True)] * 9 + [(0.79, False)] * 2, floor=5)
    assert (same.proposed, same.note, same.changes) == (0.8, "unchanged", False)

    # the wrong answers sit at 0.90, above the current threshold: it has to go up
    higher = proposal([(0.95, True)] * 10 + [(0.9, False)] * 3 + [(0.85, True)] * 5, floor=5)
    assert (higher.proposed, higher.note, higher.changes) == (0.91, "higher", True)


def test_patch_defaults_changes_only_the_threshold_and_keeps_the_comments() -> None:
    new = evals.patch_defaults(DEFAULTS_TOML, {FAMILY: 0.52, "chat.web_gate": 0.75})

    old_lines, new_lines = DEFAULTS_TOML.splitlines(), new.splitlines()
    assert len(old_lines) == len(new_lines)
    changed = [(a, b) for a, b in zip(old_lines, new_lines, strict=True) if a != b]
    assert changed == [
        (
            "threshold = 0.45        # normalised for about 18 options",
            "threshold = 0.52        # normalised for about 18 options",
        ),
        (
            'threshold = 0.8         # a comment that must survive "quoted" words',
            'threshold = 0.75         # a comment that must survive "quoted" words',
        ),
    ]
    before, after = load_defaults(DEFAULTS_TOML), load_defaults(new)
    assert after[FAMILY] == {**before[FAMILY], "threshold": 0.52}
    assert after["chat.web_gate"] == {**before["chat.web_gate"], "threshold": 0.75}
    assert {k: v for k, v in after.items() if k not in (FAMILY, "chat.web_gate")} == {
        k: v for k, v in before.items() if k not in (FAMILY, "chat.web_gate")
    }


def test_the_patcher_handles_every_table_of_the_real_defaults_toml() -> None:
    """The real file has comments, box-drawing rules and quoted table names: each of its thresholds must patch alone."""
    text = evals.defaults_text()
    before = load_defaults(text)
    assert before  # the file parses
    for spec_id, table in before.items():
        if "threshold" not in table:
            continue
        after = load_defaults(evals.patch_defaults(text, {spec_id: 0.33}))
        assert after[spec_id] == {**table, "threshold": 0.33}, spec_id
        assert {k: v for k, v in after.items() if k != spec_id} == {
            k: v for k, v in before.items() if k != spec_id
        }


def test_patch_defaults_adds_a_missing_threshold_or_table() -> None:
    text = '# header\nversion = 1\n\n["a.x"]\nmode = "primary"\n\n["a.y"]\nthreshold = 0.3\n'
    patched = evals.patch_defaults(text, {"a.x": 0.6, "b.z": 0.7})
    assert load_defaults(patched) == {
        "a.x": {"mode": "primary", "threshold": 0.6},
        "a.y": {"threshold": 0.3},
        "b.z": {"threshold": 0.7},
    }
    assert patched.startswith("# header\n") and patched.endswith("\n")


def test_make_patch_is_a_unified_diff_of_defaults_toml_or_nothing() -> None:
    patch = evals.make_patch(DEFAULTS_TOML, {FAMILY: 0.52})
    lines = patch.splitlines()
    assert lines[0] == "--- a/backend/tayyari/decisions/defaults.toml"
    assert lines[1] == "+++ b/backend/tayyari/decisions/defaults.toml"
    assert [x for x in lines if x.startswith(("-", "+")) and not x.startswith(("---", "+++"))] == [
        "-threshold = 0.45        # normalised for about 18 options",
        "+threshold = 0.52        # normalised for about 18 options",
    ]
    assert evals.make_patch(DEFAULTS_TOML, {}) == ""
    assert evals.make_patch(DEFAULTS_TOML, {FAMILY: 0.45}) == ""


# ───────────────────────── promote: the command ─────────────────────────


def promotion_rows() -> list[dict[str, Any]]:
    """60 decisions of first_read.career_family, hand-labelled "a": confidence 0.40 to 0.99.

    From 0.60 up they are all right; below it only every fourth one is.
    """
    rows = []
    for i in range(60):
        confidence = (40 + i) / 100
        right = confidence >= 0.6 or i % 4 == 0
        rows.append(line(i + 1, new=pick("a" if right else "b", confidence), old=pick("a", 0.5)))
    return rows


@pytest.fixture
def promotion(tmp_path: Path) -> tuple[Path, Path]:
    rows = promotion_rows()
    replay_file = write_jsonl(tmp_path / "replay-jev-2.0.0-x.jsonl", rows)
    labels = write_jsonl(tmp_path / "labels.jsonl", [{"id": r["id"], "label": "a"} for r in rows])
    return replay_file, labels


def test_promote_prints_the_table_and_a_patch_and_writes_the_proposal(
    promotion: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    registry: DecisionRegistry,
) -> None:
    replay_file, labels = promotion
    before = evals.defaults_text()  # the packaged file: a promote run must leave it alone

    code = evals.run_promote(
        replay=replay_file,
        target=0.9,
        labels=[str(labels)],
        defaults_toml=DEFAULTS_TOML,
        registry=registry,
        now=NOW,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "new jev-2.0.0, 60 hand labels used" in out
    assert "agree at 90% or better over at least 30 decisions" in out
    assert "-threshold = 0.45        # normalised for about 18 options" in out
    assert "+threshold = 0.55        # normalised for about 18 options" in out
    assert "--- a/backend/tayyari/decisions/defaults.toml" in out
    assert "git apply" in out and "never edits the file" in out
    assert "To adopt jev-2.0.0:" in out and "TAYYARI_JEV__MODEL=jev-2.0.0" in out
    assert "Warning" not in out  # every scored row has a hand label
    # the other primary specs are not in this replay and say so, without a patch
    assert "Not in the replay, thresholds unchanged: first_read.public_proof, chat.web_gate" in out

    saved = json.loads((tmp_path / "replay-jev-2.0.0-x.promote.json").read_text(encoding="utf-8"))
    proposal = next(p for p in saved["proposals"] if p["spec_id"] == FAMILY)
    assert (proposal["current"], proposal["proposed"], proposal["rows_above"], proposal["scored"]) == (
        0.45,
        0.55,
        45,
        60,
    )
    assert proposal["agreement"] == pytest.approx(41 / 45)
    assert proposal["references"]["label"] == 60
    assert saved["target"] == 0.9 and saved["min_rows"] == 30 and "+threshold = 0.55" in saved["patch"]
    patch_file = tmp_path / "replay-jev-2.0.0-x.promote.patch"
    assert patch_file.read_text(encoding="utf-8") == saved["patch"]  # the diff alone, ready for git apply
    assert b"\r" not in patch_file.read_bytes()
    assert f"Wrote {patch_file}" in out
    assert evals.defaults_text() == before  # defaults.toml is never written


def test_promote_matches_the_definition_on_the_same_data(promotion: tuple[Path, Path]) -> None:
    points = [(r["new"]["confidence"], r["new"]["answer"]["choice"] == "a") for r in promotion_rows()]
    assert brute_force(points, 0.9, 30) == 0.55


def test_a_dry_run_writes_no_file(
    promotion: tuple[Path, Path], tmp_path: Path, registry: DecisionRegistry
) -> None:
    replay_file, labels = promotion
    assert (
        evals.run_promote(
            replay=replay_file,
            labels=[str(labels)],
            dry_run=True,
            defaults_toml=DEFAULTS_TOML,
            registry=registry,
        )
        == 0
    )
    assert not (tmp_path / "replay-jev-2.0.0-x.promote.json").exists()
    assert not (tmp_path / "replay-jev-2.0.0-x.promote.patch").exists()
    assert not (tmp_path / "replay-jev-2.0.0-x.report.json").exists()


def test_promote_warns_when_the_agreement_is_only_consistency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], registry: DecisionRegistry
) -> None:
    replay_file = write_jsonl(
        tmp_path / "r.jsonl", promotion_rows()
    )  # no labels, no Claude answers: judged against the old Jev
    assert (
        evals.run_promote(replay=replay_file, dry_run=True, defaults_toml=DEFAULTS_TOML, registry=registry)
        == 0
    )
    out = capsys.readouterr().out
    assert "Warning: 100% of the scored rows were judged against the old Jev or Sonnet's numbers" in out


def test_promote_with_nothing_to_change_prints_no_patch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], registry: DecisionRegistry
) -> None:
    rows = [
        line(i + 1, new=pick("a", 0.5 + i / 200), claude={"choice": "b"}) for i in range(40)
    ]  # always wrong: out of reach
    replay_file = write_jsonl(tmp_path / "r.jsonl", rows)
    assert (
        evals.run_promote(replay=replay_file, dry_run=True, defaults_toml=DEFAULTS_TOML, registry=registry)
        == 0
    )
    out = capsys.readouterr().out
    assert "No threshold changes proposed." in out
    assert "--- a/" not in out
    assert "target out of reach" in out


def test_no_patch_file_is_written_when_nothing_changes(tmp_path: Path, registry: DecisionRegistry) -> None:
    rows = [line(i + 1, new=pick("a", 0.5 + i / 200), claude={"choice": "b"}) for i in range(40)]
    replay_file = write_jsonl(tmp_path / "r.jsonl", rows)
    assert evals.run_promote(replay=replay_file, defaults_toml=DEFAULTS_TOML, registry=registry) == 0
    saved = json.loads((tmp_path / "r.promote.json").read_text(encoding="utf-8"))
    assert saved["patch"] == "" and all(not p["proposed"] for p in saved["proposals"])
    assert not (tmp_path / "r.promote.patch").exists()


@pytest.mark.parametrize("options", [{"target": 0.0}, {"target": 1.5}, {"min_rows": 0}])
def test_promote_rejects_nonsense_options(tmp_path: Path, options: dict[str, Any]) -> None:
    replay_file = write_jsonl(tmp_path / "r.jsonl", promotion_rows())
    assert evals.run_promote(replay=replay_file, **options) == evals.EXIT_USAGE
    assert not (tmp_path / "r.promote.json").exists()


def test_promote_reports_unreadable_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert evals.run_promote(replay=tmp_path / "missing.jsonl") == 1
    assert "cannot open" in capsys.readouterr().err


# ───────────────────────── export, replay and eval together ─────────────────────────


def test_replay_then_report_end_to_end(tmp_path: Path, make_settings: Callable[..., Settings]) -> None:
    question = JevQuestion.choice("Which family?", {"a": "A", "b": "B", "c": "C"}).to_wire()
    corpus = tmp_path / "corpus.jsonl"
    old = {"kind": "choice", "choice": "b", "probabilities": {"b": 0.7}, "confidence": 0.5}
    corpus.write_text(
        "".join(
            json.dumps(
                {
                    "id": str(uuid.UUID(int=i)), "created_at": "2026-10-07T12:00:00+00:00", "spec_id": FAMILY,
                    "spec_version": "2", "pack": "first_read", "subject_ref": FAMILY, "mode": "primary",
                    "jev_model": "jev-1.13.0", "state": {"resume": f"person {i}"}, "question": question,
                    "jev_answer": old, "jev_confidence": 0.5, "claude_answer": {"choice": "a" if i % 4 else "c"},
                    "incumbent": None, "used": True, "source_used": "jev",
                }
            )
            + "\n"
            for i in range(1, 9)
        ),
        encoding="utf-8",
    )  # fmt: skip
    jev = FakeJev({FAMILY: choice_answer("a", options=["a", "b", "c"], p=0.9)}, input_tokens=1_000_000)
    out = tmp_path / "replay-jev-2.0.0.jsonl"

    assert (
        replay.run(
            corpus=corpus,
            model="jev-2.0.0",
            out=out,
            shuffle=2,
            settings=make_settings(),
            client_factory=lambda _s: jev,
        )
        == 0
    )
    assert evals.run_report(replay=out, defaults_toml=DEFAULTS_TOML) == 0

    report = json.loads((tmp_path / "replay-jev-2.0.0.report.json").read_text(encoding="utf-8"))
    family = report["specs"][FAMILY]
    assert family["rows"] == 8 and family["errors"] == 0
    assert family["agreement"] == 6 / 8  # Claude says "a" for six of the eight; the new Jev always says "a"
    assert family["old_agreement"] == 0  # the old Jev always said "b"
    assert family["references"]["claude"] == 8
    assert family["order"]["instability"] == 0.0 and family["order"]["runs"] == 16
    assert len(jev.calls) == 8 + 8 * 2  # a call per person, and two shuffled ones each
    assert family["cost_micro_usd"] == len(jev.calls) * jev_cost_micro("jev-2.0.0", 1_000_000)
    assert report["new_models"] == ["jev-2.0.0"] and report["old_models"] == ["jev-1.13.0"]


# ───────────────────────── registration ─────────────────────────


def test_the_eval_options_parse() -> None:
    parser = cli.build_parser()
    report = parser.parse_args(["eval", "report", "--replay", "r.jsonl", "--labels", "a/*.jsonl", "b.jsonl"])
    assert (report.command, report.eval_command, report.replay) == ("eval", "report", Path("r.jsonl"))
    assert report.labels == ["a/*.jsonl", "b.jsonl"]
    assert parser.parse_args(["eval", "report", "--replay", "r.jsonl"]).labels is None

    promote = parser.parse_args(
        ["eval", "promote", "--replay", "r.jsonl", "--target", "0.95", "--min-rows", "10", "--dry-run"]
    )
    assert (promote.eval_command, promote.target, promote.min_rows, promote.dry_run, promote.labels) == (
        "promote", 0.95, 10, True, None
    )  # fmt: skip
    defaults = parser.parse_args(["eval", "promote", "--replay", "r.jsonl"])
    assert (defaults.target, defaults.min_rows, defaults.dry_run) == (0.9, 30, False)


@pytest.mark.parametrize(
    "argv",
    [
        ["eval"],
        ["eval", "report"],
        ["eval", "promote"],
        ["eval", "promote", "--replay", "r", "--target", "0"],
        ["eval", "promote", "--replay", "r", "--target", "1.5"],
        ["eval", "promote", "--replay", "r", "--target", "high"],
        ["eval", "promote", "--replay", "r", "--min-rows", "0"],
        ["eval", "explain", "--replay", "r"],
    ],
)
def test_eval_needs_an_action_and_sane_numbers(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_eval_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def fake_report(**kwargs: Any) -> int:
        seen.append(("report", kwargs))
        return 0

    def fake_promote(**kwargs: Any) -> int:
        seen.append(("promote", kwargs))
        return 4

    monkeypatch.setattr("tayyari.cli.evals.run_report", fake_report)
    monkeypatch.setattr("tayyari.cli.evals.run_promote", fake_promote)

    assert cli.main(["eval", "report", "--replay", "r.jsonl", "--labels", "l.jsonl"]) == 0
    assert cli.main(["eval", "promote", "--replay", "r.jsonl", "--target", "0.8", "--dry-run"]) == 4
    assert seen == [
        ("report", {"replay": Path("r.jsonl"), "labels": ["l.jsonl"]}),
        (
            "promote",
            {"replay": Path("r.jsonl"), "target": 0.8, "min_rows": 30, "labels": None, "dry_run": True},
        ),
    ]


def test_help_names_the_three_new_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    for command in ("export", "replay", "eval"):
        assert command in out
