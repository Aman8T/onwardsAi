from __future__ import annotations

import json
import tracemalloc
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tayyari.api.errors import install_error_handlers
from tayyari.api.uploads import (
    DEFAULT_LIMITS,
    MSG_BAD_FILE_TYPE,
    MSG_BAD_LINKS,
    MSG_BODY_TOO_LARGE,
    MSG_INVALID_JSON,
    MSG_INVALID_MULTIPART,
    MSG_MISSING_BOUNDARY,
    MSG_NO_INPUT,
    MSG_WRONG_FILE_FIELD,
    UploadLimits,
    clean_field,
    clean_filename,
    is_accepted_upload,
    parse_analyze_form,
    parse_links,
    read_json_body,
)
from tayyari.contract.requests import AnalyzeRequest
from tayyari.core.errors import AppError, BadRequest, PayloadTooLarge
from tayyari.core.limits import (
    LINK_MAX_CHARS,
    LINKS_MAX_PER_REQUEST,
    MAX_ANALYZE_BODY_BYTES,
    MAX_RESUME_BYTES,
    MIB,
)
from tayyari.legacy.server import _field as legacy_field
from tayyari.legacy.server import _parse_links as legacy_parse_links
from tests.unit.api.helpers import (
    KIB,
    Pulled,
    make_request,
    multipart_body,
    repeated,
    split,
)

PDF = ("resume", "cv.pdf", b"%PDF-1.4 fake pdf bytes", "application/pdf")
FILE_PART_HEADER = (
    b'--b\r\nContent-Disposition: form-data; name="resume"; filename="cv.pdf"\r\n'
    b"Content-Type: application/pdf\r\n\r\n"
)
"""The start of a multipart body (boundary ``b``) whose first part is a PDF resume; the file data follows."""
SMALL = UploadLimits(
    max_body_bytes=64 * KIB, max_resume_bytes=8 * KIB, max_field_bytes=1 * KIB, max_fields=5, max_files=1
)


async def parse(
    fields: list[tuple[str, str]] | None = None,
    files: list[tuple[str, str, bytes, str | None]] | None = None,
    *,
    limits: UploadLimits = DEFAULT_LIMITS,
    chunk: int = 4096,
) -> AnalyzeRequest:
    body, content_type = multipart_body(fields or [], files or [])
    return await parse_analyze_form(
        make_request(split(body, chunk), content_type=content_type), limits=limits
    )


async def refused(exc_type: type[AppError], **kwargs: Any) -> AppError:
    with pytest.raises(exc_type) as info:
        await parse(**kwargs)
    return info.value


# ───────────────────────────── the happy path ─────────────────────────────


async def test_full_form_becomes_an_analyze_request() -> None:
    req = await parse(
        [
            ("links", json.dumps(["github.com/ada", "  https://ada.dev  "])),
            ("goal", "  Staff engineer  "),
            ("notes", "I built compilers."),
        ],
        [PDF],
    )
    assert req.resume_bytes == b"%PDF-1.4 fake pdf bytes"
    assert req.resume_name == "cv.pdf"
    assert req.links == ("github.com/ada", "https://ada.dev")
    assert req.goal == "Staff engineer"
    assert req.notes == "I built compilers."
    assert req.sample is False


async def test_links_alone_are_enough() -> None:
    req = await parse([("links", "github.com/ada")])
    assert req.resume_bytes is None
    assert req.resume_name is None
    assert req.links == ("github.com/ada",)


async def test_notes_alone_are_enough() -> None:
    req = await parse([("notes", "  five years of design work  ")])
    assert req.notes == "five years of design work"
    assert req.links == ()


async def test_a_resume_alone_is_enough() -> None:
    req = await parse(files=[("resume", "cv.txt", b"plain text resume", "text/plain")])
    assert req.resume_bytes == b"plain text resume"


async def test_an_empty_resume_file_is_still_a_resume() -> None:
    # Legacy parity: b"" is "a resume" (not None); ingestion reports the unreadable file.
    req = await parse(files=[("resume", "cv.txt", b"", "text/plain")])
    assert req.resume_bytes == b""
    assert req.resume_name == "cv.txt"


