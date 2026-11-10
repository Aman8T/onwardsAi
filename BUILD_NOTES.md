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
Use placeholder secrets locally, never the production ones. With `INBOUND_EMAIL_ENABLED=true` and a real
`RESEND_API_KEY`, a local server's reconcile loop lists and fetches the production Resend account's received emails
(the last 100, every tenth pass) and would send copies, the same as production does. Resend's webhook cannot reach
`localhost` either. So drive the inbox through the tests (signed webhook fixtures, `FakeInboundMailProvider`) and the
gallery screens; nothing in the tests calls Resend.
```
# backend/.env (placeholders only): INBOUND_EMAIL_ENABLED=true, RESEND_API_KEY=re_placeholder, RESEND_WEBHOOK_SECRET=whsec_placeholder
npm run dev                # http://localhost:5173/dev/apply?screen=applications-inbox shows the panel on fixtures
uv run --directory backend pytest -q tests/unit/inbox tests/integration/test_inbox_routes.py
npm run check:inbox        # the panel's checks
```
If you need real mail end to end, use a separate Resend account and receiving domain for testing.

## Review points
- **The email on forms.** `apply/service.py` still keeps only the sign-in email (or a `+tag` of it) as the saved
  `email` answer. The tracking address is never saved: `ApplyEngine` swaps it in at fill time through the
  `form_email` seam (`tests/unit/apply/test_form_email.py`), so people whose first check ran before they took an
  address get it too, and turning the address off puts their own email back. Check the review screen shows the
  address that went on the form.
- **Gate fixes (2026-11-10).** The code extractor needs a phrase that names a code ("verify" alone no longer makes
  one, so interview invitations are classified); subjects are scrubbed of code-shaped tokens on every row; our own
  copies coming back are `ignored` (own domain, or the `X-Leafout-Copy` header); the lease is 10 minutes with a
  5-minute classifier timeout and an in-process guard, so a slow model call is never paid twice; the stage is kept
  only when it moved; emails being read count towards the daily cap.
- **Received mail on Resend's side.** Resend keeps received emails under its own retention; the privacy page has to
  say so before the inbox is turned on in production (`docs/auto-apply.md` §5).
- **Resend's receiving API fields are unverified.** The provider reads them defensively; check one real delivery after
  the setup in `backend/DEPLOY.md`.

## Not done (out of A2)
- The auto-apply fill does not read verification codes yet: `latest_code` and `mark_used` are ready, and the place to
  call them is in `docs/auto-apply.md` §3.4.
- No retention window for `inbound_emails` and `inbox_addresses` (codes are purged).
- Not deployed, and no real email was received; the DNS records, the Resend domain and webhook, and the secrets are
  the owner's steps.
