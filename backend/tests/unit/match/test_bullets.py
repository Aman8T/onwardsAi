"""The sentence verifier and the settling of one bullet: numbers, scope verbs, posting terms, placeholders."""

from __future__ import annotations

import pytest

from tayyari.contract.match import Fact, FactRef
from tayyari.match.bullets import DraftContext, assess_draft, export_blockers, fallback_bullet, is_figure
from tayyari.match.verify import check_text, escalations, placeholders_in, scope_verbs, unsupported_numbers
from tayyari.portfolio.ground import Corpus


def fact(fid: str, text: str, kind: str = "accomplishment") -> Fact:
    return Fact(
        id=fid,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        refs=[FactRef(source="resume", docId="resume", lines=[1, 1], quote=text)],
        evidenceTier="used",
    )


CHECKOUT = fact(
    "exp-1.1",
    "Rebuilt the three-step checkout as a single-page flow in React and TypeScript; "
    "checkout conversion rose 9.4% in the A/B test and LCP dropped from 4.1s to 1.9s.",
)
LIBRARY = fact(
    "exp-1.2", "Own Kite UI, the shared React component library (42 components) used by 3 product squads."
)
SUPPORT = fact("exp-2.1", "Contributed to the checkout rebuild and supported the release.")
PLAIN = fact("exp-4.2", "Responsive landing pages for 3 early-stage clients.")


def assess(text: str, *facts: Fact, ctx: DraftContext | None = None, verdict: str = "exact"):  # type: ignore[no-untyped-def]
    return assess_draft(
        "b1", text, list(facts), ctx or DraftContext(), primary=facts[0], model_verdict=verdict
    )


# ───────────────────────── numbers ─────────────────────────


def test_a_number_the_facts_have_is_kept_exactly() -> None:
    got = assess(
        "Rebuilt the checkout in React; conversion rose 9.4% and LCP fell from 4.1s to 1.9s.", CHECKOUT
    )
    assert got.bullet is not None
    assert got.bullet.status == "verified"
    assert got.bullet.placeholders == []
    assert got.decision is None


def test_a_number_no_cited_fact_has_becomes_a_placeholder_that_blocks_export() -> None:
    got = assess("Rebuilt the checkout in React and cut load time by 40%.", CHECKOUT)
    assert got.bullet is not None
    assert got.bullet.text == "Rebuilt the checkout in React and cut load time by [x]%."
    assert got.bullet.placeholders == ["[x]%"]
    assert got.bullet.status == "needs_user"
    assert got.decision is not None
    assert got.decision.kind == "number"
    assert got.decision.options == ["fill", "soften", "drop"]
    assert "[x]%" in got.decision.prompt
    assert "40" not in got.bullet.text


@pytest.mark.parametrize(
    ("draft", "slot"),
    [
        (
            "Rebuilt the checkout for 42M shoppers.",
            "[x]M",
        ),  # 9.4 / 4.1 are there, 42 is not (and 42M is not 42)
        ("Rebuilt the checkout, a 9.4x improvement.", "[x]x"),  # 9.4% is not 9.4x
        ("Rebuilt the checkout and grew revenue by $2M.", "$[x]M"),
        ("Rebuilt the checkout for ten teams.", "[x]"),  # a spelled-out number is a number
    ],
)
def test_numbers_are_judged_with_their_units_and_spelled_out_numbers_count(draft: str, slot: str) -> None:
    got = assess(draft, CHECKOUT)
    assert got.bullet is not None
    assert got.bullet.status == "needs_user"
    assert slot in got.bullet.text
    assert got.bullet.placeholders and all(p.startswith("[x]") for p in got.bullet.placeholders)


def test_a_spelled_out_number_is_supported_by_its_digits() -> None:
    got = assess("Own the shared component library used by three product squads.", LIBRARY)
    assert got.bullet is not None
    assert got.bullet.status == "verified"


def test_a_number_from_another_fact_does_not_leak_in() -> None:
    got = assess("Own Kite UI, the component library, and cut load time from 4.1s to 1.9s.", LIBRARY)
    assert got.bullet is not None
    assert (
        got.bullet.status == "needs_user"
    )  # 4.1s and 1.9s belong to the checkout fact, which this bullet does not cite
    assert got.bullet.placeholders == ["[x]s"] or got.bullet.placeholders  # the figures are taken out


def test_a_model_written_placeholder_is_a_question_not_a_claim() -> None:
    got = assess("Own the shared React component library, used by [x] squads.", LIBRARY)
    assert got.bullet is not None
    assert got.bullet.status == "needs_user"
    assert got.bullet.placeholders == ["[x]"]
    assert got.decision is not None and got.decision.kind == "number"