async def test_sample_flag_ignores_the_resume_and_needs_no_input() -> None:
    for flag in ("1", "true"):
        req = await parse([("sample", flag)], [PDF])
        assert req.sample is True
        assert req.resume_bytes is None
        assert req.resume_name is None
    assert (await parse([("sample", "yes")], [PDF])).sample is False


async def test_sample_flag_alone_passes_validation() -> None:
    req = await parse([("sample", "1")])
    assert req.sample is True


async def test_the_last_value_of_a_repeated_field_wins() -> None:
    req = await parse(
        [("goal", "first"), ("goal", "second"), ("links", "github.com/a"), ("links", "github.com/b")]
    )
    assert req.goal == "second"
    assert req.links == ("github.com/a", "github.com/b")


async def test_text_fields_decode_as_utf8() -> None:
    req = await parse([("notes", "Zurich \u2014 caf\u00e9 \u65e5\u672c\u8a9e")])
    assert req.notes == "Zurich \u2014 caf\u00e9 \u65e5\u672c\u8a9e"


@pytest.mark.parametrize("chunk", [1, 3, 7, 64, 1 << 20])
async def test_any_chunking_of_the_body_parses_the_same(chunk: int) -> None:
    req = await parse([("links", "github.com/ada"), ("goal", "x")], [PDF], chunk=chunk)
    assert req.resume_bytes == b"%PDF-1.4 fake pdf bytes"
    assert req.links == ("github.com/ada",)
    assert req.goal == "x"


async def test_goal_and_notes_are_trimmed_and_capped() -> None:
    req = await parse(
        [("goal", "g" * 1000), ("notes", "n" * 30_000)], limits=UploadLimits(max_field_bytes=64 * KIB)
    )
    assert req.goal == "g" * 300
    assert req.notes == "n" * 20_000


async def test_blank_goal_and_notes_become_none() -> None:
    req = await parse([("goal", "   "), ("notes", "\n\n"), ("links", "github.com/a")])
    assert req.goal is None
    assert req.notes is None


async def test_the_request_repr_hides_the_content() -> None:
    req = await parse([("notes", "SECRET NOTES")], [PDF])
    assert "SECRET" not in repr(req)
    assert "PDF" not in repr(req)


# ───────────────────────────── links: parity with the legacy parser ─────────────────────────────

LINK_INPUTS: list[list[str]] = [
    [],
    [""],
    ["   "],
    ["github.com/ada"],
    ["github.com/ada https://ada.dev"],
    ["github.com/ada,https://ada.dev,  lovable.dev/x"],
    ['["github.com/ada", "https://ada.dev"]'],
    ['["github.com/ada", 7, null, "", "  ", {"a": 1}, ["nested"]]'],
    ['"just-a-json-string.com"'],
    ["123"],
    ["null"],
    ["true"],
    ['{"links": ["a.com"]}'],
    ["[not json, but a.com b.com"],
    ["a.com\nb.com\tc.com"],
    ['["' + "x" * 900 + '"]'],
    ["y" * 900],
    [f"site{i}.com" for i in range(40)],
    [json.dumps([f"https://example.com/{i}" for i in range(40)])],
    ["a.com", '["b.com", "c.com"]', "d.com, e.com", ""],
    ["\u00e9t\u00e9.fr, \u65e5\u672c.jp"],
    ["[]"],
    ["{}"],
    ['["  padded.com  "]'],
]


@pytest.mark.parametrize("values", LINK_INPUTS, ids=[repr(v)[:40] for v in LINK_INPUTS])
def test_parse_links_matches_the_legacy_parser(values: list[str]) -> None:
    assert parse_links(values) == legacy_parse_links(values)


def test_parse_links_limits() -> None:
    assert LINKS_MAX_PER_REQUEST == 20
    assert LINK_MAX_CHARS == 500
    assert len(parse_links([f"s{i}.com" for i in range(50)])) == 20
    assert all(len(link) == 500 for link in parse_links(["z" * 2000]))


