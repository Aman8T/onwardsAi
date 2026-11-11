"""The coach's action line: the parser and validator (a known-bad fixture per drop reason) and the stream filter."""

from __future__ import annotations

import pytest

from tayyari.chat.actions import (
    Dropped,
    Proposal,
    Trailer,
    TrailerFilter,
    extract,
    parse_trailer,
    validate,
)

TARGETS = {
    "role": {"ai-product-engineer": "AI Product Engineer"},
    "plan": {"p" * 32: "Senior Frontend Engineer, Testing"},
    "match": {"m1": "Staff Engineer"},
    "tracked": {"t-1": "Designer at Acme"},
    "skill": {"react": "React"},
}
ANSWER = "Practise the one claim that wobbled.\n\nWant to try it now?"


def run(chunks: list[str]) -> tuple[str, str, Trailer | None, tuple[Dropped, ...]]:
    """Feed the chunks; the text released as it streamed, the final text, the trailer, what was dropped."""
    filt = TrailerFilter()
    streamed = "".join(filt.feed(c) for c in chunks)
    finished = filt.finish()
    return streamed + finished.text, filt.text, finished.trailer, finished.dropped


# ───────────────────────── parse ─────────────────────────


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("[[action: practice]]", Trailer("practice", None)),
        ("[[action: open_role | ai-product-engineer]]", Trailer("open_role", "ai-product-engineer")),
        ("[[action:open_role|ai-product-engineer]]", Trailer("open_role", "ai-product-engineer")),
        ("[[action: practice | ]]", Trailer("practice", None)),
    ],
)
def test_parse_reads_the_destination_and_target(segment: str, expected: Trailer) -> None:
    assert parse_trailer(segment) == expected


@pytest.mark.parametrize(
    "segment",
    [
        "[[action: ]]",
        "[[action: two words]]",
        "[[action: a | b | c]]",
        "[[action: a]] x",
        "[[note: a]]",
        "[[action: a|b\nc]]",
    ],
)
def test_parse_refuses_what_is_not_a_trailer(segment: str) -> None:
    assert parse_trailer(segment) is None


# ───────────────────────── validate: one known-bad fixture per reason ─────────────────────────


def test_a_listed_destination_with_a_known_target_is_a_proposal_with_the_lists_label_and_the_targets_title() -> (
    None
):
    assert validate(Trailer("open_role", "ai-product-engineer"), TARGETS) == Proposal(
        "open_role", "ai-product-engineer", "Open this role", "AI Product Engineer"
    )
    assert validate(Trailer("open_application", "t-1"), TARGETS) == Proposal(
        "open_application", "t-1", "Open application", "Designer at Acme"
    )
    assert validate(Trailer("practice", None), TARGETS) == Proposal("practice", None, "Start Practice", None)


@pytest.mark.parametrize(
    ("trailer", "reason"),
    [
        (Trailer("delete_account", None), "unknown_destination"),
        (Trailer("Practice", None), "unknown_destination"),
        (Trailer("practice", "t-1"), "unexpected_target"),
        (Trailer("open_role", None), "missing_target"),
        (Trailer("open_role", "no-such-role"), "unknown_target"),
        (Trailer("open_role", "t-1"), "unknown_target"),  # an id of another kind is not a role
        (Trailer("open_plan", "p"), "unknown_target"),
        (Trailer("apply", "t-1"), "unknown_target"),  # a tracked id is not a match id
        (Trailer("open_application", "m1"), "unknown_target"),
    ],
)
def test_a_bad_destination_or_target_is_dropped_with_its_reason(trailer: Trailer, reason: str) -> None:
    dropped = validate(trailer, TARGETS)
    assert isinstance(dropped, Dropped) and dropped.reason == reason


def test_a_target_in_no_context_is_unknown() -> None:
    assert validate(Trailer("open_role", "ai-product-engineer"), {}) == Dropped("unknown_target", "open_role")


# ───────────────────────── the stream filter ─────────────────────────


def test_a_whole_trailer_is_never_streamed_and_closes_the_answer() -> None:
    streamed, final, trailer, dropped = run([f"{ANSWER}\n[[action: practice]]"])
    assert streamed == final == ANSWER
    assert trailer == Trailer("practice", None) and dropped == ()


