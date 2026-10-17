"""The work-sample templates: six families, the canvas's wording, three follow-ups each, checks that never reach the browser,
and the title keywords that pick a family."""

from __future__ import annotations

import re

import pytest

from tayyari.interview.tasks import TEMPLATES, template_for_family, template_for_title

FAMILIES = ["frontend", "data", "marketing", "nursing", "accounts", "teaching"]


def test_six_families_in_the_boards_order_with_the_canvas_tasks() -> None:
    assert [t.family for t in TEMPLATES] == FAMILIES
    assert [t.label for t in TEMPLATES] == [
        "Frontend engineer",
        "Data analyst",
        "Marketing",
        "Nursing",
        "Accounts",
        "Teaching and instructional design",
    ]
    assert [t.task for t in TEMPLATES] == [
        "The cart total flickers when a coupon is applied. Find out why, fix it, say how you'd test it.",
        "Weekly orders by city, 8 rows. Two numbers are wrong. Find them and write one line for your manager.",
        "A launch email for a new mango flavour, to people who bought last summer. "
        "Write the subject line and the first two lines.",
        "A handover note for bed 12 has two problems in it. What do you check first, and who do you tell?",
        "September's bank reconciliation is off by ₹4,500. Find the cause and the entry that fixes it.",
        "A 60-minute lesson on fractions for 40 students has 80 minutes of activities. What do you cut, and why?",
    ]
    assert {t.minutes for t in TEMPLATES} == {10} and all(t.assistant for t in TEMPLATES)


def test_the_frontend_material_is_the_whole_of_cart_summary() -> None:
    frontend = TEMPLATES[0]
    lines = frontend.material.split("\n")
    assert (frontend.material_name, frontend.material_kind, len(lines)) == ("CartSummary.tsx", "code", 12)
    assert lines[:6] == [  # the six lines the Professions board shows
        "export function CartSummary({ items, coupon }: Props) {",
        "  const [total, setTotal] = useState(sum(items));",
        "",
        "  useEffect(() => {",
        "    setTotal(sum(items));",
        "    if (coupon) {",
    ]
    assert lines[6] == "      applyCoupon(coupon).then((off) => setTotal(sum(items) - off));"
    assert lines[-2:] == ['  return <p className="total">{formatINR(total)}</p>;', "}"]


def test_the_checks_are_the_boards_words() -> None:
    assert [t.checks for t in TEMPLATES] == [
        (
            "Saw the total shows twice: full, then discounted",
            "Worked out the total instead of copying it into state",
            "Handled a slow or failed coupon call",
            "Ignored an older coupon answer that arrives last",
        ),
        (
            "Found the repeated Pune row",
            "Found revenue in lakhs among rupees",
            "Wrote a line a manager can act on",
            "Said how to stop it next week",
        ),
        (
            "Said who it's for",
            "One clear offer",
            "A subject under 50 characters",
            "Said how they'd measure it",
        ),
        (
            "Put the low pressure and missing output first",
            "Told the doctor in charge",
            "Said what to write down",
        ),
        ("Found the double entry", "Wrote the correcting entry", "Suggested a check to stop it"),
        ("Kept the learning goal", "Cut with a reason", "Said how they'd check understanding"),
    ]


def test_three_follow_ups_each_the_third_about_the_assistant() -> None:
    first = [t.follow_ups[0].question for t in TEMPLATES]
    assert first == [  # the board's example follow-up for each family
        "Why work out the total during render instead of storing it?",
        "How do you know the Chennai number was in lakhs, and not 7.2 rupees?",
        "Why did you lead with that offer, and not the flavour?",
        "You put one line first. Why that one before the others?",
        "Why a correcting entry, and not deleting the second payment?",
        "Which activity did you cut first, and what would the students miss?",
    ]
    assert (
        TEMPLATES[0].follow_ups[1].question
        == "The coupon call now takes 3 seconds. What does the person see?"
    )
    assert (
        TEMPLATES[0].follow_ups[2].question
        == "The assistant suggested useMemo. Why did you change its version?"
    )
    for template in TEMPLATES:
        assert len(template.follow_ups) == 3
        assert [f.about_assistant for f in template.follow_ups] == [False, False, True]
        assert all(f.question.endswith("?") and len(f.covers) >= 2 and f.topic for f in template.follow_ups)
        assert "assistant" in template.follow_ups[2].question


