"""`AuthService` and Sign in with the Leafout account: finding, linking and creating accounts from a verified Supabase
identity, the session it starts, the review of that session on later requests (recheck, swap, end, reauth), the providers
a server reports, the posting-match default, and the promise that no log line ever carries a token, an id, an email or a phone.
Everything runs on the fakes: a `FakeSupabase` behind a mock transport, in-memory repositories, a manual clock."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
import structlog

from tayyari.auth.google import GoogleError
from tayyari.auth.service import Providers
from tayyari.core.settings import GuardSettings, MatchSettings
from tests.fakes.supabase import (
    EMAIL,
    OTHER_SUB,
    SESSION_ID,
    SIGN_IN_URL,
    SUB,
    URL,
    USER_URL,
)
from tests.unit.auth.conftest import PUBLIC_URL, MakeAuth, Rig

HOUR = 3600


async def sign_in(rig: Rig, token: str | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
    return await rig.service.leafout_bridge(
        token or rig.supabase.mint(rig.clock.now()), ip_hash="iphash", user_agent="UA", **kwargs
    )


async def session_for(rig: Rig, **mint: Any):  # type: ignore[no-untyped-def]
    """Sign in and return the resolved `SessionContext` (a live session made from the cookie)."""
    result = await sign_in(rig, rig.supabase.mint(rig.clock.now(), **mint))
    assert result.signed_in is not None, result
    ctx = await rig.service.sessions.resolve(result.signed_in.token)
    assert ctx is not None
    return ctx


@pytest.fixture
def rig(make_auth: MakeAuth) -> Rig:
    return make_auth(leafout=True, google=False, mailer=False, own_sign_in=False)


# ───────────────────────── a first sign-in ─────────────────────────


async def test_a_good_token_creates_the_account_and_a_session_marked_supabase(rig: Rig) -> None:
    result = await sign_in(rig)

    signed = result.signed_in
    assert signed is not None and result.issue is None
    assert signed.user.email == EMAIL and signed.user.name == "Ana Rao" and signed.user.supabase_sub == SUB
    assert signed.user.google_sub is None and signed.user.picture_url is None
    resolved = await rig.service.sessions.resolve(signed.token)
    assert resolved is not None and resolved.user.id == signed.user.id
    assert resolved.session.source == "supabase"
    assert resolved.session.supabase_session_id == SESSION_ID
    assert resolved.session.verified_at == rig.clock.now()
    assert (resolved.session.ip_hash, resolved.session.user_agent) == ("iphash", "UA")
    assert signed.next_path == "/"


async def test_leafout_sign_in_returns_just_the_sign_in(rig: Rig) -> None:
    signed = await rig.service.leafout_sign_in(
        rig.supabase.mint(rig.clock.now()), ip_hash=None, user_agent=None
    )
    assert signed is not None and signed.user.email == EMAIL
    assert await rig.service.leafout_sign_in("garbage", ip_hash=None, user_agent=None) is None


async def test_the_same_person_is_the_same_account_next_time(rig: Rig) -> None:
    first = (await sign_in(rig)).signed_in
    rig.clock.advance(60)
    second = (await sign_in(rig)).signed_in
    assert first is not None and second is not None
    assert first.user.id == second.user.id and len(rig.fakes.users.users) == 1
    assert first.token != second.token
    assert second.user.last_login_at > first.user.last_login_at


async def test_a_supabase_call_is_made_once_per_new_session_and_never_before_the_local_checks(
    rig: Rig,
) -> None:
    await sign_in(rig, rig.supabase.mint(rig.clock.now(), role="anon"))
    await sign_in(rig, "garbage")
    assert rig.supabase.user_requests == []
    await sign_in(rig)
    assert len(rig.supabase.user_requests) == 1


# ───────────────────────── linking ─────────────────────────


async def test_a_verified_email_links_an_account_made_by_the_emailed_link(make_auth: MakeAuth) -> None:
    rig = make_auth(leafout=True, google=False, mailer=False)
    existing = await rig.fakes.users.sign_in_email(email=EMAIL, now=rig.clock.now())
    assert existing.supabase_sub is None

    signed = (await sign_in(rig)).signed_in

    assert signed is not None and signed.user.id == existing.id
    assert signed.user.supabase_sub == SUB and len(rig.fakes.users.users) == 1


async def test_a_verified_email_links_an_account_made_by_google(make_auth: MakeAuth) -> None:
    rig = make_auth(leafout=True, google=False, mailer=False)
    existing = await rig.fakes.users.sign_in_google(
        sub="g-1", email=EMAIL, name="Ana", picture_url=None, locale=None, now=rig.clock.now()
    )
    signed = (await sign_in(rig)).signed_in
    assert signed is not None and signed.user.id == existing.id
    assert (signed.user.google_sub, signed.user.supabase_sub) == ("g-1", SUB)


async def test_an_unverified_email_never_links_and_the_account_is_made_without_one(rig: Rig) -> None:
    existing = await rig.fakes.users.sign_in_email(email=EMAIL, now=rig.clock.now())
    rig.supabase.email_confirmed = False

    result = await sign_in(rig)

    assert result.signed_in is not None
    user = result.signed_in.user
    assert user.id != existing.id and user.email is None and user.supabase_sub == SUB
    untouched = rig.fakes.users.users[existing.id]
    assert untouched.supabase_sub is None and untouched.email == EMAIL  # nobody took the existing account


async def test_a_verified_email_on_an_account_linked_to_another_sign_in_is_refused(rig: Rig) -> None:
    holder = await rig.fakes.users.sign_in_supabase(
        sub=OTHER_SUB, email=EMAIL, email_verified=True, name=None, now=rig.clock.now()
    )

    result = await sign_in(rig)

    assert result.signed_in is None and result.issue == "email_taken"
    assert rig.fakes.users.users[holder.id].supabase_sub == OTHER_SUB  # nothing linked
    assert len(rig.fakes.users.users) == 1 and rig.fakes.users.sessions == {}


async def test_a_phone_only_person_gets_an_account_with_no_email(rig: Rig) -> None:
    rig.supabase.email, rig.supabase.phone, rig.supabase.name = None, "+919800000000", None

    signed = (await sign_in(rig, rig.supabase.mint(rig.clock.now(), email=None))).signed_in

    assert signed is not None
    assert (signed.user.email, signed.user.name, signed.user.supabase_sub) == (None, None, SUB)
    again = (await sign_in(rig, rig.supabase.mint(rig.clock.now(), email=None))).signed_in
    assert again is not None and again.user.id == signed.user.id  # found by sub, not by email


async def test_the_email_follows_a_change_on_the_site(rig: Rig) -> None:
    first = (await sign_in(rig)).signed_in
    assert first is not None
    rig.supabase.email = "ana.rao@example.com"
    second = (await sign_in(rig)).signed_in
    assert second is not None and second.user.id == first.user.id
    assert second.user.email == "ana.rao@example.com"


async def test_the_email_stays_when_another_account_holds_the_new_one(rig: Rig) -> None:
    first = (await sign_in(rig)).signed_in
    assert first is not None
    await rig.fakes.users.sign_in_email(email="taken@example.com", now=rig.clock.now())
    rig.supabase.email = "taken@example.com"
    second = (await sign_in(rig)).signed_in
    assert second is not None and second.user.id == first.user.id and second.user.email == EMAIL


async def test_a_name_the_person_chose_is_not_overwritten(rig: Rig) -> None:
    first = (await sign_in(rig)).signed_in
    assert first is not None
    await rig.fakes.users.update(first.user.id, name="Ana the Great")
    rig.supabase.name = "Someone Else"
    second = (await sign_in(rig)).signed_in
    assert second is not None and second.user.name == "Ana the Great"


# ───────────────────────── refusals ─────────────────────────


@pytest.mark.parametrize("status", [401, 403])
async def test_a_revoked_session_starts_nothing(rig: Rig, status: int) -> None:
    rig.supabase.user_status = status
    result = await sign_in(rig)
    assert result.signed_in is None and result.reason == "revoked"
    assert rig.fakes.users.users == {} and rig.fakes.users.sessions == {}


async def test_an_outage_at_supabase_starts_nothing_and_counts_against_nobody(rig: Rig) -> None:
    rig.supabase.user_status = 503
    result = await sign_in(rig)
    assert result.signed_in is None and result.reason == "upstream"
    rig.supabase.user_status = 200  # the next request simply tries again
    assert (await sign_in(rig)).signed_in is not None


async def test_the_gate_runs_after_the_local_checks_and_before_supabase_is_asked(rig: Rig) -> None:
    asked: list[str] = []

    async def closed() -> bool:
        asked.append("gate")
        return False

    refused = await sign_in(rig, gate=closed)
    assert refused.signed_in is None and refused.reason == "rate_limited"
    assert asked == ["gate"] and rig.supabase.user_requests == []

    bad = await sign_in(rig, "not.a.token", gate=closed)  # a garbage cookie never reaches the gate
    assert bad.reason == "malformed" and asked == ["gate"]


async def test_an_open_gate_lets_the_sign_in_through(rig: Rig) -> None:
    async def open_() -> bool:
        return True

    assert (await sign_in(rig, gate=open_)).signed_in is not None


async def test_sessions_minted_for_one_account_per_day_are_bounded(make_auth: MakeAuth) -> None:
    rig = make_auth(
        leafout=True,
        google=False,
        mailer=False,
        own_sign_in=False,
        guard=GuardSettings(auth_bridge_per_sub_per_day=2),
    )
    assert (await sign_in(rig)).signed_in is not None
    assert (await sign_in(rig)).signed_in is not None
    third = await sign_in(rig)
    assert third.signed_in is None and third.reason == "sub_limit"
    rig.clock.advance(86_400 + 1)  # the next day starts afresh
    assert (await sign_in(rig, rig.supabase.mint(rig.clock.now()))).signed_in is not None


async def test_with_leafout_not_configured_nothing_is_bridged(make_auth: MakeAuth) -> None:
    rig = make_auth()
    assert rig.service.leafout is None
    result = await rig.service.leafout_bridge("x", ip_hash=None, user_agent=None)
    assert result.signed_in is None and result.reason == "not_configured"


# ───────────────────────── the review of a live session ─────────────────────────


async def test_a_session_whose_cookie_is_gone_ends(rig: Rig) -> None:
    ctx = await session_for(rig)
    review = await rig.service.review_leafout_session(ctx, None)
    assert review.action == "end" and review.reason == "absent"
    assert (await rig.service.review_leafout_session(ctx, "")).action == "end"


@pytest.mark.parametrize("kind", ["garbage", "bad_signature", "wrong_issuer"])
async def test_a_cookie_that_is_not_a_genuine_token_counts_as_absent(rig: Rig, kind: str) -> None:
    ctx = await session_for(rig)
    cookie = {
        "garbage": "not-a-token",
        "bad_signature": rig.supabase.mint(rig.clock.now(), key=rig.supabase.other_key, kid="key-1"),
        "wrong_issuer": rig.supabase.mint(rig.clock.now(), iss="https://other.example/auth/v1"),
    }[kind]
    assert (await rig.service.review_leafout_session(ctx, cookie)).action == "end"


async def test_a_session_inside_its_recheck_window_is_kept_even_with_an_expired_token(rig: Rig) -> None:
    ctx = await session_for(rig)
    rig.clock.advance(2 * HOUR)  # the token has expired, but the recheck is 24 hours away
    old = rig.supabase.mint(rig.clock.now() - timedelta(hours=3))
    assert (await rig.service.review_leafout_session(ctx, old)).action == "keep"


async def test_a_due_recheck_with_the_same_person_and_a_current_token_slides(rig: Rig) -> None:
    ctx = await session_for(rig)
    rig.clock.advance(25 * HOUR)
    fresh = rig.supabase.mint(rig.clock.now())
    review = await rig.service.review_leafout_session(ctx, fresh)
    assert review.action == "slide"

    slid = await rig.service.slide_verified(ctx.session)
    assert slid.verified_at == rig.clock.now()
    stored = rig.fakes.users.sessions[ctx.session.id]
    assert stored.verified_at == rig.clock.now()
    assert len(rig.supabase.user_requests) == 1  # only the sign-in asked Supabase; the recheck is local


async def test_a_due_recheck_with_an_expired_token_asks_for_reauth(rig: Rig) -> None:
    ctx = await session_for(rig)
    stale = rig.supabase.mint(rig.clock.now())
    rig.clock.advance(25 * HOUR)
    review = await rig.service.review_leafout_session(ctx, stale)
    assert review.action == "reauth" and review.reason in ("exp", "too_old")


async def test_a_due_recheck_with_an_absent_cookie_ends_without_reauth(rig: Rig) -> None:
    ctx = await session_for(rig)
    rig.clock.advance(25 * HOUR)
    assert (await rig.service.review_leafout_session(ctx, None)).action == "end"


async def test_a_different_person_in_the_cookie_swaps_the_session(rig: Rig) -> None:
    ctx = await session_for(rig)
    other = rig.supabase.mint(rig.clock.now(), sub=OTHER_SUB)
    assert (await rig.service.review_leafout_session(ctx, other)).action == "swap"


async def test_a_different_person_with_an_expired_token_asks_for_reauth(rig: Rig) -> None:
    ctx = await session_for(rig)
    rig.clock.advance(2 * HOUR)
    expired = rig.supabase.mint(rig.clock.now() - timedelta(hours=3), sub=OTHER_SUB)
    review = await rig.service.review_leafout_session(ctx, expired)
    assert review.action == "reauth"


async def test_a_key_set_outage_never_signs_anyone_out(rig: Rig) -> None:
    ctx = await session_for(rig)
    token = rig.supabase.mint(rig.clock.now())
    rig.clock.advance(7 * 3600 * 24)  # the cached key set has expired too
    rig.supabase.jwks_down = True
    review = await rig.service.review_leafout_session(ctx, token)
    assert review.action == "keep" and review.reason == "upstream"


async def test_a_session_of_the_apps_own_sign_in_is_left_alone(make_auth: MakeAuth) -> None:
    rig = make_auth(leafout=True, google=False, mailer=False)
    user = await rig.fakes.users.sign_in_email(email=EMAIL, now=rig.clock.now())
    token, _ = await rig.service.sessions.start(user.id, ip_hash=None, user_agent=None)
    ctx = await rig.service.sessions.resolve(token)
    assert ctx is not None and ctx.session.source is None
    assert (await rig.service.review_leafout_session(ctx, None)).action == "keep"
    assert (await rig.service.review_leafout_session(ctx, "garbage")).action == "keep"


# ───────────────────────── providers and the app's own sign-in ─────────────────────────


def test_the_providers_report_leafout_and_the_apps_own_paths(make_auth: MakeAuth) -> None:
    assert make_auth().service.providers == Providers(google=True, magic=True, leafout=False)
    assert make_auth(leafout=True).service.providers == Providers(google=True, magic=True, leafout=True)
    assert make_auth(leafout=True, auth={"supabase_url": None}).service.providers.leafout is False


def test_with_own_sign_in_off_google_and_the_link_are_switched_off(make_auth: MakeAuth) -> None:
    rig = make_auth(leafout=True, own_sign_in=False)
    assert rig.settings.google_client_id is not None  # the credentials are set, and still not used
    assert rig.service.google is None and rig.service.magic.enabled is False
    assert rig.service.providers == Providers(google=False, magic=False, leafout=True)
    assert rig.service.own_sign_in is False
    with pytest.raises(GoogleError, match="not_configured"):
        rig.service.google_start("/")


async def test_with_own_sign_in_off_no_link_is_issued(make_auth: MakeAuth) -> None:
    rig = make_auth(leafout=True, own_sign_in=False)
    outcome = await rig.service.request_magic_link(EMAIL, "iphash")
    assert outcome.issued is False and rig.mailer.sent == []


def test_leafout_is_off_until_all_three_settings_are_set(make_auth: MakeAuth) -> None:
    for missing in ("supabase_url", "supabase_publishable_key", "supabase_sign_in_url"):
        assert make_auth(leafout=True, auth={missing: None}).service.leafout is None, missing


@pytest.mark.parametrize(
    ("providers", "setting", "allowed"),
    [
        (Providers(False, False, False), "auto", True),
        (
            Providers(False, False, True),
            "auto",
            True,
        ),  # the Leafout account does not switch anonymous match off
        (Providers(True, False, True), "auto", False),
        (Providers(False, True, False), "auto", False),
        (Providers(True, True, True), "true", True),
        (Providers(False, False, False), "false", False),
        (Providers(False, False, True), "false", False),
    ],
)
def test_anonymous_match_counts_only_the_apps_own_providers(
    providers: Providers, setting: str, allowed: bool
) -> None:
    assert MatchSettings(allow_anonymous=setting).anonymous_allowed(providers) is allowed  # type: ignore[arg-type]


# ───────────────────────── the sign-in link ─────────────────────────


def test_the_sign_in_link_carries_a_same_origin_next(rig: Rig) -> None:
    url = rig.service.sign_in_url("/match?x=1")
    assert url == f"{SIGN_IN_URL}?next=https%3A%2F%2Ftayyari.example%2Fmatch%3Fx%3D1"
    assert rig.service.sign_in_url(None) == f"{SIGN_IN_URL}?next=https%3A%2F%2Ftayyari.example%2F"
    assert PUBLIC_URL == "https://tayyari.example"


@pytest.mark.parametrize(
    "evil", ["https://evil.example/", "//evil.example", "/\\evil.example", "javascript:alert(1)", ""]
)
def test_the_sign_in_link_never_leaves_this_site(rig: Rig, evil: str) -> None:
    url = rig.service.sign_in_url(evil)
    assert url is not None and url.endswith("%2F") and "evil" not in url


def test_there_is_no_sign_in_link_without_leafout(make_auth: MakeAuth) -> None:
    assert make_auth().service.sign_in_url("/") is None


# ───────────────────────── logs ─────────────────────────


@contextmanager
def captured(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Every structured log event of the auth modules in the block."""
    for module in ("service", "supabase", "sessions", "google", "magic"):
        monkeypatch.setattr(f"tayyari.auth.{module}.log", structlog.get_logger(module))
    with structlog.testing.capture_logs() as logs:
        yield logs


