"""``eval``: judge a replay, and propose confidence thresholds for a new Jev model.

    python -m tayyari.cli eval report  --replay FILE [--labels FILE_OR_GLOB ...]
    python -m tayyari.cli eval promote --replay FILE [--target 0.9] [--min-rows 30] [--labels ...] [--dry-run]

Both read the JSONL that ``replay`` wrote (``old`` and ``new`` answers next to each other, plus the Claude answer
and the incumbent value the decision was logged with) and optional hand labels.

Reference. Agreement means "the new answer equals the reference". Per decision the reference is the first of:

    label       a hand label (``{"id": <decision id>, "label": ...}`` lines; the label is the choice label, true or
                false for a yes/no, the level number for a score)
    claude      the Claude fallback or audit answer stored with the decision
    incumbent   the value the decision was compared with (shadow specs: Sonnet's numbers), put in the spec's terms:
                a skill or fit score becomes the band of the new level (`spec.interpret(answer).band`), a
                requirement is its ``have`` flag, the closest role is its ``role_id``
    old_jev     the answer of the Jev model that was logged

The report says how many decisions were judged against each kind, because agreement with the old Jev is
consistency, not accuracy. ``old`` in the table is the old Jev's agreement with the same reference (left out where
the reference IS the old Jev).

``eval report`` prints, per spec: rows and failures, agreement, accuracy by confidence of the new answer (bins
0.0-0.5, 0.5-0.7, 0.7-0.9, 0.9-1.0), the escalation rate at the threshold in ``decisions/defaults.toml`` (the share of
answers below it, which the runner sends to the fallback or leaves neutral), the agreement of the answers at or above
it ("used"), the order instability of the shuffled choice questions and the replay cost. The same numbers are
written as JSON next to the replay file (``<replay>.report.json``).

``eval promote`` goes through the primary specs of ``defaults.toml``: the lowest threshold (in steps of 0.01) whose
answers at or above it agree with the reference at ``--target`` or better, over at least ``--min-rows`` decisions.
It prints a table and a unified diff of ``defaults.toml`` (apply it with ``git apply``). It never edits that file.
Without ``--dry-run`` it also writes the proposal as JSON (``<replay>.promote.json``) and the diff alone
(``<replay>.promote.patch``) next to the replay file.
"""

from __future__ import annotations

import bisect
import difflib
import glob
import json
import re
import sys
from collections import Counter
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tayyari.decisions.config import load_defaults
from tayyari.decisions.packs.service_impl import build_registry
from tayyari.decisions.packs.shadow import band_of
from tayyari.decisions.registry import DecisionRegistry
from tayyari.decisions.spec import DecisionSpec
from tayyari.llm.jev import JevAnswer

EXIT_USAGE = 2

BINS: tuple[tuple[float, float], ...] = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0))
"""Confidence bins of the new answer: ``lo <= confidence < hi`` (the last one includes 1.0)."""
_BIN_EDGES = tuple(hi for _lo, hi in BINS[:-1])
DEFAULT_TARGET = 0.9
DEFAULT_MIN_ROWS = 30
THRESHOLD_STEP = 100
"""Candidate thresholds are ``k / THRESHOLD_STEP``: 0.00, 0.01 ... 1.00."""
REFERENCES = ("label", "claude", "incumbent", "old_jev")
_TARGET_SLACK = 1e-9
DEFAULTS_PATCH_PATH = "backend/tayyari/decisions/defaults.toml"
WEAK_REFERENCE_SHARE = 0.5

_MISSING: Any = object()


class EvalError(ValueError):
    """A replay or label file that cannot be read. The message names the file and line."""


# ───────────────────────── reading files ─────────────────────────


def _read_jsonl(path: Path) -> Iterable[tuple[int, Any]]:
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise EvalError(f"cannot open {path}: {exc.strerror or exc}") from exc
    with handle:
        for number, text in enumerate(handle, start=1):
            if not text.strip():
                continue
            try:
                yield number, json.loads(text)
            except ValueError:
                raise EvalError(f"{path}:{number}: not valid JSON") from None


def load_replay(path: Path) -> list[dict[str, Any]]:
    """The lines of a replay file. Raises `EvalError` for an unreadable file or a line that is not a decision."""
    rows: list[dict[str, Any]] = []
    for number, row in _read_jsonl(path):
        if not isinstance(row, dict) or "spec_id" not in row:
            raise EvalError(f"{path}:{number}: not a replay line (no spec_id)")
        rows.append(row)
    return rows


