# Tayyari — career intelligence (POC)

Connect a resume, GitHub, Lovable apps and any public link. Claude reads every source, builds an
interactive **skill graph of your current role**, flips it to a **map of the roles you are closest to
next**, and turns every node into a conversation: *"Help me prepare for this topic"*, *"Help me improve
my GitHub"*, *"How do I prepare for this role?"*

Paste a job posting and every requirement in it is checked against your own evidence. A resume and a cover note are then built only from facts you can defend.

Product soul: [project-soul.md](project-soul.md) · Build plan: [docs/roadmap.md](docs/roadmap.md)

Design canvas (all screens, both explored directions): https://claude.ai/artifact/HLxXL14nAUeNHhLFNhozp9

## Requirements

- Node.js 20+ (tested on 21.7) for the React frontend
- `uv` for Python dependency management in the backend (install from https://docs.astral.sh/uv/)
- **Claude Code signed in on this machine** — the backend's LLM calls use Claude Code's credentials
- `ANTHROPIC_API_KEY` and (optionally) `JEV_API_KEY` set in `.env`

## Setup

1. **Install Python dependencies:**

   ```bash
   cd backend
   uv sync
   cd ..
   ```

2. **Configure environment variables:**

   Copy `backend/.env.example` to `backend/.env` and fill in your secrets:

   ```bash
   cp backend/.env.example backend/.env
   ```

   Edit `backend/.env` with:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   JEV_API_KEY=key-...      # or TYPESAFE_API_KEY (optional; enables Jev decisions)
   GITHUB_TOKEN=...          # optional; raises GitHub API limits
   ```

   Never commit `.env`.

   Accounts and payments are optional. Without them the analysis still runs for anonymous visitors. Sign-in, the posting match and purchases need the settings below (names only; values go in `.env`, and on FastAPI Cloud through `fastapi cloud env set --secret NAME`):

   - **Google sign-in:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`. The button stays hidden until both are set.
   - **Email sign-in links (Resend):** `RESEND_API_KEY`, `RESEND_FROM`. Without a key, a local server returns the link on the page so the flow can be tried; any other server hides email sign-in. Unset `RESEND_FROM` uses Resend's shared test sender, which only delivers to your own Resend account.
   - **Razorpay (INR):** `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`. INR plans are offered once the key id and key secret are set; `RAZORPAY_WEBHOOK_SECRET` is optional and enables the webhook.
   - **Stripe (USD):** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`. USD plans are not offered until both are set.
   - **Prices** in minor units (paise and cents; unset or 0 means not on sale): `TAYYARI_BILLING__PACK3_INR`, `TAYYARI_BILLING__PASS30_INR`, `TAYYARI_BILLING__PACK3_USD`, `TAYYARI_BILLING__PASS30_USD`. `TAYYARI_BILLING__FREE_MATCHES_PER_MONTH` (default 1) is the free posting each account gets per calendar month.

3. **Optional: generate the sample profile**

   ```bash
   npm run make-sample
   ```

## Run it

**Development (hot reload, recommended):**

```bash
npm run dev
```

This runs the Python API backend at `http://localhost:8787` (with auto-reload on file changes) and the Vite frontend at `http://localhost:5173` (with HMR). Open `http://localhost:5173` in your browser.

The frontend proxies `/api/` to the backend, so requests go to `http://localhost:8787`.

Alternatively, run each part separately in two terminals:

```bash
# Terminal 1: Python backend
npm run dev:api

# Terminal 2: React frontend
npm run dev:web
```

**Production demo (one process):**

```bash
npm run build:static   # builds and copies frontend to backend/static/
npm run smoke       # tests the API (~10 s, < $0.02)
npm run start          # serves the backend and the built frontend on :8787
```

**Click "Try a sample profile"** for a live run on the bundled fictional persona (Riya Kapoor), or upload your own resume and links. A full analysis takes ~60–90 s. If Claude is unavailable, **Open the pre-computed sample** loads the sample profile with no AI call.

## Pipeline

```
resume / GitHub / Lovable / links / pasted notes
       │  deterministic ingest (backend/tayyari/ingest): PDF/DOCX text, GitHub REST,
       │  HTML + SPA-bundle reading, link classification, private-IP guard
       ▼
Jev first read (in parallel)   Haiku 4.5 readers (one per source, in parallel)
│  triage + quick signals        │ structured JSON, thinking off
│  ~1–2 s                        │ ~5–20 s per source
▼                               ▼
       Sonnet 5.5 synthesizer — skill graph: person, mastery, domains, skills with evidence,
       next roles with alignment + have/gap requirements
       ▼
NDJSON stream → live analysis screen → skill graph (current role) ⇄ flip ⇄ next-roles map
       ▼
click a node → popover → chat seeded with that question
       ▼
Jev web-search gate (decision)   Sonnet 5.5 chat (resumable sessions, WebSearch/WebFetch)
│  allow tools?                   │ reads graph + sources + first-read insights
│                                 │ prompt caching, cache on profile
▼                                 ▼
Evidence checks (post-synth, Jev + code): drop unsupported claims
       ▼
profile + chat history → durable storage (Postgres on FastAPI Cloud, SQLite locally)
```

**Resume → Portfolio.** From the graph screen, "Portfolio" turns the profile into a public-ready portfolio page:
Haiku extracts line-cited facts, Jev reads the career shape, code composes the page from a closed kit (6 compositions,
7 Skill Graph variants, 8 gradient families), Sonnet writes the copy and every sentence is checked against the cited
facts. The same content renders as an "Old Newspaper" front page. Details: `docs/backend.md` → Portfolio.

**Posting match.** Paste a job posting (or give a public link) and Tayyari reads it first, on its own, with no profile in view, into its requirements and how strongly each one is asked. Every requirement is then checked against the person's evidence (resume facts, links, and what they have said in their own words) and lands as covered, partial, synonym only, "you have it but your resume doesn't say", gap, or can't tell. Gate questions decide whether the match is blocked, and a resume and a cover note are then composed from cited facts only. Each file is checked against applicant-system rules before download, and answers to gap questions become facts marked "from you".

Evidence rules: every requirement and gate quotes the posting, and a quote that is not in the posting is dropped. A requirement the posting only implies can never be a must or required. Every fact quotes its source line, and every sentence in the resume and the cover note cites the facts it rests on. A number that no cited fact contains becomes a `[x]` placeholder, which blocks the download until the person settles it. A row without a cited fact is "can't tell", never a gap, and a gate answered "uncertain" never blocks a match. The band and the confidence are computed in code. Instructions inside a posting are reported as anomalies and not followed, and links inside a posting are never opened.

Cost: about half a cent per match on Haiku 5.5, as measured. A match needs no account while no sign-in provider is configured: anonymous matching (`TAYYARI_MATCH__ALLOW_ANONYMOUS`, `auto` by default) lets each client address make 3 a day. With an account, the first match each calendar month is free; after that a pack of three or a 30-day pass is bought. Re-checks after answers, resumes, cover notes and downloads never use up a posting.

Routes: `POST /api/match` (NDJSON), `GET /api/match/{id}`, `GET /api/matches`, `POST /api/match/{id}/answers`, `POST|GET|PATCH /api/match/{id}/resume`, `GET /api/match/{id}/resume.pdf|.docx`, `POST|GET /api/match/{id}/cover`, `GET /api/match/{id}/cover.pdf`. Details: `docs/backend.md` → Posting match.

**Discover, with AI.** Press Search with AI and Claude Haiku 5.5 searches employers' own job sites for roles like yours while Tayyari's own employers are read first; code reads the employers' boards so every role is open today, narrows them by place, kind and level, and Haiku checks each posting against your evidence (every quote checked word for word). Roles arrive in a few seconds as cards (a swipe deck on a phone) with a fit word, pips for the asks checked, and the employer's own apply link. Each role has Prepare (gaps, likely questions, your stories), See how you fit (the posting match), Save (Your applications, kept in this browser), Compare, Share (a three-line referral note) and Not for me. The last results stay until the next search. Details: `docs/backend.md` → Search with AI; the baseline behind it: [docs/research/jobsearch-baseline.md](docs/research/jobsearch-baseline.md).

## Key backend paths

| Path | Module | Purpose |
|------|--------|---------|
| `backend/main.py` | entry | FastAPI app factory; entry point for FastAPI Cloud |
| `backend/tayyari/container.py` | composition | Builds all services, repos, clients; closes them on shutdown |
| `backend/tayyari/core/` | config, logging, errors | Settings (with environment + `.env` cascade), structlog setup, error types, limits, IDs |
| `backend/tayyari/contract/` | types | Pydantic models mirroring `shared/types.ts`; event types (AnalysisEvent, ChatEvent) |
| `backend/tayyari/llm/` | Claude + Jev | Anthropic client (pricing, caching, errors, usage), Jev HTTP adapter, capability table |
| `backend/tayyari/decisions/` | Jev decisions | Spec registry, runner, fallback, recorder; packs: first_read, triage, evidence, chat_gate, shadow |
| `backend/tayyari/analysis/` | readers → synth | Schemas, reader execution, synthesis, normalisation against golden baselines, checks |
| `backend/tayyari/chat/` | chat service | Session store, tool mapping, streaming |
| `backend/tayyari/guard/` | rate limit + spend | Per-IP fixed-window counters, daily spend ceiling, client IP hashing |
| `backend/tayyari/storage/` | persistence | Alembic migrations, SQLAlchemy models, repos for profiles/sessions/calls/decisions |
| `backend/tayyari/prompts/` | text generation | Prompt builders for readers, synthesis, chat (limits interpolated from core) |
| `backend/tayyari/ingest/` | source ingestion | PDF/DOCX parsing, GitHub API, HTML scraping, link classification |
| `backend/tayyari/api/` | HTTP routes | FastAPI app, request deps, NDJSON streaming, file uploads, error handling |
| `backend/tayyari/cli/` | commands | Python entry point: `dev`, `serve`, `migrate`, `smoke`, `make-sample`, `ingest-check`, `deploy` |
| `backend/tayyari/sample/` | fixtures | Bundled sample profile (Riya Kapoor); data source of truth for offline testing |
| `backend/tayyari/auth/` | accounts | Google sign-in (PKCE, ID-token checks), emailed sign-in links, server-side sessions, Resend email |
| `backend/tayyari/billing/` | payments | Products and prices, entitlements (pass, free monthly posting, credits), Razorpay and Stripe gateways, webhooks |
| `backend/tayyari/match/` | posting match | Reading a posting, facts, gates, coverage, radar, questions, resume and cover note, export and lint (pure services) |
| `backend/tests/` | tests | Unit, integration, fakes, golden baselines |

## Configuration (TAYYARI_* settings)

All settings are optional unless noted. Edit `backend/.env.example` for defaults.

### Models and effort

| Variable | Default | Purpose |
|---|---|---|
| `TAYYARI_READER_MODEL` | `claude-haiku-4-5` | Per-source reader model |
| `TAYYARI_SYNTH_MODEL` | `claude-sonnet-5-5` | Skill graph synthesis |
| `TAYYARI_CHAT_MODEL` | `claude-sonnet-5-5` | Career chat |
| `TAYYARI_SYNTH_EFFORT` | `medium` | Synthesis effort: `low`, `medium`, `high`, `xhigh`, `max` |
| `TAYYARI_CHAT_EFFORT` | `medium` | Chat effort |

**POC cost mode (since 2026-10-08):** the local `.env` and the FastAPI Cloud app set all three model variables to
`claude-haiku-5-5` (no Sonnet): about $0.02 per analysis, $0.01 per portfolio and well under a cent per chat turn
(web searches add $0.01 each). Unset the three variables to return to the defaults above.
Haiku 5.5 thinks adaptively by default, which made synthesis slow (48 s before its first token); readers, portfolio
calls and decision fallbacks now run with thinking off, and `TAYYARI_SYNTH_THINKING=false` (also set in both places)
turns it off for synthesis: a full analysis takes ~35 s. `TAYYARI_CHAT_THINKING` exists for chat (on).

### Jev decisions (nested: `TAYYARI_JEV__*`)

| Variable | Default | Purpose |
|---|---|---|
| `TAYYARI_JEV__ENABLED` | `true` | Master switch; `false` disables all Jev calls |
| `TAYYARI_JEV__MODEL` | `jev-1.13.0` | Pinned model (logged per decision, used for replay) |
| `TAYYARI_DECISION_OVERRIDES` | `{}` | JSON dict to override `decisions/defaults.toml`, e.g. `{"evidence.support":{"mode":"shadow"}}` |

For full list and decision thresholds, see `backend/.env.example` and `backend/tayyari/decisions/defaults.toml`.

### Cost guard (nested: `TAYYARI_GUARD__*`)

| Variable | Default | Purpose |
|---|---|---|
| `TAYYARI_GUARD__DAILY_CEILING_USD` | `20.0` | Daily spend ceiling (UTC day); `0` switches AI off |
| `TAYYARI_GUARD__EST_ANALYZE_USD` | `0.40` | Estimated cost per analysis (pre-flight check) |
| `TAYYARI_GUARD__EST_CHAT_USD` | `0.10` | Estimated cost per chat turn |

Per-IP rate limits (per `TAYYARI_GUARD__*_PER_{MINUTE,HOUR,DAY}`) are in the example file.

See **[backend/DEPLOY.md](backend/DEPLOY.md)** for cloud-specific variables and deployment instructions.

## Checks and tests

From the repository root:

| Script | Proves |
|---|---|
| `npm run test:py` | All Python tests pass (unit, integration, golden, fakes) |
| `npm run lint:py` | Code meets Ruff style and is auto-formatted |
| `npm run typecheck:py` | mypy strict type checking on new code passes |
| `npm run check:match` | The posting-match screens show every requirement row, citation and cover paragraph of the stored match, for both persona fixtures (also run by `npm run check`) |

Individual test areas:

```bash
cd backend
uv run pytest tests/unit -q        # unit tests only
uv run pytest tests/integration -q # integration tests with fakes
```

Frontend checks (unchanged):

```bash
npm run check:palette   # WCAG contrast at all hues
npm run check:restraint # no shadows, filters, infinite animations
npm run check:build     # bundle still has @property --h, tokens, SPA
npm run check:match     # posting-match screens against the stored match fixtures
npm run check           # typecheck and every check above, plus portfolio parity
```

## Atmosphere

The whole visual system — the rules, every number, the two exceptions, the checks, and how to
carry it into another application — is in **[design.md](design.md)**; the reasoning and sources
are in `docs/design-notes.md`.

The background is a soft colour field that drifts through the full hue circle once every
**42 minutes** (1° every 7 s). One hue number (`--h` on `<html>`) feeds every colour token in
`src/theme/tokens.css`; lightness and chroma are fixed per role, so contrast never changes while
the colour travels. The hue is a function of wall-clock time plus a random per-browser offset
(`localStorage` `tayyari.hue0`), so reloads never jump and nothing visibly "starts".

- **Atmosphere control** (header): **Drift** (the 42-minute cycle), **Still** (one calm colour,
  frozen where the clock was at load) or **Off** (neutral greys). The choice persists in
  `localStorage` `tayyari.atmo`.
- **Reduced motion** (`prefers-reduced-motion: reduce`) or a slow screen (`update: slow`) forces
  Still; the control then reads "Still · system".
- **`?h=<degrees>`** pins the base hue for previews and screenshots (`/?h=120`) and forces Still.
- **Fallbacks:** engines without `oklch()` get a fixed hex palette for hue 250 (regenerate it
  with `npm run emit:fallback` after changing a token); `prefers-contrast: more` removes the
  lobes and darkens the ink; `forced-colors: active` hides the field and uses system colours.
- **Cursor light (Home only):** a soft field of colour follows the mouse behind the page — one
  WebGL2 shader in `src/light/`, hue analogous to the field, lightness never below .89 so text
  keeps its contrast, asleep (zero frames) after 45 s of stillness. Not mounted on touch devices,
  under reduced motion or slow updates, under forced colours or more contrast, or with the
  Atmosphere Off. In dev, `?light=stats` shows frame, GPU and follow measurements and exposes
  `__light.sweep(seconds, pxPerSecond)`; `?light=reduce` and `?light=off` test the fallbacks.
  Rationale and numbers: `docs/design-notes.md` §7.

## Notes and limits (POC)

- LinkedIn usually blocks automated reading; paste your About section into **Extra context** instead.
- Lovable *editor* links need a login; share the published `*.lovable.app` URL.
- Profiles and chat context are stored in the database (Postgres on FastAPI Cloud, SQLite locally, encrypted on disk). Analysis, chat and portfolios stay open to anonymous visitors and are rate-limited by IP. Keeping a graph with an account and purchases need a sign-in. The posting match needs one wherever a sign-in provider is configured; where none is, a visitor can match anonymously (see Posting match above). The profile on screen is claimed by the account automatically after a sign-in.
