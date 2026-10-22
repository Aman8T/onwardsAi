"""Shared set-up for the radar card tests: the fakes, a sample-shaped profile, and accounts that own one."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tayyari.card.service import CardService
from tayyari.contract.stored import StoredContext
from tests.fakes.clock import FakeClock
from tests.fakes.repos import FakeRepositories

PROFILE = Path(__file__).resolve().parents[2] / "data" / "sample-profile.json"
T0 = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
PUBLIC = "https://app.leafout.in"


def profile_doc(pid: str, **over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(PROFILE.read_text(encoding="utf-8"))
    doc = copy.deepcopy(doc)
    doc["id"] = pid
    doc.update(over)
    return doc


class Slugs:
    """Predictable slugs: ``aaaaaaaaaa01``, ``aaaaaaaaaa02`` ..."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"aaaaaaaaaa{self.n:02d}"


@dataclass
class World:
    fakes: FakeRepositories
    clock: FakeClock
    service: CardService

    async def account(self, email: str = "riya@example.com") -> str:
        user = await self.fakes.users.sign_in_email(email=email, now=self.clock.now())
        return user.id

    async def profile(
        self, pid: str, *, owner: str | None = None, sample: bool = False, doc: dict[str, Any] | None = None
    ) -> str:
        await self.fakes.profiles.save(
            pid,
            doc if doc is not None else profile_doc(pid),
            StoredContext(),
            goal=None,
            owner_hash=None,
            is_sample=sample,
        )
        if owner is not None:
            await self.fakes.users.claim_profile(owner, pid)
        self.clock.advance(60)
        return pid


def world() -> World:
    clock = FakeClock(T0)
    fakes = FakeRepositories(clock)
    service = CardService(
        profiles=fakes.profiles,
        users=fakes.users,
        cards=fakes.card,
        clock=clock,
        public_url=PUBLIC + "/",
        slugs=Slugs(),
    )
    return World(fakes=fakes, clock=clock, service=service)


__all__ = ["PUBLIC", "T0", "Slugs", "World", "profile_doc", "world"]
