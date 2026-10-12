"""Sessions: the cookie token, what is stored, the sliding expiry, and the cookie flags."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from http.cookies import SimpleCookie
from typing import Any

import pytest
from starlette.responses import Response

from tayyari.auth.sessions import (
    TOKEN_MAX_CHARS,
    TOUCH_INTERVAL,
    SessionService,
    clear_session_cookie,
    clip_user_agent,
    hash_token,
    new_token,
    set_session_cookie,
)
from tayyari.core.settings import AuthSettings, Settings
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

DAY = 86_400


async def make_user(fakes: FakeRepositories, clock: FakeClock) -> str:
    user = await fakes.users.sign_in_email(email="ana@example.com", now=clock.now())
    return user.id


def service_for(fakes: FakeRepositories, clock: FakeClock, **auth: Any) -> SessionService:
    return SessionService(fakes.users, clock, AuthSettings(**auth))


async def test_a_started_session_stores_only_the_hash_of_the_token(
    fakes: FakeRepositories, clock: FakeClock
) -> None:
    service = service_for(fakes, clock)
    user_id = await make_user(fakes, clock)
    token, record = await service.start(user_id, ip_hash="abcd", user_agent="Mozilla/5.0")

    assert len(token) >= 43 and token not in repr(fakes.users.sessions)
    assert record.id == hash_token(token) and len(record.id) == 64
    assert list(fakes.users.sessions) == [record.id]
    assert record.expires_at == clock.now() + timedelta(days=30)
    assert (record.ip_hash, record.user_agent) == ("abcd", "Mozilla/5.0")


def test_tokens_are_random_and_long() -> None:
    assert len({new_token() for _ in range(50)}) == 50
    assert all(len(new_token()) == 43 for _ in range(5))  # 32 bytes, urlsafe base64 without padding


async def test_resolve_finds_the_user_behind_a_token(fakes: FakeRepositories, clock: FakeClock) -> None:
    service = service_for(fakes, clock)
    user_id = await make_user(fakes, clock)
    token, _ = await service.start(user_id, ip_hash=None, user_agent=None)

    found = await service.resolve(token)

    assert found is not None
    assert (found.user.id, found.user.email, found.refreshed) == (user_id, "ana@example.com", False)


@pytest.mark.parametrize("bad", [None, "", "nope", "x" * (TOKEN_MAX_CHARS + 1)])
async def test_anything_that_is_not_a_live_token_is_no_one(
    fakes: FakeRepositories, clock: FakeClock, bad: str | None
) -> None:
    service = service_for(fakes, clock)
    await service.start(await make_user(fakes, clock), ip_hash=None, user_agent=None)
    assert await service.resolve(bad) is None


async def test_an_expired_session_is_refused_and_removed(fakes: FakeRepositories, clock: FakeClock) -> None:
    service = service_for(fakes, clock, session_days=30)
    token, _ = await service.start(await make_user(fakes, clock), ip_hash=None, user_agent=None)

    clock.advance(30 * DAY + 1)
    assert await service.resolve(token) is None
    assert fakes.users.sessions == {}  # deleted on the spot


async def test_a_session_lives_until_its_last_second(fakes: FakeRepositories, clock: FakeClock) -> None:
    service = service_for(fakes, clock, session_days=30)
    token, _ = await service.start(await make_user(fakes, clock), ip_hash=None, user_agent=None)
    clock.advance(30 * DAY - 1)
    assert await service.resolve(token) is not None


async def test_the_expiry_slides_but_the_row_is_written_at_most_hourly(
    fakes: FakeRepositories, clock: FakeClock
) -> None:
    service = service_for(fakes, clock)
    token, started = await service.start(await make_user(fakes, clock), ip_hash=None, user_agent=None)

    clock.advance(TOUCH_INTERVAL.total_seconds() - 60)
    quiet = await service.resolve(token)
    assert quiet is not None and quiet.refreshed is False
    assert fakes.users.sessions[started.id].last_seen_at == started.last_seen_at  # a read, not a write

    clock.advance(120)  # now over an hour since the last write
    touched = await service.resolve(token)
    assert touched is not None and touched.refreshed is True
    stored = fakes.users.sessions[started.id]
    assert stored.last_seen_at == clock.now()
    assert stored.expires_at == clock.now() + timedelta(days=30)

    again = await service.resolve(token)  # straight after: nothing more to write
    assert again is not None and again.refreshed is False


async def test_a_session_that_is_used_keeps_living_past_the_original_expiry(
    fakes: FakeRepositories, clock: FakeClock
) -> None:
    service = service_for(fakes, clock, session_days=30)
    token, _ = await service.start(await make_user(fakes, clock), ip_hash=None, user_agent=None)
    for _ in range(4):  # a visit every 20 days for 80 days
        clock.advance(20 * DAY)
        assert await service.resolve(token) is not None


async def test_a_failing_refresh_does_not_fail_the_request(
    fakes: FakeRepositories, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = service_for(fakes, clock)
    token, _ = await service.start(await make_user(fakes, clock), ip_hash=None, user_agent=None)

    async def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("database is busy")

    monkeypatch.setattr(fakes.users, "touch_session", boom)
    clock.advance(2 * 3600)
    found = await service.resolve(token)
    assert found is not None and found.refreshed is False


async def test_ending_a_session_signs_out(fakes: FakeRepositories, clock: FakeClock) -> None:
    service = service_for(fakes, clock)
    token, _ = await service.start(await make_user(fakes, clock), ip_hash=None, user_agent=None)
    assert await service.end(token) is True
    assert await service.resolve(token) is None
    assert await service.end(token) is False
    assert await service.end(None) is False


def test_the_user_agent_is_trimmed_to_what_the_column_holds() -> None:
    assert clip_user_agent(None) is None
    assert clip_user_agent("  ") is None
    assert (
        clip_user_agent("Mozilla/5.0\r\nX-Evil: 1") == "Mozilla/5.0X-Evil: 1"
    )  # control characters are dropped
    assert len(clip_user_agent("a" * 500) or "") == 200


# ───────────────────────── the cookie ─────────────────────────


def cookie_of(response: Response) -> SimpleCookie:
    jar: SimpleCookie = SimpleCookie()
    jar.load(response.headers["set-cookie"])
    return jar


def test_the_cookie_is_httponly_lax_and_lives_thirty_days(make_settings: Callable[..., Settings]) -> None:
    response = Response()
    set_session_cookie(response, "tok", make_settings())
    morsel = cookie_of(response)["tayyari_session"]
    assert morsel.value == "tok"
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"
    assert int(morsel["max-age"]) == 30 * DAY
    assert not morsel["secure"]  # local development is plain http


def test_the_cookie_is_secure_in_prod(make_settings: Callable[..., Settings]) -> None:
    response = Response()
    set_session_cookie(response, "tok", make_settings(env="prod"))
    assert cookie_of(response)["tayyari_session"]["secure"] is True


def test_the_cookie_is_secure_when_the_public_url_is_https(make_settings: Callable[..., Settings]) -> None:
    response = Response()
    set_session_cookie(response, "tok", make_settings(auth={"public_url": "https://tayyari.example"}))
    assert cookie_of(response)["tayyari_session"]["secure"] is True


def test_the_lifetime_and_the_name_follow_the_settings(make_settings: Callable[..., Settings]) -> None:
    response = Response()
    set_session_cookie(response, "tok", make_settings(auth={"session_days": 7, "cookie_name": "sid"}))
    assert int(cookie_of(response)["sid"]["max-age"]) == 7 * DAY


def test_clearing_the_cookie_uses_the_same_name_path_and_flags(
    make_settings: Callable[..., Settings],
) -> None:
    response = Response()
    clear_session_cookie(response, make_settings(env="prod"))
    morsel = cookie_of(response)["tayyari_session"]
    assert morsel.value == "" and int(morsel["max-age"]) == 0
    assert morsel["path"] == "/" and morsel["httponly"] is True and morsel["secure"] is True
