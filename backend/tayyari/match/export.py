"""Files: the resume and the cover note as DOCX (python-docx) and PDF (reportlab platypus).

Both are built from the same plain layout (`resume_layout`, `cover_layout`) so they say exactly the same thing:

  - one column, no tables, no text boxes, no images, no headers or footers;
  - headings are plain capitals (SUMMARY, EXPERIENCE, PROJECTS, SKILLS, EDUCATION);
  - each bullet starts with a literal "•" in the text, not a list style;
  - dates are ASCII ranges with both ends ("Jan 2021 - Present");
  - contact details are ordinary text in the body;
  - the PDF uses Helvetica only, real text, the document title set.

Text is folded to what a PDF's Helvetica can show (`plain`: straight quotes, plain dashes, ASCII ellipsis); the same
fold is applied to the DOCX, and the lint applies it to the sentences it looks for, so the two files and the check
agree. A resume that still has a question open (`bullets.export_blockers`) is not exported.
"""

from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]  # reportlab ships no type stubs
from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer  # type: ignore[import-untyped]

from tayyari.contract.match import Bullet, CoverNote, ResumeVariant
from tayyari.match.bullets import all_bullets, export_blockers
from tayyari.match.errors import ExportBlocked

BULLET: Final = "•"
SECTION_HEADINGS: Final = ("SUMMARY", "EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION")
MAX_PAGES: Final = 2
LineKind = Literal["name", "contact", "heading", "entry", "bullet", "text", "skills", "greeting", "closing"]

_TRANSLATE: Final = str.maketrans(
    {
        0x2018: "'",
        0x2019: "'",
        0x201A: "'",
        0x201C: '"',
        0x201D: '"',
        0x201E: '"',
        0x2013: "-",
        0x2014: "-",
        0x2212: "-",
        0x2010: "-",
        0x2011: "-",
        0x00A0: " ",
        0x2009: " ",
        0x202F: " ",
        0x2026: "...",
        0x00B7: "|",
        0x2192: "->",
        0x00D7: "x",
        0x20B9: "Rs ",
        0x2713: "",
        0x200B: "",
        0x00AD: "",
    }
)


def plain(text: str) -> str:
    """``text`` as the files carry it: NFC, straight quotes, plain dashes, ASCII ellipsis, one line, no soft hyphens.

    A character Helvetica cannot show (outside Latin-1 after this fold) is kept as it is: the PDF lint then names it.
    """
    return " ".join(unicodedata.normalize("NFC", text).translate(_TRANSLATE).split())


@dataclass(frozen=True, slots=True)
class Line:
    """One line of the layout: what it is and the text it carries (already `plain`)."""

    kind: LineKind
    text: str


def _entry_line(title: str, org: str | None, dates: str | None) -> str:
    head = f"{title}, {org}" if org else title
    return plain(f"{head} | {dates}" if dates else head)


def resume_layout(variant: ResumeVariant) -> list[Line]:
    """The resume as lines: name, contact, then each section whose content has survived (dropped bullets are gone)."""
    lines: list[Line] = [Line("name", plain(variant.header.name))]
    if variant.header.contact:
        lines.append(Line("contact", plain(" | ".join(variant.header.contact))))
    summary = variant.summary
    if summary.status == "verified" and summary.text.strip():
        lines += [Line("heading", "SUMMARY"), Line("text", plain(summary.text))]

    def entries(heading: str, items: list[tuple[str, str | None, str | None, list[str]]]) -> None:
        if not items:
            return
        lines.append(Line("heading", heading))
        for title, org, dates, bullets in items:
            lines.append(Line("entry", _entry_line(title, org, dates)))
            lines.extend(Line("bullet", f"{BULLET} {plain(b)}") for b in bullets)

    def verified(entry_bullets: list[Bullet]) -> list[str]:
        return [b.text for b in entry_bullets if b.status == "verified" and b.text.strip()]

    entries("EXPERIENCE", [(e.title, e.org, e.dates, verified(e.bullets)) for e in variant.experience])
    entries("PROJECTS", [(e.title, e.org, e.dates, verified(e.bullets)) for e in variant.projects])
    if variant.skills:
        lines.append(Line("heading", "SKILLS"))
        for group in variant.skills:
            names = ", ".join(plain(i.name) for i in group.items)
            lines.append(Line("skills", f"{plain(group.group)}: {names}"))
    if variant.education:
        lines.append(Line("heading", "EDUCATION"))
        lines += [Line("entry", _entry_line(e.title, e.org, e.dates)) for e in variant.education]
    return lines


def cover_layout(note: CoverNote, *, name: str, contact: list[str], company: str | None = None) -> list[Line]:
    """The cover note as lines: sender, greeting, the paragraphs, sign-off."""
    lines: list[Line] = [Line("name", plain(name))]
    if contact:
        lines.append(Line("contact", plain(" | ".join(contact))))
    greeting = f"Dear {plain(company)} hiring team," if company else "Dear hiring team,"
    lines.append(Line("greeting", greeting))
    lines += [Line("text", plain(p.text)) for p in note.paragraphs]
    lines += [Line("closing", "Sincerely,"), Line("closing", plain(name))]
    return lines


# ───────────────────────── DOCX ─────────────────────────


