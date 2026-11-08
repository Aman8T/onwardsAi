# Auto-apply build notes (A1, branch `feat/auto-apply-build`)

The architecture now lives in `docs/backend.md` ("Auto-apply", the tables, retention and configuration) and the
agreed spec in `docs/auto-apply.md`. This file keeps only what a reviewer of the branch needs on top of those.

## What was built
- **Signed in only.** Every application, saved answer and resume belongs to an account (`user_id` NOT NULL in
  `applications` and `apply_profiles`); the old branch's anonymous path, saved-details form and kept answers are gone.
  The answer bank (`answer_bank`) is the only saved source of a form's values.
- **Start checks and the credit hold.** `POST /api/apply/check` (read-only, uncharged) and the start run the same
  checks (`apply/checks.py`); a start takes a hold `apply:<id>` that is captured on `sent` and released otherwise.
- **The worker runs inside the app** (started by the lifespan when `AUTO_APPLY_ENABLED` is on and
  `BROWSER_USE_API_KEY` is set; `TAYYARI_APPLY__WORKER_IN_APP=false` and `npm run apply-worker` run it as its own
  process). Postgres is the queue (a lease per row).
- **Browser Use REST + CDP.** Each run creates a Browser Use standalone browser through its REST API (v4, raw
  `httpx2`, no SDK), loads the guard extension, drives it with Playwright over CDP and stops it in a `finally`. No
  browser is installed on the server; `TAYYARI_TEST_BROWSER=1` uses a local Chromium for offline tests only.
- **The fill**: the playbook (Greenhouse, Lever, Ashby), the answer bank and defaults in code, cited model answers for
  custom questions, then the field-list loop (Haiku, then Sonnet) for what is still empty. The computer-use agents
  (Gemini, Claude computer use) of the old branch are removed.
- **The submit** replays the approved script with no model, rechecks the digest, commits `submitting`, lifts the guard
  for the playbook's hosts only, attaches the resume and clicks once. No clear outcome is `check_this_one`, never
  retried.
- **Metrics**: one `run_metrics` row per run (fills that were interrupted or put back for capacity included), counted
  per outcome (`MetricsRow`: questions, needs_signin, closed, check_this_one, interrupted, deferred).

## Run it locally
```
# backend/.env: AUTO_APPLY_ENABLED=true, BROWSER_USE_API_KEY=..., ANTHROPIC_API_KEY=...
npm run dev                # API :8787 + web :5173 (the worker runs inside the API) -> http://localhost:5173/apply
npm run apply-metrics
```
Tests: `npm run test:py` (fakes and saved HTML in `backend/tests/data/apply/`; `tests/integration/test_apply_flow.py`
runs start -> fill -> answers -> approve -> one click on fakes). Real Chromium on the saved page, offline:
`TAYYARI_TEST_BROWSER=1 uv run --directory backend pytest tests/unit/apply/test_engine.py`.

## Not done (out of A1)
- Retention for applications abandoned before they finished (only finished ones are purged).
- A `failed -> sent` move ("I applied there" on a failed row) is not allowed.
- The Weekly's in-app role links (`/discover?job=<id>`) need `route.ts` to keep `job` on the roles tab.
- Pricing: applications are uncharged until a price is set (`docs/auto-apply.md` §4).
- Live postings were not run in this build; the fill-only live test (spec A1.6) comes before the beta.
