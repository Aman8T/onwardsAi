"""Playbooks: a replayable, per-ATS (or per-site) field map and step sequence the runner follows with no model call.

A playbook is data (stored as JSON in ``ats_playbooks``): where the form is, what to click to open it, which field means which
of the person's details (by name, id, recorded selector or label), how each field is operated (typed, picked from a select,
chosen from a combobox or typeahead), what the submit button is, which hosts may receive the approved submit and the resume
upload, which read-only POSTs the form needs to render, and what success, an error and a closed posting look like. The
built-in ones cover Greenhouse, Lever and Ashby and are seeded from the experiment's recorded traces (keys only, never
values: `experiments/auto_apply/runs/*-B-*/trace.json`).

Label rules come in three layers, first match wins: the playbook's own exact rules (name, id, recorded selector), then the
never-stored rules (ID numbers, caste or category, signatures and the rest of spec 2.2 "What we leave to you"), then the
playbook's and the generic label rules. Work authorisation and sponsorship are per country: their rules give the bare key
and `scoped_key` adds the country (``workAuthorization:IN``); a question about one country never reads another's answer.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, Field

from tayyari.apply.forms import FormField

ProfileKey = Literal[
    "firstName", "lastName", "fullName", "email", "phone", "phoneCountry", "location", "country", "currentCompany",
    "currentTitle", "linkedin", "github", "website", "workAuthorization", "needsSponsorship", "willingToRelocate",
    "noticePeriod", "salaryExpectation", "startDate", "howDidYouHear", "pronouns", "eeoGender", "eeoRace", "eeoVeteran",
    "eeoDisability", "resume", "coverLetter",
    # India keys (spec 2.2, the question bank)
    "currentCtc", "expectedCtc", "servingNotice", "lastWorkingDay", "totalExperience", "dob", "highestQualification",
    "college", "graduationYear", "graduationScore", "class12Score", "class10Score", "activeBacklogs", "backlogsEver",
    "gap6m", "languages", "preferredCities", "relocation", "workMode", "shifts", "bond",
    # Other voluntary self-ID (sexual orientation, LGBTQ+, transgender, age): never in the answer bank, so only ever
    # declined, skipped or asked
    "eeoOther",
]  # fmt: skip
"""The person's details a field can ask for. ``salaryExpectation`` and ``willingToRelocate`` are the old names of
``expectedCtc`` and ``relocation`` (`KEY_ALIASES`), kept until the legacy details go (package Z)."""

NeverStoredKey = Literal[
    "pan", "aadhaar", "passportNumber", "idNumber", "bankAccount", "ifsc", "casteCategory", "religion", "maritalStatus",
    "fatherName", "signature", "declaration", "consent", "knowsSomeoneHere", "referral", "previousApplication",
]  # fmt: skip
"""Mirrors `contract.apply.NEVER_STORED_KEYS` (a test keeps them equal)."""

RuleKey = ProfileKey | NeverStoredKey
FieldOp = Literal["fill", "select", "check", "choose", "upload", "group"]

PROFILE_KEYS: Final[tuple[str, ...]] = ProfileKey.__args__  # type: ignore[attr-defined]
NEVER_STORED_RULE_KEYS: Final[frozenset[str]] = frozenset(NeverStoredKey.__args__)  # type: ignore[attr-defined]
ATTESTATION_KEYS: Final[frozenset[str]] = frozenset({"signature", "declaration", "consent"})
"""Never stored; typed or ticked by the person in Review for one form only (``perOffer``). The other never-stored keys are
typed by the person on the employer's page (``onSite``)."""
ON_SITE_KEYS: Final[frozenset[str]] = NEVER_STORED_RULE_KEYS - ATTESTATION_KEYS
SENSITIVE_KEYS: Final = frozenset({"eeoGender", "eeoRace", "eeoVeteran", "eeoDisability", "eeoOther"})
"""Voluntary self-ID: answered from what the person saved only with "use on forms" on, else declined."""
SELF_ID_KEYS: Final[frozenset[str]] = SENSITIVE_KEYS | {"pronouns"}
COUNTRY_KEYS: Final[frozenset[str]] = frozenset({"workAuthorization", "needsSponsorship"})
"""Stored per country (``workAuthorization:IN``); never inferred from another country, never sent to a model."""
PER_OFFER_KEYS: Final[frozenset[str]] = frozenset({"bond", "relocation"})
"""Decided per offer: asked each time unless the person saved a firm answer."""
REQUIRED_ONLY_KEYS: Final[frozenset[str]] = frozenset({"currentCtc", "dob"})
"""Sent only into a field the form requires (spec 2.2: current CTC and date of birth)."""
KEY_ALIASES: Final[dict[str, str]] = {"salaryExpectation": "expectedCtc", "willingToRelocate": "relocation"}
"""Old key -> its canonical name."""


