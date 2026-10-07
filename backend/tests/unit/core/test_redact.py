from __future__ import annotations

import time
from typing import Any

import pytest

from tayyari.core.redact import (
    ADDRESS_MASK,
    EMAIL_MASK,
    PHONE_MASK,
    SENSITIVE_KEYS,
    redact_event_dict,
    redact_pii,
)

NBSP = chr(0xA0)
EN_DASH = chr(0x2013)


# ----------------------------------------------------------------------------- emails


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("john.doe@example.com", EMAIL_MASK),
        ("Email: jane_doe+cv@mail.example.co.uk.", f"Email: {EMAIL_MASK}."),
        ("a@b.io, c@d.org and e@f.dev", f"{EMAIL_MASK}, {EMAIL_MASK} and {EMAIL_MASK}"),
        ("<first.last@sub.domain-name.com>", f"<{EMAIL_MASK}>"),
        ("UPPER@CASE.COM", EMAIL_MASK),
        ("mailto:me@x.in?subject=Hi", f"mailto:{EMAIL_MASK}?subject=Hi"),
    ],
)
def test_emails_are_masked(given: str, expected: str) -> None:
    assert redact_pii(given) == expected


@pytest.mark.parametrize("untouched", ["@jsmith on Twitter", "a@b", "no email here", "x@y@z"])
def test_non_emails_are_left_alone(untouched: str) -> None:
    assert redact_pii(untouched) == untouched


# ----------------------------------------------------------------------------- phones


@pytest.mark.parametrize(
    "phone",
    [
        "+91 98765 43210",
        "+91-9876543210",
        "+91 9876543210",
        "9876543210",
        "098765-43210",
        "(080) 2345 6789",
        "080-2345-6789",
        "+44 20 7946 0958",
        "+1 (415) 555-2671",
        "(415) 555-2671",
        "415.555.2671",
        "415-555-2671",
        "+49 30 12345678",
        "+65 9123 4567",
        "+1-555-0100",  # 8 digits are enough behind a plus
        "612 345 678",
        "+33 6 12 34 56 78",
        "+91" + NBSP + "98765" + NBSP + "43210",
    ],
)
def test_phone_numbers_are_masked(phone: str) -> None:
    assert redact_pii(phone) == PHONE_MASK
    assert redact_pii(f"Call me on {phone} tomorrow") == f"Call me on {PHONE_MASK} tomorrow"
    assert redact_pii(f"Phone: {phone}") == f"Phone: {PHONE_MASK}"


@pytest.mark.parametrize(
    "untouched",
    [
        "2018 - 2022",
        "2019-2023",
        "2018 - 2019 - 2020",
        "Jan 2020 " + EN_DASH + " Mar 2022",
        "2023-05-14",
        "2023-11-05T12:34:56Z",
        "12/05/2021",
        "12.05.2021",
        "since 1998",
        "Improved latency by 45% across 12000 requests",
        "560034",
        "Version 3.11.4",
        "1,200,000 users",
        "IDs: 12345678",
        "555-0100",
        "GPA 9.12/10",
        "5 years",
        "ISO 9001:2015",
        "2018\n2019\n2020",
        "12345678901234567890",
    ],
)
def test_years_dates_and_short_numbers_are_not_phones(untouched: str) -> None:
    assert redact_pii(untouched) == untouched


def test_a_phone_never_spans_two_lines() -> None:
    assert redact_pii("98765\n43210") == "98765\n43210"


def test_hex_ids_are_not_mistaken_for_phones() -> None:
    pid = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert redact_pii(pid) == pid
    assert redact_pii(f"profile 12345678901a{pid}") == f"profile 12345678901a{pid}"


# ----------------------------------------------------------------------------- addresses


