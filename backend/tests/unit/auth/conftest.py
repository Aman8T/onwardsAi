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
from tests.fakes.supabase import (
    PUBLISHABLE_KEY,
    SIGN_IN_URL,
    SIGN_OUT_URL,
    URL,
    FakeSupabase,
)

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
    supabase: FakeSupabase


MakeAuth = Callable[..., Rig]


@pytest.fixture
def make_auth(
    make_settings: Callable[..., Settings], clock: FakeClock, fakes: FakeRepositories, google_key: Any
) -> MakeAuth:
    """``make_auth(google=True, mailer=True, leafout=False, own_sign_in=True, auth={}, **settings)`` -> a `Rig`.

    ``google``: configure the Google client (id and secret). ``mailer``: give the service a recording mailer.
    ``leafout``: configure Sign in with the Leafout account (a `FakeSupabase` is installed on the rig's network).
    ``own_sign_in``: ``TAYYARI_AUTH__OWN_SIGN_IN`` (true here so the app's own paths are testable; the setting's default is
    false). ``auth``: more `AuthSettings` fields. Other keyword arguments are `Settings` fields (``env="local"``,
    ``resend_api_key="k"``...).
    """

    def build(
        *,
        google: bool = True,
        mailer: bool = True,
        leafout: bool = False,
        own_sign_in: bool = True,
        auth: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> Rig:
        auth_values: dict[str, Any] = {"public_url": PUBLIC_URL, "own_sign_in": own_sign_in}
        if leafout:
            auth_values.update(
                supabase_url=URL,
                supabase_publishable_key=PUBLISHABLE_KEY,
                supabase_sign_in_url=SIGN_IN_URL,
                supabase_sign_out_url=SIGN_OUT_URL,
            )
        auth_values.update(auth or {})
        values: dict[str, Any] = {"auth": auth_values}
        if google:
            values["google_client_id"] = CLIENT_ID
            values["google_client_secret"] = CLIENT_SECRET
        values.update(overrides)
        settings = make_settings(**values)
        net = FakeNetwork()
        fake_google = FakeGoogle(key=google_key)
        fake_google.install(net)
        supabase = FakeSupabase()
        supabase.install(net)
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
        return Rig(service, settings, clock, fakes, net, fake_google, fake_mailer, supabase)

    return build
