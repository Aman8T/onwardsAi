"""Accounts on the unlimited list (`TAYYARI_GUARD__UNLIMITED_EMAILS`) end to end through the real app: no rate limits on the
open routes (analysis, chat, portfolio, the light reads), `unlimited: true` where the web app looks, nobody exempt without a
live session or with an empty list, and the spend ceiling still applying. Every test sets limits so small that a second
request would be refused, so "a listed account got through" and "everyone else in the same setup did not" are both proven.

The posting match, search with AI, practice and speech have their own sections in their route tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tayyari.core.settings import GuardSettings, Settings
from tayyari.llm.errors import SPEND_CEILING_MESSAGE
from tayyari.storage.tables import RateLimitHitRow
from tests.fakes.claude import text_turn
from tests.integration.scenarios import NOTES, script_basic_analysis
from tests.integration.support import Env, RecordingClaude, roomy_guard, running
from tests.integration.test_portfolio_api import jev, scripted_claude

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
LISTED = "tester@example.com"
OTHER = "other@example.com"
UNLIMITED_LIST = f" {LISTED.upper()} , second.listed@example.org"
"""Written the way an owner might: padding, capitals, a second address."""

ONE_EACH = {
    "analyze_per_minute": 1,
    "analyze_per_hour": 1,
    "analyze_per_day": 1,
    "analyze_global_per_hour": 1,
    "chat_per_minute": 1,
    "chat_per_hour": 1,
    "chat_per_day": 1,
    "chat_global_per_hour": 1,
    "portfolio_per_minute": 1,
    "portfolio_per_hour": 1,
    "portfolio_per_day": 1,
    "portfolio_global_per_hour": 1,
}
"""One request per window, on every route these tests use."""


def guard(listed: str = UNLIMITED_LIST, **overrides: Any) -> GuardSettings:
    return roomy_guard(unlimited_emails=listed, **overrides)


async def sign_in(env: Env, email: str) -> dict[str, str]:
    """A signed-in account: the headers that carry its session cookie."""
    container = env.container
    user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
    token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
    return {"cookie": f"{SESSION}={token}"}


def scripted() -> RecordingClaude:
    claude = RecordingClaude()
    script_basic_analysis(claude)
    claude.script_chat(text_turn("Hi."), repeat=True)
    return claude


# ───────────────────────── analysis and chat ─────────────────────────


async def test_a_listed_account_analyzes_and_chats_past_every_limit_and_others_do_not(
    build_settings: MakeSettings,
) -> None:
    settings = build_settings(guard=guard(**ONE_EACH))
    async with running(settings, claude=scripted()) as env:
        listed, other = await sign_in(env, LISTED), await sign_in(env, OTHER)
        analyses = [(await env.analyze(notes=NOTES, headers=listed)).status_code for _ in range(4)]
        chats = [(await env.chat(headers=listed)).status_code for _ in range(5)]
        assert analyses == [200] * 4 and chats == [200] * 5
        assert (
            await env.rows(RateLimitHitRow) == []
        )  # not counted for the address, the account or the whole service

        # the same setup, the same address: the whole service had nothing counted, so one more is welcome...
        assert (await env.analyze(notes=NOTES, headers=other)).status_code == 200
        assert (await env.chat(headers=other)).status_code == 200
        # ...and then everyone else is limited as before: per address, and with no account at all
        for refused in (
            await env.analyze(notes=NOTES, headers=other),
            await env.analyze(notes=NOTES),
            await env.chat(headers=other),
            await env.chat(),
        ):
            assert refused.status_code == 429, refused.text
            assert int(refused.headers["retry-after"]) >= 1


async def test_hammering_is_shed_for_everyone_but_a_listed_account(build_settings: MakeSettings) -> None:
    settings = build_settings(guard=guard(analyze_per_minute=1))
    async with running(settings, claude=scripted()) as env:
        listed = await sign_in(env, LISTED)
        # no files: refused with a 400 once it has got past the in-memory shed, a 429 where that turns it away
        quick = [(await env.client.post("/api/analyze", headers=listed)).status_code for _ in range(8)]
        anonymous = [(await env.client.post("/api/analyze")).status_code for _ in range(8)]
    assert 429 not in quick
    assert 429 in anonymous and anonymous[0] != 429


# ───────────────────────── portfolio ─────────────────────────


async def test_a_listed_account_generates_and_rewrites_portfolios_past_the_limits(
    build_settings: MakeSettings,
) -> None:
    settings = build_settings(guard=guard(**ONE_EACH))
    async with running(settings, claude=scripted_claude(), jev=jev()) as env:
        listed, other = await sign_in(env, LISTED), await sign_in(env, OTHER)
        url = f"/api/portfolio/{env.sample_id}"
        body = {"profileId": env.sample_id, "regenerate": True}
        made = [
            (await env.client.post("/api/portfolio", json=body, headers=listed)).status_code for _ in range(3)
        ]
        rewrites = [
            (await env.client.post(f"{url}/rewrite", json={"slotId": "tagline"}, headers=listed)).status_code
            for _ in range(2)
        ]
        reads = [(await env.client.get(url, headers=listed)).status_code for _ in range(12)]
        assert made == [200] * 3 and rewrites == [200] * 2 and reads == [200] * 12
        assert [h for h in await env.rows(RateLimitHitRow) if h.bucket.startswith("portfolio")] == []

        # nothing of the above was counted, so one more is welcome; the second is refused
        assert (await env.client.post("/api/portfolio", json=body, headers=other)).status_code == 200
        assert (await env.client.post("/api/portfolio", json=body, headers=other)).status_code == 429


# ───────────────────────── the light reads ─────────────────────────


async def test_light_reads_never_shed_a_listed_account_but_do_shed_everyone_else(
    build_settings: MakeSettings,
) -> None:
    async with running(build_settings(guard=guard(chat_per_minute=1))) as env:
        listed = await sign_in(env, LISTED)
        paths = ["/api/me", "/api/billing/entitlements", f"/api/profile/{env.sample_id}"]
        for n, path in enumerate(paths):
            mine = [(await env.client.get(path, headers=listed)).status_code for _ in range(12)]
            address = {
                "cf-connecting-ip": f"198.51.100.{40 + n}"
            }  # its own address: the light reads share one bucket
            theirs = [(await env.client.get(path, headers=address)).status_code for _ in range(6)]
            assert mine == [200] * 12, path
            assert theirs[:2] == [200, 200] and 429 in theirs, path


# ───────────────────────── saying so ─────────────────────────


def entitlements_of(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200
    body = response.json()
    found: dict[str, Any] = body.get("entitlements", body)
    return found


async def test_me_and_the_entitlements_say_who_is_unlimited(build_settings: MakeSettings) -> None:
    async with running(build_settings(guard=guard())) as env:
        listed, other = await sign_in(env, LISTED), await sign_in(env, OTHER)
        mine = entitlements_of(await env.client.get("/api/me", headers=listed))
        assert mine == {"credits": 0, "freeMatchAvailable": True, "unlimited": True}
        assert (await env.client.get("/api/billing/entitlements", headers=listed)).json()["unlimited"] is True
        for headers in (other, {}):
            theirs = entitlements_of(await env.client.get("/api/me", headers=headers))
            assert "unlimited" not in theirs
            assert (
                "unlimited" not in (await env.client.get("/api/billing/entitlements", headers=headers)).json()
            )


async def test_the_address_is_matched_whatever_its_case_in_the_list_or_the_account(
    build_settings: MakeSettings,
) -> None:
    async with running(build_settings(guard=guard(listed="Tester@EXAMPLE.com"))) as env:
        container = env.container
        user = await container.repos.users.sign_in_email(
            email="TESTER@example.COM", now=container.clock.now()
        )
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        response = await env.client.get("/api/me", headers={"cookie": f"{SESSION}={token}"})
        assert entitlements_of(response)["unlimited"] is True


# ───────────────────────── who is not exempt ─────────────────────────


async def test_an_empty_list_exempts_nobody_even_with_a_session(build_settings: MakeSettings) -> None:
    async with running(build_settings(guard=guard(listed="", **ONE_EACH)), claude=scripted()) as env:
        headers = await sign_in(env, LISTED)
        assert (await env.analyze(notes=NOTES, headers=headers)).status_code == 200
        assert (await env.analyze(notes=NOTES, headers=headers)).status_code == 429
        assert "unlimited" not in entitlements_of(await env.client.get("/api/me", headers=headers))


@pytest.mark.parametrize("cookie", ["garbage", "a" * 43, ""])
async def test_a_cookie_that_is_not_a_live_session_exempts_nobody(
    build_settings: MakeSettings, cookie: str
) -> None:
    async with running(build_settings(guard=guard(**ONE_EACH)), claude=scripted()) as env:
        headers = {"cookie": f"{SESSION}={cookie}"}
        assert (await env.analyze(notes=NOTES, headers=headers)).status_code == 200
        assert (await env.analyze(notes=NOTES, headers=headers)).status_code == 429


async def test_a_signed_out_account_is_limited_again(build_settings: MakeSettings) -> None:
    async with running(build_settings(guard=guard(**ONE_EACH)), claude=scripted()) as env:
        headers = await sign_in(env, LISTED)
        assert [(await env.analyze(notes=NOTES, headers=headers)).status_code for _ in range(3)] == [200] * 3
        token = headers["cookie"].split("=", 1)[1]
        assert await env.container.auth.sessions.end(token) is True
        assert (
            await env.analyze(notes=NOTES, headers=headers)
        ).status_code == 200  # the one the address allows
        assert (await env.analyze(notes=NOTES, headers=headers)).status_code == 429


# ───────────────────────── what still applies ─────────────────────────


async def test_the_spend_ceiling_still_refuses_a_listed_account(build_settings: MakeSettings) -> None:
    claude = scripted()
    async with running(build_settings(guard=guard(daily_ceiling_usd=0.0)), claude=claude) as env:
        headers = await sign_in(env, LISTED)
        analyze = await env.analyze(notes=NOTES, headers=headers)
        chat = await env.chat(headers=headers)
    for response in (analyze, chat):
        assert response.status_code == 503
        assert response.json() == {"error": SPEND_CEILING_MESSAGE}
    assert claude.calls == []


async def test_a_listed_accounts_spend_is_still_reserved_and_recorded(build_settings: MakeSettings) -> None:
    claude = scripted()
    async with running(build_settings(guard=guard()), claude=claude) as env:
        headers = await sign_in(env, LISTED)
        assert (await env.analyze(notes=NOTES, headers=headers)).status_code == 200
        spent, calls = (await env.spend())["total"]
    assert calls > 0 and spent > 0  # the ledger stays true for the owner's own runs too
