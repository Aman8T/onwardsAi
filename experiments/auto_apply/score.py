"""Field-by-field check of a filled form against the test profile. Every arm is scored on the same thing: the DOM state
`common.formscan.scan_page` reads from the browser after the run.

A run is a success when every required field holds a value, every field we can map to the profile holds the right
value, and the resume is attached (when the form has a file field for it). Required questions we cannot map (custom
screening questions) count as answered when they hold any value; that is a deliberately lenient check.
"""

from __future__ import annotations

import re
from typing import Any

from common.profile import PROFILE

# label pattern -> profile key; first match wins. Ordered from specific to general.
RULES: list[tuple[str, str]] = [
    (r"cover\s*letter", "cover_letter"),
    (r"\b(first|given|preferred first)\s*name\b", "first_name"),
    (r"\b(last|family|sur)\s*name\b|\bsurname\b", "last_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|telephone|cell", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"website|portfolio|personal (site|url)|\burl\b", "website"),
    (r"current (company|employer)|^company\b|most recent (company|employer)", "current_company"),
    (r"current (job )?title|current role|current position", "current_title"),
    (r"^country\b|country of residence", "country"),
    (r"\bcity\b", "city"),
    (r"location|where are you (based|located)|current address|^address", "location"),
    (r"\b(full\s*)?name\b", "full_name"),
]
FILE_RESUME = re.compile(r"resume|cv\b|curriculum", re.I)


def key_for(field: dict[str, Any]) -> str | None:
    if field["type"] == "file":
        return "resume" if FILE_RESUME.search(field["label"] or "") or "resume" in (field.get("name") or "").lower() else None
    if field["type"] in ("checkbox", "radio", "checkbox-group"):
        return None
    label = (field["label"] or "").lower().rstrip("* ").strip()
    if len(label) > 50:
        return None  # a long label is a screening question, not a personal-detail field
    for pattern, key in RULES:
        if re.search(pattern, label):
            return key
    return None


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def correct(key: str, value: str) -> bool:
    expected = PROFILE.get(key, "")
    v = (value or "").strip()
    if key == "phone":
        got, exp = _digits(v), _digits(expected)
        return bool(got) and (got.endswith(exp[-7:]) or exp.endswith(got[-7:]))
    if key in ("cover_letter",):
        return len(v) >= 20
    if key in ("location", "city", "country"):
        if key == "country" and v.startswith("+"):  # a phone-number country picker shows the dial code
            return PROFILE["phone"].startswith(v.split()[0])
        return any(part.lower() in v.lower() for part in (PROFILE["city"], PROFILE["country"]))
    if not expected:
        return True  # nothing expected (e.g. linkedin): any value, or none, is fine
    return expected.lower() in v.lower() or v.lower() in expected.lower() and len(v) >= 2


def score(scan: dict[str, Any]) -> dict[str, Any]:
    fields = scan.get("fields", [])
    required = [f for f in fields if f.get("required")]
    wrong, missed, mapped_ok = [], [], 0
    resume_fields = [f for f in fields if key_for(f) == "resume"]
    if not resume_fields:
        resume_fields = [f for f in fields if f["type"] == "file"][:1]  # a lone file field is the resume
    resume_attached = any(f.get("value") for f in resume_fields) if resume_fields else None
    for f in fields:
        key = key_for(f)
        value = f.get("value") or ""
        if f.get("required") and not value:
            missed.append(f["label"][:80])
            continue
        if key and key != "resume" and value:
            if correct(key, value):
                mapped_ok += 1
            else:
                wrong.append({"label": f["label"][:80], "expected": key, "got": value[:80]})
    filled_required = len(required) - len(missed)
    success = bool(fields) and not missed and not wrong and resume_attached is not False
    return {
        "success": success,
        "fields_total": len(fields),
        "required_total": len(required),
        "required_filled": filled_required,
        "mapped_correct": mapped_ok,
        "wrong": wrong,
        "missed_required": missed,
        "resume_attached": resume_attached,
        "captcha_on_page": scan.get("blockers", {}).get("captcha_on_page"),
        "password_field": scan.get("blockers", {}).get("password_field"),
    }
