# Handover: map and open points (2026-11-05)

For agents picking this repository up from here. Read `CLAUDE.md` and `project-soul.md` first. This file adds what they don't have:
- the state on 2026-11-05;
- a map of the code;
- a prioritised list of open points, each with evidence and an ID (`ONW-nn`) you can name in a task.

**Where it came from.**
- Branch `claude/hopeful-thompson-x6arb8`, cut from `main` at `444ebce` (2026-11-03). Its only change is this file.
- Written by a Claude Code cloud session:
  - every gate was run in that session;
  - two reader agents read the roadmap and research docs in full;
  - the highest-impact items were spot-checked in the code.
- A second pass was still running when this file was first pushed: one reader per subsystem, an adversarial reviewer for every finding, and a second vote on high-severity ones. Its results go into section 5 in a follow-up commit. If section 5 still says "pending", that pass never landed: re-check `[lead]` items before acting on them.

**Tags.** Line numbers are as of `444ebce`.

| Tag | Meaning |
|---|---|
| `[verified]` | Checked in this session, by running it or reading the code. |
| `[lead]` | Reported with file:line evidence by a reader agent. Not independently re-checked. |
| `[docs]` | Stated in the repo's docs. Not checked against code. |

---

## 1. State at a glance

| | |
|---|---|
| Commit | `444ebce`; the branch equals `main` apart from this file |
| GitHub | no issues, no PRs; only `main` on origin; no tags |
| CI | none (there is no `.github/`); the gate is `npm run check:all`, run by hand |
| Code markers | no TODO, FIXME or HACK anywhere in code |
| Deployed | unknown from here; see below [docs] |
| Live address | `app.leafout.in` (canonicals since `6ca0641`); `CLAUDE.md:9` still says `tayyari.fastapicloud.dev` |

The cloud environment's network policy blocks both app hosts, so the deploy state could not be checked. The docs name 2026-10-23 as the last deploy. If so, R1 and every commit after it look undeployed: `6ca0641`, `29bbd73`, `0d4f26b`, `61a9c6b`, `e404f3c`, `b99c78d`, `444ebce`.

**Gates, run 2026-11-05** (Node 22.23, Python 3.13.18, uv 0.11) `[verified]`

| Step | Result |
|---|---|
| `npm run check` (typecheck, palette, restraint, build, portfolio, match and radar parity) | all pass |
| `npm run lint:py` (ruff check and ruff format) | pass, 665 files |
| `npm run typecheck:py` (mypy strict) | pass, 320 files |
| `npm run test:py` | 10,112 passed, 342 skipped, 0 failed, 502 s |

The 342 skips come from two places:
- **Postgres variants** of the storage tests. They need `TEST_DATABASE_URL` (`backend/tests/unit/storage/conftest.py:34`, `:104`), and nothing in the repo ever sets it.
- **Tests marked `needs_legacy`**, which compare against the deleted `tayyari/legacy` and can never run again: `backend/tests/unit/api/test_uploads.py:55`, `backend/tests/unit/contract/test_models.py:28`, `backend/tests/unit/contract/test_events.py:50`.

**Cloud-session note.** The SessionStart hook only fires when a session starts inside this repo. If `node_modules` or `backend/.venv` is missing, run:

```bash
CLAUDE_PROJECT_DIR="$PWD" CLAUDE_CODE_REMOTE=true bash .claude/hooks/cloud-setup.sh
```

---

## 2. Map

### What it is

Tayyari turns a resume and public links (plus GitHub and deployed apps) into an evidence-cited skill graph. From there:
- next roles;
- a posting match: requirement by requirement against the evidence, then a resume and a cover note built only from cited facts;
- practice interviews, a prepare plan, a job search over employers' own boards, a personal weekly newspaper, a portfolio page and a radar card.

Code computes; models classify and phrase into typed slots that code verifies (CLAUDE.md non-negotiables 1 and 2).

### Backend: `backend/tayyari/`

About 78k lines of Python, plus about 105k lines of tests.

