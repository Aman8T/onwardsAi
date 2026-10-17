"""A speech engine that never touches a network: it records what it was asked and answers (or fails) as the test scripts.

`FakeSpeechEngine` implements `tayyari.speech.engine.SpeechEngine`. A test sets what it hears and what it says aloud, makes it
raise `SpeechUnavailable` / `SpeechRejected` (or anything else), and reads back every call: the audio it was handed, the
audio type, the key terms, the text and the pace.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tayyari.speech.engine import Heard

MP3 = b"ID3\x03\x00fake-mp3"
"""Stand-in bytes for a spoken line."""


@dataclass(frozen=True, slots=True)
class Transcription:
    audio: bytes
    mime: str
    keyterms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Speech:
    text: str
    pace: float


class FakeSpeechEngine:
    def __init__(
        self,
        heard: str | Heard = "I rebuilt the checkout and measured the gain.",
        audio: bytes = MP3,
        *,
        transcribe_error: BaseException | None = None,
        speak_error: BaseException | None = None,
    ) -> None:
        self.heard = heard if isinstance(heard, Heard) else Heard(heard, "en-IN")
        self.audio = audio
        self.transcribe_error = transcribe_error
        self.speak_error = speak_error
        self.transcribed: list[Transcription] = []
        self.spoken: list[Speech] = []

    async def transcribe(self, audio: bytes, *, mime: str, keyterms: Sequence[str]) -> Heard:
        self.transcribed.append(Transcription(audio, mime, tuple(keyterms)))
        if self.transcribe_error is not None:
            raise self.transcribe_error
        return self.heard

    async def speak(self, text: str, *, pace: float) -> bytes:
        self.spoken.append(Speech(text, pace))
        if self.speak_error is not None:
            raise self.speak_error
        return self.audio

    @property
    def calls(self) -> int:
        """How many times the engine was asked to do anything."""
        return len(self.transcribed) + len(self.spoken)


__all__ = ["MP3", "FakeSpeechEngine", "Speech", "Transcription"]
