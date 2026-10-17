"""Speech for practice interviews: what is on, what a piece of an answer costs, and the things the room asks for.

    status      `SpeechStatus`: whether Sarvam writes down answers (``stt``) and reads questions aloud (``tts``) for this
                session, and if not, why (no key, switched off, the day's budget or the session's share used up).
    transcribe  one piece of an answer (audio) -> words. Checked first (an audio type we accept, not empty, not over the size
                cap), then the budget, then charged, then sent to the engine. The audio is held for the call and dropped.
    speak       one interviewer line -> MP3. A line spoken before costs nothing (an LRU cache, keyed by voice, pace and text);
                a line being made right now is shared (whoever asks for it waits for the one Sarvam call); a new one is
                checked against the budget, charged by its characters, and sent to the engine.
    prefetch    lines the interview will probably say next, made in the background before anyone asks (a question's voice
                takes about 5 s to come back from Sarvam, a made one about 1 s). Fire and forget: it never raises and never
                waits. Each line is charged like any other, and one the budget would not cover is skipped without a word.

The engine is None when there is no key (or speech is switched off): everything then says so, and the room uses the
browser's own speech. Budgets are soft (`speech.meter`). Which lines may be spoken, which to make ahead, and which words bias
recognition, are the interview's to say: the routes get them from `InterviewService` and hand them in.

Nothing here logs or keeps audio or text, only sizes, seconds and counts.
"""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from functools import partial
from typing import Any, Final, Literal

from tayyari.contract.speech import SPEAK_TEXT_MAX_CHARS, SpeechOffReason, SpeechStatus, TranscribeResult
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
MAX_PACES: Final = 2048
"""Sessions whose last pace this process remembers."""

Kind = Literal["stt", "tts"]
Source = Literal["request", "prefetch"]
Key = tuple[str, str, float, str]
"""What identifies a spoken line: the model, the speaker, the pace and the text. The cache and the lines being made share it."""


