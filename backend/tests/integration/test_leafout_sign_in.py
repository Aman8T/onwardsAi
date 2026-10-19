"""Sign in with the Leafout account through the real app: the site's ``leafout-access`` cookie becomes an app session on the
first ``/api/`` request that needs to know who is calling, the session lives on its own afterwards, the cookie rules of the
amendment (absent ends the session at once, a different person swaps, an expired token asks for ``reauth``), the refusals
(``email_taken`` shows up as ``signInIssue``), the limits, the hardening and the redirect from the old host.

Only the network edges are faked: Supabase's key set and user endpoint are a `FakeSupabase` behind a mock transport, and a
request nobody registered fails the test."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from http.cookies import SimpleCookie
from typing import Any

import httpx
import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute

from tayyari.api.deps import get_auth_context
from tayyari.core.settings import Settings
from tayyari.storage.tables import AuthSessionRow
from tests.fakes.auth import FakeMailer, FakeNetwork
from tests.fakes.clock import FakeClock
from tests.fakes.supabase import (
    COOKIE,
    EMAIL,
    OTHER_SUB,
    SIGN_IN_URL,
    SIGN_OUT_URL,
    SUB,
    FakeSupabase,
    cookie_for,
)
from tests.integration.support import Env, roomy_guard, running

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
PUBLIC = "https://app.leafout.example"
HOUR = 3600


@dataclass
class Rig:
    env: Env
    net: FakeNetwork
    supabase: FakeSupabase
    clock: FakeClock

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    def token(self, **claims: Any) -> str:
        return self.supabase.mint(self.clock.now(), **claims)

    async def me(self, token: str | None = None, session: str | None = None) -> httpx.Response:
        self.client.cookies.clear()
        headers: dict[str, str] = {}
        parts = []
        if token is not None:
            parts.append(f"{COOKIE}={token}")
        if session is not None:
            parts.append(f"{SESSION}={session}")
        if parts:
            headers["cookie"] = "; ".join(parts)
        return await self.client.get("/api/me", headers=headers)


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    """``async with start(guard={...}, auth={...}) as rig``: the app with Leafout sign-in configured against a fake project."""

    @asynccontextmanager
    async def run(
        *, auth: dict[str, Any] | None = None, guard: dict[str, Any] | None = None, **overrides: Any
    ) -> AsyncIterator[Rig]:
        auth_values: dict[str, Any] = {
            "public_url": PUBLIC,
            "supabase_url": "https://proj.supabase.example",
            "supabase_publishable_key": "sb_publishable_for_tests",
            "supabase_sign_in_url": SIGN_IN_URL,
            "supabase_sign_out_url": SIGN_OUT_URL,
        }
        auth_values.update(auth or {})
        values: dict[str, Any] = {"auth": auth_values, "guard": roomy_guard(**(guard or {}))}
        values.update(overrides)
        net, supabase, clock = FakeNetwork(), FakeSupabase(), FakeClock()
        supabase.install(net)
        async with running(
            build_settings(**values), clock=clock, auth_transport=net.transport, mailer=FakeMailer()
        ) as env:
            yield Rig(env, net, supabase, clock)

    return run


def morsels(response: httpx.Response) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        out.update(jar)
    return out


def raw_cookie(response: httpx.Response, name: str) -> str:
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}="))


# ───────────────────────── the bridge ─────────────────────────


async def test_the_sites_cookie_signs_the_person_in_and_the_app_sets_its_own_session(start: Any) -> None:
    async with start() as rig:
        res = await rig.me(rig.token())

        assert res.status_code == 200
        body = res.json()
        assert body["user"]["email"] == EMAIL and body["user"]["name"] == "Ana Rao"
        assert body["providers"] == {
            "google": False,
            "magic": False,
            "leafout": True,
            "leafoutSignInUrl": SIGN_IN_URL,
            "leafoutSignOutUrl": SIGN_OUT_URL,
        }
        assert "reauth" not in body and "signInIssue" not in body
        # the cookie: the app's own, host-only (no Domain), HttpOnly, Lax, Secure (the public URL is https)
        header = raw_cookie(res, SESSION)
        lowered = header.lower()
        assert "httponly" in lowered and "samesite=lax" in lowered and "secure" in lowered
        assert "domain=" not in lowered and "path=/" in lowered and "max-age=2592000" in lowered
        assert COOKIE not in {
            name for name in morsels(res)
        }  # the app never sets, refreshes or deletes the site's cookie


async def test_the_session_then_stands_on_its_own_without_calling_supabase_again(start: Any) -> None:
    async with start() as rig:
        first = await rig.me(rig.token())
        session = morsels(first)[SESSION].value
        assert len(rig.supabase.user_requests) == 1

        for _ in range(3):
            again = await rig.me(
                rig.token(), session
            )  # the cookie still comes along, as it will in a browser
            assert again.status_code == 200 and again.json()["user"]["email"] == EMAIL
            assert SESSION not in morsels(again)  # nothing to re-issue

        assert len(rig.supabase.user_requests) == 1


async def test_a_bad_token_is_anonymous_with_no_set_cookie(start: Any) -> None:
    async with start() as rig:
        for token in ("garbage", rig.token(role="anon"), rig.token(aud=None)):
            res = await rig.me(token)
            assert res.status_code == 200 and "user" not in res.json()
            assert res.headers.get_list("set-cookie") == []
        assert rig.supabase.user_requests == []
        assert (await rig.env.container.repos.users.get_by_email(EMAIL)) is None


async def test_no_cookie_at_all_is_just_an_anonymous_visitor(start: Any) -> None:
    async with start() as rig:
        res = await rig.me()
        assert (
            res.status_code == 200 and "user" not in res.json() and res.headers.get_list("set-cookie") == []
        )
        assert rig.supabase.jwks_requests == [] and rig.supabase.user_requests == []


async def test_a_revoked_site_session_is_anonymous(start: Any) -> None:
    async with start() as rig:
        rig.supabase.user_status = 401
        res = await rig.me(rig.token())
        assert (
            res.status_code == 200 and "user" not in res.json() and res.headers.get_list("set-cookie") == []
        )


async def test_a_gate_over_the_auth_bridge_limit_is_anonymous_not_a_429(start: Any) -> None:
    async with start(guard={"auth_bridge_per_minute": 1, "auth_bridge_per_hour": 0}) as rig:
        first = await rig.me(rig.token())
        assert "user" in first.json()
        second = await rig.me(rig.token())
        assert second.status_code == 200 and "user" not in second.json()
        assert second.headers.get_list("set-cookie") == []
        assert len(rig.supabase.user_requests) == 1  # the limit stopped the second call before Supabase


async def test_the_per_account_daily_limit_is_anonymous_too(start: Any) -> None:
    async with start(guard={"auth_bridge_per_sub_per_day": 2}) as rig:
        results = [await rig.me(rig.token()) for _ in range(3)]
        assert ["user" in r.json() for r in results] == [True, True, False]


async def test_a_session_started_from_the_cookie_is_marked_and_stored(start: Any) -> None:
    async with start() as rig:
        await rig.me(rig.token())
        users = rig.env.container.repos.users
        user = await users.get_by_email(EMAIL)
        assert user is not None and user.supabase_sub == SUB
        [stored] = await rig.env.rows(AuthSessionRow)
        assert stored.source == "supabase" and stored.user_id == user.id
        assert stored.verified_at is not None and stored.supabase_session_id


# ───────────────────────── later requests: the cookie rules ─────────────────────────


async def test_the_site_signing_out_ends_the_app_session_at_once(start: Any) -> None:
    async with start() as rig:
        session = morsels(await rig.me(rig.token()))[SESSION].value

        gone = await rig.me(None, session)  # the site deleted the cookie

        assert gone.status_code == 200 and "user" not in gone.json() and "reauth" not in gone.json()
        assert (
            'tayyari_session=""' in raw_cookie(gone, SESSION)
            or "max-age=0" in raw_cookie(gone, SESSION).lower()
        )
        assert (await rig.me(None, session)).json().get("user") is None  # the session is really gone
        assert await rig.env.rows(AuthSessionRow) == []


async def test_a_cookie_that_is_garbage_or_badly_signed_ends_the_session_like_an_absent_one(
    start: Any,
) -> None:
    async with start() as rig:
        for cookie in ("garbage", None):
            session = morsels(await rig.me(rig.token()))[SESSION].value
            if cookie is None:
                cookie = rig.token(iss="https://other.example/auth/v1")
            res = await rig.me(cookie, session)
            assert "user" not in res.json() and "reauth" not in res.json()


async def test_someone_else_signing_in_on_the_site_swaps_the_session(start: Any) -> None:
    async with start() as rig:
        first = await rig.me(rig.token())
        session = morsels(first)[SESSION].value

        rig.supabase.email, rig.supabase.name = "ben@example.com", "Ben Iyer"
        swapped = await rig.me(rig.token(sub=OTHER_SUB), session)

        assert swapped.json()["user"]["email"] == "ben@example.com"
        new_session = morsels(swapped)[SESSION].value
        assert new_session != session
        old = await rig.me(None, session)
        assert "user" not in old.json()  # the first person's session ended


async def test_an_expired_token_for_another_person_ends_the_session_and_asks_for_reauth(start: Any) -> None:
    async with start() as rig:
        session = morsels(await rig.me(rig.token()))[SESSION].value
        rig.clock.advance(3 * HOUR)
        expired = rig.supabase.mint(rig.clock.now() - timedelta(hours=3), sub=OTHER_SUB)

        res = await rig.me(expired, session)

        assert res.json().get("reauth") is True and "user" not in res.json()
        assert "max-age=0" in raw_cookie(res, SESSION).lower() or 'tayyari_session=""' in raw_cookie(
            res, SESSION
        )


async def test_the_daily_recheck_slides_with_a_current_token_and_asks_for_reauth_with_an_expired_one(
    start: Any,
) -> None:
    async with start() as rig:
        session = morsels(await rig.me(rig.token()))[SESSION].value

        rig.clock.advance(25 * HOUR)
        slid = await rig.me(rig.token(), session)  # a fresh token for the same person
        assert slid.json()["user"]["email"] == EMAIL and "reauth" not in slid.json()
        assert len(rig.supabase.user_requests) == 1  # a recheck is local: no new Supabase call

        rig.clock.advance(25 * HOUR)
        stale = rig.supabase.mint(rig.clock.now() - timedelta(hours=5))
        res = await rig.me(stale, session)
        assert res.json().get("reauth") is True and "user" not in res.json()


async def test_the_apps_own_sessions_are_left_alone_by_the_cookie_rules(start: Any) -> None:
    async with start(auth={"own_sign_in": True}, env="local") as rig:
        users = rig.env.container.repos.users
        user = await users.sign_in_email(email="own@example.com", now=rig.clock.now())
        token, _ = await rig.env.container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        res = await rig.me(None, token)
        assert res.json()["user"]["email"] == "own@example.com"
        assert (await rig.me("garbage", token)).json()["user"]["email"] == "own@example.com"


# ───────────────────────── refusals the person can read ─────────────────────────


async def test_email_taken_reaches_me_as_sign_in_issue(start: Any) -> None:
    async with start() as rig:
        users = rig.env.container.repos.users
        await users.sign_in_supabase(
            sub=OTHER_SUB, email=EMAIL, email_verified=True, name=None, now=rig.clock.now()
        )

        res = await rig.me(rig.token())

        body = res.json()
        assert res.status_code == 200 and "user" not in body
        assert body["signInIssue"] == "email_taken"
        assert res.headers.get_list("set-cookie") == []
        assert await rig.env.rows(AuthSessionRow) == []


async def test_a_phone_only_person_is_signed_in_without_an_email_in_the_answer(start: Any) -> None:
    async with start() as rig:
        rig.supabase.email, rig.supabase.phone, rig.supabase.name = None, "+919800000000", None
        res = await rig.me(rig.token(email=None))
        assert res.status_code == 200
        user = res.json()["user"]
        assert "email" not in user and user["id"]
        assert "9800000000" not in res.text  # the phone number is never stored or returned


# ───────────────────────── the other routes ─────────────────────────


async def test_logout_ends_the_session_and_clears_the_cookie(start: Any) -> None:
    async with start() as rig:
        session = morsels(await rig.me(rig.token()))[SESSION].value
        rig.client.cookies.clear()
        res = await rig.client.post("/api/auth/logout", headers={"cookie": f"{SESSION}={session}"})
        assert res.status_code == 204
        header = raw_cookie(res, SESSION).lower()
        assert "max-age=0" in header and "domain=" not in header
        assert COOKIE not in header


async def test_logout_with_only_the_sites_cookie_leaves_no_new_session_behind(start: Any) -> None:
    async with start() as rig:
        rig.client.cookies.clear()
        res = await rig.client.post("/api/auth/logout", headers=cookie_for(rig.token()))
        assert res.status_code == 204
        assert await rig.env.rows(AuthSessionRow) == []


async def test_logout_never_bridges_even_when_the_unlimited_list_makes_it_look_the_caller_up(
    start: Any,
) -> None:
    async with start(guard={"unlimited_emails": EMAIL}) as rig:
        rig.client.cookies.clear()
        res = await rig.client.post("/api/auth/logout", headers=cookie_for(rig.token()))
        assert res.status_code == 204
        assert await rig.env.rows(AuthSessionRow) == [] and rig.supabase.user_requests == []


async def test_an_unlimited_account_that_arrives_with_only_the_sites_cookie_is_recognised(start: Any) -> None:
    async with start(guard={"unlimited_emails": EMAIL}) as rig:
        res = await rig.me(rig.token())
        assert res.json()["entitlements"]["unlimited"] is True


async def test_a_phone_only_account_is_never_unlimited(start: Any) -> None:
    async with start(guard={"unlimited_emails": EMAIL}) as rig:
        rig.supabase.email, rig.supabase.name = None, None
        res = await rig.me(rig.token(email=None))
        assert "user" in res.json() and "unlimited" not in res.json()["entitlements"]


async def test_the_apps_own_magic_link_answers_404_while_own_sign_in_is_off(start: Any) -> None:
    async with start() as rig:
        res = await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        assert res.status_code == 404 and res.json() == {"error": "not_offered"}
        verify = await rig.client.get("/api/auth/magic/verify", params={"token": "x" * 43})
        assert verify.status_code == 302 and verify.headers["location"] == "/?auth=link_invalid"
        google = await rig.client.get("/api/auth/google/start")
        assert google.status_code == 302 and google.headers["location"] == "/?auth=google_unavailable"
        callback = await rig.client.get("/api/auth/google/callback", params={"code": "c", "state": "s"})
        assert callback.status_code == 302 and callback.headers["location"] == "/?auth=google_unavailable"


async def test_a_cross_subdomain_post_is_refused(start: Any) -> None:
    async with start() as rig:
        session = morsels(await rig.me(rig.token()))[SESSION].value
        rig.client.cookies.clear()
        site = {
            "cookie": f"{SESSION}={session}",
            "origin": "https://leafout.in",
            "sec-fetch-site": "same-site",
            "host": "app.leafout.in",
        }
        for path in ("/api/match", "/api/me/claim", "/api/auth/logout"):
            res = await rig.client.post(path, headers=site, json={})
            assert res.status_code == 403, path
        mine = {**site, "origin": "https://app.leafout.in"}
        ok = await rig.client.post("/api/auth/logout", headers=mine)
        assert ok.status_code == 204


async def test_anonymous_posting_match_stays_on_when_only_leafout_is_configured(start: Any) -> None:
    async with start() as rig:
        assert rig.env.container.match_anonymous is True
        assert (await rig.me()).json()["anonymousMatch"] is True


async def test_the_apps_own_providers_still_switch_anonymous_match_off_under_auto(start: Any) -> None:
    async with start(auth={"own_sign_in": True}, env="local") as rig:
        assert rig.env.container.auth.providers.magic is True
        assert rig.env.container.match_anonymous is False
        assert (await rig.me()).json()["anonymousMatch"] is False


async def test_a_streaming_route_sets_the_cookie_on_its_first_bridged_request(start: Any) -> None:
    async with start() as rig:

        async def stream(request: Request) -> StreamingResponse:
            ctx = await get_auth_context(request)

            async def body() -> AsyncIterator[bytes]:
                yield b'{"n":1}\n'
                yield (b'{"signed_in":true}\n' if ctx is not None else b'{"signed_in":false}\n')

            return StreamingResponse(body(), media_type="application/x-ndjson")

        rig.env.app.router.routes.insert(0, APIRoute("/api/_stream", stream))
        rig.client.cookies.clear()
        res = await rig.client.get("/api/_stream", headers=cookie_for(rig.token()))

        assert res.status_code == 200 and res.content == b'{"n":1}\n{"signed_in":true}\n'
        header = raw_cookie(res, SESSION).lower()
        assert "httponly" in header and "domain=" not in header
        session = morsels(res)[SESSION].value
        assert (await rig.me(rig.token(), session)).json()["user"]["email"] == EMAIL


async def test_a_payment_webhook_never_bridges_the_cookie(start: Any) -> None:
    async with start() as rig:
        rig.client.cookies.clear()
        res = await rig.client.post(
            "/api/billing/webhooks/stripe", headers=cookie_for(rig.token()), content=b"{}"
        )
        assert res.status_code in (
            400,
            401,
            404,
            503,
        )  # whatever the provider route answers, it did not sign anyone in
        assert rig.supabase.user_requests == []


# ───────────────────────── the old domain ─────────────────────────


async def test_pages_on_another_host_are_redirected_but_the_api_is_not(start: Any) -> None:
    async with start(auth={"redirect_other_hosts": True}) as rig:
        rig.client.cookies.clear()
        old = {"host": "tayyari.fastapicloud.dev"}
        page = await rig.client.get("/match?x=1&y=2", headers=old)
        assert page.status_code == 308
        assert page.headers["location"] == f"{PUBLIC}/match?x=1&y=2"

        root = await rig.client.get("/", headers=old)
        assert root.status_code == 308 and root.headers["location"] == f"{PUBLIC}/"
        shared = await rig.client.get("/p/abc123", headers=old)
        assert shared.status_code == 308 and shared.headers["location"] == f"{PUBLIC}/p/abc123"

        api = await rig.client.get("/api/me", headers=old)
        health = await rig.client.get("/api/health", headers=old)
        assert api.status_code == 200 and "user" not in api.json()
        assert health.status_code == 200

        home = await rig.client.get("/", headers={"host": "app.leafout.example"})
        assert home.status_code != 308
        forwarded = await rig.client.get(
            "/", headers={"host": "internal:8000", "x-forwarded-host": "app.leafout.example"}
        )
        assert forwarded.status_code != 308


async def test_nothing_is_redirected_by_default(start: Any) -> None:
    async with start() as rig:
        res = await rig.client.get("/match", headers={"host": "tayyari.fastapicloud.dev"})
        assert res.status_code != 308


HOLD = "tayyari_bridge_hold"


async def test_after_logout_the_same_old_site_token_is_held_but_a_new_sign_in_works(start: Any) -> None:
    async with start() as rig:
        old = rig.token()
        session = morsels(await rig.me(old))[SESSION].value
        rig.client.cookies.clear()
        res = await rig.client.post(
            "/api/auth/logout", headers={"cookie": f"{COOKIE}={old}; {SESSION}={session}"}
        )
        hold = morsels(res)[HOLD].value
        assert "max-age=600" in raw_cookie(res, HOLD).lower()
        assert old not in raw_cookie(res, HOLD)
        rig.client.cookies.clear()
        again = await rig.client.get("/api/me", headers={"cookie": f"{COOKIE}={old}; {HOLD}={hold}"})
        assert "user" not in again.json() and SESSION not in morsels(again)
        rig.clock.advance(5)
        fresh = rig.token()
        rig.client.cookies.clear()
        back = await rig.client.get("/api/me", headers={"cookie": f"{COOKIE}={fresh}; {HOLD}={hold}"})
        assert "user" in back.json()


async def test_deleting_the_account_holds_the_old_token_so_no_empty_account_is_recreated(start: Any) -> None:
    async with start() as rig:
        old = rig.token()
        session = morsels(await rig.me(old))[SESSION].value
        rig.client.cookies.clear()
        res = await rig.client.delete(
            "/api/me",
            headers={
                "cookie": f"{COOKIE}={old}; {SESSION}={session}",
                "origin": PUBLIC,
                "sec-fetch-site": "same-origin",
            },
        )
        assert res.status_code == 204
        hold = morsels(res)[HOLD].value
        rig.client.cookies.clear()
        again = await rig.client.get("/api/me", headers={"cookie": f"{COOKIE}={old}; {HOLD}={hold}"})
        assert "user" not in again.json()
        assert await rig.env.rows(AuthSessionRow) == []
