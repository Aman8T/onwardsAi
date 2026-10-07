"""The evidence pack's code stage: normalising, verbatim matches, anchor tokens and the best source window."""

from __future__ import annotations

import pytest

from tayyari.core.limits import JEV_EVIDENCE_WINDOW_CHARS
from tayyari.decisions.packs.evidence import (
    EVIDENCE_PACK,
    SUPPORT_SPEC,
    anchor_found,
    best_window,
    build_corpus,
    build_state,
    code_check,
    extract_anchors,
    fold,
    fragments,
    normalise,
)
from tayyari.decisions.packs.evidence import EvidenceQuestion as Question

CLAIM = "Rebuilt Kitebird checkout in React; owns 42-component Kite UI"
SOURCE = (
    "Frontend Engineer, Kitebird (2022-2024). I rebuilt the checkout with React and TypeScript, "
    "and I own the Kite UI design system: 42 components used by 6 teams."
)
LEFT_QUOTE = chr(0x2018)
RIGHT_QUOTE = chr(0x2019)
LEFT_DQUOTE = chr(0x201C)
RIGHT_DQUOTE = chr(0x201D)
ELLIPSIS = chr(0x2026)


def keys(text: str) -> list[str]:
    return [a.key for a in extract_anchors(text)]


# ───────────────────────── normalising ─────────────────────────


def test_normalise_applies_nfkc_casefold_quotes_ellipses_and_whitespace() -> None:
    assert normalise("  Ｐython\n\n  Ｒeact  ") == "python react"
    assert normalise("STRASSE") == normalise("Stra" + chr(0xDF) + "e")
    assert normalise(f"It{RIGHT_QUOTE}s {LEFT_DQUOTE}fast{RIGHT_DQUOTE}") == 'it\'s "fast"'
    assert normalise(f"{LEFT_QUOTE}x{RIGHT_QUOTE}") == "'x'"
    assert normalise(f"{ELLIPSIS}motion-kit: 212 stars{ELLIPSIS}") == "motion-kit: 212 stars"
    assert normalise("motion-kit: 212 stars... and more") == "motion-kit: 212 stars and more"
    assert normalise("a . . b") == "a . . b"  # only runs of two or more dots are ellipses


def test_fragments_split_a_claim_at_its_ellipses() -> None:
    assert fragments(f"Built the API{ELLIPSIS} cut latency by 40%") == ["built the api", "cut latency by 40%"]
    assert fragments("one piece") == ["one piece"]
    assert fragments(f"{ELLIPSIS}") == []
    assert fragments("a...b....c") == ["a", "b", "c"]


def test_fold_strips_accents_and_case() -> None:
    assert fold("Zürich Café") == "zurich cafe"


# ───────────────────────── anchors ─────────────────────────


def test_anchors_of_a_typical_claim() -> None:
    assert keys(CLAIM) == ["kitebird", "react", "42", "kite", "ui"]
    kinds = [a.kind for a in extract_anchors(CLAIM)]
    assert kinds == ["name", "name", "number", "name", "name"]


def test_identifiers_and_numbers() -> None:
    anchors = extract_anchors("motion-kit: 212 stars, 31 test files")
    assert [(a.kind, a.key) for a in anchors] == [
        ("identifier", "motionkit"),
        ("number", "212"),
        ("number", "31"),
    ]


def test_the_start_of_a_clause_is_not_a_name() -> None:
    assert keys("Led 3 interns at Acme Corp") == ["3", "acme", "corp"]
    assert keys("Shipped the beta. Launched in Berlin") == ["berlin"]
    assert keys("Built it - Wrote the docs") == []


def test_acronyms_mixed_case_and_digit_words_are_names() -> None:
    assert keys("wrote SQL for iOS and TypeScript apps on k8s") == ["sql", "ios", "typescript", "k8s"]


def test_repo_and_package_identifiers() -> None:
    assert keys("maintains user/design-tokens and next.js plugins") == ["userdesigntokens", "nextjs"]
    assert keys("uses v2.1 of the API") == ["v21", "api"]


def test_months_days_and_stop_words_are_not_anchors() -> None:
    assert keys("Since March 2021 and every Monday") == ["2021"]
    assert keys("I think The plan Of attack") == []


def test_numbers_with_separators_and_decimals() -> None:
    assert keys("raised 1,200 users to 2.4 million") == ["1200", "2.4"]
    assert keys("30% faster and 3x cheaper") == ["30", "3"]


def test_digits_inside_an_identifier_are_not_separate_anchors() -> None:
    assert keys("tuned the h264 encoder") == ["h264"]


def test_abbreviations_and_single_letters_are_ignored() -> None:
    assert keys("e.g. a/b tests, i.e. experiments") == []


