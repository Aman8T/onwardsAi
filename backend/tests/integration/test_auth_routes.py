"""Accounts end to end through the real app: sign in by emailed link and by Google (against a fake Google), the
session cookie and its lifetime, `/api/me`, claiming a profile, editing and deleting the account, and the guards in
front of them, over a real SQLite file. Only the network edges are faked, and a request nobody mocked fails the test."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from tayyari.api.deps import get_current_user, require_user
from tayyari.contract.stored import StoredContext
from tayyari.core import context
from tayyari.core.settings import Settings
from tayyari.storage.ports import StoredPortfolio
from tayyari.storage.tables import AuthSessionRow, MagicLinkRow, PortfolioRow, ProfileRow, UserRow
from tests.fakes.auth import (
    CLIENT_ID,
    CLIENT_SECRET,
    FakeGoogle,
    FakeMailer,
    FakeNetwork,
    FakeResend,
    link_in,
    new_key,
)
from tests.fakes.clock import FakeClock
from tests.integration.support import Env, roomy_guard, running

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
STATE = "tayyari_oauth"
PUBLIC = "https://tayyari.example"
EMAIL = "ana@example.com"
DAY = 86_400


@dataclass
class Rig:
    env: Env
    net: FakeNetwork
    google: FakeGoogle
    mailer: FakeMailer
    clock: FakeClock

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client


@pytest.fixture(scope="module")
def google_key():  # type: ignore[no-untyped-def]
    return new_key()


@pytest.fixture
def start(build_settings: MakeSettings, google_key: Any):  # type: ignore[no-untyped-def]
    """``async with start(google=True, **settings) as rig``: the app, a fake Google, a recording mailer, a clock."""

    @asynccontextmanager
    async def run(*, google: bool = False, mailer: bool = True, **overrides: Any) -> AsyncIterator[Rig]:
        values: dict[str, Any] = {"auth": {"public_url": PUBLIC}}
        if google:
            values.update(google_client_id=CLIENT_ID, google_client_secret=CLIENT_SECRET)
        values.update(overrides)
        net, fake_google, fake_mailer, clock = (
            FakeNetwork(),
            FakeGoogle(key=google_key),
            FakeMailer(),
            FakeClock(),
        )
        fake_google.install(net)
        extra: dict[str, Any] = {"mailer": fake_mailer} if mailer else {}
        async with running(
            build_settings(**values), clock=clock, auth_transport=net.transport, **extra
        ) as env:
            yield Rig(env, net, fake_google, fake_mailer, clock)

    return run


# ───────────────────────── helpers ─────────────────────────


def morsels(response: httpx.Response) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        out.update(jar)
    return out


def session_of(response: httpx.Response) -> str:
    return str(morsels(response)[SESSION].value)


def as_user(token: str, **headers: str) -> dict[str, str]:
    return {"cookie": f"{SESSION}={token}", **headers}


def path_of(link: str) -> str:
    parts = urlsplit(link)
    return f"{parts.path}?{parts.query}"


async def sign_in_by_link(rig: Rig, email: str = EMAIL, *, ip: str = "203.0.113.7") -> str:
    """Request a link, follow it, and return the session cookie value."""
    asked = await rig.client.post("/api/auth/magic", json={"email": email}, headers={"cf-connecting-ip": ip})
    assert asked.status_code == 202, asked.text
    link = link_in(rig.mailer.sent[-1])
    followed = await rig.client.get(path_of(link))
    assert followed.status_code == 302 and followed.headers["location"] == "/", followed.headers
    rig.client.cookies.clear()
    return session_of(followed)


async def sign_in_by_google(rig: Rig, *, next_path: str = "/", **claims: Any) -> httpx.Response:
    """Run the Google flow and return the callback's response."""
    started = await rig.client.get("/api/auth/google/start", params={"next": next_path})
    assert started.status_code == 302, started.text
    query = parse_qs(urlsplit(started.headers["location"]).query)
    rig.google.token_for(rig.clock.now(), nonce=query["nonce"][0], **claims)
    state_cookie = morsels(started)[STATE].value
    callback = await rig.client.get(
        "/api/auth/google/callback",
        params={"code": "auth-code", "state": query["state"][0]},
        headers={"cookie": f"{STATE}={state_cookie}"},
    )
    rig.client.cookies.clear()
    return callback


async def put_profile(env: Env, pid: str, *, sample: bool = False, name: str = "Riya Kapoor") -> None:
    await env.container.repos.profiles.save(
        pid, {"person": {"name": name}}, StoredContext(), goal="backend", owner_hash=None, is_sample=sample
    )


