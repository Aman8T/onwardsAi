"""Who may open a profile, through the real app over a real SQLite file: once an account has claimed a profile, the
profile read, the chat and every portfolio route that takes its id answer only that account's session, and everyone
else (anonymous, or another account) gets the 404 an unknown id gets. A profile nobody has claimed, and the sample
persona, stay open to whoever holds the id. A refused request costs nothing and changes nothing. A portfolio written for
a claimed profile carries its owner. Only the network edges are faked (Claude is scripted, Jev answers confidently)."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import update

from tayyari.chat.service import PROFILE_NOT_FOUND_MESSAGE
from tayyari.contract import ChatDoneEvent
from tayyari.contract.portfolio import Portfolio
from tayyari.core.settings import Settings
from tayyari.portfolio.service import PORTFOLIO_NOT_FOUND
from tayyari.storage.tables import ChatSessionRow, PortfolioRow, ProfileRow, RateLimitHitRow
from tests.fakes.claude import text_turn
from tests.integration.support import Env, RecordingClaude, chat_events, roomy_guard, running
from tests.integration.test_portfolio_api import done_page, jev, portfolio_events, scripted_claude

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
PID = "a1" * 16
OTHER_PID = "c3" * 16
UNKNOWN_ID = "f" * 32
PROFILE_NOT_FOUND = "Profile not found"
ANSWER = "Learn SQL first."


# ───────────────────────── the rig ─────────────────────────


@dataclass
class Account:
    user_id: str
    headers: dict[str, str]


@dataclass
class Rig:
    env: Env
    claude: RecordingClaude

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def account(self, email: str = "ana@example.com") -> Account:
        """A signed-in account: its row, and the cookie of a live session."""
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return Account(user.id, {"cookie": f"{SESSION}={token}"})

    async def profile(self, pid: str = PID, *, owner: Account | None = None) -> str:
        """A stored profile (a copy of the sample persona under ``pid``), claimed by ``owner`` when one is given."""
        container = self.env.container
        sample = container.sample
        assert sample.sample_profile is not None
        await container.repos.profiles.save(
            pid,
            {**sample.sample_profile.to_dict(), "id": pid},
            sample.sample_context,
            goal=sample.sample_profile.goal,
            owner_hash=None,
            is_sample=False,
        )
        if owner is not None:
            await container.repos.users.claim_profile(owner.user_id, pid)
        return pid

    @property
    def model_calls(self) -> int:
        return len(self.claude.structured_calls) + len(self.claude.chat_calls)

    async def counted(self) -> int:
        """Everything the shared rate-limit counters have counted so far."""
        return sum(row.count for row in await self.env.rows(RateLimitHitRow))

    async def pages(self) -> list[tuple[str, int, str | None]]:
        rows = await self.env.rows(PortfolioRow)
        return sorted((r.profile_id, r.version, r.user_id) for r in rows)


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    """``async with start() as rig``: the app, a scripted model that extracts the sample's facts, writes its page and
    answers a chat turn, and limits that nothing here trips."""

    @asynccontextmanager
    async def run() -> AsyncIterator[Rig]:
        claude = scripted_claude()
        claude.script_chat(text_turn(ANSWER), repeat=True)
        guard = roomy_guard(portfolio_per_minute=500, portfolio_per_hour=500, portfolio_per_day=500)
        async with running(build_settings(guard=guard), claude=claude, jev=jev()) as env:
            yield Rig(env, claude)

    return run


# ───────────────────────── the routes ─────────────────────────


@dataclass(frozen=True)
class Route:
    name: str
    send: Callable[[httpx.AsyncClient, str, dict[str, str]], Awaitable[httpx.Response]]
    ok: int
    """The status an allowed caller gets."""
    unknown: str
    """The sentence of the 404 a refused caller gets: the one the route gives an unknown id."""


def _profile(c: httpx.AsyncClient, pid: str, h: dict[str, str]) -> Awaitable[httpx.Response]:
    return c.get(f"/api/profile/{pid}", headers=h)


def _chat(c: httpx.AsyncClient, pid: str, h: dict[str, str]) -> Awaitable[httpx.Response]:
    return c.post("/api/chat", json={"profileId": pid, "message": "What should I learn next?"}, headers=h)


def _generate(c: httpx.AsyncClient, pid: str, h: dict[str, str]) -> Awaitable[httpx.Response]:
    return c.post("/api/portfolio", json={"profileId": pid, "regenerate": True}, headers=h)


def _read(c: httpx.AsyncClient, pid: str, h: dict[str, str]) -> Awaitable[httpx.Response]:
    return c.get(f"/api/portfolio/{pid}", headers=h)


def _patch(c: httpx.AsyncClient, pid: str, h: dict[str, str]) -> Awaitable[httpx.Response]:
    return c.patch(f"/api/portfolio/{pid}", json={"slots": [{"id": "tagline", "text": "Edited."}]}, headers=h)


def _rewrite(c: httpx.AsyncClient, pid: str, h: dict[str, str]) -> Awaitable[httpx.Response]:
    return c.post(f"/api/portfolio/{pid}/rewrite", json={"slotId": "tagline"}, headers=h)


def _preview(c: httpx.AsyncClient, pid: str, h: dict[str, str]) -> Awaitable[httpx.Response]:
    return c.get(f"/api/portfolio/{pid}/public-preview", headers=h)


def _delete(c: httpx.AsyncClient, pid: str, h: dict[str, str]) -> Awaitable[httpx.Response]:
    return c.delete(f"/api/portfolio/{pid}", headers=h)


ROUTES = [
    Route("profile", _profile, 200, PROFILE_NOT_FOUND),
    Route("chat", _chat, 200, PROFILE_NOT_FOUND_MESSAGE),
    Route("generate", _generate, 200, PROFILE_NOT_FOUND),
    Route("read", _read, 200, PORTFOLIO_NOT_FOUND),
    Route("patch", _patch, 200, PORTFOLIO_NOT_FOUND),
    Route("rewrite", _rewrite, 200, PORTFOLIO_NOT_FOUND),
    Route("preview", _preview, 200, PORTFOLIO_NOT_FOUND),
    Route("delete", _delete, 204, PORTFOLIO_NOT_FOUND),
]
"""In the order a person uses them; ``delete`` is last because it takes the page away."""


async def statuses(rig: Rig, pid: str, headers: dict[str, str]) -> dict[str, int]:
    """Every route in turn as one caller, by name."""
    return {route.name: (await route.send(rig.client, pid, headers)).status_code for route in ROUTES}


def everything_allowed() -> dict[str, int]:
    return {route.name: route.ok for route in ROUTES}


# ───────────────────────── a claimed profile ─────────────────────────


async def test_a_claimed_profile_is_served_to_its_owner_on_every_route(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile(owner=ana)

        assert await statuses(rig, pid, ana.headers) == everything_allowed()


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.name)
@pytest.mark.parametrize("caller", ["anonymous", "another account", "a garbage cookie"])
async def test_a_claimed_profile_answers_everyone_else_with_the_404_of_an_unknown_id(
    start: Any, route: Route, caller: str
) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile(owner=ana)
        await _generate(rig.client, pid, ana.headers)  # the page exists, so a 200 is possible
        headers = {
            "anonymous": {},
            "another account": (await rig.account("raj@example.com")).headers,
            "a garbage cookie": {"cookie": f"{SESSION}=not-a-session"},
        }[caller]

        refused = await route.send(rig.client, pid, headers)

        assert refused.status_code == 404, refused.text
        assert refused.json() == {"error": route.unknown}
        assert refused.headers["content-type"].startswith("application/json")
        # where an unknown id is a 404 too, the two cannot be told apart
        unknown = await route.send(rig.client, UNKNOWN_ID, headers)
        if unknown.status_code == 404:
            assert refused.content == unknown.content


async def test_a_refused_request_costs_nothing_and_changes_nothing(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile(owner=ana)
        await _generate(rig.client, pid, ana.headers)
        before = (rig.model_calls, await rig.counted(), await rig.pages(), await rig.env.rows(ChatSessionRow))
        page_before = (await rig.client.get(f"/api/portfolio/{pid}", headers=ana.headers)).json()

        for headers in ({}, (await rig.account("raj@example.com")).headers):
            for route in ROUTES:
                assert (await route.send(rig.client, pid, headers)).status_code == 404

        after = (rig.model_calls, await rig.counted(), await rig.pages(), await rig.env.rows(ChatSessionRow))
        assert (
            after[:3] == before[:3]
        )  # no model call, no counter, the page untouched (same version and owner)
        assert len(after[3]) == len(before[3])  # no conversation was started
        assert (await rig.client.get(f"/api/portfolio/{pid}", headers=ana.headers)).json() == page_before


async def test_the_owner_s_edits_are_still_the_owner_s_after_a_refused_attempt(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile(owner=ana)
        await _generate(rig.client, pid, ana.headers)
        edited = await _patch(rig.client, pid, ana.headers)

        stranger = await _delete(rig.client, pid, {})
        read = await _read(rig.client, pid, ana.headers)

    assert edited.status_code == 200 and stranger.status_code == 404
    tagline = next(s for s in Portfolio.model_validate(read.json()).content.slots if s.id == "tagline")
    assert tagline.text == "Edited."


async def test_a_claim_closes_a_profile_that_was_open(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile()

        before = await statuses(rig, pid, {})  # nobody has claimed it: anyone with the id, as before
        await _generate(rig.client, pid, {})  # (the walk ends by deleting the page)
        claimed = await rig.client.post("/api/me/claim", json={"profileId": pid}, headers=ana.headers)
        anonymous = await statuses(rig, pid, {})
        owner = await statuses(rig, pid, ana.headers)

    assert before == everything_allowed()
    assert claimed.status_code == 200
    assert anonymous == {route.name: 404 for route in ROUTES}
    assert owner == everything_allowed()


async def test_a_conversation_started_before_the_claim_belongs_to_the_owner_afterwards(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile()
        first = await _chat(rig.client, pid, {})  # anonymous, while nobody has claimed it
        started = chat_events(first)[-1]
        assert isinstance(started, ChatDoneEvent)
        await rig.env.container.repos.users.claim_profile(ana.user_id, pid)

        def resume(headers: dict[str, str]) -> Awaitable[httpx.Response]:
            body = {"profileId": pid, "message": "And after that?", "sessionId": started.sessionId}
            return rig.client.post("/api/chat", json=body, headers=headers)

        refused = await resume({})
        owner = await resume(ana.headers)

    assert first.status_code == 200
    assert refused.status_code == 404 and refused.json() == {"error": PROFILE_NOT_FOUND_MESSAGE}
    resumed = chat_events(owner)[-1]
    assert owner.status_code == 200 and isinstance(resumed, ChatDoneEvent)
    assert resumed.sessionId == started.sessionId


async def test_an_id_that_looks_wrong_is_the_same_404_for_everyone(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        for headers in ({}, ana.headers):
            odd = await rig.client.get("/api/profile/not%20an%20id", headers=headers)
            assert odd.status_code == 404 and odd.json() == {"error": PROFILE_NOT_FOUND}


# ───────────────────────── what stays open ─────────────────────────


async def test_a_profile_nobody_has_claimed_is_served_to_anyone_holding_its_id(start: Any) -> None:
    async with start() as rig:
        pid = await rig.profile()
        raj = await rig.account("raj@example.com")

        assert await statuses(rig, pid, {}) == everything_allowed()
        assert await statuses(rig, pid, raj.headers) == everything_allowed()


async def test_the_sample_persona_is_served_to_everyone(start: Any) -> None:
    async with start() as rig:
        pid = rig.env.sample_id
        raj = await rig.account("raj@example.com")

        assert await statuses(rig, pid, {}) == everything_allowed()
        assert await statuses(rig, pid, raj.headers) == everything_allowed()


async def test_a_sample_run_is_open_even_if_its_owner_column_were_set(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile(OTHER_PID)
        assert rig.env.container.db is not None
        async with rig.env.container.db.session() as session:  # a state `claim_profile` refuses to create
            await session.execute(
                update(ProfileRow).where(ProfileRow.id == pid).values(is_sample=True, user_id=ana.user_id)
            )

        assert await statuses(rig, pid, {}) == everything_allowed()


async def test_the_public_page_of_a_claimed_profile_is_still_public_once_published(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile(owner=ana)
        page = done_page(portfolio_events(await _generate(rig.client, pid, ana.headers)))
        stored = await rig.env.container.repos.portfolios.get(pid)
        assert stored is not None
        await rig.env.container.repos.portfolios.put(
            dataclasses.replace(stored, published=True), expected_version=stored.version
        )

        public = await rig.client.get(f"/api/p/{page.publicId}")  # the owner chose to publish it

    assert public.status_code == 200 and pid not in public.text


# ───────────────────────── whose page it is ─────────────────────────


async def test_a_portfolio_generated_after_the_claim_carries_the_user_id(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile(owner=ana)

        generated = await rig.client.post("/api/portfolio", json={"profileId": pid}, headers=ana.headers)
        first = await rig.pages()
        regenerated = await rig.client.post(
            "/api/portfolio", json={"profileId": pid, "regenerate": True}, headers=ana.headers
        )
        second = await rig.pages()
        edited = await _patch(rig.client, pid, ana.headers)
        third = await rig.pages()

    assert done_page(portfolio_events(generated)).profileId == pid
    assert done_page(portfolio_events(regenerated)).version == 2
    assert edited.status_code == 200
    assert first == [(pid, 1, ana.user_id)]
    assert second == [(pid, 2, ana.user_id)]
    assert third == [(pid, 3, ana.user_id)]


async def test_a_portfolio_of_an_unclaimed_profile_or_the_sample_has_no_owner(start: Any) -> None:
    async with start() as rig:
        pid = await rig.profile()
        await rig.client.post("/api/portfolio", json={"profileId": pid})
        await rig.client.post("/api/portfolio", json={"profileId": rig.env.sample_id})

        pages = await rig.pages()

    assert sorted(p[0] for p in pages) == sorted([pid, rig.env.sample_id])
    assert all(owner is None for _, _, owner in pages)


async def test_a_page_made_before_the_claim_is_handed_to_the_account_by_the_claim(start: Any) -> None:
    async with start() as rig:
        ana = await rig.account()
        pid = await rig.profile()
        await rig.client.post("/api/portfolio", json={"profileId": pid})
        assert await rig.pages() == [(pid, 1, None)]

        await rig.client.post("/api/me/claim", json={"profileId": pid}, headers=ana.headers)
        claimed = await rig.pages()
        regenerated = await rig.client.post(
            "/api/portfolio", json={"profileId": pid, "regenerate": True}, headers=ana.headers
        )

    assert claimed == [(pid, 1, ana.user_id)]
    assert done_page(portfolio_events(regenerated)).version == 2