class SpeechService:
    def __init__(
        self, engine: SpeechEngine | None, settings: SpeechSettings, kv: KvRepo, clock: Clock
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._meter = SpeechMeter(kv, clock, settings)
        self._cache: OrderedDict[Key, bytes] = OrderedDict()
        self._making: dict[Key, asyncio.Task[bytes]] = {}
        """Lines being made right now: one task each, shared by everyone who asks for the line meanwhile."""
        self._queued: set[Key] = set()
        """Lines `prefetch` has scheduled and not yet started making."""
        self._background: set[asyncio.Task[Any]] = set()
        """Strong references to every task this service started (the event loop only keeps weak ones)."""
        self._slots = asyncio.Semaphore(settings.tts_prefetch_concurrency)
        self._budget = asyncio.Lock()
        """Held while a prefetch looks at the budget and pays from it, so two lines made at once are not both checked against
        the same credits."""
        self._paces: OrderedDict[str, float] = OrderedDict()
        """session id -> the pace its browser last asked for (a person who chose the slower voice asks for 0.85)."""

    @property
    def meter(self) -> SpeechMeter:
        return self._meter

    @property
    def configured(self) -> bool:
        """Whether there is an engine (a key) and speech is not switched off."""
        return self._engine is not None and self._settings.enabled

    @property
    def prefetching(self) -> bool:
        """Whether lines are made ahead: Sarvam is on, ``tts_prefetch`` is on, and there is a cache to keep them in."""
        s = self._settings
        return self.configured and s.tts_prefetch and s.tts_cache_items > 0

    @property
    def prefetching_followups(self) -> bool:
        """Whether the lines only some answers lead to (the follow-up and the easier question) are made ahead too."""
        return self.prefetching and self._settings.tts_prefetch_followups

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

    def _remember(self, key: Key, audio: bytes) -> None:
        limit = self._settings.tts_cache_items
        if limit <= 0:
            return
        self._cache[key] = audio
        self._cache.move_to_end(key)
        while len(self._cache) > limit:
            self._cache.popitem(last=False)

    def _recall(self, key: Key) -> bytes | None:
        """A line made before, now the most recently used."""
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
        return cached

    @staticmethod
    def _rate(pace: float | None) -> float:
        return round(min(2.0, max(0.5, pace if pace is not None else 1.0)), 2)

    def _note_pace(self, session_id: str, rate: float) -> None:
        self._paces[session_id] = rate
        self._paces.move_to_end(session_id)
        while len(self._paces) > MAX_PACES:
            self._paces.popitem(last=False)

    def _ready(self, key: Key) -> bool:
        """Whether a line is made or being made."""
        return self._recall(key) is not None or key in self._making

    def _make(
        self,
        key: Key,
        session_id: str,
        said: str,
        rate: float,
        engine: SpeechEngine,
        source: Source,
        *,
        paid: bool = False,
    ) -> asyncio.Task[bytes]:
        """Start making a line and keep it where `speak` and `prefetch` find it until it is over. Callers check that nobody
        else is making it and call this with no await in between, so one line is only ever made once at a time. ``paid``:
        the line was charged already."""
        task = asyncio.create_task(
            self._generate(key, session_id, said, rate, engine, source, paid), name="tayyari-voice"
        )
        self._making[key] = task
        self._background.add(task)
        task.add_done_callback(partial(self._made, key))
        return task

    def _made(self, key: Key, task: asyncio.Task[bytes]) -> None:
        """A line is over (made, failed or cancelled). A made one is in the cache already, so a line is never in neither."""
        self._background.discard(task)
        if self._making.get(key) is task:
            del self._making[key]
        if not task.cancelled():
            task.exception()  # read, so a line nobody waited for does not end as "exception never retrieved"

    async def _generate(
        self,
        key: Key,
        session_id: str,
        said: str,
        rate: float,
        engine: SpeechEngine,
        source: Source,
        paid: bool,
    ) -> bytes:
        if not paid:
            await self._meter.charge(session_id, tts_chars=len(said))
        audio = await engine.speak(said, pace=rate)
        self._remember(key, audio)
        log.info("speech_spoken", chars=len(said), bytes=len(audio), pace=rate, source=source)
        return audio

    async def speak(self, session_id: str, text: str, pace: float | None = None) -> bytes:
        """``text`` as MP3. A line spoken before comes from memory and costs nothing; one being made right now is waited for
        (one Sarvam call serves everyone who asks); a new one is charged by its characters. Raises `SpeechRejected` (nothing
        to say) and `SpeechUnavailable` (no engine, budget used, or the provider failed)."""
        s = self._settings
        said = " ".join(text.split())
        if not said:
            raise SpeechRejected(NOTHING_TO_SAY, detail="empty text")
        rate = self._rate(pace)
        self._note_pace(session_id, rate)
        key: Key = (s.tts_model, s.tts_speaker, rate, said)
        cached = self._recall(key)
        if cached is not None:
            return cached
        task = self._making.get(key)
        if task is None:
            engine = await self._engine_for("tts", session_id)
            # the check above may have waited: the line can have been made, or started, by someone else meanwhile
            cached = self._recall(key)
            if cached is not None:
                return cached
            task = self._making.get(key) or self._make(key, session_id, said, rate, engine, "request")
        # shielded: a browser that hangs up does not stop a line that others (and the cache) are waiting for
        return await asyncio.shield(task)

    # ───────────────────────── making lines ahead ─────────────────────────

    def prefetch(self, session_id: str, texts: Iterable[str], *, pace: float | None = None) -> int:
        """Start making these lines in the background, in order, so they are ready when the browser asks. Fire and forget:
        it returns at once and never raises. A line already made, being made or waiting its turn is left alone; the rest
        are made ``tts_prefetch_concurrency`` at a time across the process, each charged like `speak` charges it, and one
        the day's or the session's budget would not cover is skipped. A line that fails is logged (its size and the reason,
        never its text) and leaves nothing behind. ``pace`` is the session's last pace when not given (a browser that asked
        for the slower voice is made that voice's lines, not the normal ones; 1.0 before it has asked for anything).
        Returns how many lines were scheduled."""
        if not self.prefetching:
            return 0
        try:
            return self._schedule(session_id, texts, pace)
        except Exception as exc:  # a prefetch never breaks the request that asked for it
            log.warning("speech_prefetch_failed", reason=type(exc).__name__, stage="schedule")
            return 0

    def _schedule(self, session_id: str, texts: Iterable[str], pace: float | None) -> int:
        asyncio.get_running_loop()  # fails here, before anything is scheduled, when called outside the event loop
        s = self._settings
        rate = self._rate(pace if pace is not None else self._paces.get(session_id))
        scheduled = ready = 0
        for text in texts:
            said = " ".join(text.split()) if isinstance(text, str) else ""
            if not said or len(said) > SPEAK_TEXT_MAX_CHARS:
                continue
            key: Key = (s.tts_model, s.tts_speaker, rate, said)
            if self._ready(key) or key in self._queued:
                ready += 1
                continue
            self._queued.add(key)
            task = asyncio.create_task(
                self._prefetch_line(session_id, key, said, rate), name="tayyari-prefetch"
            )
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            scheduled += 1
        if scheduled:
            log.info("speech_prefetch_scheduled", lines=scheduled, ready=ready, pace=rate)
        return scheduled

    async def _prefetch_line(self, session_id: str, key: Key, said: str, rate: float) -> None:
        """One scheduled line: wait for a place among the lines made ahead, then make it if it is still wanted and paid for."""
        try:
            async with self._slots:
                await self._prefetch_now(session_id, key, said, rate)
        except Exception as exc:  # never raises: the task is nobody's to await
            log.warning("speech_prefetch_failed", chars=len(said), reason=_reason(exc))
        finally:
            self._queued.discard(key)

    async def _prefetch_now(self, session_id: str, key: Key, said: str, rate: float) -> None:
        engine = self._engine
        if engine is None or not self.prefetching or self._ready(key):
            return
        async with self._budget:
            remaining = await self._meter.remaining(session_id)
            reason = self._spent("tts", remaining)
            if reason is not None or remaining.tts_chars < len(said):
                log.info(
                    "speech_prefetch_skipped",
                    reason=reason or "budget",
                    chars=len(said),
                    left=remaining.tts_chars,
                )
                return
            if self._ready(key):  # asked for, and started, while the budget was read
                return
            # paid before it is made, as `speak` pays
            await self._meter.charge(session_id, tts_chars=len(said))
        # a request that came while the charge was written made it and paid too: this was a few characters over
        if self._ready(key):
            return
        task = self._make(key, session_id, said, rate, engine, "prefetch", paid=True)
        await asyncio.wait({task})  # the place is kept for as long as Sarvam works on the line
        failure = None if task.cancelled() else task.exception()
        if failure is not None:
            log.warning("speech_prefetch_failed", chars=len(said), reason=_reason(failure))

    async def drain(self) -> None:
        """Wait until every line being made, or waiting to be made, is over (tests, and a clean stop)."""
        while self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    async def aclose(self) -> None:
        """Stop making lines. Safe to call twice."""
        tasks = list(self._background)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _reason(exc: BaseException) -> str:
    """Why a line failed, for a log: the error's own short detail (a status and Sarvam's code), else its type. Never its text."""
    detail = getattr(exc, "detail", None)
    return detail if isinstance(detail, str) and detail else type(exc).__name__


__all__ = [
    "BYTES_PER_SECOND",
    "DAILY_MESSAGE",
    "NOTHING_TO_SAY",
    "SESSION_MESSAGE",
    "TOO_LARGE_MESSAGE",
    "Key",
    "Kind",
    "SpeechService",
]
