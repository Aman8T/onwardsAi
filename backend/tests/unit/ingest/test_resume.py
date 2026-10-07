"""Resume parsing: generated DOCX and PDF files, the scanned-PDF path, and hostile documents.

PDFs are built by hand (`make_pdf`) and DOCX files by python-docx, so the real parsers run on real file
formats. Nothing here needs the network.
"""

from __future__ import annotations

import io
import time
import tracemalloc
import zipfile
import zlib
from pathlib import Path

import pytest

from tayyari.core import limits
from tayyari.ingest import resume as resume_module
from tayyari.ingest.resume import (
    MAX_DOCX_UNZIPPED,
    MAX_PDF_PAGES,
    MAX_RESUME_CHARS,
    MIN_TEXT_CHARS,
    ResumeError,
    clean_text,
    detect_sections,
    ingest_resume,
    parse_resume,
)
from tests.unit.ingest.helpers import (
    add_zip_member,
    encrypt_pdf,
    make_docx,
    make_pdf,
    make_pdf_with_streams,
    replace_zip_member,
)

RESUME_LINES = [
    "Riya Kapoor",
    "Frontend Engineer",
    "riya@example.com | +91 98765 43210 | Bengaluru",
    "Summary",
    "Frontend engineer with five years of experience building design systems and accessible interfaces.",
    "Experience",
    "Kitebird - Senior Frontend Engineer (2021 - present)",
    "Led the migration of the checkout to React and TypeScript and cut load time by 40 percent.",
    "Mentored two interns and ran weekly design reviews.",
    "Projects",
    "motion-kit - open source spring animation library with 212 stars.",
    "Skills",
    "React, TypeScript, Node.js, GraphQL, Storybook, Figma, Accessibility",
    "Education",
    "B.Tech in Computer Science, 2019",
]
SECTIONS = ["Summary", "Experience", "Projects", "Skills", "Education"]


async def read(data: bytes, name: str | None) -> tuple[object, list[str]]:
    lines: list[str] = []
    doc = await ingest_resume(data, name, lines.append)
    return doc, lines


def padded_pdf(characters: int) -> bytes:
    """A one-page PDF whose extracted text is exactly `characters` characters long."""
    return make_pdf([["a" * characters]])


# ----------------------------------------------------------------------------- DOCX


async def test_a_docx_is_read_in_document_order_with_sections_and_skills() -> None:
    docx_bytes = make_docx(RESUME_LINES[3:], headings=RESUME_LINES[:3])
    doc, lines = await read(docx_bytes, "Riya_Resume.docx")
    assert doc.ok is True
    assert doc.error is None
    assert doc.text.startswith("Riya Kapoor\nFrontend Engineer\nriya@example.com")
    assert doc.text.index("Experience") < doc.text.index("Projects") < doc.text.index("Skills")
    assert doc.kind_label == "Resume · DOCX"
    assert doc.src == "Riya_Resume.docx"
    assert doc.meta["format"] == "DOCX"
    assert doc.meta["sections"] == SECTIONS
    assert doc.meta["words"] == len(doc.text.split())
    assert doc.meta["pages"] >= 1
    assert "scanned" not in doc.meta
    assert doc.pdf_bytes is None
    assert len(doc.quick_signals) == 8
    assert {"React", "TypeScript"} <= set(doc.quick_signals)
    assert lines[0] == f"Read {doc.meta['words']:,} words from the document"
    assert lines[1] == "Found sections: Summary, Experience, Projects, Skills, Education"
    assert lines[2].startswith("Spotted 8 skills: ")


async def test_docx_tables_are_read_cell_by_cell_and_merged_cells_once() -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("Experience")
    grid = document.add_table(rows=2, cols=2)
    merged = grid.cell(0, 0).merge(grid.cell(0, 1))
    merged.text = "Kitebird (merged header)"
    grid.cell(1, 0).text = "Role: Engineer"
    grid.cell(1, 1).text = "Dates: 2021-2024"
    buffer = io.BytesIO()
    document.save(buffer)
    doc, _ = await read(buffer.getvalue(), "cv.docx")
    assert doc.ok is True
    assert doc.text == "Experience\nKitebird (merged header)\nRole: Engineer\nDates: 2021-2024"


