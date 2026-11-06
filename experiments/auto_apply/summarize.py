"""Aggregate results.jsonl into the per-arm tables of RESULTS.md, in USD and INR.

    uv run python summarize.py            # markdown to stdout

Groups: the latest run per job for each setup. A run counts as a success when every required field holds a value,
every mapped personal-detail field is right, and the resume is attached (score.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any

HERE = Path(__file__).resolve().parent
INR_PER_USD = 94.48  # ECB reference rate via api.frankfurter.app, 2026-11-05 (open.er-api.com: 94.62)


def setup_of(r: dict[str, Any]) -> str:
    if r["arm"] == "A":
        return f"A: Browser Use agent ({r.get('model', 'bu-ultrafast')})"
    if r["arm"] == "B":
        name = {"claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-haiku-5-5": "Claude Haiku 5.5"}.get(r.get("model", ""), r.get("model", "Claude"))
        return f"B: {name} + BU browser ({r.get('version', 'v1')})"
    return "C: replay, no LLM"


def inr(usd: float) -> str:
    return f"₹{usd * INR_PER_USD:,.2f}"


def load() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (HERE / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_per_job(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    groups: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if (r.get("failure") or "").startswith("crashed:") and not r.get("score"):
            continue  # a harness/network crash before the browser ran: not a measurement
        groups.setdefault(setup_of(r), {})[r["label"]] = r  # later rows overwrite earlier ones
    return groups


def table(groups: dict[str, dict[str, dict[str, Any]]]) -> str:
    out = ["| Setup | Jobs | Full success | Required fields filled | Resume attached | Median time | Avg Browser Use | Avg Anthropic | **Avg total / application** |",
           "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for name in sorted(groups):
        runs = list(groups[name].values())
        scores = [r.get("score") or {} for r in runs]
        ok = sum(1 for s in scores if s.get("success"))
        req = sum(s.get("required_total") or 0 for s in scores)
        filled = sum(s.get("required_filled") or 0 for s in scores)
        resume_known = [s.get("resume_attached") for s in scores if s.get("resume_attached") is not None]
        bu = mean(float(r.get("bu_cost_counted") or r.get("browser_use_usd") or 0) for r in runs)
        an = mean(float(r.get("anthropic_usd") or 0) for r in runs)
        t = median(float(r.get("wall_seconds") or r.get("seconds") or 0) for r in runs)
        out.append(f"| {name} | {len(runs)} | {ok}/{len(runs)} | {filled}/{req} | {sum(1 for x in resume_known if x)}/{len(resume_known)} | "
                   f"{t:.0f} s | ${bu:.4f} ({inr(bu)}) | ${an:.4f} ({inr(an)}) | **${bu + an:.4f} ({inr(bu + an)})** |")  # fmt: skip
    return "\n".join(out)


def per_run(rows: list[dict[str, Any]]) -> str:
    out = ["| # | Setup | Job | ATS | Success | Required | Wrong | Resume | Steps | Time | Browser Use | Anthropic | Total (₹) | Failure / note | Screenshot |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for n, r in enumerate(rows, start=1):
        s = r.get("score") or {}
        bu, an = float(r.get("bu_cost_counted") or r.get("browser_use_usd") or 0), float(r.get("anthropic_usd") or 0)
        shot = f"`{r['run_dir'].replace(chr(92), '/')}/screenshot.jpg`" if r.get("run_dir") and (HERE / r["run_dir"] / "screenshot.jpg").exists() else "none"
        note = r.get("failure") if r.get("failure") not in (None, "completed") else ""
        if not note and r.get("errors"):
            note = f"{len(r['errors'])} action errors"
        if not note and r["arm"] == "A" and s and s.get("resume_attached") is False:
            note = "agent could not attach the resume"
        steps = r.get("steps", "-")
        out.append(f"| {n} | {setup_of(r)} | {r['label']} | {r['ats']} | {'yes' if s.get('success') else 'no'} | "
                   f"{s.get('required_filled', 0)}/{s.get('required_total', 0)} | {len(s.get('wrong', []))} | {s.get('resume_attached')} | "
                   f"{steps} | {float(r.get('wall_seconds') or r.get('seconds') or 0):.0f} s | ${bu:.4f} | ${an:.4f} | {inr(bu + an)} | "
                   f"{str(note)[:70]} | {shot} |")  # fmt: skip
    return "\n".join(out)


def totals() -> str:
    led = json.loads((HERE / "spend.json").read_text(encoding="utf-8"))
    real = max((r.get("bu_spent_real_balance") or 0) for r in led["runs"]) if led["runs"] else 0
    an = led["anthropic_usd"]
    return (f"- Browser Use, from the real credit balance (includes guard checks and inspections): **${real:.4f} ({inr(real)})** "
            f"of the $0.80 stop line ({inr(0.80)}).\n"
            f"- Anthropic (Sonnet 5.5 and Haiku 5.5, summed from API usage): **${an:.4f} ({inr(an)})** of the $2 cap ({inr(2.0)}).\n"
            f"- Total: **${real + an:.4f} ({inr(real + an)})**. Exchange rate: ₹{INR_PER_USD} per USD.")


if __name__ == "__main__":
    rows = load()
    print("## Per setup (latest run per job)\n")
    print(table(latest_per_job(rows)))
    print("\n## Spend\n")
    print(totals())
    print("\n## Every run\n")
    print(per_run(rows))
