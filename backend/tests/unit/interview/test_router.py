"""The router decides, in code, what a turn is. Table tests: a button always wins; a phrase counts only as the whole of a
short turn (or, for "I don't know", as the opening of a short answer), never inside a real answer."""

from __future__ import annotations

import pytest

from tayyari.interview.router import classify, normalise


@pytest.mark.parametrize(
    "text",
    [
        "say that again",
        "Say that again.",
        "Can you say that again please?",
        "could you repeat that",
        "repeat",
        "Repeat the question",
        "pardon",
        "Pardon me?",
        "come again",
        "Um, say that again",
        "sorry what",
        "what was the question",
        "I didn't catch that",
        "once more",
        "Say it again!",
    ],
)
def test_repeat(text: str) -> None:
    assert classify(text) == "repeat"


@pytest.mark.parametrize(
    "text",
    [
        "skip",
        "Skip.",
        "skip this one",
        "skip this question",
        "let's skip",
        "Let's skip this one please",
        "next question",
        "next question please",
        "can we move on",
        "I'd like to skip",
        "pass on this",
    ],
)
def test_skip(text: str) -> None:
    assert classify(text) == "skip"


@pytest.mark.parametrize(
    "text",
    [
        "I don't know",
        "I don't know.",
        "I do not know",
        "i dont know",  # typed without the apostrophe
        "no idea",
        "No idea.",
        "I have no idea",
        "not sure",
        "I'm not sure",
        "I am not sure about that one",
        "I can't remember",
        "I can't recall anything specific",
        "I can't think of one",
        "I cannot remember",
        "pass",
        "Pass.",
        "Um, I don't know",
        "Honestly I don't know",
        "Well, no idea really",
        "I don’t know",  # a curly apostrophe, as a keyboard or a speech engine writes it
    ],
)
def test_dont_know(text: str) -> None:
    assert classify(text) == "dont_know"


@pytest.mark.parametrize(
    "text",
    [
        "That's not what my resume says",
        "that is not what my resume says about it",
        "That's not what I wrote",
        "My resume doesn't say that",
        "I didn't lead the whole thing. I led the storefront part.",
        "I did not build the whole checkout",
        "I never owned everything",
        "Not quite right",
        "That's not quite right, I only reviewed it",
        "That's wrong",
        "That's not accurate",
        "That is not how my profile says it",
    ],
)
def test_dispute(text: str) -> None:
    assert classify(text) == "dispute"


@pytest.mark.parametrize("text", ["", "   ", "yes", "Um.", "ok", "...", "?"])
def test_unclear_is_fewer_than_two_words(text: str) -> None:
    assert classify(text) == "unclear"


@pytest.mark.parametrize(
    "text",
    [
        "I merged the three steps into one page with a reducer for the checkout state.",
        # a phrase inside a real answer is part of the answer
        "I would skip the cache layer and read straight from the database",
        "We had to repeat the migration twice because the first one failed halfway",
        "I think the biggest one was the images? I'm not sure how we measured it, the PM had a dashboard.",
        "The team said that was not what the resume says so we fixed the deck, and then I rebuilt the form",
        # a don't-know phrase that opens a long answer stays an answer
        "I don't know the exact numbers but I remember we cut the bundle by removing moment and lodash and splitting routes",
        "Not sure, but I think it was the images and the fonts and maybe the third party scripts on the page that mattered most",
        # two words are enough to be an answer
        "Safe-area insets",
        "Honestly it depends on the team and the product and how much time there is before the release date, so I usually ask first",
    ],
)
def test_everything_else_is_an_answer(text: str) -> None:
    assert classify(text) == "answer"


def test_a_long_answer_is_never_a_dont_know() -> None:
    long = "I don't know " + "and " * 30 + "so on"
    assert classify(long) == "answer"
    near = "I don't know " + "word " * 9
    assert classify(near) == "answer"  # more than eight words after the phrase
    short = "I don't know " + "word " * 5
    assert classify(short) == "dont_know"


@pytest.mark.parametrize("action", ["repeat", "skip", "dont_know"])
def test_a_button_always_wins(action: str) -> None:
    for text in (
        "",
        "I merged the three steps into one page with a reducer",
        "That's not what my resume says",
    ):
        assert classify(text, action) == action  # type: ignore[arg-type]


def test_the_plain_answer_action_still_reads_the_words() -> None:
    assert classify("skip", "answer") == "skip"
    assert classify("I merged the three steps into one page with a reducer", "answer") == "answer"
    assert classify("I merged the three steps into one page with a reducer", None) == "answer"


def test_normalise_folds_case_apostrophes_and_punctuation() -> None:
    assert normalise("  I Don’t KNOW!!  ") == "i don't know"
    assert normalise("Say…that,again") == "say that again"
    assert normalise("नमस्ते दुनिया") == "नमस्ते दुनिया"  # other scripts are words too
