"""A cache of a provider's public signing keys (a JWKS), shared by Google's ID-token check and the Leafout token check.

The key set is fetched through the auth service's ``httpx2`` client (so one optional transport reaches every outgoing
call, which is how the tests run without a network) and kept for `JWKS_TTL_S`. A token signed with a key that is not in
the cached set asks for one refetch (`JwksCache.keys(refresh=True)`), but never more often than `JWKS_REFETCH_MIN_S`, so
garbage tokens cannot make the app hammer the provider. A fetch that fails raises `JwksError`; the old set is kept, and
the next call tries again.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final, cast

import httpx2
from joserfc.jwk import KeySet

from tayyari.core.clock import Clock

JWKS_TTL_S: Final = 6 * 3600
JWKS_REFETCH_MIN_S: Final = 60
"""An unknown signing key triggers a refetch of the provider's keys, but not more often than this."""
HTTP_TIMEOUT_S: Final = 10.0
MAX_KEYS: Final = 32
"""A key set larger than this is not a real provider's; it is refused."""


class JwksError(RuntimeError):
    """The key set could not be fetched or read. ``reason`` is a short code for the log (never shown to anyone)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JwksCache:
    def __init__(
        self,
        url: str,
        *,
        http: httpx2.AsyncClient,
        clock: Clock,
        on_fetch: Callable[[KeySet], None] | None = None,
    ) -> None:
        self._url = url
        self._http = http
        self._clock = clock
        self._on_fetch = on_fetch
        self._keys: KeySet | None = None
        self._keys_at = 0.0

    @property
    def url(self) -> str:
        return self._url

    async def keys(self, *, refresh: bool = False) -> KeySet:
        """The cached key set; ``refresh=True`` looks again (at most once a minute) because a key was not found."""
        age = self._clock.monotonic() - self._keys_at
        if self._keys is not None and not refresh and age < JWKS_TTL_S:
            return self._keys
        if self._keys is not None and refresh and age < JWKS_REFETCH_MIN_S:
            return self._keys
        try:
            response = await self._http.get(self._url, timeout=HTTP_TIMEOUT_S)
            response.raise_for_status()
            body = response.json()
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("keys"), list)
                or len(body["keys"]) > MAX_KEYS
            ):
                raise ValueError("not a key set")
            keys = KeySet.import_key_set(cast(Any, body)) if body["keys"] else KeySet([])
        except Exception as err:
            raise JwksError(f"jwks_unavailable:{type(err).__name__}") from None
        self._keys, self._keys_at = keys, self._clock.monotonic()
        if self._on_fetch is not None:
            self._on_fetch(keys)
        return keys


__all__ = ["HTTP_TIMEOUT_S", "JWKS_REFETCH_MIN_S", "JWKS_TTL_S", "JwksCache", "JwksError"]