@pytest.mark.parametrize(
    "line",
    [
        "Address: 12 MG Road, Bengaluru 560001",
        "Permanent Address - H.No. 12-3-45, Banjara Hills, Hyderabad",
        "ADDRESS: somewhere near the old mill",
        "221B Baker Street, London",
        "1600 Pennsylvania Avenue NW",
        "742 Evergreen Terrace Blvd.",
        "42 Galaxy Rd.",
        "Flat 402, Sunrise Apartments, 5th Cross, Koramangala, Bengaluru 560034",
        "Plot No. 21, Sector 14, Gurugram, Haryana 122001",
        "San Francisco, CA 94103",
        "Bengaluru, Karnataka 560034",
        "Austin, TX 78701-1234",
    ],
)
def test_address_lines_are_masked(line: str) -> None:
    assert redact_pii(line) == ADDRESS_MASK
    assert (
        redact_pii(f"Jane Doe\n{line}\nSoftware Engineer") == f"Jane Doe\n{ADDRESS_MASK}\nSoftware Engineer"
    )


@pytest.mark.parametrize(
    "line",
    [
        "Led Phase 2 rollout, QA, and release",
        "- Led a team of 12 engineers on the Main Street redesign",
        "* Reduced onboarding from 12 days to 3 across Park Road branches",
        "Built a roadmap for 5 products",
        "Location: Bengaluru, India",
        "Software Engineer at Acme, 2019 - 2022",
        "Managed 3 teams",
        "Senior Engineer | Bengaluru, India",
    ],
)
def test_ordinary_resume_lines_are_not_addresses(line: str) -> None:
    assert redact_pii(line) == line


def test_line_endings_are_preserved() -> None:
    given = "Name\r\nAddress: 1 Main St.\r\nSkills: Python\r\n"
    assert redact_pii(given) == f"Name\r\n{ADDRESS_MASK}\r\nSkills: Python\r\n"


def test_a_realistic_resume_header() -> None:
    given = (
        "Asha Verma\n"
        "asha.verma@example.com | +91 98765 43210 | linkedin.com/in/asha-verma\n"
        "Flat 12, Lotus Residency, 4th Cross, Indiranagar, Bengaluru 560038\n"
        "\n"
        "EXPERIENCE\n"
        "Frontend Engineer, Acme (2021 - 2024)\n"
        "- Cut bundle size by 38% across 14 routes\n"
    )
    out = redact_pii(given)
    assert "asha.verma@example.com" not in out
    assert "98765" not in out
    assert "Lotus" not in out
    assert "linkedin.com/in/asha-verma" in out
    assert "Asha Verma" in out
    assert "(2021 - 2024)" in out
    assert "38%" in out and "14 routes" in out
    assert out.splitlines()[1] == f"{EMAIL_MASK} | {PHONE_MASK} | linkedin.com/in/asha-verma"


# ----------------------------------------------------------------------------- general behaviour


@pytest.mark.parametrize("given", ["", "   ", "plain text only", "no digits, no @ sign"])
def test_text_without_contact_data_is_returned_unchanged(given: str) -> None:
    assert redact_pii(given) == given


def test_redact_pii_is_idempotent() -> None:
    given = "me@x.com +91 98765 43210\nAddress: 1 Main Road\nSkills 2019 - 2022"
    once = redact_pii(given)
    assert redact_pii(once) == once


@pytest.mark.parametrize(
    "hostile",
    [
        "a" * 200_000,
        "1 " * 100_000,
        "1" * 200_000,
        "a@" * 50_000,
        "x" * 100_000 + "@",
        "(" * 100_000,
        "1-" * 100_000,
        "12 Main " * 25_000,
        ("a" * 60 + ".") * 3000 + "@example.com",
        "1.2.3.4 " * 30_000,
    ],
    ids=[
        "letters",
        "digit-space",
        "digits",
        "at-signs",
        "long-local",
        "parens",
        "digit-dash",
        "street",
        "dots",
        "ips",
    ],
)
def test_hostile_input_is_scanned_in_linear_time(hostile: str) -> None:
    started = time.perf_counter()
    redact_pii(hostile)
    assert time.perf_counter() - started < 3.0


