"""The code-only answer rules (spec 1 rule 7 and 2.2): per-country work authorisation, the self-ID default, never-stored
fields, the Indian-form rules, question keys, the digest with the resume's hash and no upload, and the Claude answerer's
spend handling. Everything runs on hand-built fields and fakes; nothing calls a model or a browser."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from tayyari.apply.answers import (
    NO_MODEL_KEYS,
    BankEntry,
    ClaudeAnswerer,
    FillContext,
    Plan,
    Planned,
    Resume,
    apply_model_answers,
    digest_rows,
    pick_option,
    plan_fields,
    question_key,
    resolve_country,
    saved_value,
    stable_key,
)
from tayyari.apply.forms import FormField
from tayyari.apply.playbook import GREENHOUSE, Playbook
from tayyari.contract.apply import FieldKind, NeedsInput
from tayyari.llm.errors import SpendCeilingReached
from tests.fakes.claude import FakeClaudeClient, StructuredCall

PB = Playbook(ats="generic")
YES_NO = [("1", "Yes"), ("0", "No")]
CV = Resume("Riya-Kapoor.pdf", b"%PDF-1.7 fake")


def field(
    label: str,
    kind: FieldKind = "text",
    *,
    key: str = "f1",
    required: bool = True,
    options: list[tuple[str, str]] | None = None,
) -> FormField:
    return FormField(key, f'[id="{key}"]', kind, label, required, None, key, list(options or []))


def ctx(**kw: Any) -> FillContext:
    return FillContext(**kw)


def saved(**values: str) -> dict[str, BankEntry]:
    """Answers the person saved in Your details (the answer bank is the only saved source)."""
    return {key: BankEntry(value, "you") for key, value in values.items()}


def plan_one(item: FormField, context: FillContext, playbook: Playbook = PB) -> tuple[Plan, list[FormField]]:
    return plan_fields([item], playbook, context)


def only_need(plan: Plan) -> NeedsInput:
    assert not plan.filled, [p.value for p in plan.filled]
    (need,) = plan.needs
    return need


def filled_value(plan: Plan) -> tuple[str, str | None, str]:
    (got,) = plan.filled
    return got.value, got.option_value, got.source


# ───────────────────────── work authorisation per country ─────────────────────────


def test_a_us_work_auth_question_with_only_india_saved_is_asked_and_never_sent_to_a_model() -> None:
    item = field("Are you legally authorized to work in the United States?", "radio", options=YES_NO)
    bank = {"workAuthorization:IN": BankEntry("Yes", "first_check")}
    plan, questions = plan_one(item, ctx(bank={**bank, **saved(workAuthorization="Yes")}))
    need = only_need(plan)
    assert questions == []  # never an open question for the model
    assert need.questionKey == "workAuthorization:US" and need.required
    assert "the United States" in need.reason and need.perOffer is None


def test_a_yes_on_another_basis_never_picks_no_from_inside_another() -> None:
    """'no' is a substring of 'another': only whole words match, so the first check's third choice finds Yes."""
    for options in ([("No", "No"), ("Yes", "Yes")], [("n", "No"), ("y", "Yes")]):
        item = field("Are you legally authorised to work in India?", "select", options=options)
        assert pick_option(item, "Yes, on another basis") == options[1]
    item = field("Notice", "select", options=[("a", "Immediate"), ("b", "30 days")])
    assert pick_option(item, "3") is None  # never part of a number either
    assert pick_option(item, "30") == ("b", "30 days")
    assert pick_option(item, "30 days notice") == ("b", "30 days")


def test_the_india_answer_fills_an_india_question() -> None:
    item = field("Are you authorised to work in India?", "radio", options=YES_NO)
    plan, _ = plan_one(item, ctx(bank={"workAuthorization:IN": BankEntry("Yes", "first_check")}))
    assert filled_value(plan) == ("Yes", "1", "details")
    assert plan.filled[0].question_key == "workAuthorization:IN"


