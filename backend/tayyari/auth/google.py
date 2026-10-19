"""Sign in with Google: the OAuth 2.0 authorization-code flow with PKCE, and the OpenID Connect ID token check.

Why this is not authlib's Starlette ``OAuth`` wrapper: that one keeps the flow's state in a server-side session
cookie, which would need ``SessionMiddleware``. Here the state lives in one short-lived, signed cookie of our own,
and authlib's httpx client does only what it is good at (the authorization URL and the code exchange).

HTTP: authlib 1.8's client is built on ``httpx2`` (the successor of httpx, already a dependency of the Anthropic SDK),
so everything in `tayyari.auth` uses ``httpx2`` too. One optional ``transport`` reaches every outgoing call (the code
exchange, Google's keys, the email provider), which is how the tests run the whole flow without a network.

The flow, and what each step guards against:

1. ``start``: a random ``state`` (CSRF on the callback), a PKCE ``code_verifier`` (a stolen authorization code is
   useless without it) and a ``nonce`` (binds the ID token to this sign-in) are generated and put in the
   ``tayyari_oauth`` cookie together with the validated same-origin ``next`` path. The cookie value is
   ``base64url(json).base64url(HMAC-SHA256)`` under a key kept in the database (``kv["auth_state_key"]``), so every
   replica verifies it and a browser cannot forge or edit it. It is HttpOnly, SameSite=Lax (it must come back on
   the top-level navigation from Google), scoped to the callback path, and lives ten minutes.
2. ``callback``: the cookie is verified, ``state`` is compared in constant time, the code is exchanged with the
   verifier, and the ID token's signature (Google's JWKS, cached), issuer, audience, expiry and nonce are checked.
   Only a **verified** email is accepted: linking by email must not let an unverified claim take over an account.

`safe_next` is the only way a ``next`` value reaches a ``Location`` header: a path on this site, nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client  # type: ignore[import-untyped]
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger

from .jwks import HTTP_TIMEOUT_S, JWKS_REFETCH_MIN_S, JWKS_TTL_S, JwksCache, JwksError

log = get_logger(__name__)

AUTHORIZE_URL: Final = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL: Final = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a secret
JWKS_URL: Final = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS: Final = ("https://accounts.google.com", "accounts.google.com")
SCOPE: Final = "openid email profile"
CALLBACK_PATH: Final = "/api/auth/google/callback"
STATE_COOKIE: Final = "tayyari_oauth"
STATE_MAX_AGE_S: Final = 600
NEXT_MAX_CHARS: Final = 512
CLOCK_LEEWAY_S: Final = 60

STATE_KEY_NAME: Final = "auth_state_key"
"""The ``kv`` row holding the HMAC key that signs the state cookie."""


class GoogleError(RuntimeError):
    """The Google sign-in could not be completed. ``reason`` is a short code for the log (never shown to anyone)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """A verified Google account."""

    sub: str
    email: str
    name: str | None
    picture: str | None
    locale: str | None


@dataclass(frozen=True, slots=True)
class OAuthState:
    """What the state cookie carries between ``start`` and ``callback``."""

    state: str
    verifier: str
    nonce: str
    next_path: str
    issued_at: int


# ───────────────────────── next ─────────────────────────


def safe_next(raw: str | None) -> str:
    """``raw`` when it is a path on this site (``/`` or ``/something?x=1``), else ``/``.

    Refused: anything with a scheme or host (``https://evil``, ``//evil``, ``/\\evil``), control characters,
    backslashes, and anything over 512 characters. The result is safe to put in a ``Location`` header.
    """
    if not raw or len(raw) > NEXT_MAX_CHARS:
        return "/"
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return "/"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "/"
    if parts.scheme or parts.netloc:
        return "/"
    return raw


# ───────────────────────── the state cookie ─────────────────────────


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(key: bytes, payload: str) -> str:
    return _b64(hmac.new(key, b"oauth-state:" + payload.encode("ascii"), hashlib.sha256).digest())


def sign_state(key: bytes, state: OAuthState) -> str:
    """The cookie value for ``state``: ``<payload>.<signature>``."""
    body = json.dumps(
        {"s": state.state, "v": state.verifier, "n": state.nonce, "x": state.next_path, "t": state.issued_at},
        separators=(",", ":"),
    )
    payload = _b64(body.encode("utf-8"))
    return f"{payload}.{_mac(key, payload)}"


def read_state(key: bytes, value: str | None, *, now_ts: int) -> OAuthState | None:
    """The state a cookie value carries, or None when it is missing, forged, malformed or older than ten minutes."""
    if not value or len(value) > 2048 or value.count(".") != 1:
        return None
    payload, signature = value.split(".")
    try:
        if not hmac.compare_digest(_mac(key, payload), signature):
            return None
        data = json.loads(_unb64(payload))
        state = OAuthState(
            state=str(data["s"]),
            verifier=str(data["v"]),
            nonce=str(data["n"]),
            next_path=safe_next(str(data["x"])),
            issued_at=int(data["t"]),
        )
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None
    if not -CLOCK_LEEWAY_S <= now_ts - state.issued_at <= STATE_MAX_AGE_S:
        return None
    return state


