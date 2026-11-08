"""The start checks behind "Apply for me" (spec 2.1 and 3.1 step 1), as one pure function.

`evaluate_start` takes only what was already fetched or read (the sign-in, the intake's fingerprint, the liveness
check, the match's gate verdict, an earlier application to the same posting, the credit check) and returns the
`ApplyCheck` the card shows. It reads nothing, writes nothing and charges nothing, so ``POST /api/apply/check`` and the
start itself run the same checks. The reasons are checked in a fixed order and the first that fails is the answer:

    signed_out              auto-apply is for signed-in people only (CLAUDE.md rule 9)
    manual_only             the link is on a board whose terms forbid automation: never opened (rule 4)
    own_account_ats         the ATS needs the person's own account (Workday): the employer's link is the action
    not_fillable            an ATS we do not fill, or no form address
    closed                  the ATS answered that the job is gone: nothing is offered or charged
    unknown_liveness        the posting could not be checked: the employer's site is offered, never "closed"
    blocked_by_requirement  the match's hard requirements blocked (with the gate's reasons)
    already_applied         an earlier application to this posting (with its id), unless it was cancelled, closed or
                            failed, which the start reopens
    no_credit               no free application and no credit left
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol

from tayyari.apply.intake import Fingerprint, Liveness, host_of
from tayyari.contract.apply import ApplyBlockReason, ApplyCheck, AtsKind

FILLABLE_ATS: Final[frozenset[AtsKind]] = frozenset({"greenhouse", "lever", "ashby"})
"""The ATSs whose forms auto-apply fills."""
OWN_ACCOUNT_ATS: Final[frozenset[AtsKind]] = frozenset({"workday"})
"""ATSs that need the person's own account on the employer's site: never filled for them."""
REOPENABLE_STAGES: Final[frozenset[str]] = frozenset({"cancelled", "closed", "failed"})
"""An earlier application in one of these stages does not block: a new start reopens that row with a new hold."""

NOTES: Final[dict[ApplyBlockReason, str]] = {
    "signed_out": "Sign in to apply from here.",
    "manual_only": "This site does not allow automated applications. Apply on the site yourself.",
    "own_account_ats": "This employer's site asks you to apply with your own account there.",
    "not_fillable": "We can't fill this employer's form yet. Apply on their site.",
    "closed": "This posting is no longer open.",
    "unknown_liveness": "We couldn't check this posting just now. Apply on the employer's site.",
    "blocked_by_requirement": "A hard requirement of this role isn't met yet.",
    "already_applied": "You already have an application for this posting.",
    "no_credit": "You have no applications left.",
}
"""One plain line per reason, written by code; the card may show its own wording instead."""


class EarlierApplication(Protocol):
    """What the checks read of an earlier application to the same posting (`storage.ports.StoredApplication`)."""

    @property
    def id(self) -> str: ...

    @property
    def stage(self) -> str: ...


def reopens(existing: EarlierApplication | None) -> bool:
    """Does a start on this posting reopen the earlier application (cancelled, closed or failed) instead of creating
    a new one?"""
    return existing is not None and existing.stage in REOPENABLE_STAGES


def fillable(fp: Fingerprint) -> bool:
    """An ATS we fill, and a form address to open."""
    return fp.ats in FILLABLE_ATS and bool(fp.apply_url)


def evaluate_start(
    *,
    signed_in: bool,
    manual_only: bool,
    fp: Fingerprint,
    liveness: Liveness | None,
    blocked: bool,
    gate_reasons: Sequence[str] = (),
    existing: EarlierApplication | None,
    credit_ok: bool,
    host: str | None = None,
) -> ApplyCheck:
    """The start verdict from data already in hand (no fetch, no write, no charge).

    ``liveness`` None means it was not checked, which counts as ``unknown_liveness`` once that check is reached.
    ``host`` is the employer link's host for "or apply yourself on <host>"; by default the form address's host.
    """
    shown_host = host or (host_of(fp.apply_url) if fp.apply_url else None) or None
    ats: AtsKind | None = None if manual_only else fp.ats

    def refuse(reason: ApplyBlockReason, **extra: object) -> ApplyCheck:
        return ApplyCheck.model_validate(
            {
                "offered": False,
                "reason": reason,
                "host": shown_host,
                "ats": ats,
                "note": NOTES[reason],
                **extra,
            }
        )

    if not signed_in:
        return refuse("signed_out")
    if manual_only:
        return refuse("manual_only")
    if fp.ats in OWN_ACCOUNT_ATS:
        return refuse("own_account_ats")
    if not fillable(fp):
        return refuse("not_fillable")
    if liveness == "closed":
        return refuse("closed")
    if liveness != "live":
        return refuse("unknown_liveness")
    if blocked:
        return refuse("blocked_by_requirement", gateReasons=list(gate_reasons))
    if existing is not None and not reopens(existing):
        return refuse("already_applied", applicationId=existing.id)
    if not credit_ok:
        return refuse("no_credit")
    return ApplyCheck(offered=True, host=shown_host, ats=ats)


__all__ = [
    "FILLABLE_ATS",
    "NOTES",
    "OWN_ACCOUNT_ATS",
    "REOPENABLE_STAGES",
    "EarlierApplication",
    "evaluate_start",
    "fillable",
    "reopens",
]
