"""Sign in with the Leafout account: checking the access token the site mirrors into its ``.leafout.in`` cookie.

The Leafout site signs people in through Supabase Auth and writes the **access token** (never the refresh token) to the
``leafout-access`` cookie. `SupabaseVerifier` decides whether a token can start (or keep) an app session:

1. `claims` / `inspect` run the **local checks**, in this order, each with a reason code that goes to the log and never to
   the person: shape and size (``malformed``), the algorithm (``alg``: only ES256 and RS256, so ``none`` and ``HS*`` are
   refused before any key is looked up, which shuts the RS/HS confusion attack), the signing key by ``kid``
   (``kid_unknown``, ``upstream`` when the key set cannot be fetched), the signature (``signature``), ``iss``, ``aud``,
   ``role``, ``exp``, ``iat`` and the token's age (``too_old``), ``sub`` as a UUID and ``is_anonymous``.
2. `confirm` asks Supabase's ``/auth/v1/user`` whether the token's session is still alive (``revoked``, ``mismatch``,
   ``upstream``) and returns what the token does not carry reliably: whether the email is confirmed. It is the only
   network call besides the key set, and the caller counts it against the ``auth_bridge`` window first.

``verify`` modes: ``jwks`` checks the signature locally against the project's published keys; ``user_endpoint`` leaves the
signature to Supabase's own user call (the other claims are still checked as a filter); ``auto`` uses ``jwks`` when the
project publishes an ES256 or RS256 key and ``user_endpoint`` otherwise. The app never accepts an HS256 token and never
takes the project's JWT secret as a setting.

Nothing here logs a token, a ``sub``, an email or a phone number; every dataclass holding one uses ``repr=False``.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

import httpx2
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from tayyari.contract.auth import normalise_email
from tayyari.core.clock import Clock
from tayyari.core.logging import get_logger

from .jwks import HTTP_TIMEOUT_S, JwksCache, JwksError

log = get_logger(__name__)

TOKEN_MAX_CHARS: Final = 4096
ALGORITHMS: Final = ("ES256", "RS256")
AUDIENCE: Final = "authenticated"
ROLE: Final = "authenticated"
LEEWAY_S: Final = 30
NAME_MAX_CHARS: Final = 200
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_KEY_TYPES: Final = {"ES256": "EC", "RS256": "RSA"}


class SupabaseError(RuntimeError):
    """A token could not be accepted. ``reason`` is a short code for the log (never shown to anyone)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SupabaseClaims:
    """What the token itself says, once its checks passed."""

    sub: str = field(repr=False)
    session_id: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class Inspected:
    """A token that is genuine (signature and claims) but possibly out of date: ``stale`` is ``exp`` or ``too_old``."""

    claims: SupabaseClaims
    stale: str | None = None


@dataclass(frozen=True, slots=True)
class SupabaseIdentity:
    """A person Supabase vouches for right now. ``email`` is set only when Supabase has confirmed it."""

    sub: str = field(repr=False)
    session_id: str | None = field(repr=False)
    email: str | None = field(repr=False)
    name: str | None = field(repr=False)

    @property
    def email_verified(self) -> bool:
        return self.email is not None


