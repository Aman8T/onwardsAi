"""`TAYYARI_HANDOFF__*` read from the environment the way the docs in `.env.example` say."""

from __future__ import annotations

import pytest

from tayyari.core.settings import Settings


def test_origins_from_the_environment_as_a_comma_list_or_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_HANDOFF__ORIGINS", "https://a.example, https://B.example/")
    monkeypatch.setenv("TAYYARI_HANDOFF__TTL_SECONDS", "120")
    handoff = Settings(_env_file=None).handoff
    assert handoff.origins == ["https://a.example", "https://b.example"] and handoff.ttl_seconds == 120
    monkeypatch.setenv("TAYYARI_HANDOFF__ORIGINS", '["https://c.example"]')
    assert Settings(_env_file=None).handoff.origins == ["https://c.example"]


def test_the_defaults_are_the_site(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAYYARI_HANDOFF__ORIGINS", raising=False)
    handoff = Settings(_env_file=None).handoff
    assert handoff.origins == ["https://leafout.in", "https://www.leafout.in"]
    assert (handoff.ttl_seconds, handoff.max_bytes, handoff.allow_localhost) == (900, 10 * 1024 * 1024, None)