@dataclass(slots=True)
class Labels:
    """Hand labels by decision id, and by ``(state_hash, key)`` for labels that are not tied to one decision row."""

    by_id: dict[str, Any] = field(default_factory=dict)
    by_state_key: dict[tuple[str, str], Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.by_id) + len(self.by_state_key)

    def lookup(self, row: Mapping[str, Any]) -> Any:
        """The raw label of a replay line, or the module's missing marker (labels can be false or 0)."""
        found = self.by_id.get(str(row.get("id")), _MISSING)
        if found is not _MISSING:
            return found
        return self.by_state_key.get((str(row.get("state_hash")), str(row.get("key"))), _MISSING)


def load_labels(patterns: Sequence[str]) -> Labels:
    """Read label files: each pattern is a path or a glob (the shell may not expand ``*.jsonl``; Windows' does not).

    A line is ``{"id": <decision id>, "label": ...}`` or ``{"state_hash": ..., "key": ..., "label": ...}`` (``value`` works
    for ``label``). Later lines win. A path with no wildcard that does not exist raises `EvalError`; a glob that matches
    nothing is fine.
    """
    labels = Labels()
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if not matches and not glob.has_magic(pattern):
            raise EvalError(f"label file not found: {pattern}")
        for name in matches:
            path = Path(name)
            if not path.is_file():
                continue
            labels.files.append(str(path))
            for number, line in _read_jsonl(path):
                if not isinstance(line, dict):
                    raise EvalError(f"{path}:{number}: expected a JSON object")
                value = line["label"] if "label" in line else line.get("value", _MISSING)
                if value is _MISSING:
                    raise EvalError(f"{path}:{number}: no 'label'")
                if "id" in line:
                    labels.by_id[str(line["id"])] = value
                elif "state_hash" in line and "key" in line:
                    labels.by_state_key[(str(line["state_hash"]), str(line["key"]))] = value
                else:
                    raise EvalError(f"{path}:{number}: a label needs an 'id' (or 'state_hash' and 'key')")
    return labels


# ───────────────────────── answers as comparable values ─────────────────────────


def parse_answer(raw: Any) -> JevAnswer | None:
    """A stored normalised answer as a `JevAnswer`; None for a missing or malformed one."""
    if not isinstance(raw, Mapping):
        return None
    try:
        return JevAnswer.model_validate(dict(raw))
    except (ValidationError, TypeError):
        return None


def native_value(answer: JevAnswer) -> Hashable | None:
    """The answer in its own terms: the label of a choice, ``p >= 0.5`` for a yes/no, the argmax level of a score."""
    if answer.kind == "noul":
        return None if answer.noul is None else answer.noul >= 0.5
    if answer.kind == "choice":
        return answer.choice or None
    return answer.score_level


def coerce_label(kind: str, value: Any) -> Hashable | None:
    """A hand label or stored value in the terms of ``kind``; None when it does not fit."""
    if kind == "noul":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            word = value.strip().lower()
            if word in ("true", "yes", "y", "1"):
                return True
            if word in ("false", "no", "n", "0"):
                return False
        return None
    if kind == "score":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
            return int(value.strip())
        return None
    return value if isinstance(value, str) and value.strip() else None


def claude_value(kind: str, data: Any) -> Hashable | None:
    """The Claude fallback answer (``{"choice"}``, ``{"answer", "confidence"}`` or ``{"level"}``) in the terms of ``kind``."""
    if not isinstance(data, Mapping):
        return None
    if kind == "choice":
        return coerce_label("choice", data.get("choice"))
    if kind == "noul":
        answer = data.get("answer")
        return answer if isinstance(answer, bool) else None
    return coerce_label("score", data.get("level"))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def incumbent_pair(
    spec: DecisionSpec[Any] | None, answer: JevAnswer, incumbent: Any
) -> tuple[Hashable | None, Hashable | None]:
    """``(the answer in the incumbent's terms, the incumbent's value)``; ``(None, None)`` when they cannot be compared.

    The shadow specs log Sonnet's numbers: a ``band`` or ``score`` (skill.level, role.fit) is compared with the band of the
    level `spec.interpret` makes of the answer; ``have`` (requirement.have) with the yes/no; ``role_id``
    (role.closest) with the chosen option. A plain ``{"value": ...}`` incumbent is compared in the answer's own terms.
    """
    if not isinstance(incumbent, Mapping) or not incumbent:
        return None, None
    interpreted: Any = None
    if spec is not None:
        try:
            interpreted = spec.interpret(answer)
        except Exception:
            interpreted = None
    band = getattr(interpreted, "band", None)
    if isinstance(band, int):
        theirs = incumbent.get("band")
        if not isinstance(theirs, int) or isinstance(theirs, bool):
            score = _number(incumbent.get("score"))
            if score is None:
                return None, None
            theirs = band_of(score)
        return band, theirs
    if answer.kind == "noul" and "have" in incumbent:
        return native_value(answer), coerce_label("noul", incumbent["have"])
    if answer.kind == "choice" and "role_id" in incumbent:
        return native_value(answer), coerce_label("choice", incumbent["role_id"])
    if "value" in incumbent:
        return native_value(answer), coerce_label(answer.kind, incumbent["value"])
    return None, None


