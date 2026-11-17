"""Where can I go next (N1) end to end, through the real app over a real SQLite file: the owner's routes, the two public
addresses and their headers, and the redaction holding all the way to the HTML. Nothing here calls a model or the network.

    GET  /api/report?profileId=      the view; blocked for the sample; 404 for another account's
    PUT  /api/report                 sharing on and off; 400 for a hidden line not offered; 403 when blocked
    POST /api/report/new-link        a new link, the old one stops
    GET  /next/{slug}                share tags, escaped; no script; noindex; a 404 page for unknown, stopped, malformed links
    GET  /og/next/{slug}-{v}.png     1200 x 630; a 301 from an old version; a 404 with the neutral picture; ETag and 304
"""

from __future__ import annotations

import re
import struct
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tayyari.api.routes.report import IMAGE_CACHE
from tayyari.contract.report import DEFAULT_CHOICES
from tayyari.core.settings import Settings
from tayyari.report.page import PAGE_CSP
from tayyari.storage.ports import StoredResume
from tests.integration.support import Env, roomy_guard, running
from tests.unit.report.support import ORG, profile_doc

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
PID = "a1" * 16


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

    async def account(self, email: str = "ravi@example.com") -> Account:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return Account(user.id, {"cookie": f"{SESSION}={token}"})

    async def profile(
        self, pid: str, *, owner: Account | None = None, sample: bool = False, **over: Any
    ) -> str:
        """Ravi's profile from the unit fixtures (an employer named inside his evidence), with his /resume draft."""
        container = self.env.container
        assert container.sample.sample_context is not None
        await container.repos.profiles.save(
            pid,
            profile_doc(pid, **over),
            container.sample.sample_context,
            goal=None,
            owner_hash=None,
            is_sample=sample,
        )
        now = container.clock.now()
        await container.repos.resumes.create(
            StoredResume(
                id=("d" + pid)[:32],
                user_id=None,
                profile_id=pid,
                kind="upload",
                role_key="",
                title="My resume",
                draft={"experience": [{"entryFactId": "e1", "title": "Engineer", "org": ORG, "bullets": []}]},
                final=None,
                facts={},
                is_default=False,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        if owner is not None:
            await container.repos.users.claim_profile(owner.user_id, pid)
        return pid

    async def put(
        self, who: Account | None, pid: str = PID, shared: bool = True, **choices: Any
    ) -> httpx.Response:
        body = {"profileId": pid, "shared": shared, "choices": {**DEFAULT_CHOICES.model_dump(), **choices}}
        return await self.client.put("/api/report", json=body, headers=who.headers if who else {})

    async def shared(self, who: Account, **choices: Any) -> dict[str, Any]:
        response = await self.put(who, **choices)
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
        ravi = await rig.account()
        await rig.profile(PID, owner=ravi)
        first = await rig.client.get("/api/report", params={"profileId": PID}, headers=ravi.headers)
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        body = first.json()
        assert "slug" not in body and body["orgsFound"] == 1
        assert [r["id"] for r in body["public"]["roles"]] == ["qa-lead", "backend-gcc", "platform"]

        on = await rig.shared(ravi)
        assert len(on["slug"]) == 12 and on["pageUrl"].endswith(f"/next/{on['slug']}")
        page = await rig.client.get(path_of(on["pageUrl"]))
        assert page.status_code == 200

        again = await rig.client.post("/api/report/new-link", json={"profileId": PID}, headers=ravi.headers)
        assert again.status_code == 200 and again.json()["slug"] != on["slug"]
        assert (await rig.client.get(path_of(on["pageUrl"]))).status_code == 404

        off = await rig.put(ravi, shared=False)
        assert off.status_code == 200 and "slug" not in off.json()
        assert (await rig.client.get(path_of(again.json()["pageUrl"]))).status_code == 404


async def test_the_sample_is_blocked_and_another_account_gets_404(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ravi, ben = await rig.account(), await rig.account("ben@example.com")
        await rig.profile(PID, owner=ravi)
        assert (
            await rig.client.get("/api/report", params={"profileId": PID}, headers=ben.headers)
        ).status_code == 404
        assert (await rig.put(ben)).status_code == 404
        sample = "e5" * 16
        await rig.profile(sample, sample=True)
        view = await rig.client.get("/api/report", params={"profileId": sample}, headers=ravi.headers)
        assert view.status_code == 200 and view.json()["blocked"] == "sample"
        assert (await rig.put(ravi, pid=sample)).status_code == 403


async def test_a_hidden_line_must_be_offered(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ravi = await rig.account()
        await rig.profile(PID, owner=ravi)
        bad = await rig.put(ravi, hiddenQuotes=["notaquote12"])
        assert bad.status_code == 400 and bad.json()["error"]


# ───────────────────────── the public page ─────────────────────────


async def test_the_page_never_names_the_employer_and_runs_no_script(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ravi = await rig.account()
        await rig.profile(PID, owner=ravi)
        on = await rig.shared(ravi)
        page = await rig.client.get(path_of(on["pageUrl"]))
        html = page.text
        assert "zentrix" not in html.casefold()
        assert "their employer" in html
        assert "<script" not in html.casefold() and 'style="' not in html
        assert page.headers["content-security-policy"] == PAGE_CSP
        assert page.headers["x-robots-tag"] == "noindex"
        assert '<meta name="robots" content="noindex" />' in html
        assert "Where can Ravi S. go next?" in html
        assert "summary_large_image" in html and on["ogUrl"] in html
        visible = re.sub(
            r"<[^>]+>", " ", html
        ).casefold()  # the page's words, not its URLs (a mailto holds %20)
        for word in ("%", "percentile", " rank", "laid off"):
            assert word not in visible
        assert "Data Engineer" not in html  # a near miss stays off while hidden


async def test_the_owners_choices_reach_the_page(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ravi = await rig.account()
        await rig.profile(PID, owner=ravi)
        on = await rig.shared(ravi, showName=False, showNearMisses=True, openToWork=True, showOrgs=True)
        html = (await rig.client.get(path_of(on["pageUrl"]))).text
        assert "Ravi" not in html and "Where can this person go next?" in html
        assert "Close, not yet" in html and "Open to work" in html
        assert "Zentrix" in html


async def test_a_title_holding_markup_is_escaped(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ravi = await rig.account()
        doc = profile_doc(PID)
        roles = doc["roles"]
        for r in roles:
            if r["id"] == "qa-lead":
                r["title"] = "<script>alert(1)</script> Lead"
        await rig.profile(PID, owner=ravi, roles=roles)
        on = await rig.shared(ravi)
        html = (await rig.client.get(path_of(on["pageUrl"]))).text
        assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


async def test_unknown_stopped_and_malformed_links_are_a_plain_404(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        for path in ("/next/zzzzzzzzzzzz", "/next/short", "/next/AAAAAAAAAAAA"):
            page = await rig.client.get(path)
            assert page.status_code == 404 and "isn't shared any more" in page.text
            assert page.headers["x-robots-tag"] == "noindex"


async def test_the_offer_link_shows_only_when_configured(build_settings: MakeSettings) -> None:
    settings = build_settings(guard=roomy_guard(), report={"offer_url": "https://rzp.io/l/day-one"})
    async with running(settings) as env:
        rig = Rig(env)
        ravi = await rig.account()
        await rig.profile(PID, owner=ravi)
        on = await rig.shared(ravi)
        assert on["offerUrl"] == "https://rzp.io/l/day-one"
        html = (await rig.client.get(path_of(on["pageUrl"]))).text
        assert "https://rzp.io/l/day-one?role=QA%20Automation%20Lead" in html


# ───────────────────────── the link preview ─────────────────────────


async def test_the_preview_is_1200_by_630_cached_and_redirects_from_an_old_version(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ravi = await rig.account()
        await rig.profile(PID, owner=ravi)
        on = await rig.shared(ravi)
        og = path_of(on["ogUrl"])
        png = await rig.client.get(og)
        assert png.status_code == 200 and png.headers["content-type"] == "image/png"
        width, height = struct.unpack(">II", png.content[16:24])
        assert (width, height) == (1200, 630)
        assert (
            png.headers["cache-control"] == IMAGE_CACHE and png.headers["x-content-type-options"] == "nosniff"
        )
        etag = png.headers["etag"]
        assert (await rig.client.get(og, headers={"if-none-match": etag})).status_code == 304
        old = og.rsplit("-", 1)[0] + "-00000000.png"
        moved = await rig.client.get(old, follow_redirects=False)
        assert moved.status_code == 301 and moved.headers["location"] == og
        gone = await rig.client.get("/og/next/zzzzzzzzzzzz-00000000.png")
        assert gone.status_code == 404 and gone.headers["content-type"] == "image/png"
