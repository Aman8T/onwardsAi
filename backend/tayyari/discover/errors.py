"""Errors of the Discover service."""

from __future__ import annotations

from tayyari.core.errors import AppError


class DiscoverDisabled(AppError):
    """Discover is switched off (``TAYYARI_DISCOVER__ENABLED=false``): 503, body ``{"error": "discover_disabled"}``
    (a code the web app reads, like the 402 of the posting match)."""

    status = 503
    code = "discover_disabled"
    user_message = "Discover is switched off on this server."

    def to_body(self) -> dict[str, str]:
        return {"error": self.code}


class RegistryError(ValueError):
    """The employer registry is missing or does not follow the format; the message lists what is wrong."""


class BoardError(RuntimeError):
    """One employer's board could not be read. The message is safe to log and to store (no response body)."""


__all__ = ["BoardError", "DiscoverDisabled", "RegistryError"]
