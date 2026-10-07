"""
Python mirror of shared/types.ts — the contract between the API and the web app.

Field names and literal values match the TypeScript types one-for-one (camelCase included), so a
`Profile.to_dict()` is exactly the JSON the frontend expects. Stream events are plain dicts built by the
`ev_*` helpers; `AnalyzeInput` is what the HTTP layer hands to the pipeline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SourceKind = Literal['resume', 'github', 'lovable', 'linkedin', 'portfolio', 'link', 'notes']
SOURCE_KINDS: tuple[str, ...] = ('resume', 'github', 'lovable', 'linkedin', 'portfolio', 'link', 'notes')

ReaderStatus = Literal['queued', 'reading', 'done', 'error']
Phase = Literal['reading', 'synthesizing', 'done']
ChatOriginKind = Literal['skill', 'domain', 'source', 'role', 'requirement', 'you', 'free']


class _Model(BaseModel):
    """Base for every contract model: unknown keys are ignored, `None` optionals are omitted on output."""

    model_config = ConfigDict(extra='ignore')

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; optional fields left unset disappear, exactly like `undefined` in the Node version."""
        return self.model_dump(exclude_none=True)


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
    score: int
    insight: str


class ModelsUsed(_Model):
    reader: str
    synth: str


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


def _state_dict(state: ReaderState | Mapping[str, Any]) -> dict[str, Any]:
    return state.to_dict() if isinstance(state, ReaderState) else dict(state)


def ev_readers(readers: Sequence[ReaderState | Mapping[str, Any]]) -> dict[str, Any]:
    return {'type': 'readers', 'readers': [_state_dict(r) for r in readers]}


def ev_reader(state: ReaderState | Mapping[str, Any]) -> dict[str, Any]:
    return {'type': 'reader', 'reader': _state_dict(state)}


def ev_signal(label: str, src: str, kind: str) -> dict[str, Any]:
    return {'type': 'signal', 'label': label, 'src': src, 'kind': kind}


def ev_log(who: str, text: str, kind: str | None = None) -> dict[str, Any]:
    ev: dict[str, Any] = {'type': 'log', 'who': who, 'text': text}
    if kind is not None:
        ev['kind'] = kind
    return ev


def ev_phase(phase: str, label: str) -> dict[str, Any]:
    return {'type': 'phase', 'phase': phase, 'label': label}


def ev_synth(step: int, chars: int | None = None) -> dict[str, Any]:
    ev: dict[str, Any] = {'type': 'synth', 'step': step}
    if chars is not None:
        ev['chars'] = chars
    return ev


def ev_done(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {'type': 'done', 'profile': dict(profile)}


def ev_error(message: str) -> dict[str, Any]:
    return {'type': 'error', 'message': message}


# ───────────────────────── chat ─────────────────────────


class ChatOrigin(_Model):
    """What the user clicked to start a thread."""

    kind: ChatOriginKind
    refId: str | None = None
    title: str = ''
    caption: str = ''


class ChatRequest(_Model):
    profileId: str
    message: str
    origin: ChatOrigin | None = None
    sessionId: str | None = None


# ───────────────────────── pipeline input ─────────────────────────


@dataclass
class AnalyzeInput:
    """What POST /api/analyze hands to the pipeline after multipart parsing."""

    resume_bytes: bytes | None = None
    resume_name: str | None = None
    links: list[str] = field(default_factory=list)
    goal: str | None = None
    notes: str | None = None
    sample: bool = False
