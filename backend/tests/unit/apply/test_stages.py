"""The stage machine: who may move an application where."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tayyari.apply import stages
from tayyari.apply.stages import StageNotAllowed, allowed, move
from tayyari.storage.ports import StoredApplication
from tests.fakes.apply import FakeApplyRepo

T0 = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)

OLD_STAGES = ["needs_user", "ready_to_submit", "applied"]
"""The old branch's stage names: no longer stages at all."""


@pytest.mark.parametrize(
    ("frm", "to", "source", "ok"),
    [
        ("queued", "filling", "engine", True),
        ("queued", "sent", "engine", False),  # the engine never skips the review
        ("sent", "rejected", "engine", False),
        ("failed", "queued", "user", True),
        ("offer", "rejected", "user", True),
        ("queued", "waiting_for_you", "user", False),  # only the engine fills
        ("sent", "interviewing", "email", True),
        ("interviewing", "viewed", "email", False),  # email moves forward only
        ("viewed", "rejected", "email", True),
        ("offer", "rejected", "email", False),
        ("queued", "interviewing", "email", False),
        ("sent", "sent", "user", False),
        ("sent", "nonsense", "system", False),
    ],
)
def test_transitions(frm: str, to: str, source: str, ok: bool) -> None:
    assert allowed(frm, to, source) is ok  # type: ignore[arg-type]


@pytest.mark.parametrize("old", OLD_STAGES)
@pytest.mark.parametrize("source", ["engine", "user", "email", "system"])
def test_the_old_stage_names_are_gone(old: str, source: str) -> None:
    assert old not in stages.STAGES
    assert not allowed(old, "queued", source) and not allowed("queued", old, source)  # type: ignore[arg-type]
    assert not allowed("sent", old, source) and not allowed(old, "sent", source)  # type: ignore[arg-type]


ENGINE_MOVES = [
    ("queued", "filling"),
    ("filling", "waiting_for_you"),
    ("filling", "needs_signin"),
    ("filling", "closed"),
    ("filling", "failed"),
    ("filling", "queued"),  # an expired lease goes back to the queue
    ("approved", "submitting"),
    ("approved", "waiting_for_you"),  # the form changed since the review
    ("approved", "failed"),  # an error before the click
    ("submitting", "sent"),
    ("submitting", "check_this_one"),
    ("submitting", "failed"),
]
USER_MOVES = [
    *[
        (frm, "cancelled")
        for frm in ("queued", "filling", "waiting_for_you", "approved", "needs_signin", "failed", "closed")
    ],  # filling included: the worker's later move out of filling is a stale compare-and-set
    *[(frm, "queued") for frm in ("failed", "needs_signin", "closed")],
    (
        "waiting_for_you",
        "queued",
    ),  # the answers requeue: an answered row (and its siblings) goes back to the queue
    ("waiting_for_you", "approved"),
    ("check_this_one", "sent"),
    ("check_this_one", "failed"),  # resolve: it did not go through
    ("needs_signin", "sent"),  # applied on the employer's page
    ("sent", "viewed"),
    ("sent", "interviewing"),
    ("sent", "rejected"),
    ("offer", "rejected"),
    ("interviewing", "viewed"),  # by hand, the person knows best
]
EMAIL_MOVES = [
    ("check_this_one", "sent"),
    ("sent", "viewed"),
    ("viewed", "interviewing"),
    ("interviewing", "offer"),
    ("sent", "interviewing"),
    ("sent", "rejected"),
    ("viewed", "rejected"),
    ("interviewing", "rejected"),
]


@pytest.mark.parametrize(("frm", "to"), ENGINE_MOVES)
def test_the_engine_makes_every_section_3_move(frm: str, to: str) -> None:
    assert allowed(frm, to, "engine")


@pytest.mark.parametrize(("frm", "to"), USER_MOVES)
def test_the_person_makes_their_moves(frm: str, to: str) -> None:
    assert allowed(frm, to, "user")


@pytest.mark.parametrize(("frm", "to"), EMAIL_MOVES)
def test_an_email_makes_its_moves(frm: str, to: str) -> None:
    assert allowed(frm, to, "email")


@pytest.mark.parametrize(
    ("frm", "to", "source"),
    [
        ("queued", "waiting_for_you", "engine"),  # the engine fills before it asks
        ("waiting_for_you", "approved", "engine"),  # only the person approves
        ("waiting_for_you", "submitting", "engine"),  # nothing is sent without approval
        ("approved", "sent", "engine"),  # the submitting marker is committed before the click
        ("check_this_one", "submitting", "engine"),  # check_this_one is never retried
        ("check_this_one", "queued", "user"),
        ("approved", "submitting", "user"),
        ("sent", "cancelled", "user"),  # it already reached the employer
        ("check_this_one", "cancelled", "user"),
        ("queued", "approved", "user"),  # approve only what was reviewed
        ("waiting_for_you", "sent", "email"),
        ("viewed", "sent", "email"),  # forward only
        ("offer", "rejected", "email"),
        ("needs_signin", "sent", "email"),
        ("sent", "check_this_one", "user"),
        ("filling", "filling", "engine"),
    ],
)
def test_moves_that_are_refused(frm: str, to: str, source: str) -> None:
    assert not allowed(frm, to, source)  # type: ignore[arg-type]


