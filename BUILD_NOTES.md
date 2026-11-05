# Auto-apply build notes (branch `feat/auto-apply`)

## Step 0: orientation (what we reuse)
1. Stack: React 19 + Vite 6 SPA (`src/`), FastAPI on Python 3.13 (`backend/tayyari/`), contracts in `shared/*.ts` mirrored by `backend/tayyari/contract/*.py` (parity tests).
2. Auth: server sessions (`tayyari_session` cookie; Google, emailed link, Leafout/Supabase). Routes use `api.deps.user_or_anonymous`; anonymous use is allowed while no sign-in provider is configured (`Container.match_anonymous`).
3. DB/ORM: SQLAlchemy 2 async over Neon Postgres or SQLite; tables in `storage/tables.py`, hand-written additive Alembic migrations (a test diffs them against the models), Protocols in `storage/ports.py`, SQL repos in `storage/repos/`, dict fakes in `tests/fakes/`.
4. Background work: no queue. In-process loops from the lifespan (weekly, discover refresh) coordinated through rows/leases. Auto-apply adds a separate worker process that leases rows in `applications` (DB as the queue).
5. Matches: `matches` (+ `postings`, `variants`); a posting has no URL (pasted text), so "Apply" on a match asks for the application link.
6. Claude: every call through `llm.ClaudeClient.structured(...)` with a `purpose`, wrapped by `GuardedClaudeClient` (spend ceiling, cost recorded in `llm_calls`). Haiku via `reader_model`.
7. Untrusted pages: `ingest.web.fetch_text` is the SSRF-guarded fetch; job boards that forbid reading are refused before any fetch (`api.matching.BOARDS_THAT_FORBID_READING`).
8. Limits: `guard` rate limiter (per IP) and spend reservations; auto-apply keeps its own per-person and per-site limits in DB counts so the shared guard is untouched.
9. Layering is enforced by `tests/unit/test_layering.py`; `apply` sits with the model-calling services and never imports `match`.
10. Gate: `npm run check:all` (tsc, palette/restraint CSS checks, build, parity scripts, ruff, mypy strict, ~10k pytest tests). Green on `main` before this branch.

## What was built (commits on `feat/auto-apply`, one per step)
1. **Schema** (`0016_auto_apply`, additive): `applications`, `application_events`, `ats_playbooks`, `run_metrics`, `apply_profiles` (saved details, kept answers = the per-person answer cache, resume file). `ApplyRepo` Protocol, SQL repo, fake; contract test on fake + SQLite (+ Postgres when `TEST_DATABASE_URL` is set). Contract `shared/apply.ts` = `contract/apply.py` (parity test).
2. **Intake + fingerprinting** (`apply/intake.py`): URL shapes, then page markers → greenhouse / lever / ashby / workday / unknown, the form's address, the job from JSON-LD or markup, via the SSRF-guarded fetch. Manual-only sites (LinkedIn, Naukri, Indeed, Glassdoor, …) are never fetched.
3. **Worker + playbook runner** (`apply/forms.py`, `playbook.py`, `answers.py`, `runner.py`, `browser.py`, `engine.py`, `worker.py`): fields read from HTML, built-in Greenhouse/Lever playbooks, fill → screenshot → stop before submit. Playwright driver blocks private hosts and every non-GET request until an approved submit.
4. **Approve and submit** (`apply/service.py`, `stages.py`, `api/routes/apply.py`): approval must carry the review's digest (sha256 of every selector + value + file hash); the worker re-fills the same values, re-checks the digest, then clicks submit and waits for the confirmation page. Stage machine per source (engine / user / email).
5. **UI** (`src/apply/`, `/apply`): paste a link, your details + resume, list with stages, detail with screenshot + every value and its source, needs-your-input form, Approve, manual stage, retry, delete, history. "Apply" on a posting match asks for the application link.
6. **AI fallback** (`apply/agent/`): `BrowserAgent` interface, Gemini Computer Use (REST) and Claude `computer_toolset_20260801` adapters, a guard (no submit click, no Enter, no leaving the site; file fields get the resume), the recorder → draft playbook, replayed at once to make the normal review. Next application on that site uses the draft (no AI); promoted to active after 3 clean fills.
7. **Ashby playbook + email tracking** (`apply/email.py`, `POST /api/apply/email`): Haiku classifier (keyword rules without a key); the quote must be in the email, exactly one open application must match, forward moves only.
8. **Metrics**: `GET /api/apply/metrics` (open locally, `TAYYARI_APPLY__ADMIN_EMAILS` in prod), `npm run apply-metrics`, and a table on `/apply`.