@dataclass(frozen=True, slots=True)
class Judgement:
    """One decision judged: the reference, the new answer and the old Jev answer in the same terms."""

    source: str
    reference: Hashable
    new: Hashable
    old: Hashable | None
    """The old Jev answer in the same terms; None when it is missing or is itself the reference."""


def judge(
    row: Mapping[str, Any], new: JevAnswer, spec: DecisionSpec[Any] | None, label: Hashable | None = None
) -> Judgement | None:
    """Pick the reference of a decision (label, Claude, incumbent, old Jev) and put both answers in its terms.

    ``label`` is the hand label already coerced to the terms of the answer (None: there is none). Returns None when the
    decision has no reference at all or the new answer cannot be compared with it.
    """
    old = parse_answer((row.get("old") or {}).get("answer")) if isinstance(row.get("old"), Mapping) else None
    kind = new.kind
    new_value = native_value(new)
    old_value = None if old is None else native_value(old)
    if label is not None and new_value is not None:
        return Judgement("label", label, new_value, old_value)
    claude = claude_value(kind, row.get("claude_answer"))
    if claude is not None and new_value is not None:
        return Judgement("claude", claude, new_value, old_value)
    mapped_new, theirs = incumbent_pair(spec, new, row.get("incumbent"))
    if theirs is not None and mapped_new is not None:
        mapped_old = None if old is None else incumbent_pair(spec, old, row.get("incumbent"))[0]
        return Judgement("incumbent", theirs, mapped_new, mapped_old)
    if old_value is not None and new_value is not None:
        return Judgement("old_jev", old_value, new_value, None)
    return None


# ───────────────────────── numbers per spec ─────────────────────────


def bin_index(confidence: float) -> int:
    """The index into `BINS` of a confidence."""
    return bisect.bisect_right(_BIN_EDGES, confidence)


@dataclass(slots=True)
class SpecStats:
    """Counters of one spec over a replay (and ``points``, the scored answers, for ``promote``)."""

    spec_id: str
    mode: str | None = None
    threshold: float | None = None
    rows: int = 0
    errors: int = 0
    answered: int = 0
    scored: int = 0
    agree: int = 0
    old_scored: int = 0
    old_agree: int = 0
    references: Counter[str] = field(default_factory=Counter)
    bin_scored: list[int] = field(default_factory=lambda: [0] * len(BINS))
    bin_agree: list[int] = field(default_factory=lambda: [0] * len(BINS))
    escalated: int = 0
    used_scored: int = 0
    used_agree: int = 0
    order_rows: int = 0
    order_unstable: int = 0
    order_runs: int = 0
    order_flips: int = 0
    cost_micro_usd: int = 0
    points: list[tuple[float, bool]] = field(default_factory=list)

    def absorb(self, other: SpecStats) -> None:
        """Add ``other``'s counters (the TOTAL row)."""
        for name in (
            "rows", "errors", "answered", "scored", "agree", "old_scored", "old_agree", "escalated",
            "used_scored", "used_agree", "order_rows", "order_unstable", "order_runs", "order_flips",
            "cost_micro_usd",
        ):  # fmt: skip
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.references.update(other.references)
        for i in range(len(BINS)):
            self.bin_scored[i] += other.bin_scored[i]
            self.bin_agree[i] += other.bin_agree[i]


@dataclass(slots=True)
class Evaluation:
    specs: dict[str, SpecStats] = field(default_factory=dict)
    new_models: set[str] = field(default_factory=set)
    old_models: set[str] = field(default_factory=set)
    labels_loaded: int = 0
    labels_used: int = 0
    labels_invalid: int = 0

    def total(self) -> SpecStats:
        total = SpecStats("TOTAL")
        for stats in self.specs.values():
            total.absorb(stats)
        return total


