"""Playbooks: a replayable, per-ATS (or per-site) field map and step sequence the runner follows with no model call.

A playbook is data (stored as JSON in ``ats_playbooks``): where the form is, what to click to open it, which field means which
of the person's details (by name, id or label), what the submit button is and what success looks like. The built-in ones
cover Greenhouse, Lever and Ashby; the recorder turns a successful AI run on any other site into a draft one.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, Field

from tayyari.apply.forms import FormField

ProfileKey = Literal[
    "firstName", "lastName", "fullName", "email", "phone", "location", "currentCompany", "currentTitle",
    "linkedin", "github", "website", "workAuthorization", "needsSponsorship", "willingToRelocate", "noticePeriod",
    "salaryExpectation", "startDate", "howDidYouHear", "pronouns", "eeoGender", "eeoRace", "eeoVeteran",
    "eeoDisability", "resume", "coverLetter",
]  # fmt: skip
PROFILE_KEYS: Final[tuple[str, ...]] = ProfileKey.__args__  # type: ignore[attr-defined]
SENSITIVE_KEYS: Final = frozenset({"eeoGender", "eeoRace", "eeoVeteran", "eeoDisability"})
"""Answered only from what the person saved; never drafted, never defaulted."""


class FieldRule(BaseModel):
    key: ProfileKey
    name: str | None = None
    id: str | None = None
    label: str | None = None
    """A case-insensitive regular expression on the field's label."""


class Playbook(BaseModel):
    ats: str
    version: int = 1
    form_selector: str = "form"
    open_clicks: list[str] = Field(default_factory=list)
    """Selectors clicked, in order, before the form is read (an "Apply" button or tab); a missing one is skipped."""
    rules: list[FieldRule] = Field(default_factory=list)
    submit_selector: str = "button[type=submit], input[type=submit]"
    success_url: str | None = None
    """A regular expression the URL matches after a successful submit."""
    success_text: str = r"thank(s| you) for (applying|your application)|application (has been )?(submitted|received)|we('ve| have) received your application"
    error_text: str = r"(is|are) required|please (fill|complete|enter)|there (was|were) (an )?errors?"
    domain: str | None = None
    notes: list[str] = Field(default_factory=list)


GENERIC_RULES: Final[list[FieldRule]] = [
    FieldRule(key="firstName", label=r"^(your )?(first|given)[ -]?name|^prénom"),
    FieldRule(key="lastName", label=r"^(your )?(last|family|sur)[ -]?name"),
    FieldRule(key="fullName", label=r"^(your )?(full )?name$|^legal name"),
    FieldRule(key="email", label=r"e-?mail"),
    FieldRule(key="phone", label=r"phone|mobile"),
    FieldRule(key="linkedin", label=r"linked ?in"),
    FieldRule(key="github", label=r"git ?hub"),
    FieldRule(key="website", label=r"website|portfolio|personal (site|url)|other url"),
    FieldRule(key="currentCompany", label=r"^(current )?(company|employer|organi[sz]ation)"),
    FieldRule(key="currentTitle", label=r"^(current )?(job )?title|current role"),
    FieldRule(key="location", label=r"^(current )?(location|city)|where are you (located|based)"),
    FieldRule(key="resume", label=r"resume|\bcv\b|curriculum"),
    FieldRule(key="coverLetter", label=r"cover letter"),
    FieldRule(key="needsSponsorship", label=r"sponsor"),
    FieldRule(
        key="workAuthorization",
        label=r"authori[sz](ed|ation) to work|legally (able|eligible|authori[sz]ed)|right to work",
    ),
    FieldRule(key="willingToRelocate", label=r"relocat"),
    FieldRule(key="noticePeriod", label=r"notice period"),
    FieldRule(key="salaryExpectation", label=r"salary|compensation expectation|expected ctc|desired pay"),
    FieldRule(key="startDate", label=r"start date|when can you start|available to start|earliest start"),
    FieldRule(key="howDidYouHear", label=r"how did you (hear|find)|where did you (hear|find)"),
    FieldRule(key="pronouns", label=r"pronoun"),
    FieldRule(key="eeoGender", label=r"^gender|gender identity"),
    FieldRule(key="eeoRace", label=r"race|ethnic"),
    FieldRule(key="eeoVeteran", label=r"veteran"),
    FieldRule(key="eeoDisability", label=r"disabilit"),
]

GREENHOUSE: Final = Playbook(
    ats="greenhouse",
    form_selector="#application-form, #application_form, form",
    open_clicks=["#apply_button", "button.apply-button"],
    rules=[
        FieldRule(key="firstName", id="first_name"),
        FieldRule(key="lastName", id="last_name"),
        FieldRule(key="email", id="email"),
        FieldRule(key="phone", id="phone"),
        FieldRule(key="resume", id="resume"),
        FieldRule(key="coverLetter", id="cover_letter"),
        FieldRule(key="location", id="candidate-location"),
    ],
    submit_selector="#submit_app, button[type=submit]",
    success_url=r"/confirmation|thank",
)

LEVER: Final = Playbook(
    ats="lever",
    form_selector="#application-form, form.application-form",
    rules=[
        FieldRule(key="resume", name="resume"),
        FieldRule(key="fullName", name="name"),
        FieldRule(key="email", name="email"),
        FieldRule(key="phone", name="phone"),
        FieldRule(key="currentCompany", name="org"),
        FieldRule(key="location", name="location"),
        FieldRule(key="linkedin", name="urls[LinkedIn]"),
        FieldRule(key="github", name="urls[GitHub]"),
        FieldRule(key="website", name="urls[Portfolio]"),
        FieldRule(key="eeoGender", name="eeo[gender]"),
        FieldRule(key="eeoRace", name="eeo[race]"),
        FieldRule(key="eeoVeteran", name="eeo[veteran]"),
    ],
    submit_selector="#btn-submit, button[type=submit]",
    success_url=r"/thanks",
    success_text=r"application submitted|thank(s| you) for applying",
)

BUILTIN: Final[dict[str, Playbook]] = {"greenhouse": GREENHOUSE, "lever": LEVER}


def builtin_id(pb: Playbook) -> str:
    return f"builtin:{pb.ats}@{pb.version}"


def rule_for(item: FormField, playbook: Playbook) -> ProfileKey | None:
    """The person's detail this field asks for: the playbook's own rules first (exact name or id, then label), then the
    generic label rules. None for a question the rules cannot place (a custom question)."""
    for rule in playbook.rules:
        if (rule.name and item.name == rule.name) or (rule.id and item.id == rule.id):
            return rule.key
    for rule in (*playbook.rules, *GENERIC_RULES):
        if rule.label and re.search(rule.label, item.label, re.I):
            if rule.key == "resume" and item.kind != "file":
                continue  # "Resume text" boxes are questions, not the file
            if item.kind == "file" and rule.key not in ("resume", "coverLetter"):
                continue
            return rule.key
    return None


__all__ = [
    "BUILTIN",
    "GENERIC_RULES",
    "GREENHOUSE",
    "LEVER",
    "PROFILE_KEYS",
    "SENSITIVE_KEYS",
    "FieldRule",
    "Playbook",
    "ProfileKey",
    "builtin_id",
    "rule_for",
]