def test_anchors_are_unique_and_capped() -> None:
    assert keys("React and React and react") == ["react"]
    many = " ".join(f"Name{n:02d}x" for n in range(30))
    assert len(extract_anchors(many)) == 12
    assert len(extract_anchors(many, limit=3)) == 3


def test_a_claim_with_nothing_specific_has_no_anchors() -> None:
    assert extract_anchors("worked on a variety of interesting problems") == []


# ───────────────────────── the code check ─────────────────────────


def check(claim: str, source: str, reader: dict | None = None, facts: dict | None = None):  # type: ignore[no-untyped-def,type-arg]
    return code_check(claim, build_corpus(source, reader, facts))


def test_a_verbatim_claim_is_settled_at_once() -> None:
    result = check(
        "motion-kit: 212 stars, 31 test files", "Repos: motion-kit: 212 stars, 31 test files and more"
    )
    assert result.verdict == "verbatim" and result.keep


def test_verbatim_ignores_case_whitespace_and_curly_quotes() -> None:
    source = f"She said {LEFT_DQUOTE}it{RIGHT_QUOTE}s   fast{RIGHT_DQUOTE} about the build."
    assert check('SHE SAID "it\'s fast" about the build', source).verdict == "verbatim"


def test_every_fragment_around_an_ellipsis_must_be_in_the_source() -> None:
    source = "We cut latency for the search API in March and also moved storage to Postgres."
    assert (
        check(f"cut latency for the search API{ELLIPSIS} moved storage to Postgres", source).verdict
        == "verbatim"
    )
    other = check(f"cut latency for the search API{ELLIPSIS} rewrote the Rust parser", source)
    assert other.verdict == "anchors_missing"
    assert [a.key for a in other.missing] == ["rust"]


def test_a_very_short_verbatim_hit_does_not_count() -> None:
    result = check("Go", "I like Go and Rust.")
    assert result.verdict != "verbatim"


def test_all_anchors_found_keeps_the_claim() -> None:
    result = check(CLAIM, SOURCE)
    assert result.verdict == "anchors_ok" and result.keep
    assert result.missing == () and len(result.anchors) == 5


def test_a_missing_name_or_number_asks_jev() -> None:
    stripe = check("Led the Stripe payments migration", SOURCE)
    assert stripe.verdict == "anchors_missing" and not stripe.keep
    assert [a.key for a in stripe.missing] == ["stripe"]

    number = check("Kitebird checkout rebuilt for 9000 stores", SOURCE)
    assert number.verdict == "anchors_missing"
    assert [a.key for a in number.missing] == ["9000"]


def test_the_reader_output_and_the_facts_count_as_the_source() -> None:
    claim = "motion-kit: 212 stars"  # not in the raw text of this source
    source = "A long README about animation springs and easing curves. " * 3
    assert check(claim, source).verdict == "anchors_missing"
    reader = {"sub": "14 repos", "skills": [{"name": "React", "evidence": "motion-kit with 212 stars"}]}
    assert check(claim, source, reader=reader).verdict == "anchors_ok"
    assert (
        check(claim, source, facts={"top_repo": {"name": "motion-kit", "stars": 212}}).verdict == "anchors_ok"
    )


def test_a_count_the_reader_computed_is_not_sent_to_jev() -> None:
    reader = {"sub": "14 repos · 9 languages"}
    assert (
        check("Maintains 14 repos in 9 languages", "README text only. " * 5, reader=reader).verdict
        == "anchors_ok"
    )


def test_a_source_with_no_text_keeps_every_claim() -> None:
    """A scanned resume has no raw text, so there is nothing to check a claim against."""
    result = check("Led the Stripe payments migration for 9000 stores", "", reader={"sub": "x"})
    assert result.verdict == "no_source" and result.keep
    assert check("anything at all", "   \n ").verdict == "no_source"


def test_a_claim_with_no_anchors_is_kept_without_asking() -> None:
    result = check("worked on a variety of interesting problems", SOURCE)
    assert result.verdict == "no_anchors" and result.keep


@pytest.mark.parametrize(
    ("claim", "source", "found"),
    [
        ("Managed interns at Acme", "I managed one intern at Acme.", True),  # plural against singular
        ("Studied in Zürich", "Studied in Zurich last year.", True),  # accents
        ("Uses Motion Kit", "the motion-kit library", True),  # space against hyphen
        ("Uses motion-kit", "the motion kit library", True),  # hyphen against space
        ("Wrote SQL", "I wrote SQL daily.", True),  # short acronym as a whole word
        ("Wrote SQL", "I wrote MySQL daily.", False),  # but not inside another word
        ("Cut costs by 14", "Cut costs by 114 percent", False),  # a number is a whole token
        ("Raised 1,200 users", "grew to 1200 users", True),  # separators in numbers
        ("Reached 2.4", "reached 2 million", False),
        ("Knows Reactive", "Knows React", False),  # the anchor is longer than what the source has
        ("Knows React", "Knows Reactive programming", True),  # four or more characters match as a substring
    ],
)
def test_anchor_matching_is_lenient_where_it_should_be(claim: str, source: str, found: bool) -> None:
    corpus = build_corpus(source)
    anchors = [a for a in extract_anchors(claim)]
    assert anchors, claim
    assert all(anchor_found(a, corpus) for a in anchors) is found