def test_parse_links_survives_absurdly_nested_json() -> None:
    # The legacy parser raised RecursionError (a 500) here; the text is now read as one odd "link".
    links = parse_links(["[" * 200_000])
    assert len(links) == 1
    assert links[0] == "[" * LINK_MAX_CHARS


@pytest.mark.parametrize("value", [None, 5, "", "   ", "\n", " hi ", "x" * 50, ["a"]])
def test_clean_field_matches_the_legacy_field(value: object) -> None:
    assert clean_field(value, 10) == legacy_field(value, 10)


# ───────────────────────────── validation before the stream starts ─────────────────────────────


async def test_nothing_to_analyse_is_refused_with_the_legacy_text() -> None:
    err = await refused(BadRequest, fields=[("goal", "just a goal")])
    assert err.user_message == "Add a resume, a link or some notes to analyse." == MSG_NO_INPUT
    assert err.status == 400


async def test_an_empty_body_is_refused_as_nothing_to_analyse() -> None:
    _, content_type = multipart_body()
    with pytest.raises(BadRequest) as info:
        await parse_analyze_form(make_request([], content_type=content_type))
    assert info.value.user_message == MSG_NO_INPUT


async def test_links_that_are_not_web_addresses_get_their_own_text() -> None:
    err = await refused(
        BadRequest, fields=[("links", json.dumps(["not a url", "localhost-only", "ftp://x"]))]
    )
    assert err.user_message == (
        "None of those links look like valid web addresses. Add a resume or a full URL such as github.com/you."
    )
    assert err.user_message == MSG_BAD_LINKS


async def test_unusable_links_do_not_matter_when_notes_exist() -> None:
    req = await parse([("links", "garbage"), ("notes", "some notes")])
    assert req.links == ("garbage",)
    assert req.notes == "some notes"


async def test_blank_notes_do_not_count() -> None:
    err = await refused(BadRequest, fields=[("notes", "   \n  ")])
    assert err.user_message == MSG_NO_INPUT


async def test_a_non_form_content_type_is_an_empty_form() -> None:
    with pytest.raises(BadRequest) as info:
        await parse_analyze_form(
            make_request([b'{"links": ["github.com/a"]}'], content_type="application/json")
        )
    assert info.value.user_message == MSG_NO_INPUT


async def test_urlencoded_forms_still_work() -> None:
    req = await parse_analyze_form(
        make_request(
            [b"links=github.com%2Fada&goal=ship+it&notes=hello"],
            content_type="application/x-www-form-urlencoded",
        )
    )
    assert req.links == ("github.com/ada",)
    assert req.goal == "ship it"
    assert req.notes == "hello"


async def test_urlencoded_forms_are_capped_too() -> None:
    limits = UploadLimits(max_body_bytes=200, max_fields=2, max_field_bytes=50)
    ctype = "application/x-www-form-urlencoded"
    with pytest.raises(BadRequest, match="Too many fields"):
        await parse_analyze_form(make_request([b"a=1&b=2&c=3"], content_type=ctype), limits=limits)
    with pytest.raises(PayloadTooLarge, match="Part exceeded"):
        await parse_analyze_form(make_request([b"notes=" + b"x" * 80], content_type=ctype), limits=limits)
    pulled = Pulled()
    with pytest.raises(PayloadTooLarge, match=MSG_BODY_TOO_LARGE):
        await parse_analyze_form(
            make_request(repeated(b"x" * 100, 50), content_type=ctype, pulled=pulled), limits=limits
        )
    assert pulled.bytes <= 300


