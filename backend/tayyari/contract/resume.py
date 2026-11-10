"""Pydantic mirror of shared/resume.ts: the contract of `/resume` (one set of blocks, many resumes).

Same rules as `tayyari.contract.match`: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and
`tests/unit/resume/test_contract_parity.py` fails when the two drift. The constants are checked against the
TypeScript consts.

Shape: a person has one set of blocks (the cited `Fact`s of `contract.match`: one per job, project, degree and bullet)
and several resumes. A resume is an arrangement of those blocks, stored as a `ResumeVariant`, so every sentence names
the facts it rests on. It has a draft, edited freely, and at most one finalised version, the only one that can be
downloaded.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/resume.ts)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from tayyari.contract.match import Fact, LintReport, ResumeVariant
from tayyari.contract.models import _Model

# ───────────────────────── enums ─────────────────────────

ResumeKind = Literal["upload", "path"]
"""``upload``: the resume as the person wrote it. ``path``: arranged for one of the next roles."""
ResumeState = Literal["draft", "final"]
"""``draft`` while the draft differs from the finalised version or none exists yet, else ``final``."""
ResumeOpKind = Literal["move", "leave_out", "put_back", "edit", "reset"]
MoveDir = Literal["up", "down"]
MarkKind = Literal["moved", "edited", "added"]
"""How a line of a draft differs from the uploaded resume."""
FindingKind = Literal["number", "scope", "term", "name"]
"""What a check found in a line the person typed: a figure, a scope verb, a role's term or a name no cited fact has."""
FindingChoice = Literal["mine", "undo"]
"""``mine``: it is true, keep it as my own words. ``undo``: back to the fact's wording."""
BlockEntryKind = Literal["experience", "project", "education"]

# ───────────────────────── constants ─────────────────────────

RESUME_VERSION: Final = 1
MAX_RESUME_OPS: Final = 40
"""The most operations one PATCH may carry."""
MAX_LINE_CHARS: Final = 320
"""The longest line a person may type into a resume."""
MAX_FINDING_ANSWERS: Final = 200
"""The most answers one finalise call may carry (not part of the TypeScript contract: a bound on the body)."""

# ───────────────────────── list ─────────────────────────


class ResumeSummary(_Model):
    id: str
    kind: ResumeKind
    title: str
    """The file's label for an upload, the role's title for a path."""
    roleId: str | None = None
    """`Role.id` of the graph, for a path resume."""
    state: ResumeState
    isDefault: bool
    changes: int
    """Lines of the draft that differ from the uploaded resume: moved, edited, added or left out."""
    updatedAt: str
    finalisedAt: str | None = None


class PathSlot(_Model):
    """One of the graph's next roles, with the resume made for it when there is one."""

    roleId: str
    title: str
    alignment: int
    resumeId: str | None = None


class ResumeList(_Model):
    profileId: str
    resumes: list[ResumeSummary]
    paths: list[PathSlot]
    blocks: int
    """How many blocks the person has."""


# ───────────────────────── one resume ─────────────────────────


class BulletMark(_Model):
    bulletId: str
    kind: MarkKind


class ResumeDetail(_Model):
    resume: ResumeSummary
    variant: ResumeVariant
    """The draft."""
    marks: list[BulletMark]
    facts: list[Fact]
    """The facts the draft's lines cite, so the page can show each line's source."""
    leftOut: list[Fact]
    """Bullet blocks the person has that this resume does not use."""
    canDownload: bool
    """A finalised version exists, so the files can be downloaded."""


class ResumeOp(_Model):
    """``move``, ``leave_out``, ``edit``, ``reset`` name a line (``bulletId``); ``put_back`` names a block (``factId``)."""

    op: ResumeOpKind
    bulletId: str | None = None
    factId: str | None = None
    dir: MoveDir | None = None
    text: str | None = None


class ResumeOpsRequest(_Model):
    ops: list[ResumeOp] = Field(max_length=MAX_RESUME_OPS)


class PathRequest(_Model):
    roleId: str


# ───────────────────────── finalise ─────────────────────────


class Finding(_Model):
    bulletId: str
    kind: FindingKind
    text: str
    """The line as the person typed it."""
    what: str
    """The part no cited fact supports, a passage of ``text``."""
    options: list[FindingChoice]


class FindingAnswer(_Model):
    bulletId: str
    choice: FindingChoice


class FinaliseRequest(_Model):
    makeDefault: bool | None = None
    answers: list[FindingAnswer] | None = Field(default=None, max_length=MAX_FINDING_ANSWERS)


class FinaliseReport(_Model):
    done: bool
    """True when the resume was finalised by this call; false while findings wait for the person."""
    findings: list[Finding]
    fixed: list[str]
    """What the export fixed by itself, in plain words (layout only, never wording)."""
    lint: LintReport | None = None
    """The read-back of the exported files, when ``done``."""


class FinaliseResponse(_Model):
    detail: ResumeDetail
    report: FinaliseReport


# ───────────────────────── the blocks ─────────────────────────


class BlockEntry(_Model):
    factId: str
    kind: BlockEntryKind
    title: str
    org: str | None = None
    dates: str | None = None


class ResumeBlock(_Model):
    fact: Fact
    entryFactId: str | None = None
    """The job, project or degree the block belongs to."""
    usedIn: int
    """How many of the person's resumes use it."""


class BlocksResponse(_Model):
    profileId: str
    entries: list[BlockEntry]
    blocks: list[ResumeBlock]
    resumes: int
    """How many resumes the person has (the denominator of ``usedIn``)."""


__all__ = [
    "MAX_FINDING_ANSWERS",
    "MAX_LINE_CHARS",
    "MAX_RESUME_OPS",
    "RESUME_VERSION",
    "BlockEntry",
    "BlockEntryKind",
    "BlocksResponse",
    "BulletMark",
    "FinaliseReport",
    "FinaliseRequest",
    "FinaliseResponse",
    "Finding",
    "FindingAnswer",
    "FindingChoice",
    "FindingKind",
    "MarkKind",
    "MoveDir",
    "PathRequest",
    "PathSlot",
    "ResumeBlock",
    "ResumeDetail",
    "ResumeKind",
    "ResumeList",
    "ResumeOp",
    "ResumeOpKind",
    "ResumeOpsRequest",
    "ResumeState",
    "ResumeSummary",
]