| Package | Files | Lines | Role |
|---|---|---|---|
| `core` | 14 | 2,622 | Settings (`TAYYARI_<GROUP>__<FIELD>`), limits, logging with redaction, errors, ids, clock, text |
| `contract` | 20 | 3,582 | Pydantic models, mirrored field for field by `shared/*.ts` |
| `llm` | 12 | 3,718 | `ClaudeClient` (pricing, caching, usage, cost records), the Jev client, the model capability table |
| `decisions` | 17 | 4,349 | Jev decision specs, packs, runner and recorder (modes off, shadow, primary); defaults in TOML |
| `prompts` | 11 | 2,010 | Prompt builders |
| `ingest` | 7 | 3,953 | PDF and DOCX, GitHub, web pages, the SSRF guard; lint-exempt (ONW-21) |
| `sample` | 10 | 272 | The bundled Riya Kapoor persona |
| `analysis` | 13 | 2,420 | Readers, then synthesis, then evidence checks, giving the profile and graph |
| `chat` | 5 | 777 | Coach chat with web search and fetch |
| `portfolio` | 12 | 6,371 | Resume to Portfolio: line-cited facts, sentence grounding (`portfolio/ground.py`), the public page |
| `match` | 22 | 6,550 | Posting match: read the posting, gates, coverage, radar, questions, resume, cover note, PDF and DOCX export |
| `prepare` | 7 | 1,515 | "How do I prepare for this role?" as typed blocks, and stored plans |
| `discover` | 13 | 3,146 | The board store behind Search with AI: employers' own Greenhouse and Lever boards, parsers, vocabulary, quick fit |
| `jobsearch` | 10 | 3,012 | Search with AI: web-search scouts find boards, code narrows, Haiku reads, code checks quotes |
| `weekly` | 10 | 2,124 | The Leafout Weekly, a personal newspaper by email |
| `interview` | 20 | 5,037 | Practice the interview, re-drill weak claims, debrief, Fix steps into Prepare |
| `speech` | 5 | 960 | Sarvam AI speech-to-text and voice, metered against a soft budget |
| `auth` | 8 | 1,678 | Google, emailed link (Resend), the Leafout (Supabase) cookie bridge, server-side sessions |
| `billing` | 7 | 1,044 | Razorpay (INR), Stripe (USD), the entitlement ledger, webhooks |
| `handoff` | 2 | 250 | A resume or links handed over from leafout.in |
| `card` | 12 | 1,625 | The radar card as SVG, PNG (resvg with bundled Geist) and a page; no model call |
| `guard` | 5 | 1,100 | Per-IP fixed-window limits and the daily spend ceiling |
| `storage` | 42 | 8,133 | SQLAlchemy async (Neon Postgres, or SQLite locally), 15 Alembic migrations, repos, retention |
| `api` | 32 | 6,501 | App factory, routes, NDJSON streaming, uploads, CSP and headers, static hosting |
| `cli` | 15 | 4,414 | `dev`, `serve`, `migrate`, `smoke`, `make-sample`, `ingest-check`, `deploy`, `export`, `replay`, `eval` |
| `container.py` | 1 | | The composition root (it also runs the card's daily refresh) |

**Layering.** `backend/tests/unit/test_layering.py` enforces it (`RULES` at line 49):
- `core` < `contract` < `llm` < `decisions` < services < `api`.
- `storage` sees only `core` and `contract`.
- `auth`, `billing`, `discover`, `speech`, `handoff` and `card` see only `core`, `contract` and `storage.ports`.
- `match` may import `portfolio`; `prepare` may import `match`.
- `jobsearch` may import `discover` and `portfolio`.
- `interview` may import `jobsearch`, `discover` and `portfolio`.
- `weekly` may import `discover`, `jobsearch` and `auth.email`.
- `container` and `cli` may import anything.

`CLAUDE.md`'s repository map and layering paragraph omit `prepare`, `weekly` and `handoff` (ONW-40).

**Routes.** The full list is the docstring of `backend/tayyari/api/routes/__init__.py`.

| Area | Routes |
|---|---|
| Health | `/api/health`, `/api/health/deep` |
| Analysis and profile | `POST /api/analyze` (NDJSON), `POST /api/chat` (NDJSON), `/api/profile/{pid}`, `/api/sample-profile` |
| Portfolio | `/api/portfolio*`, public `GET /api/p/{public_id}` |
| Accounts | `/api/auth/*`, `/api/me` (GET, PATCH, DELETE, claim) |
| Billing | `/api/billing/*`, including webhooks for Razorpay and Stripe |
| Match | `/api/match*`: NDJSON match, answers, resume, cover note, PDF and DOCX |
| Discover and Search with AI | `/api/discover/cities`; `/api/jobsearch*` (NDJSON, prepare, referral) |
| Practice | `/api/interview/*`, `/api/speech/*` |
| Prepare and the Weekly | `/api/prepare*`, `/api/weekly/*` |
| Hand-off | `/api/handoff` |
| Radar card | `/api/card*`; public, no account: `/r/{slug}.svg`, `/og/{slug}-{version}.png`, `/u/{slug}` |

**Migrations** (additive only, run at startup under an advisory lock):
- `0001_initial`, `0002_portfolios`, `0003_users`, `0004_billing`, `0005_match`;
- `0006_match_anonymous`, `0007_discover`, `0008_jobsearch`, `0009_interview`, `0010_prepare`;
- `0011_leafout_identity`, `0012_handoffs`, `0013_weekly`, `0014_claim_marks`, `0015_card_snapshots`.

### Frontend: `src/`

About 76k lines, 22k of them CSS. There are no frontend unit tests (ONW-22).

`src/App.tsx` shows one view at a time, chosen by the store (`useApp().view`, `src/store.ts`):
- onboarding, analysis, graph, chat;
- lazy-loaded: portfolio, match, discover, practice, weekly, pricing.

Graph, chat, portfolio, discover and practice need a profile and fall back to onboarding without one. Three mounts sit on every screen: the sign-in sheet, the liquid cursor (`src/light`) and the dark-mode spotlight.

| Dir | Lines | What |
|---|---|---|
| `views/` | 4,231 | Onboarding, Analysis, GraphView (Now, Next), ChatView |
| `match/` | 7,268 | Paste, match, resume, cover note |
| `discover/` | 8,351 | Search with AI, Compare, applications tracker (browser storage) |
| `practice/` | 11,547 | Hub, plan, room (speech), debrief, progress, re-drill |
| `prepare/` | 3,127 | Prepare blocks and plan |
| `portfolio/` | 6,414 | Portfolio kit, newspaper skin, editor |
| `card/`, `weekly/`, `auth/`, `billing/` | 1,480, 1,453, 1,177, 997 | Share sheet, the Weekly, sign-in, pricing |
| `site/` | 1,906 | Public SEO pages, rendered to static HTML at build time by `scripts/build-pages.tsx` (`docs/seo.md`) |
| `components/`, `lib/`, `theme/`, `light/`, `styles/`, `dev/` | 1,208, 887, 1,269, 1,243, 22,125, 374 | Radar, Popover and Ring; the API client; tokens and hue; the cursor light; CSS; the design playground |

The TypeScript contracts are `shared/*.ts`: 13 files, 2,330 lines.

The frontend gate is a set of scripts in `scripts/`:
- `palette-check` (contrast at every hue);
- `restraint-check` (no shadows, blur, glow or thick borders);
- `build-check`;
- `portfolio-parity`, `match-parity` and `radar-parity`.

### Feature status

From the docs; the deploy state could not be checked from here.

| ID | Feature | Status |
|---|---|---|
| F1 | Accounts | Deployed 2026-10-12. Since L1, the app's own Google and link sign-in is off by default (`TAYYARI_AUTH__OWN_SIGN_IN=false`) |
| F2 | Payments and entitlements | Deployed, but sells only once a sign-in provider, the Razorpay secrets and prices are set. Prices default to 0 (`core/settings.py` around 422–428) |
| M1–M3 | Posting match | Deployed 2026-10-12. The `61a9c6b` changes (every resume line becomes a fact; a sendable cover note) are probably not deployed |
| D1 | Discover v1 | Deployed 2026-10-16; its list was then removed by D3. The rest is in `docs/discover-later.md` |
| D2 / D3 | Search with AI / Discover is Search with AI | Deployed 2026-10-16 (`fbf7907`, `b2b2b0e`) |
| I1 | Practice the interview | Deployed 2026-10-17 (`659b7d6`) |
| P1 | Prepare | Deployed 2026-10-19 |
| I2 | Re-drill weak claims; Fix steps into Prepare | Deployed 2026-10-23 (`4126184`, `67cfc99`) |
| R1 | Radar card | Committed (`40b7acf`), **not deployed** |
| L1 | Leafout sign-in (the site's Supabase login) | Deployed 2026-10-23, off until its secrets are set. Its site-repo part, L1c, is in an unknown state |
| W1 | The Leafout Weekly | Deployed 2026-10-23, off until a Resend-verified sender is set (`TAYYARI_WEEKLY__SENDER`) |
| — | The quiet look (default since `0d4f26b`) | Committed 2026-11-03; deploy unknown |
| X1 | Skills table (ESCO and O*NET, canonical skill ids) | **Named "Next" in `CLAUDE.md:125`, but not specified** (see ONW-30) |
| — | Growth: a Claude Code skill calling `/api/match`, a `/gh/<user>` entry, a README launch kit | Idea only (`docs/roadmap.md:174`) |
| — | The Home dragon | Parked. `CLAUDE.md` names a `dragon` branch, but it is not on origin; maybe local on the founder's machine |

### Configuration that decides what is live

Names only, never values. The comments are in `backend/.env.example`.

| Group | Names |
|---|---|
| Models and data | `ANTHROPIC_API_KEY`, `JEV_API_KEY`, `GITHUB_TOKEN`, `DATABASE_URL`, `LOGFIRE_TOKEN` |
| Speech | `SARVAM_API_KEY` (unset means browser speech) |
| The app's own sign-in | `GOOGLE_CLIENT_ID` / `_SECRET`, `RESEND_API_KEY` / `RESEND_FROM` |
| Payments | `RAZORPAY_KEY_ID` / `_KEY_SECRET` / `_WEBHOOK_SECRET`, `STRIPE_SECRET_KEY` / `_WEBHOOK_SECRET` |
| Leafout sign-in (L1) | `TAYYARI_AUTH__SUPABASE_*` |
| Other | `TAYYARI_WEEKLY__SENDER`, `TAYYARI_CARD__REPORT_EMAIL` |

Models: `CLAUDE.md` says every role runs on `claude-haiku-5-5` in `.env` and on the cloud (POC cost mode). The code defaults are Haiku 4.5 for readers and Sonnet 5.5 for synthesis and chat. The guard's daily ceiling is $20.

---

## 3. Open points

Ordered by what to do first. Each item has a tag, the evidence and a next step.

### Do first

**ONW-01 · The match screen claims ESCO is used; no ESCO data exists** `[verified]`
- `src/match/MatchView.tsx:209` tells users "This service uses the ESCO classification of the European Commission", and `scripts/match-parity.tsx:327` enforces that line.
- Nothing under `backend/tayyari/` mentions ESCO. `docs/roadmap.md:119` says "No ESCO yet", and X1 has not started. `project-soul.md:17` also describes the ESCO/O*NET/NCO vocabulary as if it exists.
- This is a user-facing claim without a source.
- Next: remove the line and its parity assertion until X1 lands. Reword `project-soul.md:17` as intent.

**ONW-02 · The interview judge failed strong answers and passed a bluff, which was then offered as a fact** `[docs]`
- Recorded as launch blocker 4 in `docs/research/ai-launch-audit.md:109` and `:204`. It breaks non-negotiable 1.
- `interview/judge.py` is unchanged since `659b7d6`, and raw judge output isn't logged.
- Next: reproduce it with a known-bad fixture: a bluff must never become a fact. Then fix the judge, or make the facts route require verification. Log raw judge output.

**ONW-03 · Analysis ingest may fetch job boards that forbid automation** `[lead]`
- `backend/tayyari/ingest/web.py` refuses LinkedIn specifically (`LINKEDIN_BLOCKED` at :81, logic around :1289–1362) but has no rule for Naukri or other boards.
- `backend/tayyari/api/matching.py:95` refuses Naukri only for match postings. That breaks non-negotiable 4.
- Next: one shared deny-list used by every outbound fetch (ingest, match, Discover, jobsearch, chat WebFetch), with a known-bad test for each.

**ONW-04 · The Analysis screen shows wrong model names and a developer-only hint** `[verified]`
- `src/views/Analysis.tsx:16-17` hard-codes `READER_MODEL = 'claude-haiku-4-5'` and `SYNTH_MODEL = 'claude-sonnet-5-5'`. They are shown as `{agent} · claude-haiku-4-5` (:267) and `claude-sonnet-5-5 · synthesizer` (:403, :484).
- Production runs every role on Haiku 5.5, per `CLAUDE.md`.
- `:471` and `:490-498` show "Claude Code is not signed in on the server. Run: claude auth login" when an error carries `auth`.
- Next: drop the model names from user copy, or send the real ones in the stream. Remove the CLI hint, or show it only in dev.

**ONW-05 · Deploy the pending commits, after confirming what is live** `[docs]`
- R1 and the 2026-11-03 commits look undeployed (section 1). The repo has no deploy log.
- `docs/research/web-audit.md:127` still says "redeploy the app".
- Next, user only, from the founder's machine (`npm run deploy`; never from a cloud session):
  1. Check `/api/health` on `app.leafout.in` for the live build.
  2. Deploy.
  3. Record the deploy in `CLAUDE.md`'s status.

**ONW-06 · Payments cannot sell, and the docs disagree about the prices** `[docs]`
- `CLAUDE.md` says sales need a sign-in provider, the Razorpay secrets and prices, and the prices default to 0.
- `docs/research/ai-launch-audit.md:192` says production has ₹149 and ₹399, while `docs/research/willingness-to-pay.md` recommends ₹199 per pack and ₹599 a month.
- `ai-launch-audit.md:186` says not to open payments until launch blockers 1–3 are fixed. They were fixed in `61a9c6b`, which may not be deployed.
- Next: user decision on prices, then set the secrets on FastAPI Cloud (the user runs `fastapi cloud env set --secret`).

### Next

**ONW-07 · No CI** `[verified]`
- There is no `.github/`, so nothing runs on push.
- `npm run check:all` needs no keys: the tests strip them.
- Next: a GitHub Actions workflow that runs `npm ci`, `uv sync --directory backend --frozen` and `npm run check:all`. Give it a Postgres service and set `TEST_DATABASE_URL`, which also closes ONW-08.

**ONW-08 · The Postgres test variants never run** `[verified]`
- Production is Neon Postgres, but the storage repo-contract tests run only on SQLite unless `TEST_DATABASE_URL` is set (`backend/tests/unit/storage/conftest.py:34`, `:104`). They make up most of the 342 skips.
- Next: run them in CI (ONW-07), and once locally against a throwaway database.

**ONW-09 · A portfolio can't be shared from the UI** `[verified]`
- The editor shows "Share · coming soon" (`src/portfolio/editor/EditorView.tsx:172`), although `GET /api/p/{public_id}` exists.
- `docs/research/web-audit.md:82` notes that the pricing page stopped selling it.
- Next: a design pass for publish and share, then wire it up.

**ONW-10 · The CSP blocks the Google avatar** `[verified]`
- `backend/tayyari/api/security.py:182` is `img-src 'self' data:` (`docs/roadmap.md:1231`).
- Next: add the avatar host to `img-src`, or proxy the picture. The comment at `security.py:35` explains why `img-src` is tight, so keep it tight.

**ONW-11 · No account deletion in the app's own menu** `[docs]`
- `docs/roadmap.md:1235`: only a deletion started on the site lands on `src/auth/AccountDelete.tsx`.
- Next: a design pass, then a delete action calling `DELETE /api/me`.

**ONW-12 · The anonymous 429 doesn't suggest signing in** `[docs]`
- `backend/tayyari/core/errors.py:112`; `ai-launch-audit.md:100`, `:152`.

**ONW-13 · Retention gaps** `[docs]`
- Postings are never purged (`docs/roadmap.md:1228`, `docs/backend.md:874`).
- `discover_runs` has no retention step (`docs/backend.md:864`).
- Next: add purge steps to `storage/maintenance.py` with tests.

**ONW-14 · Search with AI can drain the one shared $20 daily ceiling** `[docs]`
- `ai-launch-audit.md:135`.
- Next: a per-feature sub-ceiling in `guard`.

**ONW-15 · The Weekly can't send yet** `[docs]`
- It needs a Resend-verified leafout.in sender in `TAYYARI_WEEKLY__SENDER` (`docs/roadmap.md:1213`); the sender is still `onboarding@resend.dev` (`web-audit.md:139`).
- `backend/.env.example:416` carries sample news domains.
- Email delivery has never been verified (`ai-launch-audit.md:201`).

**ONW-16 · The match refuses "Rs." and "crores"** `[docs]`
- `_FIGURE` in `backend/tayyari/match/bullets.py:58` accepts only ₹, lakh and crore (`ai-launch-audit.md:134`, `:154`).
- Next: widen the pattern, and add fixtures in both directions.

**ONW-17 · The match and interview prompts carry no current date** `[docs]`
- `ai-launch-audit.md:112`, `:144`. The reader and synthesis prompts do have one (`prompts/reader.py:96`, `prompts/synth.py:136`).

**ONW-18 · Answers don't flow back into documents**
- Practice "From you" facts never reach the match facts (`docs/roadmap.md:433`) `[docs]`.
- A stored resume or cover note is never rebuilt after the person answers questions (`docs/roadmap.md:1227`, `docs/backend.md:193`) `[docs]`.
- A gap answer never becomes a job bullet. That needs a UI to ask which job it belongs to, so design first (`ai-launch-audit.md:125`).

**ONW-19 · Prepare ticks and shipped links don't grow the graph** `[docs]`
- `docs/roadmap.md:450`, `:465`; `project-soul.md:25` promises they will.

### X1, the skills table: named "Next", but nothing is specified

**ONW-30** `[docs]`

**What exists.** Three lines:
- `docs/roadmap.md:25`: "ESCO + O*NET skill table, canonical skill ids | M2 | later | not started".
- `docs/roadmap.md:119`: "No ESCO yet".
- `docs/roadmap.md:173`: seed from ESCO digital skills and O*NET software skills; aliases; `pending_review` for model-proposed skills; canonical ids on requirements and facts; NCO codes through an ISCO-08 crosswalk on Indian government roles.

There is no contract, table, migration, route, prompt, check, wave plan or canvas, and no recorded approval. `CLAUDE.md`'s build steps 1–3 (canvas and the user's yes, spec, waves) come first.

**Design inputs already researched** in `docs/research/open-source-resume-tools.md`:
- **:274.** A Tayyari-owned `skills` table keyed by its own slug:
  - seeded from ESCO's digital skills (1,201 concepts) and O*NET Software Skills;
  - nullable `esco_uri`, `onet_example` and `onet_soc`;
  - aliases from ESCO altLabels and O*NET examples;
  - model proposals go to `pending_review`.
- **:276–283.** Licences and coverage:
  - ESCO is free with mandatory attribution;
  - O*NET data is CC BY 4.0, but its API needs written permission for paid apps;
  - ESCO lacks React, FastAPI, LangChain and similar;
  - import once, pin the version, never call live APIs.
- **:287.** The mapping pipeline:
  1. An Aho-Corasick alias pre-pass.
  2. A Haiku read with a quote checked in code.
  3. An exact alias match, then a pgvector top-k.
  4. A Haiku re-rank with "none of these".
- **:289.** Borrow starmap's review state.

**What X1 would replace.**
- Per-analysis skill slugs: `backend/tayyari/analysis/normalise.py:414`.
- Discover's hand-made vocabulary of about 400 terms: `backend/tayyari/discover/vocab.py`.
- The match's alias tables: `backend/tayyari/match/aliases.py`.
- Name matching in the card and in "Practised".

**Questions to settle with the user before speccing:**
1. Use O*NET at all? `docs/research/job-discovery.md:155` says it is "not a dependency for Indian roles".
2. Coverage beyond ICT (nurses, accountants): import all of ESCO?
3. Embeddings: which vendor or local model, and which secret?
4. Who approves `pending_review` skills, and through what UI or CLI?
5. How to backfill canonical ids onto stored profiles, matches, cards and claim marks with additive-only migrations.
6. The source and licence of the NCO/ISCO crosswalk.
7. Does it need a canvas, given it is mostly backend?

### Cleanup and tech debt

**ONW-20 · Config and tests for the deleted legacy app** `[verified]`
- `backend/pyproject.toml:59` (`extend-exclude`), `:112` (mypy override) and `:143` (coverage omit) still name `tayyari/legacy`.
- The `needs_legacy` tests are listed in section 1.
- Next: delete them. Parity was proven at tag `wave3b`, per the test comment.

**ONW-21 · `ingest/` and `sample/` are still exempt from most lint and type checks** `[verified]`
- `backend/pyproject.toml` per-file ignores and the mypy override say "Wave 1 cleans them up". That cleanup didn't happen.
- `ingest/` holds the SSRF guard, so it deserves the full gate.

**ONW-22 · No frontend unit tests** `[verified]`
- Zero `*.test.ts(x)` for about 76k lines. The typecheck and parity scripts are the only frontend checks.
- Next: add Vitest to `package.json`, which is a dependency change for the user to approve. Cover `src/lib/api.ts` (NDJSON parsing, errors) and `src/store.ts` first.

**ONW-23 · `dist/app-shell.html` is built but unused** `[docs]`
- `api/static.py:285-293` builds the private shell in memory (`docs/seo.md:15`).

**ONW-24 · `contract/match_api.py` has no TypeScript mirror** `[docs]`
- `docs/roadmap.md:1229`.

**ONW-25 · The Discover applications tracker lives only in browser storage** `[docs]`
- `src/discover/applications/stored.ts`; `docs/discover-later.md:28`.

**ONW-26 · The walkthrough has gaps** `[docs]`
- `scripts/walkthrough.md` has no steps for the Weekly, Home, the hand-off, the quiet look or the `61a9c6b` match changes. Its title and step numbers are stale (`docs/roadmap.md:165` calls the match "step 12"; it is 13).

### Docs that disagree with the code or with each other

**ONW-40 · Fix these together in one docs commit.** All tagged `[docs]`, with some checked.

**CLAUDE.md**
- `:9`: the live address is `app.leafout.in`.
- `:105`: the "Status (2026-10-14)" heading covers content through 2026-11-03.
- `:31`, `:35`: the repo map and the layering list miss `prepare`, `weekly` and `handoff`, both in the backend and in `shared/`, and miss `src/weekly`, `src/dev` and `src/lib` `[verified]`.
- `:107`: the `dragon` branch isn't on origin `[verified]`.

**docs/roadmap.md**
- The order table is stale:
  - P1 "building" (:23); I2 "not started" (:24); L1 "not yet deployed" (:27, :892). All three are deployed.
  - D2, D3 and W1 have no rows.
- `:169-172` lists R1 and I1 as "specs to be written".
- `:429`: "Prepare cannot take a step yet" was superseded by I2.
- `:212` names `shared/discovery.ts`; the file is `shared/discover.ts`.
- `:866` puts the card refresh in `storage/maintenance.py`; it is in `container.py`.
- `:824`: a PNG render takes about 240 ms (per CLAUDE.md), not 50 ms.

**L1 spec text that the 2026-10-19 decisions override**
- `:1000` (a canvas board) against `:1181` (no canvas).
- `:905-909` (a cookie written by page script) against `:1185` (a server-set HttpOnly cookie).
- `:966` (reauth) against `:1186` (no reauth).
- `:1022-1025` (anonymous matching turns off) against `:1175` (it stays on).
- `:1172` cites a scratchpad file, `L1-decisions.md`, that isn't in the repo.

**Cost figures disagree**
- Per analysis: `docs/backend.md:49` says $0.30–0.60, `README.md:177` says about $0.02, `ai-launch-audit.md:48` says ₹0.2–0.6.
- Per interview session: `docs/roadmap.md:408` says $0.02; the audit measured ₹0.5–1.

**Audit and SEO docs with items fixed since**
- `ai-launch-audit.md:27`, `:112`, `:113`, `:115`, `:137`; `web-audit.md:1`, `:12`.
- `docs/seo.md:14` against `:67`, and `:63`; `SEO/SeoP1.md:6-12`.
- Fixed in `61a9c6b`, `e404f3c` and `1028967`.

**Status claims that conflict** (each unsure)
- L1 off, per `CLAUDE.md:121`, against "sign-in only through leafout.in in production", per `ai-launch-audit.md:100`.
- R1 "not deployed" against the pricing page listing the radar card as live (`web-audit.md:82`).

### Decisions the user owns

Collected from `docs/roadmap.md` "Open items" (:1238–1243), and the audit's Q1 and Q2 (`ai-launch-audit.md:190-196`).
- Prices, and whether to renew, for example UPI AutoPay as an opt-in.
- The email sender domain and the Google OAuth app; regenerating the Razorpay test keys; the webhook secret.
- The launch audience (Q1). The logistics card for notice period, CTC, languages and registrations (Q2).
- Practice:
  - whether "Use this wording" on "Or say less" keeps `softer` as a fact (`docs/roadmap.md:661`, `:697-698`);
  - the "All" re-drill cap of 6 (`docs/roadmap.md:699`).
- Sonnet for the synthesizer only (`ai-launch-audit.md:171`, `:194`).
- Bands instead of 0–100 scores on the card (`ai-launch-audit.md:178`, `:193`).
- Whether to index the `/u` card pages (`docs/roadmap.md:886`).
- Discover's next sources: the order is in `docs/discover-later.md:45-55`, Ashby first.

---

## 4. Suggested order of work

1. **Code-only fixes that don't need the user:** ONW-01 and ONW-04 (a few lines each, plus checks), ONW-03 (deny-list and tests), ONW-20 and ONW-40.
2. **ONW-02, the interview judge.** Write the failing fixture first.
3. **ONW-07 and ONW-08, CI with Postgres.** This adds a workflow file, so ask before adding it.
4. **With the user:**
   - ONW-05 and ONW-06: confirm the live build, deploy, set prices and secrets.
   - ONW-30 (X1): canvas, spec, waves.
5. **Everything else** by area, each with its own design pass where the UI changes: ONW-09, 10, 11, 13, 14 and 16–19.

Rules that apply to every item (from `CLAUDE.md`):
- No commits, pushes or deploys unless the user asks. Never deploy from a cloud session.
- Never touch `.env` or set secrets.
- `npm run check:all` must be green at the end.
- Add a known-bad fixture for every verifier you touch.
- Report what was and wasn't verified.

---

## 5. Verification pass

*Pending.* Results from the per-subsystem readers and adversarial reviewers will be merged here in a follow-up commit on this branch. They cover:
- analysis, chat and the model layer;
- match, portfolio and Prepare;
- Discover, Search with AI and the Weekly;
- interview and speech;
- accounts, billing, hand-off and the card;
- storage, API, CLI and deploy;
- the frontend shell;
- the feature UIs;
- a backend security sweep.

---

## 6. Re-running the checks

Install, if needed:

```bash
CLAUDE_PROJECT_DIR="$PWD" CLAUDE_CODE_REMOTE=true bash .claude/hooks/cloud-setup.sh
```

The whole gate, no keys needed (about 10 minutes; pytest alone is about 8):

```bash
npm run check:all
```

Run the Postgres variants too:

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost/throwaway npm run test:py
```

Find the open-point IDs in this file:

```bash
grep -n "ONW-" docs/handover.md
```