def _spec_or_none(registry: DecisionRegistry, spec_id: str) -> DecisionSpec[Any] | None:
    try:
        return registry.spec(spec_id)
    except KeyError:
        return None


def _threshold_of(
    spec_id: str, spec: DecisionSpec[Any] | None, defaults: Mapping[str, Mapping[str, Any]]
) -> tuple[str | None, float | None]:
    """``(mode, threshold)`` of a spec: ``defaults.toml`` first, then the spec's own default."""
    table = defaults.get(spec_id, {})
    mode = table.get("mode", spec.default.mode if spec is not None else None)
    threshold = table.get("threshold", spec.default.threshold if spec is not None else None)
    return mode, None if threshold is None else float(threshold)


def evaluate(
    rows: Iterable[Mapping[str, Any]],
    *,
    labels: Labels | None = None,
    defaults: Mapping[str, Mapping[str, Any]] | None = None,
    registry: DecisionRegistry | None = None,
) -> Evaluation:
    """Count agreement, confidence bins, escalation, order instability and cost per spec over a replay."""
    table = load_defaults() if defaults is None else defaults
    known = build_registry() if registry is None else registry
    result = Evaluation(labels_loaded=0 if labels is None else len(labels))

    for row in rows:
        spec_id = str(row["spec_id"])
        stats = result.specs.get(spec_id)
        spec = _spec_or_none(known, spec_id)
        if stats is None:
            mode, threshold = _threshold_of(spec_id, spec, table)
            stats = result.specs[spec_id] = SpecStats(spec_id, mode=mode, threshold=threshold)
        stats.rows += 1
        stats.cost_micro_usd += int(row.get("cost_micro_usd") or 0)
        old = row.get("old")
        if isinstance(old, Mapping) and old.get("model"):
            result.old_models.add(str(old["model"]))

        new = row.get("new")
        answer = parse_answer(new.get("answer")) if isinstance(new, Mapping) else None
        if row.get("error") or answer is None:
            stats.errors += 1
            continue
        stats.answered += 1
        if isinstance(new, Mapping) and new.get("model"):
            result.new_models.add(str(new["model"]))
        confidence = answer.confidence
        used = stats.threshold is None or confidence >= stats.threshold
        if not used:
            stats.escalated += 1

        label: Hashable | None = None
        if labels is not None:
            raw_label = labels.lookup(row)
            if raw_label is not _MISSING:
                label = coerce_label(answer.kind, raw_label)
                if label is None:
                    result.labels_invalid += 1
                else:
                    result.labels_used += 1

        shuffle = row.get("shuffle")
        if isinstance(shuffle, Mapping) and answer.kind == "choice":
            answers = shuffle.get("answers")
            runs = sum(1 for a in answers if a is not None) if isinstance(answers, list) else 0
            if runs:
                flips = int(shuffle.get("flips") or 0)
                stats.order_rows += 1
                stats.order_unstable += flips > 0
                stats.order_runs += runs
                stats.order_flips += flips

        verdict = judge(row, answer, spec, label)
        if verdict is None:
            stats.references["none"] += 1
            continue
        agrees = verdict.new == verdict.reference
        stats.references[verdict.source] += 1
        stats.scored += 1
        stats.agree += agrees
        stats.points.append((confidence, agrees))
        index = bin_index(confidence)
        stats.bin_scored[index] += 1
        stats.bin_agree[index] += agrees
        if used and stats.threshold is not None:
            stats.used_scored += 1
            stats.used_agree += agrees
        if verdict.old is not None:
            stats.old_scored += 1
            stats.old_agree += verdict.old == verdict.reference
    return result


