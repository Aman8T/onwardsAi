"""The speech engine over Sarvam AI's official SDK (``sarvamai``, its async client).

    transcribe   ``speech_to_text.transcribe`` (the REST endpoint, for audio under 30 seconds): the piece as one file part,
                 the model and language from `SpeechSettings`, ``input_audio_codec`` from the audio type, and the
                 interview's own names and terms as ``keyterms`` (``saaras:v4`` only; left out for any other model).
    speak        ``text_to_speech.convert``: the voice, language, model, pace, sample rate and codec from `SpeechSettings`;
                 the first audio of the answer, decoded from base64.

Every request has the SDK's retries switched off (it would otherwise wait out a ``Retry-After`` of up to a minute while a
person waits) and a wall-clock cap of ``request_timeout_s``. A failure is one of two errors (`speech.engine`):

    400 / 413 / 415 / 422 on speech to text      `SpeechRejected`: this piece could not be used
    everything else, on either call              `SpeechUnavailable`: a key Sarvam refused (401 / 403), no credits or a rate
                                                 limit (402 / 429), a server error, a timeout, no network, an answer that
                                                 cannot be read. (A text Sarvam refuses to read aloud is also this: the
                                                 room then reads the line with the browser's voice.)

The key is read from its `SecretStr` once, to build the client, and goes nowhere else. Logs carry sizes, seconds, the model,
the status, Sarvam's error code and the latency; never the key, the audio, the text, or an error's message or body.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Sequence
from typing import Any, Final, Protocol

import httpx
from pydantic import SecretStr
from sarvamai import AsyncSarvamAI
from sarvamai.core.api_error import ApiError

from tayyari.core.logging import get_logger
from tayyari.core.settings import SpeechSettings
from tayyari.speech.engine import (
    Heard,
    SpeechRejected,
    SpeechUnavailable,
    clean_keyterms,
    media_type,
    require_codec,
)

log = get_logger(__name__)

KEYTERM_MODELS: Final = ("saaras:v4",)
"""The speech-to-text models that take ``keyterms`` (the API refuses them for any other)."""
REJECTED_STATUSES: Final = frozenset({400, 413, 415, 422})
"""Statuses of a speech-to-text call that mean "this piece cannot be used" rather than "Sarvam cannot be used"."""
KEY_STATUSES: Final = frozenset({401, 403})
NO_RETRIES: Final[dict[str, Any]] = {"max_retries": 0}
"""``request_options`` of every call: the SDK's own retries wait out a ``Retry-After`` of up to a minute."""
_CODE_MAX_CHARS: Final = 64


class SdkClient(Protocol):
    """The two parts of `AsyncSarvamAI` this engine uses (a test hands in a stand-in)."""

    @property
    def speech_to_text(self) -> Any: ...

    @property
    def text_to_speech(self) -> Any: ...


def error_code(body: object) -> str | None:
    """Sarvam's own code for an error ("insufficient_quota_error", "rate_limit_exceeded_error" ...), from the body it sent,
    cut short. Nothing else of the body is kept."""
    if not isinstance(body, dict):
        return None
    inner = body.get("error")
    code = inner.get("code") if isinstance(inner, dict) else body.get("code")
    if not isinstance(code, str):
        return None
    cleaned = "".join(ch for ch in code if ch.isalnum() or ch in "_-.:")[:_CODE_MAX_CHARS]
    return cleaned or None