def test_the_country_comes_from_the_label_then_the_posting_location() -> None:
    assert resolve_country("Do you need sponsorship to work in the U.S.?", None) == "US"
    assert resolve_country("Right to work in the UK?", "Bengaluru, India") == "GB"  # the label wins
    assert (
        resolve_country("Are you legally authorized to work in the country you are applying for?", "Pune")
        == "IN"
    )
    assert resolve_country("Authorised to work here?", "Remote - India") == "IN"
    assert resolve_country("Do you reside in the United States or Canada?", None) is None  # two countries
    assert resolve_country("Authorised to work here?", "Remote") is None
    assert resolve_country("Tell us about yourself", None) is None  # "us" is not the US
    sponsor = field("Will you need sponsorship to work in this role?", "radio", options=YES_NO)
    bank = {"needsSponsorship:US": BankEntry("Yes"), "needsSponsorship:IN": BankEntry("No")}
    plan, _ = plan_one(sponsor, ctx(bank=bank, job_location="Hyderabad, Telangana, India"))
    assert filled_value(plan) == ("No", "0", "details")


def test_remote_gives_a_question_for_this_form_only() -> None:
    item = field(
        "Are you legally authorized to work in the country for which you are applying?",
        "radio",
        options=YES_NO,
    )
    bank = {"workAuthorization:IN": BankEntry("Yes")}
    plan, questions = plan_one(item, ctx(bank=bank, job_location="Remote"))
    need = only_need(plan)
    assert questions == [] and need.perOffer is True
    assert need.questionKey == question_key(item.label) and need.questionKey.startswith("q:")


def test_a_country_less_answer_never_fills_a_per_country_question() -> None:
    # an answer saved under the bare key names no country, so it is no country's answer (India's included)
    bare = saved(needsSponsorship="No", workAuthorization="Yes")
    india = field("Will you require visa sponsorship to work in India?", "select", options=YES_NO)
    plan, _ = plan_one(india, ctx(bank=bare))
    assert only_need(plan).questionKey == "needsSponsorship:IN"
    germany = field("Will you require visa sponsorship to work in Germany?", "select", options=YES_NO)
    plan, _ = plan_one(germany, ctx(bank=bare))
    assert only_need(plan).questionKey == "needsSponsorship:DE"
    plan, _ = plan_one(india, ctx(bank={**bare, "needsSponsorship:IN": BankEntry("No", "first_check")}))
    assert filled_value(plan)[:2] == ("No", "0")  # from the bank entry for IN


@pytest.mark.parametrize(
    "label",
    [
        "Are you a Singapore citizen?",
        "Are you a Singapore permanent resident?",
        "Which visa situation fits you best?",
        "Are you a US citizen?",
        "Do you hold a valid work visa for the UK?",
        "What is your current visa status?",
        "Citizenship",
        "Nationality",
    ],
)
async def test_citizenship_and_visa_questions_are_needs_and_never_sent_to_a_model(label: str) -> None:
    item = field(label, "radio", options=YES_NO)
    plan, questions = plan_one(item, ctx(facts=FACTS))
    assert questions == [] and only_need(plan).required
    claude = FakeClaudeClient().script_structured("apply.answer", _answer, repeat=True)
    assert await ClaudeAnswerer(claude, "m").answer([item], ctx(facts=FACTS)) == {}  # type: ignore[arg-type]
    assert claude.structured_calls == []


# ───────────────────────── self-ID ─────────────────────────

GENDER = [("1", "Male"), ("2", "Female"), ("3", "Prefer not to say")]


def test_self_id_declines_by_default_and_uses_the_saved_value_only_when_switched_on() -> None:
    item = field("Gender", "select", options=GENDER)
    bank = {"eeoGender": BankEntry("Female", "you")}
    off, questions = plan_one(item, ctx(bank=bank))
    assert filled_value(off) == ("Prefer not to say", "3", "default") and questions == []
    on, _ = plan_one(item, ctx(bank=bank, self_id_on_forms=True))
    assert filled_value(on) == ("Female", "2", "details")
    nothing_saved, _ = plan_one(item, ctx(self_id_on_forms=True))
    assert filled_value(nothing_saved)[2] == "default"


