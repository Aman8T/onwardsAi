"""The radar card end to end (R1) through the real app over a real SQLite file: the owner's routes, the three public
addresses, their headers, and the claim that moves a card to a newer profile. Nothing here calls a model or the network.

    GET  /api/card?profileId=     the view; blocked for the sample and an anonymous graph; 404 for another account's
    PUT  /api/card                sharing on and off; 400 for a skill not offered or a role not on the graph; 403 when blocked
    POST /api/card/new-link       a new link, the old one stops
    GET  /r/{slug}.svg            the card; a 200 neutral card for unknown, stopped and malformed links; ETag and 304
    GET  /og/{slug}-{v}.png       1200 x 627; a 301 from an old version; the neutral picture for an unknown link
    GET  /u/{slug}                share tags, escaped; noindex; a 404 page for an unknown link
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tayyari.api.routes.card import IMAGE_CACHE, SVG_CSP
from tayyari.card.page import PAGE_CSP
from tayyari.core.settings import Settings
from tayyari.storage.tables import CardSnapshotRow
from tests.integration.support import Env, roomy_guard, running

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
PID = "a1" * 16
NEWER = "b2" * 16
ANON = "c3" * 16


@dataclass
class Account:
    user_id: str
    headers: dict[str, str]


@dataclass
class Rig:
    env: Env

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def account(self, email: str = "riya@example.com") -> Account:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return Account(user.id, {"cookie": f"{SESSION}={token}"})

    async def profile(self, pid: str, *, owner: Account | None = None, **domain0: Any) -> str:
        """A copy of the sample persona under ``pid``, claimed by ``owner`` when given; ``domain0`` overrides the first area."""
        container = self.env.container
        sample = container.sample
        assert sample.sample_profile is not None
        doc = {**sample.sample_profile.to_dict(), "id": pid}
        if domain0:
            doc["domains"] = [{**doc["domains"][0], **domain0}, *doc["domains"][1:]]
        await container.repos.profiles.save(
            pid, doc, sample.sample_context, goal=None, owner_hash=None, is_sample=False
        )
        if owner is not None:
            await container.repos.users.claim_profile(owner.user_id, pid)
        return pid

    async def put(self, who: Account | None, pid: str = PID, **over: Any) -> httpx.Response:
        body: dict[str, Any] = {"profileId": pid, "shared": True, "skills": [], "theme": "light"}
        body.update(over)
        return await self.client.put("/api/card", json=body, headers=who.headers if who else {})

    async def shared(self, who: Account, **over: Any) -> dict[str, Any]:
        response = await self.put(who, **over)
        assert response.status_code == 200, response.text
        view: dict[str, Any] = response.json()
        return view


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**guard: Any) -> AsyncIterator[Rig]:
        async with running(build_settings(guard=roomy_guard(**guard))) as env:
            yield Rig(env)

    return run


def path_of(url: str) -> str:
    return "/" + url.split("://", 1)[1].split("/", 1)[1]


# ───────────────────────── the owner ─────────────────────────


async def test_the_owner_shares_and_stops_sharing(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        await rig.profile(PID, owner=ana)
        first = await rig.client.get("/api/card", params={"profileId": PID}, headers=ana.headers)
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        assert first.json()["settings"]["shared"] is False and "slug" not in first.json()

        on = await rig.shared(ana)
        assert len(on["slug"]) == 12 and on["markdown"].startswith("[![Skill shape](")
        assert (
            (await rig.client.get(path_of(on["svgUrl"]))).headers["content-type"].startswith("image/svg+xml")
        )

        off = await rig.shared(ana, shared=False)
        assert "slug" not in off
        stopped = await rig.client.get(path_of(on["svgUrl"]))
        assert stopped.status_code == 200 and "isn&#x27;t shared" not in stopped.text
        assert "This card isn't shared any more." in stopped.text
        assert (await rig.client.get(path_of(on["pageUrl"]))).status_code == 404


async def test_a_new_link_stops_the_old_one(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        await rig.profile(PID, owner=ana)
        old = await rig.shared(ana)
        new = await rig.client.post("/api/card/new-link", json={"profileId": PID}, headers=ana.headers)
        assert new.status_code == 200 and new.json()["slug"] != old["slug"]
        assert "isn't shared any more" in (await rig.client.get(path_of(old["svgUrl"]))).text
        assert "isn't shared any more" not in (await rig.client.get(path_of(new.json()["svgUrl"]))).text


async def test_the_sample_and_an_anonymous_graph_are_blocked(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        sample = await rig.client.get(
            "/api/card", params={"profileId": rig.env.sample_id}, headers=ana.headers
        )
        assert sample.status_code == 200 and sample.json()["blocked"] == "sample"
        await rig.profile(ANON)
        anon = await rig.client.get("/api/card", params={"profileId": ANON})
        assert anon.status_code == 200 and anon.json()["blocked"] == "anonymous"
        refused = await rig.put(None, ANON)
        assert refused.status_code == 403 and "Sign in" in refused.json()["error"]
        assert (await rig.put(ana, rig.env.sample_id)).status_code == 403
        assert await rig.env.rows(CardSnapshotRow) == []


async def test_another_accounts_graph_is_not_found(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana, ben = await rig.account(), await rig.account("ben@example.com")
        await rig.profile(PID, owner=ana)
        for response in (
            await rig.client.get("/api/card", params={"profileId": PID}, headers=ben.headers),
            await rig.client.get("/api/card", params={"profileId": PID}),
            await rig.put(ben),
            await rig.client.post("/api/card/new-link", json={"profileId": PID}, headers=ben.headers),
            await rig.client.get("/api/card", params={"profileId": "nope"}, headers=ana.headers),
        ):
            assert response.status_code == 404, response.text


async def test_skills_and_roles_are_checked(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        await rig.profile(PID, owner=ana)
        bad_skill = await rig.put(ana, skills=["not-a-skill"])
        assert bad_skill.status_code == 400
        bad_role = await rig.put(ana, roleId="astronaut")
        assert bad_role.status_code == 400
        too_many = await rig.put(ana, skills=["a", "b", "c", "d"])
        assert too_many.status_code == 400


# ───────────────────────── the README card ─────────────────────────


async def test_unknown_stopped_and_malformed_links_get_the_neutral_card(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        for path in (
            "/r/zzzzzzzzzzzz.svg",
            "/r/short.svg",
            "/r/ABCDEFGHIJKL.svg",
            "/r/aaaaaaaaaaaa.png",
            "/r/aaaaaaaaaa%21!.svg",
        ):
            response = await rig.client.get(path)
            assert response.status_code == 200, path
            assert response.headers["content-type"].startswith("image/svg+xml")
            assert response.headers["cache-control"] == "public, max-age=300"
            assert "This card isn't shared any more." in response.text
            ET.fromstring(response.text)  # noqa: S314  (our own output)


async def test_the_card_headers_etag_and_304(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        await rig.profile(PID, owner=ana)
        view = await rig.shared(ana, theme="dark")
        path = path_of(view["svgUrl"])
        response = await rig.client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == IMAGE_CACHE
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == SVG_CSP, (
            "the middleware must keep the route's policy"
        )
        assert "#0b131c" in response.text, "the stored theme is drawn"
        etag = response.headers["etag"]
        again = await rig.client.get(path, headers={"if-none-match": etag})
        assert again.status_code == 304 and again.content == b""
        light = await rig.client.get(path + "&theme=light" if "?" in path else path + "?theme=light")
        assert "#f0f7fe" in light.text and light.headers["etag"] != etag
        small = await rig.client.get(path_of(view["svgUrl"]).split("?")[0] + "?size=sm")
        assert 'width="340"' in small.text


async def test_compare_is_honoured_only_for_the_stored_role(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        await rig.profile(PID, owner=ana)
        view = await rig.shared(ana, roleId="design-engineer")
        base = path_of(view["svgUrl"]).split("?")[0]
        dashed = 'stroke-dasharray="10 8"'
        assert dashed in (await rig.client.get(base + "?compare=design-engineer")).text
        assert dashed not in (await rig.client.get(base + "?compare=senior-frontend-engineer")).text
        assert dashed not in (await rig.client.get(base)).text


# ───────────────────────── the LinkedIn picture ─────────────────────────


async def test_the_picture_is_1200_by_627_and_an_old_version_moves(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        await rig.profile(PID, owner=ana)
        view = await rig.shared(ana)
        path = path_of(view["ogUrl"])
        png = await rig.client.get(path)
        assert png.status_code == 200 and png.headers["content-type"] == "image/png"
        assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", png.content[16:24]) == (1200, 627)
        assert png.headers["cache-control"] == IMAGE_CACHE
        slug = view["slug"]
        old = await rig.client.get(f"/og/{slug}-00000000.png", follow_redirects=False)
        assert old.status_code == 301 and old.headers["location"] == path
        unknown = await rig.client.get("/og/zzzzzzzzzzzz-00000000.png")
        assert unknown.status_code == 200 and unknown.headers["content-type"] == "image/png"
        assert unknown.headers["cache-control"] == "public, max-age=300"


# ───────────────────────── the page ─────────────────────────


async def test_the_page_carries_escaped_share_tags_and_no_script(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        await rig.profile(PID, owner=ana, name="<script>alert(1)</script> Frontend", score=99)
        view = await rig.shared(ana)
        page = await rig.client.get(path_of(view["pageUrl"]))
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        html = page.text
        assert "<script" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt; Frontend" in html
        assert '<meta property="og:title" content="Strongest in &lt;script&gt;' in html
        assert f'<meta property="og:image" content="{view["ogUrl"]}" />' in html
        assert '<meta name="twitter:card" content="summary_large_image" />' in html
        assert '<meta name="robots" content="noindex" />' in html
        assert page.headers["x-robots-tag"] == "noindex"
        assert page.headers["content-security-policy"] == PAGE_CSP
        assert "mailto:aman@leafout.in?subject=Report%20card%20" in html
        sample = rig.env.container.sample.sample_profile
        assert sample is not None and sample.person.name not in html
        missing = await rig.client.get("/u/zzzzzzzzzzzz")
        assert missing.status_code == 404 and "isn't shared any more" in missing.text


# ───────────────────────── the card window and the claim ─────────────────────────


async def test_the_card_window_counts(start) -> None:  # type: ignore[no-untyped-def]
    async with start(card_per_minute=3) as rig:
        codes = [(await rig.client.get("/r/zzzzzzzzzzzz.svg")).status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


async def test_claiming_a_newer_profile_moves_the_card(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account()
        await rig.profile(PID, owner=ana)
        view = await rig.shared(ana)
        await rig.profile(NEWER, score=7)
        claimed = await rig.client.post("/api/me/claim", json={"profileId": NEWER}, headers=ana.headers)
        assert claimed.status_code == 200, claimed.text
        [row] = await rig.env.rows(CardSnapshotRow)
        assert row.profile_id == NEWER and row.areas[0]["score"] == 7 and row.slug == view["slug"]
        assert row.version != view["ogUrl"].rsplit("-", 1)[1].removesuffix(".png")
