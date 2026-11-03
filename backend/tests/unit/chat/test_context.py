"""The chat prompt blocks: parity with the legacy prompt, determinism, cache layout and the LRU."""

from __future__ import annotations

import copy
import hashlib
import importlib
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from tayyari.chat import context as chat_context
from tayyari.chat.context import (
    CHAT_RULES,
    ChatPromptBlocks,
    ChatPromptRenderer,
    build_graph,
    first_name,
    profile_fingerprint,
    render_about_user,
    render_origin_block,
    render_profile_block,
)
from tayyari.contract import StoredContext
from tayyari.contract.stored import StoredSource
from tayyari.core import limits
from tayyari.llm.types import TextBlock
from tayyari.prompts import chat as ported_chat
from tayyari.prompts.common import _json
from tayyari.sample.provider import SampleProvider

BACKEND_DIR = Path(__file__).resolve().parents[3]

SKILL_ORIGIN = {
    "kind": "skill",
    "refId": "motion-design",
    "title": "Motion & animation",
    "caption": "Frontend · score 91",
}
ORIGINS: dict[str, dict[str, Any] | None] = {
    "none": None,
    "skill": SKILL_ORIGIN,
    "free-as-sent-by-the-api": {"kind": "free", "title": "", "caption": ""},
    "role-with-quotes": {"kind": "role", "refId": "r1", "title": 'Design "Engineer"', "caption": "Role"},
    "unknown-kind": {"kind": "mystery", "title": "Something", "caption": ""},
}


@pytest.fixture(scope="module")
def sample() -> SampleProvider:
    return SampleProvider()


@pytest.fixture(scope="module")
def profile(sample: SampleProvider) -> dict[str, Any]:
    assert sample.sample_profile is not None
    return sample.sample_profile.to_dict()


@pytest.fixture(scope="module")
def context(sample: SampleProvider) -> StoredContext:
    return sample.sample_context


def _legacy_builders() -> list[Callable[..., str]]:
    builders: list[Callable[..., str]] = [ported_chat.build_chat_system]
    try:
        builders.append(importlib.import_module("tayyari.legacy.prompts").build_chat_system)
    except ImportError:  # the legacy app is deleted in the last wave; the ported builder stays the reference
        pass
    return builders