# ───────────────────────────── the file: type, field, count, size ─────────────────────────────


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("cv.pdf", "application/pdf"),
        ("CV.PDF", None),
        ("cv.docx", "application/octet-stream"),
        ("notes.txt", "text/plain"),
        ("notes.md", None),
        ("resume", "application/pdf"),
        ("resume", "text/plain; charset=utf-8"),
        ("resume", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("my resume (final).v2.pdf", "application/octet-stream"),
    ],
)
async def test_accepted_files(filename: str, content_type: str | None) -> None:
    req = await parse(files=[("resume", filename, b"data", content_type)])
    assert req.resume_bytes == b"data"
    assert req.resume_name == filename


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("cv.exe", "application/pdf"),
        ("cv.pdf.exe", "application/pdf"),
        ("cv.doc", "application/msword"),
        ("cv.rtf", "text/plain"),
        ("script.js", "text/plain"),
        ("archive.zip", None),
        ("resume", None),
        ("resume", "application/octet-stream"),
        ("resume", "image/png"),
    ],
)
async def test_rejected_files(filename: str, content_type: str | None) -> None:
    err = await refused(BadRequest, files=[("resume", filename, b"data", content_type)])
    assert err.user_message == "Upload your resume as a PDF, DOCX, TXT or Markdown file." == MSG_BAD_FILE_TYPE


async def test_a_bad_extension_is_refused_even_in_sample_mode() -> None:
    err = await refused(BadRequest, fields=[("sample", "1")], files=[("resume", "cv.exe", b"MZ", None)])
    assert err.user_message == MSG_BAD_FILE_TYPE


async def test_a_file_in_any_other_field_is_refused() -> None:
    err = await refused(BadRequest, files=[("attachment", "cv.pdf", b"data", "application/pdf")])
    assert err.user_message == MSG_WRONG_FILE_FIELD
    assert err.status == 400


async def test_two_files_are_refused_with_the_starlette_text() -> None:
    err = await refused(BadRequest, files=[PDF, ("resume", "other.pdf", b"x", "application/pdf")])
    assert err.user_message == "Too many files. Maximum number of files is 1."


async def test_a_second_file_in_another_field_is_a_too_many_files_error() -> None:
    err = await refused(BadRequest, files=[PDF, ("extra", "x.pdf", b"x", None)])
    assert "Too many files" in err.user_message


async def test_an_empty_file_input_is_treated_as_no_file() -> None:
    # A browser sends an empty file part (filename="") when nothing was chosen.
    req = await parse([("links", "github.com/ada")], [("resume", "", b"", "application/octet-stream")])
    assert req.resume_bytes is None
    assert req.links == ("github.com/ada",)


async def test_an_empty_file_input_does_not_use_up_the_file_allowance() -> None:
    req = await parse(files=[("resume", "", b"", "application/octet-stream"), PDF])
    assert req.resume_bytes == b"%PDF-1.4 fake pdf bytes"


async def test_a_file_without_a_name_but_with_content_is_judged_like_any_other() -> None:
    err = await refused(BadRequest, files=[("resume", "", b"data", "application/octet-stream")])
    assert err.user_message == MSG_BAD_FILE_TYPE
    req = await parse(files=[("resume", "", b"data", "application/pdf")])
    assert req.resume_bytes == b"data"
    assert req.resume_name == ""


def test_filenames_are_reduced_to_a_clean_base_name() -> None:
    assert clean_filename("C:\\Users\\me\\My CV.pdf") == "My CV.pdf"
    assert clean_filename("../../etc/passwd.txt") == "passwd.txt"
    assert clean_filename("cv\x00\r\n.pdf") == "cv.pdf"
    assert len(clean_filename("a" * 400 + ".pdf")) == 255


async def test_the_stored_file_name_is_the_clean_base_name() -> None:
    req = await parse(files=[("resume", "..\\..\\evil dir\\cv.pdf", b"data", None)])
    assert req.resume_name == "cv.pdf"


def test_is_accepted_upload_is_case_insensitive_on_the_mime_type() -> None:
    assert is_accepted_upload("resume", "Application/PDF")
    assert not is_accepted_upload("resume", "")
    assert not is_accepted_upload("resume", None)


async def test_a_resume_over_the_cap_is_refused_with_the_legacy_text() -> None:
    too_big = ("resume", "cv.pdf", b"x" * (MAX_RESUME_BYTES + 1), "application/pdf")
    err = await refused(PayloadTooLarge, files=[too_big], chunk=256 * KIB)
    assert err.user_message == "That file is larger than 10 MB."
    assert err.status == 413


