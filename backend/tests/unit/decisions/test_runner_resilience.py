"""The runner's safety nets: logging failures, internal bugs and slow audits must never reach the caller."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import pytest
from pydantic import BaseModel

from tayyari.contract.records import DecisionRecord
from tayyari.core.ids import uuid7
from tayyari.decisions import recorder as recorder_module
from tayyari.decisions.runner import DecisionRunner, _jsonable
from tayyari.decisions.spec import DecisionSpec
from tests.fakes.jev import noul_answer
from tests.unit.decisions.support import CTX, ScriptedRandom, build_harness, make_pack, noul_spec

ABOVE = noul_answer(0.95)


async def test_a_recorder_that_raises_never_reaches_the_caller() -> None:
    h = build_harness({"t.flag": ABOVE}, with_claude=False)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("disk full")

    h.recorder.add_decision = broken  # type: ignore[method-assign]
    h.recorder.add_call = broken  # type: ignore[method-assign]
    result = await h.runner.run(make_pack(noul_spec()), "x", ctx=CTX)
    assert result["t.flag"].used and result["t.flag"].source == "jev"


async def test_a_state_the_recorder_cannot_store_does_not_break_the_decision() -> None:
    h = build_harness({"t.flag": ABOVE}, with_claude=False)
    pack = make_pack(noul_spec())
    result = await h.runner.run(
        type(pack)(id=pack.id, specs=pack.specs, build_state=lambda s: {"n": float("nan")}), "x", ctx=CTX
    )
    assert result["t.flag"].used  # the answer is used; only the log row for the call was lost
    await h.drain()


async def test_an_internal_error_in_a_chunk_gives_neutral_results() -> None:
    h = build_harness({"t.flag": ABOVE})

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug in the runner")

    h.runner._call_jev = boom  # type: ignore[method-assign]
    result = await h.runner.run(make_pack(noul_spec(neutral=True)), "x", ctx=CTX)
    assert (
        result["t.flag"].used is False
        and result["t.flag"].value is True
        and result["t.flag"].source == "default"
    )


async def test_an_internal_error_in_one_decision_gives_a_neutral_result_for_it() -> None:
    h = build_harness({"t.flag": ABOVE, "t.other": ABOVE})
    original = h.runner._decide_inner

    async def flaky(pack: Any, item: Any, *args: Any, **kwargs: Any) -> Any:
        if item.key == "t.flag":
            raise RuntimeError("bug")
        return await original(pack, item, *args, **kwargs)

    h.runner._decide_inner = flaky  # type: ignore[method-assign]
    result = await h.runner.run(make_pack(noul_spec(), noul_spec("t.other")), "x", ctx=CTX)
    assert result["t.flag"].used is False and result["t.other"].used is True


async def test_an_error_while_planning_returns_an_empty_result() -> None:
    h = build_harness({"t.flag": ABOVE})

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bad plan")

    h.runner._plan = boom  # type: ignore[method-assign]
    result = await h.runner.run(make_pack(noul_spec()), "x", ctx=CTX)
    assert len(result) == 0 and result.pack_id == "t"


async def test_a_slow_audit_is_cut_off_and_the_row_is_still_written() -> None:
    h = build_harness({"t.flag": ABOVE}, rng=ScriptedRandom([0.0]))
    assert h.claude is not None

    async def never(call: Any) -> Any:
        await asyncio.sleep(30)

    h.claude.script_structured("decision_fallback", never)
    h.runner._audit_timeout_s = 0.05
    await h.runner.run(make_pack(noul_spec(audit_rate=1.0)), "x", ctx=CTX)
    await h.runner.drain(3.0)
    [row] = h.rows
    assert row.extra["claude_error"] == "timeout" and row.extra["audit"] is True and row.claude_answer is None


async def test_spawn_accepts_any_coroutine() -> None:
    h = build_harness({})
    task = h.runner.spawn(asyncio.sleep(0, result=1))
    await h.runner.drain(1.0)
    assert task.result() == 1


def test_the_runner_validates_its_chunk_size() -> None:
    h = build_harness({})
    with pytest.raises(ValueError):
        DecisionRunner(
            h.jev, None, h.recorder, h.config, h.breaker, None, h.clock, "m", max_questions_per_call=0
        )


async def test_the_recorder_forgets_the_oldest_stored_states_beyond_its_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recorder_module, "_STATE_CACHE_SIZE", 2)
    h = build_harness({})
    for i in range(4):
        h.recorder.add_state({"i": i})
        h.recorder.add_decision(_row())
        await h.recorder.flush()
    assert len(h.recorder._stored) == 2


def _row() -> DecisionRecord:
    return DecisionRecord(
        id=uuid7(),
        created_at=datetime(2026, 10, 7, tzinfo=UTC),
        spec_id="s",
        spec_version="1",
        pack="p",
        mode="primary",
        used=True,
        source_used="jev",
    )


# ───────────────────────── the log's JSON form of a value ─────────────────────────


class Colour(Enum):
    RED = "red"


@dataclasses.dataclass
class Verdict:
    label: str
    score: float


class Model(BaseModel):
    kept: bool


def test_jsonable_handles_the_values_an_interpret_function_may_return() -> None:
    assert _jsonable(None) is None and _jsonable(True) is True and _jsonable(3) == 3 and _jsonable("x") == "x"
    assert _jsonable(1.5) == 1.5 and _jsonable(float("nan")) is None and _jsonable(float("inf")) is None
    assert _jsonable(Colour.RED) == "red"
    assert _jsonable(Verdict("a", 0.5)) == {"label": "a", "score": 0.5}
    assert _jsonable(Model(kept=True)) == {"kept": True}
    assert _jsonable({"a": (1, 2), 3: {4}}) == {"a": [1, 2], "3": [4]}
    assert _jsonable(object()).startswith("<object object")
    deep: Any = "end"
    for _ in range(10):
        deep = [deep]
    assert json.dumps(_jsonable(deep))  # depth-limited, still serialisable


async def test_a_structured_value_is_logged_in_its_json_form() -> None:
    base = noul_spec()
    spec: DecisionSpec[Verdict] = DecisionSpec(
        id=base.id,
        version="1",
        pack="t",
        question=base.question,
        interpret=lambda answer: Verdict("yes" if (answer.noul or 0) > 0.5 else "no", answer.confidence),
        neutral=Verdict("none", 0.0),
        default=base.default,
    )
    h = build_harness({"t.flag": ABOVE}, with_claude=False)
    result = await h.runner.run(make_pack(spec), "x", ctx=CTX)
    await h.drain()
    assert result["t.flag"].value == Verdict("yes", pytest.approx(0.9))  # type: ignore[arg-type]
    assert h.rows[0].extra["value"] == {"label": "yes", "score": pytest.approx(0.9)}