async def test_no_log_line_carries_a_token_the_cookie_a_sub_an_email_or_a_phone(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.supabase.phone = "+919800000000"
    token = rig.supabase.mint(rig.clock.now())
    payload = token.split(".")[1]
    secrets_ = [
        token,
        payload,
        SUB,
        SESSION_ID,
        EMAIL,
        "Ana Rao",
        "919800000000",
        rig.supabase.mint(rig.clock.now()),
    ]
    with captured(monkeypatch) as logs:
        good = await sign_in(rig, token)  # a sign-in
        assert good.signed_in is not None and good.signed_in.token  # the app's own token is a secret too
        ctx = await rig.service.sessions.resolve(good.signed_in.token)
        assert ctx is not None
        await sign_in(rig, "x.y.z")  # a malformed cookie
        await sign_in(rig, rig.supabase.mint(rig.clock.now(), role="anon"))  # a refused one
        rig.supabase.user_status = 401
        await sign_in(rig, rig.supabase.mint(rig.clock.now(), exp=int(rig.clock.now().timestamp()) + 900))
        rig.supabase.user_status = 200
        rig.clock.advance(25 * HOUR)
        fresh = rig.supabase.mint(rig.clock.now())
        for cookie in (None, fresh, rig.supabase.mint(rig.clock.now(), sub=OTHER_SUB), "garbage", token):
            await rig.service.review_leafout_session(ctx, cookie)
        taken = await rig.fakes.users.sign_in_supabase(
            sub=OTHER_SUB, email="held@example.com", email_verified=True, name=None, now=rig.clock.now()
        )
        assert taken
        rig.supabase.email = "held@example.com"
        await sign_in(rig, rig.supabase.mint(rig.clock.now(), sub="2f2f2f2f-aaaa-4bbb-8ccc-1d1d1d1d1d1d"))

    assert logs, "the sign-in logged nothing at all"
    dump = json.dumps(logs, default=str)
    for secret in [*secrets_, good.signed_in.token]:
        assert secret not in dump, secret[:12]
    assert "held@example.com" not in dump and OTHER_SUB not in dump
    events = {entry["event"] for entry in logs}
    assert {"leafout_bridge", "leafout_recheck"} <= events


async def test_the_refusal_logs_a_reason_code_and_the_internal_account_id_only(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder = await rig.fakes.users.sign_in_supabase(
        sub=OTHER_SUB, email=EMAIL, email_verified=True, name=None, now=rig.clock.now()
    )
    with captured(monkeypatch) as logs:
        result = await sign_in(rig)
    assert result.issue == "email_taken"
    [entry] = [e for e in logs if e["event"] == "leafout_bridge"]
    assert entry["outcome"] == "refused" and entry["reason"] == "email_taken"
    assert URL not in json.dumps(logs, default=str) and USER_URL not in json.dumps(logs, default=str)
    assert holder.id  # the id is bound into the log context by the service, not printed as a field
