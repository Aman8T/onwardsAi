"""Regression tests for the upload findings of the security review (#1, #4, #19, #20, and the chat Content-Type).

charset       a client-chosen multipart charset never reaches ``codecs`` (a 200 KB field under ``punycode``
              used to cost over a second of event-loop time; ``undefined`` raised an uncaught UnicodeError)
deadline      a body that stalls or trickles is cut off with a 408 instead of pinning its buffers
gate          a process-wide budget of buffered upload bytes (503) and one upload being read per client (429)
empty parts   file parts with an empty file name count toward the field limit
content type  chat accepts only application/json (415)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tayyari.api import uploads
from tayyari.api.errors import install_error_handlers
from tayyari.api.uploads import (
    MSG_BAD_CHARSET,
    MSG_NOT_JSON,
    MSG_UPLOAD_IN_PROGRESS,
    MSG_UPLOAD_TIMEOUT,
    MSG_UPLOADS_BUSY,
    UPLOAD_RETRY_AFTER_S,
    UploadGate,
    UploadLimits,
    body_deadline,
    form_codec,
    is_json_content_type,
    parse_analyze_form,
    read_json_body,
    require_json_content_type,
    upload_gate,
)
from tayyari.core.errors import AppError, BadRequest, PayloadTooLarge, RateLimited, ServiceUnavailable
from tayyari.core.settings import GuardSettings
from tests.unit.api.helpers import (
    KIB,
    Pulled,
    eventually,
    make_request,
    make_stalling_request,
    multipart_body,
    split,
)

BOUNDARY = "b"
MULTIPART = f"multipart/form-data; boundary={BOUNDARY}"
NOTES_HEAD = b'--b\r\nContent-Disposition: form-data; name="notes"\r\n\r\n'
CLOSE = b"\r\n--b--\r\n"
SMALL = UploadLimits(
    max_body_bytes=64 * KIB, max_resume_bytes=8 * KIB, max_field_bytes=1 * KIB, max_fields=5, max_files=1
)


def notes_body(text: bytes) -> bytes:
    """A whole multipart body (boundary ``b``) with one ``notes`` field holding the raw bytes ``text``."""
    return NOTES_HEAD + text + CLOSE


async def parse_bytes(body: bytes, content_type: str = MULTIPART, **kwargs: Any) -> Any:
    return await parse_analyze_form(make_request(split(body, 4096), content_type=content_type), **kwargs)


# ───────────────────────────── #1 / #20: the charset ─────────────────────────────


async def test_a_punycode_charset_is_refused_in_milliseconds() -> None:
    """The review's repro: a 200 KB field and ``charset=punycode`` took about 1.3 s of CPU on the event loop."""
    body = notes_body(b"a" * 200_000)
    started = time.perf_counter()
    with pytest.raises(BadRequest) as info:
        await parse_bytes(body, MULTIPART + "; charset=punycode")
    assert time.perf_counter() - started < 0.1
    assert info.value.user_message == MSG_BAD_CHARSET


@pytest.mark.parametrize(
    "label",
    [
        "punycode",
        "undefined",
        "utf-16",
        "utf-7",
        "shift_jis",
        "idna",
        "rot13",
        "unicode_escape",
        "nonsense",
        "",
        "../x",
    ],
)
async def test_a_charset_outside_the_allow_list_is_a_400_never_a_500(label: str) -> None:
    started = time.perf_counter()
    with pytest.raises(BadRequest) as info:
        await parse_bytes(notes_body(b"hello"), f"{MULTIPART}; charset={label}")
    assert time.perf_counter() - started < 0.1
    assert info.value.user_message == MSG_BAD_CHARSET


@pytest.mark.parametrize(
    ("label", "codec"),
    [
        (None, "utf-8"),
        ("utf-8", "utf-8"),
        ("UTF-8", "utf-8"),
        ('"utf-8"', "utf-8"),
        ("utf8", "utf-8"),
        ("us-ascii", "ascii"),
        ("ISO-8859-1", "latin-1"),
        ("latin1", "latin-1"),
        ("windows-1252", "cp1252"),
        ("CP1252", "cp1252"),
    ],
)
def test_the_allow_listed_charsets_map_to_fixed_codec_names(label: str | None, codec: str) -> None:
    params = {} if label is None else {b"charset": label.encode()}
    assert form_codec(params) == codec