def test_many_invented_numbers_are_not_repaired_the_model_wording_is_thrown_away() -> None:
    got = assess(
        "Rebuilt the checkout, lifting revenue 31%, signups 12%, retention 8% and NPS by 14 points.", CHECKOUT
    )
    assert got.bullet is None
    assert got.rejected is not None


def test_unsupported_numbers_report_their_position() -> None:
    corpus = Corpus.of("Raised repeat purchase from 22% to 31%.")
    slots = unsupported_numbers("Raised it from 22% to 35%, then 3x.", corpus)
    assert [s.text for s in slots] == ["35%", "3x"]
    assert placeholders_in("by [x]% and [x]") == ["[x]%", "[x]"]


# ───────────────────────── scope verbs ─────────────────────────


@pytest.mark.parametrize(
    ("draft", "facts", "escalated"),
    [
        ("Led the checkout rebuild.", [SUPPORT], ["led"]),  # contributed -> led
        ("Built the checkout rebuild.", [SUPPORT], ["built"]),  # supported -> built
        ("Owned the checkout rebuild.", [LIBRARY], []),  # Own -> Owned: the same verb
        ("Managed the component library.", [LIBRARY], ["managed"]),  # own -> managed: a different claim
        ("Built responsive landing pages.", [PLAIN], []),  # a fact with no verb is read as work that was done
        ("Led responsive landing pages.", [PLAIN], ["led"]),
        ("Supported the checkout rebuild.", [SUPPORT], []),
        ("Contributed to the checkout rebuild.", [CHECKOUT], []),  # weaker than the facts: always fine
    ],
)
def test_scope_verbs_may_not_outrank_the_facts(draft: str, facts: list[Fact], escalated: list[str]) -> None:
    raw = " ".join(f.text for f in facts)
    assert escalations(draft, raw) == escalated


def test_an_escalated_scope_marks_the_bullet_stretch_and_asks() -> None:
    got = assess("Led the checkout rebuild.", SUPPORT)
    assert got.bullet is not None
    assert got.bullet.verdict == "stretch"
    assert got.bullet.status == "needs_user"
    assert got.decision is not None
    assert got.decision.kind == "stretch"
    assert got.decision.options == ["keep", "soften", "drop"]
    assert "led" in got.decision.prompt


def test_the_models_own_stretch_verdict_is_respected_too() -> None:
    got = assess("Contributed to the checkout rebuild.", SUPPORT, verdict="stretch")
    assert got.bullet is not None
    assert got.bullet.status == "needs_user"


def test_possessive_own_and_noun_uses_are_not_scope_claims() -> None:
    assert scope_verbs("Worked on their own roadmap with the team lead.") == {}
    assert scope_verbs("Led the team.") == {"led": 3}


# ───────────────────────── posting terms ─────────────────────────

CLEVERTAP = fact("exp-1.1", "Led the lifecycle email programme for 400,000 subscribers on CleverTap.")


def test_a_posting_term_is_used_only_when_a_cited_fact_supports_it() -> None:
    ctx = DraftContext(posting_terms=("Klaviyo", "CleverTap", "Segment"))
    ok = assess("Led the lifecycle email programme on CleverTap for 400,000 subscribers.", CLEVERTAP, ctx=ctx)
    assert ok.bullet is not None and ok.bullet.status == "verified"

    stuffed = assess(
        "Led the lifecycle email programme on Klaviyo for 400,000 subscribers.", CLEVERTAP, ctx=ctx
    )
    assert (
        stuffed.bullet is None
    )  # Klaviyo is the posting's word; no fact supports it, nothing ties it to this fact
    assert stuffed.rejected is not None and "Klaviyo" in stuffed.rejected

    segment = assess("Led the email programme and a Segment integration.", CLEVERTAP, ctx=ctx)
    assert segment.bullet is None


def test_a_term_a_coverage_row_ties_to_the_fact_is_a_question_for_the_person() -> None:
    ctx = DraftContext(
        posting_terms=("Klaviyo",), term_support={"Klaviyo": frozenset({"exp-1.1"})}
    )  # coverage said CleverTap is a synonym of the ESP the posting names
    got = assess("Led the lifecycle email programme on Klaviyo for 400,000 subscribers.", CLEVERTAP, ctx=ctx)
    assert got.bullet is not None
    assert got.bullet.verdict == "reframed"
    assert got.bullet.status == "needs_user"
    assert got.decision is not None
    assert got.decision.kind == "confirm_term"
    assert got.decision.options == ["use_posting_term", "keep_my_wording", "drop"]
    assert "Klaviyo" in got.decision.prompt