def new_state(next_path: str, *, now_ts: int) -> OAuthState:
    """A fresh ``state``, PKCE verifier (64 urlsafe characters, within RFC 7636's 43 to 128) and ``nonce``."""
    return OAuthState(
        state=secrets.token_urlsafe(24),
        verifier=secrets.token_urlsafe(48),
        nonce=secrets.token_urlsafe(24),
        next_path=safe_next(next_path),
        issued_at=now_ts,
    )


# ───────────────────────── Google ─────────────────────────


class GoogleOAuth:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http: httpx2.AsyncClient,
        clock: Clock,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http
        self._clock = clock
        self._transport = transport
        self._jwks = JwksCache(JWKS_URL, http=http, clock=clock)

    def _oauth_client(self) -> Any:
        return AsyncOAuth2Client(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            scope=SCOPE,
            token_endpoint_auth_method="client_secret_post",  # noqa: S106 - a method name, not a secret
            code_challenge_method="S256",
            timeout=HTTP_TIMEOUT_S,
            transport=self._transport,
        )

    def authorization_url(self, state: OAuthState) -> str:
        """Where to send the browser: Google's consent page, with PKCE (S256) and the nonce."""
        client = self._oauth_client()
        url, _ = client.create_authorization_url(
            AUTHORIZE_URL,
            state=state.state,
            code_verifier=state.verifier,
            nonce=state.nonce,
            prompt="select_account",
        )
        return str(url)

    async def identity(self, code: str, state: OAuthState) -> GoogleIdentity:
        """Exchange the code and return the verified account. Raises `GoogleError`."""
        id_token = await self._exchange(code, state.verifier)
        claims = await self._verified_claims(id_token, state.nonce)
        email = claims.get("email")
        verified = claims.get("email_verified")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub or not isinstance(email, str) or not email:
            raise GoogleError("no_email")
        if verified is not True and verified != "true":
            raise GoogleError("email_not_verified")
        return GoogleIdentity(
            sub=sub,
            email=email.strip().lower(),
            name=_text(claims.get("name"), 200),
            picture=_https(claims.get("picture")),
            locale=_text(claims.get("locale"), 35),
        )

    async def _exchange(self, code: str, verifier: str) -> str:
        try:
            async with self._oauth_client() as client:
                token = await client.fetch_token(TOKEN_URL, code=code, code_verifier=verifier)
        except (
            Exception
        ) as err:  # authlib raises OAuthError, httpx errors, JSON errors: all mean "no sign-in"
            raise GoogleError(f"code_exchange_failed:{type(err).__name__}") from None
        id_token = token.get("id_token") if hasattr(token, "get") else None
        if not isinstance(id_token, str) or not id_token:
            raise GoogleError("no_id_token")
        return id_token

    async def _key_set(self, *, refresh: bool) -> KeySet:
        try:
            return await self._jwks.keys(refresh=refresh)
        except JwksError as err:
            raise GoogleError(err.reason) from None

    async def _verified_claims(self, id_token: str, nonce: str) -> dict[str, Any]:
        keys = await self._key_set(refresh=False)
        try:
            token = jwt.decode(id_token, keys, algorithms=["RS256"])
        except JoseError:
            # A key Google rotated in since we cached its set: look again once.
            keys = await self._key_set(refresh=True)
            try:
                token = jwt.decode(id_token, keys, algorithms=["RS256"])
            except JoseError as err:
                raise GoogleError(f"id_token_invalid:{type(err).__name__}") from None
        registry = jwt.JWTClaimsRegistry(
            now=int(self._clock.now().timestamp()),
            leeway=CLOCK_LEEWAY_S,
            iss={"essential": True, "values": list(ISSUERS)},
            aud={"essential": True, "value": self._client_id},
            sub={"essential": True},
            exp={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        try:
            registry.validate(token.claims)
        except JoseError as err:
            raise GoogleError(f"id_token_claims:{type(err).__name__}") from None
        return dict(token.claims)


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())[:limit]
    return cleaned or None


def _https(value: object) -> str | None:
    """A profile picture URL, kept only when it is https (it is stored and handed to the web app)."""
    if not isinstance(value, str) or len(value) > 1000:
        return None
    return value if value.startswith("https://") else None


__all__ = [
    "AUTHORIZE_URL",
    "CALLBACK_PATH",
    "ISSUERS",
    "JWKS_REFETCH_MIN_S",
    "JWKS_TTL_S",
    "JWKS_URL",
    "STATE_COOKIE",
    "STATE_KEY_NAME",
    "STATE_MAX_AGE_S",
    "TOKEN_URL",
    "GoogleError",
    "GoogleIdentity",
    "GoogleOAuth",
    "OAuthState",
    "new_state",
    "read_state",
    "safe_next",
    "sign_state",
]
