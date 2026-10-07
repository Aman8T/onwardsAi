"""Pytest fixtures for the ingest tests; the helpers they wire up are in `helpers.py`."""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest
import respx

from tayyari.ingest.web import ssl_context
from tests.unit.ingest.helpers import FakeDns, FakeWeb, LogRecorder


@pytest.fixture(scope="session", autouse=True)
def _warm_tls_contexts() -> None:
    """Building the CA bundle context takes up to half a second; do it once, before any timing-sensitive test."""
    ssl_context(False)
    ssl_context(True)


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> FakeDns:
    """A fake resolver (it replaces ``socket.getaddrinfo``); an unregistered name fails the test."""
    fake = FakeDns()
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return fake


@pytest.fixture
def no_dns(dns: FakeDns) -> FakeDns:
    """The same resolver, for tests that assert ``no_dns.calls == []`` (nothing may be looked up at all)."""
    return dns


@pytest.fixture
def net() -> Iterator[respx.MockRouter]:
    """A respx router: an unmocked request raises instead of reaching the real network."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def web(net: respx.MockRouter) -> FakeWeb:
    """The fake internet: `web.serve(ip, handler)`, then read `web.requests`."""
    return FakeWeb(net)


@pytest.fixture
def record_logs(monkeypatch: pytest.MonkeyPatch) -> LogRecorder:
    """Route the ingest package's loggers (all of its modules) to one recorder."""
    recorder = LogRecorder()
    for module in ("tayyari.ingest", "tayyari.ingest.web", "tayyari.ingest.github", "tayyari.ingest.resume"):
        monkeypatch.setattr(f"{module}.logger", recorder)
    return recorder