def test_tables_are_tab_separated_rows_with_the_planted_mistakes() -> None:
    data = TEMPLATES[1]
    rows = [r.split("\t") for r in data.material.split("\n")]
    assert data.material_kind == "table" and rows[0] == ["City", "Orders", "Revenue (₹)"]
    assert len(rows) == 1 + 8 and all(len(r) == 3 for r in rows)
    assert rows.count(["Pune", "610", "9,15,000"]) == 2  # the repeated row
    assert ["Chennai", "480", "7.2"] in rows  # lakhs among rupees
    # every order is worth 1,500, so 7.2 can be checked: 480 x 1,500 = 7,20,000 = 7.2 lakhs
    for _city, orders, revenue in rows[1:]:
        if "," in revenue:
            assert int(revenue.replace(",", "")) == int(orders.replace(",", "")) * 1500

    accounts = TEMPLATES[4]
    ledger = [r.split("\t") for r in accounts.material.split("\n")]
    assert accounts.material_kind == "table" and len(ledger) == 1 + 5
    assert ledger[0] == ["Date", "Entry", "Party", "Amount (₹)"]
    assert ledger.count(["09 Sep", "Payment", "Gupta Packaging", "4,500"]) == 2


def test_text_materials_and_the_lesson_that_adds_up_to_eighty_minutes() -> None:
    assert TEMPLATES[2].material_kind == "text" and TEMPLATES[2].material.split("\n") == [
        "New: Kesar mango flavour, a limited run",
        "Sending to: People who bought our mango pack last summer",
        "Goal: First orders in the first week",
    ]
    assert TEMPLATES[3].material_kind == "text" and TEMPLATES[3].material.split("\n") == [
        "Blood pressure lower than at night",
        "No urine output recorded since 22:00",
        "Pain medicine given at 04:00",
        "Patient asleep",
    ]
    lesson = TEMPLATES[5]
    minutes = [
        int(m.group(1)) for line in lesson.material.split("\n") if (m := re.search(r": (\d+) minutes$", line))
    ]
    assert lesson.material.startswith("Goal: Students can compare two fractions and say which is bigger.")
    assert (len(minutes), sum(minutes)) == (5, 80)


def test_the_browser_gets_the_task_and_never_the_checks() -> None:
    for template in TEMPLATES:
        task = template.to_task()
        dumped = task.model_dump_json()
        assert task.family == template.family and task.familyLabel == template.label
        assert (task.minutes, task.assistant) == (10, True)
        assert task.task in dumped and task.material in task.material
        for check in template.checks:
            assert check not in dumped
        for follow_up in template.follow_ups:
            assert follow_up.question not in dumped
            for cover in follow_up.covers:
                assert cover not in dumped


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Frontend Engineer", "frontend"),
        ("Front-End Developer", "frontend"),
        ("Front end developer", "frontend"),
        ("Senior UI Engineer", "frontend"),
        ("React Native Developer", "frontend"),
        ("Web Developer", "frontend"),
        ("JavaScript Developer", "frontend"),
        ("Data Analyst", "data"),
        ("Senior Business Analyst", "data"),
        ("Data Scientist", "data"),
        ("BI Analyst", "data"),
        ("Product Analytics Lead", "data"),
        ("Growth Marketing Manager", "marketing"),
        ("Content Writer", "marketing"),
        ("Brand Manager", "marketing"),
        ("SEO Specialist", "marketing"),
        ("Social Media Executive", "marketing"),
        ("Staff Nurse", "nursing"),
        ("ICU Nurse", "nursing"),
        ("Clinical Nurse Specialist", "nursing"),
        ("Accountant", "accounts"),
        ("Accounts Trainee", "accounts"),
        ("Bookkeeper", "accounts"),
        ("Audit Associate", "accounts"),
        ("Finance Executive", "accounts"),
        ("Tax Consultant", "accounts"),
        ("CA Articled Assistant", "accounts"),
        ("Maths Teacher", "teaching"),
        ("Instructional Designer", "teaching"),
        ("Corporate Trainer", "teaching"),
        ("Lecturer in Physics", "teaching"),
        # the longer keyword wins; at a tie, the one named first
        ("Marketing Data Analyst", "data"),
        ("Marketing Analytics Manager", "marketing"),
    ],
)
def test_a_title_picks_its_family(title: str, family: str) -> None:
    found = template_for_title(title)
    assert found is not None and found.family == family


@pytest.mark.parametrize(
    "title",
    [
        "Product Manager",
        "Backend Engineer",
        "Sales Executive",
        "Biologist",
        "Cat Handler",
        "Reactor Operator",
        "",
    ],
)
def test_no_family_means_no_template(title: str) -> None:
    assert template_for_title(title) is None


def test_a_family_key_finds_its_template() -> None:
    assert template_for_family("nursing") is TEMPLATES[3] and template_for_family("pilot") is None
