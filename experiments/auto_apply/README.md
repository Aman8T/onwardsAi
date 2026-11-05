# Auto-apply experiment (Arm A / B / C)

An isolated test of how to auto-fill job applications, with three setups compared on the same real, public postings.
**Nothing is ever submitted.** Results are in [RESULTS.md](RESULTS.md); the proposed production design is in [PLAN.md](PLAN.md).

| Arm | Who reasons | Who drives the browser |
|---|---|---|
| A | Browser Use's hosted V4 agent, its own model (`bu-ultrafast` / `bu-fast`) | Browser Use |
| B | Claude Sonnet 5.5 (`claude-sonnet-5-5`): field mapping, screening answers, next actions | Our Playwright code over CDP, in a Browser Use cloud browser |
| C | No model: replays an Arm B trace (a "playbook") | Our Playwright code, Browser Use cloud browser |

Everything lives in this folder. It is its own `uv` project and does not touch the app (`backend/`, `src/`).

## Setup

1. The repo-root `.env` must hold `ANTHROPIC_API_KEY` and `BROWSER_USE_API_KEY` (it is gitignored). Values are never printed.
2. Install once: `uv sync` (Python 3.13, `browser-use-sdk`, `playwright`, `anthropic`, `httpx`). No local browser download is needed: Playwright only attaches to Browser Use's cloud Chromium over CDP.

## Run

```
cd experiments/auto_apply
uv run python run.py env                      # which keys are present (True/False)
uv run python guard_check.py                  # prove the submit guard (a POST must be BLOCKED), ~$0.0003
uv run python run.py b gh-gitlab              # one Arm B run
uv run python run.py a gh-gitlab              # one Arm A run (--model bu-fast, --max-cost 0.15)
uv run python run.py c gh-gitlab              # replay the newest Arm B trace for that job
uv run python run.py batch b gh-gitlab,lv-palantir
uv run python run.py report                   # markdown table of every run
uv run python summarize.py                    # per-setup tables in USD and INR (the RESULTS.md numbers)
```

Test postings are in `jobs.json` (labels like `gh-gitlab`, `lv-palantir`, `ab-linear`, `cu-workable-huggingface`).
Each run writes `runs/<time>-<arm>-<label>/` (`result.json`, `fields.json` = the form's final DOM state, `screenshot.jpg`,
plus `trace.json` for B or `events.json` for A), appends a line to `results.jsonl`, and updates `spend.json`.

## Guard rails

- **Submit guard** (`common/guard.py`): a Manifest V3 extension in every Browser Use browser blocks every non-GET request
  (form posts, fetch/XHR, beacons, uploads) and all CAPTCHA/challenge providers; B and C also install a Playwright route
  guard and refuse submit-like clicks. Each run probes the guard (`guard_probe: BLOCKED`).
- **No CAPTCHA solving**: V4 agent runs cannot switch Browser Use's solver off, so CAPTCHA scripts are never allowed to
  load; standalone browsers also set `solveCaptchas: false`. A CAPTCHA-gated form is a recorded failure.
- **Budget** (`common/spend.py`, `run.py`): before every run the real credit balance is read; Browser Use runs stop
  once $0.80 is spent, Anthropic is capped at $2. Standalone browsers are always stopped with
  `PATCH /api/v4/browsers/{id} {"action":"stop"}` in a `finally` block (closing CDP does not stop billing).
- **Fake identity only** (`common/profile.py`, generated PDF `common/resume.py`).
- **Secrets**: CDP and live-view URLs are credentials; they are never printed and are redacted from saved events.

## Files

| File | What |
|---|---|
| `run.py` | entry point, budget checks, results and spend logging |
| `arm_a.py`, `arm_b.py`, `arm_c.py` | the three setups |
| `score.py` | field-by-field check against the test profile (same for every arm, on the browser's DOM state) |
| `common/formscan.py` | reads fields, labels, options and values from every frame |
| `common/bu.py` | Browser Use helpers (extension upload, guarded browsers, balance, REST get/stop) |
| `guard_check.py`, `inspect_page.py` | guard proof and a read-only debugging helper |