# ───────────────────────── the report ─────────────────────────


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def stats_dict(stats: SpecStats) -> dict[str, Any]:
    """The JSON form of one spec's numbers."""
    return {
        "rows": stats.rows,
        "answered": stats.answered,
        "errors": stats.errors,
        "scored": stats.scored,
        "agree": stats.agree,
        "agreement": _ratio(stats.agree, stats.scored),
        "old_scored": stats.old_scored,
        "old_agree": stats.old_agree,
        "old_agreement": _ratio(stats.old_agree, stats.old_scored),
        "references": {name: stats.references.get(name, 0) for name in (*REFERENCES, "none")},
        "bins": [
            {
                "range": f"{lo:.1f}-{hi:.1f}",
                "lo": lo,
                "hi": hi,
                "scored": stats.bin_scored[i],
                "agree": stats.bin_agree[i],
                "accuracy": _ratio(stats.bin_agree[i], stats.bin_scored[i]),
            }
            for i, (lo, hi) in enumerate(BINS)
        ],
        "mode": stats.mode,
        "threshold": stats.threshold,
        "escalated": stats.escalated,
        "escalation_rate": _ratio(stats.escalated, stats.answered) if stats.threshold is not None else None,
        "used_scored": stats.used_scored,
        "used_agree": stats.used_agree,
        "used_agreement": _ratio(stats.used_agree, stats.used_scored),
        "order": None
        if not stats.order_rows
        else {
            "rows": stats.order_rows,
            "unstable": stats.order_unstable,
            "instability": _ratio(stats.order_unstable, stats.order_rows),
            "runs": stats.order_runs,
            "flips": stats.order_flips,
            "flip_rate": _ratio(stats.order_flips, stats.order_runs),
        },
        "cost_micro_usd": stats.cost_micro_usd,
        "cost_per_row_micro_usd": _ratio(stats.cost_micro_usd, stats.rows),
    }


def build_report(
    evaluation: Evaluation, replay: Path | str, *, now: datetime | None = None
) -> dict[str, Any]:
    """The report as a JSON-ready dict (what ``eval report`` prints and writes)."""
    moment = now if now is not None else datetime.now(UTC)
    total = evaluation.total()
    return {
        "replay": str(replay),
        "generated_at": moment.isoformat(),
        "new_models": sorted(evaluation.new_models),
        "old_models": sorted(evaluation.old_models),
        "labels": {
            "loaded": evaluation.labels_loaded,
            "used": evaluation.labels_used,
            "invalid": evaluation.labels_invalid,
        },
        "bins": [f"{lo:.1f}-{hi:.1f}" for lo, hi in BINS],
        "specs": {spec_id: stats_dict(stats) for spec_id, stats in sorted(evaluation.specs.items())},
        "total": stats_dict(total),
    }


def usd(micro: int) -> str:
    """Micro-USD as dollars: four decimals, six for amounts under a tenth of a cent."""
    dollars = micro / 1_000_000
    return f"${dollars:.4f}" if micro == 0 or micro >= 1_000 else f"${dollars:.6f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _grid(headers: Sequence[str], rows: Sequence[Sequence[str]], *, left: Sequence[int] = (0,)) -> str:
    """A plain-text table: the columns in ``left`` (default: the first) left-aligned, the others right-aligned."""
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def line(cells: Sequence[str]) -> str:
        parts = [
            cell.ljust(widths[i]) if i in left else cell.rjust(widths[i]) for i, cell in enumerate(cells)
        ]
        return "  " + "  ".join(parts).rstrip()

    rule = "  " + "  ".join("-" * w for w in widths)
    return "\n".join([line(headers), rule, *(line(r) for r in rows)])


def format_report(report: Mapping[str, Any]) -> str:
    """The report as text: a summary table, accuracy by confidence, and the references the agreement rests on."""
    specs: Mapping[str, Mapping[str, Any]] = {**report["specs"], "TOTAL": report["total"]}
    total = report["total"]
    labels = report["labels"]
    old = ", ".join(report["old_models"]) or "?"
    new = ", ".join(report["new_models"]) or "no answers"
    out = [
        f"Replay {report['replay']}",
        f"  new {new} against old {old}: {total['rows']:,} decisions, {total['errors']:,} failed, "
        f"{labels['used']:,} hand labels used"
        + (f" ({labels['invalid']:,} invalid)" if labels["invalid"] else "")
        + f", replay cost {usd(total['cost_micro_usd'])}",
        "",
        "Agreement of the new answer with the reference (old: the old Jev against the same reference; esc: answers below",
        "the defaults.toml threshold; used: agreement of the answers at or above it; order: choice answers that changed",
        "when the options were shuffled)",
    ]
    rows: list[list[str]] = []
    for spec_id, s in specs.items():
        threshold = s["threshold"]
        order = s["order"]
        rows.append(
            [
                spec_id,
                f"{s['rows']:,}",
                f"{s['errors']:,}",
                _pct(s["agreement"]),
                _pct(s["old_agreement"]),
                "-" if threshold is None else f"{threshold:g}",
                _pct(s["escalation_rate"]),
                _pct(s["used_agreement"]),
                _pct(None if order is None else order["instability"]),
                usd(s["cost_micro_usd"]),
            ]
        )
    out.append(_grid(["spec", "rows", "fail", "agree", "old", "thr", "esc", "used", "order", "cost"], rows))

    out += ["", "Accuracy by confidence of the new answer (scored rows in brackets)"]
    bin_rows = [
        [spec_id, *(f"{_pct(b['accuracy'])} ({b['scored']:,})" for b in s["bins"])]
        for spec_id, s in specs.items()
    ]
    out.append(_grid(["spec", *report["bins"]], bin_rows))

    out += ["", "What the agreement was measured against (rows)"]
    ref_rows = [
        [spec_id, *(f"{s['references'][name]:,}" for name in (*REFERENCES, "none"))]
        for spec_id, s in specs.items()
    ]
    out.append(_grid(["spec", "label", "claude", "incumbent", "old Jev", "none"], ref_rows))
    return "\n".join(out)