async def test_no_client_label_ever_reaches_the_codec_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever the form says, text is only ever decoded with one of the fixed codec names."""
    seen: set[str] = set()
    real = uploads._decode

    def spy(data: bytes | bytearray, codec: str) -> str:
        seen.add(codec)
        return real(data, codec)

    monkeypatch.setattr(uploads, "_decode", spy)
    for label in ("latin1", "UTF8", "cp1252", "us-ascii"):
        await parse_bytes(notes_body(b"hello"), f"{MULTIPART}; charset={label}")
    assert seen == {"utf-8", "latin-1", "cp1252", "ascii"}


async def test_latin1_and_cp1252_forms_decode_with_their_own_codec() -> None:
    latin = await parse_bytes(notes_body("café".encode("latin-1")), MULTIPART + "; charset=iso-8859-1")
    assert latin.notes == "café"
    euro = await parse_bytes(notes_body(b"price \x80 100"), MULTIPART + "; charset=windows-1252")
    assert euro.notes == "price € 100"
    ascii_form = await parse_bytes(notes_body(b"plain"), MULTIPART + "; charset=us-ascii")
    assert ascii_form.notes == "plain"


async def test_bytes_that_do_not_fit_the_charset_are_replaced_not_fatal() -> None:
    utf8 = await parse_bytes(notes_body(b"bad \xff\xfe bytes"))
    assert utf8.notes == "bad �� bytes"
    ascii_form = await parse_bytes(notes_body(b"caf\xe9"), MULTIPART + "; charset=us-ascii")
    assert ascii_form.notes == "caf�"


async def test_a_bad_charset_is_refused_before_the_body_is_read() -> None:
    pulled = Pulled()
    request = make_request([b"x" * 4096] * 100, content_type=MULTIPART + "; charset=punycode", pulled=pulled)
    with pytest.raises(BadRequest):
        await parse_analyze_form(request)
    assert pulled.bytes == 0


def _app(*, timeout_s: float | None = None, gate: UploadGate | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)

    @app.post("/api/analyze")
    async def analyze(request: Request) -> JSONResponse:
        req = await parse_analyze_form(request, read_timeout_s=timeout_s, gate=gate, client_key="ip-1")
        return JSONResponse({"notes": req.notes})

    @app.post("/api/chat")
    async def chat(request: Request) -> JSONResponse:
        require_json_content_type(request)
        return JSONResponse(await read_json_body(request, read_timeout_s=timeout_s))

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_http_charset_undefined_is_a_400_json_not_a_500() -> None:
    async with _client(_app()) as client:
        res = await client.post(
            "/api/analyze",
            content=notes_body(b"hello"),
            headers={"content-type": MULTIPART + "; charset=undefined"},
        )
    assert res.status_code == 400
    assert res.json() == {"error": MSG_BAD_CHARSET}


# ───────────────────────────── #4: the read deadline ─────────────────────────────


async def test_a_stalled_upload_is_a_408() -> None:
    request = make_stalling_request([NOTES_HEAD + b"half a fie"], content_type=MULTIPART)
    started = time.perf_counter()
    with pytest.raises(AppError) as info:
        await parse_analyze_form(request, read_timeout_s=0.05)
    assert time.perf_counter() - started < 2
    assert info.value.status == 408
    assert info.value.user_message == MSG_UPLOAD_TIMEOUT == "The upload took too long."


async def test_a_trickling_upload_is_cut_off_by_the_total_deadline() -> None:
    """Bytes keep arriving, but the whole read has a deadline, so slow-and-steady does not get round it."""

    async def trickle() -> AsyncIterator[bytes]:
        yield NOTES_HEAD
        for _ in range(1000):
            await asyncio.sleep(0.01)
            yield b"x"

    async with _client(_app(timeout_s=0.1)) as client:
        res = await client.post("/api/analyze", content=trickle(), headers={"content-type": MULTIPART})
    assert res.status_code == 408
    assert res.json() == {"error": MSG_UPLOAD_TIMEOUT}


async def test_a_timely_upload_is_not_affected_by_the_deadline() -> None:
    req = await parse_bytes(notes_body(b"in time"), read_timeout_s=5)
    assert req.notes == "in time"


async def test_a_stalled_json_body_is_a_408_too() -> None:
    request = make_stalling_request(
        [b'{"profileId": "p", '], content_type="application/json", path="/api/chat"
    )
    with pytest.raises(AppError) as info:
        await read_json_body(request, read_timeout_s=0.05)
    assert info.value.status == 408


async def test_http_stalled_chat_body_is_a_408_json() -> None:
    async def stall() -> AsyncIterator[bytes]:
        yield b'{"profileId":'
        await asyncio.sleep(30)
        yield b' "p"}'  # pragma: no cover

    async with _client(_app(timeout_s=0.1)) as client:
        res = await client.post("/api/chat", content=stall(), headers={"content-type": "application/json"})
    assert res.status_code == 408
    assert res.json() == {"error": MSG_UPLOAD_TIMEOUT}


async def test_the_deadline_does_not_turn_other_timeouts_into_a_408() -> None:
    with pytest.raises(TimeoutError):
        async with body_deadline(60):
            raise TimeoutError("somebody else's")


async def test_no_deadline_means_none() -> None:
    async with body_deadline(None):
        await asyncio.sleep(0.01)


# ───────────────────────────── #4: the byte budget ─────────────────────────────


async def test_an_upload_past_the_budget_is_a_503_and_gives_its_bytes_back() -> None:
    gate = UploadGate(max_inflight_bytes=20 * KIB)
    body = notes_body(b"n" * (30 * KIB))
    big = UploadLimits(max_field_bytes=64 * KIB)
    with pytest.raises(ServiceUnavailable) as info:
        await parse_bytes(body, gate=gate, client_key="a", limits=big)
    assert info.value.status == 503
    assert info.value.user_message == MSG_UPLOADS_BUSY
    assert info.value.retry_after_header() == str(int(UPLOAD_RETRY_AFTER_S))
    assert gate.inflight_bytes == 0
    assert gate.active_clients == 0


async def test_the_budget_is_shared_by_uploads_in_flight() -> None:
    gate = UploadGate(max_inflight_bytes=10 * KIB, one_per_client=False)
    release = asyncio.Event()
    first = make_stalling_request(
        [NOTES_HEAD + b"n" * (8 * KIB)], content_type=MULTIPART, release=release, tail=CLOSE
    )
    holder = asyncio.create_task(parse_analyze_form(first, gate=gate, client_key="a"))
    await eventually(lambda: gate.inflight_bytes >= 8 * KIB)
    # 8 KiB are buffered, so another 4 KiB would pass the 10 KiB budget
    with pytest.raises(ServiceUnavailable):
        await parse_bytes(notes_body(b"m" * (4 * KIB)), gate=gate, client_key="b")
    assert gate.inflight_bytes == 8 * KIB + len(NOTES_HEAD)  # the refused upload gave its bytes back
    # a small one still fits next to it
    assert (await parse_bytes(notes_body(b"tiny"), gate=gate, client_key="c")).notes == "tiny"
    release.set()
    assert (await holder).notes == "n" * (8 * KIB)
    assert gate.inflight_bytes == 0


async def test_an_abandoned_upload_gives_its_bytes_back() -> None:
    gate = UploadGate(max_inflight_bytes=64 * KIB)
    request = make_stalling_request([NOTES_HEAD + b"n" * KIB], content_type=MULTIPART)
    with pytest.raises(AppError):
        await parse_analyze_form(request, read_timeout_s=0.05, gate=gate, client_key="a")
    assert gate.inflight_bytes == 0
    assert gate.active_clients == 0


async def test_a_zero_budget_means_no_budget() -> None:
    gate = UploadGate(max_inflight_bytes=0)
    req = await parse_bytes(notes_body(b"n" * (200 * KIB)), gate=gate, client_key="a")
    assert req.notes is not None
    assert gate.inflight_bytes == 0


async def test_urlencoded_bodies_are_counted_too() -> None:
    gate = UploadGate(max_inflight_bytes=1 * KIB)
    body = b"notes=" + b"n" * (4 * KIB)
    request = make_request([body], content_type="application/x-www-form-urlencoded", content_length=len(body))
    with pytest.raises(ServiceUnavailable):
        await parse_analyze_form(request, gate=gate, client_key="a")
    assert gate.inflight_bytes == 0


# ───────────────────────────── #4: one upload per client ─────────────────────────────


async def test_a_second_upload_from_the_same_client_is_a_429_until_the_first_ends() -> None:
    gate = UploadGate()
    release = asyncio.Event()
    first = make_stalling_request([NOTES_HEAD + b"abc"], content_type=MULTIPART, release=release, tail=CLOSE)
    holder = asyncio.create_task(parse_analyze_form(first, gate=gate, client_key="ip-1"))
    await eventually(lambda: gate.active_clients >= 1)

    with pytest.raises(RateLimited) as info:
        await parse_bytes(notes_body(b"second"), gate=gate, client_key="ip-1")
    assert info.value.status == 429
    assert info.value.user_message == MSG_UPLOAD_IN_PROGRESS
    assert info.value.retry_after_header() == str(int(UPLOAD_RETRY_AFTER_S))
    # another client is not affected
    assert (await parse_bytes(notes_body(b"other"), gate=gate, client_key="ip-2")).notes == "other"

    release.set()
    assert (await holder).notes == "abc"
    assert (await parse_bytes(notes_body(b"again"), gate=gate, client_key="ip-1")).notes == "again"
    assert gate.active_clients == 0


async def test_the_client_slot_is_freed_when_the_upload_fails() -> None:
    gate = UploadGate()
    with pytest.raises(PayloadTooLarge):
        await parse_bytes(notes_body(b"n" * (2 * KIB)), gate=gate, client_key="ip-1", limits=SMALL)
    assert gate.active_clients == 0
    assert (await parse_bytes(notes_body(b"ok"), gate=gate, client_key="ip-1")).notes == "ok"


async def test_an_unknown_client_is_not_limited_to_one_upload() -> None:
    """With no ip_hash there is nobody to tell apart: one shared slot would serialise every visitor."""
    gate = UploadGate()
    release = asyncio.Event()
    first = make_stalling_request([NOTES_HEAD + b"abc"], content_type=MULTIPART, release=release, tail=CLOSE)
    holder = asyncio.create_task(parse_analyze_form(first, gate=gate, client_key=None))
    await asyncio.sleep(0.01)
    assert (await parse_bytes(notes_body(b"second"), gate=gate, client_key=None)).notes == "second"
    release.set()
    await holder


async def test_one_per_client_can_be_switched_off() -> None:
    gate = UploadGate(one_per_client=False)
    release = asyncio.Event()
    first = make_stalling_request([NOTES_HEAD + b"abc"], content_type=MULTIPART, release=release, tail=CLOSE)
    holder = asyncio.create_task(parse_analyze_form(first, gate=gate, client_key="ip-1"))
    await asyncio.sleep(0.01)
    assert (await parse_bytes(notes_body(b"second"), gate=gate, client_key="ip-1")).notes == "second"
    release.set()
    await holder


async def test_http_a_second_concurrent_upload_is_a_429_json() -> None:
    gate = UploadGate()
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow() -> AsyncIterator[bytes]:
        yield NOTES_HEAD + b"first"
        started.set()
        await release.wait()
        yield CLOSE

    async with _client(_app(gate=gate)) as client:
        first = asyncio.create_task(
            client.post("/api/analyze", content=slow(), headers={"content-type": MULTIPART})
        )
        await asyncio.wait_for(started.wait(), 5)
        second = await client.post(
            "/api/analyze", content=notes_body(b"second"), headers={"content-type": MULTIPART}
        )
        release.set()
        finished = await first
    assert second.status_code == 429
    assert second.json() == {"error": MSG_UPLOAD_IN_PROGRESS}
    assert second.headers["retry-after"] == str(int(UPLOAD_RETRY_AFTER_S))
    assert finished.status_code == 200
    assert finished.json() == {"notes": "first"}


# ───────────────────────────── upload_gate(): one per process ─────────────────────────────


def _request_with_state() -> Request:
    app = FastAPI()
    return Request({**make_request([]).scope, "app": app})


def test_the_gate_is_built_once_from_the_guard_settings() -> None:
    request = _request_with_state()
    guard = GuardSettings(upload_inflight_bytes=123_456)
    gate = upload_gate(request, guard)
    assert gate.max_inflight_bytes == 123_456
    assert gate.one_per_client is True
    assert upload_gate(request, guard) is gate
    assert request.app.state.upload_gate is gate


def test_a_disabled_guard_switches_the_budget_and_the_per_client_rule_off() -> None:
    gate = upload_gate(_request_with_state(), GuardSettings(enabled=False))
    assert gate.max_inflight_bytes == 0
    assert gate.one_per_client is False


# ───────────────────────────── #19: empty file names count ─────────────────────────────


def empty_file_part(name: str = "resume") -> bytes:
    return (
        f'--b\r\nContent-Disposition: form-data; name="{name}"; filename=""\r\n'
        "Content-Type: application/octet-stream\r\n\r\n\r\n"
    ).encode()


async def test_empty_filename_parts_count_toward_the_field_limit() -> None:
    body = b"".join(empty_file_part() for _ in range(SMALL.max_fields + 1)) + b"--b--\r\n"
    with pytest.raises(BadRequest, match=r"Too many fields\. Maximum number of fields is 5\."):
        await parse_bytes(body, limits=SMALL)


async def test_empty_filename_parts_and_fields_share_one_allowance() -> None:
    parts = [empty_file_part() for _ in range(3)]
    fine = b"".join(parts) + b'--b\r\nContent-Disposition: form-data; name="n1"\r\n\r\nx\r\n'
    fine += b'--b\r\nContent-Disposition: form-data; name="notes"\r\n\r\nok\r\n--b--\r\n'
    assert (await parse_bytes(fine, limits=SMALL)).notes == "ok"  # 3 + 2 = the 5 allowed
    too_many = b"".join(parts) + b'--b\r\nContent-Disposition: form-data; name="n1"\r\n\r\nx\r\n'
    too_many += b'--b\r\nContent-Disposition: form-data; name="n2"\r\n\r\nx\r\n'
    too_many += b'--b\r\nContent-Disposition: form-data; name="notes"\r\n\r\nok\r\n--b--\r\n'
    with pytest.raises(BadRequest, match="Too many fields"):
        await parse_bytes(too_many, limits=SMALL)


async def test_a_flood_of_empty_file_parts_is_stopped_after_a_handful() -> None:
    """The review's repro: 180,000 empty-filename parts (11 MB) were all parsed, 6.3 s of CPU."""
    part = empty_file_part()
    pulled = Pulled()

    def flood() -> Any:
        for _ in range(180_000):
            yield part

    request = make_request(flood(), content_type=MULTIPART, pulled=pulled)
    started = time.perf_counter()
    with pytest.raises(BadRequest, match="Too many fields"):
        await parse_analyze_form(request)
    assert time.perf_counter() - started < 0.5
    assert pulled.chunks <= 25  # the 21st part trips the default limit of 20 fields


