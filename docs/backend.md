# Backend architecture and deployment

This document describes the Tayyari Python backend: a modular FastAPI application serving the React frontend. The backend is the single source of truth for AI-driven career intelligence, decision logic, persistence, and cost protection.

## Module overview

The package is importable as `backend/tayyari/`, laid out in layers with import restrictions enforced by test. Each module has one purpose.

| Module | Exports | Purpose |
|--------|---------|---------|
| `core` | Settings, Logger, errors | Configuration (env + `.env` cascade), structlog + PII redaction, custom error types, limits, UUIDs, clock, text utils, DB URL normalisation |
| `contract` | Pydantic models | Types mirroring `shared/types.ts`: AnalysisEvent, ChatEvent, Profile, skill/domain/role objects |
| `llm` | ClaudeClient, JevClient | Anthropic API client (pricing, caching, errors, usage), Jev HTTP adapter, model capability table |
| `decisions` | DecisionRunner, DecisionService | Jev decision specs, packs, runner, recorder (with mode: off/shadow/primary); defaults in version-controlled TOML |
| `analysis` | AnalysisService | Reader execution, synthesis, normalisation against golden baselines, Sonnet calls |
| `chat` | ChatService | Chat session store, tool mapping, WebSearch/WebFetch, streaming |
| `guard` | RateLimiter, SpendGuard | Per-IP fixed-window counters, daily spend ceiling, client IP hashing |
| `storage` | Repos (Profiles, Sessions, LlmCalls, Decisions, Limits) | SQLAlchemy 2 async, Alembic migrations, Postgres (Neon) + SQLite |
| `prompts` | build_reader_system, build_synth_system, build_chat_system | Prompt templates (limits interpolated from `core.limits`) |
| `ingest` | fetch and extract functions | PDF/DOCX parsing, GitHub API, HTML scraping, link classification, SSRF guard |
| `api` | create_app, routes | FastAPI app factory, request dependencies, NDJSON streaming, file uploads, error handling |
| `cli` | commands | Entry point: `dev`, `serve`, `migrate`, `smoke`, `make-sample`, `ingest-check`, `deploy`, `export`, `replay`, `eval` |
| `sample` | sample_profile, SampleLoader | Bundled Riya Kapoor persona; fixtures for testing and offline mode |

**Layering rule** (enforced by test): `core` < `contract` < `llm` < `decisions` < `analysis`/`chat` < `api`. `storage` depends only on `core` and `contract`. `guard` depends on `storage.ports`. Services depend on Protocols, so every unit can be tested with fakes.

## Request flows

### Analysis (`POST /api/analyze`)

1. **Ingest** (deterministic): PDF → text, GitHub repos → README + tests, links → HTML, all with SSRF guard, rate limiting, and private-IP block.
2. **Jev first read** (parallel with readers): resume → career_family, stage, depth, nouls (leads, proof, outcomes, AI, progression). ~2.5 s, live signals on screen (if confidence ≥ 0.6).
3. **Haiku readers** (6 concurrent): each source → reader output (skills, domains, role hints, facts) in structured JSON. ~5–20 s per source depending on size and cache hits.
4. **Synthesis** (Sonnet): all reader outputs + resume → skill graph (person, mastery, 4–7 domains, skills with evidence, next roles, have/gap requirements). With prompt caching (profile block shared across threads). ~10–30 s.
5. **Evidence checks** (Jev + code, post-synth): each skill's claims → anchor-token check (names, numbers, identifiers in raw text and reader output) then Jev support/contradicts/says_nothing. Claims dropped only when both signals agree. ~4 s total, fail open.
6. **Storage**: Profile saved to DB (Postgres on cloud, SQLite locally), with context (sources + readers).
7. **Spend tracking**: every LLM call and Jev decision recorded with cost, latency, and request ID. Daily total cached.

**Cost per analysis**: ~$0.30–$0.60 (readers $0.10–$0.40, synth $0.15–$0.20, evidence $0.01–$0.10 with Jev, cache reads save ~30–50%).

### Chat (`POST /api/chat`)

