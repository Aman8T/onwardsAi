"""`parse_scan` over saved live scans (`SCAN_JS` results the experiment saved for its fictional test applicant, copied
from ``experiments/auto_apply/runs/*-B-*/fields.json`` to ``tests/data/apply/scans/``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tayyari.apply.driver import best_option, click_refused, shows
from tayyari.apply.forms import FormField
from tayyari.apply.scan import SCAN_JS, LiveScan, parse_scan, resume_name_inferable
from tests.fakes.apply_browser import FakeDriver

SCANS = Path(__file__).resolve().parents[2] / "data" / "apply" / "scans"


def saved(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((SCANS / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def field_by(scan: LiveScan, label_start: str) -> FormField:
    return next(f for f in scan.fields if f.label.startswith(label_start))


@pytest.mark.parametrize("name", ["greenhouse-cloudflare", "lever-spotify", "ashby-linear", "recruitee-bunq"])
def test_every_saved_scan_parses(name: str) -> None:
    raw = saved(name)
    scan = parse_scan([raw])
    assert scan.url == raw["url"] and scan.title == raw["title"]
    assert scan.has_form and scan.fields
    assert len({f.key for f in scan.fields}) == len(scan.fields)  # keys are unique
    assert all(f.frame == 0 for f in scan.fields)
    assert all(f.selector or f.synthetic for f in scan.fields)


def test_greenhouse_comboboxes_values_and_the_synthetic_resume_widget() -> None:
    scan = parse_scan([saved("greenhouse-cloudflare")])
    country = field_by(scan, "Country")
    assert country.kind == "combobox" and country.required and country.label == "Country"
    assert country.value == "+1" and country.key == "country" and country.id == "country"
    location = field_by(scan, "Location (City)")
    assert location.kind == "combobox" and location.value == "Pune, Maharashtra, India"
    assert field_by(scan, "Gender").kind == "combobox" and field_by(scan, "Gender").value == ""
    widget = field_by(scan, "Resume/CV")
    assert widget.synthetic and widget.selector == "" and widget.kind == "file" and widget.required
    assert widget not in scan.actionable
    cover = field_by(scan, "Cover Letter")
    assert cover.kind == "file" and not cover.synthetic and cover.accept == ".pdf,.doc,.docx,.txt,.rtf"
    tick = field_by(scan, "Acknowledge/Confirm")
    assert tick.kind == "checkbox" and tick.value == "checked" and tick.name == "question_70006084[]"
    assert scan.blockers.submit_texts == ("Apply", "Submit application")
    assert not scan.blockers.captcha_on_page and not scan.blockers.password_field


def test_lever_groups_selects_and_the_required_mark() -> None:
    scan = parse_scan([saved("lever-spotify")])
    pronouns = next(f for f in scan.fields if f.name == "pronouns")
    assert (
        pronouns.kind == "checkboxGroup"
        and pronouns.selector == '[name="pronouns"]'
        and pronouns.key == "pronouns"
    )
    assert [t for _, t in pronouns.options][:3] == ["He/him", "She/her", "They/them"]
    before = field_by(scan, "Have you ever been previously employed by Spotify?")
    assert before.kind == "checkboxGroup" and before.value == "No" and before.required  # the "✱" mark
    assert before.label == "Have you ever been previously employed by Spotify?"
    name = field_by(scan, "Full name")
    assert (
        name.required
        and name.label == "Full name"
        and name.key == "name"
        and name.selector == 'input[name="name"]'
    )
    where = next(f for f in scan.fields if f.kind == "select")
    assert (
        where.value == "India" and ("Select...", "Select...") not in where.options
    )  # the placeholder is dropped
    assert (
        where.options[0] == ("Afghanistan", "Afghanistan") and len(where.options) <= 80
    )  # the scanner keeps 80
    gender = field_by(scan, "What best describes your gender?")
    assert gender.kind == "radio" and gender.value == "Prefer not to disclose"
    assert scan.blockers.captcha_on_page  # a CAPTCHA script on the page (the form still filled)


def test_ashby_files_and_the_attached_resume() -> None:
    scan = parse_scan([saved("ashby-linear")])
    resume = next(f for f in scan.fields if f.key == "_systemfield_resume")
    assert resume.kind == "file" and resume.required and resume.value == "Jordan_Testcase_TEST_RESUME.pdf"
    assert resume.accept is not None and ".pdf" in resume.accept
    path_input = scan.fields[0]
    assert (
        path_input.selector.startswith("body > ") and path_input.key == path_input.selector
    )  # no id or unique name
    assert any(f.kind == "textarea" for f in scan.fields)


def test_recruitee_radio_groups_share_an_id_but_get_their_own_names_and_the_honeypot_is_dropped() -> None:
    raw = saved("recruitee-bunq")
    scan = parse_scan([raw])
    radios = [f for f in scan.fields if f.kind == "radio"]
    assert [f.selector for f in radios] == ['[name="q_4413543"]', '[name="q_4413554"]', '[name="q_4413556"]']
    assert radios[0].required and radios[0].label == "Have you previously worked at bunq?"  # "* (required)"
    assert not any("honeypot" in f.selector for f in scan.fields)
    assert len(scan.fields) == len(raw["fields"]) - 1
    assert any(f.kind == "tel" and f.value == "555 010 0142" for f in scan.fields)


def test_frames_are_kept_per_field_and_blockers_are_merged() -> None:
    main = {
        "url": "https://careers.acme.test/jobs/1",
        "title": "Engineer",
        "fields": [
            {
                "selector": '[id="q"]',
                "tag": "input",
                "type": "search",
                "label": "Search jobs",
                "required": False,
            }
        ],
        "blockers": {"captcha_on_page": False, "password_field": False, "submit_texts": ["Apply"]},
    }
    frame = {
        "url": "https://boards.greenhouse.io/embed/job_app?for=acme",
        "title": "",
        "fields": [
            {"selector": '[id="first_name"]', "tag": "input", "type": "text", "label": "First Name*", "required": True,
             "value": ""},
            {"selector": '[id="pw"]', "tag": "input", "type": "password", "label": "Password", "required": False},
            {"selector": '[id="loc"]', "tag": "input", "type": "month", "label": "Start", "required": False},
        ],
        "blockers": {"captcha_on_page": True, "password_field": True, "submit_texts": ["Submit application", "Apply"]},
        "frame": 2,
    }  # fmt: skip
    scan = parse_scan([main, frame])
    assert scan.url == "https://careers.acme.test/jobs/1"
    assert [(f.key, f.frame, f.kind) for f in scan.fields] == [("first_name", 2, "text"), ("loc", 2, "text")]
    assert scan.fields[0].label == "First Name" and scan.fields[0].required
    assert scan.blockers.captcha_on_page and scan.blockers.password_field
    assert scan.blockers.submit_texts == ("Apply", "Submit application")
    assert scan.has_form  # two fields to fill, both in the iframe
    assert not parse_scan([main]).has_form  # the site's own search box is not a form


def test_a_resume_chip_with_no_file_input_value_becomes_a_synthetic_field() -> None:
    raw = {
        "url": "https://jobs.lever.co/acme/1/apply",
        "fields": [
            {"selector": '[id="resume"]', "tag": "input", "type": "file", "label": "Resume", "value": ""}
        ],
        "blockers": {"resume_name_on_page": True},
    }
    scan = parse_scan([raw], resume_name="Asha_Rao.pdf")
    widget = scan.fields[-1]
    assert widget.synthetic and widget.value == "Asha_Rao.pdf" and widget.key == "widget:resume"
    assert len(parse_scan([raw]).fields) == 1  # with no resume name nothing is inferred


@pytest.mark.parametrize("name", ["resume.pdf", "Resume.pdf", "CV.docx", "my_resume.pdf", "Asha_Rao", ""])
def test_a_generic_resume_name_is_never_inferred_from_the_page(name: str) -> None:
    raw = {
        "url": "https://jobs.lever.co/acme/1/apply",
        "fields": [
            {
                "selector": '[id="resume"]',
                "tag": "input",
                "type": "file",
                "label": "Upload your resume",
                "value": "",
            }
        ],
        "blockers": {"resume_name_on_page": True},
    }
    scan = parse_scan([raw], resume_name=name)
    assert [f.key for f in scan.fields if f.synthetic] == []
    assert not resume_name_inferable(name)


def test_junk_entries_are_ignored() -> None:
    scan = parse_scan([{"url": 3, "fields": ["x", None, {"type": "text"}], "blockers": "no"}])
    assert (
        scan.url == "" and len(scan.fields) == 1 and scan.fields[0].synthetic
    )  # no selector: never actionable
    assert parse_scan([]).fields == []


def test_the_scanner_takes_the_resume_name_as_its_argument() -> None:
    assert SCAN_JS.startswith("(resumeName) =>") and "txt(resumeName)" in SCAN_JS
    assert "document.body.innerText.includes" not in SCAN_JS  # never the whole page
    assert "aria-hidden" in SCAN_JS and "checkbox-group" in SCAN_JS


# ── the driver rules every driver shares, and the fake driver over a saved scan ──


def test_click_refusals() -> None:
    for text in (
        "Submit application",
        "SUBMIT",
        "Send",
        "Finish",
        "Complete my application",
        "complete application",
    ):
        assert click_refused(text, form_on_screen=False)
    assert not click_refused("Apply for this job", form_on_screen=False)  # opens the form on a posting page
    assert click_refused("Apply for this job", form_on_screen=True)
    assert not click_refused("Reject all cookies", form_on_screen=True)
    assert not click_refused("Sender details", form_on_screen=True) and not click_refused(
        "Next", form_on_screen=True
    )


def test_best_option_and_shows() -> None:
    texts = ["Indonesia", "India", "British Indian Ocean Territory", "Yes - Intern"]
    assert best_option(texts, "india") == 1  # the same text beats a prefix
    assert best_option(texts, "Indo") == 0
    assert best_option(texts, "Ocean") == 2
    assert best_option(texts, "Yes") == 3
    assert best_option(texts, "Peru") is None and best_option(texts, "  ") is None
    assert shows("Pune, Maharashtra, India", "Pune") and shows("No", " no ")
    assert not shows("", "Pune") and not shows("Mumbai", "Pune")


async def test_the_fake_driver_scans_a_saved_scan_and_keeps_current_values() -> None:
    driver = FakeDriver(saved("recruitee-bunq"), url="https://careers.bunq.com/positions/ios-developer-4")
    await driver.goto("https://careers.bunq.com/positions/ios-developer-4")
    await driver.fill('[id="input-name-r2r"]', "Asha Rao")
    await driver.pick_in_group("q_4413543", "yes")
    with pytest.raises(LookupError):
        await driver.pick_in_group("q_4413543", "Maybe")
    with pytest.raises(LookupError):
        await driver.fill('[id="not-there"]', "x")
    scan = await driver.scan()
    assert field_by(scan, "What is your full name?").value == "Asha Rao"
    assert next(f for f in scan.fields if f.name == "q_4413543").value == "Yes"
    assert await driver.probe() == "BLOCKED"


async def test_the_fake_driver_refuses_submit_clicks_and_opens_a_form_behind_a_button() -> None:
    posting = '<html><body><a href="#">Apply for this job</a><button>Submit</button></body></html>'
    driver = FakeDriver(posting, reveals={"Apply for this job": saved("greenhouse-cloudflare")})
    assert await driver.buttons() == ["Apply for this job", "Submit"]
    assert await driver.click_text("Submit", form_on_screen=False) == "refused"
    assert await driver.click_text("Careers home", form_on_screen=False) == "not_found"
    assert await driver.click_text("Apply for this job", form_on_screen=False) == "clicked"
    assert (await driver.wait_for_form(5)).has_form
    assert await driver.click_text("Apply", form_on_screen=True) == "refused"
    assert driver.refusals == ["Submit", "Apply"] and driver.clicked_texts == ["Apply for this job"]


async def test_the_fake_driver_guard_blocks_writes_until_the_submit_hosts_are_allowed() -> None:
    page = '<html><body><form><input type="file" id="resume"><button type="submit">Submit</button></form></body></html>'
    url = "https://boards.greenhouse.io/acme/jobs/1"
    driver = FakeDriver(page, url=url, upload_url="https://boards.greenhouse.io/uploads")
    await driver.goto(url)
    await driver.upload('[id="resume"]', "cv.pdf", b"%PDF", "application/pdf")
    await driver.click("button[type=submit]")
    assert driver.sent_writes() == [] and not driver.submitted and driver.blocked_submits == 1
    assert ("POST", "https://boards.greenhouse.io/uploads") in driver.blocked
    driver.allow_submit_hosts(["boards.greenhouse.io"])
    await driver.click("button[type=submit]")
    assert driver.submitted and driver.sent_writes() == [("POST", url)]


async def test_the_fake_driver_clicks_the_submit_control_only_after_the_hosts_are_lifted() -> None:
    page = '<html><body><form><input type="text" id="name"><button type="submit">Submit</button></form></body></html>'
    url = "https://boards.greenhouse.io/acme/jobs/1"
    driver = FakeDriver(page, url=url)
    await driver.goto(url)
    with pytest.raises(PermissionError):
        await driver.click_submit("button[type=submit]")
    assert not driver.submitted
    driver.allow_submit_hosts(["boards.greenhouse.io"])
    await driver.click_submit("button[type=submit]")
    assert driver.submitted and driver.sent_writes() == [("POST", url)]
    with pytest.raises(ValueError):
        driver.allow_submit_hosts(["com.au"])