## Env vars (all documented in `backend/.env.example`)
- `AUTO_APPLY_ENABLED` (default false). `GEMINI_API_KEY` (Gemini adapter). `ANTHROPIC_API_KEY` (existing; Claude adapter, screening drafts, email classifier).
- `TAYYARI_APPLY__*`: `AGENT_ORDER`, `GEMINI_MODEL`, `CLAUDE_AGENT_MODEL`, `AGENT_MAX_STEPS`, `ANSWER_MODEL`, `EMAIL_MODEL`, `MAX_PER_DAY`, `MAX_PER_DOMAIN_PER_HOUR`, `DOMAIN_GAP_SECONDS`, `MANUAL_ONLY_DOMAINS`, `WORKER_POLL_SECONDS`, `WORKER_LEASE_SECONDS`, `HEADLESS`, `CHROMIUM_PATH`, `VIEWPORT_WIDTH/HEIGHT`, `NAVIGATION_TIMEOUT_S`, `DRAFT_PROMOTE_AFTER`, `SCREENSHOT_DAYS`, `ADMIN_EMAILS`, `EST_RUN_USD`.

## Run it locally
```
# backend/.env: AUTO_APPLY_ENABLED=true (plus ANTHROPIC_API_KEY / GEMINI_API_KEY for the AI parts)
uv sync --directory backend --group worker
uv run --directory backend playwright install chromium     # once
npm run dev                # API :8787 + web :5173  -> open http://localhost:5173/apply
npm run apply-worker       # in a second terminal (add -- --headed to watch, -- --once for one pass)
npm run apply-metrics
```
Tests: `npm run test:py` (fakes, saved HTML in `backend/tests/data/apply/`). Real Chromium on the saved page, offline: `TAYYARI_TEST_BROWSER=1 uv run --directory backend pytest tests/unit/apply/test_engine.py`.

## Stubbed / not done
- **Gmail**: `EmailSource` + `NoEmailSource`; the app's Google sign-in requests no mail scope, so emails are pasted. Wiring needs a `gmail.readonly` OAuth scope and a poller.
- **Gemini API shape is unverified**: the docs hosts (ai.google.dev, aistudio.google.com) do not resolve from the build sandbox, and job boards are blocked there too. The adapter follows `gemini-2.5-computer-use-preview-10-2025` as of late 2025 (0–999 grid, `functionResponse` with url + screenshot, `safety_decision` never auto-acknowledged); its prices are placeholders. Test with a real key first.
- **Claude adapter** follows the current docs (`computer_toolset_20260801`, `toolset_name` echo, batch rule), but calls the Anthropic SDK directly: its calls are costed into `run_metrics`, not `llm_calls`, and are **not reserved against the daily spend ceiling** yet (`EST_RUN_USD` is unused). Screening drafts and email classification do go through the guarded `ClaudeClient`.
- Live sites never tested: fixtures are modelled on Greenhouse (job-boards), Lever and Ashby markup, not captured from live pages.
- A match's tailored resume PDF is not attached yet (`ResumeSource` seam exists); the saved resume is used.
- Radio groups and custom (React) dropdowns are asked of the person rather than filled.
- Anonymous limits are per browser cookie (a new cookie resets `MAX_PER_DAY`); the global per-site limit still applies.

## Known failure modes
- Invisible reCAPTCHA / hCaptcha on submit: the run stops at `needs_user` with the link; the person re-fills by hand (the filled form is not handed over).
- Ashby (and some React forms) upload the resume through an API call when the file is chosen; that call is blocked until the approved submit, so the upload may not stick → the confirmation check fails → `failed` with "No confirmation".
- Forms that change between review and submit (new required field, A/B test) → `needs_user` "form changed" and a fresh review.
- Greenhouse boards with a react-select dropdown for yes/no questions: asked of the person.
- The worker's host check is literal (no DNS rebinding protection): run the worker in a network-isolated container.

## Decisions to review
- Re-fill-and-compare-digest at submit time instead of keeping a browser session open between review and approval.
- Model-drafted screening answers are filled only at high confidence with valid fact citations and numbers that appear in the cited facts; eligibility, salary, demographic and consent questions are never drafted.
- Draft playbooks are tried before the AI and auto-promote after 3 clean fills (each still reviewed by its person).
- Anonymous use follows the posting match's rule (allowed while no sign-in provider is configured).
- `playwright==1.63.0` added to the dev and new `worker` groups only (not the web deploy); `package.json` gained `apply-worker` / `apply-metrics`. The worker cannot run on FastAPI Cloud (no Chromium): it needs its own host.
- Migration `0016` may collide with another branch's `0016`: re-point `down_revision` when merging.

## Next 5 tasks
1. Run on 5–10 live Greenhouse/Lever/Ashby postings with a test identity (fill only, never approve) and fix the playbooks against real markup; record fixtures from them.
2. Reserve AI fallback runs against the spend ceiling and record them in `llm_calls`; verify the Gemini adapter against current docs with a real key.
3. Attach the match's tailored resume PDF (`ResumeSource` via the match export) and offer "Tailor with a match" on paste-link applications.
4. Handle radio groups and custom dropdowns in the runner (click-to-open + option text), and hand a blocked form to the person pre-filled (a headed "finish it yourself" session).
5. Gmail: add the `gmail.readonly` scope behind a separate consent, poll new messages into `EmailTracker`, and add sign-in claiming of anonymous applications.
