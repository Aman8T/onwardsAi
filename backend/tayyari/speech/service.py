"""Speech for practice interviews: what is on, what a piece of an answer costs, and the two things the room asks for.

    status      `SpeechStatus`: whether Sarvam writes down answers (``stt``) and reads questions aloud (``tts``) for this
                session, and if not, why (no key, switched off, the day's budget or the session's share used up).
    transcribe  one piece of an answer (audio) -> words. Checked first (an audio type we accept, not empty, not over the size
                cap), then the budget, then charged, then sent to the engine. The audio is held for the call and dropped.
    speak       one interviewer line -> MP3. A line spoken before costs nothing (an LRU cache, keyed by voice, pace and text);
                a new one is checked against the budget, charged by its characters, and sent to the engine.

The engine is None when there is no key (or speech is switched off): everything then says so, and the room uses the
browser's own speech. Budgets are soft (`speech.meter`). Which lines may be spoken, and which words bias recognition, are the
interview's to say: the routes get them from `InterviewService.speech_context` and hand them in.

Nothing here logs or keeps audio or text, only sizes, seconds and counts.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Sequence
from typing import Final, Literal

from tayyari.contract.speech import SpeechOffReason, SpeechStatus, TranscribeResult
from tayyari.core.clock import Clock
from tayyari.core.errors import PayloadTooLarge
from tayyari.core.logging import get_logger
from tayyari.core.settings import SpeechSettings
from tayyari.core.text import tidy
from tayyari.speech.engine import (
    Heard,
    SpeechEngine,
    SpeechRejected,
    SpeechUnavailable,
    clean_keyterms,
    require_codec,
)
from tayyari.speech.meter import Remaining, SpeechMeter
from tayyari.storage.ports import KvRepo

log = get_logger(__name__)

BYTES_PER_SECOND: Final = 4000
"""A piece of this many bytes counts as at least a second (about 32 kbit/s, the weight of a browser's Opus recording): a
short claimed length on a big body is billed by its size."""
BYTES_PER_SECOND_BY_CODEC: Final[dict[str, int]] = {
    "webm": BYTES_PER_SECOND,
    "ogg": BYTES_PER_SECOND,
    "mp4": 16_000,
    "mp3": 16_000,
    "wav": 32_000,
}
"""How heavy a second of each format is at most (Safari records AAC in mp4, a file upload may be mp3 or 16 kHz WAV), so
the size rule does not bill a heavier format several times over."""
TOO_LARGE_MESSAGE: Final = "That piece is too long to write down. Say a little less at a time, or type it."
NOTHING_TO_SAY: Final = "There is nothing to read aloud."
DAILY_MESSAGE: Final = "Today's Sarvam speech is used up. Your browser's speech will take over."
SESSION_MESSAGE: Final = "This interview's Sarvam speech is used up. Your browser's speech will take over."

Kind = Literal["stt", "tts"]


class SpeechService:
    def __init__(
        self, engine: SpeechEngine | None, settings: SpeechSettings, kv: KvRepo, clock: Clock
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._meter = SpeechMeter(kv, clock, settings)
        self._cache: OrderedDict[tuple[str, str, float, str], bytes] = OrderedDict()

    @property
    def meter(self) -> SpeechMeter:
        return self._meter

    @property
    def configured(self) -> bool:
        """Whether there is an engine (a key) and speech is not switched off."""
        return self._engine is not None and self._settings.enabled

    # ───────────────────────── status ─────────────────────────

    def _unconfigured(self) -> SpeechOffReason | None:
        """Why speech is off before any budget is looked at: no engine (no key), or switched off. None when it is on."""
        if self._engine is None:
            return "not_configured"
        return None if self._settings.enabled else "switched_off"

    def _spent(self, kind: Kind, remaining: Remaining) -> SpeechOffReason | None:
        """Why ``kind`` is off for lack of budget (a cap of 0 is "switched off"), or None when some budget is left."""
        s = self._settings
        if kind == "stt":
            day_cap, session_cap = s.stt_daily_seconds, s.stt_session_seconds
            day_left, session_left = remaining.stt_day, remaining.stt_session
        else:
            day_cap, session_cap = s.tts_daily_chars, s.tts_session_chars
            day_left, session_left = remaining.tts_day, remaining.tts_session
        if day_cap <= 0 or session_cap <= 0:
            return "switched_off"
        if day_left <= 0:
            return "daily_budget"
        if session_left is not None and session_left <= 0:
            return "session_budget"
        return None

    async def status(self, session_id: str | None = None) -> SpeechStatus:
        """What Sarvam speech can do now. Without ``session_id`` only the day's budget counts (Setup asks before a session
        exists)."""
        s = self._settings
        off = self._unconfigured()
        stt = tts = off
        if off is None:
            remaining = await self._meter.remaining(session_id)
            stt, tts = self._spent("stt", remaining), self._spent("tts", remaining)
        return SpeechStatus(
            stt=stt is None,
            tts=tts is None,
            provider="sarvam" if stt is None or tts is None else None,
            sttReason=stt,
            ttsReason=tts,
            maxSegmentSeconds=s.max_segment_seconds,
            maxSegmentBytes=s.max_segment_bytes,
        )

    async def _engine_for(self, kind: Kind, session_id: str) -> SpeechEngine:
        """The engine, when ``kind`` is on for this session; else the `SpeechUnavailable` that says why."""
        reason = self._unconfigured()
        if reason is None:
            reason = self._spent(kind, await self._meter.remaining(session_id))
        if reason is None and self._engine is not None:
            return self._engine
        if reason == "daily_budget":
            raise SpeechUnavailable(DAILY_MESSAGE, detail=f"{kind} daily budget used")
        if reason == "session_budget":
            raise SpeechUnavailable(SESSION_MESSAGE, detail=f"{kind} session budget used")
        raise SpeechUnavailable(detail=f"{kind} {reason}")

    async def ensure(self, kind: Kind, session_id: str) -> None:
        """Raise the `SpeechUnavailable` that says why ``kind`` is off for this session, or return when it is on. A route
        asks before it reads an upload, so a request that cannot be served costs no bytes."""
        await self._engine_for(kind, session_id)

    # ───────────────────────── speech to text ─────────────────────────

    def billed_seconds(self, size: int, claimed_seconds: float, codec: str = "webm") -> int:
        """The seconds a piece counts against the budget: what the browser says it ran, or what its size says if that is
        more (by the weight of its format), at least 1 and at most the longest piece."""
        claimed = math.ceil(claimed_seconds) if math.isfinite(claimed_seconds) and claimed_seconds > 0 else 0
        by_size = math.ceil(size / BYTES_PER_SECOND_BY_CODEC.get(codec, BYTES_PER_SECOND))
        return min(self._settings.max_segment_seconds, max(1, claimed, by_size))

    async def transcribe(
        self,
        session_id: str,
        audio: bytes,
        mime: str,
        claimed_seconds: float,
        keyterms: Sequence[str],
    ) -> TranscribeResult:
        """One piece of an answer as words. Raises `SpeechRejected` (not audio we accept, or empty),
        `PayloadTooLarge` (over the size cap) and `SpeechUnavailable` (no engine, budget used, or the provider failed)."""
        if not self.configured:
            raise SpeechUnavailable(detail=f"stt {self._unconfigured()}")
        codec = require_codec(mime)
        if not audio:
            raise SpeechRejected(detail="empty audio")
        if len(audio) > self._settings.max_segment_bytes:
            raise PayloadTooLarge(TOO_LARGE_MESSAGE)
        engine = await self._engine_for("stt", session_id)
        seconds = self.billed_seconds(len(audio), claimed_seconds, codec)
        await self._meter.charge(session_id, stt_s=seconds)
        heard: Heard = await engine.transcribe(audio, mime=mime, keyterms=clean_keyterms(keyterms))
        text = tidy(heard.text)
        log.info(
            "speech_transcribed", bytes=len(audio), seconds=seconds, chars=len(text), keyterms=len(keyterms)
        )
        return TranscribeResult(text=text, seconds=seconds, languageCode=heard.language_code)

    # ───────────────────────── text to speech ─────────────────────────

    def _remember(self, key: tuple[str, str, float, str], audio: bytes) -> None:
        limit = self._settings.tts_cache_items
        if limit <= 0:
            return
        self._cache[key] = audio
        self._cache.move_to_end(key)
        while len(self._cache) > limit:
            self._cache.popitem(last=False)

    async def speak(self, session_id: str, text: str, pace: float | None = None) -> bytes:
        """``text`` as MP3. A line spoken before comes from memory and costs nothing; a new one is charged by its
        characters. Raises `SpeechRejected` (nothing to say) and `SpeechUnavailable` (no engine, budget used, or the provider
        failed)."""
        s = self._settings
        said = " ".join(text.split())
        if not said:
            raise SpeechRejected(NOTHING_TO_SAY, detail="empty text")
        rate = round(min(2.0, max(0.5, pace if pace is not None else 1.0)), 2)
        key = (s.tts_model, s.tts_speaker, rate, said)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        engine = await self._engine_for("tts", session_id)
        await self._meter.charge(session_id, tts_chars=len(said))
        audio = await engine.speak(said, pace=rate)
        self._remember(key, audio)
        log.info("speech_spoken", chars=len(said), bytes=len(audio), pace=rate)
        return audio


__all__ = [
    "BYTES_PER_SECOND",
    "DAILY_MESSAGE",
    "NOTHING_TO_SAY",
    "SESSION_MESSAGE",
    "TOO_LARGE_MESSAGE",
    "Kind",
    "SpeechService",
]