async def test_a_resume_exactly_at_the_cap_is_accepted() -> None:
    exact = ("resume", "cv.pdf", b"x" * MAX_RESUME_BYTES, "application/pdf")
    req = await parse(files=[exact], chunk=256 * KIB)
    assert req.resume_bytes is not None
    assert len(req.resume_bytes) == MAX_RESUME_BYTES


async def test_the_file_cap_text_follows_the_limit() -> None:
    err = await refused(
        PayloadTooLarge, files=[("resume", "cv.pdf", b"x" * (SMALL.max_resume_bytes + 1), None)], limits=SMALL
    )
    assert err.user_message == "That file is larger than 8 KB."


async def test_a_text_field_over_the_cap_is_refused() -> None:
    err = await refused(PayloadTooLarge, fields=[("notes", "n" * (SMALL.max_field_bytes + 1))], limits=SMALL)
    assert err.user_message == "Part exceeded maximum size of 1KB."
    ok = await parse([("notes", "n" * SMALL.max_field_bytes)], limits=SMALL)
    assert ok.notes == "n" * SMALL.max_field_bytes


async def test_the_default_field_cap_is_256_kb() -> None:
    err = await refused(PayloadTooLarge, fields=[("notes", "n" * (256 * KIB + 1))])
    assert err.user_message == "Part exceeded maximum size of 256KB."


async def test_too_many_fields_are_refused_with_the_starlette_text() -> None:
    fields = [(f"f{i}", "v") for i in range(SMALL.max_fields + 1)]
    err = await refused(BadRequest, fields=[*fields, ("notes", "n")], limits=SMALL)
    assert err.user_message == "Too many fields. Maximum number of fields is 5."
    just_enough = [(f"f{i}", "v") for i in range(SMALL.max_fields - 1)] + [("notes", "n")]
    assert (await parse(just_enough, limits=SMALL)).notes == "n"


async def test_the_file_part_does_not_count_as_a_field() -> None:
    fields = [(f"f{i}", "v") for i in range(SMALL.max_fields - 1)] + [("notes", "n")]
    req = await parse(fields, [("resume", "cv.txt", b"abc", "text/plain")], limits=SMALL)
    assert req.resume_bytes == b"abc"


def test_the_default_limits_come_from_core_limits() -> None:
    assert DEFAULT_LIMITS.max_body_bytes == MAX_ANALYZE_BODY_BYTES == 11 * MIB
    assert DEFAULT_LIMITS.max_resume_bytes == MAX_RESUME_BYTES == 10 * MIB
    assert DEFAULT_LIMITS.max_field_bytes == 256 * KIB
    assert DEFAULT_LIMITS.max_fields == 20
    assert DEFAULT_LIMITS.max_files == 1


# ───────────────────────────── the body: caps on the bytes that actually arrive ─────────────────────────────


def valid_form_then_junk(junk: bytes, count: int) -> Iterator[bytes]:
    """A complete, valid form followed by ``count`` junk chunks: the parser is finished with the form (it
    ignores an epilogue), so only the cap on the streamed byte count can stop a client that never stops."""
    body, _ = multipart_body([("notes", "hi")])

    def chunks() -> Iterator[bytes]:
        yield body
        yield from repeated(junk, count)

    return chunks()


async def test_a_chunked_body_over_the_cap_is_refused_without_reading_it_all() -> None:
    _, content_type = multipart_body()
    chunk = b"x" * (64 * KIB)
    pulled = Pulled()
    # No Content-Length: a chunked upload that would be 320 MB.
    request = make_request(valid_form_then_junk(chunk, 5000), content_type=content_type, pulled=pulled)
    with pytest.raises(PayloadTooLarge) as info:
        await parse_analyze_form(request)
    assert info.value.user_message == "Request body too large"
    assert info.value.status == 413
    # The reader stopped at the first chunk that crossed the cap: bounded by cap + one chunk.
    assert pulled.bytes <= MAX_ANALYZE_BODY_BYTES + len(chunk)
    assert pulled.bytes < 5000 * len(chunk) / 10