def report_path(replay: Path) -> Path:
    """``<replay>.report.json`` next to the replay file."""
    return replay.with_name(f"{replay.stem}.report.json")


def promote_path(replay: Path) -> Path:
    """``<replay>.promote.json`` next to the replay file."""
    return replay.with_name(f"{replay.stem}.promote.json")


def patch_path(replay: Path) -> Path:
    """``<replay>.promote.patch`` next to the replay file: the diff alone, ready for ``git apply``."""
    return replay.with_name(f"{replay.stem}.promote.patch")


# ───────────────────────── promote ─────────────────────────


@dataclass(frozen=True, slots=True)
class Choice:
    """The result of the threshold search for one spec."""

    threshold: float | None
    agreement: float | None
    rows: int
    best: tuple[float, float, int] | None
    """Without a threshold: the best ``(threshold, agreement, rows)`` that still had enough rows."""


def choose_threshold(points: Sequence[tuple[float, bool]], target: float, min_rows: int) -> Choice:
    """The lowest threshold ``k / 100`` whose answers at or above it (``confidence >= threshold``, as the runner
    decides) agree at ``target`` or better, with at least ``min_rows`` of them. Agreement is not monotonic in the
    threshold, so every step is tried, lowest first."""
    ordered = sorted(points)
    confidences = [c for c, _ in ordered]
    n = len(ordered)
    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + (1 if ordered[i][1] else 0)
    best: tuple[float, float, int] | None = None
    for k in range(THRESHOLD_STEP + 1):
        threshold = k / THRESHOLD_STEP
        start = bisect.bisect_left(confidences, threshold)
        rows = n - start
        if rows < min_rows or rows == 0:
            break  # a higher threshold keeps fewer rows
        agreement = suffix[start] / rows
        if agreement + _TARGET_SLACK >= target:
            return Choice(threshold, agreement, rows, best)
        if best is None or agreement > best[1]:
            best = (threshold, agreement, rows)
    return Choice(None, None, 0, best)


@dataclass(frozen=True, slots=True)
class Proposal:
    spec_id: str
    current: float | None
    proposed: float | None
    """The lowest threshold that meets the target; None when none does (the current threshold stays)."""
    agreement: float | None
    rows_above: int
    scored: int
    references: dict[str, int]
    note: str

    @property
    def changes(self) -> bool:
        return self.proposed is not None and self.proposed != self.current


def propose(
    evaluation: Evaluation,
    *,
    target: float,
    min_rows: int,
    defaults: Mapping[str, Mapping[str, Any]],
) -> list[Proposal]:
    """One proposal per primary spec of ``defaults`` (in the file's order); specs outside the replay say so."""
    proposals: list[Proposal] = []
    for spec_id, table in defaults.items():
        if table.get("mode") != "primary":
            continue
        current = table.get("threshold")
        current_f = None if current is None else float(current)
        stats = evaluation.specs.get(spec_id)
        if stats is None or not stats.scored:
            proposals.append(Proposal(spec_id, current_f, None, None, 0, 0, {}, "not in the replay"))
            continue
        choice = choose_threshold(stats.points, target, min_rows)
        refs = {name: stats.references.get(name, 0) for name in (*REFERENCES, "none")}
        if choice.threshold is None:
            if stats.scored < min_rows:
                note = f"needs {min_rows} scored rows, has {stats.scored}"
            elif choice.best is not None:
                t, a, n = choice.best
                note = f"target out of reach (best {_pct(a)} at {t:g} over {n} rows)"
            else:
                note = "target out of reach"
            proposals.append(Proposal(spec_id, current_f, None, None, 0, stats.scored, refs, note))
            continue
        note = (
            "unchanged"
            if choice.threshold == current_f
            else ("lower" if current_f is None or choice.threshold < current_f else "higher")
        )
        proposals.append(
            Proposal(
                spec_id, current_f, choice.threshold, choice.agreement, choice.rows, stats.scored, refs, note
            )
        )
    return proposals


