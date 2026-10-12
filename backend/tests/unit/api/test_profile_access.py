"""Who may open a profile: `owner_required` (the rule) and `require_profile_access` (the dependency every route that
takes a profile id calls), against the fake repositories and a stand-in for the session lookup. The routes themselves
are covered end to end in `tests/integration/test_profile_ownership.py`."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from tayyari.api import deps
from tayyari.api.deps import PROFILE_NOT_FOUND, owner_required, require_profile_access
from tayyari.contract.stored import StoredContext
from tayyari.core.errors import NotFound
from tayyari.storage.ports import ProfileAccess, StoredUser
from tests.fakes.repos import FakeRepositories
from tests.unit.api.helpers import http_scope

ANA = "a" * 32
RAJ = "b" * 32


def user(user_id: str) -> StoredUser:
    then = datetime(2026, 10, 11, tzinfo=UTC)
    return StoredUser(
        id=user_id,
        email=f"{user_id[:3]}@example.com",
        name=None,
        picture_url=None,
        google_sub=None,
        created_at=then,
        last_login_at=then,
        locale=None,
        currency=None,
    )


# ───────────────────────── the rule ─────────────────────────


@pytest.mark.parametrize(
    ("access", "required"),
    [
        (None, None),  # no such row: the route's own handling of an unknown id applies
        (ProfileAccess(user_id=None, is_sample=False), None),  # nobody has claimed it
        (ProfileAccess(user_id=None, is_sample=True), None),  # a sample run
        (
            ProfileAccess(user_id=ANA, is_sample=True),
            None,
        ),  # a sample run is public whatever its owner column says
        (ProfileAccess(user_id=ANA, is_sample=False), ANA),  # claimed: that account only
    ],
)
def test_owner_required(access: ProfileAccess | None, required: str | None) -> None:
    assert owner_required(access) == required


# ───────────────────────── the dependency ─────────────────────────


class Rig:
    """The fake repositories behind a request, and the account the session lookup would find (None: anonymous)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fakes = FakeRepositories()
        self.signed_in: StoredUser | None = None
        self.session_lookups = 0
        container = SimpleNamespace(repos=self.fakes.bundle)
        monkeypatch.setattr(deps, "get_container", lambda request: container)

        async def current(request: Request) -> StoredUser | None:
            self.session_lookups += 1
            return self.signed_in

        monkeypatch.setattr(deps, "get_current_user", current)

    async def profile(self, pid: str, *, sample: bool = False, owner: str | None = None) -> None:
        await self.fakes.profiles.save(
            pid, {"id": pid}, StoredContext(), goal=None, owner_hash=None, is_sample=sample
        )
        if owner is not None and sample:
            self.fakes._state.profiles[pid].user_id = owner  # a claim the repository itself would refuse
        elif owner is not None:
            await self.fakes.users.claim_profile(owner, pid)

    async def check(self, pid: str, **kwargs: Any) -> None:
        await require_profile_access(Request(http_scope(path=f"/api/profile/{pid}")), pid, **kwargs)


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(monkeypatch)


async def test_an_unclaimed_profile_is_open_to_anyone_and_costs_no_session_lookup(rig: Rig) -> None:
    await rig.profile("open")
    await rig.check("open")
    rig.signed_in = user(RAJ)
    await rig.check("open")
    assert rig.session_lookups == 0


async def test_a_sample_run_is_open_to_anyone(rig: Rig) -> None:
    await rig.profile("sample-1", sample=True, owner=ANA)
    await rig.check("sample-1")
    assert rig.session_lookups == 0


async def test_an_id_no_stored_profile_has_passes_through_to_the_route(rig: Rig) -> None:
    await rig.check("not-stored")  # the route then answers it exactly as it did before
    assert rig.session_lookups == 0


async def test_a_claimed_profile_is_open_to_its_owner_only(rig: Rig) -> None:
    await rig.profile("mine", owner=ANA)

    rig.signed_in = user(ANA)
    await rig.check("mine")

    rig.signed_in = None
    with pytest.raises(NotFound):
        await rig.check("mine")

    rig.signed_in = user(RAJ)
    with pytest.raises(NotFound):
        await rig.check("mine")


async def test_the_refusal_is_the_route_s_not_found_sentence_and_a_404_never_a_403(rig: Rig) -> None:
    await rig.profile("mine", owner=ANA)

    with pytest.raises(NotFound) as default:
        await rig.check("mine")
    with pytest.raises(NotFound) as custom:
        await rig.check("mine", not_found="Portfolio not found")

    assert default.value.status == 404 and default.value.user_message == PROFILE_NOT_FOUND
    assert custom.value.status == 404 and custom.value.user_message == "Portfolio not found"
    assert default.value.code == custom.value.code == "not_found"
