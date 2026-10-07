# Deploying Tayyari to FastAPI Cloud

`backend/` is a modular FastAPI application with a Python-first architecture: LLM clients, Jev decisions, analysis and chat services, persistent storage, and guard policies for rate limiting and cost protection. FastAPI Cloud uploads the entire backend folder (along with the built React frontend in `static/`), so everything the app needs at runtime is self-contained.

Everything below is verified against the FastAPI Cloud docs and the latest CLI versions (`fastapi-cli`, `fastapi-cloud-cli`, October 2026).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (`uv`, `uvx`) for Python dependency management
- Node.js 21+ for the frontend build
- A FastAPI Cloud account, logged in once on this machine
- Anthropic API key (`ANTHROPIC_API_KEY`) for Claude models
- TypeSafe API key (`JEV_API_KEY` or `TYPESAFE_API_KEY`) for the Jev decision adapter (optional for shadow mode)
- Windows only: set `PYTHONUTF8=1` for every `uvx` or `uv run` command that touches Python. On a cp1252 console, Python print statements (especially emoji in CLI output) will crash with `UnicodeEncodeError`.

  ```powershell
  $env:PYTHONUTF8 = "1"        # PowerShell
  ```
  ```bash
  export PYTHONUTF8=1          # Git Bash
  ```

## Local development setup

### 1. Install Python dependencies

From the repository root:

```bash
cd backend
uv sync
```

This creates a Python 3.13 virtual environment at `backend/.venv` and installs all dependencies from `uv.lock`. On first run, uv downloads the Python interpreter if needed.

### 2. Set environment variables

Create a `backend/.env` file (git-ignored) with your API keys:

```bash
ANTHROPIC_API_KEY=sk-ant-...
JEV_API_KEY=key-...       # or TYPESAFE_API_KEY=... (optional)
```

Do not commit `.env`. The frontend `.env` is separate and lives in the repo root if needed.

### 3. Run the development server

```bash
npm run dev
```

This runs `npm:dev:api` (the Python backend at `http://localhost:8787`) and `npm:dev:web` (the Vite frontend at `http://localhost:5173`) in parallel. Open `http://localhost:5173` in a browser.

Alternatively, run just the API:

```bash
npm run dev:api
```

or from `backend/`:

```bash
uv run python -m tayyari.cli dev
```

The dev server watches for file changes and reloads automatically (via `--reload`).

## Debugging in VS Code

The project includes `.vscode/launch.json` with these configurations:

- **"API (uvicorn, no reload)"** — Single-threaded debugger, good for stepping through request handlers. Runs on port 8787.
- **"API (reload)"** — Watchdog reload with subprocess debugging. Use this for iterative development. Note: breakpoints in reloaded files may not trigger until the next reload.
- **"Pytest: current file"** — Debug the currently open test file with pytest.
- **"CLI: smoke"** / **"CLI: make-sample"** — Debug CLI commands.
- **"API + web"** — Compound: starts the Python API and the npm dev:web script together.

Open the Debug pane (Ctrl+Shift+D), pick a configuration, and press F5 to start.

## Testing, linting and type checking

From `backend/`:

```bash
# Run the test suite
uv run pytest -q

# Or just one area
uv run pytest tests/unit/analysis -q

# Check for lint violations (Ruff)
uv run ruff check .

# Auto-format code
uv run ruff format .

# Type checking (strict for new code)
uv run mypy tayyari
```

Or from the repo root:

```bash
npm run test:py       # pytest
npm run lint:py       # ruff check + format check
npm run typecheck:py  # mypy
```

## Building and deploying

### 1. Build the frontend and copy static files

From the repository root:

```bash
npm run build:static
```

This runs `vite build` and then `backend/build-static.mjs`, which copies the frontend build (`dist/`) into `backend/static/`. The static files are uploaded with the app.

### 2. Lock Python dependencies (once, or when `pyproject.toml` changes)

From `backend/`:

```bash
uv lock
```

FastAPI Cloud installs from `uv.lock`, which keeps deployments reproducible.

### 3. Log in to FastAPI Cloud (once per machine)

```bash
uvx --from "fastapi[standard]" fastapi cloud whoami
uvx --from "fastapi[standard]" fastapi login
```

Credentials are stored in the per-user CLI config folder, so ephemeral `uvx` environments share them.

### 4. Deploy

From the repository root, after building the frontend:

```bash
npm run deploy
```

