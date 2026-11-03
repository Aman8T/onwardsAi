#!/bin/bash
# Installs the app's dependencies when a Claude Code cloud session starts (SessionStart hook in
# .claude/settings.json). Does nothing on a local machine: CLAUDE_CODE_REMOTE is "true" only in the cloud VM.
# Never needs secrets: tests strip the model and speech keys, and nothing here reads .env.

if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 1

# Frontend: npm ci only when package.json or the lock changed since the last install.
stamp=node_modules/.cloud-setup.sha256
if ! sha256sum --check --status "$stamp" 2>/dev/null; then
  rm -f "$stamp"
  if npm ci --no-audit --no-fund; then
    sha256sum .claude/hooks/cloud-setup.sh package.json package-lock.json > "$stamp" || rm -f "$stamp"
  fi
fi

# Backend: uv picks a Python inside requires-python (fetching one if needed) and syncs backend/uv.lock.
uv sync --directory backend --frozen || uv sync --directory backend

exit 0
