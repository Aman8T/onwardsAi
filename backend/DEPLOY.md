# Deploying Tayyari to FastAPI Cloud

`cloud/` is a self-contained FastAPI app: the Python port of the Node API in `server/`, plus the
built React frontend in `cloud/static/`. FastAPI Cloud uploads this one folder, so everything the
app needs at runtime (frontend build, sample fixtures) is copied into it before deploying.

Everything below was checked against the FastAPI Cloud docs and the installed CLI
(`fastapi-cli` 0.0.32, `fastapi-cloud-cli` 0.26.0, October 2026). Commands shown with `uvx` need no
global install.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (`uv`, `uvx`) and Node.js 21+ (for the frontend build).
- A FastAPI Cloud account, logged in once on this machine (see step 3).
- An Anthropic API key. The Python API reads it from `ANTHROPIC_API_KEY`; it does not use the Claude
  Code OAuth session that the Node server uses.
- Windows only: set `PYTHONUTF8=1` for every `uvx ... fastapi` command. The CLI prints emoji, and on a
  cp1252 console it crashes with `UnicodeEncodeError: 'charmap' codec can't encode character`.

  ```powershell
  $env:PYTHONUTF8 = "1"        # PowerShell
  ```
  ```bash
  export PYTHONUTF8=1          # Git Bash
  ```

## The `fastapi` command through uvx

The package `fastapi-cloud-cli` ships no console script (its `entry_points.txt` is empty), so
`uvx fastapi-cloud-cli` runs nothing. The `fastapi` executable comes from `fastapi-cli`, which
`fastapi[standard]` installs together with `fastapi-cloud-cli`. Use this form for every command:

```bash
uvx --from "fastapi[standard]" fastapi <command>
```

Confirmed commands: `fastapi login`, `fastapi deploy`, and `fastapi cloud ...` (`whoami`, `env`,
`logs`, `apps`, `teams`, `deployments`, `tokens`, `domains`, `link`, `unlink`, `logout`).
`fastapi login` is an alias of `fastapi cloud login`, and `fastapi deploy` of `fastapi cloud deploy`.

## Deploy

Run steps 1 and 2 from the repository root, the rest from `cloud/`.

1. **Build the frontend and copy the static files.** Repeat before every deploy that changes the
   frontend or the sample fixtures.

   ```bash
   npm run cloud:build
   ```

   This runs `vite build`, then `cloud/build-static.mjs`, which replaces `cloud/static/` with `dist/`
   and copies the sample fixtures (`*.md`, `*.json` from `server/sample/`) into `cloud/tayyari/sample/`.

2. **Lock the Python dependencies** (once, and whenever `pyproject.toml` changes):

   ```bash
   cd cloud
   uv lock
   ```

   FastAPI Cloud installs from `uv.lock` when it is present, which keeps builds reproducible.

3. **Log in** (once per machine; skip if `whoami` already prints your account):

   ```bash
   uvx --from "fastapi[standard]" fastapi cloud whoami
   uvx --from "fastapi[standard]" fastapi login
   ```

   Credentials live in the per-user CLI config folder (`%APPDATA%\fastapi-cli\auth.json` on Windows),
   so ephemeral `uvx` environments share them.

4. **Create or link the app and set secrets before the first deploy** (see the next section), then:

   ```bash
   cd cloud
   uvx --from "fastapi[standard]" fastapi deploy
   ```

   The CLI archives `cloud/`, uploads it, and waits while FastAPI Cloud builds the image, rolls it out
   and checks readiness. It prints the app URL, `https://<app-name>.fastapicloud.dev`.
   `fastapi deploy --no-wait` returns right after the upload.

The first `fastapi deploy` in a folder is interactive (pick a team, name the app, confirm) and writes
`cloud/.fastapicloud/cloud.json`, which links the folder to the app; later deploys reuse it. Keep that
folder, or pass `--app-id` / set `FASTAPI_CLOUD_APP_ID`, to redeploy to the same app.

### First deploy without prompts (agents and scripts)

```bash
F='uvx --from fastapi[standard] fastapi'
$F cloud teams list                                           # note the team id
cd cloud
$F cloud apps create --name tayyari --team-id <TEAM_ID> --link   # creates the app and writes .fastapicloud/
```

Then set the variables below and run `fastapi deploy`.

## Environment variables and secrets

Variables can be set in the dashboard (App Details, Environment Variables, optional Secret switch) or
with the CLI from `cloud/`. A `.env` file is never uploaded: the CLI hard-excludes `.env` and `.env.*`.

| Variable | Needed | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key for the readers, the synthesis and the chat. |
| `GITHUB_TOKEN` | no | Raises the GitHub API rate limit for the GitHub ingest. |
| `TAYYARI_READER_MODEL` | no | Reader model, default `claude-haiku-4-5`. |
| `TAYYARI_SYNTH_MODEL` | no | Synthesis model, default `claude-sonnet-5-5`. |
| `TAYYARI_CHAT_MODEL` | no | Chat model, default `claude-sonnet-5-5`. |
| `TAYYARI_DATA_DIR` | no | Where profiles are cached on disk; defaults to the OS temp dir. |

```bash
# Secret value from stdin (keeps it out of shell history and the process list).
# --no-redeploy matters: in CLI 0.26 `env set` redeploys by default, which fails before the first deploy.
printf '%s' "$ANTHROPIC_API_KEY" | uvx --from "fastapi[standard]" fastapi cloud env set \
  --secret --value-stdin --no-redeploy ANTHROPIC_API_KEY

uvx --from "fastapi[standard]" fastapi cloud env set --no-redeploy TAYYARI_SYNTH_MODEL claude-sonnet-5-5
uvx --from "fastapi[standard]" fastapi cloud env list
```

