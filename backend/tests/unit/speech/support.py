"""Builders for the speech tests: a service over the fake key-value store, a fake clock and a fake engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tayyari.core.settings import SpeechSettings
from tayyari.speech.service import SpeechService
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeKvRepo, FakeRepositories
from tests.fakes.speech import FakeSpeechEngine

NOON = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
SESSION = "a" * 32
OTHER_SESSION = "b" * 32
WEBM = "audio/webm;codecs=opus"


@dataclass
class Rig:
    service: SpeechService
    engine: FakeSpeechEngine | None
    kv: FakeKvRepo
    clock: FakeClock
    settings: SpeechSettings = field(default_factory=SpeechSettings)

    def used_today(self) -> dict[str, Any]:
        """The day's record as the meter keeps it (empty when nothing was charged)."""
        found: dict[str, Any] | None = self.kv.values.get(self.service.meter.key())
        return found or {}


def make_rig(*, engine: FakeSpeechEngine | None = None, silent: bool = False, **settings: Any) -> Rig:
    """A `SpeechService` over fakes. ``silent`` builds it with no engine at all (no key); otherwise ``engine`` or a new fake."""
    clock = FakeClock(NOON)
    fakes = FakeRepositories(clock)
    cfg = SpeechSettings(**settings)
    chosen = None if silent else (engine if engine is not None else FakeSpeechEngine())
    return Rig(SpeechService(chosen, cfg, fakes.kv, clock), chosen, fakes.kv, clock, cfg)
