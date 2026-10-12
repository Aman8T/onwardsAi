"""Fakes for the sign-in tests: a mailer that records, and a stand-in network for Google and Resend.

FakeMailer      an `EmailSender` that keeps what it was asked to send (``sent``) or fails on demand
FakeNetwork     the transport behind every outgoing sign-in call (`ContainerOverrides.auth_transport`): a request
                nobody registered a handler for fails the test, so nothing can reach a real server
FakeGoogle      Google's side of the flow: an RSA key, ID tokens signed like Google's, the token endpoint and the
                key set, installed on a `FakeNetwork`
FakeResend      the Resend endpoint, recording what was posted
link_in(msg)    the sign-in link inside a recorded email
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl

import httpx2
from joserfc import jwt
from joserfc.jwk import RSAKey

from tayyari.auth.email import RESEND_URL, EmailError, EmailMessage
from tayyari.auth.google import ISSUERS, JWKS_URL, TOKEN_URL

CLIENT_ID = "test-client.apps.example"
CLIENT_SECRET = "test-client-secret"  # a made-up value for fakes, not a real credential

Handler = Callable[[httpx2.Request], httpx2.Response]


@dataclass
class FakeMailer:
    """Records every message; ``fail_with`` makes the sends raise `EmailError` (the provider is down)."""

    sent: list[EmailMessage] = field(default_factory=list)
    fail_with: str | None = None

    async def send(self, message: EmailMessage) -> None:
        if self.fail_with is not None:
            raise EmailError(self.fail_with)
        self.sent.append(message)


def link_in(message: EmailMessage) -> str:
    """The sign-in URL in an email's plain-text part."""
    found = re.search(r"https?://\S+", message.text)
    assert found, "no link in the email"
    return found.group(0)


@dataclass
class FakeNetwork:
    """Every outgoing request lands here. Register a handler per ``METHOD url`` (query string ignored)."""

    routes: dict[tuple[str, str], Handler] = field(default_factory=dict)
    seen: list[httpx2.Request] = field(default_factory=list)

    def on(self, method: str, url: str, handler: Handler) -> None:
        self.routes[(method.upper(), url)] = handler

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        url = str(request.url.copy_with(query=None, fragment=None))
        handler = self.routes.get((request.method, url))
        assert handler is not None, f"unexpected network call: {request.method} {url}"
        return handler(request)

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def calls_to(self, url: str) -> list[httpx2.Request]:
        return [r for r in self.seen if str(r.url.copy_with(query=None, fragment=None)) == url]


def new_key(kid: str = "key-1") -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig", "alg": "RS256"})


@dataclass
class FakeGoogle:
    """Google's side of the flow. Build with the key it should sign with, then ``install`` it on a `FakeNetwork`."""

    key: RSAKey
    client_id: str = CLIENT_ID
    id_token: str | None = None
    """What the token endpoint hands back as ``id_token`` (set by `token_for`)."""
    token_status: int = 200
    token_requests: list[dict[str, str]] = field(default_factory=list)

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.key.as_dict(private=False)]}

    def mint(self, now: datetime, **overrides: Any) -> str:
        """An ID token as Google signs them; ``overrides`` replace (or, set to None, remove) claims."""
        stamp = int(now.timestamp())
        claims: dict[str, Any] = {
            "iss": ISSUERS[0],
            "aud": self.client_id,
            "sub": "1100110011",
            "email": "ana@example.com",
            "email_verified": True,
            "name": "Ana Rao",
            "picture": "https://lh3.googleusercontent.com/a/photo",
            "locale": "en-GB",
            "iat": stamp,
            "exp": stamp + 3600,
        }
        for name, value in overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        return jwt.encode({"alg": "RS256", "kid": self.key.kid}, claims, self.key, algorithms=["RS256"])

    def token_for(self, now: datetime, **overrides: Any) -> str:
        """Make the token endpoint answer with a freshly minted ID token."""
        self.id_token = self.mint(now, **overrides)
        return self.id_token

    def install(self, net: FakeNetwork) -> None:
        def token(request: httpx2.Request) -> httpx2.Response:
            self.token_requests.append(dict(parse_qsl(request.content.decode("utf-8"))))
            if self.token_status != 200:
                return httpx2.Response(self.token_status, json={"error": "invalid_grant"})
            body: dict[str, Any] = {
                "access_token": "ya29.fake",
                "token_type": "Bearer",
                "expires_in": 3599,
                "scope": "openid email profile",
            }
            if self.id_token is not None:
                body["id_token"] = self.id_token
            return httpx2.Response(200, json=body)

        net.on("POST", TOKEN_URL, token)
        net.on("GET", JWKS_URL, lambda _request: httpx2.Response(200, json=self.jwks()))


@dataclass
class FakeResend:
    """The Resend API: records each posted email and answers like Resend does."""

    status: int = 200
    posted: list[dict[str, Any]] = field(default_factory=list)
    headers: list[httpx2.Headers] = field(default_factory=list)

    def install(self, net: FakeNetwork) -> None:
        def send(request: httpx2.Request) -> httpx2.Response:
            self.posted.append(json.loads(request.content))
            self.headers.append(request.headers)
            if self.status >= 300:
                return httpx2.Response(self.status, json={"name": "validation_error", "message": "nope"})
            return httpx2.Response(200, json={"id": "email_123"})

        net.on("POST", RESEND_URL, send)


__all__ = [
    "CLIENT_ID",
    "CLIENT_SECRET",
    "FakeGoogle",
    "FakeMailer",
    "FakeNetwork",
    "FakeResend",
    "link_in",
    "new_key",
]
