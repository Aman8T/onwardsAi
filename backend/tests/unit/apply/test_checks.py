"""The start checks: one case per reason, in their fixed order, the reopen of a cancelled, closed or failed application,
and the offer when every check passes. `evaluate_start` is pure, so the cases are plain data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tayyari.apply.checks import FILLABLE_ATS, NOTES, evaluate_start, fillable, reopens
from tayyari.apply.intake import Fingerprint

GH = Fingerprint("greenhouse", "https://job-boards.greenhouse.io/acme/jobs/4020305008", "acme", "4020305008")


@dataclass(frozen=True, slots=True)
class Earlier:
    id: str
    stage: str


def ok(**changes: Any) -> dict[str, Any]:
    """Arguments under which every check passes, with ``changes`` applied."""
    args: dict[str, Any] = {
        "signed_in": True,
        "manual_only": False,
        "fp": GH,
        "liveness": "live",
        "blocked": False,
        "gate_reasons": (),
        "existing": None,
        "credit_ok": True,
    }
    return {**args, **changes}


def test_offered_when_every_check_passes() -> None:
    got = evaluate_start(**ok())
    assert got.offered and got.reason is None and got.note is None
    assert got.host == "job-boards.greenhouse.io" and got.ats == "greenhouse"
    assert evaluate_start(**ok(host="careers.acme.com")).host == "careers.acme.com"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"signed_in": False}, "signed_out"),
        ({"manual_only": True, "fp": Fingerprint("unknown")}, "manual_only"),
        ({"fp": Fingerprint("workday")}, "own_account_ats"),
        ({"fp": Fingerprint("unknown", "https://acme.dev/apply")}, "not_fillable"),
        ({"fp": Fingerprint("greenhouse", None, None, "4567")}, "not_fillable"),
        ({"liveness": "closed"}, "closed"),
        ({"liveness": "unknown"}, "unknown_liveness"),
        ({"liveness": None}, "unknown_liveness"),
        ({"blocked": True, "gate_reasons": ("5 years of Go",)}, "blocked_by_requirement"),
        ({"existing": Earlier("app_1", "waiting_for_you")}, "already_applied"),
        ({"credit_ok": False}, "no_credit"),
    ],
)
def test_each_reason_on_its_own(changes: dict[str, Any], reason: str) -> None:
    got = evaluate_start(**ok(**changes))
    assert not got.offered and got.reason == reason
    assert got.reason is not None and got.note == NOTES[got.reason]


ORDER: list[tuple[str, dict[str, Any]]] = [
    ("signed_out", {"signed_in": False}),
    ("manual_only", {"manual_only": True}),
    ("own_account_ats", {"fp": Fingerprint("workday")}),
    ("not_fillable", {"fp": Fingerprint("lever")}),
    ("closed", {"liveness": "closed"}),
    ("unknown_liveness", {"liveness": "unknown"}),
    ("blocked_by_requirement", {"blocked": True, "gate_reasons": ["Bengaluru office"]}),
    ("already_applied", {"existing": Earlier("app_1", "sent")}),
    ("no_credit", {"credit_ok": False}),
]
"""The reasons in their order, each with the change that fails it."""


@pytest.mark.parametrize("first", range(len(ORDER)))
def test_the_reasons_are_checked_in_a_fixed_order(first: int) -> None:
    """Every check from ``first`` on fails at once (the liveness and fingerprint keys keep the earliest failing value,
    since two reasons share each); the answer is the reason at ``first``."""
    args = ok()
    for _, failing in reversed(ORDER[first:]):
        args.update(failing)
    got = evaluate_start(**args)
    assert got.reason == ORDER[first][0]


def test_the_refusals_carry_what_the_card_needs() -> None:
    blocked = evaluate_start(**ok(blocked=True, gate_reasons=["5 years of Go", "Bengaluru office"]))
    assert blocked.gateReasons == ["5 years of Go", "Bengaluru office"]
    applied = evaluate_start(**ok(existing=Earlier("app_9", "sent")))
    assert applied.applicationId == "app_9" and applied.host == "job-boards.greenhouse.io"
    manual = evaluate_start(**ok(manual_only=True, fp=Fingerprint("unknown"), host="linkedin.com"))
    assert manual.host == "linkedin.com" and manual.ats is None
    workday = evaluate_start(**ok(fp=Fingerprint("workday"), host="acme.wd5.myworkdayjobs.com"))
    assert workday.ats == "workday" and workday.host == "acme.wd5.myworkdayjobs.com"
    unchecked = evaluate_start(**ok(liveness="unknown"))
    assert unchecked.host == "job-boards.greenhouse.io"  # "Open on the employer's site" needs the host


@pytest.mark.parametrize("stage", ["cancelled", "closed", "failed"])
def test_a_cancelled_closed_or_failed_application_reopens(stage: str) -> None:
    earlier = Earlier("app_1", stage)
    assert reopens(earlier)
    got = evaluate_start(**ok(existing=earlier))
    assert got.offered and got.applicationId is None
    assert evaluate_start(**ok(existing=earlier, credit_ok=False)).reason == "no_credit"


@pytest.mark.parametrize(
    "stage",
    [
        "queued",
        "filling",
        "waiting_for_you",
        "needs_signin",
        "approved",
        "submitting",
        "sent",
        "check_this_one",
        "offer",
    ],
)
def test_any_other_earlier_application_blocks(stage: str) -> None:
    assert not reopens(Earlier("app_1", stage))
    got = evaluate_start(**ok(existing=Earlier("app_1", stage)))
    assert got.reason == "already_applied" and got.applicationId == "app_1"


def test_fillable_means_an_ats_we_fill_and_a_form_address() -> None:
    assert FILLABLE_ATS == {"greenhouse", "lever", "ashby"}
    assert fillable(GH)
    assert not fillable(Fingerprint("lever"))
    assert not fillable(Fingerprint("workday", "https://acme.wd5.myworkdayjobs.com/apply"))
    assert not reopens(None)
