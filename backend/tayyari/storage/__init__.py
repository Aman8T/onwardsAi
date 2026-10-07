"""Persistence: the async database, the schema, repository ports and implementations, migrations.

Depends only on `tayyari.core` and `tayyari.contract` (records and stored context). Services import the
Protocols from `ports` and are handed a `Repositories` bundle by the container.
"""

from __future__ import annotations

from .db import Database
from .maintenance import run_maintenance
from .migrate import head_revision, migrate_to_head, migrate_to_head_sync
from .ports import (
    ChatMessage,
    ChatSession,
    DecisionLogRepo,
    KvRepo,
    LimitRepo,
    LlmCallRepo,
    ProfileRepo,
    SessionConflict,
    SessionNotFound,
    SessionRepo,
    ToolEvent,
)
from .repos import Repositories, build_repositories

__all__ = [
    "ChatMessage",
    "ChatSession",
    "Database",
    "DecisionLogRepo",
    "KvRepo",
    "LimitRepo",
    "LlmCallRepo",
    "ProfileRepo",
    "Repositories",
    "SessionConflict",
    "SessionNotFound",
    "SessionRepo",
    "ToolEvent",
    "build_repositories",
    "head_revision",
    "migrate_to_head",
    "migrate_to_head_sync",
    "run_maintenance",
]
