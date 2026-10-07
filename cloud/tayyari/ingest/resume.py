"""Resume parsing: PDF (pypdf), DOCX (python-docx) and plain text, turned into a `SourceDoc`.

The CPU-bound work (parsing, normalising, section and skill detection) runs in a worker thread so
the event loop stays free.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .classify import IngestLog, SourceDoc, plural
from .terms import find_terms

FORMAT_LABEL = {"pdf": "PDF", "docx": "DOCX", "text": "Text"}
MAX_RESUME_CHARS = 30_000
"""Only the first MAX_RESUME_CHARS are analysed and returned."""
MAX_EXTRACT_CHARS = 250_000
"""PDF extraction stops after this many characters (a resume is a few thousand)."""
MAX_PDF_PAGES = 60
"""Pages read from a PDF (the text kept is capped far below what this many pages hold)."""
MAX_DOCX_UNZIPPED = 30 * 1024 * 1024
"""A DOCX whose parts declare more than this many bytes is refused (zip bomb)."""
MIN_TEXT_CHARS = 200
"""Below this a PDF is assumed to be scanned/image-based and is handed to the reader as a file."""

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}
_PASSWORD_MSG = "That PDF is password-protected. Export an unlocked copy and upload it again."
_ENCRYPTED_MSG = "That PDF is encrypted and cannot be opened here. Export an unprotected copy and upload it again."
_DOCX_TOO_BIG = "That DOCX file is too large to read here. Export it as a PDF and upload it again."
_UNREADABLE_MSG = {
    "pdf": "Could not read that PDF. It may be corrupted. Try exporting it again or upload a DOCX.",
    "docx": "Could not read that DOCX file. It may be corrupted. Try exporting it again or upload a PDF.",
    "text": "Could not read that text file.",
}


class ResumeError(ValueError):
    """A resume that cannot be read; the message is written for the person who uploaded it."""

    def __init__(self, message: str, fmt: str | None = None) -> None:
        super().__init__(message)
        self.fmt = fmt


def _detect_format(filename: str, head: bytes) -> str:
    """"pdf", "docx" or "text" by extension, then by magic bytes; ResumeError for anything else."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext == ".doc":
        raise ResumeError("Legacy .doc files are not supported. Save the resume as DOCX or PDF and upload it again.")
    if ext in _TEXT_EXTENSIONS:
        return "text"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[:2] == b"PK":
        return "docx"
    raise ResumeError("Unsupported file type. Upload your resume as a PDF, DOCX or TXT file.")


_ZERO_WIDTH = re.compile("[\u200b-\u200d\u2060\ufeff]")
_CONTROL = re.compile("[\x00-\x08\x0b\x0e-\x1f]")
_ODD_SPACES = re.compile("[\u00a0\u2007\u202f]")


def clean_text(text: str) -> str:
    """Normalise whitespace and strip invisible junk that PDF/DOCX extraction leaves behind."""
    s = unicodedata.normalize("NFKC", text)  # ligatures, full-width forms, odd spaces
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    s = _ODD_SPACES.sub(" ", s)
    s = _ZERO_WIDTH.sub("", s)
    s = _CONTROL.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("Summary", r"^(?:(?:professional|career|executive) )?(?:summary|profile|objective|about(?: me)?)$"),
        (
            "Experience",
            (
                r"^(?:(?:professional|work|relevant|industry|employment|internship|internships) )?(?:experience|history)$"
                r"|^employment$|^internships?$"
            ),
        ),
        ("Projects", r"^(?:(?:personal|academic|selected|key|side|notable|technical) )?projects?$"),
        (
            "Skills",
            (
                r"^(?:(?:technical|core|key|professional) )?skills?(?: (?:& |and )?(?:tools|technologies|expertise))?$"
                r"|^(?:tech(?:nical)? stack|technologies|tools(?: (?:& |and )technologies)?|competencies|expertise)$"
            ),
        ),
        ("Education", r"^education(?: (?:& |and )?(?:training|certifications?))?$|^academics?$|^academic background$"),
        (
            "Certifications",
            (
                r"^(?:certifications?|licen[sc]es?(?: (?:& |and )?certifications?)?|courses|training"
                r"|courses (?:& |and )certifications?)$"
            ),
        ),
        (
            "Achievements",
            (
                r"^(?:achievements?|awards?(?: (?:& |and )?(?:honou?rs|achievements?|recognition))?"
                r"|honou?rs(?: (?:& |and )awards)?|accomplishments|recognition)$"
            ),
        ),
        ("Publications", r"^(?:publications?|research|papers)$"),
        ("Open Source", r"^open[- ]source(?: contributions?)?$"),
        (
            "Leadership",
            (
                r"^(?:leadership|extracurriculars?|extra[- ]curricular activities|activities|positions of responsibility"
                r"|volunteer(?:ing)?(?: experience)?|community)$"
            ),
        ),
        ("Languages", r"^languages$"),
        ("Interests", r"^(?:interests|hobbies)$"),
    )
)