This runs `npm run build:static`, then `uv run --directory backend python -m tayyari.cli deploy --skip-build` from the FastAPI Cloud CLI.

Alternatively, deploy manually from `backend/`:

```bash
uvx --from "fastapi[standard]" fastapi deploy
```

On the first deploy, the CLI is interactive (pick a team, name the app, confirm). It writes `.fastapicloud/cloud.json`, which links the folder to the app. Later deploys use this file. The CLI archives `backend/`, uploads it, and waits while FastAPI Cloud builds the image and rolls it out.

**Windows note:** Set `$env:PYTHONUTF8 = "1"` before running the deploy command to avoid emoji-related crashes.

## Environment variables and secrets in the cloud

Variables can be set in the FastAPI Cloud dashboard or with the CLI. A `.env` file is never uploaded: the CLI hard-excludes `.env` and `.env.*`.

### Required

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude models (reader, synthesis, chat) |

### Optional

| Variable | Default | Purpose |
| --- | --- | --- |
| `JEV_API_KEY` | (not set) | TypeSafe Jev API key for decision models. If not set, Jev is disabled. |
| `GITHUB_TOKEN` | (not set) | GitHub personal access token to raise API rate limits for GitHub ingest. |

### Set secrets from the CLI

```bash
# Secret value from stdin (keeps it out of shell history)
printf '%s' "$ANTHROPIC_API_KEY" | uvx --from "fastapi[standard]" fastapi cloud env set \
  --secret --value-stdin --no-redeploy ANTHROPIC_API_KEY

# Non-secret variable
uvx --from "fastapi[standard]" fastapi cloud env set GITHUB_TOKEN my-token

# List all variables
uvx --from "fastapi[standard]" fastapi cloud env list
```

On Windows with PowerShell:

```powershell
$env:ANTHROPIC_API_KEY | uvx --from "fastapi[standard]" fastapi cloud env set --secret --value-stdin --no-redeploy ANTHROPIC_API_KEY
```

**Notes:**
- Secrets are encrypted and cannot be read back.
- A changed variable takes effect on the next deployment. Use `--no-redeploy` to set it without immediately deploying.
- FastAPI Cloud also injects `FASTAPICLOUD_DEPLOYMENT_ID` and `FASTAPI_PUBLIC_URL`.

## Connecting Neon for persistent storage

By default, the app uses an ephemeral SQLite database. For production, connect a Neon Postgres database through the FastAPI Cloud dashboard:

1. Open your app in the FastAPI Cloud dashboard.
2. Go to **Integrations** and link a Neon Postgres project.
3. FastAPI Cloud injects `DATABASE_URL` automatically.

The first deploy with Neon will run migrations in the lifespan (see below).

If you need to manage Neon separately, copy the `DATABASE_URL` from the Neon console and set it in the FastAPI Cloud dashboard or CLI:

```bash
uvx --from "fastapi[standard]" fastapi cloud env set --secret --value-stdin --no-redeploy DATABASE_URL
```

The app normalizes the Neon URL (strips `sslmode`, `channel_binding`, adds asyncpg SSL) and handles connection retry on cold starts.

## Database migrations

Migrations run automatically in the app's lifespan (on startup, before the first request):

1. Check if the schema is at the head. If yes, exit immediately (one cheap query per cold start).
2. Otherwise, acquire an advisory lock (`pg_advisory_xact_lock` on Postgres, automatic on SQLite).
3. Run Alembic `upgrade head` in a transaction.
4. Release the lock on commit.

This design supports zero-downtime rollouts: old and new versions overlap, and the migration is transactional.

If a migration fails, the startup crashes and readiness checks fail, so the load balancer keeps the old version running. You can manually roll back by downgrading Alembic and redeploying.

For manual migration management (e.g., testing a migration locally before deploy):

```bash
cd backend
uv run python -m tayyari.cli migrate
```

## Tayyari-specific settings

The backend reads configuration from environment variables with the `TAYYARI_` prefix. All settings are optional unless noted.

### Models

| Variable | Default | Options |
| --- | --- | --- |
| `TAYYARI_READER_MODEL` | `claude-haiku-4-5` | Any Anthropic model supporting structured output |
| `TAYYARI_SYNTH_MODEL` | `claude-sonnet-5-5` | Any Anthropic model |
| `TAYYARI_CHAT_MODEL` | `claude-sonnet-5-5` | Any Anthropic model |

### Jev and decisions

