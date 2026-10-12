"""Export and lint: both personas round-trip, and every lint item has a known-bad file that makes it fire."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from tayyari.contract.match import Bullet, CoverNote, CoverParagraph, ResumeVariant
from tayyari.match.export import BULLET, SECTION_HEADINGS, export_resume, plain, resume_layout
from tayyari.match.lint import (
    date_range_problems,
    failing,
    flat,
    lint_cover,
    lint_resume,
    read_docx,
    read_pdf,
)
from tayyari.match.models import FactBook
from tayyari.match.service import MatchService
from tests.fakes.claude import FakeClaudeClient
from tests.unit.match.support import CTX, HAIKU, Persona, meera, riya, script_pipeline

CHECKS = [
    "cites_fact",
    "numbers_cited",
    "text_layer",
    "single_column",
    "standard_headings",
    "contact_text",
    "header_footer",
    "date_ranges",
    "page_count",
    "round_trip",
]


async def built(persona: Persona) -> tuple[MatchService, FactBook, ResumeVariant, Any]:
    fake = script_pipeline(FakeClaudeClient(), persona)
    service = MatchService(claude=fake, model=HAIKU)
    outcome = await service.analyse(
        profile=persona.profile,
        context=persona.context,
        posting_text=persona.posting_text,
        call_ctx=CTX,
        portfolio_facts=persona.portfolio_facts,
    )
    composed = await service.compose_resume(
        book=outcome.book, posting=outcome.posting, result=outcome.result, call_ctx=CTX
    )
    exported = await service.export_resume(composed.variant, outcome.book)
    return service, outcome.book, composed.variant, exported


def edit_docx(data: bytes, change: Callable[[Any], None]) -> bytes:
    document = Document(io.BytesIO(data))
    change(document)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def replace_in_docx(data: bytes, old: str, new: str) -> bytes:
    def change(document: Any) -> None:
        hit = False
        for paragraph in document.paragraphs:
            if old in paragraph.text:
                paragraph.text = paragraph.text.replace(old, new)
                hit = True
        assert hit, old

    return edit_docx(data, change)


def pdf_of(draw: Callable[[canvas.Canvas], None]) -> bytes:
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=(595, 842), invariant=1)
    draw(page)
    page.save()
    return out.getvalue()


def item(report: Any, check: str) -> Any:
    return next(i for i in report.items if i.id == check)


# ───────────────────────── the real files ─────────────────────────


@pytest.mark.parametrize("persona", [meera(), riya()], ids=["meera", "riya"])
async def test_the_exported_resume_round_trips_and_passes_every_check(persona: Persona) -> None:
    _, _, variant, exported = await built(persona)
    report = exported.variant.lint
    assert report is not None
    assert [i.id for i in report.items] == CHECKS
    assert failing(report) == [], [(i.id, i.detail, i.location) for i in failing(report)]
    assert exported.files.pages <= 2
    assert variant.lint is None  # the lint is attached to the exported copy


@pytest.mark.parametrize("persona", [meera(), riya()], ids=["meera", "riya"])
async def test_the_files_are_single_column_plain_text_with_a_literal_bullet(persona: Persona) -> None:
    _, _, variant, exported = await built(persona)
    pdf = PdfReader(io.BytesIO(exported.files.pdf))
    assert pdf.metadata is not None
    assert pdf.metadata.title == f"{persona.profile.person.name} - Resume"
    fonts = {
        str(f.get_object().get("/BaseFont"))
        for page in pdf.pages
        for f in page["/Resources"]["/Font"].values()
    }
    assert fonts <= {"/Helvetica", "/Helvetica-Bold"}  # Helvetica only
    pdf_text = read_pdf(exported.files.pdf)
    assert pdf_text.error is None and len(pdf_text.text) > 200

    docx = read_docx(exported.files.docx)
    assert docx.tables == 0 and docx.columns == 1 and docx.text_boxes == 0 and docx.drawings == 0
    assert docx.header_footer == []
    bullets = [p for p in docx.body if p.startswith(BULLET + " ")]
    assert bullets, "every bullet carries a literal bullet character"
    headings = [p for p in docx.body if p in SECTION_HEADINGS]
    assert headings[0] == "SUMMARY" or headings[0] == "EXPERIENCE"
    assert "EXPERIENCE" in headings
    core = Document(io.BytesIO(exported.files.docx)).core_properties
    assert core.title == f"{persona.profile.person.name} - Resume"
    layout = resume_layout(variant)
    assert layout[0].kind == "name"
    assert layout[1].text == " | ".join(variant.header.contact)


async def test_dates_in_the_files_are_ascii_ranges_with_both_ends() -> None:
    _, _, _, exported = await built(meera())
    text = read_docx(exported.files.docx).text
    assert "Mar 2023 - Present" in text
    assert "Jun 2020 - Feb 2023" in text
    assert text.isascii() or "|" in text
    assert date_range_problems(text) == []


def test_text_is_folded_to_what_helvetica_can_show() -> None:
    assert plain("“quoted” – it’s done… ₹3.2 crore") == '"quoted" - it\'s done... Rs 3.2 crore'
    assert plain("a\n  b") == "a b"


# ───────────────────────── one known-bad file per item ─────────────────────────


async def test_numbers_cited_fires_on_a_number_no_fact_has() -> None:
    _, book, variant, exported = await built(meera())
    bad = replace_in_docx(
        exported.files.docx, "added 6.4% to site conversion", "added 340% to site conversion"
    )
    report = lint_resume(variant, book, docx=bad)
    assert item(report, "numbers_cited").ok is False
    assert "340%" in item(report, "numbers_cited").detail
    assert item(report, "numbers_cited").location == "docx"
    assert item(lint_resume(variant, book, docx=exported.files.docx), "numbers_cited").ok


async def test_cites_fact_fires_on_a_bullet_with_no_fact_and_on_text_nobody_cited() -> None:
    _, book, variant, exported = await built(meera())
    experience = variant.experience[0]
    uncited = Bullet(
        id="b-x",
        text="Visionary leader of everything.",
        factIds=[],
        verdict="exact",
        placeholders=[],
        status="verified",
    )
    broken = variant.model_copy(
        update={
            "experience": [
                experience.model_copy(update={"bullets": [*experience.bullets, uncited]}),
                *variant.experience[1:],
            ]
        }
    )
    report = lint_resume(broken, book)
    assert item(report, "cites_fact").ok is False
    assert item(report, "cites_fact").location == "b-x"

    # text added to the file after the fact: a paragraph that is not on the resume
    def add(document: Any) -> None:
        document.add_paragraph("Visionary thought leader and rockstar.")

    bad = edit_docx(exported.files.docx, add)
    flagged = item(lint_resume(variant, book, docx=bad), "cites_fact")
    assert flagged.ok is False
    assert "not on the resume" in flagged.detail
    assert item(lint_resume(variant, book, docx=exported.files.docx), "cites_fact").ok


async def test_text_layer_fires_on_an_empty_pdf_codes_and_glyphs_the_font_cannot_show() -> None:
    _, book, variant, _ = await built(meera())
    blank = pdf_of(lambda c: c.showPage())
    assert item(lint_resume(variant, book, pdf=blank), "text_layer").ok is False

    def codes(c: canvas.Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(40, 800, "(cid:12)(cid:45)(cid:78)(cid:3) " * 8)
        c.showPage()

    flagged = item(lint_resume(variant, book, pdf=pdf_of(codes)), "text_layer")
    assert flagged.ok is False and "cid" in flagged.detail

    def devanagari(c: canvas.Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(40, 800, "Meera Nair मीरा " + "plain words " * 12)
        c.showPage()

    shown = item(lint_resume(variant, book, pdf=pdf_of(devanagari)), "text_layer")
    assert shown.ok is False and "square" in shown.detail

    assert item(lint_resume(variant, book, docx=b"not a docx at all"), "text_layer").ok is False


async def test_single_column_fires_on_two_columns_a_table_or_a_columned_section() -> None:
    _, book, variant, exported = await built(meera())

    def two_columns(c: canvas.Canvas) -> None:
        c.setFont("Helvetica", 10)
        for i in range(30):
            c.drawString(40, 800 - i * 14, f"Left column line {i} with several words of text")
            c.drawString(320, 800 - i * 14, f"Right column line {i} with several words of text")
        c.showPage()

    pdf = item(lint_resume(variant, book, pdf=pdf_of(two_columns)), "single_column")
    assert pdf.ok is False and "two columns" in pdf.detail and pdf.location == "pdf page 1"

    def add_table(document: Any) -> None:
        document.add_table(rows=2, cols=2)

    assert (
        item(lint_resume(variant, book, docx=edit_docx(exported.files.docx, add_table)), "single_column").ok
        is False
    )

    def add_columns(document: Any) -> None:
        sect = document.sections[0]._sectPr
        cols = OxmlElement("w:cols")
        cols.set(qn("w:num"), "2")
        sect.append(cols)

    flagged = item(
        lint_resume(variant, book, docx=edit_docx(exported.files.docx, add_columns)), "single_column"
    )
    assert flagged.ok is False and "2 columns" in flagged.detail
    assert item(
        lint_resume(variant, book, docx=exported.files.docx, pdf=exported.files.pdf), "single_column"
    ).ok


async def test_standard_headings_fires_on_a_creative_heading_and_on_an_extra_capital_one() -> None:
    _, book, variant, exported = await built(meera())
    creative = replace_in_docx(exported.files.docx, "EXPERIENCE", "Where I Have Been")
    flagged = item(lint_resume(variant, book, docx=creative), "standard_headings")
    assert flagged.ok is False and "EXPERIENCE" in flagged.detail

    def extra(document: Any) -> None:
        document.add_paragraph("WHAT I BRING")

    odd = item(lint_resume(variant, book, docx=edit_docx(exported.files.docx, extra)), "standard_headings")
    assert odd.ok is False and "WHAT I BRING" in odd.detail

    def reorder_pdf(c: canvas.Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(40, 800, "Meera Nair")
        for i, h in enumerate(("EDUCATION", "SKILLS", "EXPERIENCE", "SUMMARY")):
            c.drawString(40, 780 - i * 20, h)
        c.showPage()

    assert item(lint_resume(variant, book, pdf=pdf_of(reorder_pdf)), "standard_headings").ok is False


async def test_contact_text_fires_when_the_contact_is_not_plain_text_in_the_body() -> None:
    _, book, variant, exported = await built(meera())
    no_contact = export_resume(
        variant.model_copy(update={"header": variant.header.model_copy(update={"contact": []})})
    )
    flagged = item(lint_resume(variant, book, pdf=no_contact.pdf, docx=no_contact.docx), "contact_text")
    assert flagged.ok is False and "plain text in the body" in flagged.detail

    nobody = variant.model_copy(update={"header": variant.header.model_copy(update={"contact": []})})
    assert item(lint_resume(nobody, book), "contact_text").ok is False  # no email or phone at all

    def into_header(document: Any) -> None:
        for p in document.paragraphs:
            if "@" in p.text:
                document.sections[0].header.paragraphs[0].text = p.text
                p.text = ""

    moved = edit_docx(exported.files.docx, into_header)
    report = lint_resume(variant, book, docx=moved)
    assert item(report, "contact_text").ok is False
    assert item(report, "header_footer").ok is False


async def test_header_footer_fires_on_a_running_header_and_on_contact_repeated_on_page_two() -> None:
    _, book, variant, exported = await built(meera())

    def footer(document: Any) -> None:
        document.sections[0].footer.paragraphs[0].text = "Page 1 of 1"

    assert (
        item(lint_resume(variant, book, docx=edit_docx(exported.files.docx, footer)), "header_footer").ok
        is False
    )

    email = variant.header.contact[0]

    def two_pages(c: canvas.Canvas) -> None:
        c.setFont("Helvetica", 10)
        for _ in range(2):
            c.drawString(40, 800, f"Meera Nair | {email}")
            c.showPage()

    flagged = item(lint_resume(variant, book, pdf=pdf_of(two_pages)), "header_footer")
    assert flagged.ok is False and flagged.location == "pdf page 2"


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("Mar 2023 – Present", "plain hyphen"),  # en dash
        ("Mar 2023 — Present", "plain hyphen"),  # em dash
        ("Mar 2023 to Present", "plain hyphen"),
        ("Mar 2023-Present", "plain hyphen"),
        ("Mar 2023 -", "no end date"),
        ("2018 -", "no end date"),
        ("Jan 2021 -  ", "no end date"),
    ],
)
def test_date_range_problems_name_each_way_a_range_goes_wrong(line: str, fragment: str) -> None:
    problems = date_range_problems(f"Growth Manager, Acme | {line}")
    assert problems and fragment in problems[0]


@pytest.mark.parametrize(
    "line", ["Mar 2023 - Present", "2015 - 2018", "Jun 2020 - Feb 2023", "Jan 2021", "May 2022 - Current"]
)
def test_good_ranges_pass(line: str) -> None:
    assert date_range_problems(f"Analyst | {line}") == []


async def test_date_ranges_fires_on_an_en_dash_and_on_a_missing_end() -> None:
    _, book, variant, exported = await built(meera())
    en_dash = replace_in_docx(exported.files.docx, "Mar 2023 - Present", "Mar 2023 – Present")
    assert item(lint_resume(variant, book, docx=en_dash), "date_ranges").ok is False
    no_end = replace_in_docx(exported.files.docx, "Jun 2020 - Feb 2023", "Jun 2020 -")
    flagged = item(lint_resume(variant, book, docx=no_end), "date_ranges")
    assert flagged.ok is False and "no end date" in flagged.detail


async def test_page_count_fires_on_three_pages_and_on_a_page_the_exporter_did_not_make() -> None:
    _, book, variant, exported = await built(meera())

    def three_pages(c: canvas.Canvas) -> None:
        c.setFont("Helvetica", 10)
        for n in range(3):
            c.drawString(40, 800, f"Page {n + 1} of a very long resume")
            c.showPage()

    flagged = item(lint_resume(variant, book, pdf=pdf_of(three_pages)), "page_count")
    assert flagged.ok is False and "3 pages" in flagged.detail
    mismatch = item(lint_resume(variant, book, pdf=exported.files.pdf, expected_pages=2), "page_count")
    assert mismatch.ok is False and "exporter made 2" in mismatch.detail
    assert item(
        lint_resume(variant, book, pdf=exported.files.pdf, expected_pages=exported.files.pages), "page_count"
    ).ok

    def many_lines(document: Any) -> None:
        for n in range(200):
            document.add_paragraph(f"line {n}")

    long_docx = item(
        lint_resume(variant, book, docx=edit_docx(exported.files.docx, many_lines)), "page_count"
    )
    assert long_docx.ok is False


async def test_round_trip_fires_when_a_verified_sentence_is_not_what_the_file_says() -> None:
    _, book, variant, exported = await built(meera())
    bad = replace_in_docx(
        exported.files.docx,
        "Ran 38 A/B tests on landing pages and checkout nudges; five winners shipped and added 6.4% to site conversion.",
        "Ran A/B tests.",
    )
    flagged = item(lint_resume(variant, book, docx=bad), "round_trip")
    assert flagged.ok is False and "did not survive extraction" in flagged.detail
    assert item(lint_resume(variant, book, docx=exported.files.docx, pdf=exported.files.pdf), "round_trip").ok


def test_flat_folds_nfc_whitespace_and_bullets_the_same_way_for_both_sides() -> None:
    assert flat("Résumé  • line\none") == flat("Résumé line one")
    assert flat("A–B “C”") == flat('A-B "C"')


# ───────────────────────── the cover note's checklist ─────────────────────────


def note_of(*pairs: tuple[str, str, list[str]]) -> CoverNote:
    paragraphs = [CoverParagraph(text=t, requirementIds=[], factIds=f, kind=k) for k, t, f in pairs]  # type: ignore[arg-type]
    return CoverNote(paragraphs=paragraphs, wordCount=sum(len(p.text.split()) for p in paragraphs))


async def test_cover_lint_fires_for_length_openers_numbers_and_missing_citations() -> None:
    _, book, _, _ = await built(meera())
    short = note_of(
        ("match", "I led the lifecycle programme on CleverTap for 400,000 subscribers.", ["exp-1.1"])
    )
    report = lint_cover(short, book)
    assert [i.id for i in failing(report)] == ["length"]

    opener = note_of(("match", "I am writing to apply for this role. I led the programme.", ["exp-1.1"]))
    assert item(lint_cover(opener, book), "openers").ok is False

    invented = note_of(("match", "I grew revenue 900% at Brightwell Foods.", ["exp-1"]))
    assert item(lint_cover(invented, book), "numbers_cited").ok is False

    uncited = note_of(("match", "I led the lifecycle programme.", []))
    assert item(lint_cover(uncited, book), "cites_fact").ok is False
    ghost = note_of(("match", "I led the lifecycle programme.", ["no-such-fact"]))
    assert item(lint_cover(ghost, book), "cites_fact").ok is False

    # a gap sentence makes no claim, so it needs no fact; the requirement's own words are allowed
    gap = CoverNote(
        paragraphs=[
            CoverParagraph(
                text="I have not yet worked with Segment.", requirementIds=[], factIds=[], kind="gap"
            )
        ],
        wordCount=7,
    )
    assert item(lint_cover(gap, book), "cites_fact").ok


def test_the_checks_are_a_fixed_list_in_a_fixed_order() -> None:
    assert len(set(CHECKS)) == len(CHECKS) == 10