_TABLE_HEADER = re.compile(r"^\s*\[\s*(?P<name>[^\[\]]+?)\s*\]\s*(?:#.*)?$")
_THRESHOLD_LINE = re.compile(r"^(?P<head>\s*threshold\s*=\s*)(?P<value>[-+0-9.eE]+)(?P<tail>.*)$")


def _table_name(line: str) -> str | None:
    match = _TABLE_HEADER.match(line)
    if match is None:
        return None
    return match.group("name").strip().strip("\"'")


def patch_defaults(text: str, thresholds: Mapping[str, float]) -> str:
    """``defaults.toml`` text with the ``threshold`` of each listed spec replaced (comments and layout kept).

    A table without a ``threshold`` line gets one right under its header; a spec with no table gets a new one at the end.
    """
    lines = text.splitlines()
    for spec_id, value in thresholds.items():
        shown = repr(round(value, 2))
        start = next((i for i, line in enumerate(lines) if _table_name(line) == spec_id), None)
        if start is None:
            lines += ["", f'["{spec_id}"]', f"threshold = {shown}"]
            continue
        end = next((j for j in range(start + 1, len(lines)) if _table_name(lines[j]) is not None), len(lines))
        for j in range(start + 1, end):
            match = _THRESHOLD_LINE.match(lines[j])
            if match is not None:
                lines[j] = f"{match.group('head')}{shown}{match.group('tail')}"
                break
        else:
            lines.insert(start + 1, f"threshold = {shown}")
    return "\n".join(lines) + "\n"


def defaults_text() -> str:
    """The packaged ``decisions/defaults.toml``."""
    return resources.files("tayyari.decisions").joinpath("defaults.toml").read_text(encoding="utf-8")


def make_patch(old_text: str, thresholds: Mapping[str, float]) -> str:
    """A unified diff of ``defaults.toml`` with ``thresholds`` applied; empty when nothing changes."""
    new_text = patch_defaults(old_text, thresholds)
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{DEFAULTS_PATCH_PATH}",
            tofile=f"b/{DEFAULTS_PATCH_PATH}",
        )
    )


def format_proposals(proposals: Sequence[Proposal], *, target: float, min_rows: int) -> str:
    """The proposals as a table (specs that are in the replay), then the primary specs that are not."""
    present = [p for p in proposals if p.note != "not in the replay"]
    absent = [p.spec_id for p in proposals if p.note == "not in the replay"]
    rows = [
        [
            p.spec_id,
            "-" if p.current is None else f"{p.current:g}",
            "-" if p.proposed is None else f"{p.proposed:g}",
            _pct(p.agreement),
            f"{p.rows_above:,}",
            f"{p.scored:,}",
            ", ".join(f"{n:,} {name}" for name, n in p.references.items() if n and name != "none") or "-",
            p.note,
        ]
        for p in present
    ]
    out = [
        f"Lowest threshold whose answers at or above it agree at {target * 100:g}% or better over at least "
        f"{min_rows} decisions (primary specs)"
    ]
    if rows:
        out.append(
            _grid(
                ["spec", "now", "proposed", "agree", "rows", "scored", "judged against", "note"],
                rows,
                left=(0, 6, 7),
            )
        )
    else:
        out.append("  (none of the primary specs is in this replay)")
    if absent:
        out.append(f"Not in the replay, thresholds unchanged: {', '.join(absent)}")
    return "\n".join(out)


def weak_reference_share(proposals: Sequence[Proposal]) -> float | None:
    """The share of the scored rows of the proposals that were judged against the old Jev or the incumbent only."""
    total = sum(sum(v for k, v in p.references.items() if k != "none") for p in proposals)
    weak = sum(p.references.get("incumbent", 0) + p.references.get("old_jev", 0) for p in proposals)
    return weak / total if total else None


# ───────────────────────── the commands ─────────────────────────


def _load(replay: Path, label_patterns: Sequence[str] | None) -> tuple[list[dict[str, Any]], Labels]:
    rows = load_replay(replay)
    labels = load_labels(label_patterns or [])
    return rows, labels


