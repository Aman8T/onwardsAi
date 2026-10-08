# Tayyari — career intelligence (POC)

Connect a resume, GitHub, Lovable apps and any public link. Claude reads every source, builds an
interactive **skill graph of your current role**, flips it to a **map of the roles you are closest to
next**, and turns every node into a conversation: *"Help me prepare for this topic"*, *"Help me improve
my GitHub"*, *"How do I prepare for this role?"*

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
npm run check           # full typecheck + three checks above
```

## Atmosphere

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

## Notes and limits (POC)

- LinkedIn usually blocks automated reading; paste your About section into **Extra context** instead.
- Lovable *editor* links need a login; share the published `*.lovable.app` URL.
- Profiles and chat context are stored in the database (Postgres on FastAPI Cloud, SQLite locally, encrypted on disk). There is no frontend auth — run it locally or set rate limits in the cloud.
