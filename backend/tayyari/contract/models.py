"""Pydantic mirrors of shared/types.ts: the contract between the API and the web app.

Field names and literal values match the TypeScript types one-for-one, camelCase included, with no
aliases, so ``Profile.to_dict()`` is exactly the JSON the frontend expects. A field is optional here
(``X | None = None``) exactly when the TypeScript field is optional (``x?:``); `tests/unit/contract`
parses shared/types.ts and fails when the two drift.

Numbers: ``score``, ``alignment``, ``current``, ``target`` and ``progress`` are whole numbers (the
analysis clamps and rounds them), ``yearsExperience`` is a float.

Stream events live in `tayyari.contract.events`; request bodies in `tayyari.contract.requests`.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/types.ts)

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, TypeGuard, get_args

from pydantic import BaseModel, ConfigDict

SourceKind = Literal["resume", "github", "lovable", "linkedin", "portfolio", "link", "notes"]
SOURCE_KINDS: tuple[SourceKind, ...] = get_args(SourceKind)
"""Every `SourceKind`, in the order shared/types.ts lists them."""

ReaderStatus = Literal["queued", "reading", "done", "error"]
Phase = Literal["reading", "synthesizing", "done"]
ChatOriginKind = Literal["skill", "domain", "source", "role", "requirement", "you", "free"]
StorageKind = Literal["postgres", "ephemeral"]
"""Where profiles and chats are kept: Neon Postgres, or a throw-away local database (not durable)."""


def is_source_kind(value: str) -> TypeGuard[SourceKind]:
    """Narrow a plain string (for example `StoredSource.kind`) to `SourceKind`."""
    return value in SOURCE_KINDS


class _Model(BaseModel):
    """Base of every contract model: unknown keys are ignored, ``None`` optionals are omitted on output."""

    model_config = ConfigDict(extra="ignore")

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; optional fields left unset disappear, exactly like ``undefined`` in TypeScript."""
        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Validate parsed JSON into this model (raises `pydantic.ValidationError` when it does not fit)."""
        return cls.model_validate(dict(data))


# ───────────────────────── profile ─────────────────────────


class SourceSummary(_Model):
    """One connected source after a Haiku reader has gone through it."""

    id: str
    kind: SourceKind
    label: str
    url: str | None = None
    sub: str
    summary: str
    notes: list[str]
    ok: bool
    error: str | None = None


class Evidence(_Model):
    sourceId: str
    text: str


class Domain(_Model):
    id: str
    name: str
    short: str
    score: int
    summary: str


class Skill(_Model):
    id: str
    name: str
    domainId: str
    score: int
    evidence: list[Evidence]
    unlocks: list[str]


class RoleRequirement(_Model):
    name: str
    skillId: str | None = None
    have: bool
    current: int
    target: int


class Role(_Model):
    id: str
    title: str
    alignment: int
    eta: str
    why: str
    requirements: list[RoleRequirement]


class Person(_Model):
    name: str
    headline: str
    currentRole: str
    location: str | None = None
    yearsExperience: float | None = None
    summary: str


class Mastery(_Model):
    """How well the person covers their current role, with a one-line insight."""

    score: int
    insight: str


class ModelsUsed(_Model):
    """Model ids used, for the "built with" footnote."""

    reader: str
    synth: str


class ReadSegment(_Model):
    """One piece of the read, the three or four sentences about the person that open the dashboard. The pieces join with
    nothing between them into the prose; a piece that rests on a skill of the graph carries that skill's id, and the
    dashboard opens that skill's evidence under it."""

    text: str
    skillId: str | None = None


class Profile(_Model):
    id: str
    createdAt: str
    goal: str | None = None
    person: Person
    mastery: Mastery
    sources: list[SourceSummary]
    domains: list[Domain]
    skills: list[Skill]
    roles: list[Role]
    models: ModelsUsed
    read: list[ReadSegment] | None = None


# ───────────────────────── analysis stream ─────────────────────────


class ReaderState(_Model):
    sourceId: str
    kind: SourceKind
    src: str
    kindLabel: str
    agent: str
    status: ReaderStatus
    line: str
    progress: int


# ───────────────────────── chat ─────────────────────────


class ChatOrigin(_Model):
    """What the user clicked to start a thread."""

    kind: ChatOriginKind
    refId: str | None = None
    title: str
    caption: str


# ───────────────────────── health ─────────────────────────


class HealthModels(_Model):
    reader: str
    synth: str
    chat: str


class Health(_Model):
    """GET /api/health. ``storage`` is the one additive field: the web app ignores it."""

    ok: bool
    models: HealthModels
    hasSample: bool
    storage: StorageKind | None = None


__all__ = [
    "SOURCE_KINDS",
    "ChatOrigin",
    "ChatOriginKind",
    "Domain",
    "Evidence",
    "Health",
    "HealthModels",
    "Mastery",
    "ModelsUsed",
    "Person",
    "Phase",
    "Profile",
    "ReadSegment",
    "ReaderState",
    "ReaderStatus",
    "Role",
    "RoleRequirement",
    "Skill",
    "SourceKind",
    "SourceSummary",
    "StorageKind",
    "is_source_kind",
]
