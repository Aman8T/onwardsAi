"""The tip and the quote of an edition: written once, here, in the house voice, and rotated by issue number. No model writes
them and none names a real person. A tip says what to do and never claims anything about the reader."""

from __future__ import annotations

from typing import Final

TIPS: Final[tuple[str, ...]] = (
    "Put one number on one line of your resume this week: a count, a time saved, a size. A reader remembers a figure before an adjective.",
    "Read a posting's last three requirements first. They are often the ones the team is short of, and the ones your note should answer.",
    "Keep a file of what you finished, one line every Friday. In six months it is your resume, already written.",
    "Apply on the employer's own board, not on a feed's copy of it. The feed may be a week old and the role may be closed.",
    "A link to something you built beats a sentence about it. Pin your best two projects and say in one line what each one does.",
    "Take one question to every interview that you could only ask this team. It shows you read past the title.",
    "When a posting names a tool you have not used, say what you have used that sits next to it. Do not claim the tool.",
    "Practise one answer out loud this week: the work you are proudest of, in three parts: the situation, what you did, what changed.",
    "Write to one person who does the job you want, and ask one question that takes a minute to answer.",
    "Update your resume the week you finish something, not the week you need it.",
)
"""Ten tips; an edition shows `TIPS[(issue - 1) % 10]`."""

QUOTES: Final[tuple[str, ...]] = (
    "Good work, kept in a list, becomes a career.",
    "A small proof today is worth more than a large promise next month.",
    "Learn the next thing the job asks for, not the thing everyone is talking about.",
    "Ask for the work that scares you a little and has someone to learn from.",
    "What you can show, you can be hired for.",
    "Progress is mostly the same hour, spent the same way, many weeks in a row.",
    "A clear sentence about what you did is a skill of its own.",
    "You do not need to be ready for the whole role. You need to be ready for its first month.",
    "Keep your evidence closer than your opinions.",
)
"""Nine quotes (a different count from the tips, so the pairs do not repeat for 90 issues)."""


def tip_for(issue: int) -> str:
    return TIPS[(max(issue, 1) - 1) % len(TIPS)]


def quote_for(issue: int) -> str:
    return QUOTES[(max(issue, 1) - 1) % len(QUOTES)]


__all__ = ["QUOTES", "TIPS", "quote_for", "tip_for"]
