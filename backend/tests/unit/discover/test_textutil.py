"""Reading a posting's text: HTML to plain text, requirement lines and the years asked."""

from __future__ import annotations

import pytest

from tayyari.discover.textutil import (
    MAX_TEXT_CHARS,
    YearsAsked,
    clean_text,
    html_to_text,
    in_spans,
    requirement_spans,
    sections,
    years_asked,
)


def test_escaped_html_from_greenhouse_becomes_plain_lines() -> None:
    raw = "&lt;h2&gt;About&lt;/h2&gt;&lt;p&gt;Build &amp;amp; ship.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;React&lt;/li&gt;&lt;li&gt;Jest&lt;/li&gt;&lt;/ul&gt;"
    lines = [line for line in html_to_text(raw).splitlines() if line]
    assert lines == ["About", "Build & ship.", "- React", "- Jest"]


def test_real_markup_script_and_entities_are_handled() -> None:
    raw = (
        "<div>Hello&nbsp;<b>world</b><script>alert(1)</script><style>p{}</style><br>Next &amp;amp; last</div>"
    )
    text = html_to_text(raw)
    assert "alert" not in text and "p{}" not in text
    assert text.splitlines()[0] == "Hello world"
    assert text.splitlines()[1] == "Next & last"


def test_whitespace_collapses_and_the_text_is_cut_at_the_limit() -> None:
    assert clean_text("  a   b \n\n\n\n c  ") == "a b\n\nc"
    long = "word " * 10_000
    assert len(html_to_text(long)) <= MAX_TEXT_CHARS
    assert html_to_text(None) == "" and html_to_text("") == ""


def test_lines_under_a_requirements_heading_and_lines_with_a_cue_are_requirements() -> None:
    text = (
        "About us\nWe make payments.\n\nWhat you'll need:\n- React\n- Jest\n\nBenefits\nFree lunch\n\n"
        "Also:\nStrong experience with GraphQL is required."
    )
    spans = requirement_spans(text)
    assert in_spans(spans, text.index("React"))
    assert in_spans(spans, text.index("Jest"))
    assert not in_spans(spans, text.index("payments"))
    assert not in_spans(spans, text.index("lunch"))
    assert in_spans(spans, text.index("GraphQL"))


@pytest.mark.parametrize(
    ("sentence", "minimum", "upper"),
    [
        ("5+ years of experience with React", 5, None),
        ("3-5 years of experience in sales", 3, 5),
        ("3 to 5 years experience", 3, 5),
        ("At least 4 years of professional experience", 4, None),
        ("Minimum of 2 yrs experience in accounts", 2, None),
        ("0-2 years of experience", 0, 2),
        ("Up to 2 years of experience", 0, 2),
        ("6 or more years of relevant experience", 6, None),
        ("1+ year of experience", 1, None),
    ],
)
def test_the_years_a_posting_asks_for(sentence: str, minimum: int, upper: int | None) -> None:
    assert years_asked(sentence) == YearsAsked(minimum, upper)


def test_the_smallest_number_of_a_sentence_and_the_largest_of_the_sentences() -> None:
    text = "- 8+ years of experience in software\n- 3+ years of experience with React\n"
    assert years_asked(text) == YearsAsked(8, None)
    assert years_asked("5-8 years of experience") == YearsAsked(5, 8)


@pytest.mark.parametrize(
    "text",
    [
        "Founded 12 years ago, we serve retailers.",
        "We have been in business for 10 years.",
        "Our warranty lasts 5 years.",
        "A great team with a 4 year plan",
        "No numbers here.",
        "",
    ],
)
def test_years_that_are_not_a_requirement_are_not_read_as_one(text: str) -> None:
    assert years_asked(text) is None


def test_years_under_a_requirements_heading_count_without_the_word_experience() -> None:
    text = "Requirements:\n- 4 years in frontend roles\n"
    assert years_asked(text) == YearsAsked(4, None)


def test_the_parts_about_the_company_and_its_offer_are_noise_until_the_next_heading() -> None:
    text = (
        "Senior Engineer\n\nAbout us\nWe are a leader in payments.\n\nThe role\nBuild things.\n\n"
        "Requirements:\n- React\n\nBenefits and perks\nLearning budget\n\nEqual opportunity\nWe welcome everyone.\n\n"
        "How to apply:\nSend a note."
    )
    required, noise = sections(text)
    assert in_spans(noise, text.index("leader"))
    assert not in_spans(noise, text.index("Build things"))
    assert not in_spans(noise, text.index("React")) and in_spans(required, text.index("React"))
    assert in_spans(noise, text.index("Learning budget"))
    assert in_spans(noise, text.index("welcome"))
    assert in_spans(noise, text.index("Send a note"))
    assert not required or not in_spans(required, text.index("leader"))


def test_about_the_role_is_the_job_and_not_noise() -> None:
    text = "About the role\nYou will build React apps.\n\nAbout You\n- TypeScript\n"
    required, noise = sections(text)
    assert noise == []
    assert in_spans(required, text.index("TypeScript"))


def test_control_characters_never_reach_storage() -> None:
    # Postgres refuses NUL in text columns, so one stray byte in a posting would fail a whole board's insert.
    from tayyari.discover.textutil import clean_text, strip_control

    assert strip_control("Senior\x00 Engineer\x07") == "Senior Engineer"
    assert clean_text("Line one\x00\nLine\x1f two\tend") == "Line one\nLine two end"
    assert "\x00" not in clean_text("\x00" * 5 + "text")
