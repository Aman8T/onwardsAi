"""The coach's one list of buttons: the Python mirror equals the list `scripts/capabilities-parity.ts` dumped from
`shared/capabilities.ts`, every route is one the app serves, and the prompt names every destination."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, get_args

import pytest

from tayyari.api.static import _app_route
from tayyari.chat.context import CHAT_RULES
from tayyari.contract.capabilities import (
    BY_DESTINATION,
    CAN_SEE,
    CANNOT,
    CAPABILITIES,
    Destination,
    TargetKind,
)
from tayyari.core.paths import REPO_ROOT
from tayyari.prompts.chat import render_capabilities_section

FIXTURE = REPO_ROOT / "backend" / "tests" / "data" / "capabilities.json"
FIELDS = ["destination", "label", "target", "can", "route"]


def _stored() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def test_the_python_list_equals_the_typescript_list_field_for_field() -> None:
    stored = _stored()["capabilities"]
    assert [list(c) for c in stored] == [FIELDS] * len(stored), "the fixture's fields or their order changed"
    assert [asdict(c) for c in CAPABILITIES] == stored


def test_what_the_coach_can_see_and_cannot_do_equal_the_typescript_lists() -> None:
    assert list(CAN_SEE) == _stored()["canSee"]
    assert list(CANNOT) == _stored()["cannot"]


def test_every_destination_is_listed_once_and_the_type_names_exactly_those() -> None:
    names = [c.destination for c in CAPABILITIES]
    assert len(names) == len(set(names))
    assert set(names) == set(get_args(Destination))
    assert {c.target for c in CAPABILITIES} <= set(get_args(TargetKind))
    assert set(BY_DESTINATION) == set(names)


def test_every_route_is_one_the_app_serves() -> None:
    for cap in CAPABILITIES:
        if cap.route is not None:
            assert _app_route(cap.route.lstrip("/"), dev=False) is not None, (
                f"{cap.destination}: {cap.route} is not an app route"
            )


def test_no_label_or_description_says_the_button_does_the_thing() -> None:
    for cap in CAPABILITIES:
        text = f"{cap.label} {cap.can}".lower()
        assert not any(word in text.split() for word in ("done", "saved", "started", "submits", "sends")), cap


@pytest.mark.parametrize("cap", CAPABILITIES, ids=lambda c: c.destination)
def test_every_destination_is_in_the_rendered_prompt_section(cap: Any) -> None:
    section = render_capabilities_section()
    assert cap.destination in section
    assert f'Button: "{cap.label}"' in section
    assert cap.destination in CHAT_RULES


def test_the_section_is_byte_stable_and_lists_what_it_cannot_do() -> None:
    assert render_capabilities_section() == render_capabilities_section()
    section = render_capabilities_section()
    for line in (*CAN_SEE, *CANNOT):
        assert line in section