def test_self_id_without_a_decline_option_is_skipped_or_asked() -> None:
    no_decline = [("1", "Yes"), ("2", "No")]
    optional, questions = plan_one(
        field("Are you a veteran?", "select", required=False, options=no_decline), ctx()
    )
    assert not optional.filled and not optional.needs and len(optional.skipped) == 1 and questions == []
    required, questions = plan_one(field("Disability status", "select", options=no_decline), ctx())
    need = only_need(required)
    assert questions == [] and "yours to give" in need.reason and need.questionKey == "eeoDisability"


# ───────────────────────── never stored ─────────────────────────


@pytest.mark.parametrize(
    "label",
    ["PAN card number", "Aadhaar number", "Bank account number", "IFSC code", "Passport number", "Caste",
     "Religion", "Marital status", "Father's name", "Do you know anyone working here?", "Referred by",
     "Have you previously applied to Acme?"],
)  # fmt: skip
def test_never_stored_labels_are_on_site_needs_and_never_reach_a_model(label: str) -> None:
    plan, questions = plan_one(field(label), ctx(bank={}, typed={"f1": "ABCDE1234F"}))
    need = only_need(plan)
    assert questions == [] and need.onSite is True and need.perOffer is None
    assert "employer's page" in need.reason


def test_signatures_and_declarations_are_the_persons_in_review_for_this_form_only() -> None:
    sign = field("Signature")
    plan, questions = plan_one(sign, ctx())
    need = only_need(plan)
    assert (
        questions == [] and need.perOffer is True and need.onSite is None and need.questionKey == "signature"
    )
    assert need.attestation is True  # the frontend reads the flag, not a mirror of the keys
    typed, _ = plan_one(sign, ctx(typed={"signature": "Riya Kapoor"}))
    assert filled_value(typed) == ("Riya Kapoor", None, "user") and typed.filled[0].attestation
    assert typed.review_fields()[0].attestation is True
    tick = field("I hereby declare that the above is true", "checkbox")
    plan, _ = plan_one(tick, ctx(bank=saved(declaration="yes")))  # a saved value is never used
    assert only_need(plan).perOffer is True


# ───────────────────────── Indian-form rules ─────────────────────────


def test_current_ctc_and_date_of_birth_go_only_into_required_fields() -> None:
    bank = {"currentCtc": BankEntry("12"), "dob": BankEntry("1999-04-02")}
    for label in ("Current CTC (in LPA)", "Date of birth"):
        optional, questions = plan_one(field(label, required=False), ctx(bank=bank))
        assert not optional.filled and not optional.needs and questions == []
        required, _ = plan_one(field(label), ctx(bank=bank))
        assert required.filled and required.filled[0].source == "details"


def test_open_to_discussion_for_a_numeric_expected_ctc_is_a_question() -> None:
    bank = {"expectedCtc": BankEntry("Open to discussion", "default")}
    numeric, questions = plan_one(field("Expected CTC (in LPA)"), ctx(bank=bank))
    need = only_need(numeric)
    assert questions == [] and need.perOffer is True and need.unit == "lakh a year"
    free_text, _ = plan_one(field("Expected CTC"), ctx(bank=bank))
    assert filled_value(free_text) == ("Open to discussion", None, "default")
    number_box, _ = plan_one(field("Expected salary", "number"), ctx(bank=bank))
    assert only_need(number_box).questionKey == "expectedCtc"


