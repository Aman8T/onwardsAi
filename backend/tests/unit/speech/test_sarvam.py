"""`SarvamEngine` over a stand-in for the SDK's client: what it asks Sarvam for, how every failure the SDK can raise (its own
error classes, httpx's, a timeout) becomes one of the two speech errors, and that the key, the audio and the text stay out
of the logs. Nothing here touches a network; the SDK's real error classes and response models are used.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sarvamai.core.api_error import ApiError
from sarvamai.errors import (
    BadGatewayError,
    BadRequestError,
    ContentTooLargeError,
    ForbiddenError,
    GatewayTimeoutError,
    InternalServerError,
    PaymentRequiredError,
    ServiceUnavailableError,
    TooManyRequestsError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from sarvamai.types import SpeechToTextResponse, TextToSpeechResponse

from tayyari.core.settings import SpeechSettings
from tayyari.speech import sarvam
from tayyari.speech.engine import REJECTED_MESSAGE, UNAVAILABLE_MESSAGE, SpeechRejected, SpeechUnavailable
from tayyari.speech.sarvam import NO_RETRIES, SarvamEngine, error_code
from tests.unit.api.helpers import RecordingLog

KEY = "sk_test_THE_SECRET_KEY_0123456789"
MP3_BYTES = b"ID3\x03\x00pretend-this-is-an-mp3"
AUDIO = b"\x1a\x45\xdf\xa3the-spoken-answer-bytes"
QUOTA = {
    "error": {
        "message": "Your account is out of credits",
        "code": "insufficient_quota_error",
        "request_id": "r1",
    }
}


@dataclass
class FakeStt:
    response: Any = field(
        default_factory=lambda: SpeechToTextResponse(transcript="hello there", language_code="en-IN")
    )
    error: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def transcribe(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


@dataclass
class FakeTts:
    response: Any = field(
        default_factory=lambda: TextToSpeechResponse(audios=[base64.b64encode(MP3_BYTES).decode("ascii")])
    )
    error: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def convert(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


@dataclass
class FakeSdk:
    speech_to_text: FakeStt = field(default_factory=FakeStt)
    text_to_speech: FakeTts = field(default_factory=FakeTts)


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    recorder = RecordingLog()
    monkeypatch.setattr(sarvam, "log", recorder)
    return recorder


def engine_over(sdk: FakeSdk, **settings: Any) -> SarvamEngine:
    return SarvamEngine(SecretStr(KEY), SpeechSettings(**settings), client=sdk)


# ───────────────────────── speech to text ─────────────────────────


async def test_a_piece_is_sent_as_one_file_with_the_model_language_codec_and_key_terms() -> None:
    sdk = FakeSdk()
    heard = await engine_over(sdk).transcribe(
        AUDIO, mime="audio/webm;codecs=opus", keyterms=["Kitebird", "LCP"]
    )
    assert (heard.text, heard.language_code) == ("hello there", "en-IN")
    (call,) = sdk.speech_to_text.calls
    assert call["file"] == ("piece.webm", AUDIO, "audio/webm")
    assert call["model"] == "saaras:v4" and call["language_code"] == "en-IN"
    assert call["input_audio_codec"] == "webm"
    assert call["keyterms"] == ["Kitebird", "LCP"]
    assert call["request_options"] == NO_RETRIES == {"max_retries": 0}


@pytest.mark.parametrize(
    ("mime", "codec", "name"),
    [
        ("audio/ogg;codecs=opus", "ogg", "piece.ogg"),
        ("audio/mp4", "mp4", "piece.mp4"),
        ("audio/x-m4a", "mp4", "piece.mp4"),
        ("audio/mpeg", "mp3", "piece.mp3"),
        ("audio/x-wav", "wav", "piece.wav"),
    ],
)
async def test_the_codec_follows_the_audio_type(mime: str, codec: str, name: str) -> None:
    sdk = FakeSdk()
    await engine_over(sdk).transcribe(AUDIO, mime=mime, keyterms=[])
    call = sdk.speech_to_text.calls[0]
    assert call["input_audio_codec"] == codec and call["file"][0] == name


async def test_no_key_terms_means_none_are_sent() -> None:
    sdk = FakeSdk()
    await engine_over(sdk).transcribe(AUDIO, mime="audio/webm", keyterms=["", "  "])
    assert "keyterms" not in sdk.speech_to_text.calls[0]


async def test_key_terms_are_cleaned_and_left_out_for_a_model_that_does_not_take_them() -> None:
    sdk = FakeSdk()
    await engine_over(sdk).transcribe(AUDIO, mime="audio/webm", keyterms=["a" * 70, " Kite  UI ", "kite ui"])
    assert sdk.speech_to_text.calls[0]["keyterms"] == ["Kite UI"]
    older = FakeSdk()
    await engine_over(older, stt_model="saaras:v3").transcribe(
        AUDIO, mime="audio/webm", keyterms=["Kitebird"]
    )
    assert "keyterms" not in older.speech_to_text.calls[0]  # the API accepts them for saaras:v4 only
    assert older.speech_to_text.calls[0]["model"] == "saaras:v3"


async def test_an_audio_type_we_do_not_take_is_rejected_without_calling_sarvam() -> None:
    sdk = FakeSdk()
    with pytest.raises(SpeechRejected):
        await engine_over(sdk).transcribe(AUDIO, mime="audio/flac", keyterms=[])
    assert sdk.speech_to_text.calls == []


async def test_a_missing_language_is_none() -> None:
    sdk = FakeSdk(FakeStt(SpeechToTextResponse(transcript="ok", language_code=None)))
    assert (await engine_over(sdk).transcribe(AUDIO, mime="audio/webm", keyterms=[])).language_code is None


@pytest.mark.parametrize(
    "error",
    [
        BadRequestError(QUOTA),
        UnprocessableEntityError({"detail": "bad audio"}),
        ContentTooLargeError({"detail": "too big"}),
        ApiError(status_code=415, body="unsupported"),
    ],
)
async def test_audio_sarvam_cannot_use_is_a_rejection(error: ApiError, log: RecordingLog) -> None:
    sdk = FakeSdk(FakeStt(error=error))
    with pytest.raises(SpeechRejected) as caught:
        await engine_over(sdk).transcribe(AUDIO, mime="audio/webm", keyterms=[])
    assert caught.value.user_message == REJECTED_MESSAGE and caught.value.status == 400
    assert log.events("warning") == ["speech_rejected"]


@pytest.mark.parametrize(
    ("error", "level"),
    [
        (UnauthorizedError({"error": {"code": "invalid_api_key_error"}}), "error"),
        (ForbiddenError({"error": {"code": "invalid_api_key_error", "message": "bad key"}}), "error"),
        (PaymentRequiredError(QUOTA), "warning"),
        (TooManyRequestsError(QUOTA), "warning"),
        (TooManyRequestsError({"error": {"code": "rate_limit_exceeded_error"}}), "warning"),
        (InternalServerError({"error": {"code": "internal_server_error"}}), "warning"),
        (ServiceUnavailableError("down"), "warning"),
        (BadGatewayError(None), "warning"),
        (GatewayTimeoutError(None), "warning"),
        (ApiError(status_code=418, body="teapot"), "warning"),
        (ApiError(status_code=None, body=None), "warning"),
    ],
)
async def test_everything_else_makes_sarvam_unavailable(
    error: ApiError, level: str, log: RecordingLog
) -> None:
    sdk = FakeSdk(FakeStt(error=error))
    with pytest.raises(SpeechUnavailable) as caught:
        await engine_over(sdk).transcribe(AUDIO, mime="audio/webm", keyterms=[])
    assert caught.value.user_message == UNAVAILABLE_MESSAGE and caught.value.status == 503
    assert log.events(level) == ["speech_provider_failed"]
    (_, _, fields) = log.records[0]
    assert fields["call"] == "transcribe" and fields["status"] == error.status_code


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("no route to host"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.RemoteProtocolError("closed"),
        TimeoutError(),
        ValueError("a response nobody expected"),
    ],
)
async def test_a_network_failure_or_a_surprise_makes_sarvam_unavailable(error: BaseException) -> None:
    sdk = FakeSdk(FakeStt(error=error))
    with pytest.raises(SpeechUnavailable) as caught:
        await engine_over(sdk).transcribe(AUDIO, mime="audio/webm", keyterms=[])
    assert caught.value.user_message == UNAVAILABLE_MESSAGE


async def test_a_response_without_a_transcript_is_unavailable() -> None:
    sdk = FakeSdk(FakeStt(response=object()))
    with pytest.raises(SpeechUnavailable):
        await engine_over(sdk).transcribe(AUDIO, mime="audio/webm", keyterms=[])


async def test_a_call_that_hangs_is_cut_at_the_request_timeout() -> None:
    class Hangs:
        async def transcribe(self, **kwargs: Any) -> Any:
            await asyncio.sleep(30)

    sdk = FakeSdk(speech_to_text=Hangs())  # type: ignore[arg-type]
    with pytest.raises(SpeechUnavailable):
        await engine_over(sdk, request_timeout_s=0.05).transcribe(AUDIO, mime="audio/webm", keyterms=[])


async def test_a_cancelled_call_is_not_swallowed() -> None:
    class Waits:
        async def transcribe(self, **kwargs: Any) -> Any:
            await asyncio.sleep(30)

    engine = engine_over(FakeSdk(speech_to_text=Waits()))  # type: ignore[arg-type]
    task = asyncio.create_task(engine.transcribe(AUDIO, mime="audio/webm", keyterms=[]))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ───────────────────────── text to speech ─────────────────────────


async def test_a_line_is_spoken_with_the_voice_model_pace_rate_and_codec_from_the_settings() -> None:
    sdk = FakeSdk()
    audio = await engine_over(sdk).speak("Tell me about the checkout.", pace=0.85)
    assert audio == MP3_BYTES
    (call,) = sdk.text_to_speech.calls
    assert call == {
        "text": "Tell me about the checkout.",
        "language_code": "en-IN",
        "speaker": "simran_en_customer",
        "model": "bulbul:v4-flash",
        "pace": 0.85,
        "speech_sample_rate": 22050,
        "output_audio_codec": "mp3",
        "request_options": {"max_retries": 0},
    }


async def test_the_voice_follows_the_settings() -> None:
    sdk = FakeSdk()
    await engine_over(sdk, tts_speaker="meera", tts_model="bulbul:v3", tts_sample_rate=24000).speak(
        "Hi.", pace=1.0
    )
    call = sdk.text_to_speech.calls[0]
    assert (call["speaker"], call["model"], call["speech_sample_rate"]) == ("meera", "bulbul:v3", 24000)


@pytest.mark.parametrize(
    "error",
    [
        BadRequestError(
            {"error": {"code": "invalid_request_error"}}
        ),  # a text Sarvam will not read: the browser's voice does
        UnprocessableEntityError({"detail": "bad text"}),
        TooManyRequestsError(QUOTA),
        ForbiddenError({"error": {"code": "invalid_api_key_error"}}),
        ServiceUnavailableError("down"),
        httpx.ConnectError("no route to host"),
        TimeoutError(),
    ],
)
async def test_every_text_to_speech_failure_is_unavailable_so_the_browser_voice_takes_over(
    error: BaseException,
) -> None:
    sdk = FakeSdk(text_to_speech=FakeTts(error=error))
    with pytest.raises(SpeechUnavailable) as caught:
        await engine_over(sdk).speak("Hello.", pace=1.0)
    assert caught.value.user_message == UNAVAILABLE_MESSAGE


@pytest.mark.parametrize(
    "response",
    [
        TextToSpeechResponse(audios=[]),
        TextToSpeechResponse(audios=[""]),
        TextToSpeechResponse(audios=["not base64 !!!"]),
        object(),
    ],
)
async def test_an_answer_without_usable_audio_is_unavailable(response: Any) -> None:
    sdk = FakeSdk(text_to_speech=FakeTts(response=response))
    with pytest.raises(SpeechUnavailable):
        await engine_over(sdk).speak("Hello.", pace=1.0)


# ───────────────────────── what stays out of the logs ─────────────────────────


async def test_the_key_the_audio_and_the_text_never_reach_a_log_or_an_error(log: RecordingLog) -> None:
    secret_text = "my private answer about the salary"
    failing = FakeSdk(
        FakeStt(
            error=ForbiddenError(
                {"error": {"code": "invalid_api_key_error", "message": f"{KEY} {secret_text}"}}
            )
        ),
        FakeTts(error=TooManyRequestsError(QUOTA)),
    )
    engine = engine_over(failing)
    with pytest.raises(SpeechUnavailable) as stt_error:
        await engine.transcribe(AUDIO, mime="audio/webm", keyterms=[secret_text])
    with pytest.raises(SpeechUnavailable) as tts_error:
        await engine.speak(secret_text, pace=1.0)
    working = engine_over(FakeSdk())
    await working.transcribe(AUDIO, mime="audio/webm", keyterms=[secret_text])
    await working.speak(secret_text, pace=1.0)

    everything = (
        repr(log.records) + repr(stt_error.value) + repr(tts_error.value) + str(stt_error.value.detail)
    )
    for forbidden in (KEY, "THE_SECRET_KEY", secret_text, AUDIO.decode("latin-1"), "private answer"):
        assert forbidden not in everything, forbidden
    assert "invalid_api_key_error" in everything  # Sarvam's own error code is kept: it says what to fix


async def test_what_is_logged_are_sizes_models_and_latency(log: RecordingLog) -> None:
    engine = engine_over(FakeSdk())
    await engine.transcribe(AUDIO, mime="audio/webm", keyterms=["Kitebird"])
    await engine.speak("Hello there.", pace=1.0)
    stt, tts = (fields for _, _, fields in log.records)
    assert log.events("info") == ["speech_provider_transcribed", "speech_provider_spoke"]
    assert (
        stt["model"] == "saaras:v4"
        and stt["bytes"] == len(AUDIO)
        and stt["keyterms"] == 1
        and stt["chars"] == 11
    )
    assert tts["model"] == "bulbul:v4-flash" and tts["chars"] == 12 and tts["bytes"] == len(MP3_BYTES)
    assert isinstance(stt["latency_ms"], int) and isinstance(tts["latency_ms"], int)


# ───────────────────────── error codes ─────────────────────────


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (QUOTA, "insufficient_quota_error"),
        ({"error": {"code": "rate_limit_exceeded_error"}}, "rate_limit_exceeded_error"),
        ({"code": "top_level_code"}, "top_level_code"),
        ({"error": {"code": "has spaces & <tags>"}}, "hasspacestags"),
        ({"error": {"code": "x" * 200}}, "x" * 64),
        ({"error": {"code": 429}}, None),
        ({"error": "just text"}, None),
        ("a string body", None),
        (None, None),
        ([1, 2], None),
    ],
)
def test_only_sarvams_error_code_is_taken_from_a_body(body: Any, code: str | None) -> None:
    assert error_code(body) == code


# ───────────────────────── the real client ─────────────────────────


async def test_the_engine_builds_the_official_client_without_a_network_and_closes_it() -> None:
    engine = SarvamEngine(SecretStr(KEY), SpeechSettings(request_timeout_s=7.0))
    try:
        http = engine._http  # the engine owns its connection pool
        assert http is not None and http.timeout.read == 7.0
        assert type(engine._client).__name__ == "AsyncSarvamAI"
        assert KEY not in repr(engine)
    finally:
        await engine.aclose()
    assert http.is_closed


async def test_an_engine_over_a_handed_in_client_has_nothing_of_its_own_to_close() -> None:
    engine = engine_over(FakeSdk())
    await engine.aclose()  # a no-op, not an error