async def put_portfolio(env: Env, pid: str) -> None:
    now = env.container.clock.now()
    await env.container.repos.portfolios.put(
        StoredPortfolio(pid, f"pub{pid}", {"profileId": pid}, None, [], 1, False, now, now),
        expected_version=None,
    )


async def user_rows(env: Env) -> list[Any]:
    return await env.rows(UserRow)


PID = "a1" * 16
PID2 = "b2" * 16


# ───────────────────────── /api/me for visitors ─────────────────────────


async def test_an_anonymous_visitor_gets_the_shape_without_a_user(start: Any) -> None:
    async with start(google=True) as rig:
        response = await rig.client.get("/api/me")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "profiles": [],
        "entitlements": {"credits": 0, "freeMatchAvailable": False},
        "providers": {"google": True, "magic": True},
        "anonymousMatch": False,  # a way to sign in exists, so a posting match asks for one
    }
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    ("google", "mailer", "settings", "expected"),
    [
        (True, True, {}, {"google": True, "magic": True}),
        (False, True, {}, {"google": False, "magic": True}),
        (True, False, {}, {"google": True, "magic": False}),
        (False, False, {}, {"google": False, "magic": False}),
        (False, False, {"env": "local"}, {"google": False, "magic": True}),  # the dev link
    ],
)
async def test_providers_report_what_this_server_can_do(
    start: Any, google: bool, mailer: bool, settings: dict[str, Any], expected: dict[str, bool]
) -> None:
    async with start(google=google, mailer=mailer, **settings) as rig:
        assert (await rig.client.get("/api/me")).json()["providers"] == expected


@pytest.mark.parametrize(
    ("google", "mailer", "settings", "mode", "expected"),
    [
        # auto: a posting may be matched with no account exactly when there is no way to get one
        (False, False, {}, "auto", True),
        (True, False, {}, "auto", False),
        (False, True, {}, "auto", False),
        (True, True, {}, "auto", False),
        (False, False, {"env": "local"}, "auto", False),  # the dev link is a way to sign in
        # the setting is auto when left out, and a blank value counts as unset
        (False, False, {}, None, True),
        (True, True, {}, None, False),
        (True, True, {}, " ", False),
        # an explicit value wins over the providers, in both directions
        (True, True, {}, "true", True),
        (False, False, {}, "false", False),
        (True, True, {}, " TRUE ", True),
    ],
)
async def test_anonymous_match_is_decided_by_the_setting_and_the_sign_in_methods(
    start: Any, google: bool, mailer: bool, settings: dict[str, Any], mode: str | None, expected: bool
) -> None:
    match = {} if mode is None else {"allow_anonymous": mode}
    async with start(google=google, mailer=mailer, match=match, **settings) as rig:
        body = (await rig.client.get("/api/me")).json()
        signed_in = await sign_in_by_link(rig) if mailer else None
        container_says = rig.env.container.match_anonymous
        known = (
            None
            if signed_in is None
            else (await rig.client.get("/api/me", headers=as_user(signed_in))).json()["anonymousMatch"]
        )
    assert body["anonymousMatch"] is expected and container_says is expected
    assert known in (None, expected)  # the same answer for a signed-in person


def test_a_setting_that_is_not_auto_true_or_false_stops_the_server_from_starting(
    build_settings: MakeSettings,
) -> None:
    with pytest.raises(ValueError, match="allow_anonymous"):
        build_settings(match={"allow_anonymous": "sometimes"})


async def test_a_garbage_cookie_is_just_an_anonymous_visitor(start: Any) -> None:
    async with start() as rig:
        for junk in ("nonsense", "x" * 300, ""):
            response = await rig.client.get("/api/me", headers=as_user(junk))
            assert response.status_code == 200 and "user" not in response.json()


# ───────────────────────── the emailed link ─────────────────────────


async def test_a_link_signs_you_in_and_me_says_who_you_are(start: Any) -> None:
    async with start() as rig:
        asked = await rig.client.post("/api/auth/magic", json={"email": "  Ana@Example.com "})
        assert (asked.status_code, asked.json()) == (202, {"ok": True})  # no link in the answer
        assert asked.headers["cache-control"] == "no-store"
        (message,) = rig.mailer.sent
        assert message.to == EMAIL

        followed = await rig.client.get(path_of(link_in(message)))
        assert (followed.status_code, followed.headers["location"]) == (302, "/")
        assert followed.headers["cache-control"] == "no-store"
        token = session_of(followed)
        rig.client.cookies.clear()

        me = (await rig.client.get("/api/me", headers=as_user(token))).json()
        assert me["user"] == {"id": me["user"]["id"], "email": EMAIL} and len(me["user"]["id"]) == 32
        # a new account has no credits, but this month's free posting is still unused (wave F2)
        assert me["profiles"] == [] and me["entitlements"] == {"credits": 0, "freeMatchAvailable": True}
        assert len(await user_rows(rig.env)) == 1


