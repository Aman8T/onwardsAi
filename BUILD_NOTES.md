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
