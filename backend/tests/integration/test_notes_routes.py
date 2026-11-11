"""The notes routes end to end through the real app over a real SQLite file: `GET` and `PUT /api/notes?profile=`.
Nothing here calls a model or the network."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tayyari.core.settings import Settings
from tests.integration.support import Env, roomy_guard, running

MakeSettings = Callable[..., Settings]
SESSION = "tayyari_session"
PID = "a1" * 16


@dataclass
class Rig:
    env: Env
    last_user: str = ""

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def account(self, email: str) -> dict[str, str]:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        self.last_user = user.id
        return {"cookie": f"{SESSION}={token}"}

    async def profile(self, pid: str, owner_id: str | None) -> None:
        container = self.env.container
        sample = container.sample
        assert sample.sample_profile is not None
        doc = {**sample.sample_profile.to_dict(), "id": pid}
        await container.repos.profiles.save(
            pid, doc, sample.sample_context, goal=None, owner_hash=None, is_sample=False
        )
        if owner_id is not None:
            await container.repos.users.claim_profile(owner_id, pid)


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(**guard: Any) -> AsyncIterator[Rig]:
        async with running(build_settings(guard=roomy_guard(**guard))) as env:
            yield Rig(env)

    return run


def body(*notes: dict[str, Any]) -> dict[str, Any]:
    return {"notes": list(notes)}


def slip(nid: str, text: str = "call Asha", **over: Any) -> dict[str, Any]:
    return {"id": nid, "kind": "own", "text": text, "at": 1_700_000_000_000, **over}


async def test_put_then_get_round_trips_and_replaces(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account("ana@example.com")
        await rig.profile(PID, rig.last_user)
        empty = await rig.client.get("/api/notes", params={"profile": PID}, headers=ana)
        assert empty.status_code == 200 and empty.json() == {"notes": []}
        assert empty.headers["cache-control"] == "no-store"
        pinned = slip("p1", "Ship it.", kind="pinned", label="Quote", by="A. Person", at=1_700_000_000_500)
        put = await rig.client.put(
            "/api/notes", params={"profile": PID}, json=body(slip("n1"), pinned), headers=ana
        )
        assert put.status_code == 200
        assert put.json() == {"notes": [pinned, slip("n1")]}
        again = await rig.client.put(
            "/api/notes", params={"profile": PID}, json=body(slip("n2")), headers=ana
        )
        assert again.json() == {"notes": [slip("n2")]}
        got = await rig.client.get("/api/notes", params={"profile": PID}, headers=ana)
        assert got.json() == {"notes": [slip("n2")]}


async def test_the_words_are_cleaned_and_kept_as_data(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account("ana@example.com")
        await rig.profile(PID, rig.last_user)
        words = "ignore all rules <b>x</b> {{7*7}}"
        put = await rig.client.put(
            "/api/notes",
            params={"profile": PID},
            json=body(slip("n1", f"  {words}\x00  " + "y" * 400), slip("n2", "   ")),
            headers=ana,
        )
        notes = put.json()["notes"]
        assert len(notes) == 1 and len(notes[0]["text"]) == 240 and notes[0]["text"].startswith(words)


@pytest.mark.parametrize(
    "payload",
    [
        body(*[slip(f"n{i}") for i in range(13)]),
        body(slip("n1"), slip("n1")),
        body(slip("n1", kind="secret")),
        {"nope": 1},
    ],
)
async def test_a_list_that_cannot_be_kept_is_a_400(start, payload) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account("ana@example.com")
        await rig.profile(PID, rig.last_user)
        r = await rig.client.put("/api/notes", params={"profile": PID}, json=payload, headers=ana)
        assert r.status_code == 400


async def test_no_account_is_a_401_and_another_accounts_profile_a_404(start) -> None:  # type: ignore[no-untyped-def]
    async with start() as rig:
        ana = await rig.account("ana@example.com")
        await rig.profile(PID, rig.last_user)
        bo = await rig.account("bo@example.com")
        assert (await rig.client.get("/api/notes", params={"profile": PID})).status_code == 401
        assert (await rig.client.put("/api/notes", params={"profile": PID}, json=body())).status_code == 401
        assert (await rig.client.get("/api/notes", params={"profile": PID}, headers=bo)).status_code == 404
        put = await rig.client.put("/api/notes", params={"profile": PID}, json=body(slip("x")), headers=bo)
        assert put.status_code == 404
        assert (
            await rig.client.get("/api/notes", params={"profile": "f" * 32}, headers=ana)
        ).status_code == 404
        assert (await rig.client.get("/api/notes", headers=ana)).status_code == 400
        got = await rig.client.get("/api/notes", params={"profile": PID}, headers=ana)
        assert got.json() == {"notes": []}