def test_aliases_of_a_supported_term_count_as_supported() -> None:
    ctx = DraftContext(posting_terms=("JS",))
    js = fact("e1", "Built the storefront in JavaScript.")
    got = assess("Built the storefront in JS.", js, ctx=ctx)
    assert got.bullet is not None
    assert got.bullet.status == "verified"


# ───────────────────────── names, puffery, voice ─────────────────────────


def test_an_employer_a_tool_or_puffery_the_facts_do_not_have_rejects_the_draft() -> None:
    assert assess("Rebuilt the checkout at Globex in React.", CHECKOUT).rejected
    assert assess("Rebuilt the checkout with Kubernetes and React.", CHECKOUT).rejected
    assert assess("Rebuilt the checkout as a passionate, results-driven engineer.", CHECKOUT).rejected
    assert assess("I rebuilt the checkout in React.", CHECKOUT).rejected  # first person on a resume
    assert assess("", CHECKOUT).rejected
    assert assess("Rebuilt " + "the checkout " * 30, CHECKOUT).rejected  # far too long for one line


def test_exact_and_reframed_are_told_apart_by_code() -> None:
    same = assess(CHECKOUT.text, CHECKOUT)
    assert same.bullet is not None and same.bullet.verdict == "exact"
    reworded = assess("Rebuilt the checkout in React and TypeScript, lifting conversion 9.4%.", CHECKOUT)
    assert reworded.bullet is not None and reworded.bullet.verdict == "reframed"


def test_check_text_needs_a_cited_fact_unless_the_sentence_claims_nothing() -> None:
    assert not check_text("Rebuilt the checkout.", []).ok
    assert check_text(
        "I have not yet worked with Segment.", [], extra="Segment", first_person_ok=True, allow_no_facts=True
    ).ok


def test_the_fallback_is_the_facts_own_wording_and_always_passes() -> None:
    bullet = fallback_bullet("b1", CHECKOUT)
    assert bullet.text == CHECKOUT.text
    assert bullet.factIds == ["exp-1.1"]
    assert (bullet.verdict, bullet.status) == ("exact", "verified")
    assert check_text(bullet.text, [CHECKOUT], max_words=60).ok


def test_export_blockers_list_open_bullets_only() -> None:
    from tayyari.contract.match import Bullet, ExperienceEntry, ResumeHeader, ResumeVariant

    def b(bid: str, status: str, text: str = "Did a thing.", placeholders: list[str] | None = None) -> Bullet:
        return Bullet(
            id=bid, text=text, factIds=["f"], verdict="exact", placeholders=placeholders or [], status=status
        )  # type: ignore[arg-type]

    variant = ResumeVariant(
        header=ResumeHeader(name="A B", contact=[]),
        summary=b("summary", "verified"),
        skills=[],
        experience=[
            ExperienceEntry(
                entryFactId="e",
                title="t",
                bullets=[
                    b("ok", "verified"),
                    b("open", "needs_user"),
                    b("gone", "dropped", "x [x]"),
                    b("slot", "verified", "by [x]%", ["[x]%"]),
                ],
            )
        ],
        projects=[],
        education=[],
        decisions=[],
        changes=[],
    )
    assert export_blockers(variant) == ["open: waiting for your answer", "slot: still has a [x] to fill"]


@pytest.mark.parametrize(
    "value",
    ["35%", "9.4%", "15 %", "3x", "10K", "$2M", "40", "1,20,000", "₹12,00,000", "₹12 lakh", "2 lakhs", "12lakh",
     "Rs. 8 crores", "Rs 8 crore", "rs.8crores", "INR 2.5 cr", "₹ 1.2 Cr", "12 LPA", "₹8 lakh p.a.", "50+", "1,000+"],
)  # fmt: skip
def test_a_figure_is_one_number_with_its_unit_in_any_indian_or_western_spelling(value: str) -> None:
    assert is_figure(value), value


@pytest.mark.parametrize(
    "value",
    ["", "   ", "a lot", "about 30", "30 or 40", "thirty", "35% of sales", "x", "lakh", "Rs.", "₹",
     "12 lakh and 3 crore", "1 2", "9" * 41],
)  # fmt: skip
def test_anything_more_or_less_than_one_figure_is_refused(value: str) -> None:
    assert not is_figure(value), value