async def test_an_empty_docx_is_refused_with_a_helpful_message() -> None:
    doc, _ = await read(make_docx(), "empty.docx")
    assert doc.ok is False
    assert doc.error == "No readable text was found in that file. Try a different export of your resume."
    assert doc.kind_label == "Resume · DOCX"


async def test_a_short_docx_is_kept_as_is_and_is_not_treated_as_scanned() -> None:
    doc, _ = await read(make_docx(["Just a few words."]), "short.docx")
    assert doc.ok is True
    assert doc.text == "Just a few words."
    assert "scanned" not in doc.meta
    assert doc.pdf_bytes is None


@pytest.mark.parametrize(
    "data",
    [b"PK\x03\x04 this is not really a zip", b"plain text pretending", b"", b"PK"],
    ids=["bad-zip", "text", "empty", "stub"],
)
async def test_a_broken_docx_gets_a_clear_message(data: bytes) -> None:
    doc, _ = await read(data, "broken.docx")
    assert doc.ok is False
    expected = (
        "That file is empty. Please upload your resume again."
        if not data
        else "Could not read that DOCX file."
    )
    assert doc.error.startswith(expected)


async def test_a_zip_that_is_not_a_docx_is_unreadable() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("hello.txt", "hello")
    doc, _ = await read(buffer.getvalue(), "fake.docx")
    assert doc.ok is False
    assert doc.error.startswith("Could not read that DOCX file.")


# ----------------------------------------------------------------------------- DOCX zip bombs and XML tricks


async def test_a_docx_that_unpacks_past_the_limit_is_refused_without_unpacking_it() -> None:
    bomb = add_zip_member(
        make_docx(["x" * 300]), "word/media/payload.bin", b"\0" * (MAX_DOCX_UNZIPPED + 1024 * 1024)
    )
    assert len(bomb) < 200_000  # about 31 MB of zeros in a file of under 40 KB
    started = time.perf_counter()
    doc, _ = await read(bomb, "bomb.docx")
    assert time.perf_counter() - started < 10
    assert doc.ok is False
    assert doc.error == "That DOCX file is too large to read here. Export it as a PDF and upload it again."
    assert doc.kind_label == "Resume · DOCX"


async def test_a_large_but_legitimate_docx_below_the_limit_is_still_read() -> None:
    big = add_zip_member(
        make_docx(["Experience", "Built things with React and TypeScript. " * 10]),
        "word/media/photo.bin",
        b"\0" * (12 << 20),
    )
    doc, _ = await read(big, "with-photo.docx")
    assert doc.ok is True
    assert "Built things with React" in doc.text


async def test_the_zip_bomb_limit_matches_core() -> None:
    assert MAX_DOCX_UNZIPPED == limits.RESUME_MAX_DOCX_UNZIPPED_BYTES == 30 * 1024 * 1024


