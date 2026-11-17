"""The sign-in gate through the real app: with it on, every ``/api/*`` route but a short public list answers 401 to a visitor
with no sign-in, the public ones stay reachable, a signed-in person passes, and nothing outside ``/api/`` changes. With it off
the app behaves as it always did.

The routes are enumerated from ``app.routes``, so a route added tomorrow is covered the day it exists: it must either answer 401
signed out or be put on `PUBLIC` here on purpose. The rig is the one of `test_auth_routes` (the app's own emailed-link sign-in,
switched on for these tests, stands in for the Leafout cookie)."""

# ruff: noqa: F401, F811  (``start`` is that module's fixture: imported so pytest finds it, then taken by name)

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
import structlog

from tayyari.api.gate import is_public
from tests.integration.test_auth_routes import as_user, google_key, sign_in_by_link, start

ON = {"auth": {"require_sign_in": True}}
SIGN_IN_MESSAGE = "Sign in to continue."

# (method, path template) of every route that is reachable without a sign-in. Written out here on purpose: the gate's own table
# is not the oracle for its tests.
PUBLIC: frozenset[tuple[str, str]] = frozenset(
    {
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
        ("GET", "/api/p/{public_id}"),
        ("GET", "/api/weekly/unsubscribe"),
        ("POST", "/api/weekly/unsubscribe"),
    }
)
PARAM = re.compile(r"\{[^}]*\}")


def _walk(routes: Any, prefix: str = "") -> Any:
    """Every ``(path, methods)`` under ``routes``. FastAPI includes a router lazily (an included-router entry that holds the
    original router and its prefix), so a plain ``app.routes`` loop would see only the catch-alls."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes, prefix + (getattr(route.include_context, "prefix", "") or ""))
        elif getattr(route, "path", None) is not None:
            yield prefix + route.path, route.methods or ()


def api_endpoints(app: Any) -> list[tuple[str, str]]:
    """``(method, template)`` for every method of every route registered under /api (the catch-alls included; OPTIONS left out,
    because the gate treats a preflight as public)."""
    found: list[tuple[str, str]] = []
    for path, methods in _walk(app.routes):
        if path == "/api" or path.startswith("/api/"):
            found.extend((method, path) for method in sorted(methods) if method != "OPTIONS")
    return found


def concrete(template: str) -> str:
    return PARAM.sub("x", template)


def says_sign_in(response: httpx.Response) -> bool:
    """The gate's answer: 401 with the sign-in sentence (a HEAD answer carries no body)."""
    if response.status_code != 401:
        return False
    if response.request.method == "HEAD":
        return True
    return bool(response.json() == {"error": SIGN_IN_MESSAGE})


# ───────────────────────── the gate on ─────────────────────────


async def test_every_api_route_that_is_not_public_answers_401_signed_out(start: Any) -> None:
    async with start(**ON) as rig:
        gated = [(m, p) for m, p in api_endpoints(rig.env.app) if (m, p) not in PUBLIC]
        assert len(gated) > 40  # the enumeration found the app's routes, not an empty list
        failures = []
        for method, template in gated:
            response = await rig.client.request(method, concrete(template))
            if not says_sign_in(response):
                failures.append(f"{method} {template} -> {response.status_code}")
        assert failures == []


async def test_the_public_list_names_real_routes_and_the_gate_agrees_with_it(start: Any) -> None:
    async with start(**ON) as rig:
        registered = set(api_endpoints(rig.env.app))
    # HEAD is the gate's courtesy to a GET (a probe), not a registered method of every route.
    assert {entry for entry in PUBLIC if entry[0] != "HEAD"} <= registered
    for method, template in sorted(PUBLIC):
        assert is_public(method, concrete(template)), (method, template)
    for method, template in sorted(registered - PUBLIC):
        if not template.startswith("/api/auth/"):
            assert not is_public(method, concrete(template)), (method, template)


async def test_each_public_route_is_reachable_signed_out(start: Any) -> None:
    async with start(**ON) as rig:
        statuses: dict[tuple[str, str], int] = {}
        for method, template in sorted(PUBLIC):
            response = await rig.client.request(method, concrete(template))
            statuses[(method, template)] = response.status_code
            # Whatever the route answers (a 400 for an empty body, a 404 for an unknown id), the gate did not answer for it.
            assert not says_sign_in(response), (method, template)
        assert statuses[("GET", "/api/health")] == 200
        assert statuses[("HEAD", "/api/health")] == 200
        assert statuses[("GET", "/api/me")] == 200
        assert statuses[("GET", "/api/billing/entitlements")] == 200
        assert statuses[("GET", "/api/p/{public_id}")] == 404  # an unknown id: the route's own answer


