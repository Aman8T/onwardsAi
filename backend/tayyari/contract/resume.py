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

from tayyari.contract.match import Fact, LintReport, MatchFactKind, ResumeVariant
from tayyari.contract.models import _Model

# ───────────────────────── enums ─────────────────────────

ResumeKind = Literal["upload", "path", "posting"]
"""``upload``: the resume as the person wrote it. ``path``: arranged for one of the next roles. ``posting``: the resume
tailored for a posting in Match, saved into the list by the person."""
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
MergeSection = Literal["same", "new", "near", "disagree"]
"""Where a line of a second file went: ``same`` (the block exists, this file is another source of it), ``new`` (a new
block), ``near`` (the same thing worded differently: both wordings are kept) or ``disagree`` (the same entry or line with
a conflicting figure, date, title or employer: each file keeps its own words)."""
MergeDiffer = Literal["figure", "date", "title", "employer"]
ResumeFileOrigin = Literal["analysis", "upload"]
"""``analysis``: the file the profile was analysed from. ``upload``: a file added later in `/resume`."""

# ───────────────────────── constants ─────────────────────────

RESUME_VERSION: Final = 1
MAX_RESUME_OPS: Final = 40
"""The most operations one PATCH may carry."""
MAX_LINE_CHARS: Final = 320
"""The longest line a person may type into a resume."""
MAX_WORDINGS_PER_LINE: Final = 3
"""The most wordings from other resumes offered for one line."""
MAX_RESUME_FILES: Final = 8
"""The most resume files one profile keeps (the one it was analysed from included)."""
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
    matchId: str | None = None
    """The posting match it was saved from, for a posting resume."""
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


# ───────────────────────── the grammar check ─────────────────────────


class GrammarProposal(_Model):
    """One line whose grammar, spelling or punctuation can be fixed without changing what it says. Not stored: the
    person accepts it by sending the ``edit`` op for ``bulletId`` with ``after`` as its text."""

    bulletId: str
    before: str
    after: str
    why: str


class GrammarResponse(_Model):
    proposals: list[GrammarProposal]
    ran: bool
    """False when the check could not run (no model key, a model failure, a timeout): ``proposals`` is then empty."""


# ───────────────────────── a wording from another resume ─────────────────────────


class LineWording(_Model):
    """A finalised wording of the same block(s) that another resume of the profile uses: the person may take it for
    ``lineId`` of this resume (the ``edit`` op with ``text``)."""

    lineId: str
    text: str
    fromResumeId: str
    fromTitle: str


class WordingsResponse(_Model):
    wordings: list[LineWording]
    """At most `MAX_WORDINGS_PER_LINE` per line, de-duplicated, never the line's own current text."""


# ───────────────────────── saving a match's resume ─────────────────────────


class FromMatchRequest(_Model):
    matchId: str


# ───────────────────────── the blocks ─────────────────────────


class BlockEntry(_Model):
    factId: str
    kind: BlockEntryKind
    title: str
    org: str | None = None
    dates: str | None = None


class BlockWording(_Model):
    """Another wording of a block, from a file that said the same thing differently (a ``near`` line)."""

    fileId: str
    filename: str
    text: str
    resumeId: str | None = None
    """The resume made from that file."""


class BlockDisagreement(_Model):
    """A file whose line is the same entry or bullet as the block but conflicts with it. It keeps its own words."""

    fileId: str
    filename: str
    text: str
    differs: list[MergeDiffer]
    resumeId: str | None = None


class ResumeBlock(_Model):
    fact: Fact
    entryFactId: str | None = None
    """The job, project or degree the block belongs to."""
    usedIn: int
    """How many of the person's resumes use it."""
    wordings: list[BlockWording] | None = None
    """The block's other wordings, each tied to its file (absent when there are none)."""
    disagreements: list[BlockDisagreement] | None = None
    """What other files say that conflicts with the block (absent when nothing does)."""


class BlocksResponse(_Model):
    profileId: str
    entries: list[BlockEntry]
    blocks: list[ResumeBlock]
    resumes: int
    """How many resumes the person has (the denominator of ``usedIn``)."""


# ───────────────────────── a second file ─────────────────────────


class MergeItem(_Model):
    """One line of a file and where it went."""

    blockId: str
    """The block of the person's set: an existing one for ``same``, ``near`` and ``disagree``, a new one for ``new``."""
    kind: MatchFactKind
    text: str
    """The new file's own words."""
    existing: str | None = None
    """``near`` and ``disagree``: the words the block already had."""
    differs: list[MergeDiffer] | None = None
    """``disagree``: what differs."""


class MergeCounts(_Model):
    same: int
    new: int
    near: int
    disagree: int


class MergeReport(_Model):
    """Every fact of a file in exactly one list."""

    fileId: str
    filename: str
    same: list[MergeItem]
    new: list[MergeItem]
    near: list[MergeItem]
    disagree: list[MergeItem]


class UploadResponse(_Model):
    """``POST /api/resumes/upload``: the file's own resume and how it merged into the blocks."""

    resume: ResumeDetail
    merge: MergeReport


class ResumeFile(_Model):
    id: str
    filename: str
    mime: str
    size: int
    origin: ResumeFileOrigin
    uploadedAt: str
    resumeId: str | None = None
    """The resume made from this file."""
    counts: MergeCounts | None = None
    """How its lines merged (absent for the file the profile was analysed from)."""


class ResumeFilesResponse(_Model):
    profileId: str
    files: list[ResumeFile]
    """Oldest first: the file of the analysis, then each upload."""


__all__ = [
    "MAX_FINDING_ANSWERS",
    "MAX_LINE_CHARS",
    "MAX_RESUME_FILES",
    "MAX_RESUME_OPS",
    "MAX_WORDINGS_PER_LINE",
    "RESUME_VERSION",
    "BlockDisagreement",
    "BlockEntry",
    "BlockEntryKind",
    "BlockWording",
    "BlocksResponse",
    "BulletMark",
    "FinaliseReport",
    "FinaliseRequest",
    "FinaliseResponse",
    "Finding",
    "FindingAnswer",
    "FindingChoice",
    "FindingKind",
    "FromMatchRequest",
    "GrammarProposal",
    "GrammarResponse",
    "LineWording",
    "MarkKind",
    "MergeCounts",
    "MergeDiffer",
    "MergeItem",
    "MergeReport",
    "MergeSection",
    "MoveDir",
    "PathRequest",
    "PathSlot",
    "ResumeBlock",
    "ResumeDetail",
    "ResumeFile",
    "ResumeFileOrigin",
    "ResumeFilesResponse",
    "ResumeKind",
    "ResumeList",
    "ResumeOp",
    "ResumeOpKind",
    "ResumeOpsRequest",
    "ResumeState",
    "ResumeSummary",
    "UploadResponse",
    "WordingsResponse",
]
