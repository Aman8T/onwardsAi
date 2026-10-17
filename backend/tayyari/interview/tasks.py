"""The work-sample templates: one small real task for each of six families of work, written before anyone practises.

Code picks the template from the family of the person's role (`template_for_title`); a model writes none of it. The checks
("what a full answer covers") and the follow-up notes stay on the server until the debrief; `WorkTemplate.to_task()` is
what the browser gets. The wording is the `Professions` board of the I1 canvas, word for word, with three follow-ups for
each (the third is about the assistant and is skipped when the person never used it).

Where the canvas drew only part of the material (the data analyst's table shows 5 of 8 rows) the rest is filled in here so
the task is whole; the figures stay consistent (every order is worth ₹1,500, so a revenue given in lakhs is checkable).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from tayyari.contract.interview import MaterialKind, WorkTask

TEMPLATE_MINUTES: Final = 10
"""A guide, not a limit."""

_CART_SUMMARY: Final = """export function CartSummary({ items, coupon }: Props) {
  const [total, setTotal] = useState(sum(items));

  useEffect(() => {
    setTotal(sum(items));
    if (coupon) {
      applyCoupon(coupon).then((off) => setTotal(sum(items) - off));
    }
  }, [items, coupon]);

  return <p className="total">{formatINR(total)}</p>;
}"""

_ORDERS: Final = "\n".join(
    "\t".join(row)
    for row in (
        ("City", "Orders", "Revenue (₹)"),
        ("Bengaluru", "1,240", "18,60,000"),
        ("Pune", "610", "9,15,000"),
        ("Pune", "610", "9,15,000"),
        ("Chennai", "480", "7.2"),
        ("Hyderabad", "530", "7,95,000"),
        ("Mumbai", "920", "13,80,000"),
        ("Delhi", "700", "10,50,000"),
        ("Kolkata", "350", "5,25,000"),
    )
)

_MANGO_BRIEF: Final = "\n".join(
    (
        "New: Kesar mango flavour, a limited run",
        "Sending to: People who bought our mango pack last summer",
        "Goal: First orders in the first week",
    )
)

_HANDOVER: Final = "\n".join(
    (
        "Blood pressure lower than at night",
        "No urine output recorded since 22:00",
        "Pain medicine given at 04:00",
        "Patient asleep",
    )
)

_CASHBOOK: Final = "\n".join(
    "\t".join(row)
    for row in (
        ("Date", "Entry", "Party", "Amount (₹)"),
        ("02 Sep", "Receipt", "Sharma Traders", "18,000"),
        ("09 Sep", "Payment", "Gupta Packaging", "4,500"),
        ("09 Sep", "Payment", "Gupta Packaging", "4,500"),
        ("16 Sep", "Receipt", "Rao and Sons", "12,000"),
        ("28 Sep", "Payment", "Salaries", "22,000"),
    )
)

_LESSON: Final = "\n".join(
    (
        "Goal: Students can compare two fractions and say which is bigger.",
        "Warm-up quiz: 10 minutes",
        "Teacher explains halves, quarters and thirds: 15 minutes",
        "Pair worksheet: 20 minutes",
        "Paper-strip fraction wall: 20 minutes",
        "Class game: 15 minutes",
    )
)


@dataclass(frozen=True, slots=True)
class FollowUp:
    """One question about the person's work. ``covers`` are the judge's notes (hidden); ``about_assistant`` marks the
    question that is skipped when the assistant was not used."""

    topic: str
    question: str
    covers: tuple[str, ...]
    about_assistant: bool = False


@dataclass(frozen=True, slots=True)
class WorkTemplate:
    family: str
    label: str
    keywords: tuple[str, ...]
    task: str
    material_name: str
    material: str
    material_kind: MaterialKind
    checks: tuple[str, ...]
    follow_ups: tuple[FollowUp, ...]
    minutes: int = TEMPLATE_MINUTES
    assistant: bool = True

    def to_task(self) -> WorkTask:
        """What the browser sees: the task and its material. Never the checks or the notes."""
        return WorkTask(
            family=self.family,
            familyLabel=self.label,
            task=self.task,
            materialName=self.material_name,
            material=self.material,
            materialKind=self.material_kind,
            minutes=self.minutes,
            assistant=self.assistant,
        )


TEMPLATES: Final[tuple[WorkTemplate, ...]] = (
    WorkTemplate(
        family="frontend",
        label="Frontend engineer",
        keywords=(
            "frontend",
            "front-end",
            "front end",
            "ui engineer",
            "ui developer",
            "react",
            "web developer",
            "javascript",
        ),
        task="The cart total flickers when a coupon is applied. Find out why, fix it, say how you'd test it.",
        material_name="CartSummary.tsx",
        material=_CART_SUMMARY,
        material_kind="code",
        checks=(
            "Saw the total shows twice: full, then discounted",
            "Worked out the total instead of copying it into state",
            "Handled a slow or failed coupon call",
            "Ignored an older coupon answer that arrives last",
        ),
        follow_ups=(
            FollowUp(
                "Deriving the total",
                "Why work out the total during render instead of storing it?",
                (
                    "Says the total can be worked out from the items and the discount",
                    "Names the extra render or the two values that can disagree",
                ),
            ),
            FollowUp(
                "A slow coupon call",
                "The coupon call now takes 3 seconds. What does the person see?",
                (
                    "Says what shows while the call is pending",
                    "Says what happens when the call fails or an older answer arrives last",
                ),
            ),
            FollowUp(
                "Using the assistant",
                "The assistant suggested useMemo. Why did you change its version?",
                (
                    "Says what was asked of the assistant and what came back",
                    "Gives a reason for each change to its version",
                ),
                about_assistant=True,
            ),
        ),
    ),
    WorkTemplate(
        family="data",
        label="Data analyst",
        keywords=("data analyst", "analytics", "business analyst", "bi", "data scientist"),
        task="Weekly orders by city, 8 rows. Two numbers are wrong. Find them and write one line for your manager.",
        material_name="Weekly orders by city",
        material=_ORDERS,
        material_kind="table",
        checks=(
            "Found the repeated Pune row",
            "Found revenue in lakhs among rupees",
            "Wrote a line a manager can act on",
            "Said how to stop it next week",
        ),
        follow_ups=(
            FollowUp(
                "The Chennai number",
                "How do you know the Chennai number was in lakhs, and not 7.2 rupees?",
                (
                    "Compares revenue per order with the other cities",
                    "Says the figure looks like lakhs, and what to confirm with the source",
                ),
            ),
            FollowUp(
                "The line for your manager",
                "What would you check before sending that line to your manager?",
                (
                    "Names the totals or figures to recompute",
                    "Says who or what to confirm the corrected numbers with",
                ),
            ),
            FollowUp(
                "Using the assistant",
                "What did you ask the assistant, and which of its numbers did you check yourself?",
                (
                    "Says what was asked of the assistant",
                    "Names a figure that was checked by hand or against the table",
                ),
                about_assistant=True,
            ),
        ),
    ),
    WorkTemplate(
        family="marketing",
        label="Marketing",
        keywords=("marketing", "growth", "content", "brand", "social media", "seo", "campaign"),
        task=(
            "A launch email for a new mango flavour, to people who bought last summer. "
            "Write the subject line and the first two lines."
        ),
        material_name="The brief",
        material=_MANGO_BRIEF,
        material_kind="text",
        checks=(
            "Said who it's for",
            "One clear offer",
            "A subject under 50 characters",
            "Said how they'd measure it",
        ),
        follow_ups=(
            FollowUp(
                "The offer",
                "Why did you lead with that offer, and not the flavour?",
                (
                    "Gives a reason tied to the goal of first orders",
                    "Says what the reader should do next",
                ),
            ),
            FollowUp(
                "The audience",
                "Who on that list would you leave out, and why?",
                (
                    "Names a group on the list and a reason",
                    "Links the reason to last summer's purchase",
                ),
            ),
            FollowUp(
                "Using the assistant",
                "What did you ask the assistant for, and what did you change in its wording?",
                (
                    "Says what was asked of the assistant",
                    "Gives a reason for each change to its wording",
                ),
                about_assistant=True,
            ),
        ),
    ),
    WorkTemplate(
        family="nursing",
        label="Nursing",
        keywords=("nurse", "nursing", "staff nurse", "clinical"),
        task="A handover note for bed 12 has two problems in it. What do you check first, and who do you tell?",
        material_name="Bed 12 · handover note",
        material=_HANDOVER,
        material_kind="text",
        checks=(
            "Put the low pressure and missing output first",
            "Told the doctor in charge",
            "Said what to write down",
        ),
        follow_ups=(
            FollowUp(
                "What comes first",
                "You put one line first. Why that one before the others?",
                (
                    "Gives a reason that rests on the note",
                    "Says what could happen if it waits",
                ),
            ),
            FollowUp(
                "Who needs to know",
                "Who else on the ward needs to know, and when?",
                (
                    "Names a person or role and a moment",
                    "Says what is passed on and how it is recorded",
                ),
            ),
            FollowUp(
                "Using the assistant",
                "What did you ask the assistant, and what did you check against your ward's own protocol?",
                (
                    "Says what was asked of the assistant",
                    "Says what was checked against the ward's protocol",
                ),
                about_assistant=True,
            ),
        ),
    ),
    WorkTemplate(
        family="accounts",
        label="Accounts",
        keywords=("accountant", "accounts", "bookkeeper", "finance executive", "audit", "ca", "tax"),
        task="September's bank reconciliation is off by ₹4,500. Find the cause and the entry that fixes it.",
        material_name="Cashbook · September",
        material=_CASHBOOK,
        material_kind="table",
        checks=(
            "Found the double entry",
            "Wrote the correcting entry",
            "Suggested a check to stop it",
        ),
        follow_ups=(
            FollowUp(
                "The correcting entry",
                "Why a correcting entry, and not deleting the second payment?",
                (
                    "Says the books keep a trail and an entry is not removed",
                    "Names the entry and its two sides",
                ),
            ),
            FollowUp(
                "A real second payment",
                "How would you tell a duplicate from two real payments of the same amount?",
                (
                    "Names a document or reference to compare",
                    "Says who or what confirms it",
                ),
            ),
            FollowUp(
                "Using the assistant",
                "What did you ask the assistant, and how did you check its entry yourself?",
                (
                    "Says what was asked of the assistant",
                    "Says how the entry was checked against the cashbook",
                ),
                about_assistant=True,
            ),
        ),
    ),
    WorkTemplate(
        family="teaching",
        label="Teaching and instructional design",
        keywords=("teacher", "tutor", "lecturer", "instructional designer", "trainer", "educator"),
        task=(
            "A 60-minute lesson on fractions for 40 students has 80 minutes of activities. "
            "What do you cut, and why?"
        ),
        material_name="Fractions lesson",
        material=_LESSON,
        material_kind="text",
        checks=(
            "Kept the learning goal",
            "Cut with a reason",
            "Said how they'd check understanding",
        ),
        follow_ups=(
            FollowUp(
                "What you cut",
                "Which activity did you cut first, and what would the students miss?",
                (
                    "Names an activity and what it taught",
                    "Says how the goal still gets met",
                ),
            ),
            FollowUp(
                "Checking understanding",
                "How would you know in the lesson itself that students can compare two fractions?",
                (
                    "Names something students do or say that shows it",
                    "Says what happens if many are not there yet",
                ),
            ),
            FollowUp(
                "Using the assistant",
                "What did you ask the assistant, and what did you change to fit your students?",
                (
                    "Says what was asked of the assistant",
                    "Gives a reason for each change",
                ),
                about_assistant=True,
            ),
        ),
    ),
)


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    words = [re.escape(part) for part in re.split(r"[\s-]+", keyword.strip().lower()) if part]
    return re.compile(rf"(?<![a-z0-9]){'[\\s-]*'.join(words)}(?![a-z0-9])")


_PATTERNS: Final[tuple[tuple[WorkTemplate, tuple[tuple[str, re.Pattern[str]], ...]], ...]] = tuple(
    (template, tuple((kw, _keyword_pattern(kw)) for kw in template.keywords)) for template in TEMPLATES
)


def template_for_title(title: str) -> WorkTemplate | None:
    """The template of the family a job title belongs to, or None. A title that fits two families goes to the one with
    the longer keyword, then the one named first in it, then the first template."""
    lowered = title.lower()
    best: tuple[int, int, int] | None = None
    chosen: WorkTemplate | None = None
    for order, (template, patterns) in enumerate(_PATTERNS):
        for keyword, pattern in patterns:
            found = pattern.search(lowered)
            if found is None:
                continue
            rank = (-len(keyword), found.start(), order)
            if best is None or rank < best:
                best, chosen = rank, template
    return chosen


def template_for_family(family: str) -> WorkTemplate | None:
    """The template with this family key ("frontend", "data" ...), or None."""
    return next((t for t in TEMPLATES if t.family == family), None)


__all__ = [
    "TEMPLATES",
    "TEMPLATE_MINUTES",
    "FollowUp",
    "WorkTemplate",
    "template_for_family",
    "template_for_title",
]