def test_a_trailer_split_across_deltas_is_held_until_it_is_known() -> None:
    text = f"{ANSWER}\n\n[[action: open_role | ai-product-engineer]]"
    for size in (1, 2, 3, 5, 9):
        chunks = [text[i : i + size] for i in range(0, len(text), size)]
        streamed, final, trailer, dropped = run(chunks)
        assert streamed == final == ANSWER, size
        assert trailer == Trailer("open_role", "ai-product-engineer") and dropped == (), size


def test_nothing_of_a_trailer_reaches_the_client_even_in_pieces() -> None:
    filt = TrailerFilter()
    shown = [filt.feed(c) for c in ["Go.\n", "[", "[act", "ion: prac", "tice", "]", "]"]]
    assert "".join(shown) == "Go."
    assert "[" not in "".join(shown)
    assert filt.finish().trailer == Trailer("practice", None)


def test_whitespace_around_the_trailer_is_not_shown_and_text_with_none_is_unchanged() -> None:
    assert run([f"{ANSWER}  \n\n[[action: practice]]\n\n"])[1] == ANSWER
    assert run([f"{ANSWER}\n\n"])[1] == f"{ANSWER}\n\n"  # no trailer: the answer is exactly what was said


def test_an_ordinary_double_bracket_is_plain_text() -> None:
    text = "See the [[1]] reference and [[note]] too, and a lone [ bracket. [[a]]"
    streamed, final, trailer, dropped = run([text[:20], text[20:40], text[40:]])
    assert streamed == final == text
    assert trailer is None and dropped == ()


def test_a_trailer_in_the_middle_of_the_text_is_dropped_and_not_shown() -> None:
    streamed, final, trailer, dropped = run(["Try this [[action: practice]] and then rest.\nQuestion?"])
    assert "[[" not in streamed and "action" not in streamed
    assert final == "Try this  and then rest.\nQuestion?"
    assert trailer is None
    assert dropped == (Dropped("mid_text", "practice"),)


def test_two_trailers_at_the_end_are_handed_back_in_order_and_none_is_shown() -> None:
    filt = TrailerFilter()
    streamed = filt.feed(f"{ANSWER}\n[[action: practice]]\n[[action: weekly]]")
    finished = filt.finish()
    assert streamed + finished.text == filt.text == ANSWER
    assert finished.trailers == (Trailer("practice", None), Trailer("weekly", None))
    assert (
        finished.trailer == Trailer("practice", None) and finished.dropped == ()
    )  # the caller keeps the first valid one


def test_a_trailer_in_the_middle_makes_a_later_one_at_the_end_multiple() -> None:
    streamed, final, trailer, dropped = run(["A [[action: practice]] b.\n[[action: weekly]]"])
    assert final == streamed == "A  b."
    assert trailer is None
    assert [d.reason for d in dropped] == ["mid_text", "multiple"]
    assert dropped[-1].destination == "weekly"


def test_a_half_trailer_at_the_end_of_a_cut_off_stream_is_not_shown() -> None:
    for cut in ("[[action: pra", "[[", "[[action:", "[[action: open_role | ai-pro"):
        streamed, final, trailer, dropped = run([f"{ANSWER}\n", cut])
        assert final == streamed == ANSWER, cut
        assert trailer is None
        assert dropped == (Dropped("incomplete"),), cut


def test_a_lone_bracket_at_the_very_end_is_released_as_text() -> None:
    assert run(["Items [a", "] and ["])[1] == "Items [a] and ["


def test_something_that_looks_like_a_trailer_but_never_closes_is_released_as_plain_text() -> None:
    streamed, _final, _, dropped = run(["[[action: x\nmore text after a newline"])
    assert "more text after a newline" in streamed
    assert dropped == (Dropped("malformed"),)


def test_a_malformed_action_line_is_dropped_and_not_shown() -> None:
    streamed, final, trailer, dropped = run([f"{ANSWER}\n[[action: not valid at all!]]"])
    assert streamed == final == ANSWER
    assert trailer is None and dropped == (Dropped("malformed"),)


def test_a_very_long_unclosed_trailer_is_not_held_forever() -> None:
    streamed, *_ = run(["Intro. [[action: " + "x" * 400, " and then more"])
    assert "and then more" in streamed


def test_extract_gives_the_same_answer_as_the_stream() -> None:
    text = f"{ANSWER}\n[[action: open_role | ai-product-engineer]]"
    got = extract(text)
    assert got.text == ANSWER
    assert got.trailer == Trailer("open_role", "ai-product-engineer")
    assert extract(ANSWER).trailer is None and extract(ANSWER).text == ANSWER