```powershell
$env:ANTHROPIC_API_KEY | uvx --from "fastapi[standard]" fastapi cloud env set --secret --value-stdin --no-redeploy ANTHROPIC_API_KEY
```

- Secrets are encrypted on save and cannot be read back, in the dashboard or the CLI. To change one, set it again.
  A variable cannot be converted between secret and plain after creation.
- A changed variable only takes effect on a new deployment. Without `--no-redeploy`, `env set` starts one for you.
- FastAPI Cloud also injects `FASTAPICLOUD_DEPLOYMENT_ID` and `FASTAPI_PUBLIC_URL`.

## Verify

Replace the host with the URL printed by the deploy.

```bash
BASE=https://<app-name>.fastapicloud.dev
curl "$BASE/api/health"                      # {"ok":true,"models":{...},"hasSample":true}
curl "$BASE/api/sample-profile" | head -c 300  # precomputed profile, needs no API key
curl -N -X POST -F sample=1 "$BASE/api/analyze"   # NDJSON events should arrive one line at a time
```

Then open `$BASE/` in a browser, click "Try a sample profile", and send a chat message. If an analysis or chat
fails with "Set ANTHROPIC_API_KEY", the secret is missing: set it (without `--no-redeploy`, or redeploy afterwards).

## Logs

```bash
uvx --from "fastapi[standard]" fastapi cloud logs                       # stream live (Ctrl+C to stop)
uvx --from "fastapi[standard]" fastapi cloud logs --no-follow --tail 200 --since 1h
uvx --from "fastapi[standard]" fastapi cloud deployments list           # find a deployment id
uvx --from "fastapi[standard]" fastapi cloud deployments build-logs <ID>   # build failures
```

`--tail` is capped at 1000 lines and `--since` is limited by the plan's log retention. The same logs are
in the dashboard (Apps, your app, Logs). The app logs with the `[pipeline]` and `[chat]` prefixes.

## What gets uploaded

- The upload is the contents of `cloud/`. The CLI always skips `.venv`, `__pycache__`, `.mypy_cache`,
  `.pytest_cache`, `.git`, `.env*` and `*.pyc`, and honours `.gitignore` and `.fastapicloudignore`
  (the latter wins; `!static/` style lines un-ignore). The repo's root `.gitignore` ignores `dist/`,
  `data/`, `.env*`, `*.log` and `node_modules/`; none of these match `cloud/static/` or
  `cloud/tayyari/sample/`, so no ignore file is needed. If you ever add a rule that does, un-ignore with
  `cloud/.fastapicloudignore`.
- A deploy over 10 MB of any single file prints a warning (`--large-file-threshold`). Ours is about 1 MB in total.
- The entrypoint is `main:app`, set in `[tool.fastapi]` of `pyproject.toml` (needs `fastapi-cli` >= 0.0.16).
  Check it locally with `uv run fastapi dev` in `cloud/`; if that works, the deploy will too.
- Dependencies come from `pyproject.toml` / `uv.lock` and install in the cloud, not locally. No `[build-system]`
  or `[tool.uv]` section is needed: the project is an application (no wheel to build), and uv installs only its
  dependencies. FastAPI Cloud picks the latest stable Python allowed by `requires-python`. If a build fails for
  lack of a wheel on a new Python, pin it with `requires-python = "==3.13.*"`.

## Known limits

Documented by FastAPI Cloud:

- **Scale to zero is on by default.** An idle app stops and the next request pays a cold start. Hobby plans
  allow 1 to 3 replicas at most, Pro up to 10; instance sizes start at 0.1 CPU / 500 MiB (Free) and 0.5 CPU / 500 MiB (Micro).
- **Zero-downtime rollouts.** New instances must pass readiness checks before old ones stop.

Not documented, so treat as unknown and test:

- **Request timeout and streaming.** The docs state neither a maximum request duration nor how responses are
  buffered. The API streams NDJSON (`application/x-ndjson`) for `/api/analyze` and `/api/chat`; the analysis emits
  a progress event about every 700 ms, so the connection is never idle for long. If events arrive in one lump,
  check that the responses send `Cache-Control: no-cache` and `X-Accel-Buffering: no`. If long analyses are cut off
  with a 5xx, check `fastapi cloud logs` and open an issue with FastAPI Cloud support.
- **Filesystem.** Treat it as ephemeral. Profiles are cached in memory first and in JSON files under
  `TAYYARI_DATA_DIR` as a best effort.
- **Archive size cap.** The CLI fails with an "archive too large" error above a server-side limit; not a concern at ~1 MB.

Consequences for this app:

- **Profiles and chat sessions are in memory.** They reset on every redeploy, on scale-to-zero, and are not
  shared between replicas. After a reset, `GET /api/profile/:id` returns 404 and a chat with an unknown
  `sessionId` starts a fresh session (the model is told the earlier messages are no longer available). The precomputed sample
  profile is bundled and always available.
- **Keep one replica.** In the dashboard (Scaling), set the maximum replicas to 1 so a chat request lands on the
  replica that holds the profile and session.
- **No frontend CDN cache.** The frontend is served by the app itself (`cloud/static/`), not through FastAPI Cloud's
  `app.frontend()` helper, so assets are not cached on Cloudflare.
