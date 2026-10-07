"""Resume parsing: PDF (pypdf), DOCX (python-docx) and plain text, turned into a `SourceDoc`.

The CPU-bound work (parsing, normalising, section and skill detection) runs in a worker thread so
the event loop stays free.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import time
import unicodedata
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from typing import IO, Any

from ..core import limits
from ..core.logging import get_logger
from ..core.text import plural
from .classify import IngestLog, SourceDoc, strip_nul
from .terms import find_terms

logger = get_logger(__name__)

FORMAT_LABEL = {"pdf": "PDF", "docx": "DOCX", "text": "Text"}
MAX_RESUME_CHARS = limits.RESUME_MAX_CHARS
"""Only the first MAX_RESUME_CHARS are analysed and returned."""
MAX_EXTRACT_CHARS = limits.RESUME_MAX_EXTRACT_CHARS
"""PDF extraction stops after this many characters (a resume is a few thousand)."""
MAX_PDF_PAGES = limits.RESUME_MAX_PDF_PAGES
"""Pages read from a PDF (the text kept is capped far below what this many pages hold)."""
MAX_DOCX_UNZIPPED = limits.RESUME_MAX_DOCX_UNZIPPED_BYTES
"""A DOCX whose parts declare more than this many bytes in total is refused (zip bomb)."""
MAX_DOCX_XML = limits.RESUME_MAX_DOCX_XML_BYTES
"""No XML part of a DOCX (word/document.xml above all) may be larger than this once unpacked."""
MAX_DOCX_ELEMENTS = limits.RESUME_MAX_DOCX_ELEMENTS
"""Paragraph and run start tags allowed in a DOCX; counted while streaming, before python-docx builds its tree."""
MIN_TEXT_CHARS = limits.RESUME_MIN_TEXT_CHARS
"""Below this a PDF is assumed to be scanned/image-based and is handed to the reader as a file."""
MAX_PDF_STREAM_BYTES = limits.RESUME_MAX_PDF_STREAM_BYTES
"""One PDF stream may inflate to at most this much while its text is read (a resume's streams are far smaller)."""
PDF_EXTRACT_BUDGET_S = 10.0
"""No PDF page or content operator is started after this long (the thread reading it cannot be cancelled)."""
MAX_PDF_FORM_INVOCATIONS = 64
"""Form XObjects expanded per page while reading text (pypdf's own default is 5000, each one a stream to parse)."""

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}
_PASSWORD_MSG = "That PDF is password-protected. Export an unlocked copy and upload it again."
_ENCRYPTED_MSG = (
    "That PDF is encrypted and cannot be opened here. Export an unprotected copy and upload it again."
)
TOO_COMPLEX_MSG = "That file is too large or complex to read as a resume."
"""The one answer for a document that would cost too much to open (zip bomb, element flood, stream flood)."""
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
    """ "pdf", "docx" or "text" by extension, then by magic bytes; ResumeError for anything else."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext == ".doc":
        raise ResumeError(
            "Legacy .doc files are not supported. Save the resume as DOCX or PDF and upload it again."
        )
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
        (
            "Education",
            r"^education(?: (?:& |and )?(?:training|certifications?))?$|^academics?$|^academic background$",
        ),
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


@contextmanager
def _pdf_limits() -> Iterator[None]:
    """Cap how much pypdf may inflate from ONE stream (its default is 75 MB) and how many forms it expands.

    A 64 KB PDF can hold a content stream that inflates to 70 MB of blanks: pypdf's pure-Python parser then
    needs about ten seconds and 140 MB for that one page, in a thread that cannot be cancelled, and a
    document can point every page at the same stream. A resume's streams are a few hundred KB.

    The declared stream length is left alone on purpose: it can never exceed the file (10 MB at most), and a
    photo or a scanned page above the stream cap is a legitimate stream that text extraction never inflates.
    """
    import pypdf

    apply = getattr(pypdf, "apply_configuration", None)
    if apply is None:  # pypdf 5 has no per-call configuration
        yield
        return
    cap = MAX_PDF_STREAM_BYTES
    wanted: dict[str, int] = {
        "array_based_stream_maximum_output_length": cap,
        "lzw_maximum_output_length": cap,
        "run_length_maximum_output_length": cap,
        "zlib_maximum_output_length": cap,
        "image_maximum_buffer_size": cap,
        "xform_maximum_invocations_per_extraction": MAX_PDF_FORM_INVOCATIONS,
    }
    known = {f.name for f in fields(pypdf.get_configuration())}
    with apply(**{name: value for name, value in wanted.items() if name in known}):
        yield


class _OutOfTime(BaseException):  # noqa: N818 - not an error: it is raised through pypdf to stop reading a page
    """The PDF time budget is spent. A BaseException, so no `except Exception` inside pypdf can swallow it."""


def _extract_pdf(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError

    deadline = time.monotonic() + PDF_EXTRACT_BUDGET_S

    def check_time(*_operator: object) -> None:
        """Called by pypdf before every content operator, so one heavy page cannot outlive the budget."""
        if time.monotonic() > deadline:
            raise _OutOfTime

    try:
        with _pdf_limits():
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
                if parts and time.monotonic() > deadline:
                    break  # what was read is used; the reader looks at the file itself if that is too little
                try:
                    text = page.extract_text(visitor_operand_before=check_time) or ""
                except _OutOfTime:
                    break
                except Exception:  # noqa: BLE001 - one bad page must not lose the others
                    text = ""
                parts.append(text)
                total += len(text)
                if total >= MAX_EXTRACT_CHARS:
                    break
            return "\n\n".join(parts)[:MAX_EXTRACT_CHARS], pages
    except ResumeError:
        raise
    except Exception as exc:  # pypdf raises many types for damaged files
        if re.search(r"password", f"{type(exc).__name__} {exc}", re.IGNORECASE):
            raise ResumeError(_PASSWORD_MSG, "pdf") from exc
        if type(exc).__name__ == "LimitReachedError":  # pypdf itself refused to open it (a flood of pages...)
            raise ResumeError(TOO_COMPLEX_MSG, "pdf") from exc
        raise ResumeError(_UNREADABLE_MSG["pdf"], "pdf") from exc


# A start tag of a paragraph or a run, whatever namespace prefix it uses: <w:p>, <w:r w:rsidR="..">, <x:p/>.
# Bounded and with no nested quantifier, so it is linear; "<w:pPr>" and "<w:rFonts/>" do not match.
_PARAGRAPH_OR_RUN = re.compile(rb"<(?:[A-Za-z_][\w.\-]{0,30}:)?[pr][\s/>]")
_SCAN_CHUNK = 64 * 1024
_TAG_MAX = 48
"""Longer than any match of `_PARAGRAPH_OR_RUN`: a tag cut by the end of a chunk is carried over to the next."""


def _count_paragraphs_and_runs(stream: IO[bytes], limit: int) -> int:
    """Paragraph and run start tags in an XML stream, read in chunks; stops as soon as the count passes `limit`.

    Streaming keeps the memory flat, and the count is what tells a resume (hundreds) from a flood (a million
    `<w:p><w:r>` pairs in a few MB) before python-docx turns every element into an object.
    """
    count = 0
    pending = b""
    while True:
        chunk = stream.read(_SCAN_CHUNK)
        buffer = pending + chunk
        pending = b""
        if chunk:
            # A tag starts at one "<" and holds no other, so everything before the last "<" is complete.
            cut = buffer.rfind(b"<", max(0, len(buffer) - _TAG_MAX))
            if cut >= 0:
                buffer, pending = buffer[:cut], buffer[cut:]
        count += len(_PARAGRAPH_OR_RUN.findall(buffer))
        if count > limit or not chunk:
            return count


def _check_docx_cost(data: bytes) -> None:
    """Refuse a DOCX that would be expensive to open. Reads the zip directory and streams the XML parts once.

    The declared sizes are binding: `zipfile` never hands out more bytes than a member declares. The parts
    together may not unpack past MAX_DOCX_UNZIPPED, no XML part may pass MAX_DOCX_XML, and the XML together
    may hold at most MAX_DOCX_ELEMENTS paragraph and run tags (python-docx builds a Python object tree, about
    20 times the size of the XML, in a thread that cannot be cancelled).
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if sum(member.file_size for member in members) > MAX_DOCX_UNZIPPED:
            raise ResumeError(TOO_COMPLEX_MSG, "docx")
        elements = 0
        for member in members:
            if not member.filename.lower().endswith(".xml"):
                continue
            if member.file_size > MAX_DOCX_XML:
                raise ResumeError(TOO_COMPLEX_MSG, "docx")
            with archive.open(member) as stream:
                elements += _count_paragraphs_and_runs(stream, MAX_DOCX_ELEMENTS - elements)
            if elements > MAX_DOCX_ELEMENTS:
                raise ResumeError(TOO_COMPLEX_MSG, "docx")


