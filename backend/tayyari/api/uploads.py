"""Request bodies, read with hard caps on the bytes that actually arrive.

`parse_analyze_form(request)` turns POST /api/analyze's multipart form into an `AnalyzeRequest`. It never
calls ``request.form()`` (which spools file parts to disk with no size limit): the body is read with
``request.stream()`` and fed to ``python_multipart.MultipartParser`` chunk by chunk, so every cap is enforced
as the bytes arrive and memory stays bounded by the caps whatever the client sends, a chunked body with no
Content-Length included:

    whole body     MAX_ANALYZE_BODY_BYTES counted on the stream (and the Content-Length header, as a pre-check)
    the resume     MAX_RESUME_BYTES, kept in memory; exactly one file, and only in the field ``resume``
    a text field   MAX_FIELD_BYTES (goal, notes, links ...)
    field count    MAX_FORM_FIELDS non-file parts

The parser stops reading at the first breach and the answer is a JSON error: `PayloadTooLarge` (413) for sizes,
`BadRequest` (400) for everything malformed. The texts are those of the legacy server (and of Starlette's form
parser, which it relied on); the one new case is a file in a field other than ``resume``.

After parsing, the legacy pre-stream validation runs (`tayyari.ingest.classify.usable_links`): a request with no
resume, no usable link and no notes is refused before any work starts.

`read_json_body(request, max_bytes)` is the same idea for JSON bodies (POST /api/chat).
"""

from __future__ import annotations

import json
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, MultipartState, parse_options_header
from starlette.requests import Request

from tayyari.contract.requests import AnalyzeRequest
from tayyari.core.errors import BadRequest, PayloadTooLarge
from tayyari.core.limits import (
    GOAL_MAX_CHARS,
    KIB,
    LINK_MAX_CHARS,
    LINKS_MAX_PER_REQUEST,
    MAX_ANALYZE_BODY_BYTES,
    MAX_CHAT_BODY_BYTES,
    MAX_FIELD_BYTES,
    MAX_FORM_FIELDS,
    MAX_RESUME_BYTES,
    MAX_UPLOAD_FILES,
    MIB,
    NOTES_MAX_CHARS,
    UPLOAD_EXTENSIONS,
    UPLOAD_MIME_TYPES,
)
from tayyari.ingest.classify import usable_links

if TYPE_CHECKING:
    from python_multipart.multipart import MultipartCallbacks

RESUME_FIELD = "resume"
FILENAME_MAX_CHARS = 255

MSG_BODY_TOO_LARGE = "Request body too large"
MSG_INVALID_JSON = "Invalid JSON body"
MSG_BAD_FILE_TYPE = "Upload your resume as a PDF, DOCX, TXT or Markdown file."
MSG_WRONG_FILE_FIELD = f'Attach the resume file in the "{RESUME_FIELD}" field.'
MSG_NO_INPUT = "Add a resume, a link or some notes to analyse."
MSG_BAD_LINKS = (
    "None of those links look like valid web addresses. Add a resume or a full URL such as github.com/you."
)
MSG_INVALID_MULTIPART = "Invalid multipart data."
MSG_MISSING_BOUNDARY = "Missing boundary in multipart."
MSG_MISSING_NAME = 'The Content-Disposition header field "name" must be provided.'


def _size_text(limit: int) -> str:
    return f"{limit // MIB} MB" if limit % MIB == 0 else f"{limit // KIB} KB"


def msg_file_too_large(limit: int) -> str:
    return f"That file is larger than {_size_text(limit)}."


def msg_field_too_large(limit: int) -> str:
    return f"Part exceeded maximum size of {limit // KIB}KB."


def msg_too_many_fields(limit: int) -> str:
    return f"Too many fields. Maximum number of fields is {limit}."


def msg_too_many_files(limit: int) -> str:
    return f"Too many files. Maximum number of files is {limit}."


