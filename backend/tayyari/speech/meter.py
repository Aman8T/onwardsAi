"""A soft budget for Sarvam speech: seconds of answers written down and characters of questions read aloud.

Sarvam bills by the second of audio and the character of text, and the free credits are finite, so the service meters both.
Two caps, neither exact (this is a protection for the credits, not a ledger):

    per day      one key in the ``kv`` table per UTC day, ``speech:usage:<YYYY-MM-DD>`` -> ``{"stt_s": n, "tts_chars": n}``,
                 shared by every replica. A charge reads the day's record and writes it back, so two replicas charging at
                 once can lose one update: the cap is loosened by a race, never tightened. Old days stay in the table
                 (one tiny row a day).
    per session  a bounded dictionary in this process, least recently used session dropped first. Another replica (or a
                 restart) starts a session from zero again, which only loosens the cap.

If the day's record cannot be read the day counts as used up (the browser's own speech takes over: it costs nothing); a
charge that cannot be written is logged and dropped.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Final

from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger
from tayyari.core.settings import SpeechSettings
from tayyari.storage.ports import KvRepo

log = get_logger(__name__)

USAGE_KEY_PREFIX: Final = "speech:usage:"
MAX_SESSIONS: Final = 2048
"""Sessions whose usage this process remembers."""


@dataclass(frozen=True, slots=True)
class Remaining:
    """What is left, per kind. A cap that is not enforced for the session (no session id given) is None."""

    stt_day: int
    tts_day: int
    stt_session: int | None
    tts_session: int | None

    @property
    def stt_s(self) -> int:
        """Seconds that may still be written down: the smaller of the day's and the session's."""
        return self.stt_day if self.stt_session is None else min(self.stt_day, self.stt_session)

    @property
    def tts_chars(self) -> int:
        """Characters that may still be read aloud: the smaller of the day's and the session's."""
        return self.tts_day if self.tts_session is None else min(self.tts_day, self.tts_session)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


class SpeechMeter:
    def __init__(
        self, kv: KvRepo, clock: Clock, settings: SpeechSettings, *, max_sessions: int = MAX_SESSIONS
    ) -> None:
        self._kv = kv
        self._clock = clock
        self._settings = settings
        self._max_sessions = max(1, max_sessions)
        self._sessions: OrderedDict[str, list[int]] = OrderedDict()
        """session id -> [seconds written down, characters read aloud]."""
        self._lock = asyncio.Lock()

    def key(self) -> str:
        """The kv key of today's record (the UTC date)."""
        return f"{USAGE_KEY_PREFIX}{self._clock.now().date().isoformat()}"

    async def _day(self) -> tuple[int, int] | None:
        """Today's ``(seconds, characters)``, or None when the record cannot be read."""
        try:
            stored = await self._kv.get(self.key())
        except Exception as exc:
            log.warning("speech_meter_unreadable", error=type(exc).__name__)
            return None
        if not isinstance(stored, dict):
            return 0, 0
        return _count(stored.get("stt_s")), _count(stored.get("tts_chars"))

    def _session(self, session_id: str) -> list[int]:
        used = self._sessions.get(session_id)
        if used is None:
            used = [0, 0]
            self._sessions[session_id] = used
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
        else:
            self._sessions.move_to_end(session_id)
        return used

    async def remaining(self, session_id: str | None = None) -> Remaining:
        """What is left today, and for ``session_id`` when one is given (without one only the day's cap applies)."""
        s = self._settings
        day = await self._day()
        stt_day = 0 if day is None else max(0, s.stt_daily_seconds - day[0])
        tts_day = 0 if day is None else max(0, s.tts_daily_chars - day[1])
        if session_id is None:
            return Remaining(stt_day, tts_day, None, None)
        stt_used, tts_used = self._session(session_id)
        return Remaining(
            stt_day,
            tts_day,
            max(0, s.stt_session_seconds - stt_used),
            max(0, s.tts_session_chars - tts_used),
        )

    async def charge(self, session_id: str | None, *, stt_s: int = 0, tts_chars: int = 0) -> None:
        """Add ``stt_s`` seconds and ``tts_chars`` characters to today's record and to ``session_id``'s."""
        stt_s, tts_chars = max(0, stt_s), max(0, tts_chars)
        if not (stt_s or tts_chars):
            return
        if session_id is not None:
            used = self._session(session_id)
            used[0] += stt_s
            used[1] += tts_chars
        async with self._lock:
            day = await self._day()
            if day is None:
                return
            try:
                await self._kv.set(self.key(), {"stt_s": day[0] + stt_s, "tts_chars": day[1] + tts_chars})
            except Exception as exc:
                log.warning("speech_meter_unwritable", error=type(exc).__name__)

    def __len__(self) -> int:
        """How many sessions this process remembers."""
        return len(self._sessions)


__all__ = ["MAX_SESSIONS", "USAGE_KEY_PREFIX", "Remaining", "SpeechMeter"]
