"""The container's speech wiring: the Sarvam engine exists exactly when there is a key and speech is on, a test can swap the
engine or the whole service, the engine's connection pool is closed at shutdown, and the key is never in a log."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from pydantic import SecretStr

from tayyari.container import Container, ContainerOverrides
from tayyari.core.settings import Settings, SpeechSettings
from tayyari.speech.sarvam import SarvamEngine
from tayyari.speech.service import SpeechService
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories
from tests.fakes.speech import FakeSpeechEngine
from tests.unit.wiring.support import FakeAnalysis, FakeChat, roomy_guard

KEY = "sk_test_WIRING_SECRET_KEY_0123456789"

MakeSettings = Callable[..., Settings]
Build = Callable[..., Awaitable[Container]]


@pytest.fixture
async def build() -> AsyncIterator[Build]:
    """``await build(settings, speech_engine=...)``: a container over fake repositories (no database), closed at the end."""
    built: list[Container] = []

    async def factory(settings: Settings, **overrides: Any) -> Container:
        values: dict[str, Any] = {
            "analysis": FakeAnalysis(),
            "chat": FakeChat(),
            "repos": FakeRepositories().bundle,
        }
        values.update(overrides)
        container = await Container.build(settings, overrides=ContainerOverrides(**values))
        built.append(container)
        return container

    yield factory
    for container in built:
        await container.aclose()


def settings_with(make_settings: MakeSettings, key: str | None, **speech: Any) -> Settings:
    return make_settings(
        guard=roomy_guard(),
        sarvam_api_key=None if key is None else SecretStr(key),
        speech=SpeechSettings(**speech),
    )


async def test_with_a_key_the_sarvam_engine_is_wired(make_settings: MakeSettings, build: Build) -> None:
    container = await build(settings_with(make_settings, KEY))
    assert isinstance(container.speech, SpeechService) and container.speech.configured
    assert isinstance(container.speech._engine, SarvamEngine)
    status = await container.speech.status()
    assert (status.stt, status.tts, status.provider) == (True, True, "sarvam")


async def test_without_a_key_there_is_no_engine_and_the_status_says_so(
    make_settings: MakeSettings, build: Build
) -> None:
    container = await build(settings_with(make_settings, None))
    assert container.speech._engine is None and not container.speech.configured
    status = await container.speech.status()
    assert (status.stt, status.tts, status.sttReason) == (False, False, "not_configured")


async def test_the_kill_switch_builds_no_engine_even_with_a_key(
    make_settings: MakeSettings, build: Build
) -> None:
    container = await build(settings_with(make_settings, KEY, enabled=False))
    assert container.speech._engine is None and not container.speech.configured
    assert (await container.speech.status()).sttReason == "not_configured"


async def test_a_test_can_hand_in_an_engine_and_the_service_still_meters_it(
    make_settings: MakeSettings, build: Build
) -> None:
    engine = FakeSpeechEngine()
    container = await build(settings_with(make_settings, None), speech_engine=engine)
    assert container.speech._engine is engine and container.speech.configured
    await container.speech.speak("a" * 32, "Hello there.", 1.0)
    assert await container.repos.kv.get(container.speech.meter.key()) == {"stt_s": 0, "tts_chars": 12}


async def test_the_kill_switch_beats_a_handed_in_engine(make_settings: MakeSettings, build: Build) -> None:
    container = await build(
        settings_with(make_settings, None, enabled=False), speech_engine=FakeSpeechEngine()
    )
    assert not container.speech.configured


async def test_a_test_can_hand_in_the_whole_service(make_settings: MakeSettings, build: Build) -> None:
    service = SpeechService(None, SpeechSettings(), FakeRepositories().kv, FakeClock())
    container = await build(settings_with(make_settings, KEY), speech=service)
    assert container.speech is service


async def test_shutdown_closes_the_engines_connection_pool(make_settings: MakeSettings) -> None:
    container = await Container.build(
        settings_with(make_settings, KEY),
        overrides=ContainerOverrides(
            analysis=FakeAnalysis(), chat=FakeChat(), repos=FakeRepositories().bundle
        ),
    )
    engine = container.speech._engine
    assert isinstance(engine, SarvamEngine) and engine._http is not None
    http = engine._http
    assert not http.is_closed
    await container.aclose()
    assert http.is_closed


async def test_the_key_never_reaches_the_log_or_the_container_repr(
    make_settings: MakeSettings,
    build: Build,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    container = await build(settings_with(make_settings, KEY))
    await container.speech.status()
    printed = capsys.readouterr()
    seen = printed.out + printed.err + caplog.text + repr(container) + repr(container.settings)
    assert KEY not in seen and "WIRING_SECRET" not in seen
    assert "speech_ready" in (caplog.text + seen)