# ----------------------------------------------------------------------------- the structlog processor


def run(event: dict[str, Any]) -> dict[str, Any]:
    return dict(redact_event_dict(None, "info", event))


def test_sensitive_keys_are_replaced_with_their_length() -> None:
    out = run({"event": "reader_started", **{k: "x" * 12 for k in SENSITIVE_KEYS}})
    assert out["event"] == "reader_started"
    for key in SENSITIVE_KEYS:
        assert out[key] == "<redacted 12 chars>"


def test_the_spec_lists_these_sensitive_keys() -> None:
    assert SENSITIVE_KEYS == {
        "resume",
        "text",
        "content",
        "notes",
        "message",
        "state",
        "prompt",
        "system",
        "body",
        "raw",
        "excerpt",
    }


def test_sensitive_key_matching_is_case_insensitive() -> None:
    out = run({"event": "e", "Resume": "my whole resume", "PROMPT": "abc"})
    assert out["Resume"] == "<redacted 15 chars>"
    assert out["PROMPT"] == "<redacted 3 chars>"


def test_other_strings_get_pii_masking() -> None:
    out = run(
        {"event": "failed for jane@example.com", "reason": "call +91 98765 43210", "count": 12, "ok": True}
    )
    assert out["event"] == f"failed for {EMAIL_MASK}"
    assert out["reason"] == f"call {PHONE_MASK}"
    assert out["count"] == 12
    assert out["ok"] is True


def test_non_string_sensitive_values_are_redacted_by_size() -> None:
    out = run(
        {"event": "e", "state": {"resume": "a" * 100}, "raw": b"12345", "content": None, "body": ["x", "y"]}
    )
    assert out["state"].startswith("<redacted ") and "chars>" in out["state"]
    assert out["raw"] == "<redacted 5 chars>"
    assert out["content"] is None
    assert out["body"] == f"<redacted {len(str(['x', 'y']))} chars>"


def test_empty_strings_are_left_for_unlisted_keys_and_counted_for_listed_ones() -> None:
    out = run({"event": "e", "note": "", "text": ""})
    assert out["note"] == ""
    assert out["text"] == "<redacted 0 chars>"


def test_nested_structures_are_walked() -> None:
    out = run(
        {
            "event": "analysis_done",
            "payload": {
                "resume": "secret resume",
                "contact": "me@x.com",
                "deeper": [{"text": "abc"}, "bob@y.org"],
            },
            "emails": ("a@b.co", 3),
        }
    )
    assert out["payload"]["resume"] == "<redacted 13 chars>"
    assert out["payload"]["contact"] == EMAIL_MASK
    assert out["payload"]["deeper"] == [{"text": "<redacted 3 chars>"}, EMAIL_MASK]
    assert out["emails"] == (EMAIL_MASK, 3)


def test_structural_fields_are_never_touched() -> None:
    event = {
        "event": "e",
        "timestamp": "2026-10-07T12:34:56.789012Z",
        "level": "info",
        "logger": "tayyari.core",
        "request_id": "1234567890123456",
        "profile_id": "sample-12345678901234567890123456789012",
        "ip_hash": "0123456789abcdef",
        "exc_info": (ValueError, ValueError("x"), None),
    }
    out = run(dict(event))
    assert out == event


def test_processor_mutates_and_returns_the_same_mapping() -> None:
    event: dict[str, Any] = {"event": "e", "text": "abc"}
    assert redact_event_dict(None, "info", event) is event
    assert event["text"] == "<redacted 3 chars>"


def test_depth_is_bounded() -> None:
    nested: Any = "deep@example.com"
    for _ in range(10):
        nested = {"level": nested}
    out = run({"event": "e", "tree": nested})
    assert out["tree"] is not None  # no recursion error
