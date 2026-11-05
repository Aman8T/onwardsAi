"""The only entry point for experiment runs. One application per invocation (or a capped batch).

    uv run python run.py env                  # which keys are present (True/False only)
    uv run python run.py a <label>            # Arm A: Browser Use agent (bu-ultrafast; --model bu-fast)
    uv run python run.py b <label>            # Arm B: Claude Sonnet + Browser Use browser
    uv run python run.py c <label>            # Arm C: replay the newest successful Arm B trace for <label>
    uv run python run.py batch <arm> <l1,l2>  # several labels, cap-checked before each one
    uv run python run.py report               # results table (markdown) from results.jsonl

Before every run the real credit balance is read; a run whose estimate would take Browser Use spend past $0.80 is
refused, and so is an Anthropic estimate past the $2 cap. After every run `spend.json` and `results.jsonl` are written.
Nothing is ever submitted (see common/guard.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import bu, spend
from common.env import load_env, presence

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
RESULTS = HERE / "results.jsonl"
CREDIT_START_USD = 1.00  # the one-time free credit; spend is measured against the real balance
CODE_VERSION = "v2"  # v1 = first batch; v2 = buttons in Arm B's prompt, error details, typeahead, Ashby read-only POSTs
ESTIMATE = {"A": (0.15, 0.0), "B": (0.01, 0.15), "C": (0.005, 0.0)}  # (Browser Use, Anthropic) per run, USD


def jobs() -> dict[str, dict[str, Any]]:
    return {j["label"]: j for j in json.loads((HERE / "jobs.json").read_text(encoding="utf-8"))}


def bu_spent() -> float:
    """Spend from the real balance; retried, and never lower than the ledger (a failed read is not a free pass)."""
    for attempt in range(4):
        try:
            credit = bu.credit_usd(bu.balance())
            if credit is not None:
                return max(CREDIT_START_USD - credit, 0.0)
        except Exception:
            time.sleep(3 * (attempt + 1))
    return spend.load()["browser_use_usd"] + 0.05  # pessimistic when the balance cannot be read


def preflight(arm: str, max_cost: float | None) -> float:
    est_bu, est_anth = ESTIMATE[arm]
    if arm == "A" and max_cost is not None:
        est_bu = max_cost
    spent = bu_spent()
    if spent + est_bu > spend.BU_STOP_USD:
        raise spend.BudgetExceeded(f"Browser Use spent {spent:.4f} (real balance) + estimate {est_bu:.3f} > ${spend.BU_STOP_USD}")
    spend.check(bu_estimate=0.0, anthropic_estimate=est_anth)
    return spent


def latest_trace(label: str) -> Path | None:
    for line in reversed(RESULTS.read_text(encoding="utf-8").splitlines() if RESULTS.exists() else []):
        r = json.loads(line)
        if r.get("arm") == "B" and r.get("label") == label and (r.get("score") or {}).get("required_filled"):
            trace = HERE / r["run_dir"] / "trace.json"
            if trace.exists():
                return trace
    return None


def one(arm: str, label: str, model: str, max_cost: float) -> dict[str, Any]:
    job = jobs()[label]
    spent_before = preflight(arm, max_cost if arm == "A" else None)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    run_dir = RUNS / f"{stamp}-{arm}-{label}"
    run_dir.mkdir(parents=True)
    t0 = time.monotonic()
    try:
        if arm == "A":
            import arm_a

            result = arm_a.run(job, run_dir, model=model, max_cost=max_cost)
        elif arm == "B":
            import arm_b

            result = arm_b.run(job, run_dir)
        else:
            import arm_c

            trace = latest_trace(label)
            if trace is None:
                raise SystemExit(f"no Arm B trace with filled fields for {label}")
            result = arm_c.run(job, run_dir, trace)
    except spend.BudgetExceeded:
        raise
    except Exception as exc:
        result = {"arm": arm, "failure": f"crashed: {type(exc).__name__}: {str(exc)[:200]}",
                  "browser_use_usd": 0.0, "anthropic_usd": 0.0}  # fmt: skip
    time.sleep(4)  # let the balance settle after the browser stopped
    spent_after = bu_spent()
    result.update(version=CODE_VERSION, label=label, ats=job["ats"], company=job["company"], run_dir=str(run_dir.relative_to(HERE)),
                  wall_seconds=round(time.monotonic() - t0, 1), bu_spent_before=round(spent_before, 6),
                  bu_spent_after=round(spent_after, 6), bu_balance_delta=round(spent_after - spent_before, 6))  # fmt: skip
    bu_cost = max(float(result.get("browser_use_usd") or 0), result["bu_balance_delta"])
    result["bu_cost_counted"] = round(bu_cost, 6)
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    with RESULTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, default=str) + "\n")
    led = spend.record({"kind": f"arm_{arm}", "label": label, "run_dir": result["run_dir"],
                        "browser_use_usd": bu_cost, "anthropic_usd": result.get("anthropic_usd") or 0,
                        "bu_spent_real_balance": round(spent_after, 6)})  # fmt: skip
    s = result.get("score") or {}
    print(f"[{arm}] {label}: success={s.get('success')} required={s.get('required_filled')}/{s.get('required_total')} "
          f"wrong={len(s.get('wrong', []))} resume={s.get('resume_attached')} failure={result.get('failure') or result.get('status')} "
          f"time={result['wall_seconds']}s bu=${bu_cost:.4f} anth=${float(result.get('anthropic_usd') or 0):.4f} "
          f"| totals bu(real)=${spent_after:.4f} anth=${led['anthropic_usd']:.4f}")  # fmt: skip
    return result


def report() -> str:
    rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines()] if RESULTS.exists() else []
    out = ["| # | arm | job | ATS | success | required filled | wrong | resume | steps | time (s) | BU $ | Anthropic $ | failure / note | screenshot |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for n, r in enumerate(rows, start=1):
        s = r.get("score") or {}
        shot = f"{r['run_dir']}/screenshot.jpg" if (HERE / r.get("run_dir", "x") / "screenshot.jpg").exists() else "none"
        note = r.get("failure") or r.get("scan_error") or ("; ".join(r.get("errors", [])[:2]) if r.get("errors") else "")
        out.append(f"| {n} | {r['arm']} | {r['label']} | {r['ats']} | {'yes' if s.get('success') else 'no'} | "
                   f"{s.get('required_filled', '-')}/{s.get('required_total', '-')} | {len(s.get('wrong', []))} | "
                   f"{s.get('resume_attached')} | {r.get('steps', '-')} | {r.get('wall_seconds')} | "
                   f"{r.get('bu_cost_counted', 0):.4f} | {float(r.get('anthropic_usd') or 0):.4f} | {str(note)[:90]} | {shot} |")  # fmt: skip
    return "\n".join(out)


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["env", "a", "b", "c", "batch", "report"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--model", default="bu-ultrafast")
    ap.add_argument("--max-cost", type=float, default=0.15)
    a = ap.parse_args()
    if a.cmd == "env":
        print(presence())
        return 0
    if a.cmd == "report":
        print(report())
        return 0
    if a.cmd == "batch":
        arm, labels = a.rest[0].upper(), a.rest[1].split(",")
        for label in labels:
            try:
                one(arm, label, a.model, a.max_cost)
            except spend.BudgetExceeded as exc:
                print(f"STOP: {exc}")
                return 3
        return 0
    try:
        one(a.cmd.upper(), a.rest[0], a.model, a.max_cost)
    except spend.BudgetExceeded as exc:
        print(f"STOP: {exc}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
