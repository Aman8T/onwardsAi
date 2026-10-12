"""`AuthService`: the Google sign-in from start to finish with a fake Google, and which providers a server offers."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from tayyari.auth.google import GoogleError, read_state
from tests.fakes.auth import link_in
from tests.unit.auth.conftest import PUBLIC_URL, STATE_KEY, MakeAuth, Rig


def begin(rig: Rig, next_path: str | None = "/graph") -> tuple[str, str, str]:
    """Start a sign-in: ``(state, nonce, cookie value)`` the way the browser would carry them."""
    start = rig.service.google_start(next_path)
    query = parse_qs(urlsplit(start.url).query)
    return query["state"][0], query["nonce"][0], start.cookie_value


async def finish(
    rig: Rig, state: str | None, cookie: str | None, *, code: str | None = "code-1", ua: str | None = "UA"
):  # type: ignore[no-untyped-def]
    return await rig.service.google_finish(
        code=code, state=state, cookie_value=cookie, ip_hash="iphash", user_agent=ua
    )


async def test_a_google_sign_in_creates_the_account_and_a_session(make_auth: MakeAuth) -> None:
    rig = make_auth()
    state, nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce=nonce)

    signed = await finish(rig, state, cookie)

    assert signed.next_path == "/graph"
    assert (signed.user.email, signed.user.name, signed.user.google_sub) == (
        "ana@example.com",
        "Ana Rao",
        "1100110011",
    )
    assert signed.user.picture_url == "https://lh3.googleusercontent.com/a/photo"
    resolved = await rig.service.sessions.resolve(signed.token)
    assert resolved is not None and resolved.user.id == signed.user.id
    assert resolved.session.ip_hash == "iphash" and resolved.session.user_agent == "UA"


async def test_the_same_google_account_is_the_same_user_next_time(make_auth: MakeAuth) -> None:
    rig = make_auth()
    ids = []
    for _ in range(2):
        state, nonce, cookie = begin(rig)
        rig.google.token_for(
            rig.clock.now(), nonce=nonce, email="renamed@example.com"
        )  # even with a new email
        ids.append((await finish(rig, state, cookie)).user.id)
    assert ids[0] == ids[1] and len(rig.fakes.users.users) == 1


async def test_a_google_sign_in_finds_the_account_made_by_an_emailed_link(make_auth: MakeAuth) -> None:
    rig = make_auth()
    await rig.service.request_magic_link("ana@example.com", None)
    from tests.unit.auth.test_magic import token_of

    by_link = await rig.service.verify_magic_link(
        token_of(link_in(rig.mailer.sent[0])), ip_hash=None, user_agent=None
    )
    assert by_link is not None and by_link.user.google_sub is None

    state, nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce=nonce)  # the same address, verified by Google
    by_google = await finish(rig, state, cookie)

    assert by_google.user.id == by_link.user.id and len(rig.fakes.users.users) == 1
    assert by_google.user.google_sub == "1100110011"  # attached for next time


async def test_google_does_not_overwrite_a_name_the_person_chose(make_auth: MakeAuth) -> None:
    rig = make_auth()
    state, nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce=nonce)
    first = await finish(rig, state, cookie)
    await rig.fakes.users.update(first.user.id, name="Ana R.")

    state, nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce=nonce, picture="https://lh3.googleusercontent.com/a/new")
    again = await finish(rig, state, cookie)

    assert again.user.name == "Ana R."
    assert again.user.picture_url == "https://lh3.googleusercontent.com/a/new"


async def test_an_email_google_has_not_verified_never_signs_in_or_links(make_auth: MakeAuth) -> None:
    rig = make_auth()
    victim = await rig.fakes.users.sign_in_email(email="ana@example.com", now=rig.clock.now())
    state, nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce=nonce, email_verified=False, sub="attacker")

    with pytest.raises(GoogleError, match="email_not_verified"):
        await finish(rig, state, cookie)

    assert (await rig.fakes.users.get(victim.id)).google_sub is None  # type: ignore[union-attr]
    assert rig.fakes.users.sessions == {}


async def test_the_state_must_match_the_cookie(make_auth: MakeAuth) -> None:
    rig = make_auth()
    state, nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce=nonce)
    for wrong in (None, "", "another-state", state + "x"):
        with pytest.raises(GoogleError, match="state_mismatch"):
            await finish(rig, wrong, cookie)
    assert rig.google.token_requests == []  # the code was never exchanged
    assert rig.fakes.users.users == {}


async def test_without_the_state_cookie_there_is_no_sign_in(make_auth: MakeAuth) -> None:
    rig = make_auth()
    state, nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce=nonce)
    for missing in (None, "", "garbage", cookie[:-3] + "AAA"):
        with pytest.raises(GoogleError, match="state_cookie_missing_or_invalid"):
            await finish(rig, state, missing)
    assert rig.google.token_requests == []


async def test_a_state_from_another_login_is_not_accepted(make_auth: MakeAuth) -> None:
    """A callback built from someone else's state and the attacker's own cookie still has to match its nonce."""
    rig = make_auth()
    state, _nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce="a-nonce-from-another-sign-in")
    with pytest.raises(GoogleError, match="id_token_claims"):
        await finish(rig, state, cookie)


async def test_the_state_cookie_expires_after_ten_minutes(make_auth: MakeAuth) -> None:
    rig = make_auth()
    state, nonce, cookie = begin(rig)
    rig.google.token_for(rig.clock.now(), nonce=nonce)
    rig.clock.advance(601)
    with pytest.raises(GoogleError, match="state_cookie_missing_or_invalid"):
        await finish(rig, state, cookie)


async def test_the_callback_needs_a_code(make_auth: MakeAuth) -> None:
    rig = make_auth()
    state, _nonce, cookie = begin(rig)
    with pytest.raises(GoogleError, match="no_code"):
        await finish(rig, state, cookie, code=None)


async def test_next_is_carried_in_the_cookie_and_kept_on_this_site(make_auth: MakeAuth) -> None:
    rig = make_auth()
    for asked, expected in [("/graph?x=1", "/graph?x=1"), ("https://evil.example", "/"), (None, "/")]:
        start = rig.service.google_start(asked)
        saved = read_state(STATE_KEY, start.cookie_value, now_ts=int(rig.clock.now().timestamp()))
        assert saved is not None and saved.next_path == expected


def test_the_authorization_redirect_points_back_at_our_callback(make_auth: MakeAuth) -> None:
    rig = make_auth()
    url = rig.service.google_start("/").url
    assert parse_qs(urlsplit(url).query)["redirect_uri"] == [f"{PUBLIC_URL}/api/auth/google/callback"]


# ───────────────────────── providers ─────────────────────────


def test_providers_say_what_this_server_can_do(make_auth: MakeAuth) -> None:
    assert make_auth(google=True, mailer=True).service.providers == (True, True)
    assert make_auth(google=False, mailer=True).service.providers == (False, True)
    assert make_auth(google=True, mailer=False).service.providers == (True, False)
    assert make_auth(google=False, mailer=False).service.providers == (False, False)
    assert make_auth(google=False, mailer=False, env="local").service.providers == (False, True)


async def test_without_google_credentials_google_sign_in_is_unavailable(make_auth: MakeAuth) -> None:
    rig = make_auth(google=False)
    with pytest.raises(GoogleError, match="not_configured"):
        rig.service.google_start("/")
    with pytest.raises(GoogleError, match="not_configured"):
        await finish(rig, "s", "c")


async def test_one_half_of_the_google_credentials_is_not_enough(make_auth: MakeAuth) -> None:
    rig = make_auth(google=False, google_client_id="only-the-id")
    assert rig.service.providers[0] is False