1. **Session resolve**: load profile and context. Unknown `sessionId` → new session + reset note.
2. **Jev web-search gate**: message + origin → needs_outside_info (noul). Tools withheld only if `p ≤ 0.15` and confidence ≥ 0.7.
3. **Cached system blocks**: tools, static coach rules, profile block (shared, cache breakpoint 1), thread origin, history (automatic tail breakpoint).
4. **Stream through Sonnet**: WebSearch and WebFetch tools available (or withheld by gate). Map tool names `web_search` → `WebSearch`.
5. **Persist**: append user message, assistant message, tools used, and `llm_call_id` in one transaction. Optimistic on `(session_id, seq)` with retry.
6. **Emit**: `done{sessionId}` at end. Truncation → append note. Refusal → error event with friendly message.

**Cost per turn**: ~$0.02–$0.10 (first turn ~$0.05 with cache write, later turns ~$0.002–$0.005 with cache read).

## Jev decisions framework

### Decision specifications

Each spec is a **DecisionSpec**: question (template with `bind()` for dynamic parts), `interpret()` function (argmax/thresholding in code), neutral value, and a default config.

Specs are grouped into **packs** sharing a state (batched into one Jev call to save cost). Example: all first-read questions use state `{resume, notes}` and run in one call.

### Execution modes (from `decisions/defaults.toml`)

