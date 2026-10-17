"""A speech engine that never touches a network: it records what it was asked and answers (or fails) as the test scripts.

`FakeSpeechEngine` implements `tayyari.speech.engine.SpeechEngine`. A test sets what it hears and what it says aloud, makes it
raise `SpeechUnavailable` / `SpeechRejected` (or anything else), and reads back every call: the audio it was handed, the
audio type, the key terms, the text and the pace. Give it a ``hold`` event and every spoken line waits for the event to be set
(a slow Sarvam); ``asked`` is set when the first line reaches it, and ``active`` and ``peak`` say how many lines it was
making at once.
"""

from __future__ import annotations

import asyncio
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
        hold: asyncio.Event | None = None,
    ) -> None:
        self.heard = heard if isinstance(heard, Heard) else Heard(heard, "en-IN")
        self.audio = audio
        self.transcribe_error = transcribe_error
        self.speak_error = speak_error
        self.hold = hold
        self.asked = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.transcribed: list[Transcription] = []
        self.spoken: list[Speech] = []

    async def transcribe(self, audio: bytes, *, mime: str, keyterms: Sequence[str]) -> Heard:
        self.transcribed.append(Transcription(audio, mime, tuple(keyterms)))
        if self.transcribe_error is not None:
            raise self.transcribe_error
        return self.heard

    async def speak(self, text: str, *, pace: float) -> bytes:
        self.spoken.append(Speech(text, pace))
        self.asked.set()
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.hold is not None:
                await self.hold.wait()
            if self.speak_error is not None:
                raise self.speak_error
            return self.audio
        finally:
            self.active -= 1

    @property
    def calls(self) -> int:
        """How many times the engine was asked to do anything."""
        return len(self.transcribed) + len(self.spoken)


__all__ = ["MP3", "FakeSpeechEngine", "Speech", "Transcription"]