async def test_an_empty_file_input_still_does_not_use_up_the_file_allowance() -> None:
    body = empty_file_part() + empty_file_part("other")
    body += (
        b'--b\r\nContent-Disposition: form-data; name="resume"; filename="cv.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\nmy resume\r\n--b--\r\n"
    )
    req = await parse_bytes(body, limits=SMALL)
    assert req.resume_bytes == b"my resume"


async def test_a_nameless_part_with_content_turns_out_to_be_a_file_not_a_field() -> None:
    """Counted as a field while its content is unknown, as a file once it has some: never as both."""
    fields = [(f"f{i}", "v") for i in range(3)]
    before, _ = multipart_body(fields, boundary=BOUNDARY)
    nameless = (
        b'--b\r\nContent-Disposition: form-data; name="resume"; filename=""\r\n'
        b"Content-Type: application/pdf\r\n\r\n%PDF-1.4 data\r\n"
    )
    after = b'--b\r\nContent-Disposition: form-data; name="f3"\r\n\r\nv\r\n'
    after += b'--b\r\nContent-Disposition: form-data; name="notes"\r\n\r\nhi\r\n--b--\r\n'
    body = before.removesuffix(b"--b--\r\n") + nameless + after
    req = await parse_bytes(body, limits=SMALL)  # 5 fields: f0-f2, f3, notes
    assert req.resume_bytes == b"%PDF-1.4 data"
    assert req.notes == "hi"