def canonical_key(key: str) -> str:
    return KEY_ALIASES.get(key, key)


def legacy_names(key: str) -> tuple[str, ...]:
    """The canonical key and its old names, in lookup order."""
    return (key, *(old for old, new in KEY_ALIASES.items() if new == key))


def scoped_key(key: str, country: str) -> str:
    """``workAuthorization:IN``: the answer-bank key of a per-country question."""
    return f"{key}:{country.upper()}"


class FieldRule(BaseModel):
    key: RuleKey | None = None
    """The person's detail this field asks for; None for a widget hint (an ``op`` by ``type``) or for a custom question
    with its own ``question_key``."""
    name: str | None = None
    id: str | None = None
    label: str | None = None
    """A case-insensitive regular expression on the field's label."""
    op: FieldOp | None = None
    """How the field is operated; None means by its kind (`op_for`)."""
    type: str | None = None
    """The field kind a label rule or a widget hint applies to (``combobox``, ``select``, ...); None for any."""
    selector: str | None = None
    """A recorded stable selector (``[id="..."]`` or ``[name="..."]``)."""
    frame: int = 0
    question_key: str | None = None
    """The answer-bank key of a custom question (``q:<hash>``) when ``key`` is None."""


class Playbook(BaseModel):
    ats: str
    version: int = 1
    form_selector: str = "form"
    open_clicks: list[str] = Field(default_factory=list)
    """Selectors clicked, in order, before the form is read (an "Apply" button or tab); a missing one is skipped."""
    open_texts: list[str] = Field(default_factory=list)
    """Visible texts of buttons or links clicked, in order, before the form is read; a missing one is skipped."""
    rules: list[FieldRule] = Field(default_factory=list)
    submit_selector: str = "button[type=submit], input[type=submit]"
    success_url: str | None = None
    """A regular expression the URL matches after a successful submit."""
    success_text: str = r"thank(s| you) for (applying|your application)|application (has been )?(submitted|received)|we('ve| have) received your application"
    error_text: str = r"(is|are) required|please (fill|complete|enter)|there (was|were) (an )?errors?"
    closed_text: str = r"no longer (accepting applications|available|open)|(job|position|posting) (is |has been )?(closed|filled|expired)|job not found"
    """What a posting that is gone says (the run then ends ``closed``)."""
    submit_hosts: list[str] = Field(default_factory=list)
    """Hosts that may receive the approved submit (non-GET), and only after the ``submitting`` marker."""
    upload_hosts: list[str] = Field(default_factory=list)
    """Hosts that may receive the resume upload in the submit pass (Greenhouse and Ashby upload on attach)."""
    read_only_posts: list[str] = Field(default_factory=list)
    """Regular expressions of POST URLs the form needs just to render (allowed in the preview; never a submit or an
    upload)."""
    domain: str | None = None
    notes: list[str] = Field(default_factory=list)


def _r(key: RuleKey, label: str) -> FieldRule:
    return FieldRule(key=key, label=label)


NEVER_STORED_RULES: Final[list[FieldRule]] = [
    _r("pan", r"permanent account number|\bpan\b(?!\s*-?\s*india)"),
    _r("aadhaar", r"aadh?aa?r|\buidai\b"),
    _r("passportNumber", r"passport (number|no\b)"),
    _r("ifsc", r"\bifsc\b"),
    _r("bankAccount", r"\bbank (account|a/c|name|details|branch)|\baccount (number|no\b)"),
    _r(
        "idNumber",
        r"\b(national|government|govt\.?|voter|photo) id\b|\bid (proof|number|no\b)|social security|\bssn\b|"
        r"licen[cs]e (number|no\b)",
    ),
    _r(
        "casteCategory",
        r"\bcaste\b|reserv(ed|ation) category|social category|^category\b|\b(?-i:SC|ST|OBC|EWS)\b",
    ),
    _r("religion", r"religio"),
    _r("maritalStatus", r"marital|\bmarried\b"),
    _r("fatherName", r"(father|mother|guardian|parent|husband|spouse)'?s?'? (full )?name"),
    _r("signature", r"signature|\bsign (here|below)|\be-?sign\b"),
    _r(
        "declaration",
        r"declar|i hereby|i certify|i confirm (that|the)|i acknowledge|attest|acknowledge ?/ ?confirm|"
        r"information (provided|given) (above )?is (true|correct|accurate)",
    ),
    _r("consent", r"consent|i agree|terms (and|&) conditions|privacy (policy|notice)"),
    _r(
        "knowsSomeoneHere",
        r"know (any ?one|someone|anybody)|"
        r"(friends?|relatives?|family( members?)?) (who )?(currently )?(work|working|employed)",
    ),
    _r(
        "referral",
        r"who referred you|referred by|referral (code|name|id|employee)|employee referral|were you referred|"
        r"\breferred you\b|\b(any ?one|some ?one|somebody)\b.*\brefer",
    ),
    _r(
        "previousApplication",
        r"(previously|ever|already) applied|applied (to us |with us |here |for this role )?before|prior application",
    ),
]
"""Spec 2.2 "What we leave to you": matched before the generic label rules, so "Bank account number" or "Aadhaar linked
mobile" never reads as a phone."""