async def test_the_body_cap_trips_before_the_file_cap_when_it_is_tighter() -> None:
    limits = UploadLimits(max_body_bytes=20 * KIB, max_resume_bytes=1 * MIB)
    pulled = Pulled()

    def chunks() -> Iterator[bytes]:
        yield FILE_PART_HEADER
        yield from repeated(b"q" * KIB, 10_000)

    request = make_request(chunks(), content_type="multipart/form-data; boundary=b", pulled=pulled)
    with pytest.raises(PayloadTooLarge, match=MSG_BODY_TOO_LARGE):
        await parse_analyze_form(request, limits=limits)
    assert pulled.bytes <= limits.max_body_bytes + KIB


async def test_a_declared_content_length_over_the_cap_is_refused_before_reading() -> None:
    _, content_type = multipart_body()
    pulled = Pulled()
    request = make_request(
        repeated(b"x", 3), content_type=content_type, content_length=MAX_ANALYZE_BODY_BYTES + 1, pulled=pulled
    )
    with pytest.raises(PayloadTooLarge, match=MSG_BODY_TOO_LARGE):
        await parse_analyze_form(request)
    assert pulled.bytes == 0


async def test_a_lying_content_length_cannot_smuggle_more_bytes() -> None:
    # Content-Length says "small", the stream keeps going: the streamed byte count is what is enforced.
    _, content_type = multipart_body()
    pulled = Pulled()
    request = make_request(
        valid_form_then_junk(b"y" * KIB, 1000),
        content_type=content_type,
        content_length=10,
        pulled=pulled,
    )
    with pytest.raises(PayloadTooLarge):
        await parse_analyze_form(request, limits=SMALL)
    assert pulled.bytes <= SMALL.max_body_bytes + KIB


@pytest.mark.parametrize("header", ["abc", "", "-5", "\u00b2"])
async def test_a_garbage_content_length_is_ignored_not_trusted(header: str) -> None:
    body, content_type = multipart_body([("notes", "hi")])
    request = make_request(split(body, 100), content_type=content_type, headers=[("content-length", header)])
    assert (await parse_analyze_form(request)).notes == "hi"


async def test_a_body_exactly_at_the_cap_is_allowed_through_to_validation() -> None:
    body, content_type = multipart_body([("notes", "x" * 100)])
    limits = UploadLimits(max_body_bytes=len(body))
    assert (
        await parse_analyze_form(make_request(split(body, 50), content_type=content_type), limits=limits)
    ).notes
    tight = UploadLimits(max_body_bytes=len(body) - 1)
    with pytest.raises(PayloadTooLarge):
        await parse_analyze_form(make_request(split(body, 50), content_type=content_type), limits=tight)


async def test_memory_stays_bounded_while_an_endless_upload_is_refused() -> None:
    """40 MB of file data arrives in 64 KiB chunks against an 11 MB cap: peak memory stays near the cap."""
    header_block = FILE_PART_HEADER
    chunk = b"z" * (64 * KIB)

    def body() -> Any:
        yield header_block
        yield from repeated(chunk, 640)  # 40 MB, lazily

    pulled = Pulled()
    request = make_request(body(), content_type="multipart/form-data; boundary=b", pulled=pulled)
    tracemalloc.start()
    try:
        with pytest.raises(PayloadTooLarge):
            await parse_analyze_form(request)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert pulled.bytes <= MAX_RESUME_BYTES + len(chunk) + len(header_block)
    assert peak < MAX_RESUME_BYTES + 6 * MIB  # the resume buffer plus slack, never the 40 MB that was offered


async def test_a_refused_upload_stops_reading_at_the_file_cap() -> None:
    header_block = FILE_PART_HEADER

    def body() -> Any:
        yield header_block
        yield from repeated(b"q" * KIB, 100_000)

    pulled = Pulled()
    request = make_request(body(), content_type="multipart/form-data; boundary=b", pulled=pulled)
    with pytest.raises(PayloadTooLarge, match=r"That file is larger than 8 KB\."):
        await parse_analyze_form(request, limits=SMALL)
    assert pulled.bytes <= len(header_block) + SMALL.max_resume_bytes + KIB


