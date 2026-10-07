"""The per-profile context persisted next to a Profile: raw source text and each reader's output.

Same JSON shape as the legacy store files (`{"sources": [...], "readers": {...}}`), so old records load.
Frozen by the orchestrator at the Wave 0 interface freeze.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StoredSource(BaseModel):
    """One planned source and the text it produced (empty when its ingest failed)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    kind: str
    url: str | None = None
    content: str = ""


class StoredContext(BaseModel):
    """Everything chat needs beyond the Profile: source excerpts and the cleaned reader outputs."""

    model_config = ConfigDict(extra="ignore")

    sources: list[StoredSource] = Field(default_factory=list)
    readers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)