def _docx_lines(document: Any) -> Iterator[str]:
    """Paragraphs and table cells of a document in reading order.

    Table cells are read straight from the XML, so no table grid is built (a table of tens of thousands of
    cells costs only its paragraphs). A merged cell is a single `w:tc`, so it is read once.
    """
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    p_tag, tbl_tag, tr_tag, tc_tag = qn("w:p"), qn("w:tbl"), qn("w:tr"), qn("w:tc")
    for child in document.element.body.iterchildren():
        if child.tag == p_tag:
            yield Paragraph(child, document).text
        elif child.tag == tbl_tag:
            for row in child.iterchildren(tr_tag):
                for cell in row.iterchildren(tc_tag):
                    for paragraph in cell.iterchildren(p_tag):
                        yield Paragraph(paragraph, document).text


def _extract_docx(data: bytes) -> str:
    import docx

    try:
        _check_docx_cost(data)
        lines: list[str] = []
        total = 0
        for line in _docx_lines(docx.Document(io.BytesIO(data))):
            if line.strip():
                lines.append(line)
                total += len(line)
                if total >= MAX_EXTRACT_CHARS:
                    break
        return "\n".join(lines)[:MAX_EXTRACT_CHARS]
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
        # A UTF-8 character is at most 4 bytes, so this keeps every character that survives the cut.
        raw = data[: MAX_EXTRACT_CHARS * 4].decode("utf-8", errors="replace")[:MAX_EXTRACT_CHARS]
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
    name = strip_nul(filename or "resume") or "resume"

    def failed(message: str, fmt: str | None = None, **extra: Any) -> SourceDoc:
        logger.info("resume_rejected", format=fmt, reason=message)  # our own wording, never the document
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
            # Scanned or image-only PDF: the reader looks at the file itself. The text stays EMPTY on purpose:
            # the pipeline attaches `pdf_bytes` to the reader call only when the source has no text (a
            # placeholder here made that branch unreachable). The source is still readable (`ok` stays True,
            # no error), and the few characters that were extracted are not worth keeping.
            log("Little selectable text found, the reader will view the file directly")
            doc.meta["scanned"] = True
            doc.pdf_bytes = data
            doc.text = ""
        elif chars == 0:
            return failed(
                "No readable text was found in that file. Try a different export of your resume.",
                fmt,
                meta=meta,
            )
    logger.info(
        "resume_ingested",
        format=fmt,
        pages=pages,
        chars=chars,
        words=words,
        sections=len(a.sections),
        scanned=bool(doc.meta.get("scanned")),
        truncated=bool(doc.meta.get("truncated")),
    )
    return doc
