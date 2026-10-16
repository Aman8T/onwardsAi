"""Reading a posting's text: HTML to plain text, which lines are requirements, and the years of experience it asks for.

Postings are untrusted data (project-soul.md): nothing here follows an instruction in a posting or opens a link in one.
The text is only cut up and counted. Everything is deterministic, with no model call.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Final

MAX_TEXT_CHARS: Final = 20_000
"""The most plain text kept per posting (the posting match refuses a longer one)."""
MAX_YEARS: Final = 40
"""A number of years above this is not a requirement for experience."""

_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_BLOCK_OPEN = re.compile(
    r"<\s*(?:p|div|section|article|h[1-6]|ul|ol|table|tr|blockquote)\b[^>]*>", re.IGNORECASE
)
_BLOCK_CLOSE = re.compile(
    r"<\s*/\s*(?:p|div|section|article|h[1-6]|ul|ol|table|tr|blockquote)\s*>", re.IGNORECASE
)
_LIST_ITEM = re.compile(r"<\s*li\b[^>]*>", re.IGNORECASE)
_BREAK = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]{0,2000}>")
_SPACES = re.compile(r"[ \t\r\f\v ​]+")
_BLANKS = re.compile(r"\n{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
"""Control characters other than tab, newline, form feed and carriage return. Postgres refuses NUL in text, and none of
them belongs in a posting."""


def strip_control(text: str) -> str:
    """``text`` without control characters (see `_CONTROL`)."""
    return _CONTROL.sub("", text)


def html_to_text(raw: str | None, *, limit: int = MAX_TEXT_CHARS) -> str:
    """Plain text from HTML, or from HTML that was escaped once (Greenhouse sends ``&lt;p&gt;...``).

    The markup is unescaped, script and style are dropped, block tags and list items become line breaks (a list item
    starts with ``- ``), the remaining tags go, entities are decoded, and whitespace is collapsed. Cut at ``limit``.
    """
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _SCRIPT.sub(" ", text)
    text = _BREAK.sub("\n", text)
    text = _LIST_ITEM.sub("\n- ", text)
    text = _BLOCK_CLOSE.sub("\n", text)
    text = _BLOCK_OPEN.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return clean_text(text, limit=limit)


def clean_text(text: str, *, limit: int = MAX_TEXT_CHARS) -> str:
    """Collapse whitespace inside each line, drop empty lines beyond one blank line, trim, and cut at ``limit``."""
    text = strip_control(text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    out = _BLANKS.sub("\n\n", "\n".join(lines)).strip()
    if len(out) > limit:
        out = out[:limit].rstrip()
    return out


# ───────────────────────── requirement lines ─────────────────────────

_HEADING_ASKS = re.compile(
    r"^(?:the\s+)?(?:requirements?|qualifications?|(?:basic|minimum|preferred|required|desired)\s+(?:qualifications?|requirements?|skills)"
    r"|what\s+you(?:'ll|’ll|\s+will)?\s+(?:need|bring|have)|what\s+we(?:'re|’re|\s+are)\s+looking\s+for|who\s+you\s+are"
    r"|you\s+(?:have|are|will\s+have|bring|should\s+have)|you(?:'ll|’ll)\s+bring|skills(?:\s+and\s+experience)?|must[\s-]+haves?"
    r"|nice[\s-]+to[\s-]+haves?|bonus\s+points|about\s+you|your\s+profile|your\s+background|experience|looking\s+for"
    r"|we\s+expect|desired\s+skills|key\s+skills|technical\s+skills|requirements\s+and\s+skills)\b",
    re.IGNORECASE,
)
_HEADING_OTHER = re.compile(
    r"^(?:the\s+)?(?:responsibilities|what\s+you(?:'ll|’ll|\s+will)?\s+do|the\s+role|about\s+(?:the\s+)?(?:role|us|team|company|job)"
    r"|benefits|perks|what\s+we\s+offer|why\s+(?:join|us)|compensation|how\s+to\s+apply|our\s+(?:values|culture)"
    r"|life\s+at|equal\s+opportunity|overview|role\s+overview|your\s+impact|in\s+this\s+role|day\s+to\s+day|team)\b",
    re.IGNORECASE,
)
_HEADING_NOISE = re.compile(
    r"^(?:the\s+)?(?:about\s+(?!(?:the\s+)?(?:role|job|position|opportunity|work|you|your|this)\b)"
    r"|benefits|perks|what\s+we\s+offer|what\s+you(?:'ll|’ll|\s+will)?\s+get|why\s+|who\s+we\s+are|our\s+(?:values|culture|mission|story|benefits|team|company|commitment)"
    r"|life\s+at|equal|eeo|diversity|compensation|salary|pay\s+range|how\s+to\s+apply|how\s+we\s+hire|hiring\s+process"
    r"|interview\s+process|privacy|legal|disclaimer|accommodations?|additional\s+information|working\s+at|join\s+us"
    r"|company\s+overview|a\s+note\s+on|notice)\b",
    re.IGNORECASE,
)
"""Headings of the parts of a posting that are about the company or what it offers, not about the job: a skill named only
there (a benefits section that says "compensation" or "learning budget") is not a skill the job asks for."""
_CUE = re.compile(
    r"\b(?:required|requirements?|must\s+have|must-have|minimum|proficien\w+|experience\s+(?:with|in|of|using)|"
    r"knowledge\s+of|familiar\w*\s+with|expertise\s+(?:in|with)|hands-on|you\s+have|you(?:'ve|’ve)|strong\s+\w+\s+skills?|"
    r"working\s+knowledge|ability\s+to|understanding\s+of|proven)\b",
    re.IGNORECASE,
)
_HEADING_MAX_CHARS: Final = 70


def _is_heading(line: str) -> bool:
    if not line or len(line) > _HEADING_MAX_CHARS or line.startswith(("- ", "• ", "* ")):
        return False
    stripped = line.rstrip(":").strip()
    if line.endswith(":"):
        return True
    if line.endswith((".", ",", ";")):
        return False
    known = _HEADING_ASKS.match(stripped) or _HEADING_OTHER.match(stripped) or _HEADING_NOISE.match(stripped)
    return bool(known) and len(stripped.split()) <= 8


def _add(spans: list[tuple[int, int]], start: int, end: int) -> None:
    if spans and spans[-1][1] + 1 >= start:
        spans[-1] = (spans[-1][0], end)
    else:
        spans.append((start, end))


def sections(text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """``(requirements, noise)``: character ranges of ``text``, each sorted and non-overlapping.

    *Requirements*: the lines under a heading that asks for something ("Requirements", "What you'll need",
    "Qualifications"), until the next heading, and any line elsewhere that says "required", "experience with",
    "proficiency" and the like. *Noise*: the lines under a heading about the company or what it offers ("About us",
    "Benefits", "Equal opportunity", "How to apply"), until the next heading: a skill named only there is not asked for.
    """
    required: list[tuple[int, int]] = []
    noise: list[tuple[int, int]] = []
    position = 0
    mode = ""
    for line in text.split("\n"):
        start, end = position, position + len(line)
        position = end + 1
        stripped = line.strip()
        if not stripped:
            continue
        if _is_heading(stripped):
            heading = stripped.rstrip(":").strip()
            if _HEADING_NOISE.match(heading):
                mode = "noise"
            elif _HEADING_ASKS.match(heading) and not _HEADING_OTHER.match(heading):
                mode = "asks"
            else:
                mode = ""
            continue
        if mode == "noise":
            _add(noise, start, end)
        elif mode == "asks" or _CUE.search(stripped):
            _add(required, start, end)
    return required, noise


def requirement_spans(text: str) -> list[tuple[int, int]]:
    """The requirement ranges of `sections`."""
    return sections(text)[0]


def in_spans(spans: list[tuple[int, int]], position: int) -> bool:
    """Whether ``position`` falls inside one of the sorted ``spans`` (binary search)."""
    low, high = 0, len(spans)
    while low < high:
        mid = (low + high) // 2
        start, end = spans[mid]
        if position < start:
            high = mid
        elif position > end:
            low = mid + 1
        else:
            return True
    return False


# ───────────────────────── years of experience ─────────────────────────

_YEAR_WORD = r"(?:years?|yrs?)"
_RANGE = re.compile(
    rf"(?<![\d.])(\d{{1,2}})\s*(?:-|–|—|to)\s*(\d{{1,2}})\s*\+?\s*{_YEAR_WORD}\b", re.IGNORECASE
)
_UP_TO = re.compile(
    rf"(?:up\s+to|upto|less\s+than|under|max(?:imum)?(?:\s+of)?)\s*(\d{{1,2}})\s*\+?\s*{_YEAR_WORD}\b",
    re.IGNORECASE,
)
_SINGLE = re.compile(
    rf"(?<![\d.\-–—])(\d{{1,2}})\s*(?:\+|(?:or|and)\s+(?:more|above|up))?\s*{_YEAR_WORD}\b", re.IGNORECASE
)
_AT_LEAST = re.compile(
    rf"(?:at\s*least|minimum(?:\s+of)?|min\.?(?:\s+of)?)\s*(\d{{1,2}})\s*\+?\s*{_YEAR_WORD}\b", re.IGNORECASE
)
_NOT_EXPERIENCE = re.compile(
    r"\b(?:ago|old|in\s+business|of\s+history|since|anniversary|warranty|founded|established|of\s+growth|"
    r"of\s+innovation|of\s+operation|of\s+trust|of\s+excellence|of\s+service|serving)\b",
    re.IGNORECASE,
)
_YEARS_CONTEXT = re.compile(
    r"\b(?:experience|experienced|exp|expertise|proficien\w+|hands-on|track\s+record|background|professional|relevant|"
    r"work(?:ing)?\s+(?:in|with|as|on)|industry)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"[\n•]|(?<=[.;!?])\s+")


@dataclass(frozen=True, slots=True)
class YearsAsked:
    """What a posting asks for in years. ``minimum`` is the years it asks for (see `years_asked`); ``upper`` is the top of a
    range or an "up to" limit when it gives one (``0-2 years`` has minimum 0 and upper 2)."""

    minimum: int
    upper: int | None


def _numbers(sentence: str) -> list[tuple[int, int | None]]:
    """``(minimum, upper)`` for every years phrase in ``sentence``."""
    found: list[tuple[int, int | None]] = []
    for match in _RANGE.finditer(sentence):
        low, high = int(match.group(1)), int(match.group(2))
        if low <= high <= MAX_YEARS:
            found.append((low, high))
    for match in _UP_TO.finditer(sentence):
        high = int(match.group(1))
        if high <= MAX_YEARS:
            found.append((0, high))
    taken = [m.span() for m in (*_RANGE.finditer(sentence), *_UP_TO.finditer(sentence))]
    for pattern in (_AT_LEAST, _SINGLE):
        for match in pattern.finditer(sentence):
            if any(start <= match.start(1) < end for start, end in taken):
                continue
            number = int(match.group(1))
            if number <= MAX_YEARS:
                found.append((number, None))
                taken.append(match.span())
    return found


def years_asked(text: str) -> YearsAsked | None:
    """The years of experience a posting asks for, or None when it does not say.

    Only sentences that read as a requirement count: they talk about experience, expertise or the like (or sit under a
    requirements heading or carry a requirement cue), and are not about the company's age ("founded 10 years ago").
    In one sentence the smallest number is taken ("3-5 years" asks for 3). Across sentences the largest of those is the
    ask ("8+ years in software, 3+ years of React" asks for 8), so a posting for a senior role is not read as junior.
    """
    spans = requirement_spans(text)
    position = 0
    best: YearsAsked | None = None
    for raw in _SENTENCE_SPLIT.split(text):
        start = text.find(raw, position) if raw else position
        if start < 0:
            start = position
        position = start + len(raw)
        sentence = raw.strip()
        if not sentence or _NOT_EXPERIENCE.search(sentence):
            continue
        numbers = _numbers(sentence)
        if not numbers:
            continue
        if not (_YEARS_CONTEXT.search(sentence) or _CUE.search(sentence) or in_spans(spans, start)):
            continue
        low = min(n[0] for n in numbers)
        uppers = [n[1] for n in numbers if n[1] is not None]
        upper = max(uppers) if uppers else None
        if (
            best is None
            or low > best.minimum
            or (low == best.minimum and upper is not None and (best.upper is None or upper > best.upper))
        ):
            best = YearsAsked(low, upper)
    return best


__all__ = [
    "MAX_TEXT_CHARS",
    "MAX_YEARS",
    "YearsAsked",
    "clean_text",
    "html_to_text",
    "in_spans",
    "requirement_spans",
    "sections",
    "years_asked",
]
