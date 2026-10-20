"""tayyari.contract.weekly must match shared/weekly.ts exactly: the interfaces and the literal unions."""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel

from tayyari.contract import weekly as wk
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

WEEKLY_TS = REPO_ROOT / "shared" / "weekly.ts"

if not WEEKLY_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(WEEKLY_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "WeeklySubscription": wk.WeeklySubscription,
    "WeeklySubscriptionUpdate": wk.WeeklySubscriptionUpdate,
    "WeeklyCite": wk.WeeklyCite,
    "WeeklyMet": wk.WeeklyMet,
    "WeeklyJob": wk.WeeklyJob,
    "WeeklyProgress": wk.WeeklyProgress,
    "WeeklyNews": wk.WeeklyNews,
    "WeeklyEdition": wk.WeeklyEdition,
    "WeeklyEditionSummary": wk.WeeklyEditionSummary,
    "WeeklyEditionList": wk.WeeklyEditionList,
}

UNIONS = {
    "WeeklyDay": wk.WeeklyDay,
    "WeeklySection": wk.WeeklySection,
    "WeeklyStatus": wk.WeeklyStatus,
    "WeeklyFit": wk.WeeklyFit,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


@pytest.mark.parametrize("name", sorted(UNIONS))
def test_literal_union_matches(name: str) -> None:
    assert _ts().shape(name) == ("lit", frozenset(get_args(UNIONS[name])))


def test_an_absent_optional_stays_absent_on_output() -> None:
    sub = wk.WeeklySubscription(status="off", day="mon", sections=["jobs"], unread=0)
    assert sub.to_dict() == {"status": "off", "day": "mon", "sections": ["jobs"], "unread": 0}
