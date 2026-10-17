"""The seam between the speech service and a speech provider: what an engine does, how it fails, and the audio types.

An engine turns one piece of an answer into text (`transcribe`) and one of the interviewer's lines into audio (`speak`). The
only engine today is `speech.sarvam.SarvamEngine`; tests use `tests/fakes/speech.FakeSpeechEngine`. Both failures are
`AppError`s with a sentence written for the person who meets it:

    SpeechUnavailable   the provider cannot be used right now (a key it refused, no credits left, a rate limit, a server
                        error, a timeout, no network): the room falls back to the browser's own speech. 503.
    SpeechRejected      the provider could not use this one piece of audio (400 / 422): say it again, or type it. 400.

Nothing here logs or stores audio or text.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from tayyari.core.errors import BadRequest, ServiceUnavailable

UNAVAILABLE_MESSAGE: Final = (
    "Sarvam's speech isn't available right now. Your browser's speech will take over."
)
REJECTED_MESSAGE: Final = "We couldn't make out that piece. Say it again, or type it."

KEYTERM_MAX_COUNT: Final = 50
"""Sarvam takes up to 50 key terms to bias recognition."""
KEYTERM_MAX_CHARS: Final = 64
"""Each key term is at most 64 characters."""


class SpeechUnavailable(ServiceUnavailable):
    """The speech provider cannot be used right now. The browser's own speech takes over; typing always works."""

    code = "speech_unavailable"
    user_message = UNAVAILABLE_MESSAGE


class SpeechRejected(BadRequest):
    """The provider could not use this piece of audio (or it was not audio we accept)."""

    code = "speech_rejected"
    user_message = REJECTED_MESSAGE


CODECS: Final[dict[str, str]] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/x-m4a": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}
"""The audio types a piece may be sent as, each with the codec name Sarvam's ``input_audio_codec`` takes."""


def media_type(mime: str) -> str:
    """``audio/webm`` for ``Audio/WebM;codecs=opus``: the media type alone, lower case, without its parameters."""
    return mime.split(";", 1)[0].strip().lower()


def codec_for(mime: str) -> str | None:
    """The codec name of an audio type we accept (parameters such as ``;codecs=opus`` ignored), else None."""
    return CODECS.get(media_type(mime))


def require_codec(mime: str) -> str:
    """The codec name for ``mime``, or `SpeechRejected` for any other type (before any provider call)."""
    codec = codec_for(mime)
    if codec is None:
        raise SpeechRejected(detail=f"unsupported audio type {media_type(mime)[:40]!r}")
    return codec


def clean_keyterms(terms: Iterable[str]) -> list[str]:
    """The key terms an engine may send: trimmed, one space between words, no repeats (case aside), none empty or longer than
    `KEYTERM_MAX_CHARS`, at most `KEYTERM_MAX_COUNT`, in the order given (most useful first)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in terms:
        term = " ".join(raw.split()) if isinstance(raw, str) else ""
        key = term.casefold()
        if not term or len(term) > KEYTERM_MAX_CHARS or key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= KEYTERM_MAX_COUNT:
            break
    return out


@dataclass(frozen=True, slots=True)
class Heard:
    """What the provider heard in one piece: the words as it wrote them ('' when nothing), and the language it reports."""

    text: str
    language_code: str | None = None


class SpeechEngine(Protocol):
    async def transcribe(self, audio: bytes, *, mime: str, keyterms: Sequence[str]) -> Heard:
        """The words in ``audio`` (one piece, under 30 seconds), biased toward ``keyterms``.

        Raises `SpeechRejected` for audio the provider cannot use (and for an audio type we do not accept, before any call)
        and `SpeechUnavailable` for everything else that goes wrong."""
        ...

    async def speak(self, text: str, *, pace: float) -> bytes:
        """``text`` read aloud: MP3 bytes. Raises `SpeechUnavailable` when it cannot be done."""
        ...


__all__ = [
    "CODECS",
    "KEYTERM_MAX_CHARS",
    "KEYTERM_MAX_COUNT",
    "REJECTED_MESSAGE",
    "UNAVAILABLE_MESSAGE",
    "Heard",
    "SpeechEngine",
    "SpeechRejected",
    "SpeechUnavailable",
    "clean_keyterms",
    "codec_for",
    "media_type",
    "require_codec",
]