def _document_xml(body: str, doctype: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"{doctype}"
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()


async def test_an_xml_entity_bomb_inside_a_docx_does_not_expand() -> None:
    entities = ['<!ENTITY e0 "lol">'] + [f'<!ENTITY e{i} "{f"&e{i - 1};" * 10}">' for i in range(1, 10)]
    xml = _document_xml(
        "<w:p><w:r><w:t>Experience &e9; </w:t></w:r></w:p>", f"<!DOCTYPE bomb [{''.join(entities)}]>"
    )
    docx_bytes = replace_zip_member(make_docx(["placeholder"]), "word/document.xml", xml)
    started = time.perf_counter()
    doc, _ = await read(docx_bytes, "billion-laughs.docx")
    assert time.perf_counter() - started < 10  # a billion "lol"s would take minutes and gigabytes
    assert len(doc.text) < 1000  # the entity was not expanded into the text


async def test_an_external_entity_does_not_read_local_files(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET-LOCAL-FILE-CONTENT", encoding="utf-8")
    xml = _document_xml(
        "<w:p><w:r><w:t>Experience &xxe; at Kitebird</w:t></w:r></w:p>",
        f'<!DOCTYPE doc [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]>',
    )
    docx_bytes = replace_zip_member(make_docx(["placeholder"]), "word/document.xml", xml)
    doc, _ = await read(docx_bytes, "xxe.docx")
    assert "SECRET-LOCAL-FILE-CONTENT" not in repr(doc)


# ----------------------------------------------------------------------------- PDF


async def test_a_pdf_is_read_page_by_page_with_sections_and_skills() -> None:
    pdf = make_pdf([RESUME_LINES[:9], RESUME_LINES[9:]])
    doc, lines = await read(pdf, "Riya_Resume.pdf")
    assert doc.ok is True
    assert doc.error is None
    assert doc.text.startswith("Riya Kapoor\nFrontend Engineer\nriya@example.com")
    assert "B.Tech in Computer Science, 2019" in doc.text
    assert doc.kind_label == "Resume · PDF"
    assert doc.meta["format"] == "PDF"
    assert doc.meta["pages"] == 2
    assert doc.meta["sections"] == SECTIONS
    assert "scanned" not in doc.meta
    assert doc.pdf_bytes is None
    assert {"React", "TypeScript"} <= set(doc.quick_signals)
    assert lines[0] == "Parsed 2 pages"
    assert lines[1] == "Found sections: Summary, Experience, Projects, Skills, Education"
    assert doc.sub == "2 pages"


async def test_parentheses_and_backslashes_in_pdf_text_survive() -> None:
    line = "Rate (per hour) was 40 and the path was C:" + chr(92) + "work " + "x" * 200
    pdf = make_pdf([[line]])
    text = await parse_resume(pdf, "a.pdf")
    assert text == line


async def test_the_page_limit_applies_to_reading_but_not_to_counting() -> None:
    pdf = make_pdf([[f"PAGE-{n}-MARKER of the document"] for n in range(1, 71)])
    text = await parse_resume(pdf, "long.pdf")
    assert f"PAGE-{MAX_PDF_PAGES}-MARKER" in text
    assert f"PAGE-{MAX_PDF_PAGES + 1}-MARKER" not in text
    doc, lines = await read(pdf, "long.pdf")
    assert doc.meta["pages"] == 70
    assert lines[0] == "Parsed 70 pages"


async def test_extracted_text_is_cut_to_the_analysis_budget_and_flagged() -> None:
    page = [("Responsible for delivering projects across teams " * 2).strip() for _ in range(100)]
    pdf = make_pdf([page] * 60)
    doc, _ = await read(pdf, "huge.pdf")
    assert doc.ok is True
    assert len(doc.text) == MAX_RESUME_CHARS == 30_000
    assert doc.meta["truncated"] is True
    assert doc.meta["chars"] > MAX_RESUME_CHARS


async def test_one_unreadable_page_does_not_lose_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PageObject

    original = PageObject.extract_text
    calls = {"n": 0}

    def flaky(self: PageObject, *args: object, **kwargs: object) -> str:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("broken page")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", flaky)
    pdf = make_pdf([["FIRST-PAGE " + "a" * 100], ["SECOND-PAGE " + "b" * 100], ["THIRD-PAGE " + "c" * 100]])
    text = await parse_resume(pdf, "x.pdf")
    assert "FIRST-PAGE" in text
    assert "SECOND-PAGE" not in text
    assert "THIRD-PAGE" in text


@pytest.mark.parametrize(
    "data",
    [b"%PDF-1.4\ngarbage", b"%PDF-1.7\n" + b"\x00" * 64, b"not a pdf at all"],
    ids=["garbage", "zeros", "text"],
)
async def test_a_corrupt_pdf_gets_a_clear_message(data: bytes) -> None:
    doc, _ = await read(data, "broken.pdf")
    assert doc.ok is False
    assert (
        doc.error == "Could not read that PDF. It may be corrupted. Try exporting it again or upload a DOCX."
    )
    assert doc.kind_label == "Resume · PDF"
    assert doc.pdf_bytes is None


async def test_a_truncated_pdf_does_not_crash() -> None:
    pdf = make_pdf([RESUME_LINES])
    doc, _ = await read(pdf[: len(pdf) // 2], "cut.pdf")
    assert doc.ok in (True, False)  # recovered or refused, but always a SourceDoc


# ----------------------------------------------------------------------------- encrypted PDFs


async def test_a_password_protected_pdf_asks_for_an_unlocked_copy() -> None:
    locked = encrypt_pdf(make_pdf([RESUME_LINES]), user_password="correct horse")
    doc, _ = await read(locked, "locked.pdf")
    assert doc.ok is False
    assert doc.error == "That PDF is password-protected. Export an unlocked copy and upload it again."
    assert doc.kind_label == "Resume · PDF"
    assert doc.pdf_bytes is None  # an unreadable file is never handed to the reader


async def test_a_pdf_with_only_an_owner_password_is_readable() -> None:
    restricted = encrypt_pdf(make_pdf([RESUME_LINES]), user_password="")
    doc, _ = await read(restricted, "restricted.pdf")
    assert doc.ok is True
    assert "Frontend Engineer" in doc.text


async def test_an_aes_pdf_without_the_crypto_package_says_it_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError

    def no_crypto(self: PdfReader, password: str) -> int:
        raise DependencyError("cryptography>=3.1 is required for AES algorithm")

    monkeypatch.setattr(PdfReader, "decrypt", no_crypto)
    locked = encrypt_pdf(make_pdf([RESUME_LINES]), user_password="x")
    doc, _ = await read(locked, "aes.pdf")
    assert doc.ok is False
    assert (
        doc.error
        == "That PDF is encrypted and cannot be opened here. Export an unprotected copy and upload it again."
    )


async def test_any_other_decrypt_failure_reads_as_a_password_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfReader

    def boom(self: PdfReader, password: str) -> int:
        raise NotImplementedError("unsupported scheme")

    monkeypatch.setattr(PdfReader, "decrypt", boom)
    doc, _ = await read(encrypt_pdf(make_pdf([RESUME_LINES]), user_password="x"), "odd.pdf")
    assert doc.error == "That PDF is password-protected. Export an unlocked copy and upload it again."


# ----------------------------------------------------------------------------- the scanned-PDF path


async def test_a_pdf_without_text_comes_back_with_empty_text_the_file_attached_and_still_readable() -> None:
    blank = make_pdf([[], []])
    doc, lines = await read(blank, "scan.pdf")
    # The pipeline attaches `pdf_bytes` only when `text` is empty, and reads a source only when `ok` is True.
    assert doc.text == ""
    assert doc.pdf_bytes == blank
    assert doc.meta["scanned"] is True
    assert doc.ok is True
    assert doc.error is None
    assert doc.meta["pages"] == 2
    assert doc.meta["chars"] == 0
    assert doc.sub == "2 pages"
    assert "Little selectable text found, the reader will view the file directly" in lines
    assert not any(line.startswith("Found sections") for line in lines)


async def test_a_pdf_with_a_stray_scrap_of_text_is_also_scanned_and_its_text_dropped() -> None:
    pdf = make_pdf([["Page 1 of 1", "Scanned with CamScanner"]])
    doc, _ = await read(pdf, "scan.pdf")
    assert doc.text == ""  # not a placeholder, and not the scrap either: a non-empty text would hide the file
    assert doc.pdf_bytes == pdf
    assert doc.meta["scanned"] is True
    assert doc.meta["chars"] == len("Page 1 of 1\nScanned with CamScanner")
    assert doc.ok is True


@pytest.mark.parametrize(
    ("characters", "scanned"),
    [(0, True), (1, True), (MIN_TEXT_CHARS - 1, True), (MIN_TEXT_CHARS, False), (MIN_TEXT_CHARS + 1, False)],
)
async def test_the_scanned_threshold_is_exactly_min_text_chars(characters: int, scanned: bool) -> None:
    pdf = padded_pdf(characters) if characters else make_pdf([[]])
    doc, _ = await read(pdf, "t.pdf")
    assert doc.meta["chars"] == characters
    assert bool(doc.meta.get("scanned")) is scanned
    assert (doc.text == "") is scanned
    assert (doc.pdf_bytes is not None) is scanned
    assert doc.ok is True


async def test_a_scanned_source_serialises_without_the_file_bytes() -> None:
    blank = make_pdf([[]])
    doc, _ = await read(blank, "scan.pdf")
    data = doc.to_dict()
    assert data["text"] == "" and data["content"] == ""
    assert data["ok"] is True
    assert "pdf_bytes" not in data
    assert "pdf_bytes" not in repr(doc)  # repr=False: a scanned file does not flood logs and tracebacks


async def test_scanned_is_a_pdf_only_state() -> None:
    doc, _ = await read(b"   \n  ", "empty.txt")
    assert doc.ok is False
    assert doc.error == "No readable text was found in that file. Try a different export of your resume."
    doc, _ = await read(b"Short note.", "short.txt")
    assert doc.ok is True
    assert doc.text == "Short note."
    assert "scanned" not in doc.meta


# ----------------------------------------------------------------------------- hostile PDFs


def _flate(size: int, tail: bytes = b"BT /F1 11 Tf 72 720 Td (hidden) Tj ET") -> bytes:
    compressor = zlib.compressobj(9)
    parts = [compressor.compress(b" " * (1 << 20)) for _ in range(size >> 20)]
    parts += [compressor.compress(tail), compressor.flush()]
    return b"".join(parts)


async def test_a_content_stream_that_inflates_far_past_the_limit_is_cut_quickly() -> None:
    bomb = make_pdf_with_streams([(_flate(64 << 20), "/Filter /FlateDecode")])
    assert len(bomb) < 100_000
    tracemalloc.start()
    started = time.perf_counter()
    try:
        doc, _ = await read(bomb, "bomb.pdf")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert time.perf_counter() - started < 5  # about ten seconds and 140 MB with pypdf's default 75 MB limit
    assert peak < 80 * 1024 * 1024
    assert doc.ok is True
    assert doc.meta["scanned"] is True  # nothing readable survived, so the reader looks at the file itself


async def test_every_page_pointing_at_the_same_heavy_stream_stops_at_the_time_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.ingest.helpers import _assemble_pdf

    monkeypatch.setattr(resume_module, "PDF_EXTRACT_BUDGET_S", 1.0)
    stream = _flate(5 << 20)  # under the stream limit, so each page really costs about a second
    pages = 60
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{4 + i} 0 R' for i in range(pages))}] /Count {pages} >>".encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    shared = 4 + pages
    for i in range(pages):
        objects[4 + i] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {shared} 0 R >>"
        ).encode()
    objects[shared] = (
        f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode() + stream + b"\nendstream"
    )
    started = time.perf_counter()
    doc, _ = await read(_assemble_pdf(objects), "shared.pdf")
    elapsed = time.perf_counter() - started
    assert doc.ok is True
    assert doc.meta["pages"] == pages
    assert elapsed < 15  # sixty pages at a second each would take a minute


def test_the_pdf_stream_limit_is_restored_after_each_extraction() -> None:
    import pypdf

    apply = getattr(pypdf, "apply_configuration", None)
    if apply is None:
        pytest.skip("this pypdf has no per-call configuration")
    before = pypdf.get_configuration().zlib_maximum_output_length
    with resume_module._pdf_limits():
        assert pypdf.get_configuration().zlib_maximum_output_length == resume_module.MAX_PDF_STREAM_BYTES
    assert pypdf.get_configuration().zlib_maximum_output_length == before


# ----------------------------------------------------------------------------- text and markdown files


async def test_a_text_file_is_read_as_utf8_with_the_bom_and_odd_spaces_removed() -> None:
    raw = "﻿Riya Kapoor\r\nSkills\r\nReact,​ TypeScript\r\n".encode()
    doc, lines = await read(raw, "cv.txt")
    assert doc.ok is True
    assert doc.text == "Riya Kapoor\nSkills\nReact, TypeScript"
    assert doc.meta["format"] == "Text"
    assert doc.meta["sections"] == ["Skills"]
    assert lines[0] == "Read 5 words of text"


@pytest.mark.parametrize("name", ["cv.txt", "cv.md", "cv.markdown", "cv.text", "CV.TXT"])
async def test_text_extensions(name: str) -> None:
    doc, _ = await read(b"React developer with experience.", name)
    assert doc.ok is True
    assert doc.text == "React developer with experience."


async def test_extension_beats_magic_bytes() -> None:
    doc, _ = await read(make_pdf([RESUME_LINES]), "notes.txt")
    assert doc.meta["format"] == "Text"  # a .txt file is text, even if it starts with %PDF


@pytest.mark.parametrize(
    ("data", "name", "fmt"),
    [
        (make_pdf([RESUME_LINES]), "resume", "PDF"),
        (make_pdf([RESUME_LINES]), "resume.upload", "PDF"),
        (make_docx(RESUME_LINES), None, "DOCX"),
        (make_docx(RESUME_LINES), "resume.bin", "DOCX"),
    ],
    ids=["pdf-no-extension", "pdf-odd-extension", "docx-no-name", "docx-odd-extension"],
)
async def test_the_format_is_detected_from_magic_bytes_when_the_extension_says_nothing(
    data: bytes, name: str | None, fmt: str
) -> None:
    doc, _ = await read(data, name)
    assert doc.ok is True
    assert doc.meta["format"] == fmt
    assert doc.src == (name or "resume")


@pytest.mark.parametrize(
    ("name", "message"),
    [
        (
            "old.doc",
            "Legacy .doc files are not supported. Save the resume as DOCX or PDF and upload it again.",
        ),
        ("cv.rtf", "Unsupported file type. Upload your resume as a PDF, DOCX or TXT file."),
        ("cv.exe", "Unsupported file type. Upload your resume as a PDF, DOCX or TXT file."),
        ("cv", "Unsupported file type. Upload your resume as a PDF, DOCX or TXT file."),
    ],
)
async def test_unsupported_files_are_refused(name: str, message: str) -> None:
    doc, _ = await read(b"MZ\x90\x00 not a document", name)
    assert doc.ok is False
    assert doc.error == message
    assert doc.kind_label == "Resume"  # the format is unknown


async def test_an_empty_upload_is_refused() -> None:
    doc, _ = await read(b"", "resume.pdf")
    assert doc.ok is False
    assert doc.error == "That file is empty. Please upload your resume again."


async def test_a_very_large_text_file_is_cut_quickly() -> None:
    data = ("Delivered a project with React and Python.\n" * 200_000).encode()  # about 8.6 MB
    started = time.perf_counter()
    doc, _ = await read(data, "huge.txt")
    assert time.perf_counter() - started < 10
    assert doc.ok is True
    assert len(doc.text) == MAX_RESUME_CHARS
    assert doc.meta["truncated"] is True


async def test_parse_resume_returns_clean_text_or_raises_a_user_message() -> None:
    assert await parse_resume(b"  Hello\r\n\r\n\r\n\r\nWorld  ", "a.txt") == "Hello\n\nWorld"
    with pytest.raises(ResumeError) as exc:
        await parse_resume(b"garbage", "a.pdf")
    assert exc.value.fmt == "pdf"
    assert "Could not read that PDF" in str(exc.value)
    with pytest.raises(ResumeError, match="Unsupported file type"):
        await parse_resume(b"x", "a.xyz")


# ----------------------------------------------------------------------------- text helpers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ﬁnance ﬂow", "finance flow"),  # ligatures
        ("a​b‌‍⁠c﻿", "abc"),  # zero-width characters
        ("a b c d", "a b c d"),  # odd spaces
        ("a\x00b\x07c\x1fd\x0b", "abcd"),  # control characters
        ("a\r\nb\rc\fd", "a\nb\nc\nd"),  # line endings and form feeds
        ("a \t  b", "a b"),
        ("a \n b", "a\nb"),
        ("a\n\n\n\n\nb", "a\n\nb"),
        ("  padded  ", "padded"),
        ("ＡＢＣ", "ABC"),  # full-width forms
    ],
)
def test_clean_text(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Experience\nSkills", ["Experience", "Skills"]),
        ("Work Experience\nTechnical Skills\nEducation & Training", ["Experience", "Skills", "Education"]),
        (
            "PROFESSIONAL SUMMARY\nSelected Projects\nOpen Source Contributions",
            ["Summary", "Projects", "Open Source"],
        ),
        ("--- EXPERIENCE ---\n• Skills:\n## Education", ["Experience", "Skills", "Education"]),
        (
            "Certifications\nAwards & Honors\nPublications\nLanguages\nInterests",
            ["Certifications", "Achievements", "Publications", "Languages", "Interests"],
        ),
        ("Positions of Responsibility\nVolunteer Experience", ["Leadership"]),
        ("Experience\nExperience\nexperience", ["Experience"]),
        ("I have a lot of experience in many things", []),
        ("Skills " + "x" * 60, []),
        ("", []),
    ],
)
def test_detect_sections(text: str, expected: list[str]) -> None:
    assert detect_sections(text) == expected


