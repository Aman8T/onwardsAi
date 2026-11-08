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

Time and memory are bounded as well as size (all optional arguments of `parse_analyze_form`, wired by the route):

    read_timeout_s   a deadline for the whole body read: a client that trickles or stalls gets 408
                     ``The upload took too long.`` instead of pinning its buffers for ever
    gate             an `UploadGate`: a process-wide budget of body bytes being buffered at once (503 when
                     it is spent) and at most one upload being read per client (429)

The text encoding of the form is never taken on trust. A ``charset`` parameter on the multipart Content-Type
is looked up in a short allow-list (UTF-8, ASCII, ISO-8859-1 / Latin-1, Windows-1252) and mapped to a fixed
codec name; anything else is a 400, and the client's label never reaches ``codecs`` (some codecs, such as
``punycode``, take quadratic time, and ``undefined`` raises). Text is decoded with ``errors="replace"``.

`read_json_body(request, max_bytes)` is the same idea for JSON bodies (POST /api/chat).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, MultipartState, parse_options_header
from starlette.requests import Request

from tayyari.contract.requests import AnalyzeRequest
from tayyari.core.errors import AppError, BadRequest, PayloadTooLarge, RateLimited, ServiceUnavailable
from tayyari.core.files import is_accepted_upload
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
)
from tayyari.core.logging import get_logger
from tayyari.core.settings import GuardSettings
from tayyari.ingest.classify import usable_links

if TYPE_CHECKING:
    from python_multipart.multipart import MultipartCallbacks

log = get_logger(__name__)

RESUME_FIELD = "resume"
FILENAME_MAX_CHARS = 255

MSG_BODY_TOO_LARGE = "Request body too large"
MSG_INVALID_JSON = "Invalid JSON body"
MSG_UPLOAD_TIMEOUT = "The upload took too long."
MSG_UPLOAD_IN_PROGRESS = "You already have an upload in progress. Wait for it to finish and try again."
MSG_UPLOADS_BUSY = "Leafout is receiving a lot of uploads right now. Please try again in a moment."
MSG_BAD_CHARSET = "Unsupported text encoding. Send the form as UTF-8."
MSG_NOT_JSON = "Send the message as application/json."
UPLOAD_RETRY_AFTER_S = 5.0
"""``Retry-After`` for a refused upload (another one of the client's is in progress, or the memory budget is spent)."""
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


# ───────────────────────── time and memory bounds ─────────────────────────


@asynccontextmanager
async def body_deadline(seconds: float | None) -> AsyncIterator[None]:
    """Give the block ``seconds`` to read a request body; past that the answer is 408 ``The upload took too long.``

    ``None`` means no deadline. Only the deadline's own expiry becomes the 408: any other error passes through.
    """
    if seconds is None:
        yield
        return
    deadline = asyncio.timeout(seconds)
    try:
        async with deadline:
            yield
    except TimeoutError:
        if not deadline.expired():
            raise
        log.info("upload_timeout", seconds=seconds)
        raise AppError(MSG_UPLOAD_TIMEOUT, code="upload_timeout", status=408) from None


class BodyTicket:
    """What one request body has taken from its `UploadGate`'s byte budget so far."""

    __slots__ = ("_gate", "taken")

    def __init__(self, gate: UploadGate) -> None:
        self._gate = gate
        self.taken = 0

    def add(self, size: int) -> None:
        """Count ``size`` more buffered bytes, or raise `ServiceUnavailable` (503) when the budget is spent."""
        gate = self._gate
        if gate.max_inflight_bytes > 0 and gate.inflight_bytes + size > gate.max_inflight_bytes:
            log.warning(
                "upload_budget_spent",
                inflight_bytes=gate.inflight_bytes,
                budget_bytes=gate.max_inflight_bytes,
            )
            raise ServiceUnavailable(
                MSG_UPLOADS_BUSY, code="upload_budget", retry_after_s=UPLOAD_RETRY_AFTER_S
            )
        gate.inflight_bytes += size
        self.taken += size