async def test_a_link_works_once(start: Any) -> None:
    async with start() as rig:
        await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        path = path_of(link_in(rig.mailer.sent[0]))
        first = await rig.client.get(path)
        again = await rig.client.get(path)
        assert first.status_code == 302 and SESSION in morsels(first)
        assert (again.status_code, again.headers["location"]) == (302, "/?auth=link_invalid")
        assert SESSION not in morsels(again)


async def test_a_link_expires(start: Any) -> None:
    async with start() as rig:
        await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        path = path_of(link_in(rig.mailer.sent[0]))
        rig.clock.advance(15 * 60)
        late = await rig.client.get(path)
        assert late.headers["location"] == "/?auth=link_invalid" and SESSION not in morsels(late)
        assert len(await user_rows(rig.env)) == 0  # an expired link creates nothing


@pytest.mark.parametrize("token", ["", "nope", "A" * 43, "x" * 500])
async def test_an_unknown_token_is_refused_like_an_expired_one(start: Any, token: str) -> None:
    async with start() as rig:
        response = await rig.client.get("/api/auth/magic/verify", params={"token": token})
        assert response.headers["location"] == "/?auth=link_invalid" and SESSION not in morsels(response)
        assert (await rig.client.get("/api/auth/magic/verify")).headers["location"] == "/?auth=link_invalid"


async def test_asking_never_reveals_whether_there_is_an_account(start: Any) -> None:
    async with start() as rig:
        await sign_in_by_link(rig, "known@example.com")
        known = await rig.client.post("/api/auth/magic", json={"email": "known@example.com"})
        stranger = await rig.client.post("/api/auth/magic", json={"email": "stranger@example.com"})
        assert (known.status_code, known.json(), dict(known.headers).keys() - {"x-request-id"}) == (
            stranger.status_code,
            stranger.json(),
            dict(stranger.headers).keys() - {"x-request-id"},
        )


async def test_a_mailer_that_fails_still_gets_the_same_answer(start: Any) -> None:
    async with start() as rig:
        rig.mailer.fail_with = "the email provider answered 500"
        response = await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        assert (response.status_code, response.json()) == (202, {"ok": True})


@pytest.mark.parametrize(
    "body",
    [{}, {"email": ""}, {"email": "nope"}, {"email": "a@b"}, {"email": 5}, {"email": ["a@b.co"]}, []],
)
async def test_a_bad_address_is_a_400_with_a_plain_message(start: Any, body: Any) -> None:
    async with start() as rig:
        response = await rig.client.post("/api/auth/magic", json=body)
        assert response.status_code == 400
        assert set(response.json()) == {"error"} and response.json()["error"].startswith("Invalid request")
        assert rig.mailer.sent == []


async def test_the_body_must_be_json_and_small(start: Any) -> None:
    async with start() as rig:
        text = await rig.client.post(
            "/api/auth/magic", content='{"email":"ana@example.com"}', headers={"content-type": "text/plain"}
        )
        big = await rig.client.post("/api/auth/magic", json={"email": "a" * 5000 + "@example.com"})
        assert (text.status_code, big.status_code) == (415, 413)
        assert rig.mailer.sent == []


async def test_a_cross_site_page_cannot_ask_for_links_in_a_visitors_name(start: Any) -> None:
    async with start() as rig:
        response = await rig.client.post(
            "/api/auth/magic", json={"email": EMAIL}, headers={"sec-fetch-site": "cross-site"}
        )
        assert response.status_code == 403 and rig.mailer.sent == []


async def test_an_address_gets_three_links_per_fifteen_minutes_but_always_a_202(start: Any) -> None:
    async with start() as rig:
        answers = []
        for i in range(5):
            r = await rig.client.post(
                "/api/auth/magic", json={"email": EMAIL}, headers={"cf-connecting-ip": f"203.0.113.{i + 1}"}
            )
            answers.append((r.status_code, r.json()))
        assert answers == [(202, {"ok": True})] * 5
        assert len(rig.mailer.sent) == 3
        assert len(await rig.env.rows(MagicLinkRow)) == 3


async def test_a_client_gets_three_links_per_fifteen_minutes_whoever_they_ask_for(start: Any) -> None:
    async with start() as rig:
        for i in range(5):
            r = await rig.client.post("/api/auth/magic", json={"email": f"person{i}@example.com"})
            assert r.status_code == 202
        assert len(rig.mailer.sent) == 3
        rig.clock.advance(15 * 60)
        await rig.client.post("/api/auth/magic", json={"email": "later@example.com"})
        assert len(rig.mailer.sent) == 4