# ───────────────────────────── malformed multipart ─────────────────────────────


async def test_a_multipart_request_without_a_boundary_is_refused() -> None:
    with pytest.raises(BadRequest) as info:
        await parse_analyze_form(make_request([b"whatever"], content_type="multipart/form-data"))
    assert info.value.user_message == MSG_MISSING_BOUNDARY == "Missing boundary in multipart."


async def test_a_truncated_body_is_refused_not_silently_accepted() -> None:
    body, content_type = multipart_body([("notes", "hello")], [PDF], closed=False)
    with pytest.raises(BadRequest) as info:
        await parse_analyze_form(make_request(split(body, 64), content_type=content_type))
    assert info.value.user_message == MSG_INVALID_MULTIPART == "Invalid multipart data."


async def test_garbage_that_is_not_multipart_is_refused() -> None:
    request = make_request(
        [b"this is not a multipart body at all"], content_type="multipart/form-data; boundary=zzz"
    )
    with pytest.raises(BadRequest) as info:
        await parse_analyze_form(request)
    assert info.value.user_message in (MSG_INVALID_MULTIPART, MSG_NO_INPUT)


async def test_an_absurd_boundary_is_refused() -> None:
    request = make_request([b"x"], content_type="multipart/form-data; boundary=" + "b" * 300)
    with pytest.raises(BadRequest) as info:
        await parse_analyze_form(request)
    assert info.value.user_message == MSG_INVALID_MULTIPART


async def test_a_part_without_a_name_is_refused() -> None:
    body = b"--b\r\nContent-Disposition: form-data\r\n\r\nvalue\r\n--b--\r\n"
    with pytest.raises(BadRequest, match="Content-Disposition"):
        await parse_analyze_form(make_request([body], content_type="multipart/form-data; boundary=b"))


async def test_a_leading_blank_line_and_a_trailing_epilogue_are_ignored() -> None:
    body, content_type = multipart_body([("notes", "hi")])
    request = make_request([b"\r\n", body, b"trailing epilogue"], content_type=content_type)
    assert (await parse_analyze_form(request)).notes == "hi"


async def test_text_before_the_first_boundary_is_refused() -> None:
    body, content_type = multipart_body([("notes", "hi")])
    request = make_request([b"ignored preamble\r\n", body], content_type=content_type)
    with pytest.raises(BadRequest, match="Invalid multipart data"):
        await parse_analyze_form(request)


# ───────────────────────────── read_json_body ─────────────────────────────


async def test_read_json_body_parses_json() -> None:
    request = make_request([b'{"a": ', b"[1, 2]}"], content_type="application/json")
    assert await read_json_body(request, 1024) == {"a": [1, 2]}


@pytest.mark.parametrize("body", [b"", b"not json", b"{", b"\xff\xfe\x00", b'{"a": nope}'])
async def test_read_json_body_rejects_what_is_not_json(body: bytes) -> None:
    with pytest.raises(BadRequest) as info:
        await read_json_body(make_request([body]), 1024)
    assert info.value.user_message == "Invalid JSON body" == MSG_INVALID_JSON
    assert info.value.status == 400


async def test_read_json_body_survives_absurdly_nested_json() -> None:
    with pytest.raises(BadRequest, match="Invalid JSON body"):
        await read_json_body(make_request([b"[" * 200_000]), 1 * MIB)


async def test_read_json_body_caps_a_chunked_body_while_it_streams() -> None:
    pulled = Pulled()
    request = make_request(repeated(b"x" * KIB, 10_000), pulled=pulled)
    with pytest.raises(PayloadTooLarge) as info:
        await read_json_body(request, 4 * KIB)
    assert info.value.user_message == "Request body too large"
    assert pulled.bytes <= 4 * KIB + KIB


async def test_read_json_body_checks_content_length_first() -> None:
    pulled = Pulled()
    request = make_request(repeated(b"x", 5), content_length=10_000, pulled=pulled)
    with pytest.raises(PayloadTooLarge):
        await read_json_body(request, 4 * KIB)
    assert pulled.bytes == 0


