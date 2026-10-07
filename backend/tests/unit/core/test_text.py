"""core.text against the legacy functions it replaces, on a table of inputs: behaviour must be identical.

The parity tests need `tayyari.legacy` (the deployed app) and `tayyari.ingest`; they skip themselves once
the legacy package is deleted, and the semantic tests keep guarding the behaviour.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from tayyari.core import text

legacy_pipeline = pytest.importorskip("tayyari.legacy.pipeline")
legacy_prompts = pytest.importorskip("tayyari.legacy.prompts")
legacy_classify = pytest.importorskip("tayyari.ingest.classify")
legacy_github = pytest.importorskip("tayyari.ingest.github")
legacy_web = pytest.importorskip("tayyari.ingest.web")

LONG_WORDS = (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau " * 3
)

UI_STRINGS: list[Any] = [
    None,
    "",
    "   ",
    "short",
    "  padded   with   gaps  ",
    "line one\nline two\r\n\tline three",
    "Frontend Engineering and Design Systems",
    "Reading README · motion-kit: spring-physics animation library for React, 212 stars",
    "word " * 40,
    LONG_WORDS,
    "x" * 200,
    "supercalifragilisticexpialidocious" * 3,
    "ends with separators, ; : · - ",
    "a, b; c: d · e - f, g; h: i · j - k, l; m: n · o - p",
    "Ünïcödé text with “curly quotes” and an emoji \U0001f642 inside a rather long sentence",
    "no-spaces-but-hyphens-and-dots.many.of.them-here-and-here-and-more",
    "tab\tseparated\tvalues\tthat\tgo\ton\tand\ton",
    12345,
    3.5,
    ["a", "b"],
    {"k": "v"},
    True,
]
LENGTHS = [0, 1, 2, 5, 10, 16, 24, 32, 40, 48, 64, 90, 120, 180, 1000]


def case_id(value: Any) -> str:
    return repr(value)[:30]


# ----------------------------------------------------------------------------- whitespace


@pytest.mark.parametrize("value", UI_STRINGS, ids=case_id)
def test_tidy_matches_legacy(value: Any) -> None:
    assert text.tidy(value) == legacy_pipeline.tidy(value)


@pytest.mark.parametrize("value", [v for v in UI_STRINGS if isinstance(v, str)], ids=case_id)
def test_squash_and_one_line_match_legacy(value: str) -> None:
    assert text.squash(value) == legacy_web._squash(value)
    assert text.one_line(value) == legacy_github._one_line(value)
    assert text.squash(value) == text.one_line(value) == text.tidy(value)


def test_whitespace_semantics() -> None:
    assert text.squash("  a \n\t b\u00a0c  ") == "a b c"
    assert text.one_line("first\nsecond\r\nthird") == "first second third"
    assert text.tidy(None) == ""
    assert text.tidy(7) == ""


# ----------------------------------------------------------------------------- clipping


@pytest.mark.parametrize("max_len", LENGTHS)
@pytest.mark.parametrize("value", UI_STRINGS, ids=case_id)
def test_clip_words_matches_legacy(value: Any, max_len: int) -> None:
    assert text.clip_words(value, max_len) == legacy_pipeline.clip_words(value, max_len)


@pytest.mark.parametrize("max_len", LENGTHS)
@pytest.mark.parametrize("value", UI_STRINGS, ids=case_id)
def test_short_label_matches_legacy(value: Any, max_len: int) -> None:
    assert text.short_label(value, max_len) == legacy_pipeline.short_label(value, max_len)


PROMPT_TEXTS = [
    "",
    "short",
    "x" * 100,
    "abc def ghi " * 40,
    "line\n" * 100,
    " " * 60 + "tail",
    "trailing spaces   " * 10,
    "é" * 70,
]


@pytest.mark.parametrize("max_len", [0, 1, 10, 39, 40, 41, 59, 60, 100, 479, 480, 481, 5000])
@pytest.mark.parametrize("value", PROMPT_TEXTS, ids=case_id)
def test_clip_matches_legacy(value: str, max_len: int) -> None:
    assert text.clip(value, max_len) == legacy_prompts.clip(value, max_len)


def test_clip_semantics() -> None:
    assert text.clip("short", 100) == "short"
    cut = text.clip("word " * 100, 100)
    assert cut.endswith("\n…[trimmed]")
    assert len(cut) <= 100
    assert text.clip("x" * 100, 10) == "x" * 10  # under 40 characters of room: no marker
    assert text.clip("abc", -5) == ""


def test_clip_words_semantics() -> None:
    assert text.clip_words("one two three four", 10) == "one two…"
    assert text.clip_words("short", 10) == "short"
    assert text.clip_words("x" * 50, 10) == "x" * 9 + "…"
    assert text.clip_words(None, 10) == ""
    assert len(text.clip_words("word " * 100, 48)) <= 48


def test_short_label_semantics() -> None:
    assert text.short_label("Frontend Engineering", 16) == "Frontend"
    assert text.short_label("AI", 16) == "AI"
    assert text.short_label("Supercalifragilistic", 8) == "Superca…"


# ----------------------------------------------------------------------------- plural

PLURALS: list[tuple[int, str, str | None]] = [
    (0, "repo", None),
    (1, "repo", None),
    (2, "repo", None),
    (1, "case study", "case studies"),
    (5, "case study", "case studies"),
    (1, "language", None),
    (11, "skill", None),
    (-1, "x", None),
]


@pytest.mark.parametrize(("n", "word", "plural_word"), PLURALS)
def test_plural_matches_classify(n: int, word: str, plural_word: str | None) -> None:
    assert text.plural(n, word, plural_word) == legacy_classify.plural(n, word, plural_word)


@pytest.mark.parametrize(("n", "word", "plural_word"), [p for p in PLURALS if p[2] is None])
def test_plural_matches_pipeline(n: int, word: str, plural_word: str | None) -> None:
    assert text.plural(n, word) == legacy_pipeline.plural(n, word)


def test_plural_semantics() -> None:
    assert text.plural(1, "repo") == "1 repo"
    assert text.plural(2, "repo") == "2 repos"
    assert text.plural(0, "repo") == "0 repos"
    assert text.plural(2, "case study", "case studies") == "2 case studies"


# ----------------------------------------------------------------------------- keys and ids

KEY_INPUTS: list[Any] = [
    None,
    "",
    "REST & GraphQL",
    "rest-graphql",
    "C++",
    "C#",
    "c sharp",
    "Node.js",
    "node js",
    " Vue  3 ",
    "Ünïcode Straße",
    "AI/ML Ops",
    "日本語",
    "!!!",
    "a_b-c d",
    42,
    ["x"],
    "TypeScript",
    "typescript ",
]


@pytest.mark.parametrize("value", KEY_INPUTS, ids=case_id)
def test_loose_key_matches_legacy(value: Any) -> None:
    assert text.loose_key(value) == legacy_pipeline.loose_key(value)


def test_loose_key_semantics() -> None:
    assert text.loose_key("REST & GraphQL") == text.loose_key("rest-graphql") == "restgraphql"
    assert text.loose_key("C++") == "c++"
    assert text.loose_key("C#") == "c#"
    assert text.loose_key(None) == ""


SLUG_INPUTS: list[str] = [i for i in KEY_INPUTS if isinstance(i, str)] + [
    "Frontend Engineering",
    "C++ & Rust",
    "Résumé Parsing",
    "naïve Bayes",
    "  ",
    "---",
    "x" * 100,
    "a-" * 30,
    "Foo -- Bar",
    "front end / back end",
    "Café Ünïcode",
    "x" * 39 + "-yyyy",
    "Senior Software Engineer (Platform & Infrastructure) at Large Company",
    "ﬁne ligature ĳ",
]


@pytest.mark.parametrize("value", SLUG_INPUTS, ids=case_id)
def test_slug_matches_legacy(value: str) -> None:
    assert text.slug(value) == legacy_pipeline.slug(value)


def test_slug_semantics() -> None:
    assert text.slug("C++ & Rust") == "cpp-and-rust"
    assert text.slug("C#") == "csharp"
    assert text.slug("Résumé Parsing") == "resume-parsing"
    assert text.slug("日本語") == "item"
    assert text.slug("") == "item"
    assert len(text.slug("x" * 100)) == 40
    assert not text.slug("x" * 39 + "-yyyy").endswith("-")


# ----------------------------------------------------------------------------- js_round

ROUND_INPUTS: list[float] = [
    0,
    0.4,
    0.5,
    0.6,
    1.5,
    2.5,
    3.5,
    -0.5,
    -1.5,
    -2.5,
    -0.4,
    99.5,
    100.49999,
    1e15 + 0.5,
    2.675,
    7,
    -7,
]


@pytest.mark.parametrize("value", ROUND_INPUTS)
def test_js_round_matches_legacy(value: float) -> None:
    assert text.js_round(value) == legacy_pipeline._js_round(value) == legacy_github._js_round(value)


def test_js_round_is_math_round_not_bankers_rounding() -> None:
    assert text.js_round(0.5) == 1
    assert text.js_round(2.5) == 3  # round(2.5) == 2 in Python
    assert text.js_round(-2.5) == -2  # towards +infinity
    assert isinstance(text.js_round(1.2), int)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_js_round_rejects_non_finite_like_the_originals(bad: float) -> None:
    with pytest.raises((ValueError, OverflowError)):
        text.js_round(bad)
    with pytest.raises((ValueError, OverflowError)):
        legacy_pipeline._js_round(bad)