FIRST_RULES: Final[list[FieldRule]] = [
    _r("fullName", r"^(your )?(full |legal )?name (exactly )?as (on|per|in)\b"),
]
"""Matched before the never-stored rules: "Name as per PAN" asks for the name (spec 2.2 "Full name as on ID"), not the
number."""

GENERIC_RULES: Final[list[FieldRule]] = [
    _r("firstName", r"^(your )?(first|given)[ -]?name|^prénom"),
    _r("lastName", r"^(your )?(last|family|sur)[ -]?name"),
    _r("fullName", r"^(your )?(full )?name$|^legal name|^full name"),
    _r("email", r"e-?mail"),
    _r("phoneCountry", r"country code|dial(l?ing)? code|phone country"),
    # The whole label, or "<phone word> number": a custom question that mentions a mobile app is not the phone
    _r(
        "phone",
        r"^(your |a )?(phone|mobile|cell|telephone|whatsapp)( phone)?( number| no\.?)?\s*[*:]?\s*$|"
        r"\b(phone|mobile|cell|telephone|whatsapp|contact) (number|no\b)",
    ),
    _r("linkedin", r"linked ?in"),
    _r("github", r"git ?hub"),
    _r("website", r"website|portfolio|personal (site|url)|other url"),
    # Sponsorship and work authorisation before location: "... sponsorship to remain in your current location?"
    _r("needsSponsorship", r"sponsor"),
    _r(
        "workAuthorization",
        r"authori[sz](ed|ation) to work|legally (able|eligible|authori[sz]ed|permitted)|right to work|work permit|"
        r"eligib(le|ility) to work|permitted to work|citizen(ship)?\b|permanent resident|\bvisa\b|green card|"
        r"nationality|immigration",
    ),
    _r(
        "currentCompany",
        r"^(current |present )?(company|employer|organi[sz]ation)( name)?\b|^(name of )?(your )?current employer",
    ),
    _r("currentTitle", r"^(current )?(job )?title|^(your )?current (role|designation|position)|^designation"),
    _r(
        "totalExperience",
        r"^(total|overall) (work |professional |full[- ]time )?experience|"
        r"^(total )?years of (total |professional |work |full[- ]time )?experience\s*\??$|^experience \(?in years\)?$",
    ),
    _r(
        "preferredCities",
        r"preferred (job |work )?(location|cit)|cities you('d| would)|which cities|location preference",
    ),
    _r(
        "country",
        r"country of residence|(what|which) country (are you|do you) (currently )?(based|live|reside|located)|"
        r"^country$|current country",
    ),
    _r(
        "location",
        r"^(current )?(location|city)|where are you (located|based)|current (city|location)|city of residence",
    ),
    _r("resume", r"resume|\bcv\b|curriculum"),
    _r("coverLetter", r"cover letter"),
    _r("relocation", r"relocat"),
    _r("servingNotice", r"serving (your |the |a )?notice|currently (on|in|serving) (your |a )?notice"),
    _r("lastWorkingDay", r"last working (day|date)|\blwd\b|relieving date"),
    _r("noticePeriod", r"notice period|\bnotice\b|how soon can you join|when can you join"),
    _r(
        "currentCtc",
        r"current (ctc|salary|compensation|package|annual (salary|ctc))|present (ctc|salary)|\bcctc\b",
    ),
    _r(
        "expectedCtc",
        r"expected (ctc|salary|compensation|package)|\bectc\b|salary expectation|compensation expectation|"
        r"desired (pay|salary|compensation)|salary",
    ),
    _r("startDate", r"start date|when can you start|available to start|earliest start"),
    _r(
        "howDidYouHear",
        r"how did you (hear|find|learn|come across)|how you (heard|found|learned|learnt) (about|of)|"
        r"where did you (hear|find|see)",
    ),
    _r("dob", r"date of birth|\bd\.?o\.?b\b|birth ?date"),
    _r(
        "class12Score",
        r"(class|std\.?|standard|grade) ?(xii|12)(th)?\b|\b12th\b|\bhsc\b|higher secondary|\bpuc\b",
    ),
    _r(
        "class10Score",
        r"(class|std\.?|standard|grade) ?(x|10)(th)?\b|\b10th\b|\bssc\b|\bsslc\b|matriculation",
    ),
    _r(
        "graduationScore",
        r"\b(graduation|degree|ug|undergraduate|bachelor'?s?|university|college)\b.*"
        r"\b(score|cgpa|gpa|percentage|marks|grade)\b|\bcgpa\b|\bgpa\b|aggregate (percentage|marks)",
    ),
    _r("graduationYear", r"year of (passing|graduation|completion)|graduation year|passing year|passed out"),
    _r(
        "highestQualification",
        r"highest (level of )?(qualification|education|degree)|^(qualification|degree|education)( level)?$|"
        r"educational qualification",
    ),
    _r("college", r"college|university|institut(e|ion)|alma mater"),
    _r(
        "activeBacklogs",
        r"(active|current|live|pending|standing) (backlogs?|arrears?)|backlogs? (currently|pending)",
    ),
    _r("backlogsEver", r"backlogs?|arrears?"),
    _r("gap6m", r"\bgap\b|career break|break in (your )?(education|career|studies)"),
    _r("languages", r"(?<!programming )(?<!coding )\blanguages?\b"),
    _r("bond", r"\bbond\b|service (agreement|bond)"),
    _r("shifts", r"\bshifts?\b|rotational"),
    _r(
        "workMode",
        r"work (mode|arrangement|setup|from office|from home)|\bwfo\b|\bwfh\b|"
        r"(prefer|open|okay|ok|comfortable|willing)\b.*\b(remote|hybrid|on-?site|in-?office)\b",
    ),
    _r("pronouns", r"\bpronouns?\b"),
    _r("eeoGender", r"\bgender\b|^sex$"),
    _r("eeoRace", r"\brace\b|ethnic|hispanic|latin[oax]\b|latinx"),
    _r("eeoVeteran", r"veteran"),
    _r("eeoDisability", r"disabilit|differently.abled|\bpwd\b"),
    _r(
        "eeoOther",
        r"sexual orientation|\blgbt|transgender|\bqueer\b|\bage\b|age range|over (the age of )?18|18 years",
    ),
]