| Variable | Default | Purpose |
| --- | --- | --- |
| `TAYYARI_JEV__ENABLED` | `true` | Set `false` to disable Jev entirely (decisions will use Claude fallbacks) |
| `TAYYARI_JEV__TRANSPORT` | `http` | Use `http` for raw httpx requests, or `sdk` for the TypeSafe SDK (requires `typesafe-sdk` pip dependency) |
| `TAYYARI_JEV__MODEL` | `jev-1.13.0` | Pinned Jev model version |
| `TAYYARI_DECISION_OVERRIDES` | `{}` | JSON dict of per-spec config overrides, e.g. `{"evidence.support":{"mode":"shadow"}}` |

### Guard: rate limits and spend ceiling

| Variable | Default | Purpose |
| --- | --- | --- |
| `TAYYARI_GUARD__DAILY_CEILING_USD` | `20` | Daily spend ceiling (UTC day). Set `0` to switch AI off. |
| `TAYYARI_GUARD__EST_ANALYZE_USD` | `0.40` | Estimated cost per analysis (used for pre-flight checks) |
| `TAYYARI_GUARD__EST_CHAT_USD` | `0.10` | Estimated cost per chat turn |
| `TAYYARI_GUARD__ANALYZE_RULES` | Hardcoded | Rate limit windows for analyze: `[{"window_s": 60, "limit": 2}, ...]` (2/min, 6/hour, 15/day, 60/hour global) |
| `TAYYARI_GUARD__CHAT_RULES` | Hardcoded | Rate limit windows for chat: `[...]` (20/min, 120/hour, 400/day) |

### Concurrency and cleanup

| Variable | Default | Purpose |
| --- | --- | --- |
| `TAYYARI_CONCURRENCY__READER_CONCURRENCY` | `6` | Semaphore size for parallel reader work |
| `TAYYARI_CONCURRENCY__READER_TIMEOUT_S` | `90` | Timeout per reader (seconds) |
| `TAYYARI_CONCURRENCY__MAX_CONCURRENT_ANALYSES` | `4` | Limit simultaneous analyses |
| `TAYYARI_RETENTION__PROFILE_DAYS` | `60` | Delete profiles idle for this many days |
| `TAYYARI_RETENTION__PAYLOAD_DAYS` | `30` | Purge Jev payloads after this many days |
| `TAYYARI_RETENTION__METADATA_DAYS` | `180` | Keep decision metadata for this many days |

## Verifying the deployment

Once deployed, the app is available at `https://<app-name>.fastapicloud.dev`:

```bash
BASE=https://<app-name>.fastapicloud.dev

# Health check (no database access, always fast)
curl "$BASE/api/health"

# Sample profile endpoint (also no database access)
curl "$BASE/api/sample-profile" | head -c 300

# Analysis stream (NDJSON events, progressive)
curl -N -X POST "$BASE/api/analyze" -d '{"goal":"...","links":[]}'

# Deep health check (tests database connectivity)
curl "$BASE/api/health/deep"
```

Then open `$BASE/` in a browser and test:
1. "Try a sample profile" — uses the pre-computed sample, no API keys needed.
2. Upload a resume and click "Analyze" — should stream Jev first-read logs within about 2 seconds, then reader and synth results.
3. Click a skill and start a chat — should load the profile and your message history.

If analysis or chat fails with "Set ANTHROPIC_API_KEY", the secret is missing. Set it without `--no-redeploy` (or redeploy after setting it).

### Scale-to-zero behavior

By default, FastAPI Cloud scales to zero. When the app is idle:
- Profiles and chat sessions are persisted in Neon.
- Static routes (`/api/health`, `/api/sample-profile`, `/`) are cached on Cloudflare and do not wake the app.
- A profile request or chat message wakes the app (cold start ~5 seconds on Neon).
- History survives: a chat thread continues from where it left off.

## Logs

Stream logs from the running app:

```bash
uvx --from "fastapi[standard]" fastapi cloud logs                      # live (Ctrl+C to stop)
uvx --from "fastapi[standard]" fastapi cloud logs --no-follow --tail 200 --since 1h
```

App logs are prefixed `[pipeline]`, `[analysis]`, `[chat]`, etc. In production, logs are structured JSON (via structlog) and can be searched in the FastAPI Cloud dashboard.

Check build logs for a deployment:

```bash
uvx --from "fastapi[standard]" fastapi cloud deployments list           # find deployment id
uvx --from "fastapi[standard]" fastapi cloud deployments build-logs <ID>
```