class SarvamEngine:
    def __init__(
        self,
        api_key: SecretStr,
        settings: SpeechSettings,
        *,
        client: SdkClient | None = None,
    ) -> None:
        self._settings = settings
        self._http: httpx.AsyncClient | None = None
        if client is None:
            # Our own httpx client, so the engine owns the connection pool and can close it.
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.request_timeout_s), follow_redirects=True
            )
            client = AsyncSarvamAI(
                api_subscription_key=api_key.get_secret_value(),
                timeout=settings.request_timeout_s,
                httpx_client=self._http,
            )
        self._client: SdkClient = client

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    # ───────────────────────── failures ─────────────────────────

    def _failed(
        self, call: str, started: float, exc: BaseException, *, rejects: bool
    ) -> SpeechUnavailable | SpeechRejected:
        """Log what happened (never the error's text) and give the error the caller raises."""
        latency_ms = round((time.monotonic() - started) * 1000)
        model = self._settings.stt_model if call == "transcribe" else self._settings.tts_model
        if isinstance(exc, ApiError):
            status = exc.status_code
            code = error_code(exc.body)
            if rejects and status in REJECTED_STATUSES:
                log.warning("speech_rejected", call=call, model=model, status=status, code=code)
                return SpeechRejected(detail=f"{call} {status} {code}")
            level = log.error if status in KEY_STATUSES else log.warning
            level(
                "speech_provider_failed",
                call=call,
                model=model,
                status=status,
                code=code,
                latency_ms=latency_ms,
            )
            return SpeechUnavailable(detail=f"{call} {status} {code}")
        if isinstance(exc, TimeoutError | httpx.TimeoutException):
            log.warning("speech_provider_timeout", call=call, model=model, latency_ms=latency_ms)
            return SpeechUnavailable(detail=f"{call} timeout")
        if isinstance(exc, httpx.HTTPError):
            log.warning(
                "speech_provider_unreachable",
                call=call,
                model=model,
                error=type(exc).__name__,
                latency_ms=latency_ms,
            )
            return SpeechUnavailable(detail=f"{call} {type(exc).__name__}")
        log.error(
            "speech_provider_error", call=call, model=model, error=type(exc).__name__, latency_ms=latency_ms
        )
        return SpeechUnavailable(detail=f"{call} {type(exc).__name__}")

    # ───────────────────────── speech to text ─────────────────────────

    async def transcribe(self, audio: bytes, *, mime: str, keyterms: Sequence[str]) -> Heard:
        s = self._settings
        codec = require_codec(mime)
        terms = clean_keyterms(keyterms) if s.stt_model in KEYTERM_MODELS else []
        extra: dict[str, Any] = {"keyterms": terms} if terms else {}
        started = time.monotonic()
        try:
            async with asyncio.timeout(s.request_timeout_s):
                response = await self._client.speech_to_text.transcribe(
                    file=(f"piece.{codec}", audio, media_type(mime)),
                    model=s.stt_model,
                    language_code=s.stt_language,
                    input_audio_codec=codec,
                    request_options=NO_RETRIES,
                    **extra,
                )
            transcript = response.transcript
            language = response.language_code
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise self._failed("transcribe", started, exc, rejects=True) from None
        heard = Heard(
            text=transcript if isinstance(transcript, str) else "",
            language_code=language if isinstance(language, str) and language else None,
        )
        log.info(
            "speech_provider_transcribed",
            model=s.stt_model,
            bytes=len(audio),
            codec=codec,
            keyterms=len(terms),
            chars=len(heard.text),
            language=heard.language_code,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return heard

    # ───────────────────────── text to speech ─────────────────────────

    async def speak(self, text: str, *, pace: float) -> bytes:
        s = self._settings
        started = time.monotonic()
        try:
            async with asyncio.timeout(s.request_timeout_s):
                response = await self._client.text_to_speech.convert(
                    text=text,
                    language_code=s.tts_language,
                    speaker=s.tts_speaker,
                    model=s.tts_model,
                    pace=pace,
                    speech_sample_rate=s.tts_sample_rate,
                    output_audio_codec=s.tts_codec,
                    request_options=NO_RETRIES,
                )
            audios = response.audios
            audio = base64.b64decode(audios[0], validate=True) if audios else b""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise self._failed("speak", started, exc, rejects=False) from None
        if not audio:
            raise self._failed("speak", started, ValueError("empty audio"), rejects=False)
        log.info(
            "speech_provider_spoke",
            model=s.tts_model,
            chars=len(text),
            bytes=len(audio),
            pace=pace,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        return audio


__all__ = ["KEYTERM_MODELS", "NO_RETRIES", "SarvamEngine", "SdkClient", "error_code"]
