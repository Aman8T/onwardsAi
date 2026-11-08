"""The built-in Greenhouse, Lever and Ashby playbooks and the label rules: the built-ins load (and survive a JSON round trip,
as stored playbooks do), the saved pages' fields get the right keys and operations, the labels recorded in the experiment's
traces (keys only) are placed, the never-stored rules come before every other label rule, and each ATS carries its hosts and
its success, error and closed signatures."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tayyari.apply.forms import FormField, scan_form
from tayyari.apply.playbook import (
    ASHBY,
    ATTESTATION_KEYS,
    BUILTIN,
    GREENHOUSE,
    LEVER,
    NEVER_STORED_RULE_KEYS,
    ON_SITE_KEYS,
    PROFILE_KEYS,
    FieldRule,
    Playbook,
    builtin_id,
    generic_rule_for,
    op_for,
    rule_for,
    scoped_key,
)
from tayyari.contract.apply import NEVER_STORED_KEYS
from tayyari.storage.ports import NEVER_STORED_ANSWER_KEYS

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"


def field(label: str, kind: str = "text", *, id: str | None = None, name: str | None = None) -> FormField:
    key = id or name or "f"
    selector = f'[id="{id}"]' if id else f'[name="{name}"]' if name else '[id="f"]'
    return FormField(key, selector, kind, label, True, name, id)  # type: ignore[arg-type]


def keys_of(page: str, playbook: Playbook) -> dict[str, str | None]:
    return {f.key: rule_for(f, playbook) for f in scan_form((DATA / page).read_text(encoding="utf-8")).fields}


def test_the_built_ins_load_and_survive_a_json_round_trip() -> None:
    assert set(BUILTIN) == {"greenhouse", "lever", "ashby"}
    for ats, pb in BUILTIN.items():
        assert pb.ats == ats and builtin_id(pb) == f"builtin:{ats}@1"
        again = Playbook.model_validate_json(pb.model_dump_json())
        assert again == pb
        for pattern in (
            pb.success_text,
            pb.error_text,
            pb.closed_text,
            pb.success_url or "x",
            *pb.read_only_posts,
        ):
            re.compile(pattern)
    # A playbook stored before this change (no hosts, no ops) still loads, with empty lists.
    old = Playbook.model_validate({"ats": "lever", "rules": [{"key": "expectedCtc", "name": "salary"}]})
    assert old.submit_hosts == [] and old.read_only_posts == [] and old.open_texts == []
    assert rule_for(field("Pay", name="salary"), old) == "expectedCtc"
    # the old branch's key names are gone: a rule that uses one is refused
    with pytest.raises(ValueError):
        Playbook.model_validate({"ats": "lever", "rules": [{"key": "salaryExpectation", "name": "salary"}]})


def test_greenhouse_page_fields_get_their_keys() -> None:
    assert keys_of("greenhouse_job.html", GREENHOUSE) == {
        "first_name": "firstName",
        "last_name": "lastName",
        "email": "email",
        "phone": "phone",
        "resume": "resume",
        "question_101": "linkedin",
        "question_102": None,
        "question_103": "needsSponsorship",
        "gender": "eeoGender",
    }


def test_lever_page_fields_get_their_keys() -> None:
    assert keys_of("lever_apply.html", LEVER) == {
        "resume-upload-input": "resume",
        "name": "fullName",
        "email": "email",
        "phone": "phone",
        "org": "currentCompany",
        "urls[LinkedIn]": "linkedin",
        "urls[GitHub]": "github",
        "cards[7a1b][field0]": "noticePeriod",
        "comments": None,
        "eeo[gender]": "eeoGender",
    }


def test_ashby_page_fields_get_their_keys() -> None:
    assert keys_of("ashby_application.html", ASHBY) == {
        "_systemfield_name": "fullName",
        "_systemfield_email": "email",
        "_systemfield_resume": "resume",
        "9c1d2e3f-0000-4000-8000-000000000001": "phone",
        "9c1d2e3f-0000-4000-8000-000000000002": "linkedin",
    }


# Labels recorded in experiments/auto_apply/runs/*-B-*/trace.json (labels and kinds only, never values).
TRACE_LABELS: list[tuple[Playbook, str, str, str | None]] = [
    (GREENHOUSE, "What is your current country of residence?", "combobox", "country"),
    (GREENHOUSE, "Will you now or in the future require sponsorship for a visa to remain in your current location?",
     "combobox", "needsSponsorship"),
    (GREENHOUSE, "Do you now or will you in the future require immigration sponsorship to work at Cloudflare", "combobox",
     "needsSponsorship"),
    (GREENHOUSE, "Are you subject to any employment agreements and/or post-employment restrictions with your current "
     "employer or a past employer?", "combobox", None),
    (GREENHOUSE, "Have you previously worked at or consulted for GitLab?", "combobox", None),
    (GREENHOUSE, "Do you reside in the United States or Canada?", "combobox", None),
    (GREENHOUSE, "How did you hear about this job?", "text", "howDidYouHear"),
    (GREENHOUSE, "Do you currently live or are you willing to relocate to the job's location?", "combobox", "relocation"),
    (GREENHOUSE, "Acknowledge/Confirm", "checkbox", "declaration"),
    (LEVER, "Current location", "text", "location"),
    (LEVER, "Portfolio URL", "text", "website"),
    (LEVER, "Have you ever been previously employed by Spotify?", "checkboxGroup", None),
    (LEVER, "What best describes your gender?", "radio", "eeoGender"),
    (LEVER, "Language Skill(s) (Check all that apply)", "checkboxGroup", "languages"),
    (LEVER, "Please tell us how you heard about this opportunity.", "select", "howDidYouHear"),
    (LEVER, "Are you legally authorized to work in the country for which you are applying?", "radio",
     "workAuthorization"),
    (LEVER, "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B, etc.)?", "radio",
     "needsSponsorship"),
    (LEVER, "Are you a Singapore citizen?", "radio", "workAuthorization"),
    (ASHBY, "What country are you based in?", "text", "country"),
    (ASHBY, "Notice Period", "text", "noticePeriod"),
    (ASHBY, "Cover letter", "textarea", "coverLetter"),
    (ASHBY, "Github", "text", "github"),
    (ASHBY, "What are you extremely good at? What could you teach the team?", "textarea", None),
    (ASHBY, "Have you contributed to a mobile app(s) and/or several features that reached a large number of users?",
     "textarea", None),
    (ASHBY, "Name Pronunciation | How do you pronounce your name?", "text", None),
    (ASHBY, "Has someone at Linear referred you for this role? If so, please include their name here:", "text",
     "referral"),
    (GREENHOUSE, "Are you Hispanic/Latino?", "combobox", "eeoRace"),
    (LEVER, "Are you a Singapore permanent resident?", "radio", "workAuthorization"),
    (GREENHOUSE, "Phone", "text", "phone"),
    (GREENHOUSE, "Mobile Number", "text", "phone"),
]  # fmt: skip


@pytest.mark.parametrize(("pb", "label", "kind", "expected"), TRACE_LABELS)
def test_labels_from_the_recorded_traces_are_placed(
    pb: Playbook, label: str, kind: str, expected: str | None
) -> None:
    assert rule_for(field(label, kind, id="x1"), pb) == expected


INDIAN_FORM_LABELS: list[tuple[str, str, str | None]] = [
    ("PAN card number", "text", "pan"),
    ("Aadhaar number", "text", "aadhaar"),
    ("Bank account number", "text", "bankAccount"),
    ("IFSC code", "text", "ifsc"),
    ("Passport number", "text", "passportNumber"),
    ("Category (General/OBC/SC/ST)", "select", "casteCategory"),
    ("Religion", "select", "religion"),
    ("Marital status", "select", "maritalStatus"),
    ("Father's name", "text", "fatherName"),
    ("Signature", "text", "signature"),
    ("I hereby declare that the information above is true", "checkbox", "declaration"),
    ("I agree to the privacy policy", "checkbox", "consent"),
    ("Do you know anyone working at Acme?", "radio", "knowsSomeoneHere"),
    ("Referred by (employee name)", "text", "referral"),
    ("Have you previously applied to Acme?", "radio", "previousApplication"),
    ("Name as per PAN", "text", "fullName"),  # the name, not the number
    ("Are you open to pan India relocation?", "radio", "relocation"),  # not a PAN
    ("Which college? (e.g. St. Xavier's)", "text", "college"),  # "St." is not a caste category
    ("Current CTC (in LPA)", "number", "currentCtc"),
    ("Expected CTC", "text", "expectedCtc"),
    ("Salary expectation", "text", "expectedCtc"),
    ("Are you serving notice period?", "radio", "servingNotice"),
    ("Last working day", "date", "lastWorkingDay"),
    ("Notice period", "select", "noticePeriod"),
    ("Total years of experience", "text", "totalExperience"),
    (
        "How many years of experience do you have with Python?",
        "text",
        None,
    ),  # a skill question, for the facts
    ("Date of birth", "date", "dob"),
    ("Highest qualification", "select", "highestQualification"),
    ("College / University", "text", "college"),
    ("Year of passing", "text", "graduationYear"),
    ("Graduation CGPA", "text", "graduationScore"),
    ("Class 12 percentage", "text", "class12Score"),
    ("10th CGPA", "text", "class10Score"),
    ("Active backlogs", "number", "activeBacklogs"),
    ("Any backlogs?", "radio", "backlogsEver"),
    ("Any gap of 6 months or more in education or career?", "radio", "gap6m"),
    ("Languages you can work in", "text", "languages"),
    ("Which programming languages do you know?", "text", None),
    ("Preferred locations", "text", "preferredCities"),
    ("Preferred work mode", "select", "workMode"),
    ("Are you comfortable with rotational shifts?", "radio", "shifts"),
    ("Are you okay with a 1 year service bond?", "radio", "bond"),
    ("Willing to relocate?", "radio", "relocation"),
    ("Are you authorised to work in India?", "radio", "workAuthorization"),
    ("Phone country code", "select", "phoneCountry"),
    ("Pronouns", "text", "pronouns"),
]


@pytest.mark.parametrize(("label", "kind", "expected"), INDIAN_FORM_LABELS)
def test_indian_form_and_never_stored_labels(label: str, kind: str, expected: str | None) -> None:
    assert generic_rule_for(field(label, kind)) == expected


def test_the_never_stored_keys_mirror_the_contract_and_the_answer_bank() -> None:
    assert NEVER_STORED_RULE_KEYS == NEVER_STORED_KEYS == NEVER_STORED_ANSWER_KEYS
    assert ATTESTATION_KEYS == {"signature", "declaration", "consent"} and not ATTESTATION_KEYS & ON_SITE_KEYS
    assert {"expectedCtc", "currentCtc", "noticePeriod", "bond", "relocation", "class12Score"} <= set(
        PROFILE_KEYS
    )


def test_a_never_stored_rule_beats_a_playbook_label_rule() -> None:
    pb = Playbook(ats="x", rules=[FieldRule(key="phone", label=r"number")])
    assert rule_for(field("Aadhaar linked mobile number"), pb) == "aadhaar"
    assert rule_for(field("Mobile number"), pb) == "phone"
    exact = Playbook(ats="x", rules=[FieldRule(key="email", id="e1")])
    assert rule_for(field("Anything at all", id="e1"), exact) == "email"  # an exact id rule wins


def test_country_keys_are_scoped_and_the_old_names_are_gone() -> None:
    assert scoped_key("workAuthorization", "in") == "workAuthorization:IN"
    assert not {"salaryExpectation", "willingToRelocate"} & set(PROFILE_KEYS)


def test_widgets_get_their_operations() -> None:
    assert (
        op_for(field("Country", "combobox", id="country"), GREENHOUSE) == "choose"
    )  # the phone-country picker
    assert rule_for(field("Country", "combobox", id="country"), GREENHOUSE) == "phoneCountry"
    assert op_for(field("Location (City)", "combobox", id="candidate-location"), GREENHOUSE) == "choose"
    assert (
        op_for(field("Do you need a visa?", "combobox", id="question_9"), GREENHOUSE) == "choose"
    )  # react-select
    assert (
        op_for(field("Current location", "text", name="location"), LEVER) == "choose"
    )  # the location typeahead
    assert op_for(field("Resume/CV", "file", id="resume-upload-input", name="resume"), LEVER) == "upload"
    assert op_for(field("Languages", "checkboxGroup", name="cards[a][field0]"), LEVER) == "group"
    assert op_for(field("Why us?", "textarea", id="q"), ASHBY) == "fill"
    assert op_for(field("Pick one", "select", id="q"), ASHBY) == "select"


def test_each_ats_carries_hosts_and_signatures() -> None:
    for pb in (GREENHOUSE, LEVER, ASHBY):
        assert pb.submit_hosts and pb.upload_hosts and isinstance(pb.read_only_posts, list)
        assert all("/" not in host and "." in host for host in (*pb.submit_hosts, *pb.upload_hosts))
        assert pb.notes
    assert "job-boards.greenhouse.io" in GREENHOUSE.submit_hosts and "jobs.lever.co" in LEVER.submit_hosts
    assert "jobs.ashbyhq.com" in ASHBY.submit_hosts
    # Ashby renders through two read-only GraphQL operations; its submit operation stays blocked.
    graphql = "https://jobs.ashbyhq.com/api/non-user-graphql?op="
    allowed = [u for u in (graphql + "ApiJobPosting", graphql + "ApiOrganizationFromHostedJobsPageName",
                           graphql + "ApiSubmitSingleApplicationFormAction")
               if any(re.match(rx, u) for rx in ASHBY.read_only_posts)]  # fmt: skip
    assert allowed == [graphql + "ApiJobPosting", graphql + "ApiOrganizationFromHostedJobsPageName"]
    assert GREENHOUSE.read_only_posts == [] and LEVER.read_only_posts == []
    samples = [
        (GREENHOUSE, "Thank you for applying to Acme!", "This field is required", "The job you are looking for is no longer open."),
        (LEVER, "Application submitted!", "Please fill out this field", "This posting is no longer accepting applications"),
        (ASHBY, "Thanks for applying", "Missing entry for required field", "Job not found"),
    ]  # fmt: skip
    for pb, success, error, closed in samples:
        assert re.search(pb.success_text, success, re.I), pb.ats
        assert re.search(pb.error_text, error, re.I), pb.ats
        assert re.search(pb.closed_text, closed, re.I), pb.ats
        assert not re.search(pb.success_text, error, re.I)
    assert GREENHOUSE.success_url and re.search(
        GREENHOUSE.success_url, "https://job-boards.greenhouse.io/acme/confirmation"
    )
    assert LEVER.success_url and re.search(LEVER.success_url, "https://jobs.lever.co/acme/123/thanks")