async def test_a_listed_email_gets_the_same_three_links_per_fifteen_minutes(start: Any) -> None:
    """The unlimited list lifts the rate limits, not the limits on mail we send: exempting them would let anyone flood
    that inbox through our sender."""
    async with start(guard=roomy_guard(unlimited_emails=f" {EMAIL.upper()} ")) as rig:
        signed_in = as_user(
            await sign_in_by_link(rig)
        )  # one link used to sign in; the account is on the list now
        me = await rig.client.get("/api/me", headers=signed_in)
        assert me.json()["entitlements"]["unlimited"] is True
        answers = []
        for i in range(5):  # asked from a signed-in browser and from strangers alike
            headers = {"cf-connecting-ip": f"203.0.113.{i + 20}", **(signed_in if i % 2 else {})}
            r = await rig.client.post("/api/auth/magic", json={"email": EMAIL}, headers=headers)
            answers.append((r.status_code, r.json()))
        assert answers == [(202, {"ok": True})] * 5
        assert len(rig.mailer.sent) == 3 and len(await rig.env.rows(MagicLinkRow)) == 3
        rig.clock.advance(15 * 60)
        await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        assert len(rig.mailer.sent) == 4


async def test_a_client_still_gets_three_links_per_fifteen_minutes_when_the_asker_is_listed(
    start: Any,
) -> None:
    async with start(guard=roomy_guard(unlimited_emails=f" {EMAIL.upper()} ")) as rig:
        signed_in = as_user(await sign_in_by_link(rig, ip="203.0.113.99"))
        for i in range(5):
            await rig.client.post(
                "/api/auth/magic",
                json={"email": f"person{i}@example.com"},
                headers={"cf-connecting-ip": "203.0.113.99", **signed_in},
            )
        assert len(rig.mailer.sent) == 3  # the first of them was the sign-in above


# ───────────────────────── the dev link ─────────────────────────


async def test_a_local_server_with_no_email_key_returns_the_link_and_it_works(start: Any) -> None:
    async with start(mailer=False, env="local") as rig:
        response = await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        body = response.json()
        assert response.status_code == 202 and body["ok"] is True
        assert body["devLink"].startswith(f"{PUBLIC}/api/auth/magic/verify?token=")

        followed = await rig.client.get(path_of(body["devLink"]))
        assert followed.headers["location"] == "/" and SESSION in morsels(followed)


@pytest.mark.parametrize("env", ["test", "prod"])
async def test_no_other_environment_returns_a_link(start: Any, env: str) -> None:
    async with start(mailer=False, env=env) as rig:
        response = await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        assert (response.status_code, response.json()) == (202, {"ok": True})
        assert await rig.env.rows(MagicLinkRow) == []  # and nothing was issued: there is no way to deliver it


async def test_a_local_server_with_an_email_key_sends_through_resend_and_returns_nothing(start: Any) -> None:
    resend = FakeResend()
    async with start(
        mailer=False, env="local", resend_api_key="re_test", resend_from="Tayyari <hi@tayyari.example>"
    ) as rig:
        resend.install(rig.net)
        response = await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        assert (response.status_code, response.json()) == (202, {"ok": True})
        (posted,) = resend.posted
        assert posted["to"] == [EMAIL] and posted["from"] == "Tayyari <hi@tayyari.example>"
        assert "/api/auth/magic/verify?token=" in posted["text"]
        assert resend.headers[0]["authorization"] == "Bearer re_test"


# ───────────────────────── the session cookie ─────────────────────────


async def test_the_session_cookie_is_httponly_lax_and_not_secure_on_a_local_server(start: Any) -> None:
    async with start(env="local", auth={"public_url": "http://localhost:5173"}) as rig:
        await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        followed = await rig.client.get(path_of(link_in(rig.mailer.sent[0])))
        cookie = morsels(followed)[SESSION]
        assert cookie["httponly"] is True and cookie["samesite"].lower() == "lax"
        assert cookie["path"] == "/" and int(cookie["max-age"]) == 30 * DAY
        assert not cookie["secure"]
        assert len(cookie.value) >= 43


async def test_the_session_cookie_is_secure_in_prod(start: Any) -> None:
    async with start(env="prod") as rig:
        await rig.client.post("/api/auth/magic", json={"email": EMAIL})
        followed = await rig.client.get(path_of(link_in(rig.mailer.sent[0])))
        cookie = morsels(followed)[SESSION]
        assert cookie["secure"] is True and cookie["httponly"] is True and cookie["samesite"].lower() == "lax"


