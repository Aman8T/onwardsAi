"""tayyari.contract.handoff must match shared/handoff.ts exactly: the interfaces, the error-code union and the limits."""

from __future__ import annotations

import re
from typing import get_args

import pytest
from pydantic import BaseModel

from tayyari.contract import handoff as ho
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

HANDOFF_TS = REPO_ROOT / "shared" / "handoff.ts"

if not HANDOFF_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(HANDOFF_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "HandoffResume": ho.HandoffResume,
    "HandoffPayload": ho.HandoffPayload,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


def test_the_error_codes_match() -> None:
    assert _ts().shape("HandoffErrorCode") == ("lit", frozenset(get_args(ho.HandoffErrorCode)))


@pytest.mark.parametrize(
    "name",
    ["HANDOFF_LINKS_MAX", "HANDOFF_LINK_MAX_CHARS", "HANDOFF_GOAL_MAX_CHARS", "HANDOFF_NOTES_MAX_CHARS"],
)
def test_the_limits_match(name: str) -> None:
    found = re.search(rf"export const {name} = (\d+);", HANDOFF_TS.read_text(encoding="utf-8"))
    assert found is not None
    assert int(found.group(1)) == getattr(ho, name)


def test_an_absent_optional_stays_absent_on_output() -> None:
    assert ho.HandoffPayload(links=["https://github.com/you"]).to_dict() == {
        "links": ["https://github.com/you"]
    }
