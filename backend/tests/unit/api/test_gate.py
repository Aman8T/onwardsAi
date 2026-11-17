"""`api.gate`: which requests are public, and how the router's path is read.

The full app (every registered route, a real sign-in) is in ``tests/integration/test_sign_in_gate.py``.
"""

from __future__ import annotations

import pytest

from tayyari.api.gate import is_public, route_path

PUBLIC = [
    ("GET", "/api/health"),
    ("HEAD", "/api/health"),
    ("GET", "/api/health/deep"),
    ("GET", "/api/me"),
    ("GET", "/api/auth/google/start"),
    ("GET", "/api/auth/google/callback"),
    ("POST", "/api/auth/magic"),
    ("GET", "/api/auth/magic/verify"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/billing/webhooks/razorpay"),
    ("POST", "/api/billing/webhooks/stripe"),
    ("GET", "/api/billing/entitlements"),
    ("GET", "/api/p/abc123"),
    ("GET", "/api/weekly/unsubscribe"),
    ("POST", "/api/weekly/unsubscribe"),
    ("OPTIONS", "/api/analyze"),
    ("OPTIONS", "/api/anything/at/all"),
]
PRIVATE = [
    ("POST", "/api/health"),
    ("PATCH", "/api/me"),
    ("DELETE", "/api/me"),
    ("POST", "/api/me/claim"),
    ("POST", "/api/analyze"),
    ("POST", "/api/chat"),
    ("GET", "/api/sample-profile"),
    ("GET", "/api/profile/abc"),
    ("POST", "/api/billing/checkout"),
    ("POST", "/api/billing/verify"),
    ("GET", "/api/billing/webhooks/razorpay"),
    ("POST", "/api/billing/entitlements"),
    ("GET", "/api/portfolio/abc"),
    ("PATCH", "/api/p/abc"),
    ("GET", "/api/p"),
    ("GET", "/api/p/"),
    ("GET", "/api/p/abc/extra"),
    ("GET", "/api/weekly/subscription"),
    ("PUT", "/api/weekly/subscription"),
    ("POST", "/api/handoff"),
    ("GET", "/api/handoff/abc"),
    ("GET", "/api/card"),
    ("GET", "/api/auth"),  # the folder itself is not a route
    ("GET", "/api/auth/../jobsearch/latest"),  # dot segments never borrow a public prefix
    ("GET", "/api/p/../me/claim"),
    ("GET", "/api/me/"),
    ("GET", "/api"),
    ("GET", "/api/no-such-route"),
]


@pytest.mark.parametrize(("method", "path"), PUBLIC)
def test_public_requests(method: str, path: str) -> None:
    assert is_public(method, path) is True


@pytest.mark.parametrize(("method", "path"), PRIVATE)
def test_everything_else_is_private(method: str, path: str) -> None:
    assert is_public(method, path) is False


@pytest.mark.parametrize(
    ("path", "root", "expected"),
    [
        ("/api/me", "", "/api/me"),
        ("/tayyari/api/me", "/tayyari", "/api/me"),
        ("/tayyari", "/tayyari", ""),
        ("/tayyarix/api", "/tayyari", "/tayyarix/api"),
        ("/other/api/me", "/tayyari", "/other/api/me"),
    ],
)
def test_route_path_strips_the_root_path_like_starlette(path: str, root: str, expected: str) -> None:
    assert route_path({"path": path, "root_path": root}) == expected
