"""Accounts: sign in with Google or an emailed link, server-side sessions behind an HttpOnly cookie.

    service    `AuthService`: composes the parts below and owns the decisions (the routes own HTTP)
    google     OAuth 2.0 authorization code + PKCE through authlib's httpx client, the ID token check against
               Google's JWKS (joserfc), the signed OAuth-state cookie, `safe_next`
    magic      emailed one-time links: issue, limits, verify
    sessions   the cookie token, the stored session (sliding), the cookie flags
    email      `EmailSender` and the Resend implementation

Sits between `contract` and `api`: it reads storage only through the Protocols in `storage.ports`. The container
builds one `AuthService` per process; `api.deps.get_current_user` and the `/api/auth/*` and `/api/me` routes use it.
"""

from tayyari.auth.service import AuthService, GoogleStart, SignedIn
from tayyari.auth.sessions import SessionContext

__all__ = ["AuthService", "GoogleStart", "SessionContext", "SignedIn"]
