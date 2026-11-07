"""The generic stage route cannot go around approve (digest), resolve or "I applied there"."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from tayyari.apply.intake import PageFetcher
from tayyari.apply.service import ApplyService
from tayyari.core.errors import BadRequest
from tayyari.core.settings import Settings
from tayyari.storage.ports import StoredApplication
from tests.fakes.apply import FakeApplyRepo
from tests.fakes.clock import FakeClock

T0 = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
OWNER = "u:usr1"


def _app(stage: str) -> StoredApplication:
    return StoredApplication(
        id="ap1", owner_key=OWNER, source="match", job_url="https://boards.greenhouse.io/acme/jobs/1",
        domain="boards.greenhouse.io", ats="greenhouse", stage=stage, created_at=T0, updated_at=T0, user_id="usr1",
    )  # fmt: skip


async def _service(make_settings: Callable[..., Settings], stage: str) -> tuple[ApplyService, FakeApplyRepo]:
    repo = FakeApplyRepo()
    await repo.create_application(_app(stage))
    fetcher = cast(PageFetcher, cast(Any, object()))  # never called by set_stage
    return ApplyService(repo, make_settings(), FakeClock(T0), fetcher), repo


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        ("waiting_for_you", "approved"),  # approve needs the review's digest
        ("check_this_one", "sent"),  # resolve
        ("check_this_one", "waiting_for_you"),  # resolve
        ("needs_signin", "sent"),  # "I applied there"
    ],
)
async def test_the_stage_route_refuses_moves_that_have_their_own_route(
    make_settings: Callable[..., Settings], frm: str, to: str
) -> None:
    svc, repo = await _service(make_settings, frm)
    with pytest.raises(BadRequest):
        await svc.set_stage(OWNER, "ap1", to, None)
    current = await repo.get_application("ap1", None)
    assert current is not None and current.stage == frm
    assert await repo.list_events("ap1") == []


async def test_the_stage_route_still_tracks_by_hand(make_settings: Callable[..., Settings]) -> None:
    svc, _ = await _service(make_settings, "sent")
    moved = await svc.set_stage(OWNER, "ap1", "interviewing", None)
    assert moved.stage == "interviewing"