def test_a_hostile_resume_is_scanned_for_sections_in_linear_time() -> None:
    started = time.perf_counter()
    assert detect_sections("-" * 300_000 + "\n" + "=" * 300_000 + "\n" + "Experience") == ["Experience"]
    assert detect_sections("\n".join(["Skills"] * 200_000)) == ["Skills"]
    assert detect_sections(" \n" * 300_000) == []
    clean_text("\n \t" * 300_000)
    clean_text("a" * 2_000_000)
    assert time.perf_counter() - started < 8


# ----------------------------------------------------------------------------- logging


async def test_ingesting_a_resume_logs_counts_never_its_text(record_logs) -> None:
    secret = "CONFIDENTIAL-RESUME-LINE-5521"
    pdf = make_pdf([[secret, *RESUME_LINES]])
    doc, _ = await read(pdf, "me.pdf")
    assert doc.ok is True
    [event] = record_logs.named("resume_ingested")
    assert event["format"] == "pdf"
    assert event["pages"] == 1
    assert event["scanned"] is False
    assert event["sections"] == 5
    assert isinstance(event["chars"], int) and event["chars"] > 200
    flattened = " ".join(str(v) for e in record_logs.events for v in e.values())
    assert secret not in flattened
    assert "me.pdf" not in flattened  # not even the file name


async def test_a_scanned_resume_is_logged_as_scanned(record_logs) -> None:
    await read(make_pdf([[]]), "scan.pdf")
    [event] = record_logs.named("resume_ingested")
    assert event["scanned"] is True
    assert event["chars"] == 0


async def test_a_rejected_resume_is_logged_with_the_reason_and_format_only(record_logs) -> None:
    locked = encrypt_pdf(make_pdf([RESUME_LINES]), user_password="correct horse")
    await read(locked, "secret-name.pdf")
    await read(b"", "empty.pdf")
    await read(b"   ", "blank.txt")
    reasons = [(e["format"], e["reason"]) for e in record_logs.named("resume_rejected")]
    assert reasons == [
        ("pdf", "That PDF is password-protected. Export an unlocked copy and upload it again."),
        (None, "That file is empty. Please upload your resume again."),
        ("text", "No readable text was found in that file. Try a different export of your resume."),
    ]
    assert not record_logs.named("resume_ingested")
    assert "secret-name.pdf" not in " ".join(str(v) for e in record_logs.events for v in e.values())