class UploadGate:
    """Process-wide limits on request bodies that are being read, so half-sent uploads cannot pin memory.

    - ``max_inflight_bytes``: body bytes buffered across all uploads at once, reserved as they arrive and
      given back when the read ends (finished, refused or abandoned). Past it a read stops with 503. 0 = no budget.
    - ``one_per_client``: a client key (the ``ip_hash``) may have one upload being read at a time; a second one
      is a 429. A ``None`` key is not limited (the address is unknown, and one shared slot would serialise everyone).

    One instance per process (kept on ``app.state``). There is no await between a check and its update, so the
    counters need no lock.
    """

    def __init__(self, *, max_inflight_bytes: int = 0, one_per_client: bool = True) -> None:
        self.max_inflight_bytes = max_inflight_bytes
        self.one_per_client = one_per_client
        self.inflight_bytes = 0
        self._clients: set[str] = set()

    @property
    def active_clients(self) -> int:
        """How many clients have an upload being read right now."""
        return len(self._clients)

    @asynccontextmanager
    async def reading(self, client_key: str | None) -> AsyncIterator[BodyTicket]:
        """Hold this client's upload slot and byte budget for the duration of the block."""
        held: str | None = None
        if self.one_per_client and client_key is not None:
            if client_key in self._clients:
                log.info("upload_refused", reason="client_has_upload_in_progress")
                raise RateLimited(
                    MSG_UPLOAD_IN_PROGRESS, code="upload_in_progress", retry_after_s=UPLOAD_RETRY_AFTER_S
                )
            self._clients.add(client_key)
            held = client_key
        ticket = BodyTicket(self)
        try:
            yield ticket
        finally:
            self.inflight_bytes = max(0, self.inflight_bytes - ticket.taken)
            if held is not None:
                self._clients.discard(held)


_GATE_ATTR = "upload_gate"


def upload_gate(request: Request, guard: GuardSettings) -> UploadGate:
    """The process's `UploadGate`: ``app.state.upload_gate``, built from the guard settings on first use.

    The budget and the one-upload-per-client rule follow ``guard.upload_inflight_bytes`` and ``guard.enabled``
    (the whole guard switched off means neither applies).
    """
    state = request.app.state
    gate = getattr(state, _GATE_ATTR, None)
    if not isinstance(gate, UploadGate):
        gate = UploadGate(
            max_inflight_bytes=guard.upload_inflight_bytes if guard.enabled else 0,
            one_per_client=guard.enabled,
        )
        setattr(state, _GATE_ATTR, gate)
    return gate


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


async def _read_capped(request: Request, limit: int, ticket: BodyTicket | None = None) -> bytes:
    """The whole body, refusing as soon as more than ``limit`` bytes have arrived."""
    check_content_length(request, limit)
    chunks: list[bytes] = []
    size = 0
    async with aclosing(request.stream()) as stream:
        async for chunk in stream:
            size += len(chunk)
            if size > limit:
                raise PayloadTooLarge(MSG_BODY_TOO_LARGE)
            if ticket is not None:
                ticket.add(len(chunk))
            chunks.append(chunk)
    return b"".join(chunks)


def is_json_content_type(request: Request) -> bool:
    """True when the request says it carries ``application/json`` (parameters such as a charset are fine)."""
    media, _ = parse_options_header(request.headers.get("content-type", ""))
    return media.lower() == b"application/json"


def require_json_content_type(request: Request) -> None:
    """415 ``Send the message as application/json.`` for any other Content-Type.

    A cross-site page can send ``text/plain`` (and form types) without a CORS preflight; JSON it cannot.
    """
    if not is_json_content_type(request):
        raise AppError(MSG_NOT_JSON, code="unsupported_media_type", status=415)


async def read_capped_body(request: Request, max_bytes: int, *, read_timeout_s: float | None = None) -> bytes:
    """The raw request body: 413 past ``max_bytes`` (counted on the stream, whatever Content-Length says or omits), 408
    when ``read_timeout_s`` runs out."""
    async with body_deadline(read_timeout_s):
        return await _read_capped(request, max_bytes)


async def read_json_body(
    request: Request, max_bytes: int = MAX_CHAT_BODY_BYTES, *, read_timeout_s: float | None = None
) -> Any:
    """The request body parsed as JSON. 413 ``Request body too large`` past ``max_bytes`` (counted on the
    stream as it arrives), 408 when ``read_timeout_s`` runs out, 400 ``Invalid JSON body`` when it is not JSON."""
    async with body_deadline(read_timeout_s):
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


