"""``migrate``: bring the configured database to the newest schema (the app does the same at start-up)."""

from __future__ import annotations

import sys

from sqlalchemy.engine import make_url

from tayyari.core.dburl import DbConfig
from tayyari.core.settings import get_settings
from tayyari.storage.migrate import migrate_to_head_sync


def _scrub(message: str, cfg: DbConfig) -> str:
    """The message with the database password masked (a driver error can quote the connection string)."""
    try:
        password = make_url(cfg.url).password
    except Exception:
        return message
    return message.replace(password, "***") if password else message


def run() -> int:
    """Migrate the configured database to head and report the revision. Returns 1 when it cannot."""
    settings = get_settings()
    try:
        cfg = settings.db
    except ValueError as exc:  # an unsupported or unparsable DATABASE_URL (the message never echoes it)
        print(f"Invalid database configuration: {exc}", file=sys.stderr)
        return 1
    print(f"Migrating {cfg.safe_url} ...")
    try:
        revision = migrate_to_head_sync(cfg)
    except Exception as exc:
        print(f"Migration failed: {type(exc).__name__}: {_scrub(str(exc), cfg)}", file=sys.stderr)
        return 1
    print(f"Database is at revision {revision}")
    print(f"Database: {cfg.safe_url}")
    return 0