def test_a_cgpa_is_never_converted_to_a_percentage() -> None:
    cgpa = {"graduationScore": BankEntry("8.2 CGPA", "resume")}
    plan, questions = plan_one(field("Graduation percentage"), ctx(bank=cgpa))
    need = only_need(plan)
    assert questions == [] and "never convert" in need.reason and need.suggestion is None
    assert not any(ch.isdigit() for ch in need.reason)  # no converted figure is offered
    plan, _ = plan_one(field("Class 12 CGPA"), ctx(bank={"class12Score": BankEntry("91%")}))
    assert only_need(plan).questionKey == "class12Score"
    plan, _ = plan_one(field("Graduation CGPA"), ctx(bank=cgpa))
    assert filled_value(plan) == ("8.2 CGPA", None, "resume")
    plan, _ = plan_one(field("Graduation score"), ctx(bank=cgpa))  # the form does not say: copied as given
    assert filled_value(plan) == ("8.2 CGPA", None, "resume")


def test_bond_and_relocation_are_asked_per_offer() -> None:
    for label in ("Are you okay with a 1 year service bond?", "Are you willing to relocate?"):
        plan, questions = plan_one(field(label, "radio", options=YES_NO), ctx())
        need = only_need(plan)
        assert questions == [] and need.perOffer is True
    asked_each_time = {"relocation": BankEntry(None, "default", ask_each_time=True)}
    plan, _ = plan_one(field("Willing to relocate?", "radio", options=YES_NO), ctx(bank=asked_each_time))
    assert only_need(plan).perOffer is True
    firm = {"relocation": BankEntry("Yes", "you")}
    plan, _ = plan_one(field("Willing to relocate?", "radio", options=YES_NO), ctx(bank=firm))
    assert filled_value(plan) == ("Yes", "1", "details")


def test_an_ask_each_time_custom_question_never_goes_to_the_model() -> None:
    item = field("Why do you want to work here?", "textarea")
    plan, questions = plan_one(
        item, ctx(bank={question_key(item.label): BankEntry(None, "form", ask_each_time=True)})
    )
    assert questions == [] and only_need(plan).perOffer is True


def test_every_new_key_is_kept_from_the_model() -> None:
    for key in ("currentCtc", "expectedCtc", "noticePeriod", "dob", "class10Score", "bond", "pan", "declaration",
                "workAuthorization", "needsSponsorship", "eeoGender", "pronouns"):  # fmt: skip
        assert key in NO_MODEL_KEYS


# ───────────────────────── precedence and keys ─────────────────────────


def test_typed_beats_the_bank_beats_a_need() -> None:
    item = field("Notice period")
    bank = {"noticePeriod": BankEntry("30 days", "first_check")}
    assert filled_value(plan_one(item, ctx(bank=bank, typed={"f1": "15 days"}))[0])[0] == "15 days"
    assert (
        filled_value(plan_one(item, ctx(bank=bank, typed={"noticePeriod": "Immediate"}))[0])[0] == "Immediate"
    )
    assert filled_value(plan_one(item, ctx(bank=bank))[0]) == ("30 days", None, "details")
    need = only_need(plan_one(item, ctx(company="Acme"))[0])
    assert need.questionKey == "noticePeriod" and need.requiredBy == "Acme"
    # the old branch's key names are no saved answer any more
    old_name = plan_one(field("Expected CTC"), ctx(bank=saved(salaryExpectation="18 LPA")))[0]
    assert only_need(old_name).questionKey == "expectedCtc"


def test_name_fields_are_derived_from_the_saved_name() -> None:
    full = saved(fullName="Riya Kapoor")
    assert saved_value(ctx(bank=full), "firstName") == ("Riya", "details", False)
    assert saved_value(ctx(bank=full), "lastName") == ("Kapoor", "details", False)
    parts = saved(firstName="Riya", lastName="Kapoor")
    assert saved_value(ctx(bank=parts), "fullName") == ("Riya Kapoor", "details", False)
    assert saved_value(ctx(bank=saved(fullName="Riya")), "lastName") == (
        None,
        None,
        False,
    )  # one word: no last name
    plan, _ = plan_one(field("First Name", key="first_name"), ctx(bank=full), GREENHOUSE)
    assert filled_value(plan) == ("Riya", None, "details")


