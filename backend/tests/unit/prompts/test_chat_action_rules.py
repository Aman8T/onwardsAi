"""The coach's prompt says how to hand over a button and what never to claim, from the one capability list."""

from __future__ import annotations

import re

from tayyari.chat.context import CHAT_RULES, ChatPromptRenderer
from tayyari.contract.capabilities import CAPABILITIES
from tayyari.prompts.chat import ACTION_RULES
from tayyari.sample.provider import SampleProvider


def test_the_rules_say_how_to_write_the_line_and_that_there_is_at_most_one() -> None:
    assert "[[action: <destination> | <target id>]]" in ACTION_RULES
    assert "[[action: <destination>]]" in ACTION_RULES  # no target for a destination that takes none
    assert "At most one" in ACTION_RULES
    assert "one final line" in ACTION_RULES


def test_the_rules_forbid_saying_an_action_is_done_sent_saved_or_started() -> None:
    flat = " ".join(ACTION_RULES.split())
    assert "never say that anything is done, sent, saved or started" in flat
    assert "the button below opens it" in flat


def test_the_rules_say_no_first_then_name_the_nearest_real_thing() -> None:
    flat = " ".join(ACTION_RULES.split())
    assert "say no first" in flat and "nearest real thing" in flat


def test_every_destination_and_its_target_kind_is_in_the_cached_rules_block() -> None:
    for cap in CAPABILITIES:
        assert re.search(rf"^- {cap.destination}\b", CHAT_RULES, flags=re.MULTILINE), cap.destination
        if cap.target != "none":
            line = next(ln for ln in CHAT_RULES.splitlines() if ln.startswith(f"- {cap.destination} ("))
            assert "target:" in line


def test_the_rules_are_the_first_block_cached_and_identical_for_everyone() -> None:
    sample = SampleProvider()
    assert sample.sample_profile is not None
    renderer = ChatPromptRenderer()
    one = renderer.render(sample.sample_profile.to_dict(), sample.sample_context, None)
    other = renderer.render(
        sample.sample_profile.to_dict(), sample.sample_context, {"kind": "free", "title": "x"}
    )
    assert one.rules == other.rules == CHAT_RULES
    assert "Riya" not in CHAT_RULES