_HEADING_LEAD = " \t\r\n\f\v-–—•·*#|:_=~"
_HEADING_TRAIL = " \t\r\n\f\v:-–—|_=~"


def detect_sections(text: str) -> list[str]:
    """Section headings found in the text, in document order ("Experience", "Projects", "Skills"...)."""
    found: list[str] = []
    for raw_line in text.split("\n"):
        # lstrip/rstrip with a character set (not a regex) so a line of 30k dashes costs linear time
        line = raw_line.strip().lstrip(_HEADING_LEAD).rstrip(_HEADING_TRAIL).lower()
        if not line or len(line) > 45:
            continue
        for name, pattern in _SECTION_PATTERNS:
            if pattern.search(line):
                if name not in found:
                    found.append(name)
                break
    return found


# --------------------------------------------------------------------------- extractors


def _extract_pdf(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                unlocked = bool(reader.decrypt(""))
            except DependencyError as exc:  # AES needs the optional `cryptography` package
                raise ResumeError(_ENCRYPTED_MSG, "pdf") from exc
            except Exception:  # noqa: BLE001 - wrong password or an unsupported scheme
                unlocked = False
            if not unlocked:
                raise ResumeError(_PASSWORD_MSG, "pdf")
        pages = len(reader.pages)
        parts: list[str] = []
        total = 0
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one bad page must not lose the others
                text = ""
            parts.append(text)
            total += len(text)
            if total >= MAX_EXTRACT_CHARS:
                break
        return "\n\n".join(parts), pages
    except ResumeError:
        raise
    except Exception as exc:  # pypdf raises many types for damaged files
        if re.search(r"password", f"{type(exc).__name__} {exc}", re.IGNORECASE):
            raise ResumeError(_PASSWORD_MSG, "pdf") from exc
        raise ResumeError(_UNREADABLE_MSG["pdf"], "pdf") from exc


def _docx_lines(document: Any) -> Iterator[str]:
    """Paragraphs and table cells of a document in reading order."""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document).text
        elif child.tag == qn("w:tbl"):
            for row in Table(child, document).rows:
                seen: set[int] = set()
                for cell in row.cells:  # merged cells repeat; read each underlying cell once
                    if id(cell._tc) not in seen:
                        seen.add(id(cell._tc))
                        for paragraph in cell.paragraphs:
                            yield paragraph.text


def _extract_docx(data: bytes) -> str:
    import docx

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(member.file_size for member in archive.infolist()) > MAX_DOCX_UNZIPPED:
                raise ResumeError(_DOCX_TOO_BIG, "docx")
        lines: list[str] = []
        total = 0
        for line in _docx_lines(docx.Document(io.BytesIO(data))):
            if line.strip():
                lines.append(line)
                total += len(line)
                if total >= MAX_EXTRACT_CHARS:
                    break
        return "\n".join(lines)
    except ResumeError:
        raise
    except Exception as exc:  # bad zip, missing parts, malformed XML
        raise ResumeError(_UNREADABLE_MSG["docx"], "docx") from exc