def clean_filename(name: str) -> str:
    """The base name only (no directories), without control characters, at most 255 characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(ch for ch in base if ch.isprintable()).strip()[:FILENAME_MAX_CHARS]


_FORM_CHARSETS: dict[str, str] = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "us-ascii": "ascii",
    "ascii": "ascii",
    "iso-8859-1": "latin-1",
    "iso8859-1": "latin-1",
    "latin-1": "latin-1",
    "latin1": "latin-1",
    "windows-1252": "cp1252",
    "cp1252": "cp1252",
}
"""The ``charset`` labels a multipart form may name, each mapped to the fixed codec that decodes it."""


def form_codec(params: dict[bytes, bytes]) -> str:
    """The codec for the form's text: UTF-8 unless the Content-Type names one of `_FORM_CHARSETS`.

    A label outside the list is a 400. It is looked up in a dict and never handed to ``codecs``: the codec
    registry holds a quadratic decoder (``punycode``) and one that always raises (``undefined``).
    """
    raw = params.get(b"charset")
    if raw is None:
        return "utf-8"
    label = raw.decode("ascii", "replace").strip().strip("\"'").strip().lower()
    codec = _FORM_CHARSETS.get(label)
    if codec is None:
        raise BadRequest(MSG_BAD_CHARSET)
    return codec


def _decode(data: bytes | bytearray, codec: str) -> str:
    """``data`` as text in one of the fixed codecs of `_FORM_CHARSETS`; bytes that do not fit become U+FFFD."""
    try:
        return data.decode(codec, "replace")
    except (
        UnicodeError,
        LookupError,
    ):  # not reachable with the fixed codecs; never a 500 if one is added wrongly
        raise BadRequest(MSG_BAD_CHARSET) from None


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

    def __init__(self, limits: UploadLimits, codec: str, ticket: BodyTicket | None = None) -> None:
        self.limits = limits
        self.codec = codec
        self.ticket = ticket
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
        part.name = _decode(raw_name, self.codec)
        if b"filename" in options:
            part.is_file = True
            part.filename = clean_filename(_decode(options[b"filename"], self.codec))
            raw_type = part.headers.get(b"content-type")
            part.content_type = raw_type.decode("latin-1") if raw_type is not None else None
            if part.filename:
                self._count_file()
                self._check_file(part)
            else:
                # A browser's empty file input. It counts as a field until its content is known (below), so a
                # body made of nothing but such parts is stopped by the field limit like any other flood.
                part.deferred = True
                self._count_field()
        else:
            self._count_field()

    def _count_field(self) -> None:
        self._fields_seen += 1
        if self._fields_seen > self.limits.max_fields:
            raise BadRequest(msg_too_many_fields(self.limits.max_fields))

    def _count_file(self) -> None:
        self._files_seen += 1
        if self._files_seen > self.limits.max_files:
            raise BadRequest(msg_too_many_files(self.limits.max_files))

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
                if part.size == 0:  # an empty file input: nothing was attached; it stays counted as a field
                    return
                self._fields_seen -= 1  # it does carry data: a file after all
                self._count_file()
                self._check_file(part)
            self.upload = Upload(part.filename, part.content_type, bytes(part.data))
        else:
            self.fields.append((part.name, _decode(part.data, self.codec)))

    @staticmethod
    def _check_file(part: _Part) -> None:
        if part.name != RESUME_FIELD:
            raise BadRequest(MSG_WRONG_FILE_FIELD)
        if not is_accepted_upload(part.filename, part.content_type):
            raise BadRequest(MSG_BAD_FILE_TYPE)


async def _read_multipart(
    request: Request, params: dict[bytes, bytes], limits: UploadLimits, ticket: BodyTicket | None = None
) -> tuple[list[tuple[str, str]], Upload | None]:
    boundary = params.get(b"boundary")
    if not boundary:
        raise BadRequest(MSG_MISSING_BOUNDARY)
    collector = _FormCollector(limits, form_codec(params), ticket)
    total = 0
    try:
        parser = MultipartParser(boundary, collector.callbacks())
        async with aclosing(request.stream()) as stream:
            async for chunk in stream:
                total += len(chunk)
                if total > limits.max_body_bytes:
                    raise PayloadTooLarge(MSG_BODY_TOO_LARGE)
                if chunk:
                    if ticket is not None:
                        ticket.add(len(chunk))
                    parser.write(chunk)
        parser.finalize()
    except FormParserError:
        raise BadRequest(MSG_INVALID_MULTIPART) from None
    if total and parser.state is not MultipartState.END:  # the closing boundary never arrived
        raise BadRequest(MSG_INVALID_MULTIPART)
    return collector.fields, collector.upload


async def _read_urlencoded(
    request: Request, limits: UploadLimits, ticket: BodyTicket | None = None
) -> list[tuple[str, str]]:
    body = await _read_capped(request, limits.max_body_bytes, ticket)
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


async def _read_form(
    request: Request, limits: UploadLimits, ticket: BodyTicket | None = None
) -> tuple[list[tuple[str, str]], Upload | None]:
    """Text fields (in order) and the resume file of the request; any other content type is an empty form."""
    content_type = request.headers.get("content-type", "")
    media, params = parse_options_header(content_type)
    if media.lower() == b"multipart/form-data":
        return await _read_multipart(request, params, limits, ticket)
    if media.lower() == b"application/x-www-form-urlencoded":
        return await _read_urlencoded(request, limits, ticket), None
    return [], None


def _last(fields: list[tuple[str, str]], name: str) -> str | None:
    """The value of the last field called ``name`` (a repeated field: the last one wins, as in Starlette)."""
    for key, value in reversed(fields):
        if key == name:
            return value
    return None


async def read_upload_form(
    request: Request,
    *,
    limits: UploadLimits = DEFAULT_LIMITS,
    read_timeout_s: float | None = None,
    gate: UploadGate | None = None,
    client_key: str | None = None,
) -> tuple[list[tuple[str, str]], Upload | None]:
    """The text fields (in order) and the one resume file of a multipart or urlencoded request, every cap enforced on
    the streamed bytes and the deadline and `UploadGate` applied as `parse_analyze_form` describes. The file's name and
    type have been checked (`is_accepted_upload`); what the fields mean is the caller's business."""
    check_content_length(request, limits.max_body_bytes)
    async with body_deadline(read_timeout_s):
        if gate is None:
            return await _read_form(request, limits)
        async with gate.reading(client_key) as ticket:
            return await _read_form(request, limits, ticket)


