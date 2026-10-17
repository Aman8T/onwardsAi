"""The small text checks every other module leans on: a quote is a passage of what was said, a name or number is in the sources, a
sentence does not read the person, a Fix step is advice. Each has a known-bad example."""

from __future__ import annotations

import pytest

from tayyari.interview.text import (
    claims_person,
    clip_quote,
    few_words,
    has_personality,
    inflated,
    locate,
    outside_brackets,
    passage,
    placeholders_of,
    shared_words,
    starts_with_verb,
    tokens,
    unseen_openers,
    unsupported,
)

# ───────────────────────── personality ─────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "You sounded confident.",
        "A lack of confidence showed.",
        "You seemed nervous.",
        "Your tone was calm.",
        "Your accent was strong.",
        "Good energy.",
        "An energetic answer.",
        "Very enthusiastic.",
        "Real charisma.",
        "Too many filler words.",
        "You said um twice.",
        "Great body language.",
        "Steady eye contact.",
        "A culture fit.",
        "A culture-fit answer.",
        "A good attitude.",
        "A strong personality.",
        "Passionate about React.",
        "Shows real passion.",
    ],
)
def test_words_that_read_the_person_are_caught(text: str) -> None:
    assert has_personality(text)


@pytest.mark.parametrize(
    "text",
    [
        "The answer names the fix and the browser it was in.",
        "Say which change was yours.",
        "Find the pull request behind the LCP change.",
        "Umbrella terms are vague.",  # "um" only as a word
        "A stone wall of text.",  # "tone" only as a word
        "",
    ],
)
def test_ordinary_sentences_are_not_personality(text: str) -> None:
    assert not has_personality(text)


def test_a_word_the_persons_own_answer_or_source_uses_is_their_content_not_a_reading_of_them() -> None:
    assert has_personality("They work at an energy company.")
    assert not has_personality("They work at an energy company.", "I built dashboards for an energy utility")
    assert not has_personality("Your confidence in the model.", "We measured model confidence on 200 samples")
    assert has_personality(
        "You sounded nervous and confident.", "We measured model confidence"
    )  # nervous is still unsaid


# ───────────────────────── names, numbers, openers ─────────────────────────


def test_a_number_or_name_must_be_in_one_of_the_parts() -> None:
    parts = (
        "Rebuilt the checkout in React at Kitebird; LCP went from 4.1s to 1.9s.",
        "We shipped it in March",
    )
    assert unsupported("LCP went from 4.1s to 1.9s at Kitebird", *parts) == []
    assert unsupported("It went from 4.1s to 2.9s", *parts) == ["2.9"]
    assert unsupported("We used Kubernetes at Zomato", *parts) == ["Kubernetes", "Zomato"]


def test_a_name_that_opens_a_sentence_is_caught_by_the_opener_check() -> None:
    parts = ("Rebuilt the checkout in React at Kitebird",)
    assert (
        unsupported("Zomato's checkout was slow. I rebuilt it.", *parts) == []
    )  # the anchors skip sentence openers
    assert unseen_openers("Zomato's checkout was slow. I rebuilt it.", *parts) == ["Zomato"]
    assert unseen_openers("I rebuilt it. Kitebird's checkout was slow. Razorpay failed.", *parts) == [
        "Razorpay"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "I rebuilt the checkout.",
        "The payment step was slow. After that, we measured it.",
        "On the cart page, nothing changed.",
        "Built the page and shipped it in a week.",  # a verb: not a name
        "Fixing the footer took a day.",
        "React was the plan. Kitebird wanted it.",  # in the parts
        "",
    ],
)
def test_ordinary_openers_and_verbs_and_words_from_the_parts_are_not_flagged(text: str) -> None:
    assert unseen_openers(text, "React and Kitebird") == []


def test_a_seniority_verb_must_be_in_the_parts_in_some_form() -> None:
    assert inflated("I led the migration", "I did the migration") == ["led"]
    assert inflated("I led the migration", "Leader of the migration") == []
    assert inflated("I owned the pager and managed two interns", "paged on call, mentored two interns") == [
        "owned",
        "managed",
    ]
    assert inflated("on my own time I wrote docs", "wrote docs") == []  # "my own" is not ownership
    assert inflated("I wrote the docs", "") == []


@pytest.mark.parametrize(
    "text",
    [
        "You have built a fast checkout.",
        "You've shipped this before.",
        "Your experience shows speed.",
        "Your resume already says it.",
        "I built the checkout.",
        "You are a strong candidate.",
    ],
)
def test_a_fix_step_may_not_say_what_the_person_has_done(text: str) -> None:
    assert claims_person(text)


