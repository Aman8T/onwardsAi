"""Accounts in the composition root and the app: the container builds `AuthService`, the state key is shared through
the database, shutdown closes what it opened, and the routes are registered ahead of the web app's catch-all."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from tayyari.api.app import create_app
from tayyari.auth.email import ResendEmailSender
from tayyari.auth.google import STATE_KEY_NAME
from tayyari.auth.service import AuthService
from tayyari.container import Container, ContainerOverrides
from tayyari.core.settings import Settings
from tayyari.storage.tables import KvRow
from tests.fakes.auth import FakeMailer
from tests.fakes.repos import FakeRepositories
from tests.unit.api.helpers import RecordingLog
from tests.unit.wiring.support import FakeAnalysis, FakeChat

MakeSettings = Callable[..., Settings]


async def build(settings: Settings, **overrides: Any) -> Container:
    values: dict[str, Any] = {"analysis": FakeAnalysis(), "chat": FakeChat(), **overrides}
    return await Container.build(settings, overrides=ContainerOverrides(**values))


async def test_the_container_builds_an_auth_service_from_the_settings(make_settings: MakeSettings) -> None:
    container = await build(make_settings(google_client_id="id", google_client_secret="secret"))
    try:
        assert isinstance(container.auth, AuthService)
        assert container.auth.providers == (True, False)  # test env, no mail key
    finally:
        await container.aclose()


async def test_a_mail_key_builds_the_resend_sender_and_a_local_server_without_one_uses_the_dev_link(
    make_settings: MakeSettings,
) -> None:
    with_key = await build(make_settings(resend_api_key="re_key"))
    local = await build(make_settings(env="local"))
    try:
        assert isinstance(with_key.auth.magic._mailer, ResendEmailSender)
        assert with_key.auth.providers == (False, True)
        assert local.auth.magic._mailer is None and local.auth.magic.dev_mode is True
        assert local.auth.providers == (False, True)
    finally:
        await with_key.aclose()
        await local.aclose()


async def test_an_injected_mailer_and_service_are_used_as_given(make_settings: MakeSettings) -> None:
    mailer = FakeMailer()
    container = await build(make_settings(), mailer=mailer)
    try:
        assert container.auth.magic._mailer is mailer and container.auth.providers == (False, True)
    finally:
        await container.aclose()
    own = await build(make_settings(), auth=container.auth)
    try:
        assert own.auth is container.auth
    finally:
        await own.aclose()


async def test_the_state_key_is_made_once_and_kept_in_the_database(make_settings: MakeSettings) -> None:
    fakes = FakeRepositories()
    first = await build(make_settings(), repos=fakes.bundle)
    second = await build(make_settings(), repos=fakes.bundle)
    try:
        stored = fakes.kv.values[STATE_KEY_NAME]
        assert isinstance(stored, str) and len(bytes.fromhex(stored)) == 32
        assert first.auth._state_key == second.auth._state_key == bytes.fromhex(stored)  # replicas agree
    finally:
        await first.aclose()
        await second.aclose()


async def test_the_state_key_is_a_secret_row_in_the_real_database(make_settings: MakeSettings) -> None:
    container = await Container.build(
        make_settings(), overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat())
    )
    try:
        assert container.db is not None
        async with container.db.session() as s:
            value = (await s.execute(select(KvRow.value).where(KvRow.key == STATE_KEY_NAME))).scalar_one()
        assert bytes.fromhex(value) == container.auth._state_key
    finally:
        await container.aclose()


async def test_the_http_client_it_opened_is_closed_with_the_container(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []
    original = httpx2.AsyncClient.aclose

    async def spy(self: httpx2.AsyncClient) -> None:
        closed.append("auth_http")
        await original(self)

    monkeypatch.setattr(httpx2.AsyncClient, "aclose", spy)
    container = await build(make_settings())
    await container.aclose()
    assert "auth_http" in closed


async def test_a_production_server_pointing_at_localhost_says_so(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tayyari.auth import service as service_module

    log = RecordingLog()
    monkeypatch.setattr(service_module, "log", log)
    container = await build(make_settings(env="prod"))  # no FASTAPI_PUBLIC_URL: the default is the dev server
    await container.aclose()
    assert [event for _level, event, _fields in log.records] == ["auth_public_url_is_local"]

    log.records.clear()
    ok = await build(make_settings(env="prod", auth={"public_url": "https://tayyari.example"}))
    await ok.aclose()
    assert log.records == []


async def test_the_account_routes_are_registered_before_the_web_apps_catch_all(
    make_settings: MakeSettings,
) -> None:
    app = create_app(make_settings())

    def flatten(routes: Any) -> list[tuple[str, tuple[str, ...]]]:
        # FastAPI keeps an included router as one entry; the web app's catch-all routes follow it
        out: list[tuple[str, tuple[str, ...]]] = []
        for route in routes:
            inner = getattr(getattr(route, "original_router", None), "routes", None)
            if inner is not None:
                out.extend(flatten(inner))
            else:
                out.append((route.path, tuple(sorted(route.methods or ()))))
        return out

    ordered = flatten(app.router.routes)
    expected = [
        ("/api/me", ("GET",)),
        ("/api/me", ("PATCH",)),
        ("/api/me", ("DELETE",)),
        ("/api/me/claim", ("POST",)),
        ("/api/auth/google/start", ("GET",)),
        ("/api/auth/google/callback", ("GET",)),
        ("/api/auth/magic", ("POST",)),
        ("/api/auth/magic/verify", ("GET",)),
        ("/api/auth/logout", ("POST",)),
    ]
    catch_all = ordered.index(("/{path:path}", ("GET", "HEAD")))
    for entry in expected:
        assert entry in ordered, entry
        assert ordered.index(entry) < catch_all  # or the SPA fallback would answer first


async def test_without_a_lifespan_the_account_routes_answer_503_not_500(make_settings: MakeSettings) -> None:
    import httpx

    app = create_app(make_settings())  # no lifespan: the container does not exist
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/me")
    assert response.status_code == 503


async def test_a_running_app_answers_me(make_settings: MakeSettings) -> None:
    import httpx

    app = create_app(make_settings(), overrides=ContainerOverrides(analysis=FakeAnalysis(), chat=FakeChat()))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/me")
    assert response.status_code == 200 and "user" not in response.json()