async def test_only_the_hash_of_the_token_is_in_the_database(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        (row,) = await rig.env.rows(AuthSessionRow)
        assert row.id != token and len(row.id) == 64 and token not in str(row.__dict__)


async def test_a_session_ends_thirty_days_after_its_last_use(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        rig.clock.advance(30 * DAY + 1)
        assert "user" not in (await rig.client.get("/api/me", headers=as_user(token))).json()
        assert await rig.env.rows(AuthSessionRow) == []  # and the expired row is gone


async def test_using_the_session_slides_it_and_hands_the_browser_a_fresh_cookie(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        (before,) = await rig.env.rows(AuthSessionRow)

        soon = await rig.client.get("/api/me", headers=as_user(token))
        assert "user" in soon.json() and SESSION not in morsels(soon)  # within the hour: nothing is written

        rig.clock.advance(2 * 3600)
        later = await rig.client.get("/api/me", headers=as_user(token))
        assert "user" in later.json()
        assert morsels(later)[SESSION].value == token  # same token, a new lifetime
        assert int(morsels(later)[SESSION]["max-age"]) == 30 * DAY
        (after,) = await rig.env.rows(AuthSessionRow)
        assert after.last_seen_at > before.last_seen_at and after.expires_at > before.expires_at

        rig.clock.advance(25 * DAY)  # 27 days after sign-in, 25 after the last use: still in
        assert "user" in (await rig.client.get("/api/me", headers=as_user(token))).json()


async def test_logging_out_ends_the_session_and_clears_the_cookie(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        out = await rig.client.post("/api/auth/logout", headers=as_user(token))
        assert out.status_code == 204 and out.content == b""
        cleared = morsels(out)[SESSION]
        assert cleared.value == "" and int(cleared["max-age"]) == 0 and cleared["path"] == "/"
        assert await rig.env.rows(AuthSessionRow) == []
        assert (
            "user" not in (await rig.client.get("/api/me", headers=as_user(token))).json()
        )  # the old cookie is dead


async def test_logging_out_without_a_session_is_fine(start: Any) -> None:
    async with start() as rig:
        assert (await rig.client.post("/api/auth/logout")).status_code == 204


async def test_logout_is_a_post_and_guarded_against_cross_site_requests(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        assert (await rig.client.get("/api/auth/logout", headers=as_user(token))).status_code in (404, 405)
        forged = await rig.client.post(
            "/api/auth/logout", headers=as_user(token, **{"sec-fetch-site": "cross-site"})
        )
        assert forged.status_code == 403
        assert "user" in (await rig.client.get("/api/me", headers=as_user(token))).json()  # still signed in


async def test_two_devices_are_two_sessions(start: Any) -> None:
    async with start() as rig:
        phone = await sign_in_by_link(rig)
        laptop = await sign_in_by_link(rig)
        assert phone != laptop and len(await user_rows(rig.env)) == 1
        await rig.client.post("/api/auth/logout", headers=as_user(phone))
        assert "user" not in (await rig.client.get("/api/me", headers=as_user(phone))).json()
        assert "user" in (await rig.client.get("/api/me", headers=as_user(laptop))).json()


# ───────────────────────── Google ─────────────────────────


async def test_google_start_sends_the_browser_to_google_with_a_signed_state_cookie(start: Any) -> None:
    async with start(google=True) as rig:
        started = await rig.client.get("/api/auth/google/start", params={"next": "/graph"})
    assert started.status_code == 302 and started.headers["cache-control"] == "no-store"
    location = urlsplit(started.headers["location"])
    assert (
        f"{location.scheme}://{location.netloc}{location.path}"
        == "https://accounts.google.com/o/oauth2/v2/auth"
    )
    query = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert query["client_id"] == CLIENT_ID and query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == f"{PUBLIC}/api/auth/google/callback"
    assert CLIENT_SECRET not in started.headers["location"]

    state = morsels(started)[STATE]
    assert state["httponly"] is True and state["samesite"].lower() == "lax"
    assert state["path"] == "/api/auth/google/callback" and int(state["max-age"]) == 600
    assert state["secure"] is True  # the public url is https
    assert query["state"] not in state.value  # the cookie is not a copy of the URL parameter ...
    assert state.value.count(".") == 1  # ... it is a signed blob: payload.signature


async def test_a_google_sign_in_end_to_end(start: Any) -> None:
    async with start(google=True) as rig:
        callback = await sign_in_by_google(rig, next_path="/graph?tab=1")
        assert (callback.status_code, callback.headers["location"]) == (302, "/graph?tab=1")
        jar = morsels(callback)
        assert jar[STATE].value == "" and int(jar[STATE]["max-age"]) == 0  # the state cookie is spent
        token = jar[SESSION].value

        me = (await rig.client.get("/api/me", headers=as_user(token))).json()
        assert me["user"]["email"] == EMAIL and me["user"]["name"] == "Ana Rao"
        assert me["user"]["pictureUrl"] == "https://lh3.googleusercontent.com/a/photo"
        (row,) = await user_rows(rig.env)
        assert row.google_sub == "1100110011" and row.locale == "en-GB"
        assert len(rig.google.token_requests) == 1


async def test_signing_in_with_google_twice_is_one_account(start: Any) -> None:
    async with start(google=True) as rig:
        await sign_in_by_google(rig)
        await sign_in_by_google(rig)
        assert len(await user_rows(rig.env)) == 1 and len(await rig.env.rows(AuthSessionRow)) == 2


async def test_google_and_an_emailed_link_for_the_same_address_are_one_account(start: Any) -> None:
    async with start(google=True) as rig:
        by_link = await sign_in_by_link(rig)
        callback = await sign_in_by_google(rig)
        by_google = morsels(callback)[SESSION].value
        ids = {
            (await rig.client.get("/api/me", headers=as_user(t))).json()["user"]["id"]
            for t in (by_link, by_google)
        }
        assert len(ids) == 1 and len(await user_rows(rig.env)) == 1


@pytest.mark.parametrize(
    "claims", [{"email_verified": False}, {"aud": "someone-else"}, {"iss": "https://evil.example"}]
)
async def test_a_google_answer_we_do_not_trust_signs_nobody_in(start: Any, claims: dict[str, Any]) -> None:
    async with start(google=True) as rig:
        callback = await sign_in_by_google(rig, **claims)
        assert (callback.status_code, callback.headers["location"]) == (302, "/?auth=google_failed")
        assert SESSION not in morsels(callback) and morsels(callback)[STATE].value == ""
        assert await user_rows(rig.env) == []


async def test_a_callback_that_does_not_belong_to_this_browser_is_refused(start: Any) -> None:
    async with start(google=True) as rig:
        started = await rig.client.get("/api/auth/google/start")
        query = parse_qs(urlsplit(started.headers["location"]).query)
        rig.google.token_for(rig.clock.now(), nonce=query["nonce"][0])
        state_cookie = morsels(started)[STATE].value
        rig.client.cookies.clear()

        wrong_state = await rig.client.get(
            "/api/auth/google/callback",
            params={"code": "c", "state": "forged"},
            headers={"cookie": f"{STATE}={state_cookie}"},
        )
        no_cookie = await rig.client.get(
            "/api/auth/google/callback", params={"code": "c", "state": query["state"][0]}
        )
        forged_cookie = await rig.client.get(
            "/api/auth/google/callback",
            params={"code": "c", "state": query["state"][0]},
            headers={"cookie": f"{STATE}={state_cookie[:-3]}AAA"},
        )
        no_code = await rig.client.get(
            "/api/auth/google/callback",
            params={"state": query["state"][0]},
            headers={"cookie": f"{STATE}={state_cookie}"},
        )
        for response in (wrong_state, no_cookie, forged_cookie, no_code):
            assert response.headers["location"] == "/?auth=google_failed" and SESSION not in morsels(response)
        assert (
            rig.google.token_requests == [] and await user_rows(rig.env) == []
        )  # the code was never exchanged


async def test_a_declined_google_prompt_goes_back_to_the_app_quietly(start: Any) -> None:
    async with start(google=True) as rig:
        response = await rig.client.get("/api/auth/google/callback", params={"error": "access_denied"})
        assert (response.status_code, response.headers["location"]) == (302, "/?auth=google_denied")


async def test_google_refusing_the_code_signs_nobody_in(start: Any) -> None:
    async with start(google=True) as rig:
        rig.google.token_status = 400
        callback = await sign_in_by_google(rig)
        assert callback.headers["location"] == "/?auth=google_failed" and await user_rows(rig.env) == []


async def test_google_start_without_credentials_goes_back_to_the_app(start: Any) -> None:
    async with start(google=False) as rig:
        response = await rig.client.get("/api/auth/google/start")
        assert (response.status_code, response.headers["location"]) == (302, "/?auth=google_unavailable")
        assert STATE not in morsels(response)


@pytest.mark.parametrize(
    "evil",
    [
        "https://evil.example/",
        "//evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
        "evil",
        "/x\r\nSet-Cookie: a=b",
    ],
)
async def test_next_cannot_be_used_to_leave_the_site(start: Any, evil: str) -> None:
    async with start(google=True) as rig:
        callback = await sign_in_by_google(rig, next_path=evil)
        assert callback.status_code == 302 and callback.headers["location"] == "/"


# ───────────────────────── claiming and editing ─────────────────────────


async def test_claiming_a_profile_attaches_it_and_its_portfolio(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        await put_profile(rig.env, PID)
        await put_portfolio(rig.env, PID)

        response = await rig.client.post("/api/me/claim", json={"profileId": PID}, headers=as_user(token))

        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        me = response.json()
        assert [(p["id"], p["personName"], p["goal"]) for p in me["profiles"]] == [
            (PID, "Riya Kapoor", "backend")
        ]
        assert me["profiles"][0]["createdAt"].startswith("2026-10-07")
        (profile,) = await rig.env.rows(ProfileRow, ProfileRow.id == PID)
        (page,) = await rig.env.rows(PortfolioRow, PortfolioRow.profile_id == PID)
        assert profile.user_id == page.user_id == me["user"]["id"]
        # ... and it is what GET /api/me lists from now on
        again = (await rig.client.get("/api/me", headers=as_user(token))).json()
        assert [p["id"] for p in again["profiles"]] == [PID]


async def test_claiming_twice_is_fine_and_someone_elses_profile_is_a_409(start: Any) -> None:
    async with start() as rig:
        ana = await sign_in_by_link(rig, "ana@example.com", ip="203.0.113.1")
        raj = await sign_in_by_link(rig, "raj@example.com", ip="203.0.113.2")
        await put_profile(rig.env, PID)

        assert (
            await rig.client.post("/api/me/claim", json={"profileId": PID}, headers=as_user(ana))
        ).status_code == 200
        assert (
            await rig.client.post("/api/me/claim", json={"profileId": PID}, headers=as_user(ana))
        ).status_code == 200
        taken = await rig.client.post("/api/me/claim", json={"profileId": PID}, headers=as_user(raj))

        assert taken.status_code == 409
        assert taken.json() == {"error": "That profile already belongs to another account."}
        assert (await rig.client.get("/api/me", headers=as_user(raj))).json()["profiles"] == []


@pytest.mark.parametrize("pid", ["c3" * 16, "sample-" + "d4" * 16, "../etc/passwd", "x" * 97, "has space"])
async def test_a_profile_that_is_missing_a_sample_or_malformed_is_a_404(start: Any, pid: str) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        await put_profile(rig.env, "sample-" + "d4" * 16, sample=True)
        response = await rig.client.post("/api/me/claim", json={"profileId": pid}, headers=as_user(token))
        assert response.status_code in (
            404,
            400,
        )  # 400 for a body the model refuses (too long), 404 otherwise
        if len(pid) <= 96:
            assert response.status_code == 404 and response.json() == {"error": "Profile not found"}


async def test_claiming_needs_a_sign_in_json_and_a_same_site_request(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        await put_profile(rig.env, PID)

        anonymous = await rig.client.post("/api/me/claim", json={"profileId": PID})
        assert anonymous.status_code == 401 and anonymous.json() == {"error": "Sign in to continue."}
        text = await rig.client.post(
            "/api/me/claim",
            content='{"profileId":"x"}',
            headers=as_user(token, **{"content-type": "text/plain"}),
        )
        cross = await rig.client.post(
            "/api/me/claim",
            json={"profileId": PID},
            headers=as_user(token, **{"sec-fetch-site": "cross-site"}),
        )
        assert (text.status_code, cross.status_code) == (415, 403)
        assert (await rig.client.get("/api/me", headers=as_user(token))).json()["profiles"] == []


async def test_a_signed_out_profile_survives_in_the_account_after_signing_in_elsewhere(start: Any) -> None:
    async with start() as rig:
        first = await sign_in_by_link(rig)
        await put_profile(rig.env, PID)
        await rig.client.post("/api/me/claim", json={"profileId": PID}, headers=as_user(first))
        await rig.client.post("/api/auth/logout", headers=as_user(first))

        second = await sign_in_by_link(rig)
        me = (await rig.client.get("/api/me", headers=as_user(second))).json()
        assert [p["id"] for p in me["profiles"]] == [PID]


async def test_patching_the_account(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        response = await rig.client.patch(
            "/api/me", json={"name": "  Ana   Rao ", "currency": "INR"}, headers=as_user(token)
        )
        assert response.status_code == 200
        assert response.json()["user"] == {
            "id": response.json()["user"]["id"],
            "email": EMAIL,
            "name": "Ana Rao",
            "currency": "INR",
        }
        only_currency = await rig.client.patch("/api/me", json={"currency": "USD"}, headers=as_user(token))
        assert (
            only_currency.json()["user"]["name"] == "Ana Rao"
            and only_currency.json()["user"]["currency"] == "USD"
        )
        assert (await rig.client.patch("/api/me", json={}, headers=as_user(token))).status_code == 200


@pytest.mark.parametrize("body", [{"currency": "EUR"}, {"name": "  "}, {"name": "x" * 200}, {"currency": 5}])
async def test_a_bad_patch_is_a_400(start: Any, body: dict[str, Any]) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        response = await rig.client.patch("/api/me", json=body, headers=as_user(token))
        assert response.status_code == 400 and set(response.json()) == {"error"}


async def test_patching_needs_a_sign_in(start: Any) -> None:
    async with start() as rig:
        assert (await rig.client.patch("/api/me", json={"name": "x"})).status_code == 401


async def test_deleting_the_account_removes_everything_it_owns(start: Any) -> None:
    async with start() as rig:
        ana = await sign_in_by_link(rig, "ana@example.com", ip="203.0.113.1")
        raj = await sign_in_by_link(rig, "raj@example.com", ip="203.0.113.2")
        for pid, token in ((PID, ana), (PID2, raj)):
            await put_profile(rig.env, pid)
            await put_portfolio(rig.env, pid)
            await rig.client.post("/api/me/claim", json={"profileId": pid}, headers=as_user(token))

        gone = await rig.client.delete("/api/me", headers=as_user(ana))

        assert gone.status_code == 204 and morsels(gone)[SESSION]["max-age"] == "0"
        assert [r.email for r in await user_rows(rig.env)] == ["raj@example.com"]
        assert [r.id for r in await rig.env.rows(ProfileRow)] != [] and PID not in [
            r.id for r in await rig.env.rows(ProfileRow)
        ]
        assert [r.profile_id for r in await rig.env.rows(PortfolioRow)] == [PID2]
        assert "user" not in (await rig.client.get("/api/me", headers=as_user(ana))).json()
        assert (
            "user" in (await rig.client.get("/api/me", headers=as_user(raj))).json()
        )  # the other account is fine


async def test_deleting_the_account_needs_a_sign_in_and_a_same_site_request(start: Any) -> None:
    async with start() as rig:
        token = await sign_in_by_link(rig)
        assert (await rig.client.delete("/api/me")).status_code == 401
        cross = await rig.client.delete("/api/me", headers=as_user(token, **{"sec-fetch-site": "cross-site"}))
        assert cross.status_code == 403 and len(await user_rows(rig.env)) == 1


# ───────────────────────── the dependencies ─────────────────────────


@pytest.fixture
def whoami() -> Callable[[Env], None]:
    """Mounts ``/api/_whoami`` and ``/api/_private`` on a running app, ahead of the catch-all API 404."""

    def mount(env: Env) -> None:
        async def who(request: Request) -> JSONResponse:
            user = await get_current_user(request)
            return JSONResponse({"user": None if user is None else user.id, "log": context.current()})

        async def private(request: Request) -> JSONResponse:
            user = await require_user(request)
            return JSONResponse({"user": user.id})

        env.app.router.routes.insert(0, APIRoute("/api/_whoami", who))
        env.app.router.routes.insert(0, APIRoute("/api/_private", private))

    return mount


async def test_get_current_user_binds_the_user_id_but_never_the_email(
    start: Any, whoami: Callable[[Env], None]
) -> None:
    async with start() as rig:
        whoami(rig.env)
        token = await sign_in_by_link(rig)

        known = (await rig.client.get("/api/_whoami", headers=as_user(token))).json()
        stranger = (await rig.client.get("/api/_whoami")).json()

        assert known["user"] and known["log"]["user_id"] == known["user"]
        assert EMAIL not in str(known["log"])
        assert stranger["user"] is None and "user_id" not in stranger["log"]  # nothing leaks across requests


async def test_require_user_answers_401_with_a_plain_message(
    start: Any, whoami: Callable[[Env], None]
) -> None:
    async with start() as rig:
        whoami(rig.env)
        token = await sign_in_by_link(rig)
        refused = await rig.client.get("/api/_private")
        junk = await rig.client.get("/api/_private", headers=as_user("not-a-session"))
        allowed = await rig.client.get("/api/_private", headers=as_user(token))
        assert (refused.status_code, refused.json()) == (401, {"error": "Sign in to continue."})
        assert junk.status_code == 401 and allowed.status_code == 200


# ───────────────────────── the rest of the app is untouched ─────────────────────────


async def test_anonymous_use_needs_no_account(start: Any) -> None:
    async with start() as rig:
        health = await rig.client.get("/api/health")
        sample = await rig.client.get(f"/api/profile/{rig.env.sample_id}")
        assert health.status_code == 200 and sample.status_code == 200
        assert "set-cookie" not in health.headers and "set-cookie" not in sample.headers


async def test_the_migration_left_old_profiles_anonymous(start: Any) -> None:
    async with start() as rig:
        await put_profile(rig.env, PID)
        (profile,) = await rig.env.rows(ProfileRow, ProfileRow.id == PID)
        assert profile.user_id is None