# ───────────────────────── the best window ─────────────────────────


def filler(n: int) -> str:
    return ("The quarterly planning meeting covered budgets and hiring. " * (n // 55 + 1))[:n]


def test_a_short_source_is_returned_whole() -> None:
    assert best_window("  a short source  ", "anything") == "a short source"


def test_the_window_is_centred_on_the_best_match() -> None:
    target = "Kitebird checkout was rebuilt with React, and the Kite design system has 42 components."
    source = filler(20_000) + target + filler(20_000)
    window = best_window(source, CLAIM)

    assert len(window) <= JEV_EVIDENCE_WINDOW_CHARS
    assert target in window
    assert (
        window.index(target) <= JEV_EVIDENCE_WINDOW_CHARS // 5
    )  # a little context before the match, not the middle


def test_a_cluster_of_anchor_words_beats_one_stray_word() -> None:
    far = filler(10_000) + "React is mentioned once here. " + filler(10_000)
    cluster = "Kitebird checkout rebuilt: 42 components in the Kite UI. React was the base."
    window = best_window(far + cluster + filler(5_000), CLAIM)
    assert "Kitebird" in window and "42 components" in window


def test_a_claim_that_shares_no_word_gets_the_opening_of_the_source() -> None:
    source = "OPENING " + filler(10_000)
    window = best_window(source, "zzzz qqqq xxxx")
    assert window.startswith("OPENING") and len(window) <= JEV_EVIDENCE_WINDOW_CHARS


def test_ties_go_to_the_earliest_window() -> None:
    source = (
        "First: Kitebird shipped checkout. "
        + filler(6_000)
        + "Second: Kitebird shipped checkout. "
        + filler(3_000)
    )
    window = best_window(source, "Kitebird shipped checkout")
    assert "First: Kitebird" in window and "Second:" not in window


def test_the_window_size_can_be_chosen() -> None:
    assert len(best_window(filler(5_000), "planning meeting budgets", size=300)) <= 300


def test_a_window_of_a_large_source_is_fast_enough() -> None:
    import time

    source = filler(250_000) + "Kitebird React 42" + filler(10_000)
    started = time.perf_counter()
    for _ in range(5):
        best_window(source, CLAIM)
    assert time.perf_counter() - started < 5.0


# ───────────────────────── the spec ─────────────────────────


def test_the_support_question_is_a_three_way_choice() -> None:
    question = SUPPORT_SPEC.question
    assert question.kind == "choice"
    assert question.option_labels == ("supports", "contradicts", "says_nothing")
    assert all(len(str(d)) <= 255 for d in (question.criteria or {}).values())  # type: ignore[union-attr]
    assert SUPPORT_SPEC.id == "evidence.support" and SUPPORT_SPEC.pack == "evidence"
    assert EVIDENCE_PACK.spec("evidence.support") is SUPPORT_SPEC
    assert SUPPORT_SPEC.default.threshold == 0.8 and SUPPORT_SPEC.default.audit_rate == 0.05


def test_the_state_is_the_claim_and_the_section() -> None:
    assert build_state(Question("a claim", "a section")) == {"claim": "a claim", "section": "a section"}


# ───────────────────────── corners ─────────────────────────


def test_digits_after_a_separator_inside_an_identifier_are_not_separate_anchors() -> None:
    assert keys("runs on node-18 daily") == ["node18"]


def test_a_repeated_name_is_one_anchor() -> None:
    assert keys("Led React work and React training") == ["react"]


def test_a_corpus_survives_reader_output_that_json_cannot_dump() -> None:
    loop: dict = {}  # type: ignore[type-arg]
    loop["me"] = loop
    corpus = build_corpus("some source text about Kitebird", reader_output=loop)
    assert anchor_found(extract_anchors("Works at Kitebird")[0], corpus)


def test_a_claim_of_only_short_or_common_words_gets_the_opening_of_the_source() -> None:
    source = "OPENING " + filler(6_000)
    assert best_window(source, "to be or not to be").startswith("OPENING")


def test_a_prebuilt_word_index_gives_the_same_window() -> None:
    from tayyari.decisions.packs.evidence import word_positions

    source = filler(8_000) + "Kitebird checkout in React, Kite UI with 42 components." + filler(8_000)
    assert best_window(source, CLAIM) == best_window(source, CLAIM, positions=word_positions(source.strip()))
