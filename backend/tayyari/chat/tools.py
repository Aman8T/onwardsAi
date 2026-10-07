"""Chat tool helpers: the UI's names for the web tools, and the "is there a link in this message" check.

The Anthropic API calls the server tools ``web_search`` and ``web_fetch``; the web app only prettifies
``WebSearch`` and ``WebFetch`` ("Searched the web"). The Claude client already emits the UI names; `ui_tool_name`
is the one place the chat service maps a name that arrives in the raw form, so the live defect of the old port
(raw names reached the UI, which showed them as is) cannot return through another client.
"""

from __future__ import annotations

import re
from typing import Final

from tayyari.llm.anthropic_client import WEB_TOOL_UI_NAMES

# Words that look like a domain ("Node.js/React", "main.py/utils") but are file or framework names.
_NOT_TLDS: Final[frozenset[str]] = frozenset(
    {
        "js",
        "jsx",
        "ts",
        "tsx",
        "json",
        "yml",
        "yaml",
        "py",
        "rs",
        "go",
        "rb",
        "md",
        "txt",
        "css",
        "html",
        "cfg",
    }
)

_SCHEME_URL: Final[re.Pattern[str]] = re.compile(r"https?://[^\s/$.?#<>\"'][^\s<>\"']*", re.IGNORECASE)
_BARE_DOMAIN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w@.-])(?P<host>(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?P<tld>[a-z]{2,24}))(?::\d{1,5})?(?P<rest>/[^\s<>\"']+)?",
    re.IGNORECASE,
)


def url_in_message(text: str) -> bool:
    """True when the message holds something to open: an ``http(s)://`` URL, or a bare domain with a path.

    ``github.com/ada/engine`` and ``www.acme.dev`` count (the person pasted a page); ``example.com`` alone, an
    email address, a version number or a file name such as ``Node.js/React`` do not. A hit is a hint only: the
    chat service then keeps the web tools available without asking the decision gate.
    """
    if _SCHEME_URL.search(text):
        return True
    for match in _BARE_DOMAIN.finditer(text):
        if match["tld"].lower() in _NOT_TLDS:
            continue
        if match["rest"] or match["host"].lower().startswith("www."):
            return True
    return False


def ui_tool_name(name: str) -> str:
    """``web_search`` -> ``WebSearch``, ``web_fetch`` -> ``WebFetch``; a name that is already a UI name is returned as is."""
    return WEB_TOOL_UI_NAMES.get(name, name)


__all__ = ["WEB_TOOL_UI_NAMES", "ui_tool_name", "url_in_message"]
