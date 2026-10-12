"""Lint: read the exported files back and check them, as a checklist (never a vendor pass/fail, never a percentage).

The check does not trust the exporter. It extracts the text of the PDF (pypdf) and the DOCX (python-docx) the way an
applicant tracking system would, and asks:

    cites_fact       every sentence of the resume cites facts that exist, and the file holds no text that is not the
                     resume (no line added after the fact)
    numbers_cited    every number in the files is in a fact the resume cites (or in its dates and contact line)
    text_layer       the PDF has a real text layer, without (cid:NNN) codes or replacement characters
    single_column    no second column (text sharing a baseline across a wide gap, a second left edge), no table, no
                     text box, no multi-column section
    standard_headings  the headings are the plain capitals the resume should have, and no others
    contact_text     the email and phone are ordinary text in the body, as written on the resume
    header_footer    no contact details in a running header or footer
    date_ranges      every date range is ASCII with both ends: "Jan 2021 - Present"
    page_count       at most two pages, and the pages the exporter reported
    round_trip       every verified sentence survives extraction (after NFC and whitespace folding)

A cover note is checked for `cites_fact`, `numbers_cited`, `length` (250 to 400 words), `openers` (no generic opener,
no puffery), `text_layer`, `single_column` and `round_trip`.

Each item says what it looked at (`detail`) and where it failed (`location`: "pdf", "docx", a bullet id).
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Final

from docx import Document
from docx.oxml.ns import qn
from pypdf import PdfReader

from tayyari.contract.match import (
    COVER_WORDS_MAX,
    COVER_WORDS_MIN,
    CoverNote,
    LintItem,
    LintReport,
    Posting,
    ResumeVariant,
)
from tayyari.match.bullets import all_bullets
from tayyari.match.cover import banned_opener, banned_phrase, refusal_suffix
from tayyari.match.export import (
    MAX_PAGES,
    SECTION_HEADINGS,
    expected_pieces,
    plain,
    resume_layout,
    sentence_texts,
)
from tayyari.match.models import FactBook
from tayyari.match.text import clip, word_count
from tayyari.match.verify import corpus_of
from tayyari.portfolio.ground import measures_of

_INVISIBLE: Final = re.compile("[​‌‍⁠﻿­]")
_SPACE: Final = re.compile(r"\s+")
_MONTH: Final = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE: Final = rf"(?:{_MONTH}\s+)?(?:19|20)\d{{2}}"
_RANGE: Final = re.compile(
    rf"(?P<a>{_DATE})(?P<sep>\s*(?:[-‐-―−]|\bto\b)\s*)(?P<b>{_DATE}|Present|Current|Now)?",
    re.IGNORECASE,
)
_ALLCAPS: Final = re.compile(r"^[A-Z][A-Z &/]{2,28}$")
_CID: Final = re.compile(r"\(cid:\d+\)")

LABELS: Final[dict[str, str]] = {
    "cites_fact": "Every sentence cites a fact",
    "numbers_cited": "Every number comes from a cited fact",
    "text_layer": "The file has a real text layer",
    "single_column": "One column, no tables or text boxes",
    "standard_headings": "Standard section headings",
    "contact_text": "Contact details are plain text",
    "header_footer": "No contact details in headers or footers",
    "date_ranges": "Dates are plain ranges with both ends",
    "page_count": "Page count",
    "round_trip": "The text survives extraction",
    "length": "Length is 250 to 400 words",
    "openers": "No generic opener or puffery",
}


# ───────────────────────── reading the files ─────────────────────────


@dataclass(slots=True)
class PdfRead:
    pages: list[str] = field(default_factory=list)
    fragments: list[list[tuple[float, float, float, str]]] = field(default_factory=list)
    """Per page: ``(x, y, font size, text)`` of each text-showing operation."""
    widths: list[float] = field(default_factory=list)
    error: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.pages)


def read_pdf(data: bytes) -> PdfRead:
    """The text of each page and where its pieces sit. An unreadable file comes back with ``error`` set."""
    out = PdfRead()
    try:
        reader = PdfReader(io.BytesIO(data))
        for page in reader.pages:
            pieces: list[tuple[float, float, float, str]] = []

            def visit(
                text: str,
                cm: list[float],
                tm: list[float],
                font: Any,
                size: float,
                sink: list[tuple[float, float, float, str]] = pieces,
            ) -> None:
                if text.strip():
                    x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                    y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                    sink.append((x, y, float(size or 0.0) or 10.0, text))

            # WinAnsiEncoding names both 0x95 and 0x7F "bullet"; ReportLab writes 0x7F, which pypdf reads as DEL.
            out.pages.append((page.extract_text(visitor_text=visit) or "").replace(chr(0x7F), chr(0x2022)))
            out.fragments.append(pieces)
            out.widths.append(float(page.mediabox.width))
    except Exception as exc:  # pypdf raises many types for a damaged file
        out.error = type(exc).__name__
    return out


@dataclass(slots=True)
class DocxRead:
    body: list[str] = field(default_factory=list)
    header_footer: list[str] = field(default_factory=list)
    tables: int = 0
    columns: int = 1
    text_boxes: int = 0
    drawings: int = 0
    error: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.body)


def read_docx(data: bytes) -> DocxRead:
    """The body paragraphs, header and footer text, and the layout features of a DOCX."""
    out = DocxRead()
    try:
        document = Document(io.BytesIO(data))
        out.body = [p.text for p in document.paragraphs]
        out.tables = len(document.tables)
        for section in document.sections:
            for part in (
                section.header,
                section.footer,
                section.first_page_header,
                section.first_page_footer,
            ):
                out.header_footer += [p.text for p in part.paragraphs if p.text.strip()]
            for cols in section._sectPr.findall(qn("w:cols")):
                out.columns = max(out.columns, int(cols.get(qn("w:num"), "1")))
        xml = document.element.xml
        out.text_boxes = xml.count("<w:txbxContent")
        out.drawings = xml.count("<w:drawing") + xml.count("<w:pict")
    except Exception as exc:
        out.error = type(exc).__name__
    return out


# ───────────────────────── folding for comparison ─────────────────────────


def flat(text: str) -> str:
    """The comparison form of extracted text: NFC, plain quotes and dashes, bullets and soft hyphens gone, one line."""
    return (
        _SPACE.sub(" ", plain(_INVISIBLE.sub("", unicodedata.normalize("NFC", text)).replace("•", " ")))
        .strip()
        .casefold()
    )


def _item(check: str, ok: bool, detail: str, location: str | None = None) -> LintItem:
    return LintItem(id=check, ok=ok, label=LABELS[check], detail=clip(detail, 240), location=location)


@dataclass(slots=True)
class _Findings:
    """What went wrong with one check, by file."""

    problems: list[tuple[str, str]] = field(default_factory=list)
    """``(location, what)``"""

    def add(self, where: str, what: str) -> None:
        self.problems.append((where, what))

    def item(self, check: str, ok_detail: str) -> LintItem:
        if not self.problems:
            return _item(check, True, ok_detail)
        where, what = self.problems[0]
        more = f" (and {len(self.problems) - 1} more)" if len(self.problems) > 1 else ""
        return _item(check, False, f"{what}{more}", where)


# ───────────────────────── the checks ─────────────────────────


def _files(pdf: PdfRead | None, docx: DocxRead | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if pdf is not None:
        out.append(("pdf", pdf.text))
    if docx is not None:
        out.append(("docx", docx.text))
    return out


def _text_layer(pdf: PdfRead | None, docx: DocxRead | None) -> LintItem:
    found = _Findings()
    if pdf is not None:
        if pdf.error:
            found.add("pdf", f"the PDF could not be read ({pdf.error})")
        elif len(pdf.text.strip()) < 80:
            found.add("pdf", "the PDF has almost no text: it may be an image of a page")
        elif _CID.search(pdf.text) or chr(0xFFFD) in pdf.text:
            found.add("pdf", "the PDF text holds (cid:NNN) codes or replacement characters")
        elif chr(0x25A0) in pdf.text:
            found.add("pdf", "a character the PDF font cannot show was replaced by a square (use the DOCX)")
    if docx is not None:
        if docx.error:
            found.add("docx", f"the DOCX could not be read ({docx.error})")
        elif len(docx.text.strip()) < 40:
            found.add("docx", "the DOCX has almost no text")
        elif "�" in docx.text:
            found.add("docx", "the DOCX text holds replacement characters")
    return found.item("text_layer", "Both files carry real text.")


def _single_column(pdf: PdfRead | None, docx: DocxRead | None) -> LintItem:
    found = _Findings()
    if pdf is not None and not pdf.error:
        for number, (pieces, width) in enumerate(zip(pdf.fragments, pdf.widths, strict=False), start=1):
            if _two_columns(pieces, width):
                found.add(f"pdf page {number}", "text sits in two columns")
    if docx is not None and not docx.error:
        if docx.tables:
            found.add("docx", f"{docx.tables} table(s): a table can scramble the reading order")
        if docx.columns > 1:
            found.add("docx", f"a section has {docx.columns} columns")
        if docx.text_boxes:
            found.add("docx", "text sits in a text box")
        if docx.drawings:
            found.add("docx", "the file holds drawings or images")
    return found.item("single_column", "One column of plain paragraphs.")


def _two_columns(pieces: Sequence[tuple[float, float, float, str]], width: float) -> bool:
    """Two columns look like text on one baseline separated by a wide gap, or two well-apart left edges."""
    if not pieces or width <= 0:
        return False
    rows: dict[int, list[tuple[float, float, str]]] = {}
    for x, y, size, text in pieces:
        rows.setdefault(round(y / 3), []).append((x, size, text))
    for row in rows.values():
        ordered = sorted(row)
        for (x1, size1, text1), (x2, _, _) in pairwise(ordered):
            end = x1 + len(text1) * size1 * 0.5
            if x2 - end > 0.12 * width:
                return True
    total = sum(len(t) for _, _, _, t in pieces) or 1
    right = sum(len(t) for x, _, _, t in pieces if x > 0.38 * width)
    left = sum(len(t) for x, _, _, t in pieces if x < 0.2 * width)
    return right / total > 0.25 and left / total > 0.25


def _standard_headings(variant: ResumeVariant, pdf: PdfRead | None, docx: DocxRead | None) -> LintItem:
    wanted = [line.text for line in resume_layout(variant) if line.kind == "heading"]
    name = flat(variant.header.name)
    found = _Findings()
    for where, text in _files(pdf, docx):
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        have = [ln for ln in lines if ln in SECTION_HEADINGS]
        for heading in wanted:
            if heading not in have:
                found.add(where, f"the heading {heading} is missing or not a plain capital line")
        for ln in lines[1:]:
            if _ALLCAPS.match(ln) and ln not in SECTION_HEADINGS and flat(ln) != name:
                found.add(where, f'"{ln}" looks like a heading but is not a standard one')
        order = [h for h in have if h in wanted]
        if order != [h for h in wanted if h in order]:
            found.add(where, "the headings are out of the usual order")
    return found.item("standard_headings", "Headings: " + ", ".join(wanted) + ".")


def _contact_text(variant: ResumeVariant, pdf: PdfRead | None, docx: DocxRead | None) -> LintItem:
    contact = variant.header.contact
    if not contact:
        return _item("contact_text", False, "No email or phone on the resume: add them before you send it.")
    found = _Findings()
    for where, text in _files(pdf, docx):
        folded = flat(text)
        for piece in contact:
            if flat(piece) not in folded:
                found.add(where, f"{clip(piece, 30)} is not plain text in the body")
    return found.item("contact_text", "Email and phone are plain text in the body.")


def _header_footer(variant: ResumeVariant, pdf: PdfRead | None, docx: DocxRead | None) -> LintItem:
    found = _Findings()
    if docx is not None and not docx.error and docx.header_footer:
        found.add("docx", f"header or footer text: {clip(docx.header_footer[0], 40)}")
    if pdf is not None and not pdf.error and len(pdf.pages) > 1:
        for number, page in enumerate(pdf.pages[1:], start=2):
            folded = flat(page)
            if any(flat(piece) in folded for piece in variant.header.contact):
                found.add(f"pdf page {number}", "contact details repeat as a running header")
    return found.item("header_footer", "Contact details appear once, in the body.")


def date_range_problems(text: str) -> list[str]:
    """Date ranges in ``text`` that are not ``Mon YYYY - Mon YYYY`` / ``YYYY - YYYY`` / ``... - Present`` in ASCII."""
    problems: list[str] = []
    for line in text.split("\n"):
        for m in _RANGE.finditer(line):
            sep = m.group("sep")
            written = clip(m.group(0).strip(), 40)
            if m.group("b") is None:
                problems.append(f'"{written}" has no end date')
            elif sep != " - ":
                problems.append(f'"{written}" is not written as "start - end" with a plain hyphen')
    return problems


def _date_ranges(pdf: PdfRead | None, docx: DocxRead | None) -> LintItem:
    found = _Findings()
    for where, text in _files(pdf, docx):
        for problem in date_range_problems(text):
            found.add(where, problem)
    return found.item("date_ranges", "Every date range reads like Jan 2021 - Present.")


def _page_count(pdf: PdfRead | None, docx: DocxRead | None, expected: int | None) -> LintItem:
    found = _Findings()
    detail = ""
    if pdf is not None and not pdf.error:
        pages = len(pdf.pages)
        detail = f"{pages} page{'s' if pages != 1 else ''}"
        if pages > MAX_PAGES:
            found.add("pdf", f"{pages} pages: a resume is {MAX_PAGES} at most")
        if expected is not None and pages != expected:
            found.add("pdf", f"{pages} pages, but the exporter made {expected}")
    if docx is not None and not docx.error:
        lines = sum(max(1, -(-len(p) // 95)) for p in docx.body if p.strip()) + sum(
            1 for p in docx.body if not p.strip()
        )
        estimate = max(1, -(-lines // 54))
        if estimate > MAX_PAGES:
            found.add("docx", f"about {estimate} pages: a resume is {MAX_PAGES} at most")
        detail = detail or f"about {estimate} page{'s' if estimate != 1 else ''} (estimated)"
    return found.item("page_count", detail or "No file to count.")


def _numbers_in(text: str) -> list[str]:
    return [m.text for m in measures_of(text, strict=True)]


def _numbers_cited(
    variant: ResumeVariant, book: FactBook, pdf: PdfRead | None, docx: DocxRead | None
) -> LintItem:
    facts = book.by_id()
    cited_ids = {fid for b in all_bullets(variant) for fid in b.factIds}
    cited_ids |= {fid for g in variant.skills for i in g.items for fid in i.factIds}
    cited_ids |= {e.entryFactId for e in (*variant.experience, *variant.projects)}
    cited_ids |= {e.factId for e in variant.education}
    dates = [e.dates or "" for e in variant.experience] + [e.dates or "" for e in variant.projects]
    dates += [e.dates or "" for e in variant.education]
    extra = "\n".join([*variant.header.contact, *dates])
    corpus, _ = corpus_of((facts[f] for f in cited_ids if f in facts), extra)
    found = _Findings()
    for where, text in _files(pdf, docx):
        seen: set[str] = set()
        for m in measures_of(text, strict=True):
            if corpus.has_measure(m) or m.text in seen:
                continue
            seen.add(m.text)
            found.add(where, f"the number {m.text} is in no cited fact")
    return found.item("numbers_cited", "Every number is in a fact the resume cites.")


def _cites_fact(
    variant: ResumeVariant, book: FactBook, pdf: PdfRead | None, docx: DocxRead | None
) -> LintItem:
    facts = book.by_id()
    found = _Findings()
    for bullet in all_bullets(variant):
        if bullet.status != "verified" or not bullet.text.strip():
            continue
        if not bullet.factIds or any(f not in facts for f in bullet.factIds):
            found.add(bullet.id, "a sentence cites no fact, or a fact that does not exist")
    for group in variant.skills:
        for skill in group.items:
            if not skill.factIds or any(f not in facts for f in skill.factIds):
                found.add(f"skill {skill.name}", "a skill cites no fact")
    pieces = sorted((flat(p) for p in expected_pieces(variant)), key=len, reverse=True)
    for where, text in _files(pdf, docx):
        rest = flat(text)
        for piece in pieces:
            if piece:
                rest = rest.replace(piece, " ", 1)
        leftover = _SPACE.sub(" ", re.sub(r"[\W_]+", " ", rest)).strip()
        if len(leftover) >= 6:
            found.add(where, f'text that is not on the resume: "{clip(leftover, 50)}"')
    return found.item("cites_fact", "Every sentence cites facts, and the files hold nothing else.")


def _round_trip(variant: ResumeVariant, pdf: PdfRead | None, docx: DocxRead | None) -> LintItem:
    found = _Findings()
    sentences = sentence_texts(variant)
    for where, text in _files(pdf, docx):
        folded = flat(text)
        missing = [s for s in sentences if flat(s) not in folded]
        if missing:
            found.add(
                where, f'{len(missing)} sentence(s) did not survive extraction, e.g. "{clip(missing[0], 50)}"'
            )
    return found.item("round_trip", f"All {len(sentences)} sentences read back exactly.")


def lint_resume(
    variant: ResumeVariant,
    book: FactBook,
    *,
    pdf: bytes | None = None,
    docx: bytes | None = None,
    expected_pages: int | None = None,
) -> LintReport:
    """Read the exported files back and check them (see the module docstring). Pass either file or both."""
    p = read_pdf(pdf) if pdf is not None else None
    d = read_docx(docx) if docx is not None else None
    return LintReport(
        items=[
            _cites_fact(variant, book, p, d),
            _numbers_cited(variant, book, p, d),
            _text_layer(p, d),
            _single_column(p, d),
            _standard_headings(variant, p, d),
            _contact_text(variant, p, d),
            _header_footer(variant, p, d),
            _date_ranges(p, d),
            _page_count(p, d, expected_pages),
            _round_trip(variant, p, d),
        ]
    )


# ───────────────────────── cover note ─────────────────────────


def lint_cover(
    note: CoverNote,
    book: FactBook,
    *,
    posting: Posting | None = None,
    pdf: bytes | None = None,
    docx: bytes | None = None,
) -> LintReport:
    """The cover note's checklist (see the module docstring); the files are optional. ``posting`` supplies the words
    of the requirements a gap sentence may repeat."""
    p = read_pdf(pdf) if pdf is not None else None
    d = read_docx(docx) if docx is not None else None
    facts = book.by_id()
    cites = _Findings()
    for number, para in enumerate(note.paragraphs, start=1):
        if para.kind in ("match", "logistics") and (
            not para.factIds or any(f not in facts for f in para.factIds)
        ):
            cites.add(f"paragraph {number}", "a paragraph that claims something cites no fact")
    cited = [facts[f] for para in note.paragraphs for f in para.factIds if f in facts]
    named = {r for para in note.paragraphs for r in para.requirementIds}
    requirement_words = " ".join(
        f"{r.text} {' '.join(r.terms)}" for r in (posting.requirements if posting else []) if r.id in named
    )
    corpus, _ = corpus_of(cited, requirement_words)
    numbers = _Findings()
    for number, para in enumerate(note.paragraphs, start=1):
        for m in measures_of(para.text, strict=True):
            if not corpus.has_measure(m):
                numbers.add(f"paragraph {number}", f"the number {m.text} is in no cited fact")
    words = sum(word_count(para.text) for para in note.paragraphs)
    length = (
        _item("length", True, f"{words} words.")
        if COVER_WORDS_MIN <= words <= COVER_WORDS_MAX
        else _item(
            "length",
            False,
            f"{words} words; the note should be {COVER_WORDS_MIN} to {COVER_WORDS_MAX}.{refusal_suffix(note)}",
        )
    )
    openers = _Findings()
    for number, para in enumerate(note.paragraphs, start=1):
        opener = banned_opener(para.text)
        phrase = banned_phrase(para.text)
        if opener or phrase:
            openers.add(f"paragraph {number}", f'"{opener or phrase}" says nothing a reader can use')
    trip = _Findings()
    for where, text in _files(p, d):
        folded = flat(text)
        missing = [para.text for para in note.paragraphs if flat(plain(para.text)) not in folded]
        if missing:
            trip.add(
                where,
                f'{len(missing)} paragraph(s) did not survive extraction, e.g. "{clip(missing[0], 50)}"',
            )
    return LintReport(
        items=[
            cites.item("cites_fact", "Every claim cites facts."),
            numbers.item("numbers_cited", "Every number is in a fact the note cites."),
            length,
            openers.item("openers", "No generic opener or puffery."),
            _text_layer(p, d),
            _single_column(p, d),
            trip.item("round_trip", f"All {len(note.paragraphs)} paragraphs read back exactly."),
        ]
    )


def failing(report: LintReport) -> list[LintItem]:
    """The items that did not pass, in report order."""
    return [item for item in report.items if not item.ok]


__all__ = [
    "LABELS",
    "DocxRead",
    "PdfRead",
    "date_range_problems",
    "failing",
    "flat",
    "lint_cover",
    "lint_resume",
    "read_docx",
    "read_pdf",
]
