"""The stage machine: who may move an application where."""

from __future__ import annotations

import pytest

from tayyari.apply.stages import allowed


@pytest.mark.parametrize(
    ("frm", "to", "source", "ok"),
    [
        ("queued", "filling", "engine", True),
        ("filling", "ready_to_submit", "engine", True),
        ("ready_to_submit", "applied", "engine", True),
        ("queued", "applied", "engine", False),  # the engine never skips the review
        ("needs_user", "applied", "engine", False),
        ("applied", "rejected", "engine", False),
        ("needs_user", "queued", "user", True),
        ("failed", "queued", "user", True),
        ("applied", "queued", "user", False),
        ("needs_user", "applied", "user", True),  # applied on the site by hand
        ("offer", "rejected", "user", True),
        ("queued", "ready_to_submit", "user", False),  # only the engine fills
        ("applied", "interviewing", "email", True),
        ("interviewing", "viewed", "email", False),  # email moves forward only
        ("viewed", "rejected", "email", True),
        ("offer", "rejected", "email", False),
        ("needs_user", "applied", "email", True),
        ("queued", "interviewing", "email", False),
        ("applied", "applied", "user", False),
        ("applied", "nonsense", "system", False),
    ],
)
def test_transitions(frm: str, to: str, source: str, ok: bool) -> None:
    assert allowed(frm, to, source) is ok  # type: ignore[arg-type]
