"""`SupabaseVerifier`: the local checks on a Leafout access token (one known-bad row per check), the key set (cached,
refetched once for a rotated key, throttled), Supabase's own user call, and the three verify modes. No network: the project
is `FakeSupabase` behind a mock transport."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest

from tayyari.auth.jwks import JWKS_REFETCH_MIN_S, JWKS_TTL_S
from tayyari.auth.supabase import SupabaseError, SupabaseVerifier
from tests.fakes.auth import FakeNetwork
from tests.fakes.clock import FakeClock
from tests.fakes.supabase import (
    EMAIL,
    ISSUER,
    JWKS_URL,
    PUBLISHABLE_KEY,
    SESSION_ID,
    SUB,
    URL,
    USER_URL,
    FakeSupabase,
    b64,
    unsigned,
)


@dataclass
class Rig:
    verifier: SupabaseVerifier
    fake: FakeSupabase
    net: FakeNetwork
    clock: FakeClock
    http: httpx2.AsyncClient

    def mint(self, **overrides: Any) -> str:
        return self.fake.mint(self.clock.now(), **overrides)

    @property
    def now(self) -> int:
        return int(self.clock.now().timestamp())


@pytest.fixture
def make_rig():  # type: ignore[no-untyped-def]
    def build(mode: str = "jwks", **fake_fields: Any) -> Rig:
        clock, net, fake = FakeClock(), FakeNetwork(), FakeSupabase(**fake_fields)
        fake.install(net)
        http = httpx2.AsyncClient(transport=net.transport)
        verifier = SupabaseVerifier(
            url=URL, publishable_key=PUBLISHABLE_KEY, mode=mode, max_token_age_s=3600, http=http, clock=clock
        )
        return Rig(verifier, fake, net, clock, http)

    return build


@pytest.fixture
def rig(make_rig: Callable[..., Rig]) -> Rig:
    return make_rig()


# ───────────────────────── a good token ─────────────────────────


async def test_a_good_token_gives_its_claims(rig: Rig) -> None:
    claims = await rig.verifier.claims(rig.mint())
    assert claims.sub == SUB and claims.session_id == SESSION_ID


async def test_the_claims_are_never_in_a_repr(rig: Rig) -> None:
    token = rig.mint()
    claims = await rig.verifier.claims(token)
    identity = await rig.verifier.confirm(token, claims)
    text = repr(claims) + repr(identity)
    for secret in (SUB, SESSION_ID, EMAIL, "Ana Rao"):
        assert secret not in text


async def test_the_audience_may_be_a_list(rig: Rig) -> None:
    assert (await rig.verifier.claims(rig.mint(aud=["authenticated", "other"]))).sub == SUB


async def test_a_token_a_little_in_the_future_or_just_expired_is_within_the_leeway(rig: Rig) -> None:
    await rig.verifier.claims(rig.mint(iat=rig.now + 20))
    await rig.verifier.claims(rig.mint(iat=rig.now - 100, exp=rig.now - 20))


# ───────────────────────── the known-bad tokens: one row per check ─────────────────────────


def _claims(rig: Rig, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": ISSUER,
        "aud": "authenticated",
        "role": "authenticated",
        "sub": SUB,
        "iat": rig.now,
        "exp": rig.now + 3600,
    }
    base.update(over)
    return base


def _hs256_with_the_public_key(rig: Rig) -> str:
    """The classic confusion attack: an HS256 token whose MAC key is the public key everyone can read."""
    header = b64(b'{"alg":"HS256","typ":"JWT","kid":"key-1"}')
    import json

    payload = b64(json.dumps(_claims(rig)).encode())
    secret = rig.fake.signing.as_pem(private=False)
    mac = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(mac)}"


def _tampered(rig: Rig) -> str:
    import json

    header, _payload, signature = rig.mint().split(".")
    forged = b64(json.dumps(_claims(rig, sub="11111111-2222-4333-8444-555555555555")).encode())
    return f"{header}.{forged}.{signature}"


BAD_TOKENS: list[tuple[str, Callable[[Rig], str], str]] = [
    ("alg none", lambda r: unsigned({"alg": "none", "kid": "key-1"}, _claims(r)), "alg"),
    ("alg HS256 signed with the public key", _hs256_with_the_public_key, "alg"),
    ("alg HS512", lambda r: unsigned({"alg": "HS512", "kid": "key-1"}, _claims(r)), "alg"),
    ("alg missing", lambda r: unsigned({"kid": "key-1"}, _claims(r)), "alg"),
    (
        "RS256 with an unknown kid",
        lambda r: unsigned({"alg": "RS256", "kid": "ghost"}, _claims(r)),
        "kid_unknown",
    ),
    ("no kid", lambda r: unsigned({"alg": "ES256"}, _claims(r)), "kid_unknown"),
    (
        "a known kid signed by another key",
        lambda r: r.mint(key=r.fake.other_key, kid="key-1"),
        "signature",
    ),
    ("a tampered payload", _tampered, "signature"),
    ("iss of another project", lambda r: r.mint(iss="https://other.supabase.example/auth/v1"), "iss"),
    ("iss missing", lambda r: r.mint(iss=None), "iss"),
    ("aud anon", lambda r: r.mint(aud="anon"), "aud"),
    ("aud missing", lambda r: r.mint(aud=None), "aud"),
    ("aud a list without authenticated", lambda r: r.mint(aud=["anon"]), "aud"),
    ("role service_role", lambda r: r.mint(role="service_role"), "role"),
    ("role anon", lambda r: r.mint(role="anon"), "role"),
    ("role missing", lambda r: r.mint(role=None), "role"),
    ("exp in the past", lambda r: r.mint(iat=r.now - 7200, exp=r.now - 3600), "exp"),
    ("exp missing", lambda r: r.mint(exp=None), "exp"),
    ("exp not a number", lambda r: r.mint(exp="soon"), "exp"),
    ("iat in the future", lambda r: r.mint(iat=r.now + 600), "iat"),
    ("iat missing", lambda r: r.mint(iat=None), "iat"),
    (
        "iat older than the maximum age, exp still valid",
        lambda r: r.mint(iat=r.now - 7200, exp=r.now + 3600),
        "too_old",
    ),
    ("sub not a uuid", lambda r: r.mint(sub="not-a-uuid"), "sub"),
    ("sub missing", lambda r: r.mint(sub=None), "sub"),
    ("an anonymous Supabase user", lambda r: r.mint(is_anonymous=True), "anonymous"),
    ("5000 characters", lambda r: "a" * 5000, "malformed"),
    ("empty", lambda r: "", "malformed"),
    ("two parts", lambda r: "abc.def", "malformed"),
    ("broken base64", lambda r: "!!!.@@@.###", "malformed"),
    ("a header that is not JSON", lambda r: f"{b64(b'nope')}.{b64(b'{}')}.{b64(b'x')}", "malformed"),
    ("a payload that is a list", lambda r: f"{b64(b'{}')}.{b64(b'[1]')}.{b64(b'x')}", "malformed"),
]


@pytest.mark.parametrize(("label", "build", "reason"), BAD_TOKENS, ids=[row[0] for row in BAD_TOKENS])
async def test_each_check_refuses_its_known_bad_token(
    rig: Rig, label: str, build: Callable[[Rig], str], reason: str
) -> None:
    with pytest.raises(SupabaseError) as caught:
        await rig.verifier.claims(build(rig))
    assert caught.value.reason == reason, label


async def test_a_refused_token_never_reaches_supabases_user_endpoint(rig: Rig) -> None:
    with pytest.raises(SupabaseError):
        await rig.verifier.claims(rig.mint(role="anon"))
    assert rig.fake.user_requests == []


async def test_alg_is_refused_before_any_key_is_fetched(rig: Rig) -> None:
    with pytest.raises(SupabaseError, match="alg"):
        await rig.verifier.claims(unsigned({"alg": "none"}, _claims(rig)))
    assert rig.fake.jwks_requests == []


# ───────────────────────── identity check: time set aside, signature kept ─────────────────────────


async def test_inspect_reports_an_expired_token_as_stale_but_genuine(rig: Rig) -> None:
    found = await rig.verifier.inspect(rig.mint(iat=rig.now - 7200, exp=rig.now - 3600))
    assert found.stale == "exp" and found.claims.sub == SUB


async def test_inspect_reports_an_old_token_as_too_old(rig: Rig) -> None:
    assert (await rig.verifier.inspect(rig.mint(iat=rig.now - 7200))).stale == "too_old"


async def test_inspect_still_refuses_a_bad_signature_on_an_expired_token(rig: Rig) -> None:
    forged = rig.mint(key=rig.fake.other_key, kid="key-1", iat=rig.now - 7200, exp=rig.now - 3600)
    with pytest.raises(SupabaseError, match="signature"):
        await rig.verifier.inspect(forged)


async def test_inspect_of_a_current_token_is_not_stale(rig: Rig) -> None:
    assert (await rig.verifier.inspect(rig.mint())).stale is None


# ───────────────────────── the key set ─────────────────────────


async def test_the_key_set_is_fetched_once_and_reused(rig: Rig) -> None:
    for _ in range(3):
        await rig.verifier.claims(rig.mint())
    assert len(rig.fake.jwks_requests) == 1


async def test_the_key_set_expires_after_hours(rig: Rig) -> None:
    await rig.verifier.claims(rig.mint())
    rig.clock.advance(JWKS_TTL_S + 1)
    await rig.verifier.claims(rig.mint())
    assert len(rig.fake.jwks_requests) == 2


async def test_a_rotated_key_triggers_one_refetch_and_the_refetch_is_throttled(rig: Rig) -> None:
    await rig.verifier.claims(rig.mint())
    assert len(rig.fake.jwks_requests) == 1

    rig.fake.rotate()  # the project signs with a new key and publishes only that one
    fresh = rig.mint()
    for _ in range(5):  # within a minute of the last fetch: no refetch, so the new key is not known yet
        with pytest.raises(SupabaseError, match="kid_unknown"):
            await rig.verifier.claims(fresh)
    assert len(rig.fake.jwks_requests) == 1

    rig.clock.advance(JWKS_REFETCH_MIN_S + 1)
    await rig.verifier.claims(rig.mint())  # one look at the key set finds it
    assert len(rig.fake.jwks_requests) == 2


async def test_garbage_cannot_make_the_app_hammer_the_key_endpoint(rig: Rig) -> None:
    for _ in range(6):
        with pytest.raises(SupabaseError, match="kid_unknown"):
            await rig.verifier.claims(unsigned({"alg": "ES256", "kid": "ghost"}, _claims(rig)))
    assert len(rig.fake.jwks_requests) == 1


async def test_a_key_whose_alg_differs_from_the_headers_is_not_used(rig: Rig) -> None:
    published = rig.fake.signing.as_dict(private=False)
    published["alg"] = "RS256"
    rig.net.on("GET", JWKS_URL, lambda _r: httpx2.Response(200, json={"keys": [published]}))
    with pytest.raises(SupabaseError, match="kid_unknown"):
        await rig.verifier.claims(rig.mint())


async def test_an_unreachable_key_endpoint_is_upstream_not_a_refusal(rig: Rig) -> None:
    rig.fake.jwks_down = True
    with pytest.raises(SupabaseError, match="upstream"):
        await rig.verifier.claims(rig.mint())
    rig.fake.jwks_down = False  # the next request tries again
    assert (await rig.verifier.claims(rig.mint())).sub == SUB


async def test_a_key_set_that_is_not_one_is_upstream(rig: Rig) -> None:
    rig.net.on("GET", JWKS_URL, lambda _r: httpx2.Response(200, json={"keys": "nope"}))
    with pytest.raises(SupabaseError, match="upstream"):
        await rig.verifier.claims(rig.mint())


# ───────────────────────── Supabase's own answer ─────────────────────────


async def test_the_user_call_sends_the_token_and_the_publishable_key(rig: Rig) -> None:
    token = rig.mint()
    identity = await rig.verifier.confirm(token, await rig.verifier.claims(token))
    [request] = rig.fake.user_requests
    assert str(request.url) == USER_URL
    assert request.headers["authorization"] == f"Bearer {token}"
    assert request.headers["apikey"] == PUBLISHABLE_KEY
    assert (identity.email, identity.email_verified, identity.name) == (EMAIL, True, "Ana Rao")
    assert identity.sub == SUB and identity.session_id == SESSION_ID


async def test_a_revoked_session_is_refused(rig: Rig) -> None:
    rig.fake.user_status = 401
    token = rig.mint()
    claims = await rig.verifier.claims(token)
    with pytest.raises(SupabaseError, match="revoked"):
        await rig.verifier.confirm(token, claims)


async def test_a_deleted_user_is_refused(rig: Rig) -> None:
    rig.fake.user_status = 403
    token = rig.mint()
    with pytest.raises(SupabaseError, match="revoked"):
        await rig.verifier.confirm(token, await rig.verifier.claims(token))


async def test_an_answer_for_a_different_person_is_refused(rig: Rig) -> None:
    rig.fake.user_id = "99999999-8888-4777-8666-555555555555"
    token = rig.mint()
    with pytest.raises(SupabaseError, match="mismatch"):
        await rig.verifier.confirm(token, await rig.verifier.claims(token))


@pytest.mark.parametrize("status", [500, 502, 503, 429])
async def test_an_outage_is_upstream(rig: Rig, status: int) -> None:
    rig.fake.user_status = status
    token = rig.mint()
    with pytest.raises(SupabaseError, match="upstream"):
        await rig.verifier.confirm(token, await rig.verifier.claims(token))


async def test_an_unreachable_user_endpoint_is_upstream(rig: Rig) -> None:
    def down(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route")

    rig.net.on("GET", USER_URL, down)
    token = rig.mint()
    with pytest.raises(SupabaseError, match="upstream"):
        await rig.verifier.confirm(token, await rig.verifier.claims(token))


async def test_an_unconfirmed_email_is_not_a_verified_one(rig: Rig) -> None:
    rig.fake.email_confirmed = False
    token = rig.mint()
    identity = await rig.verifier.confirm(token, await rig.verifier.claims(token))
    assert identity.email is None and identity.email_verified is False


async def test_a_phone_only_person_has_no_email(rig: Rig) -> None:
    rig.fake.email, rig.fake.phone, rig.fake.name = None, "+919800000000", None
    token = rig.mint(email=None)
    identity = await rig.verifier.confirm(token, await rig.verifier.claims(token))
    assert (identity.email, identity.name) == (None, None)


async def test_the_name_is_cleaned_and_clipped(rig: Rig) -> None:
    rig.fake.name = "  Ana \n  Rao  " + "x" * 400
    token = rig.mint()
    identity = await rig.verifier.confirm(token, await rig.verifier.claims(token))
    assert identity.name is not None and identity.name.startswith("Ana Rao x") and len(identity.name) == 200


# ───────────────────────── the verify modes ─────────────────────────


async def test_user_endpoint_mode_leaves_the_signature_to_supabase_but_still_filters_claims(
    make_rig: Callable[..., Rig],
) -> None:
    rig = make_rig("user_endpoint", empty_jwks=True)
    forged = rig.mint(key=rig.fake.other_key)  # a signature this app could not check
    assert (await rig.verifier.claims(forged)).sub == SUB  # accepted locally; Supabase's user call decides
    assert rig.fake.jwks_requests == []  # the key set is not even asked for
    with pytest.raises(SupabaseError, match="role"):
        await rig.verifier.claims(rig.mint(role="service_role"))
    with pytest.raises(SupabaseError, match="alg"):
        await rig.verifier.claims(unsigned({"alg": "HS256"}, _claims(rig)))


async def test_auto_mode_uses_the_keys_when_the_project_publishes_one(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig("auto")
    assert (await rig.verifier.claims(rig.mint())).sub == SUB
    with pytest.raises(SupabaseError, match="signature"):
        await rig.verifier.claims(rig.mint(key=rig.fake.other_key, kid="key-1"))


async def test_auto_mode_falls_back_to_the_user_endpoint_with_no_usable_key(
    make_rig: Callable[..., Rig],
) -> None:
    rig = make_rig("auto", empty_jwks=True)
    assert (await rig.verifier.claims(rig.mint())).sub == SUB


async def test_jwks_mode_with_an_empty_key_set_refuses_everything(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig("jwks", empty_jwks=True)
    with pytest.raises(SupabaseError, match="kid_unknown"):
        await rig.verifier.claims(rig.mint())