def run_report(
    *,
    replay: Path,
    labels: Sequence[str] | None = None,
    defaults_toml: str | None = None,
    registry: DecisionRegistry | None = None,
    now: datetime | None = None,
) -> int:
    """Print the report of a replay and write ``<replay>.report.json``. Exit code 1 for an unreadable file or an empty replay.

    ``defaults_toml`` is the text of a ``defaults.toml`` to read the thresholds from (default: the packaged one).
    """
    try:
        rows, loaded = _load(replay, labels)
    except EvalError as exc:
        print(f"eval report: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print(f"eval report: {replay} has no rows", file=sys.stderr)
        return 1
    table = load_defaults(defaults_toml)
    report = build_report(evaluate(rows, labels=loaded, defaults=table, registry=registry), replay, now=now)
    print(format_report(report))
    target = report_path(replay)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"\nWrote {target}")
    return 0


def run_promote(
    *,
    replay: Path,
    target: float = DEFAULT_TARGET,
    min_rows: int = DEFAULT_MIN_ROWS,
    labels: Sequence[str] | None = None,
    dry_run: bool = False,
    defaults_toml: str | None = None,
    registry: DecisionRegistry | None = None,
    now: datetime | None = None,
) -> int:
    """Propose thresholds for the primary specs and print a ``defaults.toml`` patch. Never edits ``defaults.toml``.

    ``defaults_toml`` is the text of the ``defaults.toml`` to read and to patch (default: the packaged one).
    """
    if not 0.0 < target <= 1.0 or min_rows < 1:
        print("eval promote: --target must be in (0, 1] and --min-rows at least 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        rows, loaded = _load(replay, labels)
    except EvalError as exc:
        print(f"eval promote: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print(f"eval promote: {replay} has no rows", file=sys.stderr)
        return 1
    source = defaults_text() if defaults_toml is None else defaults_toml
    table = load_defaults(source)
    evaluation = evaluate(rows, labels=loaded, defaults=table, registry=registry)
    proposals = propose(evaluation, target=target, min_rows=min_rows, defaults=table)
    print(
        f"Replay {replay}: new {', '.join(sorted(evaluation.new_models)) or 'no answers'}, {evaluation.labels_used:,} hand labels used\n"
    )
    print(format_proposals(proposals, target=target, min_rows=min_rows))

    weak = weak_reference_share(proposals)
    if weak is not None and weak >= WEAK_REFERENCE_SHARE:
        print(
            f"\nWarning: {weak * 100:.0f}% of the scored rows were judged against the old Jev or Sonnet's numbers, not a "
            "hand label or a Claude answer. Agreement with them is consistency, not accuracy: add labels before you promote."
        )

    changes = {p.spec_id: p.proposed for p in proposals if p.changes and p.proposed is not None}
    patch = make_patch(source, changes) if changes else ""
    if patch:
        print(
            f"\nPatch for {DEFAULTS_PATCH_PATH} (apply with `git apply`; this command never edits the file):\n"
        )
        print(patch, end="")
    else:
        print("\nNo threshold changes proposed.")

    if not dry_run:
        out = promote_path(replay)
        payload = {
            "replay": str(replay),
            "generated_at": (now if now is not None else datetime.now(UTC)).isoformat(),
            "new_models": sorted(evaluation.new_models),
            "target": target,
            "min_rows": min_rows,
            "proposals": [
                {
                    "spec_id": p.spec_id,
                    "current": p.current,
                    "proposed": p.proposed,
                    "agreement": p.agreement,
                    "rows_above": p.rows_above,
                    "scored": p.scored,
                    "references": p.references,
                    "note": p.note,
                }
                for p in proposals
            ],
            "patch": patch,
        }
        out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"\nWrote {out}")
        if patch:
            patch_file = patch_path(replay)
            patch_file.write_text(patch, encoding="utf-8", newline="\n")
            print(f"Wrote {patch_file} (from the repository root: git apply --ignore-whitespace <that file>)")
    models = sorted(evaluation.new_models)
    if models:
        print(
            f"\nTo adopt {models[0]}: apply the patch, set the pin (JevSettings.model in core/settings.py, or "
            f"TAYYARI_JEV__MODEL={models[0]} in the environment), then deploy."
        )
    return 0


__all__ = [
    "BINS",
    "Choice",
    "EvalError",
    "Evaluation",
    "Judgement",
    "Labels",
    "Proposal",
    "SpecStats",
    "bin_index",
    "build_report",
    "choose_threshold",
    "coerce_label",
    "evaluate",
    "format_report",
    "incumbent_pair",
    "judge",
    "load_labels",
    "load_replay",
    "make_patch",
    "patch_defaults",
    "propose",
    "run_promote",
    "run_report",
]
