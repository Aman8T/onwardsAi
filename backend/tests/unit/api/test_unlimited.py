"""`api.deps.unlimited`: who has no limits. The fast paths answer without looking anyone up (anonymous traffic must cost what
it cost before), the lookup is the cached session lookup, and the answer is kept on the request. The routes are covered end to
end in `tests/integration/test_unlimited_accounts.py`."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from tayyari.api import deps
from tayyari.api.deps import unlimited
from tayyari.core.settings import AuthSettings, GuardSettings
from tayyari.storage.ports import StoredUser
from tests.unit.api.helpers import http_scope

COOKIE = "tayyari_session"


def account(email: str) -> StoredUser:
    then = datetime(2026, 10, 11, tzinfo=UTC)
    return StoredUser(
        id="a" * 32,
        email=email,
        name=None,
        picture_url=None,
        google_sub=None,
        created_at=then,
        last_login_at=then,
        locale=None,
        currency=None,
    )


class Rig:
    """A container with only settings, and a stand-in for the session lookup that counts how often it is asked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, listed: str) -> None:
        self.lookups = 0
        self.signed_in: StoredUser | None = None
        self.fail: Exception | None = None
        self.container: Any = SimpleNamespace(
            settings=SimpleNamespace(guard=GuardSettings(unlimited_emails=listed), auth=AuthSettings())
        )

        async def current(request: Request) -> StoredUser | None:
            self.lookups += 1
            if self.fail is not None:
                raise self.fail
            return self.signed_in

        monkeypatch.setattr(deps, "get_current_user", current)

    @staticmethod
    def request(*, cookie: str | None = "token") -> Request:
        headers = [] if cookie is None else [("cookie", f"{COOKIE}={cookie}")]
        return Request(http_scope(path="/api/match", headers=headers))

    async def ask(self, request: Request) -> bool:
        return await unlimited(request, self.container)


def rig(monkeypatch: pytest.MonkeyPatch, listed: str = "tester@example.com") -> Rig:
    return Rig(monkeypatch, listed=listed)


async def test_an_empty_list_answers_false_without_looking_anyone_up(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch, listed="")
    r.signed_in = account("tester@example.com")
    assert await r.ask(r.request()) is False
    assert r.lookups == 0


async def test_a_blank_list_counts_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch, listed=" , ;  ")
    assert await r.ask(r.request()) is False
    assert r.lookups == 0


async def test_no_session_cookie_answers_false_without_looking_anyone_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = rig(monkeypatch)
    r.signed_in = account("tester@example.com")
    assert await r.ask(r.request(cookie=None)) is False
    assert r.lookups == 0


async def test_another_cookie_is_not_a_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch)
    request = Request(http_scope(path="/", headers=[("cookie", "theme=dark; tayyari_state=x")]))
    assert await r.ask(request) is False
    assert r.lookups == 0


async def test_a_listed_account_is_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch)
    r.signed_in = account("Tester@Example.com")
    assert await r.ask(r.request()) is True
    assert r.lookups == 1


async def test_an_account_not_on_the_list_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch)
    r.signed_in = account("someone@example.com")
    assert await r.ask(r.request()) is False


async def test_a_cookie_that_names_no_live_sign_in_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch)
    r.signed_in = None
    assert await r.ask(r.request(cookie="garbage")) is False
    assert r.lookups == 1


async def test_the_answer_is_kept_on_the_request_so_asking_twice_costs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = rig(monkeypatch)
    r.signed_in = account("tester@example.com")
    request = r.request()
    assert [await r.ask(request), await r.ask(request), await r.ask(request)] == [True, True, True]
    assert r.lookups == 1


async def test_a_negative_answer_is_kept_too(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch)
    r.signed_in = account("someone@example.com")
    request = r.request()
    assert [await r.ask(request), await r.ask(request)] == [False, False]
    assert r.lookups == 1


async def test_the_fast_path_is_kept_too(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch, listed="")
    request = r.request()
    await r.ask(request)
    r.container.settings.guard = GuardSettings(
        unlimited_emails="tester@example.com"
    )  # not read again for this request
    r.signed_in = account("tester@example.com")
    assert await r.ask(request) is False
    assert r.lookups == 0


async def test_each_request_is_answered_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch)
    r.signed_in = account("tester@example.com")
    first, second = r.request(), r.request()
    assert await r.ask(first) is True
    r.signed_in = account("someone@example.com")
    assert await r.ask(second) is False
    assert r.lookups == 2


async def test_a_failing_lookup_means_the_limits_apply_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch)
    r.fail = RuntimeError("database unreachable")
    assert await r.ask(r.request()) is False
    assert r.lookups == 1


async def test_the_cookie_name_comes_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    r = rig(monkeypatch)
    r.container.settings.auth = AuthSettings(cookie_name="sid")
    r.signed_in = account("tester@example.com")
    assert await r.ask(r.request()) is False  # the default cookie is not the one asked for
    assert r.lookups == 0
    request = Request(http_scope(path="/", headers=[("cookie", "sid=token")]))
    assert await r.ask(request) is True
