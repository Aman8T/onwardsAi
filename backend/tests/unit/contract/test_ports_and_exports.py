from __future__ import annotations

import ast
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import tayyari.contract as contract
from tayyari.analysis.ports import AnalysisService
from tayyari.chat.ports import ChatService
from tayyari.contract import (
    AnalysisEvent,
    AnalyzeRequest,
    ChatEvent,
    ChatRequestBody,
    DoneEvent,
    ErrorEvent,
    RequestContext,
    ev_chat_done,
    ev_delta,
    ev_error,
    ev_log,
    events,
    models,
    records,
    requests,
    stored,
    wire,
)

RC = RequestContext("req-1", None, None, datetime(2026, 10, 7, tzinfo=UTC))
PKG_DIR = Path(contract.__file__).resolve().parent.parent


class _FakeAnalysis:
    def run(self, req: AnalyzeRequest, rc: RequestContext) -> AsyncIterator[AnalysisEvent]:
        return self._stream(req)

    async def _stream(self, req: AnalyzeRequest) -> AsyncIterator[AnalysisEvent]:
        yield ev_log("Tayyari", f"sample={req.sample}")
        yield ev_error("done early")


class _FakeChat:
    async def stream_turn(self, req: ChatRequestBody, rc: RequestContext) -> AsyncIterator[ChatEvent]:
        yield ev_delta(req.message)
        yield ev_chat_done("sess-1")


class _NotAService:
    pass


async def test_a_fake_satisfies_the_analysis_port() -> None:
    service = _FakeAnalysis()
    assert isinstance(service, AnalysisService)
    out = [ev async for ev in service.run(AnalyzeRequest(sample=True), RC)]
    assert [e.type for e in out] == ["log", "error"]
    assert isinstance(out[-1], ErrorEvent)


async def test_an_async_generator_satisfies_the_chat_port() -> None:
    service = _FakeChat()
    assert isinstance(service, ChatService)
    body = ChatRequestBody.model_validate({"profileId": "p", "message": "hi"})
    out = [ev async for ev in service.stream_turn(body, RC)]
    assert [e.type for e in out] == ["delta", "done"]


def test_other_objects_are_not_services() -> None:
    assert not isinstance(_NotAService(), AnalysisService)
    assert not isinstance(_NotAService(), ChatService)
    assert not isinstance(_FakeChat(), AnalysisService)
    assert not isinstance(_FakeAnalysis(), ChatService)


def test_done_event_type_is_exported() -> None:
    assert DoneEvent.model_fields["type"].default == "done"


@pytest.mark.parametrize("module", [models, events, requests, wire, records, stored])
def test_every_public_name_of_a_submodule_is_re_exported(module: object) -> None:
    public = [
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == module.__name__  # type: ignore[attr-defined]
        and (isinstance(value, type) or callable(value))
    ]
    missing = [name for name in public if name not in contract.__all__]
    assert not missing, f"{module.__name__} defines public names missing from tayyari.contract: {missing}"


def test_all_is_complete_unique_and_resolvable() -> None:
    assert len(set(contract.__all__)) == len(contract.__all__)
    for name in contract.__all__:
        assert hasattr(contract, name), name
    for module in (models, events, requests, wire):
        for name in module.__all__:
            assert name in contract.__all__, name
            assert getattr(contract, name) is getattr(module, name)
    for name in (
        "LlmCallRecord",
        "JevCallRecord",
        "DecisionRecord",
        "StateBlob",
        "StoredContext",
        "StoredSource",
    ):
        assert name in contract.__all__


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_contract_imports_only_core_and_itself() -> None:
    for path in sorted((PKG_DIR / "contract").glob("*.py")):
        for module in _imported_modules(path):
            if module.split(".")[0] == "tayyari":
                assert module.split(".")[1] in {"core", "contract"}, f"{path.name} imports {module}"


def test_the_ports_import_only_contract() -> None:
    for area in ("analysis", "chat"):
        for module in _imported_modules(PKG_DIR / area / "ports.py"):
            if module.split(".")[0] == "tayyari":
                assert module.split(".")[1] == "contract", f"{area}/ports.py imports {module}"
