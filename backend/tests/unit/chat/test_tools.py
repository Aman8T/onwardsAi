"""The chat tool helpers: UI tool names and the link check that forces the web tools on."""

from __future__ import annotations

import time

import pytest

from tayyari.chat import tools
from tayyari.chat.tools import WEB_TOOL_UI_NAMES, ui_tool_name, url_in_message
from tayyari.llm import anthropic_client


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/ada/engine",
        "Please read http://example.com for me",
        "HTTPS://EXAMPLE.COM/Path?q=1",
        "https://example.com",  # a scheme URL counts even without a path
        "(see https://acme.dev/careers).",
        "github.com/ada/engine",
        "my portfolio is at ada.dev/work, what do you think?",
        "linkedin.com/in/ada-lovelace",
        "docs.python.org/3/library/asyncio.html",
        "example.com:8080/status",
        "www.acme.dev",  # a www host is a page even without a path
        "WWW.ACME.DEV is our site",
        "Read\nhttps://example.com/a\nthen answer",
    ],
)
def test_links_are_found(text: str) -> None:
    assert url_in_message(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "How do I get better at testing?",
        "example.com",  # a bare domain without a path is just a word
        "mail me at ada@example.com/",
        "ada@example.com",
        "I know Node.js/React and main.py/utils well",
        "3.5/5 stars, v2.0/x",
        "e.g. this, i.e. that",
        "and/or either/or",
        "https://",
        "Try Next.js/TypeScript or Vue.js/Nuxt",
        "localhost:3000/health",
        "a.b/c",  # a one-letter top-level domain is not a domain
    ],
)
def test_plain_text_is_not_a_link(text: str) -> None:
    assert url_in_message(text) is False


def test_ui_tool_names_are_the_clients() -> None:
    assert WEB_TOOL_UI_NAMES is anthropic_client.WEB_TOOL_UI_NAMES
    assert WEB_TOOL_UI_NAMES == {"web_search": "WebSearch", "web_fetch": "WebFetch"}
    assert set(tools.__all__) == {"WEB_TOOL_UI_NAMES", "ui_tool_name", "url_in_message"}


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("web_search", "WebSearch"),
        ("web_fetch", "WebFetch"),
        ("WebSearch", "WebSearch"),
        ("WebFetch", "WebFetch"),
        ("something_else", "something_else"),
    ],
)
def test_ui_tool_name(raw: str, shown: str) -> None:
    assert ui_tool_name(raw) == shown


@pytest.mark.parametrize(
    "text",
    [
        "a." * 4000,
        "a-" * 4000,
        "a" * 8000,
        "a.b " * 2000,
        "-" * 8000,
        ("x" * 62 + ".") * 120,
        "." * 8000,
        "a" * 63 + "." + "b" * 63 + "." + "c" * 8000,
        "http" * 2000,
        "https://" + "a" * 8000,
    ],
)
def test_adversarial_input_is_fast(text: str) -> None:
    started = time.perf_counter()
    url_in_message(text)
    assert time.perf_counter() - started < 0.5