@dataclass(frozen=True, slots=True)
class UploadLimits:
    """The caps `parse_analyze_form` enforces (defaults: `core.limits`; tests pass small ones)."""

    max_body_bytes: int = MAX_ANALYZE_BODY_BYTES
    max_resume_bytes: int = MAX_RESUME_BYTES
    max_field_bytes: int = MAX_FIELD_BYTES
    max_fields: int = MAX_FORM_FIELDS
    max_files: int = MAX_UPLOAD_FILES


DEFAULT_LIMITS = UploadLimits()


# ───────────────────────── shared helpers ─────────────────────────


def check_content_length(request: Request, limit: int) -> None:
    """Refuse an oversized body from its Content-Length header, before any of it is read."""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        length = int(raw)
    except ValueError:
        return
    if length > limit:
        raise PayloadTooLarge(MSG_BODY_TOO_LARGE)


async def _read_capped(request: Request, limit: int) -> bytes:
    """The whole body, refusing as soon as more than ``limit`` bytes have arrived."""
    check_content_length(request, limit)
    chunks: list[bytes] = []
    size = 0
    async with aclosing(request.stream()) as stream:
        async for chunk in stream:
            size += len(chunk)
            if size > limit:
                raise PayloadTooLarge(MSG_BODY_TOO_LARGE)
            chunks.append(chunk)
    return b"".join(chunks)


async def read_json_body(request: Request, max_bytes: int = MAX_CHAT_BODY_BYTES) -> Any:
    """The request body parsed as JSON. 413 ``Request body too large`` past ``max_bytes`` (counted on the
    stream as it arrives), 400 ``Invalid JSON body`` when it is not JSON."""
    body = await _read_capped(request, max_bytes)
    try:
        return json.loads(body)
    except (ValueError, RecursionError):  # not JSON, not UTF-8, or nested deeper than the parser allows
        raise BadRequest(MSG_INVALID_JSON) from None


def parse_links(values: list[str]) -> list[str]:
    """The ``links`` field(s): a JSON array string, or whitespace/comma separated URLs.

    At most `LINKS_MAX_PER_REQUEST`, `LINK_MAX_CHARS` each (identical to the legacy ``_parse_links``, except
    that absurdly nested JSON is read as plain text instead of crashing the request).
    """
    out: list[str] = []
    for value in values:
        if not value.strip():
            continue
        try:
            parsed: Any = json.loads(value)
        except (ValueError, RecursionError):  # not JSON (or nested too deeply to be): plain separated text
            parsed = value.replace(",", " ").split()
        items = parsed if isinstance(parsed, list) else [parsed]
        out.extend(item.strip()[:LINK_MAX_CHARS] for item in items if isinstance(item, str) and item.strip())
    return out[:LINKS_MAX_PER_REQUEST]


def clean_field(value: object, max_len: int) -> str | None:
    """A trimmed text field cut to ``max_len`` characters, or None when it is empty."""
    return value.strip()[:max_len] if isinstance(value, str) and value.strip() else None


def is_accepted_upload(filename: str, content_type: str | None) -> bool:
    """A resume file: a known extension, or no extension and a known MIME type."""
    ext = PurePath(filename).suffix.lower()
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return ext in UPLOAD_EXTENSIONS or (not ext and mime in UPLOAD_MIME_TYPES)


