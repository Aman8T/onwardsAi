"""Check the form as the browser holds it, never as a step said it went (spec 3.2; RESULTS 7.7: agent self-reports
overclaim). Ported from ``experiments/auto_apply/score.correct``.

`value_matches` says whether a field's live value shows what was planned, allowing for what widgets do to a value: a phone
mask (compared by its last 7 digits), a phone-country picker that shows the dial code (``India`` -> ``+91``), a combobox or
typeahead that adds a region (``Pune`` -> ``Pune, Maharashtra, India``), case and spacing. `verify` compares a whole plan
with a fresh scan: the planned fields that did not stick, and the required fields that are still empty. In the preview the
resume is left out: it is attached only in the submit pass (spec 1 rule 3).

Pure code, no browser and no model.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Final

from tayyari.apply.answers import Plan
from tayyari.apply.driver import OpenMode, shows
from tayyari.apply.forms import FormField
from tayyari.apply.scan import LiveScan

_DIGITS: Final = re.compile(r"\D")
_PHONE_SHAPE: Final = re.compile(r"^\+?[\d\s().\-]{7,}$")
_DIAL: Final = re.compile(r"^\s*\+\s*(\d{1,4})\b")
_YES: Final = frozenset({"yes", "y", "true", "1", "checked", "on"})
_CHOICE_KINDS: Final = frozenset({"select", "radio", "combobox", "checkboxGroup"})

DIAL_COUNTRIES: Final[dict[str, tuple[str, ...]]] = {
    "1": ("united states", "usa", "us", "canada"),
    "91": ("india",),
    "44": ("united kingdom", "uk", "great britain"),
    "65": ("singapore",),
    "49": ("germany",),
    "61": ("australia",),
    "971": ("united arab emirates", "uae"),
    "353": ("ireland",),
    "31": ("netherlands",),
    "33": ("france",),
    "81": ("japan",),
    "34": ("spain",),
    "46": ("sweden",),
    "41": ("switzerland",),
    "48": ("poland",),
    "64": ("new zealand",),
    "972": ("israel",),
}
"""A dial code -> the country names a phone-country picker may stand for."""


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _digits(text: str) -> str:
    return _DIGITS.sub("", text)


def _phone_like(text: str) -> bool:
    return bool(_PHONE_SHAPE.match(text.strip())) and len(_digits(text)) >= 7


def _same_phone(planned: str, live: str) -> bool:
    a, b = _digits(planned), _digits(live)
    if not a or not b:
        return False
    return a.endswith(b[-7:]) or b.endswith(a[-7:])


def _dial_country(planned: str, live: str) -> bool:
    """A phone-country picker: one side shows a dial code (``+91``), the other the country (``India``) or the code."""
    for code_side, other in ((live, planned), (planned, live)):
        match = _DIAL.match(code_side)
        if match is None:
            continue
        code = match.group(1)
        other_match = _DIAL.match(other)
        if other_match is not None:
            return other_match.group(1) == code
        folded = _fold(other)
        return any(re.match(rf"{re.escape(name)}\b", folded) for name in DIAL_COUNTRIES.get(code, ()))
    return False


def value_matches(kind: str, planned: str, live: str) -> bool:
    """True when the live value shows the planned one. ``kind`` is the field's `FieldKind`."""
    want, got = planned.strip(), live.strip()
    if not want:
        return not got
    if not got:
        return False
    if kind == "checkbox":
        return _fold(got) in _YES and _fold(want) in _YES
    if kind == "tel" or (kind in ("text", "combobox") and _phone_like(want)):
        # A full planned number is compared by its digits: a mask that kept only the dial code (``+91``) did not keep
        # the number. The dial-code picker below is for a planned country or a bare code.
        return (kind == "tel" or _phone_like(got)) and _same_phone(want, got)
    if _dial_country(want, got):
        return True
    if kind in _CHOICE_KINDS:
        if kind == "checkboxGroup":
            picked = [p for p in (s.strip() for s in got.split(";")) if p]
            return any(shows(p, want) for p in picked)
        return shows(got, want)
    return _fold(want) == _fold(got)


@dataclass(slots=True)
class VerifyResult:
    missing_required: list[str] = field(default_factory=list)
    """Keys of required fields (live, actionable) that are still empty."""
    not_stuck: list[str] = field(default_factory=list)
    """Keys of planned fields whose live value does not show the planned one (or that are gone from the page)."""

    @property
    def ok(self) -> bool:
        return not self.missing_required and not self.not_stuck


def find_live(scan: LiveScan, item: FormField) -> FormField | None:
    """The live field a planned one refers to: the same selector, else the same key, else the same id or name."""
    live = scan.actionable
    tests: tuple[Callable[[FormField], bool], ...] = (
        lambda f: bool(item.selector) and f.selector == item.selector,
        lambda f: f.key == item.key,
        lambda f: bool(item.id) and f.id == item.id,
        lambda f: bool(item.name) and f.name == item.name and f.kind == item.kind,
    )
    for test in tests:
        hit = next((f for f in live if test(f)), None)
        if hit is not None:
            return hit
    return None


def verify(plan: Plan, scan: LiveScan, mode: OpenMode, *, skip: Collection[str] = ()) -> VerifyResult:
    """Compare ``plan`` with a fresh ``scan``. In ``preview`` the resume's own input is left out of the empty-required
    list (it is never attached before approval). ``skip`` names field keys to leave out (the questions for the person)."""
    out = VerifyResult()
    for planned in plan.filled:
        if planned.item.key in skip:
            continue
        live = find_live(scan, planned.item)
        if live is None or not value_matches(planned.item.kind, planned.value, live.value):
            out.not_stuck.append(planned.item.key)
    resume = plan.resume
    for item in scan.actionable:
        if not item.required or item.value.strip() or item.key in skip:
            continue
        if mode == "preview" and resume is not None and item.kind == "file" and item.key == resume.key:
            continue
        out.missing_required.append(item.key)
    return out


__all__ = ["DIAL_COUNTRIES", "VerifyResult", "find_live", "value_matches", "verify"]