GREENHOUSE: Final = Playbook(
    ats="greenhouse",
    form_selector="#application-form, #application_form, form",
    open_clicks=["#apply_button", "button.apply-button"],
    open_texts=["Apply", "Apply for this job"],
    rules=[
        FieldRule(key="firstName", id="first_name", op="fill"),
        FieldRule(key="lastName", id="last_name", op="fill"),
        FieldRule(key="email", id="email", op="fill"),
        FieldRule(key="phone", id="phone", op="fill"),
        # The phone's country picker: a react-select combobox beside the phone field (with about 245 hidden options in
        # the DOM), filled from the phone's dial code.
        FieldRule(key="phoneCountry", id="country", op="choose", type="combobox"),
        FieldRule(key="resume", id="resume", op="upload", type="file"),
        FieldRule(key="coverLetter", id="cover_letter", op="upload", type="file"),
        # "Location (City)": a typeahead combobox.
        FieldRule(key="location", id="candidate-location", op="choose", type="combobox"),
        # Every custom select on the new job boards is a react-select combobox: type, then pick from its own listbox
        # (its hidden "requiredInput" twin is not a field).
        FieldRule(type="combobox", op="choose"),
    ],
    submit_selector="#submit_app, button[type=submit]",
    success_url=r"/confirmation|thank",
    success_text=r"thank(s| you) for (applying|your application|your interest)|application (has been )?(submitted|received)",
    error_text=r"(this field )?(is|are) required|please (fill|complete|enter|select)|there (was|were) (an )?errors?|"
    r"(an )?error (occurred|submitting)",
    closed_text=r"(the )?job (you are looking for )?(is no longer (open|available)|has (been )?closed)|"
    r"no longer accepting applications|page you were looking for (doesn't|does not) exist",
    submit_hosts=["boards.greenhouse.io", "job-boards.greenhouse.io"],
    upload_hosts=["boards.greenhouse.io", "job-boards.greenhouse.io"],
    read_only_posts=[],
    notes=[
        "Seeded from the gh-gitlab and gh-cloudflare traces (keys only).",
        "No read-only POST was needed to render either form (0 blocked requests in every preview run).",
        "The resume uploads the moment it is attached, so it is attached only in the submit pass, after the marker.",
        "submit_hosts and upload_hosts are the board hosts; the A1.6 live test confirms where the upload goes.",
    ],
)

