"""A fake Supabase project for the Leafout sign-in tests: its signing key, its tokens and its two endpoints.

FakeSupabase    the project. An EC P-256 key pair is generated when the test run first needs one (nothing is stored in the
                repository), ``mint`` signs access tokens like Supabase does (ES256, ``kid`` header, the claims of a signed-in
                user), and ``install`` registers the project's two endpoints on a `FakeNetwork` (an ``httpx2.MockTransport``
                behind it, so a request nobody registered fails the test):
                  GET <url>/auth/v1/.well-known/jwks.json   the published keys
                  GET <url>/auth/v1/user                    who the token's session belongs to
                Switches: ``empty_jwks`` (no usable key published), ``rotate`` (a new signing key, the old one gone),
                ``user_status`` (401 for a revoked session, 5xx for an outage), ``user_id`` (an answer for a different
                person), ``email`` / ``email_confirmed`` / ``name`` (what the user call reports; ``email=None`` is a
                phone-only person), ``jwks_down`` (the key endpoint fails).
OTHER_KEY       a second key nobody publishes, for tokens signed with the wrong key
cookie_for      the ``Cookie`` header carrying a token
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx2
from joserfc import jwt
from joserfc.jwk import ECKey

from tests.fakes.auth import FakeNetwork

URL = "https://proj.supabase.example"
ISSUER = f"{URL}/auth/v1"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
USER_URL = f"{ISSUER}/user"
PUBLISHABLE_KEY = "sb_publishable_for_tests"  # a made-up value for fakes, not a real credential
SIGN_IN_URL = "https://leafout.example/signin"
SIGN_OUT_URL = "https://leafout.example/signout"
COOKIE = "leafout-access"
SUB = "0b8c6f1e-3a4d-4e5f-9a1b-2c3d4e5f6a7b"
OTHER_SUB = "7d2a1c9e-5b4f-4a3e-8c6d-1f0e9d8c7b6a"
SESSION_ID = "3f2e1d0c-9b8a-4766-8554-433221100ffe"
EMAIL = "ana@example.com"

_keys: dict[str, ECKey] = {}


def make_key(kid: str) -> ECKey:
    """A fresh EC P-256 signing key, as the project publishes them."""
    return ECKey.generate_key("P-256", parameters={"kid": kid, "use": "sig", "alg": "ES256"})


def _key(name: str) -> ECKey:
    """One key per name for the whole test run (generating takes a few milliseconds, and none is ever stored)."""
    if name not in _keys:
        _keys[name] = make_key(name)
    return _keys[name]


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unsigned(header: dict[str, Any], claims: dict[str, Any], signature: bytes = b"x") -> str:
    """A token with the given header and claims and a junk signature (``alg: none`` and similar attacks)."""
    return ".".join([b64(json.dumps(header).encode()), b64(json.dumps(claims).encode()), b64(signature)])


def cookie_for(token: str, name: str = COOKIE) -> dict[str, str]:
    return {"cookie": f"{name}={token}"}


@dataclass
class FakeSupabase:
    signing: ECKey = field(default_factory=lambda: _key("key-1"))
    """The key tokens are signed with (and, unless ``empty_jwks``, the one the project publishes)."""
    empty_jwks: bool = False
    jwks_down: bool = False
    user_status: int = 200
    user_id: str | None = None
    """Answer the user call for this id instead of the token's ``sub``."""
    email: str | None = EMAIL
    email_confirmed: bool = True
    name: str | None = "Ana Rao"
    phone: str | None = None
    jwks_requests: list[httpx2.Request] = field(default_factory=list)
    user_requests: list[httpx2.Request] = field(default_factory=list)

    # ───────────── tokens ─────────────

    @property
    def other_key(self) -> ECKey:
        return _key("key-other")

    def rotate(self) -> None:
        """The project starts signing with a new key and stops publishing the old one."""
        self.signing = _key("key-2")

    def mint(
        self, now: datetime, *, key: ECKey | None = None, kid: str | None = None, **overrides: Any
    ) -> str:
        """An access token as Supabase signs them; ``overrides`` replace claims (set to None to remove one)."""
        stamp = int(now.timestamp())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": "authenticated",
            "role": "authenticated",
            "sub": SUB,
            "session_id": SESSION_ID,
            "email": self.email,
            "iat": stamp,
            "exp": stamp + 3600,
            "is_anonymous": False,
        }
        for name, value in overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        signer = key or self.signing
        header = {"alg": "ES256", "kid": kid if kid is not None else str(signer.kid)}
        return jwt.encode(header, claims, signer, algorithms=["ES256"])

    # ───────────── the endpoints ─────────────

    def jwks(self) -> dict[str, Any]:
        return {"keys": [] if self.empty_jwks else [self.signing.as_dict(private=False)]}

    def user_body(self, sub: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": self.user_id or sub,
            "aud": "authenticated",
            "role": "authenticated",
            "user_metadata": {"full_name": self.name} if self.name else {},
        }
        if self.email:
            body["email"] = self.email
            if self.email_confirmed:
                body["email_confirmed_at"] = "2026-10-01T10:00:00Z"
        if self.phone:
            body["phone"] = self.phone
            body["phone_confirmed_at"] = "2026-10-01T10:00:00Z"
        return body

    def install(self, net: FakeNetwork) -> None:
        def jwks(request: httpx2.Request) -> httpx2.Response:
            self.jwks_requests.append(request)
            if self.jwks_down:
                raise httpx2.ConnectError("no route")
            return httpx2.Response(200, json=self.jwks())

        def user(request: httpx2.Request) -> httpx2.Response:
            self.user_requests.append(request)
            if self.user_status != 200:
                return httpx2.Response(self.user_status, json={"msg": "no"})
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            payload = json.loads(base64.urlsafe_b64decode(bearer.split(".")[1] + "==="))
            return httpx2.Response(200, json=self.user_body(str(payload.get("sub"))))

        net.on("GET", JWKS_URL, jwks)
        net.on("GET", USER_URL, user)


__all__ = [
    "COOKIE",
    "EMAIL",
    "ISSUER",
    "JWKS_URL",
    "OTHER_SUB",
    "PUBLISHABLE_KEY",
    "SESSION_ID",
    "SIGN_IN_URL",
    "SIGN_OUT_URL",
    "SUB",
    "URL",
    "USER_URL",
    "FakeSupabase",
    "b64",
    "cookie_for",
    "make_key",
    "unsigned",
]
