"""Small pure helpers for received mail: addresses in their many shapes, the recipients on our domain, and HTML to text.

Nothing here logs or keeps anything; the body only passes through `html_to_text`.
"""

from __future__ import annotations

import html
import re
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any, Final

RECIPIENT_KEYS: Final = (
    "to",
    "received_for",
    "receivedFor",
    "envelope_to",
    "recipient",
    "recipients",
    "forwarded_to",
)
"""Fields of Resend's webhook ``data`` (and of a fetched email) that may name who the mail was for. Resend documents ``to``;
the others are read in case the mail reached us as a Bcc or through a forward (their names are not verified)."""


def _strings(value: Any) -> list[str]:
    """A recipient field as strings: a string (maybe comma-separated), a list of strings, or ``{"email": ...}`` objects."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found = value.get("email") or value.get("address")
        return [found] if isinstance(found, str) else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


def addresses_in(value: Any) -> list[str]:
    """Every email address in a field, lower-cased, angle brackets and display names removed, in order, no repeats."""
    seen: dict[str, None] = {}
    for _, addr in getaddresses(_strings(value)):
        clean = addr.strip().strip("<>").strip().lower()
        if "@" in clean and clean not in seen:
            seen[clean] = None
    return list(seen)


def sender_of(value: Any) -> str:
    """The sender's bare address, lower-cased ("" when there is none)."""
    found = addresses_in(value)
    return found[0] if found else ""


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].strip().lower().rstrip(".") if "@" in address else ""


def local_parts_on(domain: str, *fields: Any) -> list[str]:
    """The local parts of every recipient in ``fields`` whose domain is ``domain``, in order, no repeats."""
    wanted = domain.lower().rstrip(".")
    out: dict[str, None] = {}
    for value in fields:
        for addr in addresses_in(value):
            local, _, dom = addr.rpartition("@")
            if dom.rstrip(".") == wanted and local:
                out[local] = None
    return list(out)


def recipients_from(data: dict[str, Any], domain: str) -> list[str]:
    """Local parts on ``domain`` in a webhook's (or fetched email's) recipient fields."""
    return local_parts_on(domain, *(data.get(key) for key in RECIPIENT_KEYS))


# ───────────────────────── HTML to plain text ─────────────────────────

_BLOCK: Final = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote", "section",
     "article", "header", "footer", "hr"}
)  # fmt: skip
_SKIP: Final = frozenset({"script", "style", "head", "title", "noscript", "template"})


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(self._skip - 1, 0)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Readable plain text from an HTML email: no tags, scripts or styles; block elements become line breaks."""
    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # a broken document: strip the tags crudely rather than fail
        return _squeeze(html.unescape(re.sub(r"<[^>]*>", " ", markup)))
    return _squeeze("".join(parser.parts))


def _squeeze(text: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v ]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


__all__ = [
    "RECIPIENT_KEYS",
    "addresses_in",
    "domain_of",
    "html_to_text",
    "local_parts_on",
    "recipients_from",
    "sender_of",
]