async def parse_analyze_form(
    request: Request,
    *,
    limits: UploadLimits = DEFAULT_LIMITS,
    read_timeout_s: float | None = None,
    gate: UploadGate | None = None,
    client_key: str | None = None,
) -> AnalyzeRequest:
    """POST /api/analyze's form as an `AnalyzeRequest`, every cap enforced on the streamed bytes.

    Fields: ``resume`` (file), ``links`` (JSON array string or URLs separated by whitespace or commas, may repeat),
    ``goal`` (<= 300 characters), ``notes`` (<= 20 000), ``sample`` (``1`` or ``true``: the pre-computed sample
    profile, and the resume is ignored). Raises `PayloadTooLarge` (413) or `BadRequest` (400) with the texts
    described in the module docstring.

    ``read_timeout_s`` is the deadline for reading the whole body (408). ``gate`` and ``client_key`` (the
    caller's ``ip_hash``) apply an `UploadGate`: 429 when that client already has an upload being read, 503
    when the process-wide byte budget is spent. All three default to "off", for tests and tools.
    """
    fields, upload = await read_upload_form(
        request, limits=limits, read_timeout_s=read_timeout_s, gate=gate, client_key=client_key
    )

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
    "MSG_BAD_CHARSET",
    "MSG_BAD_FILE_TYPE",
    "MSG_BAD_LINKS",
    "MSG_BODY_TOO_LARGE",
    "MSG_INVALID_JSON",
    "MSG_INVALID_MULTIPART",
    "MSG_MISSING_BOUNDARY",
    "MSG_MISSING_NAME",
    "MSG_NOT_JSON",
    "MSG_NO_INPUT",
    "MSG_UPLOADS_BUSY",
    "MSG_UPLOAD_IN_PROGRESS",
    "MSG_UPLOAD_TIMEOUT",
    "MSG_WRONG_FILE_FIELD",
    "RESUME_FIELD",
    "UPLOAD_RETRY_AFTER_S",
    "BodyTicket",
    "Upload",
    "UploadGate",
    "UploadLimits",
    "body_deadline",
    "check_content_length",
    "clean_field",
    "clean_filename",
    "form_codec",
    "is_accepted_upload",
    "is_json_content_type",
    "msg_field_too_large",
    "msg_file_too_large",
    "msg_too_many_fields",
    "msg_too_many_files",
    "parse_analyze_form",
    "parse_links",
    "read_capped_body",
    "read_json_body",
    "read_upload_form",
    "require_json_content_type",
    "upload_gate",
]