def _unb64(part: str) -> bytes:
    if _BASE64URL.fullmatch(part) is None:
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _number(value: object) -> float | None:
    """``value`` as a number, or None (a bool is not one)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _clean_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    kept = "".join(ch for ch in value if ch.isprintable() or ch.isspace())
    cleaned = " ".join(kept.split())[:NAME_MAX_CHARS]
    return cleaned or None


class SupabaseVerifier:
    def __init__(
        self,
        *,
        url: str,
        publishable_key: str,
        mode: str,
        max_token_age_s: int,
        http: httpx2.AsyncClient,
        clock: Clock,
    ) -> None:
        self._issuer = f"{url.rstrip('/')}/auth/v1"
        self._publishable_key = publishable_key
        self._configured_mode = mode
        self._max_age = max_token_age_s
        self._http = http
        self._clock = clock
        self._jwks = JwksCache(
            f"{self._issuer}/.well-known/jwks.json", http=http, clock=clock, on_fetch=self._log_keys
        )
        self._logged_mode: str | None = None

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def mode(self) -> str:
        """The configured mode (``auto`` is resolved per token against the project's published keys)."""
        return self._configured_mode

    @staticmethod
    def _log_keys(keys: KeySet) -> None:
        log.info(
            "supabase_jwks_refreshed", key_count=len(keys.keys), kids=[str(k.kid) for k in keys.keys][:8]
        )

    # ───────────────────────── local checks ─────────────────────────

    async def claims(self, token: str) -> SupabaseClaims:
        """The checked claims of a token that is genuine **and** current. Raises `SupabaseError`."""
        found = await self.inspect(token)
        if found.stale is not None:
            raise SupabaseError(found.stale)
        return found.claims

    async def inspect(self, token: str) -> Inspected:
        """Every check, except that ``exp`` and the age limit are reported as ``stale`` instead of refused.

        That is the identity check: a signature-valid token whose time has run out still says who signed in on the site.
        Raises `SupabaseError` for everything else.
        """
        header, payload = self._parse(token)
        alg = header.get("alg")
        if alg not in ALGORITHMS:
            raise SupabaseError("alg")
        if await self._use_jwks():
            await self._check_signature(token, header, str(alg))
        stale = self._check_claims(payload)
        session_id = payload.get("session_id")
        sid = (
            session_id.lower()
            if isinstance(session_id, str) and _UUID.fullmatch(session_id.lower())
            else None
        )
        return Inspected(SupabaseClaims(sub=str(payload["sub"]).lower(), session_id=sid), stale)

    def _parse(self, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not token or len(token) > TOKEN_MAX_CHARS:
            raise SupabaseError("malformed")
        parts = token.split(".")
        if len(parts) != 3 or not all(parts):
            raise SupabaseError("malformed")
        try:
            header = json.loads(_unb64(parts[0]))
            payload = json.loads(_unb64(parts[1]))
            _unb64(parts[2])
        except (ValueError, UnicodeError):
            raise SupabaseError("malformed") from None
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise SupabaseError("malformed")
        return header, payload

    async def _use_jwks(self) -> bool:
        mode = self._configured_mode
        if mode == "jwks":
            return True
        if mode == "user_endpoint":
            return False
        try:
            keys = await self._jwks.keys()
        except JwksError:
            raise SupabaseError("upstream") from None
        usable = any(self._usable(key, alg) for key in keys.keys for alg in ALGORITHMS)
        chosen = "jwks" if usable else "user_endpoint"
        if self._logged_mode != chosen:
            self._logged_mode = chosen
            log.info("supabase_verify_mode", configured="auto", chosen=chosen)
        return usable

    @staticmethod
    def _usable(key: Any, alg: str) -> bool:
        """A published key can verify ``alg``: its type fits and its own ``alg`` (when it names one) is the same."""
        declared = key.get("alg")
        return bool(key.key_type == _KEY_TYPES[alg] and (declared is None or declared == alg))

    async def _find_key(self, kid: str, alg: str) -> Any | None:
        for refresh in (False, True):
            try:
                keys = await self._jwks.keys(refresh=refresh)
            except JwksError:
                raise SupabaseError("upstream") from None
            for key in keys.keys:
                if key.kid == kid and self._usable(key, alg):
                    return key
        return None

    async def _check_signature(self, token: str, header: dict[str, Any], alg: str) -> None:
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise SupabaseError("kid_unknown")
        key = await self._find_key(kid, alg)
        if key is None:
            raise SupabaseError("kid_unknown")
        try:
            jwt.decode(token, key, algorithms=[alg])
        except (JoseError, ValueError, TypeError):
            raise SupabaseError("signature") from None

    def _check_claims(self, payload: dict[str, Any]) -> str | None:
        """Checks 5 to 10. Returns ``exp`` or ``too_old`` when only the token's time has run out, else None."""
        now = self._clock.now().timestamp()
        if payload.get("iss") != self._issuer:
            raise SupabaseError("iss")
        aud = payload.get("aud")
        if not (aud == AUDIENCE or (isinstance(aud, list) and AUDIENCE in aud)):
            raise SupabaseError("aud")
        if payload.get("role") != ROLE:
            raise SupabaseError("role")
        exp = _number(payload.get("exp"))
        if exp is None:
            raise SupabaseError("exp")
        iat = _number(payload.get("iat"))
        if iat is None or iat > now + LEEWAY_S:
            raise SupabaseError("iat")
        sub = payload.get("sub")
        if not isinstance(sub, str) or _UUID.fullmatch(sub.lower()) is None:
            raise SupabaseError("sub")
        if payload.get("is_anonymous") is True:
            raise SupabaseError("anonymous")
        if exp <= now - LEEWAY_S:
            return "exp"
        if now - iat > self._max_age:
            return "too_old"
        return None

    # ───────────────────────── Supabase's own answer ─────────────────────────

    async def confirm(self, token: str, claims: SupabaseClaims) -> SupabaseIdentity:
        """Ask Supabase whether the token's session is alive and read what it says about the person.

        Raises `SupabaseError` (``revoked``, ``mismatch``, ``anonymous``, ``upstream``).
        """
        try:
            response = await self._http.get(
                f"{self._issuer}/user",
                headers={"Authorization": f"Bearer {token}", "apikey": self._publishable_key},
                timeout=HTTP_TIMEOUT_S,
            )
        except httpx2.HTTPError:
            raise SupabaseError("upstream") from None
        if response.status_code in (401, 403, 404):
            raise SupabaseError("revoked")
        if response.status_code != 200:
            raise SupabaseError("upstream")
        try:
            body = response.json()
        except ValueError:
            raise SupabaseError("upstream") from None
        if not isinstance(body, dict):
            raise SupabaseError("upstream")
        found = body.get("id")
        if not isinstance(found, str) or found.lower() != claims.sub:
            raise SupabaseError("mismatch")
        if body.get("is_anonymous") is True:
            raise SupabaseError("anonymous")
        email: str | None = None
        raw_email = body.get("email")
        if isinstance(raw_email, str) and body.get("email_confirmed_at"):
            email = normalise_email(raw_email)
        meta = body.get("user_metadata")
        name = None
        if isinstance(meta, dict):
            name = _clean_name(meta.get("full_name")) or _clean_name(meta.get("name"))
        return SupabaseIdentity(sub=claims.sub, session_id=claims.session_id, email=email, name=name)


__all__ = [
    "ALGORITHMS",
    "Inspected",
    "SupabaseClaims",
    "SupabaseError",
    "SupabaseIdentity",
    "SupabaseVerifier",
]
