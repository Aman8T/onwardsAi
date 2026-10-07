from __future__ import annotations

import asyncio
import uuid

import pytest

from tayyari.core import context
from tayyari.core.context import bind, current


def test_nothing_is_bound_by_default() -> None:
    assert current() == {}


def test_bind_sets_and_restores() -> None:
    with bind(request_id="r1", ip_hash="abc"):
        assert current() == {"request_id": "r1", "ip_hash": "abc"}
        assert context.request_id.get() == "r1"
    assert current() == {}
    assert context.request_id.get() is None


def test_nested_binds_stack_and_unwind() -> None:
    with bind(request_id="r1", profile_id="p1"):
        with bind(profile_id="p2", session_id="s1"):
            assert current() == {"request_id": "r1", "profile_id": "p2", "session_id": "s1"}
        assert current() == {"request_id": "r1", "profile_id": "p1"}
    assert current() == {}


def test_binding_none_clears_a_value_inside_the_block() -> None:
    with bind(request_id="r1"):
        with bind(request_id=None):
            assert current() == {}
        assert current() == {"request_id": "r1"}


def test_current_lists_variables_in_the_documented_order() -> None:
    with bind(ip_hash="i", session_id="s", profile_id="p", analysis_id="a", request_id="r"):
        assert list(current()) == ["request_id", "analysis_id", "profile_id", "session_id", "ip_hash"]
    assert context.KEYS == ("request_id", "analysis_id", "profile_id", "session_id", "ip_hash")


def test_uuid_values_are_stored_as_strings() -> None:
    aid = uuid.uuid4()
    with bind(analysis_id=aid):
        assert current() == {"analysis_id": str(aid)}


def test_unknown_names_raise_and_bind_nothing() -> None:
    with pytest.raises(TypeError, match="colour"):
        with bind(request_id="r1", colour="red"):  # type: ignore[arg-type]
            pass
    assert current() == {}


def test_an_exception_inside_the_block_still_restores() -> None:
    with pytest.raises(RuntimeError):
        with bind(request_id="r1"):
            raise RuntimeError("boom")
    assert current() == {}


async def test_concurrent_tasks_do_not_see_each_others_bindings() -> None:
    seen: dict[str, dict[str, str]] = {}

    async def worker(name: str, delay: float) -> None:
        with bind(request_id=name):
            await asyncio.sleep(delay)
            seen[name] = current()

    await asyncio.gather(worker("a", 0.03), worker("b", 0.01), worker("c", 0.02))
    assert seen == {"a": {"request_id": "a"}, "b": {"request_id": "b"}, "c": {"request_id": "c"}}
    assert current() == {}


async def test_child_tasks_inherit_the_binding() -> None:
    async def child() -> dict[str, str]:
        return current()

    with bind(profile_id="p9"):
        assert await asyncio.create_task(child()) == {"profile_id": "p9"}