## What gets uploaded

FastAPI Cloud uploads the contents of `backend/`. Files excluded:

- `.venv`, `__pycache__`, `.mypy_cache`, `.pytest_cache`, `.git`, `.env*`, `*.pyc` (always)
- Any file in `.gitignore` or `.fastapicloudignore` (the latter wins; `!` un-ignores)

The repo's root `.gitignore` excludes `dist/`, `data/`, `node_modules/`, etc. The `backend/` folder has `.fastapicloudignore` which un-ignores `static/` (the frontend build) and `tayyari/sample/` (sample fixtures).

The entrypoint is `main:app`, set in `[tool.fastapi]` in `pyproject.toml`. FastAPI Cloud reads it and runs the app as:

```bash
python main.py
```

## Known limits and behaviors

Documented by FastAPI Cloud:

- **Scale to zero is on by default.** Idle apps stop; the next request pays a cold start (roughly 5 seconds on Neon, less on SQLite).
- **Replicas and auto-scaling.** Hobby plans allow 1–3 replicas. Instances start at 0.1 CPU / 500 MiB.
- **Zero-downtime rollouts.** New instances must pass readiness checks before old ones stop. A readiness failure keeps the old version running.

Not documented (treat as unknown, test locally first):

- **Request timeout and streaming.** The API streams NDJSON (`application/x-ndjson`) for `/api/analyze` and `/api/chat`. Events arrive roughly every 700 ms, so the connection is never idle for long. If all events arrive at once, check that responses send `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- **Filesystem.** The root filesystem is ephemeral. SQLite (if used) lives in a temp directory and resets on redeploy. Use Neon for durable storage.

Consequences for this app:

- **Profiles and chat sessions are durable** (in Neon) as long as the database is connected.
- **Neon cold start** can add 5 seconds to the first request after scale-to-zero. The app retries DB connections for ~45 seconds on startup.

## Troubleshooting

### "Archive too large" error during deploy

The FastAPI Cloud CLI has a server-side upload size limit. Check the archive size:

```bash
cd backend
du -sh .
```

Our app is typically 1–2 MB. If it is larger:
- Check for stray `.git` folders or large test fixtures.
- Verify `.fastapicloudignore` excludes `__pycache__`, `.venv`, etc.

### Deploy fails with "uvx: command not found"

Ensure `uv` is installed and in your PATH. On Windows, also set `PYTHONUTF8=1`:

```powershell
$env:PYTHONUTF8 = "1"
npm run deploy
```

### Database connectivity issues

If migrations fail or the app cannot connect to Neon:
- Check that `DATABASE_URL` is set in the FastAPI Cloud dashboard.
- Check that Neon's IP is allowed (should be automatic).
- For local testing, set `DATABASE_URL` in `backend/.env`:
  ```
  DATABASE_URL=postgresql://user:password@localhost/tayyari
  ```

### Request timeout or streaming issues

If analyses or chat are cut off with a 5xx error:
1. Check `uvx --from "fastapi[standard]" fastapi cloud logs` for the actual error.
2. Verify that response headers include `Cache-Control: no-cache` and `X-Accel-Buffering: no` (set in `tayyari.api.ndjson`).
3. Test locally with `npm run dev` and `curl -N http://localhost:8787/api/analyze`.

### "Set ANTHROPIC_API_KEY" error

The `ANTHROPIC_API_KEY` secret is missing or unset:
```bash
printf '%s' "$ANTHROPIC_API_KEY" | uvx --from "fastapi[standard]" fastapi cloud env set \
  --secret --value-stdin ANTHROPIC_API_KEY
```

Then redeploy or wait for the next deployment.

## uvx command quirks

FastAPI Cloud CLI commands are accessed through uvx because the `fastapi-cloud-cli` package installs no console script:

```bash
uvx --from "fastapi[standard]" fastapi <command>
```

The `--from "fastapi[standard]"` ensures both `fastapi-cli` and `fastapi-cloud-cli` are available in the ephemeral environment. Without it, `uvx fastapi-cloud-cli` runs nothing.

Confirmed working commands: `fastapi login`, `fastapi deploy`, `fastapi cloud whoami`, `fastapi cloud env`, `fastapi cloud logs`, `fastapi cloud apps`, `fastapi cloud teams`, `fastapi cloud deployments`, `fastapi cloud domains`, `fastapi cloud tokens`, `fastapi cloud link`, `fastapi cloud unlink`, `fastapi cloud logout`.