async def test_me_tells_a_visitor_the_gate_is_on_and_matching_is_never_anonymous(start: Any) -> None:
    async with start(match={"allow_anonymous": "true"}, **ON) as rig:
        body = (await rig.client.get("/api/me")).json()
        assert body["signInRequired"] is True
        assert body["anonymousMatch"] is False
        assert rig.env.container.match_anonymous is False
        assert "user" not in body


async def test_a_signed_in_person_passes_and_the_lookup_is_done_once(
    start: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with start(**ON) as rig:
        token = await sign_in_by_link(rig)
        sessions = rig.env.container.auth.sessions
        calls: list[str | None] = []
        real = sessions.resolve

        async def counting(value: str | None) -> Any:
            calls.append(value)
            return await real(value)

        monkeypatch.setattr(sessions, "resolve", counting)
        card = await rig.client.get("/api/weekly/subscription", headers=as_user(token))
        assert card.status_code == 200, card.text
        assert len(calls) == 1  # the gate's lookup was kept for the route
        me = (await rig.client.get("/api/me", headers=as_user(token))).json()
        assert me["signInRequired"] is True and me["user"]["email"] == "ana@example.com"
        profile = await rig.client.get("/api/profile/" + "a" * 32, headers=as_user(token))
        assert not says_sign_in(profile)


async def test_a_cookie_that_names_no_live_sign_in_is_a_visitor(start: Any) -> None:
    async with start(**ON) as rig:
        for value in ("garbage", "x" * 5000):
            response = await rig.client.get("/api/card", headers=as_user(value))
            assert says_sign_in(response)


async def test_a_refusal_carries_the_request_id_and_the_security_headers(start: Any) -> None:
    async with start(**ON) as rig:
        response = await rig.client.get("/api/card", headers={"x-request-id": "gate-test-1"})
    assert says_sign_in(response)
    assert response.headers["x-request-id"] == "gate-test-1"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert "set-cookie" not in response.headers


async def test_a_cross_site_write_is_still_a_403_before_the_gate(start: Any) -> None:
    async with start(**ON) as rig:
        response = await rig.client.post("/api/analyze", headers={"sec-fetch-site": "cross-site"})
    assert response.status_code == 403


async def test_a_preflight_passes_the_gate(start: Any) -> None:
    async with start(**ON) as rig:
        response = await rig.client.options("/api/card")
    assert not says_sign_in(response)


async def test_a_refusal_is_logged_without_the_path_or_the_cookie(start: Any) -> None:
    async with start(**ON) as rig:
        with structlog.testing.capture_logs() as logs:
            await rig.client.get("/api/handoff/secret-handoff-id", headers={"cookie": "tayyari_session=abc"})
    refused = [
        entry for entry in logs if entry.get("event") == "app_error" and entry.get("gate") == "sign_in"
    ]
    assert len(refused) == 1
    assert refused[0]["status"] == 401 and refused[0]["code"] == "unauthorized"
    assert "secret-handoff-id" not in repr(logs)
    assert "abc" not in repr(refused)


async def test_nothing_outside_the_api_is_gated(start: Any) -> None:
    async with start(**ON) as rig:
        for path in (
            "/",
            "/how-it-works",
            "/pricing",
            "/about",
            "/robots.txt",
            "/sitemap.xml",
            "/u/abcdefghijkl",
            "/r/abcdefghijkl.svg",
            "/og/abcdefghijkl-1.png",
            "/match",
            "/p/someid",
            "/no-such-page",
        ):
            response = await rig.client.get(path)
            assert response.status_code != 401, path


# ───────────────────────── the gate off ─────────────────────────


async def test_with_the_gate_off_nothing_changes(start: Any) -> None:
    async with start(auth={"require_sign_in": False}) as rig:
        # A route that wants no account answers a visitor; one that wants an account says so itself.
        assert (await rig.client.get("/api/profile/" + "a" * 32)).status_code == 404
        assert (await rig.client.get("/api/weekly/subscription")).status_code == 401
        assert (await rig.client.get("/api/me")).json()["signInRequired"] is False


async def test_without_the_setting_the_environment_decides(start: Any, build_settings: Any) -> None:
    assert build_settings(env="test").sign_in_required is False
    assert build_settings(env="local").sign_in_required is False
    assert build_settings(env="prod").sign_in_required is True
    async with start(match={"allow_anonymous": "true"}) as rig:
        assert (
            rig.env.container.match_anonymous is True
        )  # the gate is off here: the match setting decides, as before
        assert (await rig.client.get("/api/me")).json()["anonymousMatch"] is True
