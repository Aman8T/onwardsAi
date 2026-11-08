"""The DOM check: a value counts as filled only when the live form shows it, allowing for what widgets do to a value."""

from __future__ import annotations

from tayyari.apply.answers import Plan, Planned, ResumePlan
from tayyari.apply.forms import FormField
from tayyari.apply.scan import LiveScan
from tayyari.apply.verify import find_live, value_matches, verify
from tayyari.contract.apply import FieldKind


def test_a_phone_is_compared_by_its_last_seven_digits() -> None:
    assert value_matches("tel", "+91 98765 43210", "098765 43210")
    assert value_matches("tel", "+91 98765 43210", "(987) 654-3210")
    assert not value_matches("tel", "+91 98765 43210", "+91 98765 43999")
    assert value_matches("text", "+91 98765 43210", "9876543210")  # a phone typed into a plain text box
    assert not value_matches("tel", "+91 98765 43210", "")
    # A mask that kept only the dial code did not keep the number, whatever the field's kind
    assert not value_matches("text", "+91 98765 43210", "+91")
    assert not value_matches("combobox", "+91 98765 43210", "+91")
    assert not value_matches("text", "+1 415 555 0100", "+1")


def test_a_phone_country_picker_may_show_the_dial_code() -> None:
    assert value_matches("combobox", "India", "+91")
    assert value_matches("combobox", "+91", "India")
    assert value_matches("combobox", "+44", "+44 United Kingdom")
    assert not value_matches("combobox", "India", "+1")
    assert not value_matches("combobox", "Ukraine", "+44")  # "uk" is a whole word, not a prefix


def test_a_combobox_or_typeahead_may_add_a_region() -> None:
    assert value_matches("combobox", "Pune", "Pune, Maharashtra, India")
    assert value_matches("select", "Decline To Self Identify", "decline to self identify")
    assert not value_matches("combobox", "Pune", "Mumbai, Maharashtra, India")
    assert value_matches("checkboxGroup", "Python", "SQL; Python")
    assert not value_matches("checkboxGroup", "Go", "SQL; Python")


def test_text_is_compared_with_case_and_spacing_folded() -> None:
    assert value_matches("text", "Riya  Kapoor", "riya kapoor")
    assert not value_matches("text", "Riya Kapoor", "Riya K")
    assert value_matches("checkbox", "yes", "checked") and not value_matches("checkbox", "yes", "")
    assert value_matches("text", "", "") and not value_matches("text", "", "something")
    assert not value_matches("date", "2026-11-08", "2025-11-08")  # a date is never read as a phone


def _field(key: str, kind: FieldKind = "text", *, required: bool = True, value: str = "") -> FormField:
    return FormField(
        key=key, selector=f'[id="{key}"]', kind=kind, label=key, required=required, id=key, value=value
    )


def test_verify_lists_what_did_not_stick_and_the_required_fields_still_empty() -> None:
    name, phone, city = _field("name"), _field("phone", "tel"), _field("city", "combobox")
    plan = Plan(filled=[Planned(name, "Riya Kapoor", "details"), Planned(phone, "+91 98765 43210", "details"),
                        Planned(city, "Pune", "details")])  # fmt: skip
    live = LiveScan(
        "https://x",
        [
            _field("name", value="Riya Kapoor"),
            _field("phone", "tel", value="98765 43210"),
            _field("city", "combobox", value=""),  # the pick did not stick
            _field("why", "textarea"),  # required and nobody filled it
            _field("resume", "file"),
        ],
    )
    result = verify(plan, live, "submit")
    assert result.not_stuck == ["city"]
    assert result.missing_required == ["city", "why", "resume"]
    assert not result.ok
    assert verify(plan, live, "submit", skip={"why", "city"}).missing_required == ["resume"]


def test_the_resume_is_left_out_in_the_preview_only() -> None:
    plan = Plan(resume=ResumePlan("Riya-Kapoor.pdf", "0" * 64, "application/pdf", "resume", '[id="resume"]'))
    live = LiveScan("https://x", [_field("resume", "file"), _field("cover", "file")])
    assert verify(plan, live, "preview").missing_required == ["cover"]
    assert verify(plan, live, "submit").missing_required == ["resume", "cover"]


def test_a_field_gone_from_the_page_did_not_stick_and_synthetic_fields_are_never_matched() -> None:
    gone = _field("old")
    plan = Plan(filled=[Planned(gone, "x", "details")])
    widget = FormField(
        key="old", selector="", kind="file", label="old", required=False, synthetic=True, value="x"
    )
    live = LiveScan("https://x", [widget])
    assert find_live(live, gone) is None
    assert verify(plan, live, "preview").not_stuck == ["old"]