def lines_of(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def legacy_text(
    builder: Callable[..., str], profile: Mapping[str, Any], context: StoredContext, origin: Any
) -> str:
    sources = [s.model_dump() for s in context.sources]
    return builder(profile, origin, sources, context.readers, budget=limits.CHAT_EXCERPT_BUDGET_CHARS)


# ───────────────────────── parity with the legacy prompt ─────────────────────────


@pytest.mark.parametrize("origin", list(ORIGINS.values()), ids=list(ORIGINS))
@pytest.mark.parametrize("builder", _legacy_builders(), ids=lambda b: b.__module__)
def test_blocks_keep_every_legacy_line(
    builder: Callable[..., str],
    origin: dict[str, Any] | None,
    profile: dict[str, Any],
    context: StoredContext,
) -> None:
    """Joined, the blocks hold every non-blank legacy line; the only lines that differ are the intentional ones."""
    legacy = lines_of(legacy_text(builder, profile, context, origin))
    mine = lines_of(ChatPromptRenderer().render(profile, context, origin).text)

    missing = [line for line in legacy if line not in set(mine)]
    added = [line for line in mine if line not in set(legacy)]

    # Intentional difference 1: the opening line no longer names the person (the name moves to "About the user").
    # Intentional difference 2: the tools are called "web search and web fetch", not WebSearch / WebFetch.
    assert missing == [
        "You are Leafout, a candid, specific and warm career coach for Riya. You have already analysed their",
        "- You may use WebSearch / WebFetch sparingly — to pull current, high-quality resources (docs, courses, papers) or to",
    ]
    assert added == [
        "You are Leafout, a candid, specific and warm career coach for the user. You have already analysed their",
        "- You may use web search and web fetch sparingly — to pull current, high-quality resources (docs, courses, papers) or to",
        "## About the user",
        'Their first name is Riya: address them as "Riya" (sparingly).',
    ]


@pytest.mark.parametrize("builder", _legacy_builders(), ids=lambda b: b.__module__)
def test_the_legacy_section_text_is_unchanged_except_for_those_lines(
    builder: Callable[..., str], profile: dict[str, Any], context: StoredContext
) -> None:
    """Not only the lines: every legacy section is a verbatim run of text in one block."""
    legacy = legacy_text(builder, profile, context, SKILL_ORIGIN)
    blocks = ChatPromptRenderer().render(profile, context, SKILL_ORIGIN)

    data_sections = legacy[legacy.index("## Their profile (the graph they are looking at)") :]
    assert data_sections in blocks.profile
    why = legacy[legacy.index("## Why they are here") : legacy.index("## How to answer")].rstrip("\n")
    assert blocks.origin == why
    how = legacy[legacy.index("## How to answer") : legacy.index("## Their profile")].rstrip("\n")
    assert (
        how.replace("WebSearch / WebFetch", "web search and web fetch")
        == CHAT_RULES[CHAT_RULES.index("## How to answer") :]
    )


def test_the_sections_come_in_cache_order(profile: dict[str, Any], context: StoredContext) -> None:
    text = ChatPromptRenderer().render(profile, context, SKILL_ORIGIN).text
    order = [
        "## How to answer",
        "## About the user",
        "## Their profile (the graph they are looking at)",
        "## What each reader found",
        "## Raw source excerpts",
        "## Why they are here",
    ]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    assert all(text.count(h) == 1 for h in order)


# ───────────────────────── block contents ─────────────────────────


def test_rules_are_static_and_name_nobody(profile: dict[str, Any]) -> None:
    assert "Riya" not in CHAT_RULES
    assert "Kapoor" not in CHAT_RULES
    assert "WebSearch" not in CHAT_RULES
    assert "WebFetch" not in CHAT_RULES
    assert "web search and web fetch" in CHAT_RULES
    assert "{" not in CHAT_RULES  # nothing is interpolated
    assert not re.search(r"\b(19|20)\d\d\b", CHAT_RULES)  # no date or year
    assert not re.search(
        r"\b(January|February|March|April|June|July|August|September|October|November|December)\b", CHAT_RULES
    )
    assert CHAT_RULES.startswith("You are Leafout, a candid, specific and warm career coach for the user.")


def test_profile_block_holds_the_graph_the_readers_and_the_excerpts(
    profile: dict[str, Any], context: StoredContext
) -> None:
    block = render_profile_block(profile, context)
    assert block.startswith(
        '## About the user\nTheir first name is Riya: address them as "Riya" (sparingly).\n\n'
    )
    assert f"## Their profile (the graph they are looking at)\n{_json(build_graph(profile))}\n" in block
    for sid, reader in context.readers.items():
        assert f"[{sid}] {reader.get('summary', '')}" in block
    for source in context.sources:
        assert f'<source id="{source.id}" kind="{source.kind}"' in block
    # the resume is the first excerpt
    assert block.index('<source id="resume"') < block.index('<source id="github"')


def test_the_graph_is_the_compact_legacy_shape(profile: dict[str, Any]) -> None:
    graph = build_graph(profile)
    assert list(graph) == ["person", "goal", "mastery", "domains", "skills", "roles", "sources"]
    assert set(graph["skills"][0]) == {"id", "name", "domain", "score", "evidence", "unlocks"}
    assert all(e.startswith("[") for e in graph["skills"][0]["evidence"])
    requirement = graph["roles"][0]["requirements"][0]
    assert re.fullmatch(r".+: \d+(\.\d+)?/\d+(\.\d+)? \((have|gap)\)", requirement)
    assert "text" not in graph["sources"][0]  # no raw text in the graph, only the summary


def test_the_graph_json_is_exactly_the_legacy_json(profile: dict[str, Any], context: StoredContext) -> None:
    legacy = legacy_text(ported_chat.build_chat_system, profile, context, None)
    graph_line = next(line for line in legacy.split("\n") if line.startswith('{"person":'))
    assert _json(build_graph(profile)) == graph_line


@pytest.mark.parametrize(
    ("person", "expected"),
    [
        ({"name": "Riya Kapoor"}, "Riya"),
        ({"name": "Riya"}, "Riya"),
        ({"name": "  Riya"}, None),  # the legacy split(" ")[0] of a leading space is empty
        ({"name": "You"}, None),
        ({"name": ""}, None),
        ({}, None),
        ({"name": None}, None),
        ({"name": "X" * 200}, "X" * chat_context.MAX_FIRST_NAME_CHARS),
    ],
)
def test_first_name(person: dict[str, Any], expected: str | None) -> None:
    assert first_name(person) == expected


def test_a_missing_name_is_addressed_as_you() -> None:
    assert (
        render_about_user({"name": "You"})
        == '## About the user\nTheir name is not known: address them directly as "you".'
    )
    assert "Their first name is Riya" in render_about_user({"name": "Riya Kapoor"})


def test_an_empty_context_says_so(profile: dict[str, Any]) -> None:
    block = render_profile_block(profile, StoredContext())
    assert block.endswith(
        "## What each reader found\n(no reader details stored)\n\n## Raw source excerpts\n(no raw source text stored)"
    )


def test_excerpts_share_the_character_budget(profile: dict[str, Any]) -> None:
    huge = StoredContext(
        sources=[
            StoredSource(id="resume", label="Resume", kind="resume", content="r" * 200_000),
            StoredSource(id="github", label="GitHub", kind="github", content="g" * 200_000),
        ]
    )
    block = render_profile_block(profile, huge)
    excerpts = block[block.index("## Raw source excerpts") :]
    assert "…[trimmed]" in excerpts
    # the budget plus the element markup and the two trims
    assert limits.CHAT_EXCERPT_BUDGET_CHARS < len(excerpts) < limits.CHAT_EXCERPT_BUDGET_CHARS + 1_000
    small = render_profile_block(profile, huge, budget=1_000)
    assert len(small) < len(block) - 50_000


def test_source_attributes_are_escaped(profile: dict[str, Any]) -> None:
    context = StoredContext(
        sources=[
            StoredSource(
                id="x", label='Ada "the" Dev', kind="link", url='https://a.dev/?q="1"', content="text"
            )
        ]
    )
    block = render_profile_block(profile, context)
    assert (
        '<source id="x" kind="link" label="Ada &quot;the&quot; Dev" url="https://a.dev/?q=&quot;1&quot;">'
        in block
    )


# ───────────────────────── the origin block ─────────────────────────


def test_origin_block_for_a_clicked_node() -> None:
    block = render_origin_block(SKILL_ORIGIN)
    assert block.startswith(
        '## Why they are here\nThe user opened this conversation by clicking "Motion & animation" '
        '(skill, id "motion-design" · Frontend · score 91) on their graph.\n'
    )
    assert block.endswith(ported_chat.ORIGIN_GUIDE["skill"])


def test_origin_block_without_an_origin_or_with_an_unknown_kind_is_the_free_guide() -> None:
    free = f"## Why they are here\n{ported_chat.ORIGIN_GUIDE['free']}"
    assert render_origin_block(None) == free
    assert render_origin_block({}) == free
    assert render_origin_block({"kind": "mystery", "title": "T"}).endswith(ported_chat.ORIGIN_GUIDE["free"])


def test_origin_block_leaves_out_an_empty_ref_and_caption() -> None:
    block = render_origin_block({"kind": "domain", "title": "Frontend", "caption": ""})
    assert "(domain) on their graph" in block
    assert "id " not in block.split("\n")[1]


# ───────────────────────── cache layout and determinism ─────────────────────────


def test_system_blocks_put_the_breakpoint_on_the_profile_block_only(
    profile: dict[str, Any], context: StoredContext
) -> None:
    blocks = ChatPromptRenderer().render(profile, context, SKILL_ORIGIN)
    system = blocks.system_blocks()
    assert system == [
        TextBlock(blocks.rules, cache=False),
        TextBlock(blocks.profile, cache=True),
        TextBlock(blocks.origin, cache=False),
    ]
    assert [b.cache for b in system] == [False, True, False]
    assert blocks.text == "\n\n".join(b.text for b in system)
    assert isinstance(blocks, ChatPromptBlocks)


def test_the_same_input_renders_the_same_strings(profile: dict[str, Any], context: StoredContext) -> None:
    first = ChatPromptRenderer().render(profile, context, SKILL_ORIGIN)
    again = ChatPromptRenderer().render(
        copy.deepcopy(profile), context.model_copy(deep=True), dict(SKILL_ORIGIN)
    )
    assert first == again
    assert first.rules == again.rules
    assert first.profile == again.profile
    assert first.origin == again.origin


def test_every_thread_of_a_profile_shares_the_cached_prefix(
    profile: dict[str, Any], context: StoredContext
) -> None:
    renderer = ChatPromptRenderer()
    a = renderer.render(profile, context, SKILL_ORIGIN)
    b = renderer.render(profile, context, {"kind": "role", "title": "Staff Engineer", "caption": ""})
    c = renderer.render(profile, context, None)
    assert a.rules == b.rules == c.rules
    assert a.profile == b.profile == c.profile
    assert len({a.origin, b.origin, c.origin}) == 3


def test_rendering_does_not_depend_on_the_hash_seed(profile: dict[str, Any], context: StoredContext) -> None:
    """Two interpreters with different PYTHONHASHSEED render byte-identical blocks (no set or hash ordering)."""
    code = (
        "import hashlib, sys\n"
        "from tayyari.chat.context import ChatPromptRenderer\n"
        "from tayyari.sample.provider import SampleProvider\n"
        "s = SampleProvider()\n"
        "b = ChatPromptRenderer().render(s.sample_profile.to_dict(), s.sample_context,\n"
        "    {'kind': 'skill', 'refId': 'x', 'title': 'T', 'caption': 'C'})\n"
        "sys.stdout.write(hashlib.sha256(b.text.encode()).hexdigest())\n"
    )
    digests = set()
    for seed in ("1", "2", "random"):
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", code],
            cwd=BACKEND_DIR,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        digests.add(result.stdout)
    assert len(digests) == 1
    expected = hashlib.sha256(
        ChatPromptRenderer()
        .render(profile, context, {"kind": "skill", "refId": "x", "title": "T", "caption": "C"})
        .text.encode()
    ).hexdigest()
    assert digests == {expected}


def test_no_block_holds_a_timestamp_or_a_random_id(profile: dict[str, Any], context: StoredContext) -> None:
    blocks = ChatPromptRenderer().render(profile, context, SKILL_ORIGIN)
    for text in (blocks.rules, blocks.origin):
        assert not re.search(r"\d{4}-\d{2}-\d{2}", text)
        assert not re.search(r"[0-9a-f]{32}", text)
    assert profile["id"] not in blocks.profile  # the graph carries no profile id or creation time
    assert profile["createdAt"] not in blocks.profile


# ───────────────────────── the LRU ─────────────────────────


def _variant(context: StoredContext, marker: str) -> StoredContext:
    sources = [s.model_copy(update={"content": s.content + marker}) for s in context.sources]
    return StoredContext(sources=sources, readers=context.readers)


def test_the_renderer_memoises_per_profile(profile: dict[str, Any], context: StoredContext) -> None:
    renderer = ChatPromptRenderer()
    first = renderer.render(profile, context, None)
    assert (renderer.hits, renderer.misses, len(renderer)) == (0, 1, 1)
    second = renderer.render(profile, context, SKILL_ORIGIN)
    assert (renderer.hits, renderer.misses, len(renderer)) == (1, 1, 1)
    assert second.profile is first.profile  # the very same string object: no second render
    renderer.render(profile, _variant(context, "!"), None)
    assert (renderer.hits, renderer.misses, len(renderer)) == (1, 2, 2)


def test_the_lru_evicts_the_least_recently_used_profile(
    profile: dict[str, Any], context: StoredContext
) -> None:
    renderer = ChatPromptRenderer(max_entries=2)
    a, b, c = (_variant(context, m) for m in "abc")
    renderer.render(profile, a, None)
    renderer.render(profile, b, None)
    renderer.render(profile, a, None)  # a is now the most recent
    renderer.render(profile, c, None)  # evicts b
    assert len(renderer) == 2
    assert renderer.misses == 3
    renderer.render(profile, a, None)
    assert renderer.hits == 2
    renderer.render(profile, b, None)  # was evicted: a miss again
    assert renderer.misses == 4
    renderer.clear()
    assert len(renderer) == 0


def test_a_new_profile_renders_new_text(profile: dict[str, Any], context: StoredContext) -> None:
    renderer = ChatPromptRenderer()
    first = renderer.render(profile, context, None)
    changed = copy.deepcopy(profile)
    changed["skills"][0]["score"] = 99
    second = renderer.render(changed, context, None)
    assert second.profile != first.profile
    assert renderer.misses == 2


def test_the_renderer_needs_room_for_one_entry() -> None:
    with pytest.raises(ValueError):
        ChatPromptRenderer(max_entries=0)


def test_fingerprint_ignores_key_order_and_sees_content(
    profile: dict[str, Any], context: StoredContext
) -> None:
    key = profile_fingerprint(profile, context)
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    reordered = dict(reversed(list(profile.items())))
    assert profile_fingerprint(reordered, context) == key
    assert profile_fingerprint(profile, _variant(context, "x")) != key
    changed = copy.deepcopy(profile)
    changed["person"]["name"] = "Someone Else"
    assert profile_fingerprint(changed, context) != key
    assert profile_fingerprint(profile, context, budget=1_000) != key


def test_fingerprint_survives_text_a_utf8_encoder_would_reject(
    profile: dict[str, Any], context: StoredContext
) -> None:
    odd = StoredContext(
        sources=[StoredSource(id="x", label="L", kind="link", content="lone \ud800 surrogate")]
    )
    assert len(profile_fingerprint(profile, odd)) == 64


def test_public_names() -> None:
    assert set(chat_context.__all__) >= {
        "CHAT_RULES",
        "ChatPromptRenderer",
        "ChatPromptBlocks",
        "render_origin_block",
    }