LEVER: Final = Playbook(
    ats="lever",
    form_selector="#application-form, form.application-form",
    open_texts=["Deny", "Apply for this job"],
    rules=[
        FieldRule(
            key="resume", name="resume", op="upload", type="file", selector='[id="resume-upload-input"]'
        ),
        FieldRule(key="fullName", name="name", op="fill"),
        FieldRule(key="email", name="email", op="fill"),
        FieldRule(key="phone", name="phone", op="fill"),
        FieldRule(key="currentCompany", name="org", op="fill"),
        # "Current location": a typeahead; type, then pick the first suggestion (or keep the typed text when none).
        FieldRule(key="location", name="location", op="choose", selector='[id="location-input"]'),
        FieldRule(key="linkedin", name="urls[LinkedIn]", op="fill"),
        FieldRule(key="github", name="urls[GitHub]", op="fill"),
        FieldRule(key="website", name="urls[Portfolio]", op="fill"),
        FieldRule(key="eeoGender", name="eeo[gender]", op="select"),
        FieldRule(key="eeoRace", name="eeo[race]", op="select"),
        FieldRule(key="eeoVeteran", name="eeo[veteran]", op="select"),
        FieldRule(key="eeoDisability", name="eeo[disability]", op="select"),
        # Custom "cards" questions with several boxes or buttons are groups: pick by the option's text.
        FieldRule(type="checkboxGroup", op="group"),
        FieldRule(type="radio", op="group"),
    ],
    submit_selector="#btn-submit, button[type=submit]",
    success_url=r"/thanks",
    success_text=r"application submitted|thank(s| you) for applying",
    error_text=r"(this field )?(is|are) required|please (fill|complete|enter|select)|there (was|were) (an )?errors?",
    closed_text=r"(posting|job) (you're looking for )?(is no longer (open|available)|has (been )?closed)|"
    r"no longer (accepting applications|open)|couldn't find (that|the) (job|posting)",
    submit_hosts=["jobs.lever.co", "jobs.eu.lever.co"],
    upload_hosts=["jobs.lever.co", "jobs.eu.lever.co"],
    read_only_posts=[],
    notes=[
        "Seeded from the lv-spotify and lv-palantir traces (keys only). The cookie banner is declined ('Deny').",
        "No read-only POST was needed to render either form (0 blocked requests in every preview run).",
        "submit_hosts and upload_hosts are the board hosts; the A1.6 live test confirms where the upload goes.",
    ],
)

ASHBY: Final = Playbook(
    ats="ashby",
    form_selector=".ashby-application-form-container form, form",
    open_texts=["Application"],
    rules=[
        FieldRule(key="fullName", id="_systemfield_name", op="fill"),
        FieldRule(key="email", id="_systemfield_email", op="fill"),
        FieldRule(key="resume", id="_systemfield_resume", op="upload", type="file"),
        FieldRule(key="location", id="_systemfield_location", op="choose"),
        FieldRule(key="phone", name="phone", op="fill"),
    ],
    submit_selector=".ashby-application-form-submit-button, button[type=submit]",
    success_text=r"thank(s| you) for applying|successfully submitted|application (was|has been) (submitted|received)",
    error_text=r"missing entry for required field|(is|are) required|please (fill|complete|enter)|"
    r"there (was|were) (an )?errors?",
    closed_text=r"job (posting )?(not found|is no longer (open|available)|has (been )?closed)|no longer accepting",
    submit_hosts=["jobs.ashbyhq.com"],
    upload_hosts=["jobs.ashbyhq.com"],
    read_only_posts=[
        r"^https://jobs\.ashbyhq\.com/api/non-user-graphql\?op=(ApiJobPosting|ApiOrganizationFromHostedJobsPageName)$",
    ],
    notes=[
        "Seeded from the ab-linear and ab-ramp traces (keys only).",
        "Ashby loads the posting and its form through GraphQL POSTs: only those two read-only operations are allowed.",
        "Ashby uploads the resume through its API when it is chosen, so it is attached only in the submit pass.",
    ],
)

