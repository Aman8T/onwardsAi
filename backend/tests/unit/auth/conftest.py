"""Fixtures for the sign-in tests: a manual clock, fake repositories, and a factory for `AuthService` wired to a
`FakeNetwork` (so no test can reach Google or Resend)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from tayyari.auth.service import AuthService
from tayyari.core.settings import Settings
from tests.fakes.auth import CLIENT_ID, CLIENT_SECRET, FakeGoogle, FakeMailer, FakeNetwork, new_key
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

PUBLIC_URL = "https://tayyari.example"
STATE_KEY = bytes(range(32))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fakes(clock: FakeClock) -> FakeRepositories:
    return FakeRepositories(clock)


@pytest.fixture(scope="session")
def google_key():  # type: ignore[no-untyped-def]
    """One RSA key for the whole run: generating it takes a few hundred milliseconds."""
    return new_key()


@dataclass
class Rig:
    """An `AuthService` with everything it talks to."""

    service: AuthService
    settings: Settings
    clock: FakeClock
    fakes: FakeRepositories
    net: FakeNetwork
    google: FakeGoogle
    mailer: FakeMailer


MakeAuth = Callable[..., Rig]


@pytest.fixture
def make_auth(
    make_settings: Callable[..., Settings], clock: FakeClock, fakes: FakeRepositories, google_key: Any
) -> MakeAuth:
    """``make_auth(google=True, mailer=True, **settings)`` -> a `Rig`.

    ``google``: configure the Google client (id and secret). ``mailer``: give the service a recording mailer.
    Other keyword arguments are `Settings` fields (``env="local"``, ``resend_api_key="k"``...).
    """

    def build(*, google: bool = True, mailer: bool = True, **overrides: Any) -> Rig:
        values: dict[str, Any] = {"auth": {"public_url": PUBLIC_URL}}
        if google:
            values["google_client_id"] = CLIENT_ID
            values["google_client_secret"] = CLIENT_SECRET
        values.update(overrides)
        settings = make_settings(**values)
        net = FakeNetwork()
        fake_google = FakeGoogle(key=google_key)
        fake_google.install(net)
        fake_mailer = FakeMailer()
        service = AuthService(
            settings=settings,
            users=fakes.users,
            limits=fakes.limits,
            clock=clock,
            state_key=STATE_KEY,
            mailer=fake_mailer if mailer else None,
            transport=net.transport,
        )
        return Rig(service, settings, clock, fakes, net, fake_google, fake_mailer)

    return build