@pytest.mark.parametrize(
    "text",
    [
        "Find the pull request behind the LCP change.",
        "Write down one real example.",
        "Pick one handoff and note what shipped.",
    ],
)
def test_advice_makes_no_claim_about_the_person(text: str) -> None:
    assert not claims_person(text)


@pytest.mark.parametrize(
    ("text", "verb"),
    [
        ("Find the pull request", True),
        ("Write down one real example", True),
        ("Pick one handoff", True),
        ("Rehearse the story aloud", True),
        ("Focus on the one change", True),
        ("  Add one line", True),
        ("The LCP change matters", False),
        ("You should find the report", False),
        ("Finding the report helps", False),
        ("Finds the report", False),
        ("Quickly find it", False),
        ("1 thing to do", False),
        ("", False),
    ],
)
def test_a_fix_step_starts_with_a_verb(text: str, verb: bool) -> None:
    assert starts_with_verb(text) is verb


# ───────────────────────── brackets ─────────────────────────


def test_placeholders_are_listed_in_order_and_malformed_brackets_are_refused() -> None:
    assert placeholders_of("I used [the tool you used] and [a number you can stand behind].") == [
        "the tool you used",
        "a number you can stand behind",
    ]
    assert placeholders_of("No brackets here") == []
    for bad in (
        "I [x] it",
        "I [ ] it",
        "stray ] bracket",
        "stray [ bracket",
        "[nested [inner] one]",
        f"[{'a' * 61}]",
    ):
        assert placeholders_of(bad) is None, bad
    assert placeholders_of(f"[{'a' * 60}]") == ["a" * 60] and placeholders_of("[ab]") == ["ab"]


def test_text_outside_the_brackets_is_what_code_must_be_able_to_source() -> None:
    assert (
        outside_brackets("I used [the tool you used] to cut [what changed] to 1.9s.")
        == "I used to cut to 1.9s."
    )


# ───────────────────────── quotes ─────────────────────────


def test_a_passage_is_found_after_folding_and_needs_three_to_forty_words() -> None:
    from tayyari.jobsearch.judge import fold

    folded = [fold("I rolled it back; it didn’t ship. We tried again the next day")]
    assert passage("it DIDN'T ship", folded) and passage("rolled it back", folded)
    assert not passage("it shipped", folded) and not passage("rolled back", folded)  # two words
    assert not passage("never said anything like this", folded)
    assert not passage(" ".join(["word"] * 41), [fold(" ".join(["word"] * 41))])


def test_wider_limits_are_for_code_and_a_passage_is_never_under_five_characters() -> None:
    from tayyari.jobsearch.judge import fold

    folded = [fold('<p>{pending ? "Applying coupon…" : total}</p>')]
    assert passage("Applying coupon…", folded, min_words=1, max_words=60)
    assert not passage("Applying coupon…", folded)  # two words under the speech limits
    assert not passage("a", [fold("a b c")], min_words=1, max_words=60)
    assert passage("pending", folded, min_words=1, max_words=60)


def test_locate_gives_back_the_wording_of_the_answer() -> None:
    answer = 'Our designer said "it can’t be built" so I wrote a safe-area fix'
    assert locate(answer, "so I wrote a safe-area fix") == "so I wrote a safe-area fix"
    assert locate(answer, 'DESIGNER said "it can\'t be built"  so') == 'designer said "it can’t be built" so'
    assert locate(answer, "so I wrote a safe–area fix") == "so I wrote a safe-area fix"
    assert locate(answer, "words that are not there") == "words that are not there"
    assert locate(answer, "   ") == ""


# ───────────────────────── words ─────────────────────────


def test_a_topic_is_the_first_few_words_without_trailing_punctuation() -> None:
    assert few_words("Strong proficiency in React (must-have), along with JavaScript and TypeScript") == (
        "Strong proficiency in React"
    )
    assert (
        few_words("Experience with Git and basic performance optimization") == "Experience with Git and basic"
    )
    assert few_words("Short") == "Short" and few_words("") == ""
    assert few_words("Extraordinarily-long-single-word-with-no-spaces-at-all-anywhere") == (
        "Extraordinarily-long-single-word"
    )
    assert len(few_words("word " * 40)) <= 32


def test_a_quote_inside_a_sentence_is_clipped_at_a_word_boundary() -> None:
    assert clip_quote("a short line") == "a short line"
    clipped = clip_quote("one two three four five six seven eight nine ten eleven twelve " * 3)
    assert len(clipped) <= 90 and clipped.endswith("…") and " " not in clipped[-2:]


def test_shared_words_ignore_stop_words_plurals_and_case() -> None:
    assert tokens("Strong experience with React components") == {"react", "component"}
    assert shared_words("Experience with GraphQL APIs and Apollo Client", "REST & GraphQL API contracts") == 2
    assert shared_words("Solid understanding of design", "something else entirely") == 0
