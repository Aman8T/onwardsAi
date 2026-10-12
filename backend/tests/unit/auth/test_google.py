"""Sign in with Google: the same-origin `next`, the signed state cookie, the authorization URL, and the ID token
checks, against a fake Google (an RSA key, a token endpoint and a key set on a `FakeNetwork`: no network)."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from tayyari.auth import google as g
from tayyari.auth.google import GoogleError, GoogleOAuth, OAuthState
from tests.fakes.auth import CLIENT_ID, CLIENT_SECRET, FakeGoogle, FakeNetwork, new_key
from tests.fakes.clock import FakeClock

KEY = bytes(range(32))
REDIRECT = "https://tayyari.example/api/auth/google/callback"

# ───────────────────────── safe_next ─────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/", "/"),
        ("/profile?tab=graph", "/profile?tab=graph"),
        ("/a/b#frag", "/a/b#frag"),
        ("https://evil.example/", "/"),
        ("http://evil.example", "/"),
        ("//evil.example/path", "/"),
        ("///evil.example", "/"),
        ("/\\evil.example", "/"),
        ("\\\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("evil.example", "/"),
        ("path/without/slash", "/"),
        ("/ok\r\nSet-Cookie: a=b", "/"),
        ("/ok\x00", "/"),
        ("/ok\x7f", "/"),
        ("/" + "a" * 600, "/"),
    ],
)
def test_next_must_be_a_path_on_this_site(raw: str | None, expected: str) -> None:
    assert g.safe_next(raw) == expected


# ───────────────────────── the state cookie ─────────────────────────


def a_state(now_ts: int = 1_000_000, next_path: str = "/graph") -> OAuthState:
    return g.new_state(next_path, now_ts=now_ts)


def test_a_state_survives_the_round_trip() -> None:
    state = a_state()
    value = g.sign_state(KEY, state)
    assert g.read_state(KEY, value, now_ts=state.issued_at + 5) == state
    assert 43 <= len(state.verifier) <= 128  # RFC 7636
    assert len({g.new_state("/", now_ts=1).state for _ in range(20)}) == 20


def test_a_forged_edited_or_foreign_cookie_is_refused() -> None:
    state = a_state()
    value = g.sign_state(KEY, state)
    payload, signature = value.split(".")

    edited = json.loads(base64.urlsafe_b64decode(payload + "=="))
    edited["x"] = "/admin"
    forged_payload = base64.urlsafe_b64encode(json.dumps(edited).encode()).rstrip(b"=").decode()

    assert g.read_state(bytes(32), value, now_ts=state.issued_at) is None  # signed with another key
    assert g.read_state(KEY, f"{forged_payload}.{signature}", now_ts=state.issued_at) is None  # edited
    assert g.read_state(KEY, f"{payload}.{signature[:-2]}AA", now_ts=state.issued_at) is None  # bad signature
    assert g.read_state(KEY, payload, now_ts=state.issued_at) is None  # no signature
    assert g.read_state(KEY, f"{payload}.{signature}.x", now_ts=state.issued_at) is None
    assert g.read_state(KEY, "", now_ts=state.issued_at) is None
    assert g.read_state(KEY, None, now_ts=state.issued_at) is None
    assert g.read_state(KEY, "x" * 5000, now_ts=state.issued_at) is None
    assert g.read_state(KEY, "not.base64!!", now_ts=state.issued_at) is None


def test_a_state_is_good_for_ten_minutes() -> None:
    state = a_state()
    value = g.sign_state(KEY, state)
    assert g.read_state(KEY, value, now_ts=state.issued_at + 600) is not None
    assert g.read_state(KEY, value, now_ts=state.issued_at + 601) is None
    assert g.read_state(KEY, value, now_ts=state.issued_at - 61) is None  # from the future


def test_a_signed_next_is_checked_again_when_it_is_read() -> None:
    bad = OAuthState(
        state="s", verifier="v" * 50, nonce="n", next_path="https://evil.example", issued_at=1000
    )
    assert g.read_state(KEY, g.sign_state(KEY, bad), now_ts=1000).next_path == "/"  # type: ignore[union-attr]


# ───────────────────────── the Google client ─────────────────────────


class Rig:
    def __init__(self, key: Any) -> None:
        self.clock = FakeClock()
        self.google = FakeGoogle(key=key)
        self.net = FakeNetwork()
        self.google.install(self.net)
        transport = self.net.transport
        self.http = httpx2.AsyncClient(transport=transport)
        self.oauth = GoogleOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT,
            http=self.http,
            clock=self.clock,
            transport=transport,
        )
        self.state = g.new_state("/", now_ts=int(self.clock.now().timestamp()))

    def token(self, **claims: Any) -> None:
        self.google.token_for(self.clock.now(), **{"nonce": self.state.nonce, **claims})

    async def identity(self) -> g.GoogleIdentity:
        return await self.oauth.identity("auth-code", self.state)


@pytest.fixture
async def rig(google_key: Any) -> AsyncIterator[Rig]:
    r = Rig(google_key)
    yield r
    await r.http.aclose()


def test_the_authorization_url_carries_state_pkce_and_nonce(rig: Rig) -> None:
    url = rig.oauth.authorization_url(rig.state)
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}

    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == g.AUTHORIZE_URL
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == REDIRECT
    assert set(query["scope"].split()) == {"openid", "email", "profile"}
    assert query["state"] == rig.state.state
    assert query["nonce"] == rig.state.nonce
    assert query["code_challenge_method"] == "S256"
    expected = base64.urlsafe_b64encode(hashlib.sha256(rig.state.verifier.encode()).digest()).rstrip(b"=")
    assert query["code_challenge"] == expected.decode()
    assert rig.state.verifier not in url  # the verifier itself never leaves our cookie
    assert CLIENT_SECRET not in url


async def test_a_good_sign_in_returns_the_verified_identity(rig: Rig) -> None:
    rig.token()
    who = await rig.identity()
    assert who == g.GoogleIdentity(
        sub="1100110011",
        email="ana@example.com",
        name="Ana Rao",
        picture="https://lh3.googleusercontent.com/a/photo",
        locale="en-GB",
    )


async def test_the_code_is_exchanged_with_the_pkce_verifier(rig: Rig) -> None:
    rig.token()
    await rig.identity()
    (sent,) = rig.google.token_requests
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "auth-code"
    assert sent["code_verifier"] == rig.state.verifier
    assert sent["redirect_uri"] == REDIRECT
    assert (sent["client_id"], sent["client_secret"]) == (CLIENT_ID, CLIENT_SECRET)


async def test_the_email_is_lower_cased_and_a_string_true_counts_as_verified(rig: Rig) -> None:
    rig.token(email="Ana.Rao@Example.COM", email_verified="true")
    assert (await rig.identity()).email == "ana.rao@example.com"


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"nonce": "someone-elses"}, "id_token_claims"),
        ({"nonce": None}, "id_token_claims"),
        ({"aud": "another-client"}, "id_token_claims"),
        ({"iss": "https://evil.example"}, "id_token_claims"),
        ({"exp": 1}, "id_token_claims"),
        ({"sub": None}, "id_token_claims"),
        ({"email_verified": False}, "email_not_verified"),
        ({"email_verified": "false"}, "email_not_verified"),
        ({"email_verified": None}, "email_not_verified"),
        ({"email": None}, "no_email"),
    ],
)
async def test_an_id_token_that_is_not_for_this_sign_in_is_refused(
    rig: Rig, claims: dict[str, Any], reason: str
) -> None:
    rig.token(**claims)
    with pytest.raises(GoogleError) as refused:
        await rig.identity()
    assert refused.value.reason.startswith(reason)


async def test_the_old_issuer_spelling_is_accepted(rig: Rig) -> None:
    rig.token(iss="accounts.google.com")
    assert (await rig.identity()).sub == "1100110011"


async def test_a_token_signed_with_another_key_is_refused(rig: Rig) -> None:
    attacker = FakeGoogle(key=new_key("key-1"))  # same key id, different key
    rig.google.id_token = attacker.mint(rig.clock.now(), nonce=rig.state.nonce)
    with pytest.raises(GoogleError, match="id_token_invalid"):
        await rig.identity()


async def test_an_unsigned_token_is_refused(rig: Rig) -> None:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {
        "iss": g.ISSUERS[0],
        "aud": CLIENT_ID,
        "sub": "1",
        "email": "ana@example.com",
        "email_verified": True,
        "nonce": rig.state.nonce,
        "exp": int(rig.clock.now().timestamp()) + 3600,
    }
    rig.google.id_token = f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."
    with pytest.raises(GoogleError, match="id_token_invalid"):
        await rig.identity()


async def test_a_token_signed_with_a_symmetric_algorithm_is_refused(rig: Rig) -> None:
    """The classic confusion attack: HS256 with the public key as the secret."""
    import hmac

    public_pem = rig.google.key.as_pem(private=False)

    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {
        "iss": g.ISSUERS[0],
        "aud": CLIENT_ID,
        "sub": "1",
        "email": "ana@example.com",
        "nonce": rig.state.nonce,
    }
    signing_input = f"{part({'alg': 'HS256', 'kid': rig.google.key.kid})}.{part(claims)}"
    signature = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    rig.google.id_token = f"{signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
    with pytest.raises(GoogleError, match="id_token_invalid"):
        await rig.identity()


async def test_an_error_from_the_token_endpoint_means_no_sign_in(rig: Rig) -> None:
    rig.google.token_status = 400
    with pytest.raises(GoogleError, match="code_exchange_failed"):
        await rig.identity()


async def test_a_token_response_without_an_id_token_is_refused(rig: Rig) -> None:
    rig.google.id_token = None
    with pytest.raises(GoogleError, match="no_id_token"):
        await rig.identity()


async def test_a_picture_that_is_not_https_is_dropped(rig: Rig) -> None:
    rig.token(picture="http://tracker.example/pixel.png")
    assert (await rig.identity()).picture is None


async def test_google_keys_are_fetched_once_and_reused(rig: Rig) -> None:
    for _ in range(3):
        rig.token()
        await rig.identity()
    assert len(rig.net.calls_to(g.JWKS_URL)) == 1


async def test_a_rotated_key_triggers_one_refetch(rig: Rig) -> None:
    rig.token()
    await rig.identity()
    assert len(rig.net.calls_to(g.JWKS_URL)) == 1

    rotated = FakeGoogle(key=new_key("key-2"))
    rig.google.key = rotated.key  # Google starts signing with a new key and publishes it
    rig.clock.advance(g.JWKS_REFETCH_MIN_S + 1)
    rig.token()
    assert (await rig.identity()).sub == "1100110011"
    assert len(rig.net.calls_to(g.JWKS_URL)) == 2


async def test_garbage_cannot_make_us_hammer_google_for_keys(rig: Rig) -> None:
    rig.google.id_token = "not.a.jwt"
    for _ in range(5):
        with pytest.raises(GoogleError):
            await rig.identity()
    assert len(rig.net.calls_to(g.JWKS_URL)) == 1  # the second look is rate limited to one a minute


async def test_the_key_set_expires_after_hours(rig: Rig) -> None:
    rig.token()
    await rig.identity()
    rig.clock.advance(g.JWKS_TTL_S + 1)
    rig.token()
    await rig.identity()
    assert len(rig.net.calls_to(g.JWKS_URL)) == 2


async def test_an_unreachable_key_endpoint_fails_closed(rig: Rig) -> None:
    def down(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route")

    rig.net.on("GET", g.JWKS_URL, down)
    rig.token()
    with pytest.raises(GoogleError, match="jwks_unavailable"):
        await rig.identity()