| Mode | Behavior | Use case |
|------|----------|----------|
| `off` | Never ask Jev. Return neutral. | Disabled specs. |
| `shadow` | Call Jev, log next to incumbent (e.g., Sonnet's own output), never delay stream > 1.5 s. Return incumbent (never escalate). | Skill levels, role fit — gather data, no impact on live output. |
| `primary` | Ask Jev first. If confidence ≥ threshold, use it. Otherwise, run Claude Haiku fallback (same rubric) and return with `escalated=true`. Fail open to fallback on errors, circuit breaker, or spend trip. | Live decisions: first read (with fallback=skip for 1 s latency), triage, evidence checks, chat gate. |

### Fallback behavior

- `fallback="claude"`: Haiku structured call with the same schema (enum labels for choice, boolean + confidence for noul, integer for score). Escalation reason: `low_confidence`, `jev_error`, `circuit_open`, `jev_disabled`, or `spend`.
- `fallback="skip"`: Return neutral, e.g., first-read with no fallback because a Haiku call cannot meet 1 s latency.

### Recorder and logging

**DecisionRecorder** buffers and flushes every 1 s or 50 rows:
- `jev_calls`: request, response, latency, cost.
- `decision_states`: SHA256-deduplicated, zlib-compressed state (resume text) to save space.
- `decision_log`: decision id, source (jev/claude/default), confidence, threshold, escalation, incumbent (for shadow decisions).

Each row carries the pinned Jev model and spec version, so replays never mix versions.

**Audit sampling**: `audit_rate: 0.05` means 5% of used Jev answers also get a background Claude answer (tracked task). Agreement statistics feed the `cli eval` harness.

### Adopting a new Jev version

1. Bump `TAYYARI_JEV__MODEL=jev-1.14.0` in `.env` or cloud secrets.
2. Export the corpus: `cli export > corpus.jsonl` (join decision_log with state).
3. Replay against the new Jev: `cli replay --model jev-1.14.0 < corpus.jsonl > report.json`.
4. Review agreement by confidence bin: `cli eval report report.json`.
5. Compute new thresholds: `cli eval promote --dry-run < corpus.jsonl` (per spec, the lowest threshold meeting agreement target).
6. Commit the `defaults.toml` update.

Because the log records the model pin, old results never mix with new ones, and you can safely A/B test or roll back.

### Adding a new decision

1. Define a **DecisionSpec** with id, version, pack, question template, `interpret()`, neutral value, and default config.
2. Add it to the pack's `specs` tuple.
3. In `decisions/defaults.toml`, add a `[spec.id]` section with mode, threshold, fallback, etc.
4. Update **DecisionService** (or its implementation) to expose the decision to the caller (e.g., `AnalysisService` or `ChatService`).
5. Wire the service to the decision runner in `container.py`.
6. Write a unit test that mocks Jev and Claude.
7. Add a row to `cli eval` to track the new spec's agreement.

## Storage

### Tables

All timestamps are `timestamptz` (UTC). JSON columns use `JSON().with_variant(JSONB, "postgresql")` for Postgres efficiency.

| Table | Key | Columns | Indexes / notes |
|-------|-----|---------|-----------------|
| `profiles` | `id` PK | `created_at`, `last_accessed_at`, `owner_hash` (HMAC, nullable), `is_sample`, `schema_version`, `goal`, `profile` JSON | `(last_accessed_at)` for retention; `(owner_hash, created_at)` for owner queries |
| `profile_contexts` | `profile_id` PK/FK | `context` JSON (sources + reader outputs), `bytes` | split from profiles so `/api/profile` never reads source text |
| `chat_sessions` | `id` PK | `profile_id` FK cascade, `origin` JSON, `created_at`, `updated_at`, `turn_count`, `ip_hash` | `(profile_id)`, `(updated_at)` for old-session cleanup |
| `chat_messages` | PK `(session_id, seq)` | `role`, `content` text, `tools` JSON null, `llm_call_id` FK null, `created_at` | optimistic append guard |
| `llm_calls` | `id` UUID PK | `created_at`, `purpose` (reader/synth/chat/decision_fallback), `model`, `request_id`, `profile_id` FK, `session_id` FK, `analysis_id` FK, `ip_hash`, `attempt`, `status`, `error_kind`, `stop_reason`, `input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens`, `web_searches`, `cost_micro_usd` BIGINT, `latency_ms`, `ttft_ms` | `(created_at)`, `(profile_id)`, `(purpose, created_at)` for queries |
| `jev_calls` | `id` UUID PK | `created_at`, `pack`, `model_requested`, `model_served`, `state_hash` FK, `questions` JSON, `response` JSON null, `status`, `error_kind`, `request_id`, `input_tokens`, `output_tokens`, `cost_micro_usd`, `latency_ms`, `profile_id` FK, `analysis_id` FK, `is_sample`, `payload_purged_at` | `(pack, created_at)`, `(state_hash)` for dedup query |
| `decision_states` | `hash` CHAR(64) PK (SHA256) | `payload` BYTEA (zlib JSON), `raw_bytes` text, `created_at`, `last_ref_at`, `is_sample` | deduplicates resume text across 10–40 calls per analysis |
| `decision_log` | `id` UUID PK | `created_at`, `call_id` FK null, `spec_id`, `spec_version`, `pack`, `subject_ref` (claim or skill id), `profile_id` FK, `analysis_id` FK, `mode`, `jev_answer` JSON, `jev_confidence`, `threshold`, `claude_call_id` FK null, `claude_answer` JSON, `incumbent` JSON, `used`, `source_used` (jev/claude/default), `escalated`, `escalation_reason`, `latency_ms`, `cost_micro_usd`, `is_sample` | `(spec_id, created_at)`, `(profile_id)`, `(call_id)` |
| `rate_limit_hits` | PK `(bucket, window_start)` | `count` | `(window_start)` for cleanup; purged after 2 days |
| `spend_daily` | PK `(day, scope)` (scope: total/anthropic/jev) | `micro_usd`, `calls` | `cli spend --reconcile` sums calls per day to flag drift |
| `kv` | `key` PK | `value` JSON, `updated_at` | maintenance bookkeeping (last purge, IP hash key) |

### Retention (run at most every 6 hours, from lifespan under `pg_advisory_xact_lock`)

- **Profiles**: deleted when `last_accessed_at` is older than 60 days (sample rows kept forever).
- **Jev payloads** (states + raw JSON): purged after 30 days. Metadata (`decision_log` rows) kept for 180 days.
- **Rate limit hits**: deleted after 2 days.
- **Sample rows** (`is_sample=true`): kept permanently as an eval corpus.

### Migrations (Alembic)

Migrations live in `storage/migrations/` inside the package. No external `alembic.ini`.

**Fast path** (cold starts): read `alembic_version` and compare to head. If equal, do nothing.

**Upgrade path**:
1. Start a transaction.
2. On Postgres: `SET LOCAL lock_timeout='10s'`, then `SELECT pg_advisory_xact_lock(<key>)`. This works behind Neon's pgbouncer (transaction-level lock).
3. Run `upgrade head`.
4. Commit (lock released).

If migration fails, the transaction rolls back and startup crashes. Readiness checks fail, so the load balancer keeps the old version running.

**Additive-only rule**: migrations add tables or nullable columns only. No drops, renames, or constraint tightening after revision 0001 (unless a file carries a `# contract-phase` marker).

## Cost guard

### Rate limits (per-IP fixed-window counters)

Each IP has atomic counters (one `INSERT … ON CONFLICT … RETURNING count` per window):

| Endpoint | Window | Limit | Global |
|----------|--------|-------|--------|
| `/api/analyze` | 1 min | 2 | — |
| | 1 hour | 6 | 60 per hour (cross-IP) |
| | 1 day | 15 | — |
| `/api/chat` | 1 min | 20 | — |
| | 1 hour | 120 | — |
| | 1 day | 400 | — |

**In-memory bucket** (per replica): rejects hammering before it reaches the DB. If the DB errors, fail open to the in-memory bucket and log.

**Responses**: 429 JSON with `Retry-After` header.

### Spend ceiling (soft)

Daily ledger: `spend_daily` row per UTC day (scope: total/anthropic/jev). Incremented atomically with every `llm_calls` and `jev_calls` write. Read with 5 s cache.

Default ceiling: **$20/day UTC**. Reaching it returns 503 JSON before the stream starts. Mid-analysis, the run may finish up to 125% of the ceiling (in-flight grace), then emit an error event. Spend guard fails closed if DB is down (because profiles can't be saved anyway).

Also set a **spend limit on the Anthropic workspace** as a hard backstop outside our code.

### Client IP identification

`X-Forwarded-For` header (configurable via `TAYYARI_GUARD__TRUSTED_PROXY_HOPS`, default 1 hop from the right). HMAC-SHA256 of the IP (salt generated at first startup, stored in `kv` table). Never store the raw IP.

## Testing layout

Tests use **pytest** with **respx** (mocking HTTP) and **asgi-lifespan** (FastAPI lifespan fixture).

| Directory | Purpose | Mocks |
|-----------|---------|-------|
| `tests/unit` | Fast, isolated | FakeClaudeClient, FakeJevClient, FakeRepos, in-memory limits |
| `tests/integration` | Contract and integration | Same fakes, but real composition and flows |
| `tests/golden` | Regression on Sonnet + readers | Captured NDJSON baselines; compared after a rebuild |
| `tests/fakes` | Fake implementations | dict-backed repos, scripted Claude/Jev clients |
| `tests/data` | Fixtures | sample resumes, links, reader outputs |

**Sample corpus**: `tayyari/sample/` (Riya Kapoor persona) is the source of truth. `cli make-sample` regenerates it with a live run. Golden baseline captures it and compares on any rebuild.

**Jev contract test**: mock `jev_http` and test both `HttpxJev` and `TypeSafeSdkJev` (if the SDK is pinned) against the same scripted server, verifying normalisation (noul confidence, choice normalization by option count, score mapping).

## Verification checklist

Run these before shipping Wave 4:

**Local:**
1. `npm run test:py` — contract, decision matrix, SSRF, chunk upload 413s, limiter concurrency, migrations, golden NDJSON, cancellation.
2. `npm run lint:py` — Ruff style.
3. `npm run typecheck:py` — mypy strict on new modules.
4. `npm run smoke` — Haiku call, Sonnet chat, cache read, PDF parse, no 400 retry on effort.
5. Manual: upload sample, check Jev first-read lines appear in ~2 s, graph renders, evidence log appears.
6. Manual: chat, check tool chips read "Searched the web". Restart API, continue thread; history survives.
7. Manual: check `llm_calls`, `jev_calls`, `decision_log` have rows. `spend_daily` reconciles.
8. Manual: trip guard (3 analyses under 2/hour → 429, tiny ceiling → 503).
9. `cli ingest-check` and `cli make-sample` succeed.
10. `npm run check` (full frontend + backend typecheck).

**Cloud (FastAPI Cloud + Neon):**
1. Deploy from `backend/`. Check `/api/health` (storage: postgres), `/api/sample-profile`, live browser analysis (curl -N).
2. Logs: migrations ran once across replicas.
3. Scale to zero, then chat. History intact.
4. Trip per-IP limit via proxy. Hashed IP matches real public IP.
5. Cold start latency on Neon.
6. Optional: `cli replay` on exported corpus.

## Configuration reference

See **[backend/.env.example](backend/.env.example)** for the complete list and defaults. Key variables:

- `ANTHROPIC_API_KEY` (required for analysis/chat)
- `JEV_API_KEY` (optional; enables Jev decisions)
- `DATABASE_URL` (Neon on cloud; SQLite locally if unset)
- `TAYYARI_*` (models, efforts, limits, Jev, guard settings)

For deployment to FastAPI Cloud, also see **[backend/DEPLOY.md](backend/DEPLOY.md)** (linking here per task rules).
