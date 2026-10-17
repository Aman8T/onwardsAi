"""Pydantic mirror of shared/speech.ts: speech for practice interviews through Sarvam AI.

Same rules as the other contracts: camelCase field names that are the JSON keys, a field is optional
(``X | None = None``) exactly when the TypeScript field is ``x?:``, and `tests/unit/contract/test_speech_parity.py`
fails when the two drift. Tayyari keeps no audio: a piece of an answer is sent to Sarvam and dropped, and only the text
reaches the interview.
"""

# ruff: noqa: N815  (the field names are camelCase on purpose: they are the JSON keys of shared/speech.ts)

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from tayyari.contract.models import _Model

SpeechProvider = Literal["sarvam"]
SpeechOffReason = Literal["not_configured", "switched_off", "daily_budget", "session_budget"]

PROFILE_ID_MAX_CHARS: Final = 64
SESSION_ID_MAX_CHARS: Final = 64
SPEAK_TEXT_MAX_CHARS: Final = 1200
"""An interviewer's line (a lead and a question) is well under this."""


class SpeechStatus(_Model):
    stt: bool
    tts: bool
    provider: SpeechProvider | None = None
    sttReason: SpeechOffReason | None = None
    ttsReason: SpeechOffReason | None = None
    maxSegmentSeconds: int
    maxSegmentBytes: int


class TranscribeResult(_Model):
    text: str
    seconds: int
    languageCode: str | None = None


class SpeakRequest(_Model):
    profileId: str = Field(max_length=PROFILE_ID_MAX_CHARS)
    sessionId: str = Field(max_length=SESSION_ID_MAX_CHARS)
    text: str = Field(max_length=SPEAK_TEXT_MAX_CHARS)
    pace: float | None = Field(default=None, ge=0.5, le=2.0)


__all__ = [
    "PROFILE_ID_MAX_CHARS",
    "SESSION_ID_MAX_CHARS",
    "SPEAK_TEXT_MAX_CHARS",
    "SpeakRequest",
    "SpeechOffReason",
    "SpeechProvider",
    "SpeechStatus",
    "TranscribeResult",
]