# ───────────────────────────── chat Content-Type ─────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("application/json", True),
        ("application/json; charset=utf-8", True),
        ("Application/JSON", True),
        ("text/plain", False),
        ("text/plain;charset=UTF-8", False),
        ("application/x-www-form-urlencoded", False),
        ("multipart/form-data; boundary=b", False),
        ("application/jsonp", False),
        ("application/json-seq", False),
        ("", False),
    ],
)
def test_only_application_json_is_json(value: str, expected: bool) -> None:
    request = make_request([b"{}"], content_type=value or None)
    assert is_json_content_type(request) is expected


def test_a_missing_content_type_is_not_json() -> None:
    assert is_json_content_type(make_request([b"{}"])) is False


async def test_http_a_non_json_content_type_is_a_415_json() -> None:
    async with _client(_app()) as client:
        plain = await client.post("/api/chat", content=b'{"a": 1}', headers={"content-type": "text/plain"})
        form = await client.post("/api/chat", data={"a": "1"})
        none = await client.post("/api/chat", content=b'{"a": 1}', headers={"content-type": ""})
        ok = await client.post("/api/chat", json={"a": 1})
    for res in (plain, form, none):
        assert res.status_code == 415
        assert res.json() == {"error": MSG_NOT_JSON}
    assert ok.status_code == 200
