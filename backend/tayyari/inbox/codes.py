"""Verification codes and ATS verification links, read in code (code computes, models classify: no model reads these).

A **code** is 4 to 8 letters and digits with at least one digit (and at least two when it has letters too, so a role name
such as "SDE2" is not one), standing on its own (not glued to ``#``, ``-``, ``$``, ``:``, ``/`` or a decimal point), close
to a phrase that names a code (up to `AFTER_CHARS` after it, or `BEFORE_CHARS` before it: "482913 is your code"):

- always: "verification code", "security code", "confirmation code", "passcode", "OTP", "one-time password/passcode/pin/
  code", "login/sign-in/access code";
- a plain "code" (not "postal code", "job code", "promo code" and the like) only when the email also says one of the D6
  keywords: "verification", "verify", "one-time", "passcode", "OTP", "security code" or "confirmation code".

"verify" or "verification" alone never anchors a code: employers write "please verify your availability on the 14th" and
"background verification for role SDE2", and those emails must reach the classifier. What is never a code either: a year
(1900 to 2099), an ordinal or a time ("14th", "10am"), a number that is part of a phone number, an amount, and anything
right after "order", "invoice", "ref", "ticket", "id" or "#".

A **link** counts only when its host is on `ATS_HOSTS` (the host or a subdomain of it), it is https, its path or query says
verify, confirm, activate, validate, token or otp, and the email has one of the keywords. Links in the HTML's ``href``
attributes are read too, since `mail.html_to_text` drops them.

`scrub` hides code-shaped tokens in a subject that talks about codes, for every row we store (a subject is stored before
the email is read, and stays on rows that are never read).

The value is returned to the caller and stored for 15 minutes; it is never logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

ATS_HOSTS: Final = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "myworkday.com",
    "workday.com",
    "smartrecruiters.com",
    "workable.com",
)
AFTER_CHARS: Final = 80
BEFORE_CHARS: Final = 40

KEYWORDS: Final = re.compile(
    r"verification|security\s+code|\bverify\b|one[\s-]?time|pass\s?code|\bOTP\b|confirmation\s+code",
    re.I,
)
"""The D6 keywords: an email without one carries no link, and a plain "code" in it anchors nothing."""
CODE_PHRASES: Final = re.compile(
    r"(?:verification|security|confirmation|access|log[\s-]?in|sign[\s-]?in|auth(?:entication)?|2fa|mfa)\s+code"
    r"|\bpass\s?code|\bOTP\b|one[\s-]?time\s+(?:pass(?:word|code)?|pin|code)",
    re.I,
)
"""Phrases that name a code on their own."""
_PLAIN_CODE: Final = re.compile(
    r"(?<!postal )(?<!zip )(?<!area )(?<!promo )(?<!discount )(?<!coupon )(?<!referral )(?<!source )(?<!tax )"
    r"(?<!country )(?<!dress )(?<!job )(?<!role )(?<!course )(?<!reference )(?<!qr )(?<!bar )(?<!pin )\bcodes?\b",
    re.I,
)
"""A plain "code", which anchors a candidate only in an email that also has a `KEYWORDS` match."""
_CANDIDATE: Final = re.compile(r"(?<![\w#+\-/.:$₹€£@=?&])[A-Za-z0-9]{4,8}(?![\w\-/:@]|[.,]\d)")
_PHONE: Final = re.compile(r"\+?\(?\d[\d\s().-]{6,}\d")
_YEAR: Final = re.compile(r"^(19|20)\d\d$")
_ORDINAL_OR_TIME: Final = re.compile(r"^\d{1,4}(st|nd|rd|th|am|pm|hrs?|mins?|secs?|days?|yrs?|k|m)$", re.I)
_REFERENCE: Final = re.compile(
    r"(order|invoice|ref(erence)?|ticket|req(uisition)?|job|id|no|number|#)\s*(no\.?|number|#|id)?\s*[:#.]?\s*$",
    re.I,
)
_URL: Final = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.I)
_HREF: Final = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.I)
_LINK_WORDS: Final = re.compile(r"verif|confirm|activat|validat|token|otp|magic", re.I)


@dataclass(frozen=True, slots=True)
class Verification:
    """What an email carried: a code, or else a link. Values are left out of ``repr`` so they never reach a log by accident."""

    code: str | None = field(default=None, repr=False)
    link: str | None = field(default=None, repr=False)


def _phone_digits(span: str) -> int:
    return sum(ch.isdigit() for ch in span)


def _in_phone(text: str, start: int, end: int) -> bool:
    for m in _PHONE.finditer(text):
        if m.start() <= start and end <= m.end() and _phone_digits(m.group(0)) >= 9:
            return True
    return False


def _is_code(text: str, m: re.Match[str]) -> bool:
    token = m.group(0)
    digits = sum(ch.isdigit() for ch in token)
    if digits == 0 or (digits < 2 and not token.isdigit()):
        return False
    if _ORDINAL_OR_TIME.match(token):
        return False
    if token.isdigit() and _YEAR.match(token):
        return False
    if token.isdigit() and _in_phone(text, m.start(), m.end()):
        return False
    before = text[max(0, m.start() - 16) : m.start()]
    return not _REFERENCE.search(before)


def _anchors(text: str) -> list[tuple[int, int]]:
    """Where ``text`` names a code: every `CODE_PHRASES` match, and a plain "code" when the text has a D6 keyword."""
    spans = [(k.start(), k.end()) for k in CODE_PHRASES.finditer(text)]
    if KEYWORDS.search(text):
        spans += [(k.start(), k.end()) for k in _PLAIN_CODE.finditer(text)]
    return spans


def find_code(text: str) -> str | None:
    """The code in ``text`` closest after a phrase that names a code (else just before one), or None."""
    if not text:
        return None
    keywords = _anchors(text)
    if not keywords:
        return None
    best: tuple[int, str] | None = None
    for m in _CANDIDATE.finditer(text):
        if not _is_code(text, m):
            continue
        for k_start, k_end in keywords:
            if k_end <= m.start() and m.start() - k_end <= AFTER_CHARS:
                distance = m.start() - k_end
            elif m.end() <= k_start and k_start - m.end() <= BEFORE_CHARS:
                distance = (k_start - m.end()) + AFTER_CHARS  # a code after a keyword wins over one before
            else:
                continue
            if best is None or distance < best[0]:
                best = (distance, m.group(0))
    return None if best is None else best[1]


def on_ats_host(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https" or not host:
        return False
    return any(host == d or host.endswith("." + d) for d in ATS_HOSTS)


def find_link(text: str, html: str | None = None) -> str | None:
    """The first https link on an ATS host whose path or query says it verifies something, or None."""
    urls = [m.group(0) for m in _URL.finditer(text or "")]
    urls += [m.group(1).replace("&amp;", "&") for m in _HREF.finditer(html or "")]
    for url in urls:
        clean = url.rstrip(".,;")
        if not on_ats_host(clean):
            continue
        parts = urlsplit(clean)
        if _LINK_WORDS.search(f"{parts.path}?{parts.query}"):
            return clean
    return None


def _without_links(text: str) -> str:
    """``text`` with its URLs blanked out: a token or a keyword inside a link is neither a code nor a keyword."""
    return _URL.sub(" ", text)


def extract(subject: str, text: str, html: str | None = None) -> Verification | None:
    """A verification code (the subject first, then the subject and the text together, so a keyword in the subject reaches a
    code at the top of the text), else a verification link, else None. A link needs a keyword outside any link, like a
    code."""
    head, body = _without_links(subject or ""), _without_links(text or "")
    code = find_code(head) or find_code(f"{head}\n{body}" if head else body)
    if code is not None:
        return Verification(code=code)
    if not (KEYWORDS.search(head) or KEYWORDS.search(body)):
        return None
    link = find_link(text or "", html)
    return Verification(link=link) if link is not None else None


def scrub(text: str) -> str:
    """``text`` (a subject) with every code-shaped token replaced by ``[code]`` when it talks about codes at all (a D6
    keyword, or a phrase or a plain "code" that names one); else unchanged. Wider than `find_code` on purpose: it runs on
    every subject we store, including ones never read ("Your Greenhouse code is 482913" to an address that is off)."""
    if not text or not (KEYWORDS.search(text) or CODE_PHRASES.search(text) or _PLAIN_CODE.search(text)):
        return text
    out, last = [], 0
    for m in _CANDIDATE.finditer(text):
        if _is_code(text, m):
            out.append(text[last : m.start()])
            out.append("[code]")
            last = m.end()
    out.append(text[last:])
    return "".join(out)


def redact(text: str, code: str | None) -> str:
    """``text`` with ``code`` replaced, so a subject such as "Your code is 482913" is stored without it."""
    if not code:
        return text
    return re.sub(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", "[code]", text)


__all__ = [
    "AFTER_CHARS",
    "ATS_HOSTS",
    "BEFORE_CHARS",
    "CODE_PHRASES",
    "KEYWORDS",
    "Verification",
    "extract",
    "find_code",
    "find_link",
    "on_ats_host",
    "redact",
    "scrub",
]
