# Auto-apply experiment: results

Branch `exp/auto-apply-test`. Status: **in progress** (this file is filled in as runs complete).

## 1. Env findings

- The app loads the Anthropic key as **`ANTHROPIC_API_KEY`** (`backend/tayyari/core/settings.py`, `AliasChoices("ANTHROPIC_API_KEY")`). No other name (`ANTHROPIC_KEY`, `CLAUDE_API_KEY`) is used anywhere in code, config, docs or CI.
- `.env` (repo root) holds `ANTHROPIC_API_KEY` (present, `sk-ant-` prefix) and `BROWSER_USE_API_KEY` (present, `bu_` prefix). Names match what the code reads, so no alias was added.
- `.env` is gitignored (`.gitignore` line 5: `.env`, which covers `backend/.env` too). Only `backend/.env.example` is tracked. No key value was printed, logged or committed; every commit is scanned for `sk-ant-` / `bu_` prefixes before it is made.
- Sonnet model string already used by the repo: **`claude-sonnet-5-5`** (`llm/models.py`, `core/settings.py`). Price per the repo's model table: $2 / 1M input, $10 / 1M output.
- Browser Use project: free tier, $1 one-time credit (balance $0.994 when the experiment started), 10 concurrent sessions, so only the cost-efficient V4 models are allowed (`bu-ultrafast`, `bu-fast`, `gpt-6-luna`, ...).

## 2. Cost estimate before any run (from browser-use.com/pricing.md, 2026-10-28)

Rates used: cloud browser $0.02 per browser-hour (billed per minute, 1-minute minimum, unused reserved time refunded on stop); direct egress $0.20/GB (we disable the $5/GB residential proxy); `bu-ultrafast` bills GPT-6 Luna tokens at the priority rate, 2x standard, so $0.24 / 1M input, $0.024 / 1M cached input, $1.20 / 1M output.

| Arm | Assumptions | Estimate per application |
|---|---|---|
| A: Browser Use agent (`bu-ultrafast`) | ~25 steps x ~10k input tokens (about 70% cached) + ~500 output tokens a step; 4 browser-minutes; ~5 MB | LLM about $0.037 + browser $0.0013 + traffic $0.001 = **about $0.04** (range $0.02 to $0.10). Hard cap per run: `maxCostUsd` $0.15 |
| B: Claude Sonnet 5.5 + Browser Use browser | 2 Claude calls x (6k input + 1.5k output incl. thinking); 3 browser-minutes; ~5 MB | Browser Use about $0.002; Anthropic about $0.054. **About $0.056** (range $0.03 to $0.12) |
| C: replay, no LLM | 1.5 browser-minutes; ~5 MB | **About $0.0015**, all Browser Use |

Budget plan: pilot one A and one B, then size the rest from their real cost. Browser Use stops at $0.80 spent (real balance); Anthropic cap $2.

## 3. Safety measures (every run)

- **Never submits.** An MV3 extension loaded into every Browser Use browser (`common/guard.py`) blocks every non-GET request (form posts, fetch/XHR, beacons, uploads) and every CAPTCHA/bot-challenge provider. Arms B and C add a Playwright route guard with the same rules, and refuse submit-like clicks in code. `guard_check.py` proved a POST from a page is blocked (`BLOCKED`) before any real run, and each run re-probes.
- **No CAPTCHA bypass.** Browser Use solves CAPTCHAs automatically and V4 agent runs cannot turn that off, so the extension stops CAPTCHA scripts from loading at all; standalone browsers (B, C) also set `solveCaptchas: false`. A form that needs a CAPTCHA to be filled is a recorded failure.
- **Fictional applicant only** (`common/profile.py`): example.com email, 555-01xx phone, a generated one-page PDF marked TEST.
- Every standalone browser is stopped with `PATCH /api/v4/browsers/{id} {"action":"stop"}` in a `finally` block; Arm A's agent browser is stopped after scoring.
