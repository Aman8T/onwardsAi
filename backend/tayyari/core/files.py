"""Which uploaded resume files are accepted, shared by every route that takes one (analyze, the site hand-off).

The lists are the constants in `core.limits`; this module only holds the rule that reads them, so a route never
re-implements it.
"""

from __future__ import annotations

from pathlib import PurePath

from tayyari.core.limits import UPLOAD_EXTENSIONS, UPLOAD_MIME_TYPES

_EXTENSION_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
"""The media type each accepted extension stands for (the client's own label is not trusted when there is an extension)."""


def is_accepted_upload(filename: str, content_type: str | None) -> bool:
    """A resume file: a known extension, or no extension and a known MIME type."""
    ext = PurePath(filename).suffix.lower()
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return ext in UPLOAD_EXTENSIONS or (not ext and mime in UPLOAD_MIME_TYPES)


def resume_media_type(filename: str, content_type: str | None) -> str | None:
    """The media type to keep for an accepted resume file: the one its extension stands for, else the client's label when
    it is on the accepted list. None for a file `is_accepted_upload` refuses."""
    if not is_accepted_upload(filename, content_type):
        return None
    ext = PurePath(filename).suffix.lower()
    if ext in _EXTENSION_TYPES:
        return _EXTENSION_TYPES[ext]
    return (content_type or "").split(";", 1)[0].strip().lower()


def stored_media_type(filename: str, content_type: str | None, data: bytes) -> str:
    """The media type to keep with the bytes of an accepted resume file: `resume_media_type`, else what the first bytes
    say (a PDF, a zip that is a DOCX), else plain text."""
    known = resume_media_type(filename, content_type)
    if known is not None:
        return known
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:2] == b"PK":
        return _EXTENSION_TYPES[".docx"]
    return "text/plain"


__all__ = ["is_accepted_upload", "resume_media_type", "stored_media_type"]
