"""Emailed sign-in links.

A link carries a random 32-byte token. Only its sha256 is stored (``magic_links.token_hash``), it works once
(`UserRepo.consume_magic_link` marks it used in the same statement that finds it, so two clicks never both win)
and for ``magic_link_minutes``. Asking for one never says whether the address has an account, and `request` never
raises for a limit: it answers "nothing sent" and the route still replies 202.

Limits: ``magic_per_email`` and ``magic_per_ip`` links per 15 minutes, counted in the shared ``rate_limit_hits``
table through `LimitRepo.hit` (a fixed window, like the rest of the guard). The email is keyed by an HMAC, so the
table never holds a reversible hash of anybody's address.

Delivery: through the configured `EmailSender` (Resend). A local server with no email key sends nothing: it logs
the link at INFO and returns it to the caller, who puts it in the 202 body. That shortcut needs ``env == "local"``
AND no ``RESEND_API_KEY``, so a deployment that has a key can never hand a link to the person who asked for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final
from urllib.parse import quote

from tayyari.core.clock import Clock
from tayyari.core.hashing import hmac_hex
from tayyari.core.logging import get_logger
from tayyari.core.settings import Settings
from tayyari.storage.ports import LimitRepo, StoredMagicLink, StoredUser, UserRepo

from .email import EmailError, EmailSender, sign_in_message
from .sessions import TOKEN_MAX_CHARS, hash_token, new_token

log = get_logger(__name__)

LIMIT_WINDOW_S: Final = 15 * 60
VERIFY_PATH: Final = "/api/auth/magic/verify"


@dataclass(frozen=True, slots=True)
class MagicOutcome:
    """What `MagicLinks.request` did. ``dev_link`` is set only on a local server with no email key."""

    issued: bool
    dev_link: str | None = field(default=None, repr=False)


class MagicLinks:
    def __init__(
        self,
        *,
        settings: Settings,
        users: UserRepo,
        limits: LimitRepo,
        clock: Clock,
        key: bytes,
        mailer: EmailSender | None,
    ) -> None:
        self._settings = settings
        self._users = users
        self._limits = limits
        self._clock = clock
        self._key = key
        self._mailer = mailer

    @property
    def dev_mode(self) -> bool:
        """True when links are logged and returned instead of emailed: a local server with no email key."""
        return (
            self._settings.env == "local" and self._settings.resend_api_key is None and self._mailer is None
        )

    @property
    def enabled(self) -> bool:
        """Whether signing in by email can work here at all (the sign-in sheet hides the form otherwise)."""
        return self._mailer is not None or self.dev_mode

    def link_for(self, token: str) -> str:
        return f"{self._settings.auth.public_url}{VERIFY_PATH}?token={quote(token, safe='')}"

    async def _within_limits(self, email: str, ip_hash: str | None) -> bool:
        auth = self._settings.auth
        now = self._clock.now()
        email_key = hmac_hex(self._key, f"magic-email:{email}", 32)
        # Both counters are always advanced, so a client that keeps asking stays over its limit for the window.
        by_email = await self._limits.hit(f"auth:magic:email:{email_key}", LIMIT_WINDOW_S, now)
        by_ip = await self._limits.hit(f"auth:magic:ip:{ip_hash or 'unknown'}", LIMIT_WINDOW_S, now)
        if auth.magic_per_email > 0 and by_email > auth.magic_per_email:
            log.info("magic_link_limited", by="email", count=by_email)
            return False
        if auth.magic_per_ip > 0 and by_ip > auth.magic_per_ip:
            log.info("magic_link_limited", by="ip", count=by_ip, ip_hash=ip_hash)
            return False
        return True

    async def request(self, email: str, ip_hash: str | None) -> MagicOutcome:
        """Issue and deliver a link for ``email`` (already normalised), unless a limit or the setup says no."""
        if not self.enabled:
            log.warning("magic_link_unavailable", reason="no email provider configured")
            return MagicOutcome(issued=False)
        if not await self._within_limits(email, ip_hash):
            return MagicOutcome(issued=False)

        now = self._clock.now()
        token = new_token()
        minutes = self._settings.auth.magic_link_minutes
        await self._users.add_magic_link(
            StoredMagicLink(
                token_hash=hash_token(token),
                email=email,
                created_at=now,
                expires_at=now + timedelta(minutes=minutes),
                used_at=None,
                ip_hash=ip_hash,
            )
        )
        link = self.link_for(token)
        if self._mailer is not None:
            try:
                await self._mailer.send(sign_in_message(email, link, minutes))
            except EmailError as err:
                # The person is told nothing different (no enumeration); the failure is ours to fix.
                log.warning("magic_link_send_failed", reason=str(err))
                return MagicOutcome(issued=True)
            log.info("magic_link_sent")
            return MagicOutcome(issued=True)
        log.info("magic_link_dev", link=link)
        return MagicOutcome(issued=True, dev_link=link)

    async def verify(self, token: str) -> StoredUser | None:
        """Spend a link: the account it signs in (created on first use), or None when it is unknown, used or old."""
        if not token or len(token) > TOKEN_MAX_CHARS:
            return None
        now = self._clock.now()
        link = await self._users.consume_magic_link(hash_token(token), now)
        if link is None:
            return None
        return await self._users.sign_in_email(email=link.email, now=now)


__all__ = ["LIMIT_WINDOW_S", "VERIFY_PATH", "MagicLinks", "MagicOutcome"]
