"""PII masking: for text sent to a second processor (Jev) and for every log line.

`redact_pii` masks the three kinds of contact data a resume carries (emails, phone numbers, street
address lines) and leaves everything else alone, including years, date ranges, scores and short
numbers. It is a safety net, not an anonymiser: names, employers and links stay, because the analysis
needs them. `redact_event_dict` is the structlog processor that keeps resume text, prompts and notes out
of the logs entirely.

All patterns are bounded (no unbounded nested quantifiers), so hostile input of tens of thousands of
characters is scanned in linear time.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

EMAIL_MASK = "[email]"
PHONE_MASK = "[phone]"
TOKEN_MASK = "[token]"  # noqa: S105  (a placeholder, not a password)
ADDRESS_MASK = "[address]"

SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "resume",
        "text",
        "content",
        "notes",
        "message",
        "state",
        "prompt",
        "system",
        "body",
        "raw",
        "excerpt",
        "token",
        "access_token",
        "refresh_token",
        "cookie",
        "authorization",
    }
)
"""Log keys whose value is replaced by ``<redacted N chars>`` (matched case-insensitively, exact name)."""

_SAFE_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "level",
        "logger",
        "logger_name",
        "exc_info",
        "request_id",
        "analysis_id",
        "profile_id",
        "session_id",
        "ip_hash",
    }
)
"""Structured fields that never carry user content; ids are skipped so hex runs are never mistaken for phones."""

_MAX_DEPTH = 4
_ACCESS_ID_KEYS = ("profile_id", "session_id", "handoff_id")
_ACCESS_ID_PREFIX = 15  # "sample-" + 8 hex, or 15 hex characters
_MAX_ADDRESS_LINE = 400

# A secret in a query string (the one-click unsubscribe link of The Leafout Weekly: ``/api/weekly/unsubscribe?token=...``).
_TOKEN_IN_QUERY = re.compile(r"([?&](?:unsubscribe_)?token=)[^&#\s\"'<>]+", re.IGNORECASE)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8}")

# A run of 8 to 19 digits with at most three separators ( space . - parentheses ) between neighbours,
# optionally led by "+" and "(". Newlines are not separators, so a phone never spans two lines. Which
# runs really are phone numbers is decided in `_mask_phone` (digit count, years, dates).
_PHONE_CANDIDATE = re.compile(
    r"(?<![\w+])(?:\+[ \t\u00a0]?)?(?:\([ \t]?)?\d(?:[ \t\u00a0().-]{0,3}\d){7,18}(?!\w)"
)
_DIGITS_IN_GROUP = re.compile(r"\d+")
_ISO_DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")
_DMY_DATE_PREFIX = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{4}(?!\d)")
_MIN_PHONE_DIGITS = 9
_MIN_PHONE_DIGITS_WITH_PLUS = 8
_MAX_PHONE_DIGITS = 15  # E.164

_ADDRESS_LABEL = re.compile(
    r"^\s*(?:(?:permanent|current|mailing|home|residential|postal)\s+)?(?:address|addr)\s*[:\-–]\s*\S",
    re.IGNORECASE,
)
_STREET = re.compile(
    r"\b\d{1,5}[A-Za-z]?(?:[-/]\d{1,5})?,?\s+(?:[A-Za-z0-9.'’-]+\s+){0,4}"
    r"(?:street|avenue|boulevard|road|lane|ave\.?|blvd\.?|rd\.|st\.|ln\.)(?![A-Za-z])",
    re.IGNORECASE,
)
_UNIT = re.compile(
    r"\b(?:flat|plot|house|h\.?\s?no|door|apartment|apt|suite|unit|floor|block|sector|phase|building|bldg)\b\.?"
    r"\s*(?:no\.?|number)?\s*[#:\-]?\s*\d",
    re.IGNORECASE,
)
_LOCALITY = re.compile(
    r"\b(?:apartments?|residency|nagar|colony|layout|enclave|society|towers?|heights|road|cross|main|street|"
    r"extension|vihar|puram|marg)\b",
    re.IGNORECASE,
)
_POSTAL = re.compile(r"(?<!\d)(?:\d{6}|\d{5}(?:-\d{4})?)(?!\d)")
_CITY_STATE_POSTAL_END = re.compile(
    r",\s*(?:[A-Za-z][A-Za-z .'-]{2,30}|[A-Z]{2}),?\s+(?:\d{6}|\d{5}(?:-\d{4})?)\s*$"
)
_BULLET_START = re.compile(r"^\s*(?:[•·*–—-]|\d+[.)])\s")


def _mask_phone(match: re.Match[str]) -> str:
    text = match.group(0)
    groups = _DIGITS_IN_GROUP.findall(text)
    count = sum(len(g) for g in groups)
    has_plus = text.lstrip("(").lstrip().startswith("+")
    if count > _MAX_PHONE_DIGITS or count < (_MIN_PHONE_DIGITS_WITH_PLUS if has_plus else _MIN_PHONE_DIGITS):
        return text
    # "2018 - 2020 - 2022": a list of years is not a phone number
    if all(len(g) == 4 and 1900 <= int(g) <= 2100 for g in groups):
        return text
    if _ISO_DATE_PREFIX.match(text) or _DMY_DATE_PREFIX.match(text):
        return text
    return PHONE_MASK


def _is_address_line(line: str) -> bool:
    if len(line) > _MAX_ADDRESS_LINE:
        return False
    if _ADDRESS_LABEL.match(line):
        return True
    if _BULLET_START.match(line):
        return False
    if _STREET.search(line):
        return True
    if _UNIT.search(line) and (_POSTAL.search(line) or (line.count(",") >= 2 and _LOCALITY.search(line))):
        return True
    return _CITY_STATE_POSTAL_END.search(line) is not None


def _redact_line(line: str) -> str:
    body = line.rstrip("\r\n")
    end = line[len(body) :]
    has_digit = any(c.isdigit() for c in body)
    if (has_digit or _ADDRESS_LABEL.match(body)) and _is_address_line(body):
        return f"{ADDRESS_MASK}{end}"
    if has_digit:
        return f"{_PHONE_CANDIDATE.sub(_mask_phone, body)}{end}"
    return line


def redact_pii(text: str) -> str:
    """Mask emails, phone numbers and likely street-address lines.

    - Emails become ``[email]``.
    - The value of a ``token=`` query parameter becomes ``[token]``.
    - Phone numbers become ``[phone]``: international (``+44 20 7946 0958``), Indian
      (``+91 98765 43210``, ``098765-43210``, ``(080) 2345 6789``) and US (``(415) 555-2671``) styles, 9 to
      15 digits (8 with a leading ``+``). Years, year ranges, ISO and day-month-year dates, and anything
      shorter are left alone.
    - A line that looks like a postal address (an ``Address:`` label, "12 Baker Street", "Flat 402, ...
      Layout, ... 560034", "City, ST 94103") becomes ``[address]``. Bullet lines are never treated as
      addresses.
    """
    if not text:
        return text
    if "token=" in text.lower():
        text = _TOKEN_IN_QUERY.sub(lambda m: m.group(1) + TOKEN_MASK, text)
    if "@" in text:
        text = _EMAIL.sub(EMAIL_MASK, text)
    if not any(c.isdigit() for c in text) and "ddr" not in text.lower():
        return text
    return "".join(_redact_line(line) for line in text.splitlines(keepends=True))


def _redact_value(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        return redact_pii(value) if value else value
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, Mapping):
        return {k: _redact_item(k, v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v, depth + 1) for v in value)
    return value


def _redact_item(key: Any, value: Any, depth: int) -> Any:
    name = key.lower() if isinstance(key, str) else ""
    if name in _SAFE_KEYS:
        return value
    if name in SENSITIVE_KEYS:
        if value is None:
            return None
        size = len(value) if isinstance(value, str | bytes | bytearray) else len(str(value))
        return f"<redacted {size} chars>"
    return _redact_value(value, depth)


def redact_event_dict(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: sensitive keys become ``<redacted N chars>``; other strings go through `redact_pii`.

    Nested dicts and lists are handled to a depth of four. Mutates and returns ``event_dict``.
    """
    for key in list(event_dict):
        event_dict[key] = _redact_item(key, event_dict[key], 0)
    for key in _ACCESS_ID_KEYS:
        value = event_dict.get(key)
        if isinstance(value, str) and len(value) > _ACCESS_ID_PREFIX + 1:
            # Profile and session ids are the only access control, so logs keep a correlatable prefix only.
            event_dict[key] = value[:_ACCESS_ID_PREFIX] + "…"
    return event_dict


__all__ = [
    "ADDRESS_MASK",
    "EMAIL_MASK",
    "PHONE_MASK",
    "SENSITIVE_KEYS",
    "TOKEN_MASK",
    "redact_event_dict",
    "redact_pii",
]
