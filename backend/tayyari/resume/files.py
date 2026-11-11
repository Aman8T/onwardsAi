"""Resume files in the service: what a second file adds to a profile, read back as blocks, a list and its texts.

Pure helpers over the stored files (`StoredResumeFile`): the merged book that every resume function sees, the other
wordings and open disagreements of a block, the list of files, the title of a file's resume. The service
(`ResumeService.upload_file`) does the work that needs the model or the repositories.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath

from pydantic import ValidationError

from tayyari.contract.resume import (
    BlockDisagreement,
    BlockWording,
    MergeCounts,
    MergeReport,
    ResumeFile,
)
from tayyari.core.logging import get_logger
from tayyari.match.models import FactBook
from tayyari.storage.ports import StoredResume, StoredResumeFile

log = get_logger(__name__)

TITLE_MAX = 255
FILENAME_MAX = 255


@dataclass(frozen=True, slots=True)
class ResumeText:
    """One stored resume file's text, for whoever reads every resume the person has (the graph)."""

    file_id: str
    filename: str
    text: str
    created_at: datetime
    origin: str


def clean_filename(name: str) -> str:
    """A file name as it is kept: no NUL or control character, one line, at most 255 characters."""
    one = " ".join("".join(ch for ch in name if ch.isprintable()).split())
    return one[:FILENAME_MAX] or "resume"


def file_title(filename: str) -> str:
    """The title of the resume made from a file: its name without the extension."""
    stem = PurePath(filename).stem.strip()
    return (stem or "Uploaded resume")[:TITLE_MAX]


def merged_book(base: FactBook, files: Sequence[StoredResumeFile]) -> FactBook:
    """The person's blocks as the newest file left them, else ``base`` (the book the first resume was made from). A file
    whose stored book cannot be read is skipped."""
    for row in reversed(files):
        if row.merged_book is None:
            continue
        try:
            return FactBook.model_validate(row.merged_book)
        except ValidationError:
            log.error("resume_file_unreadable", file_id=row.id)
    return base


def report_of(row: StoredResumeFile) -> MergeReport | None:
    if row.merge is None:
        return None
    try:
        return MergeReport.model_validate(row.merge)
    except ValidationError:
        log.error("resume_file_report_unreadable", file_id=row.id)
        return None


def resume_ids(files: Sequence[StoredResumeFile], resumes: Sequence[StoredResume]) -> dict[str, str]:
    """File id -> the id of the resume made from it (the analysis file's is the uploaded resume with no role key)."""
    uploads = {r.role_key: r.id for r in resumes if r.kind == "upload"}
    out: dict[str, str] = {}
    for row in files:
        found = uploads.get("" if row.origin == "analysis" else row.id)
        if found is not None:
            out[row.id] = found
    return out


def decorations(
    files: Sequence[StoredResumeFile], resumes: Sequence[StoredResume]
) -> tuple[dict[str, list[BlockWording]], dict[str, list[BlockDisagreement]]]:
    """Per block id: the wordings other files gave it (``near``) and the conflicts they raised (``disagree``), oldest
    file first."""
    made = resume_ids(files, resumes)
    wordings: dict[str, list[BlockWording]] = {}
    clashes: dict[str, list[BlockDisagreement]] = {}
    for row in files:
        report = report_of(row)
        if report is None:
            continue
        for item in report.near:
            wordings.setdefault(item.blockId, []).append(
                BlockWording(fileId=row.id, filename=row.filename, text=item.text, resumeId=made.get(row.id))
            )
        for item in report.disagree:
            clashes.setdefault(item.blockId, []).append(
                BlockDisagreement(
                    fileId=row.id,
                    filename=row.filename,
                    text=item.text,
                    differs=list(item.differs or []),
                    resumeId=made.get(row.id),
                )
            )
    return wordings, clashes


def listing(files: Sequence[StoredResumeFile], resumes: Sequence[StoredResume]) -> list[ResumeFile]:
    made = resume_ids(files, resumes)
    out: list[ResumeFile] = []
    for row in files:
        report = report_of(row)
        out.append(
            ResumeFile(
                id=row.id,
                filename=row.filename,
                mime=row.mime,
                size=row.size,
                origin=row.origin,
                uploadedAt=row.created_at.isoformat(),
                resumeId=made.get(row.id),
                counts=None
                if report is None
                else MergeCounts(
                    same=len(report.same),
                    new=len(report.new),
                    near=len(report.near),
                    disagree=len(report.disagree),
                ),
            )
        )
    return out


__all__ = [
    "ResumeText",
    "clean_filename",
    "decorations",
    "file_title",
    "listing",
    "merged_book",
    "report_of",
    "resume_ids",
]