BUILTIN: Final[dict[str, Playbook]] = {"greenhouse": GREENHOUSE, "lever": LEVER, "ashby": ASHBY}

_GENERIC: Final = Playbook(ats="generic")


def builtin_id(pb: Playbook) -> str:
    return f"builtin:{pb.ats}@{pb.version}"


def _key_of(rule: FieldRule) -> str | None:
    if rule.key is not None:
        return canonical_key(rule.key)
    return rule.question_key


def _exact(rule: FieldRule, item: FormField) -> bool:
    return bool(
        (rule.name and item.name == rule.name)
        or (rule.id and item.id == rule.id)
        or (rule.selector and item.selector == rule.selector)
    )


def _by_label(rule: FieldRule, item: FormField) -> bool:
    if not (rule.label and re.search(rule.label, item.label, re.I)):
        return False
    if rule.type is not None and rule.type != item.kind:
        return False
    if rule.key == "resume" and item.kind != "file":
        return False  # "Resume text" boxes are questions, not the file
    return not (
        item.kind == "file"
        and rule.key not in ("resume", "coverLetter")
        and rule.key not in NEVER_STORED_RULE_KEYS
    )


def match_rule(item: FormField, playbook: Playbook) -> FieldRule | None:
    """The rule that places this field: the playbook's exact rules (name, id or recorded selector, whatever the field's
    kind), then "Name as per ...", then the never-stored label rules, then the playbook's and the generic label rules.
    Widget hints (no key) are never returned."""
    keyed = [rule for rule in playbook.rules if _key_of(rule) is not None]
    for rule in keyed:
        if _exact(rule, item):
            return rule
    for rule in (*FIRST_RULES, *NEVER_STORED_RULES, *keyed, *GENERIC_RULES):
        if _by_label(rule, item):
            return rule
    return None


def rule_for(item: FormField, playbook: Playbook) -> str | None:
    """The key this field asks for: a canonical profile key (``noticePeriod``, ``workAuthorization``), a never-stored key
    (``pan``), a recorded custom question's ``q:<hash>``, or None for a question the rules cannot place. Work authorisation
    and sponsorship come back bare: `scoped_key` adds the country."""
    rule = match_rule(item, playbook)
    return _key_of(rule) if rule is not None else None


_OP_BY_KIND: Final[dict[str, FieldOp]] = {
    "select": "select", "radio": "select", "checkbox": "check", "combobox": "choose", "checkboxGroup": "group",
    "file": "upload",
}  # fmt: skip


def op_for(item: FormField, playbook: Playbook) -> FieldOp:
    """How a field is operated: the matching rule's op, else the playbook's widget hint for its kind, else by its kind."""
    rule = match_rule(item, playbook)
    if rule is not None and rule.op is not None:
        return rule.op
    for hint in playbook.rules:
        if _key_of(hint) is None and hint.op is not None and hint.type == item.kind:
            return hint.op
    return _OP_BY_KIND.get(item.kind, "fill")


def generic_rule_for(item: FormField) -> str | None:
    """`rule_for` with no playbook: the never-stored and generic label rules only."""
    return rule_for(item, _GENERIC)


__all__ = [
    "ASHBY",
    "ATTESTATION_KEYS",
    "BUILTIN",
    "COUNTRY_KEYS",
    "FIRST_RULES",
    "GENERIC_RULES",
    "GREENHOUSE",
    "KEY_ALIASES",
    "LEVER",
    "NEVER_STORED_RULES",
    "NEVER_STORED_RULE_KEYS",
    "ON_SITE_KEYS",
    "PER_OFFER_KEYS",
    "PROFILE_KEYS",
    "REQUIRED_ONLY_KEYS",
    "SELF_ID_KEYS",
    "SENSITIVE_KEYS",
    "FieldOp",
    "FieldRule",
    "NeverStoredKey",
    "Playbook",
    "ProfileKey",
    "RuleKey",
    "builtin_id",
    "canonical_key",
    "generic_rule_for",
    "legacy_names",
    "match_rule",
    "op_for",
    "rule_for",
    "scoped_key",
]