def test_question_keys() -> None:
    custom = field("Why  do you want to work at Acme?")
    assert (
        question_key(custom)
        == question_key("why do you want to work at acme")
        == question_key(custom, None, None)
    )
    assert question_key(custom).startswith("q:") and len(question_key(custom)) == 22
    assert question_key(field("x"), "workAuthorization", "IN") == "workAuthorization:IN"
    assert question_key(field("x"), "relocation", None) == "relocation"
    assert question_key(field("x"), "q:abc", None) == "q:abc"
    plan, _ = plan_one(custom, ctx())
    assert plan.question_keys == {"f1": question_key(custom)}


# ───────────────────────── the resume and the digest ─────────────────────────


def test_the_preview_plan_has_no_file_in_filled() -> None:
    fields = [field("Full name", key="name"), field("Resume/CV", "file", key="resume")]
    plan, _ = plan_fields(fields, PB, ctx(bank=saved(firstName="Riya", lastName="Kapoor"), resume=CV))
    assert [p.item.key for p in plan.filled] == ["name"] and plan.filled[0].value == "Riya Kapoor"
    assert (
        plan.resume is not None
        and plan.resume.sha256 == CV.sha256
        and plan.resume.selector == '[id="resume"]'
    )
    assert all(f.kind != "file" for f in plan.review_fields())
    without, _ = plan_fields(fields, PB, ctx(bank=saved(firstName="Riya")))
    assert without.resume is None and [n.key for n in without.needs] == ["resume"]


def test_the_digest_is_stable_across_value_equal_read_backs_and_changes_with_the_resume() -> None:
    rows = [('[id="name"]', "Riya Kapoor"), ('[name="q1"]', "Yes"), ("label:why acme|textarea", "Because")]
    reread = [
        ("label:why acme|textarea", "Because "),
        ('[name="q1"]', "Yes"),
        ('[id="name"]', "Riya  Kapoor"),
    ]
    sha = CV.sha256
    assert digest_rows(rows, sha) == digest_rows(reread, sha)
    assert digest_rows(rows, sha) != digest_rows(rows, Resume("cv.pdf", b"other").sha256)
    assert digest_rows(rows, sha) != digest_rows(rows, None)
    assert digest_rows(rows, sha) != digest_rows([*rows[:2], ("label:why acme|textarea", "Other")], sha)
    fields = [field("Full name", key="name"), field("Resume/CV", "file", key="resume")]
    name = saved(firstName="Riya", lastName="Kapoor")
    first, _ = plan_fields(fields, PB, ctx(bank=name, resume=CV))
    other, _ = plan_fields(fields, PB, ctx(bank=name, resume=Resume("Riya-Kapoor.pdf", b"%PDF-1.7 new")))
    assert first.digest() != other.digest()  # a changed resume needs a new approval, with nothing uploaded


def test_the_stable_key_is_the_id_or_name_else_the_label_and_kind() -> None:
    assert stable_key(FormField("a", '[id="a"]', "text", "A", True, "an", "a")) == '[id="a"]'
    assert stable_key(FormField("g", '[name="g"]', "radio", "Pick", True, "g", "g_1")) == '[name="g"]'
    assert stable_key(FormField("n", '[name="n"]', "text", "N", True, "n", None)) == '[name="n"]'
    assert (
        stable_key(FormField("x", "div > input:nth-of-type(2)", "text", "Why Acme?*", True))
        == "label:why acme|text"
    )


# ───────────────────────── model answers ─────────────────────────

FACTS = [{"id": "f1", "text": "Built a payments API serving 2 million users at Fintech Co."}]


