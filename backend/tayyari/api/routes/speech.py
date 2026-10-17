"""Speech for practice interviews through Sarvam AI: whether it is on, a spoken answer written down, a question read aloud.

    GET  /api/speech/status?profileId=&sessionId=                  `SpeechStatus` (``sessionId`` is optional: Setup asks before
                                                                   a session exists, and then only the day's budget counts)
    POST /api/speech/transcribe?profileId=&sessionId=&seconds=     the raw audio of one piece of an answer -> `TranscribeResult`
    POST /api/speech/speak                                         `SpeakRequest` -> ``audio/mpeg`` (one interviewer line)

A profile is opened exactly as the practice routes open it (`api.routes.interview`): one nobody has claimed or a sample run is
open to whoever holds its id, a claimed one only to its owner's session. A session id must be 32 hexadecimal characters and
belong to that profile, or the answer is the same 404 an unknown one gets. Every answer is ``Cache-Control: no-store``.

Transcribing, in this order: ``shed`` (the in-memory ``chat`` bucket, before anything is read) -> practice switched off (503) ->
the audio type (415) and the ``seconds`` it claims (400) -> the profile and the session (404; 409 when the interview has ended)
-> Sarvam speech off or its budget used (503, with the sentence the room shows) -> the client's ``speech`` window (429 with
``Retry-After``) -> the audio, read with a byte cap counted as it arrives (413, or 408 when it trickles) -> Sarvam (400 when it
cannot use the piece, 503 when it cannot be reached). The piece is held for the call and dropped: nothing is stored or logged
of it, and the text is logged nowhere here.

Speaking, in this order: ``shed`` (the ``speech`` bucket) -> practice switched off (503) -> the body (415 / 413 / 408 / 400) ->
the profile and the session (404) -> the text must be one of the interview's own lines (404 "That line isn't part of this
interview.", so the voice reads nothing else) -> the ``speech`` window (429) -> the speech
service, which serves a line read before from memory (no credits spent, even when the budget is used up), else needs Sarvam
speech on and its budget (503) and then calls Sarvam.

The speech window is 40 requests a minute per address (`GuardSettings.speech_per_minute`); Sarvam's credits have their own
soft budget (`SpeechSettings`, `speech.meter`). Nothing is charged to an account and no model is called.
"""

from __future__ import annotations

import math
from contextlib import aclosing
from typing import Final, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from tayyari.api.deps import get_request_context
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.routes.interview import SESSION_NOT_FOUND, _profile, _require_hex
from tayyari.api.security import subnet_hash
from tayyari.api.uploads import body_deadline
from tayyari.container import Container
from tayyari.contract.requests import RequestContext
from tayyari.contract.speech import SpeakRequest
from tayyari.core.errors import AppError, BadRequest, NotFound, PayloadTooLarge
from tayyari.interview.errors import InterviewEnded
from tayyari.interview.speech import SpeechContext
from tayyari.speech.engine import codec_for, media_type
from tayyari.speech.service import TOO_LARGE_MESSAGE

router = APIRouter()

NO_STORE: Final = {"Cache-Control": "no-store"}
SPEAK_BODY_MAX_BYTES: Final = 12 * 1024
"""A speak request holds two ids and at most 1,200 characters of text; JSON may spell one as up to six bytes."""
MAX_CLAIMED_SECONDS: Final = 3600.0
LINE_NOT_FOUND: Final = "That line isn't part of this interview."
UNSUPPORTED_AUDIO: Final = "Send the piece as audio/webm, audio/ogg, audio/mp4, audio/mpeg or audio/wav."
"""The sentence of the 415 for a body that is not an audio type we take."""


def _shed(container: Container, rc: RequestContext, bucket: Literal["chat", "speech"]) -> None:
    """Turn hammering away before anything is read (the in-memory bucket only; no database)."""
    container.rate_limiter.shed(
        bucket, rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )


async def _count(container: Container, rc: RequestContext, *, memory: bool) -> None:
    """Count one request in the client's ``speech`` window (429 with ``Retry-After`` past it)."""
    await container.rate_limiter.check(
        "speech",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        memory=memory,
        globally=False,
    )


async def _context(
    request: Request, container: Container, profile_id: str, session_id: str
) -> tuple[str, SpeechContext]:
    """The session's speech context for a profile this caller may open; the 404 an unknown session gets otherwise."""
    _require_hex(session_id)
    profile = await _profile(request, container, profile_id)
    context = await container.interview.speech_context(profile, session_id)
    if context is None:
        raise NotFound(SESSION_NOT_FOUND)
    return session_id, context


def _claimed_seconds(raw: str | None) -> float:
    """The seconds of audio the browser says the piece runs (0 when it does not say)."""
    if raw is None or not raw.strip():
        return 0.0
    try:
        seconds = float(raw)
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        raise BadRequest("Invalid request: seconds is not a number of seconds")
    return min(seconds, MAX_CLAIMED_SECONDS)


async def _read_audio(request: Request, limit: int, timeout_s: float) -> bytes:
    """The whole body, refusing as soon as more than ``limit`` bytes have arrived (413) or when it takes too long (408)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLarge(TOO_LARGE_MESSAGE)
    chunks: list[bytes] = []
    size = 0
    async with body_deadline(timeout_s), aclosing(request.stream()) as stream:
        async for chunk in stream:
            size += len(chunk)
            if size > limit:
                raise PayloadTooLarge(TOO_LARGE_MESSAGE)
            chunks.append(chunk)
    return b"".join(chunks)


# ───────────────────────── status ─────────────────────────


@router.get("/api/speech/status")
async def speech_status(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc, "speech")
    params = request.query_params
    session_id = params.get("sessionId")
    if session_id:
        await _context(request, container, params.get("profileId", ""), session_id)
    else:
        await _profile(request, container, params.get("profileId", ""))
    status = await container.speech.status(session_id or None)
    return JSONResponse(status.to_dict(), headers=NO_STORE)


# ───────────────────────── a spoken answer, written down ─────────────────────────


@router.post("/api/speech/transcribe")
async def transcribe_piece(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc, "chat")
    container.interview.require_enabled()
    params = request.query_params
    mime = media_type(request.headers.get("content-type", ""))
    if codec_for(mime) is None:
        raise AppError(UNSUPPORTED_AUDIO, code="unsupported_media_type", status=415)
    seconds = _claimed_seconds(params.get("seconds"))
    session_id, context = await _context(
        request, container, params.get("profileId", ""), params.get("sessionId", "")
    )
    if not context.active:
        raise InterviewEnded()
    await container.speech.ensure("stt", session_id)
    await _count(container, rc, memory=True)
    audio = await _read_audio(
        request, container.settings.speech.max_segment_bytes, container.settings.guard.upload_read_timeout_s
    )
    result = await container.speech.transcribe(session_id, audio, mime, seconds, context.keyterms)
    return JSONResponse(result.to_dict(), headers=NO_STORE)


# ───────────────────────── an interviewer's line, read aloud ─────────────────────────


@router.post("/api/speech/speak")
async def speak_line(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed(container, rc, "speech")
    container.interview.require_enabled()
    body = await read_model(request, container, SpeakRequest, max_bytes=SPEAK_BODY_MAX_BYTES)
    session_id, context = await _context(request, container, body.profileId, body.sessionId)
    if not context.allows(body.text):
        raise NotFound(LINE_NOT_FOUND)
    await _count(container, rc, memory=False)
    audio = await container.speech.speak(session_id, body.text, body.pace)
    return Response(content=audio, media_type="audio/mpeg", headers=NO_STORE)


__all__ = ["LINE_NOT_FOUND", "UNSUPPORTED_AUDIO", "router"]