def clean_filename(name: str) -> str:
    """The base name only (no directories), without control characters, at most 255 characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(ch for ch in base if ch.isprintable()).strip()[:FILENAME_MAX_CHARS]


def _decode(data: bytes | bytearray, charset: str) -> str:
    try:
        return bytes(data).decode(charset)
    except (UnicodeDecodeError, LookupError):
        return bytes(data).decode("latin-1")


# ───────────────────────── multipart ─────────────────────────


@dataclass(slots=True)
class Upload:
    """The one accepted file part, in memory."""

    filename: str
    content_type: str | None
    data: bytes = field(repr=False)


@dataclass(slots=True)
class _Part:
    headers: dict[bytes, bytes] = field(default_factory=dict)
    name: str = ""
    filename: str = ""
    content_type: str | None = None
    is_file: bool = False
    deferred: bool = False
    """A file part with an empty file name (a browser's empty file input): judged when the part ends."""
    size: int = 0
    data: bytearray = field(default_factory=bytearray)


class _FormCollector:
    """Receives the parser's callbacks. Raises (`BadRequest` / `PayloadTooLarge`) the moment a cap is crossed,
    so the parser, and the loop feeding it, stop reading."""

    def __init__(self, limits: UploadLimits, charset: str) -> None:
        self.limits = limits
        self.charset = charset
        self.fields: list[tuple[str, str]] = []
        self.upload: Upload | None = None
        self._files_seen = 0
        self._fields_seen = 0
        self._part = _Part()
        self._header_name = bytearray()
        self._header_value = bytearray()

    def callbacks(self) -> MultipartCallbacks:
        return {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
        }

    def on_part_begin(self) -> None:
        self._part = _Part()
        self._header_name.clear()
        self._header_value.clear()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        self._part.headers[bytes(self._header_name).lower()] = bytes(self._header_value)
        self._header_name.clear()
        self._header_value.clear()

    def on_headers_finished(self) -> None:
        part = self._part
        _, options = parse_options_header(part.headers.get(b"content-disposition"))
        raw_name = options.get(b"name")
        if raw_name is None:
            raise BadRequest(MSG_MISSING_NAME)
        part.name = _decode(raw_name, self.charset)
        if b"filename" in options:
            self._files_seen += 1
            if self._files_seen > self.limits.max_files:
                raise BadRequest(msg_too_many_files(self.limits.max_files))
            part.is_file = True
            part.filename = clean_filename(_decode(options[b"filename"], self.charset))
            raw_type = part.headers.get(b"content-type")
            part.content_type = raw_type.decode("latin-1") if raw_type is not None else None
            if part.filename:
                self._check_file(part)
            else:
                part.deferred = True
        else:
            self._fields_seen += 1
            if self._fields_seen > self.limits.max_fields:
                raise BadRequest(msg_too_many_fields(self.limits.max_fields))

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        part = self._part
        part.size += end - start
        if part.is_file:
            if part.size > self.limits.max_resume_bytes:
                raise PayloadTooLarge(msg_file_too_large(self.limits.max_resume_bytes))
        elif part.size > self.limits.max_field_bytes:
            raise PayloadTooLarge(msg_field_too_large(self.limits.max_field_bytes))
        part.data += memoryview(data)[start:end]

    def on_part_end(self) -> None:
        part = self._part
        if part.is_file:
            if part.deferred:
                if part.size == 0:  # an empty file input: nothing was attached
                    self._files_seen -= 1
                    return
                self._check_file(part)
            self.upload = Upload(part.filename, part.content_type, bytes(part.data))
        else:
            self.fields.append((part.name, _decode(part.data, self.charset)))

    @staticmethod
    def _check_file(part: _Part) -> None:
        if part.name != RESUME_FIELD:
            raise BadRequest(MSG_WRONG_FILE_FIELD)
        if not is_accepted_upload(part.filename, part.content_type):
            raise BadRequest(MSG_BAD_FILE_TYPE)


async def _read_multipart(
    request: Request, params: dict[bytes, bytes], limits: UploadLimits
) -> tuple[list[tuple[str, str]], Upload | None]:
    boundary = params.get(b"boundary")
    if not boundary:
        raise BadRequest(MSG_MISSING_BOUNDARY)
    charset = params.get(b"charset", b"utf-8").decode("latin-1")
    collector = _FormCollector(limits, charset)
    total = 0
    try:
        parser = MultipartParser(boundary, collector.callbacks())
        async with aclosing(request.stream()) as stream:
            async for chunk in stream:
                total += len(chunk)
                if total > limits.max_body_bytes:
                    raise PayloadTooLarge(MSG_BODY_TOO_LARGE)
                if chunk:
                    parser.write(chunk)
        parser.finalize()
    except FormParserError:
        raise BadRequest(MSG_INVALID_MULTIPART) from None
    if total and parser.state is not MultipartState.END:  # the closing boundary never arrived
        raise BadRequest(MSG_INVALID_MULTIPART)
    return collector.fields, collector.upload


async def _read_urlencoded(request: Request, limits: UploadLimits) -> list[tuple[str, str]]:
    body = await _read_capped(request, limits.max_body_bytes)
    try:
        pairs = parse_qsl(
            body.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=limits.max_fields
        )
    except ValueError:
        raise BadRequest(msg_too_many_fields(limits.max_fields)) from None
    for name, value in pairs:
        if len(name.encode()) > limits.max_field_bytes or len(value.encode()) > limits.max_field_bytes:
            raise PayloadTooLarge(msg_field_too_large(limits.max_field_bytes))
    return pairs


async def _read_form(request: Request, limits: UploadLimits) -> tuple[list[tuple[str, str]], Upload | None]:
    """Text fields (in order) and the resume file of the request; any other content type is an empty form."""
    content_type = request.headers.get("content-type", "")
    media, params = parse_options_header(content_type)
    if media.lower() == b"multipart/form-data":
        return await _read_multipart(request, params, limits)
    if media.lower() == b"application/x-www-form-urlencoded":
        return await _read_urlencoded(request, limits), None
    return [], None


def _last(fields: list[tuple[str, str]], name: str) -> str | None:
    """The value of the last field called ``name`` (a repeated field: the last one wins, as in Starlette)."""
    for key, value in reversed(fields):
        if key == name:
            return value
    return None


async def parse_analyze_form(request: Request, *, limits: UploadLimits = DEFAULT_LIMITS) -> AnalyzeRequest:
    """POST /api/analyze's form as an `AnalyzeRequest`, every cap enforced on the streamed bytes.

    Fields: ``resume`` (file), ``links`` (JSON array string or URLs separated by whitespace or commas, may repeat),
    ``goal`` (<= 300 characters), ``notes`` (<= 20 000), ``sample`` (``1`` or ``true``: the pre-computed sample
    profile, and the resume is ignored). Raises `PayloadTooLarge` (413) or `BadRequest` (400) with the texts
    described in the module docstring.
    """
    check_content_length(request, limits.max_body_bytes)
    fields, upload = await _read_form(request, limits)

    sample = _last(fields, "sample") in ("1", "true")
    links = parse_links([value for name, value in fields if name == "links"])
    goal = clean_field(_last(fields, "goal"), GOAL_MAX_CHARS)
    notes = clean_field(_last(fields, "notes"), NOTES_MAX_CHARS)
    # In sample mode the fixtures replace whatever was uploaded.
    resume = upload.data if upload is not None and not sample else None

    if not sample and resume is None and not usable_links(links) and not notes:
        raise BadRequest(MSG_BAD_LINKS if links else MSG_NO_INPUT)

    return AnalyzeRequest(
        resume_bytes=resume,
        resume_name=upload.filename if upload is not None and resume is not None else None,
        links=tuple(links),
        goal=goal,
        notes=notes,
        sample=sample,
    )


__all__ = [
    "DEFAULT_LIMITS",
    "FILENAME_MAX_CHARS",
    "MSG_BAD_FILE_TYPE",
    "MSG_BAD_LINKS",
    "MSG_BODY_TOO_LARGE",
    "MSG_INVALID_JSON",
    "MSG_INVALID_MULTIPART",
    "MSG_MISSING_BOUNDARY",
    "MSG_MISSING_NAME",
    "MSG_NO_INPUT",
    "MSG_WRONG_FILE_FIELD",
    "RESUME_FIELD",
    "Upload",
    "UploadLimits",
    "check_content_length",
    "clean_field",
    "clean_filename",
    "is_accepted_upload",
    "msg_field_too_large",
    "msg_file_too_large",
    "msg_too_many_fields",
    "msg_too_many_files",
    "parse_analyze_form",
    "parse_links",
    "read_json_body",
]