def test_cited_fact_and_digit_checks_are_kept() -> None:
    item = field("Tell us about a project", "textarea")
    plan, questions = plan_one(item, ctx(facts=FACTS))
    good = {
        "f1": {
            "answer": "I built a payments API for 2 million users.",
            "factIds": ["f1"],
            "confidence": "high",
        }
    }
    apply_model_answers(plan, questions, good, ctx(facts=FACTS))
    assert plan.model_fields == 1 and plan.filled[0].source == "model"
    for bad in (
        {
            "answer": "I led 40 engineers.",
            "factIds": ["f1"],
            "confidence": "high",
        },  # a number not in the fact
        {"answer": "I like payments.", "factIds": ["nope"], "confidence": "high"},  # cites nothing real
    ):
        plan, questions = plan_one(item, ctx(facts=FACTS))
        apply_model_answers(plan, questions, {"f1": bad}, ctx(facts=FACTS))
        need = only_need(plan)
        assert need.suggestion is None and need.questionKey == question_key(item.label)
    medium = {"f1": {"answer": "I built a payments API.", "factIds": ["f1"], "confidence": "medium"}}
    plan, questions = plan_one(item, ctx(facts=FACTS))
    apply_model_answers(plan, questions, medium, ctx(facts=FACTS))
    assert only_need(plan).suggestion == "I built a payments API."


def _answer(call: StructuredCall) -> dict[str, Any]:
    return {
        "answers": [
            {"key": "f1", "factIds": ["f1"], "answer": "I built a payments API.", "confidence": "high"}
        ]
    }


async def test_the_reserve_is_entered_before_the_structured_call() -> None:
    order: list[str] = []

    @asynccontextmanager
    async def reserve(usd: float) -> AsyncIterator[None]:
        order.append(f"reserve {usd}")
        yield
        order.append("release")

    def answer(call: StructuredCall) -> dict[str, Any]:
        order.append("call")
        return _answer(call)

    claude = FakeClaudeClient().script_structured("apply.answer", answer)
    answerer = ClaudeAnswerer(claude, "claude-haiku-5-5", reserve=reserve, est_usd=0.005)  # type: ignore[arg-type]
    out = await answerer.answer(
        [field("Tell us about a project", "textarea")], ctx(facts=FACTS, profile_id="p1")
    )
    assert order == ["reserve 0.005", "call", "release"] and out["f1"]["answer"] == "I built a payments API."
    (call,) = claude.structured_calls
    assert (
        call.ctx.purpose == "apply.answer"
        and call.ctx.profile_id == "p1"
        and call.model == "claude-haiku-5-5"
    )


async def test_the_answerer_re_raises_the_spend_ceiling_and_turns_other_errors_into_questions() -> None:
    @asynccontextmanager
    async def refused(usd: float) -> AsyncIterator[None]:
        raise SpendCeilingReached()
        yield

    question = [field("Tell us about a project", "textarea")]
    claude = FakeClaudeClient()
    with pytest.raises(SpendCeilingReached):
        await ClaudeAnswerer(claude, "m", reserve=refused).answer(question, ctx(facts=FACTS))  # type: ignore[arg-type]
    assert claude.structured_calls == []  # refused before any call
    ceiling = FakeClaudeClient().script_structured("apply.answer", SpendCeilingReached())
    with pytest.raises(SpendCeilingReached):
        await ClaudeAnswerer(ceiling, "m").answer(question, ctx(facts=FACTS))  # type: ignore[arg-type]
    broken = FakeClaudeClient().script_structured("apply.answer", RuntimeError("boom"))
    assert await ClaudeAnswerer(broken, "m").answer(question, ctx(facts=FACTS)) == {}  # type: ignore[arg-type]


async def test_the_answerer_never_sends_a_kept_back_question_and_escalates_on_request() -> None:
    claude = FakeClaudeClient().script_structured("apply.answer", _answer, repeat=True)
    answerer = ClaudeAnswerer(claude, "claude-haiku-5-5", escalate_model="claude-sonnet-5-5")  # type: ignore[arg-type]
    kept_back = [field("PAN card number"), field("Are you authorised to work in the US?"), field("Gender")]
    assert await answerer.answer(kept_back, ctx(facts=FACTS)) == {} and claude.structured_calls == []
    await answerer.answer([field("Tell us about a project", "textarea")], ctx(facts=FACTS), escalate=True)
    assert [c.model for c in claude.structured_calls] == ["claude-sonnet-5-5"]
    assert "PAN" not in claude.structured_calls[0].user_text