def _docx(lines: list[Line], *, title: str, author: str) -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21.0)
    section.left_margin = section.right_margin = Cm(1.9)
    section.top_margin = section.bottom_margin = Cm(1.7)
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)
    doc.core_properties.title = title
    doc.core_properties.author = author
    doc.core_properties.subject = "Application document"
    for line in lines:
        para = doc.add_paragraph()
        fmt = para.paragraph_format
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(3)
        run = para.add_run(line.text)
        if line.kind == "name":
            run.bold = True
            run.font.size = Pt(16)
            fmt.space_after = Pt(2)
        elif line.kind == "heading":
            run.bold = True
            run.font.size = Pt(10.5)
            fmt.space_before = Pt(9)
            fmt.space_after = Pt(3)
        elif line.kind == "entry":
            run.bold = True
            fmt.space_before = Pt(4)
            fmt.space_after = Pt(1)
        elif line.kind == "bullet":
            fmt.left_indent = Cm(0.55)
            fmt.first_line_indent = Cm(-0.4)
            fmt.space_after = Pt(1.5)
        elif line.kind == "greeting":
            fmt.space_before = Pt(14)
            fmt.space_after = Pt(8)
        elif line.kind == "text":
            fmt.space_after = Pt(7)
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        elif line.kind == "closing":
            fmt.space_before = Pt(6)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# ───────────────────────── PDF ─────────────────────────


def _styles(base: float = 9.6) -> dict[str, ParagraphStyle]:
    lead = base * 1.28
    common = {"fontName": "Helvetica", "fontSize": base, "leading": lead, "textColor": "#000000"}
    return {
        "name": ParagraphStyle(
            "name", **{**common, "fontName": "Helvetica-Bold", "fontSize": 16, "leading": 19, "spaceAfter": 2}
        ),
        "contact": ParagraphStyle("contact", **{**common, "spaceAfter": 2}),
        "heading": ParagraphStyle(
            "heading",
            **{
                **common,
                "fontName": "Helvetica-Bold",
                "fontSize": 10.2,
                "leading": 12.5,
                "spaceBefore": 9,
                "spaceAfter": 3,
            },
        ),
        "entry": ParagraphStyle(
            "entry", **{**common, "fontName": "Helvetica-Bold", "spaceBefore": 4, "spaceAfter": 1}
        ),
        "bullet": ParagraphStyle(
            "bullet", **{**common, "leftIndent": 12, "firstLineIndent": -9, "spaceAfter": 1.6}
        ),
        "text": ParagraphStyle("text", **{**common, "spaceAfter": 6}),
        "skills": ParagraphStyle("skills", **{**common, "spaceAfter": 1.6}),
        "greeting": ParagraphStyle("greeting", **{**common, "spaceBefore": 12, "spaceAfter": 7}),
        "closing": ParagraphStyle("closing", **{**common, "spaceBefore": 4}),
    }


def _pdf(lines: list[Line], *, title: str, author: str, base: float = 9.6) -> bytes:
    styles = _styles(base)
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=19 * mm,
        rightMargin=19 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
        author=author,
        subject="Application document",
        creator="Leafout",
        invariant=1,
    )
    story: list[Paragraph | Spacer] = [Paragraph(escape(line.text), styles[line.kind]) for line in lines]
    doc.build(story)
    return out.getvalue()


# ───────────────────────── public ─────────────────────────


@dataclass(frozen=True, slots=True)
class ExportedFiles:
    pdf: bytes
    docx: bytes
    pages: int
    """Pages of the PDF."""


def _pages(pdf: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(pdf)).pages)


def _build(lines: list[Line], *, title: str, author: str) -> ExportedFiles:
    pdf = _pdf(lines, title=title, author=author)
    pages = _pages(pdf)
    if pages > MAX_PAGES:  # one more try, a little tighter, before the lint reports the length
        tighter = _pdf(lines, title=title, author=author, base=8.8)
        if _pages(tighter) < pages:
            pdf, pages = tighter, _pages(tighter)
    return ExportedFiles(pdf=pdf, docx=_docx(lines, title=title, author=author), pages=pages)


def export_resume(variant: ResumeVariant) -> ExportedFiles:
    """The resume as a PDF and a DOCX. Raises `ExportBlocked` while any bullet still needs the person."""
    blockers = export_blockers(variant)
    if blockers:
        raise ExportBlocked(detail="; ".join(blockers[:6]))
    name = plain(variant.header.name)
    return _build(resume_layout(variant), title=f"{name} - Resume", author=name)


def export_cover(
    note: CoverNote, *, name: str, contact: list[str], company: str | None = None
) -> ExportedFiles:
    """The cover note as a PDF and a DOCX."""
    who = plain(name)
    return _build(
        cover_layout(note, name=name, contact=contact, company=company),
        title=f"{who} - Cover note",
        author=who,
    )


def expected_pieces(variant: ResumeVariant) -> list[str]:
    """Every piece of text the resume files must contain, in reading order (the lint looks each one up)."""
    return [line.text for line in resume_layout(variant)]


def sentence_texts(variant: ResumeVariant) -> list[str]:
    """The verified sentences of the resume as the files carry them (the summary and each bullet, without the "•")."""
    return [plain(b.text) for b in all_bullets(variant) if b.status == "verified" and b.text.strip()]


__all__ = [
    "BULLET",
    "MAX_PAGES",
    "SECTION_HEADINGS",
    "ExportedFiles",
    "Line",
    "cover_layout",
    "expected_pieces",
    "export_cover",
    "export_resume",
    "plain",
    "resume_layout",
    "sentence_texts",
]