@dataclass
class _Parsed:
    fmt: str
    text: str
    """The whole cleaned text (analysis below only looks at the first MAX_RESUME_CHARS)."""
    pages: int | None


def _parse(data: bytes, filename: str) -> _Parsed:
    fmt = _detect_format(filename, data[:8])
    pages: int | None = None
    if fmt == "pdf":
        raw, pages = _extract_pdf(data)
    elif fmt == "docx":
        raw = _extract_docx(data)
    else:
        raw = data.decode("utf-8", errors="replace")
    return _Parsed(fmt, clean_text(raw), pages)


async def parse_resume(data: bytes, filename: str) -> str:
    """The cleaned text of a PDF, DOCX or text resume. Raises ResumeError (with a user-facing message)."""
    return (await asyncio.to_thread(_parse, data, filename)).text


@dataclass
class _Analysis:
    parsed: _Parsed
    text: str
    words: int
    sections: list[str]
    signals: list[str]


def _read(data: bytes, filename: str) -> _Analysis:
    parsed = _parse(data, filename)
    text = parsed.text[:MAX_RESUME_CHARS]
    return _Analysis(parsed, text, len(text.split()), detect_sections(text), find_terms(text, 8))


async def ingest_resume(data: bytes, filename: str | None, log: IngestLog) -> SourceDoc:
    """Read an uploaded resume. Expected problems come back as a failed `SourceDoc`, never an exception."""
    name = filename or "resume"

    def failed(message: str, fmt: str | None = None, **extra: Any) -> SourceDoc:
        kind_label = f"Resume · {FORMAT_LABEL[fmt]}" if fmt else "Resume"
        return SourceDoc.failure("resume", "resume", "Resume", kind_label, None, name, message, **extra)

    if not data:
        return failed("That file is empty. Please upload your resume again.")
    try:
        a = await asyncio.to_thread(_read, data, name)
    except ResumeError as exc:
        return failed(str(exc), exc.fmt)

    fmt, chars, words = a.parsed.fmt, len(a.parsed.text), a.words
    pages = a.parsed.pages if fmt == "pdf" else max(1, -(-chars // 3200))
    if fmt == "pdf":
        log(f"Parsed {plural(pages or 0, 'page')}")
    else:
        log(f"Read {words:,} words from the document" if fmt == "docx" else f"Read {words:,} words of text")
    if a.sections:
        log(f"Found sections: {', '.join(a.sections)}")
    if a.signals:
        log(f"Spotted {plural(len(a.signals), 'skill')}: {', '.join(a.signals)}")

    meta: dict[str, Any] = {
        "pages": pages,
        "chars": chars,
        "words": words,
        "sections": a.sections,
        "format": FORMAT_LABEL[fmt],
    }
    if chars > MAX_RESUME_CHARS:
        meta["truncated"] = True
    doc = SourceDoc(
        id="resume",
        kind="resume",
        label="Resume",
        src=name,
        kind_label=f"Resume · {FORMAT_LABEL[fmt]}",
        text=a.text,
        meta=meta,
        quick_signals=a.signals,
    )

    if chars < MIN_TEXT_CHARS:
        if fmt == "pdf":
            # Scanned or image-only PDF: the reader looks at the file itself.
            log("Little selectable text found, the reader will view the file directly")
            doc.meta["scanned"] = True
            doc.pdf_bytes = data
            doc.text = (
                "The text of this resume could not be extracted (it looks like a scanned or image-based PDF). "
                "If the PDF is attached to this message, read the resume from it."
                + (f"\n\nPartial text that could be extracted:\n{a.text}" if a.text else "")
            )
        elif chars == 0:
            return failed(
                "No readable text was found in that file. Try a different export of your resume.", fmt, meta=meta
            )
    return doc
