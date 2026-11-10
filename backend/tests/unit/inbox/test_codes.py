"""The verification-code extractor (code, no model): real codes near their keywords in the subject or the text, ATS links
only on the allowlist, and the known-bad cases that must not fire: order numbers, phone numbers, years, times, amounts,
references, codes far from any keyword, links on other hosts."""

from __future__ import annotations

import pytest

from tayyari.inbox.codes import extract, find_code, find_link, on_ats_host, redact, scrub


@pytest.mark.parametrize(
    ("subject", "text", "code"),
    [
        ("Your Greenhouse verification code", "Use 482913 to continue your application.", "482913"),
        ("Security code: 7XK2P9", "", "7XK2P9"),
        ("Verify your email", "Your one-time passcode is A1B2C3. It expires in 10 minutes.", "A1B2C3"),
        ("", "Your OTP: 5521", "5521"),
        ("", "Confirmation code 90X1QZ for your Workday account", "90X1QZ"),
        ("", "482913 is your verification code.", "482913"),
        ("Your code", "Enter this verification code:\n\n  309127  \n\nThanks, Lever", "309127"),
        ("", "Hi,\nYour security code for Ashby is 8812.\nThe Ashby team", "8812"),
    ],
)
def test_codes_near_their_keywords(subject: str, text: str, code: str) -> None:
    found = extract(subject, text)
    assert found is not None and found.code == code and found.link is None


@pytest.mark.parametrize(
    "text",
    [
        "Thanks for your order #12345678. Your verification is complete.",
        "Order number: 88231 - verification of payment received",
        "Questions about verification? Call us at +1 (415) 555-0134.",
        "To verify, call 1800 425 1234 today.",
        "Verification of your 2026 application season has started.",
        "Your verification call is at 10:30 tomorrow.",
        "Verification fee of $1500 was refunded.",
        "Please verify your details in ticket REF 99231.",
        "Job ID 4012345: please verify your phone number.",
        "Verify your email address to continue.",
        "Version 2.5.1 of our verification app is out.",
    ],
)
def test_numbers_that_are_not_codes(text: str) -> None:
    assert find_code(text) is None


@pytest.mark.parametrize(
    "text",
    [
        # employer replies that say "verify" or "one-time" but carry no code: they must reach the classifier
        "Please verify your availability for an interview on the 14th of November.",
        "Please verify you can attend at 10am on Monday.",
        "Background verification will follow for role SDE2.",
        "Please complete the one-time onboarding form by Nov 30th.",
        "Your interview is in Room B204. Please verify your ID at reception.",
        "Please verify your postal code 560001 for the background verification.",
        "Use promo code SAVE2026 - verification of your offer is underway.",
        "We would like to verify your references; the job code is ENG4521.",
    ],
)
def test_employer_replies_that_say_verify_are_not_codes(text: str) -> None:
    assert extract("Next steps for your application", text) is None


def test_a_plain_code_needs_a_keyword_in_the_email() -> None:
    assert find_code("Your code is 482913") is None
    assert find_code("Your code is 482913. Use it to verify your email.") == "482913"


def test_a_number_far_from_any_keyword_is_not_a_code() -> None:
    text = "Your verification is done." + " Lorem ipsum dolor sit amet." * 6 + " Our office is in suite 4410."
    assert find_code(text) is None


def test_no_keyword_no_code() -> None:
    assert extract("Thanks for applying", "Your application 552134 was received.") is None


def test_the_subject_wins_over_the_text() -> None:
    found = extract("Your verification code is 111AAA", "verification code: 222BBB")
    assert found is not None and found.code == "111AAA"


def test_the_code_closest_after_the_keyword_wins() -> None:
    assert find_code("Ref 2201. Your verification code is 482913 (valid 15 minutes).") == "482913"


def test_values_never_appear_in_repr() -> None:
    found = extract("Your verification code", "482913")
    assert found is not None and "482913" not in repr(found)


# ───────────────────────── links ─────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://app.greenhouse.io/users/email_verification?token=abc",
        "https://jobs.lever.co/acme/verify/abc123",
        "https://acme.wd5.myworkdayjobs.com/en-US/careers/activate/xyz",
        "https://jobs.ashbyhq.com/acme/confirm-email?t=1",
        "https://jobs.smartrecruiters.com/verify?token=1",
        "https://apply.workable.com/acme/confirm/1",
    ],
)
def test_an_ats_verification_link(url: str) -> None:
    found = extract("Verify your email", f"Click {url} to continue.")
    assert found is not None and found.link == url and found.code is None


@pytest.mark.parametrize(
    "url",
    [
        "https://greenhouse.io.evil.com/verify?token=1",
        "https://evilgreenhouse.io/verify?token=1",
        "http://app.greenhouse.io/verify?token=1",
        "https://bit.ly/verify-abc",
        "https://app.greenhouse.io/jobs/4012345",  # an ATS host but not a verification link
    ],
)
def test_links_that_are_not_verification_links(url: str) -> None:
    assert extract("Verify your email", f"Click {url}") is None


def test_a_link_needs_a_keyword_in_the_email() -> None:
    assert extract("Thanks for applying", "https://app.greenhouse.io/verify?token=1") is None


def test_a_link_in_the_html_href_counts() -> None:
    html = '<a href="https://app.greenhouse.io/users/email_verification?token=abc&amp;x=1">Verify</a>'
    found = extract("Please verify your email", "Click the button below.", html)
    assert (
        found is not None and found.link == "https://app.greenhouse.io/users/email_verification?token=abc&x=1"
    )
    assert find_link("no links here", None) is None


def test_on_ats_host() -> None:
    assert on_ats_host("https://boards.greenhouse.io/x")
    assert not on_ats_host("https://example.com/greenhouse.io")
    assert not on_ats_host("not a url")


def test_redact_takes_the_code_out_of_a_subject() -> None:
    assert redact("Your code is 482913", "482913") == "Your code is [code]"
    assert redact("Code 4829131 and 482913", "482913") == "Code 4829131 and [code]"
    assert redact("Nothing", None) == "Nothing"


def test_scrub_hides_code_shaped_tokens_in_a_subject_about_codes() -> None:
    assert scrub("Your Greenhouse code is 482913") == "Your Greenhouse code is [code]"
    assert scrub("Your verification code: 7XK2P9") == "Your verification code: [code]"
    assert scrub("Interview at Acme on Nov 14th, 2026") == "Interview at Acme on Nov 14th, 2026"
    assert scrub("Order 12345 shipped") == "Order 12345 shipped"
    assert scrub("") == ""