@pytest.mark.parametrize("source", ["user", "email"])
@pytest.mark.parametrize("to", sorted(set(stages.STAGES) - {"submitting"}))
def test_only_the_engine_leaves_submitting(source: str, to: str) -> None:
    assert not allowed("submitting", to, source)  # type: ignore[arg-type]


def test_a_system_repair_may_leave_submitting() -> None:
    assert allowed("submitting", "check_this_one", "system")


@pytest.mark.parametrize("to", ["viewed", "interviewing", "offer", "rejected"])
@pytest.mark.parametrize(
    "frm", ["queued", "filling", "waiting_for_you", "needs_signin", "approved", "check_this_one", "failed", "closed",
            "cancelled"],
)  # fmt: skip
@pytest.mark.parametrize("source", ["user", "email"])
def test_tracking_stages_are_refused_before_sent(frm: str, to: str, source: str) -> None:
    assert not allowed(frm, to, source)  # type: ignore[arg-type]


def test_every_new_stage_is_in_the_machine() -> None:
    new = {
        "waiting_for_you",
        "needs_signin",
        "approved",
        "submitting",
        "sent",
        "check_this_one",
        "closed",
        "cancelled",
    }
    assert new <= set(stages.STAGES)
    assert stages.REOPENABLE <= set(stages.STAGES) and "submitting" not in stages.USER_CANCELLABLE
    assert stages.TRACKING == stages.SENT_AND_LATER and "sent" in stages.TRACKING


def _app(stage: str) -> StoredApplication:
    return StoredApplication(
        id="ap1", owner_key="u:usr1", source="match", job_url="https://boards.greenhouse.io/acme/jobs/1",
        domain="boards.greenhouse.io", ats="greenhouse", stage=stage, created_at=T0, updated_at=T0, user_id="usr1",
    )  # fmt: skip


async def test_move_writes_the_stage_and_its_history() -> None:
    repo = FakeApplyRepo()
    await repo.create_application(_app("approved"))
    app = await repo.get_application("ap1", None)
    assert app is not None
    moved = await move(
        repo, app, "submitting", source="engine", now=T0 + timedelta(minutes=1), note="Sending."
    )
    assert moved is not None and moved.stage == "submitting"
    events = await repo.list_events("ap1")
    assert [(e.source, e.from_stage, e.to_stage) for e in events] == [("engine", "approved", "submitting")]


async def test_a_stale_expected_stage_is_a_no_op() -> None:
    repo = FakeApplyRepo()
    await repo.create_application(_app("approved"))
    stale = await repo.get_application("ap1", None)
    assert stale is not None
    first = await move(repo, stale, "submitting", source="engine", now=T0)
    assert first is not None
    # another worker read the row while it was still approved: its move finds the stage changed and does nothing
    again = await move(repo, stale, "failed", source="engine", now=T0, note="Before the click.")
    assert again is None
    current = await repo.get_application("ap1", None)
    assert current is not None and current.stage == "submitting"
    assert [e.to_stage for e in await repo.list_events("ap1")] == ["submitting"]


async def test_a_refused_move_raises_and_writes_nothing() -> None:
    repo = FakeApplyRepo()
    await repo.create_application(_app("submitting"))
    app = await repo.get_application("ap1", None)
    assert app is not None
    with pytest.raises(StageNotAllowed):
        await move(repo, app, "cancelled", source="user", now=T0)
    current = await repo.get_application("ap1", None)
    assert current is not None and current.stage == "submitting" and await repo.list_events("ap1") == []


def test_the_answers_requeue_is_not_try_again() -> None:
    assert "waiting_for_you" not in stages.USER_RETRYABLE


def test_approve_resolve_and_i_applied_there_have_their_own_routes() -> None:
    assert {"approved", "sent", "waiting_for_you"} <= stages.ROUTED_TARGETS
    assert not (stages.ROUTED_TARGETS & (stages.SENT_AND_LATER - {"sent"}))


def test_resolve_settles_a_check_this_one_to_sent_or_failed_only() -> None:
    assert allowed("check_this_one", "sent", "user") and allowed("check_this_one", "failed", "user")
    assert not allowed(
        "check_this_one", "waiting_for_you", "user"
    )  # nothing is filled again from an unclear send
    assert not allowed("check_this_one", "queued", "user")