async def test_read_json_body_accepts_a_body_exactly_at_the_cap() -> None:
    body = json.dumps({"k": "v" * 90}).encode()
    assert await read_json_body(make_request([body]), len(body)) == {"k": "v" * 90}
    with pytest.raises(PayloadTooLarge):
        await read_json_body(make_request([body]), len(body) - 1)


# ───────────────────────────── through a real app: JSON error answers ─────────────────────────────


def make_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)

    @app.post("/api/analyze")
    async def analyze(request: Request) -> JSONResponse:
        req = await parse_analyze_form(request)
        return JSONResponse(
            {
                "resumeBytes": None if req.resume_bytes is None else len(req.resume_bytes),
                "resumeName": req.resume_name,
                "links": list(req.links),
                "goal": req.goal,
                "notes": req.notes,
                "sample": req.sample,
            }
        )

    @app.post("/api/chat")
    async def chat(request: Request) -> JSONResponse:
        return JSONResponse(await read_json_body(request, 1 * KIB))

    return app


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url="http://test")


async def test_http_upload_with_httpx_multipart(client: httpx.AsyncClient) -> None:
    async with client:
        res = await client.post(
            "/api/analyze",
            files={"resume": ("cv.pdf", b"%PDF-1.4 hello", "application/pdf")},
            data={"links": json.dumps(["github.com/ada"]), "goal": "Staff", "sample": ""},
        )
    assert res.status_code == 200
    assert res.json() == {
        "resumeBytes": 14,
        "resumeName": "cv.pdf",
        "links": ["github.com/ada"],
        "goal": "Staff",
        "notes": None,
        "sample": False,
    }


async def test_http_chunked_upload_over_the_cap_is_a_413_json_with_bounded_reads(
    client: httpx.AsyncClient,
) -> None:
    sent = Pulled()
    chunk = b"c" * (64 * KIB)

    async def body() -> Any:
        yield FILE_PART_HEADER
        for _ in range(100_000):  # would be 6 GB
            sent.bytes += len(chunk)
            yield chunk

    async with client:
        res = await client.post(
            "/api/analyze", content=body(), headers={"content-type": "multipart/form-data; boundary=b"}
        )
    assert res.status_code == 413
    assert res.headers["content-type"].startswith("application/json")
    assert res.json() == {"error": "That file is larger than 10 MB."}
    assert sent.bytes <= MAX_RESUME_BYTES + 2 * len(chunk)


async def test_http_chunked_body_has_no_content_length(client: httpx.AsyncClient) -> None:
    seen: dict[str, str | None] = {}

    async def body() -> Any:
        yield b"x" * 100

    request = client.build_request("POST", "/api/analyze", content=body())
    seen["content-length"] = request.headers.get("content-length")
    seen["transfer-encoding"] = request.headers.get("transfer-encoding")
    assert seen == {"content-length": None, "transfer-encoding": "chunked"}
    await client.aclose()


async def test_http_json_errors_for_bad_input(client: httpx.AsyncClient) -> None:
    async with client:
        empty = await client.post("/api/analyze", data={"goal": "x"})
        links = await client.post("/api/analyze", data={"links": "nonsense"})
        bad = await client.post(
            "/api/analyze", files={"resume": ("cv.exe", b"MZ", "application/octet-stream")}
        )
        oversized_json = await client.post("/api/chat", content=b"x" * 2048)
        bad_json = await client.post("/api/chat", content=b"{nope")
    assert (empty.status_code, empty.json()) == (400, {"error": MSG_NO_INPUT})
    assert (links.status_code, links.json()) == (400, {"error": MSG_BAD_LINKS})
    assert (bad.status_code, bad.json()) == (400, {"error": MSG_BAD_FILE_TYPE})
    assert (oversized_json.status_code, oversized_json.json()) == (413, {"error": MSG_BODY_TOO_LARGE})
    assert (bad_json.status_code, bad_json.json()) == (400, {"error": MSG_INVALID_JSON})