async def test_greenhouse_plan_end_to_end_keeps_the_resume_out_and_the_questions_keyed() -> None:
    fields = [
        field("First Name", key="first_name"),
        field("Resume/CV", "file", key="resume"),
        field("Will you now or in the future require visa sponsorship to work in India?", "select", key="q3",
              options=YES_NO),
        field("Why do you want to work at Acme?", "textarea", key="q2"),
    ]  # fmt: skip
    context = ctx(bank={**saved(firstName="Riya"), "needsSponsorship:IN": BankEntry("No")}, resume=CV)
    plan, questions = plan_fields(fields, GREENHOUSE, context)
    assert [p.item.key for p in plan.filled] == ["first_name", "q3"] and [q.key for q in questions] == ["q2"]
    assert isinstance(plan.filled[0], Planned) and plan.filled[1].question_key == "needsSponsorship:IN"
    assert plan.resume is not None and plan.question_keys["q2"] == question_key(
        "Why do you want to work at Acme?"
    )


@pytest.mark.parametrize(
    "label",
    [
        "Are you Hispanic/Latino?",
        "Sexual orientation",
        "Do you identify as LGBTQ+?",
        "Do you identify as transgender?",
        "Age",
    ],
)
async def test_other_demographic_questions_are_declined_and_never_sent_to_a_model(label: str) -> None:
    options = [("1", "Yes"), ("0", "No"), ("2", "Decline To Self Identify")]
    item = field(label, "select", options=options)
    plan, questions = plan_one(item, ctx(facts=FACTS))
    assert questions == [] and filled_value(plan) == ("Decline To Self Identify", "2", "default")
    claude = FakeClaudeClient().script_structured("apply.answer", _answer, repeat=True)
    assert await ClaudeAnswerer(claude, "m").answer([item], ctx(facts=FACTS)) == {}  # type: ignore[arg-type]
    assert claude.structured_calls == []


def test_a_question_about_mobile_apps_never_gets_the_phone_number() -> None:
    label = "Have you contributed to a mobile app(s) and/or several features that reached a large number of users?"
    plan, questions = plan_one(field(label, "textarea"), ctx(bank=saved(phone="+91 98765 43210")))
    assert plan.filled == [] and [q.label for q in questions] == [label]


def test_a_referral_question_in_ashby_wording_is_typed_on_the_employers_page() -> None:
    label = "Has someone at Linear referred you for this role? If so, please include their name here:"
    plan, questions = plan_one(field(label, "text"), ctx(facts=FACTS))
    assert questions == [] and only_need(plan).onSite is True


def test_a_text_area_cover_letter_is_never_drafted() -> None:
    plan, questions = plan_one(field("Cover letter", "textarea"), ctx(facts=FACTS))
    need = only_need(plan)
    assert questions == [] and need.perOffer is True and "file" not in need.reason
    plan, _ = plan_one(field("Cover letter", "textarea", required=False), ctx(facts=FACTS))
    assert plan.needs == [] and plan.filled == []


async def test_the_answerer_drops_rows_for_questions_it_did_not_send() -> None:
    def answer(call: StructuredCall) -> dict[str, Any]:
        return {
            "answers": [
                {"key": "f1", "factIds": ["f1"], "answer": "I built a payments API.", "confidence": "high"},
                {"key": "pan", "factIds": ["f1"], "answer": "ABCDE1234F", "confidence": "high"},
            ]
        }

    claude = FakeClaudeClient().script_structured("apply.answer", answer)
    questions = [field("Tell us about a project", "textarea"), field("PAN card number", key="pan")]
    out = await ClaudeAnswerer(claude, "m").answer(questions, ctx(facts=FACTS))  # type: ignore[arg-type]
    assert list(out) == ["f1"]
