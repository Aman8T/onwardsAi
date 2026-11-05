"""The AI fallback: an unknown site is filled by an agent through the guard (no submit, no Enter), the run becomes a draft
playbook replayed into a normal review, and the next application on that site needs no AI. The Gemini and Claude adapters
speak their APIs' shapes (mocked transports; nothing calls the network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2

from tayyari.apply.agent import SUBMIT_REFUSED, AgentOutcome, AiFallback, Guarded, Recorder
from tayyari.apply.agent.claude import NOT_EXECUTED, ClaudeAgent
from tayyari.apply.agent.gemini import GeminiAgent
from tayyari.apply.answers import FillContext, Resume
from tayyari.apply.browser import ElementInfo
from tayyari.apply.engine import ApplyEngine
from tayyari.apply.worker import ApplyWorker
from tayyari.core.settings import ApplySettings
from tayyari.storage.ports import StoredApplication
from tests.fakes.apply import FakeApplyRepo
from tests.fakes.apply_browser import FakeDriver

PAGE = (Path(__file__).resolve().parents[2] / "data" / "apply" / "unknown_form.html").read_text(
    encoding="utf-8"
)
T0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
OWNER = "a:" + "4" * 40
ELEMENTS = {
    (10, 10): ElementInfo("input", "fn", None, "text", "Your first name", None, '[id="fn"]', True),
    (10, 20): ElementInfo("input", "ln", None, "text", "Your last name", None, '[id="ln"]', True),
    (10, 30): ElementInfo("input", "mail", None, "email", "E-mail address", None, '[id="mail"]', True),
    (10, 40): ElementInfo("input", "cv", None, "file", "Upload CV", None, '[id="cv"]', True),
    (10, 50): ElementInfo("button", None, None, "submit", None, "Send application", None, True),
}


class AgentDriver(FakeDriver):
    def __init__(self, page: str) -> None:
        super().__init__(page)
        self.typed: list[str] = []
        self.pressed: list[str] = []

    async def screenshot_png(self) -> bytes:
        return b"\x89PNG fake"

    def viewport(self) -> tuple[int, int]:
        return 1280, 800

    async def element_at(self, x: int, y: int) -> ElementInfo | None:
        return ELEMENTS.get((x, y))

    async def click_at(self, x: int, y: int) -> None:
        self.actions.append(("click_at", f"{x},{y}", ""))

    async def type_text(self, text: str) -> None:
        self.typed.append(text)

    async def press(self, keys: str) -> None:
        self.pressed.append(keys)

    async def scroll(self, x: int, y: int, dx: int, dy: int) -> None: ...

    async def wait(self, seconds: float) -> None: ...


class Scripted:
    name = "scripted"

    def __init__(self) -> None:
        self.refusals: list[str | None] = []

    async def run(self, task: str, actions: Guarded, max_steps: int) -> AgentOutcome:
        assert "Riya" in task and "Never click a submit" in task
        for (x, y), text in [((10, 10), "Riya"), ((10, 20), "Kapoor"), ((10, 30), "riya@example.com")]:
            await actions.type(text, x, y)
        self.refusals.append(await actions.click(10, 40))  # the file field: the resume is attached
        self.refusals.append(await actions.click(10, 50))  # submit: refused
        self.refusals.append(await actions.key("Return"))  # Enter: refused
        return AgentOutcome("done", 6, cost_micro_usd=1234)


class Opener:
    def __init__(self) -> None:
        self.drivers: list[AgentDriver] = []

    async def new_page(self) -> AgentDriver:
        self.drivers.append(AgentDriver(PAGE))
        return self.drivers[-1]


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        self.t += timedelta(seconds=30)
        return self.t

    def monotonic(self) -> float:
        return 0.0


def app(app_id: str) -> StoredApplication:
    return StoredApplication(id=app_id, owner_key=OWNER, source="link", job_url="https://careers.beta.example/analyst",
                             domain="careers.beta.example", ats="unknown", stage="queued", created_at=T0, updated_at=T0)  # fmt: skip


async def test_an_unknown_site_is_filled_by_the_agent_then_by_its_draft_playbook() -> None:
    repo = FakeApplyRepo()
    await repo.put_details(
        OWNER,
        user_id=None,
        details={"firstName": "Riya", "lastName": "Kapoor", "email": "riya@example.com"},
        now=T0,
    )
    await repo.put_resume(
        OWNER, user_id=None, name="cv.pdf", media_type="application/pdf", data=b"%PDF", now=T0
    )
    await repo.create_application(app("ap1"))
    agent = Scripted()
    opener = Opener()
    engine = ApplyEngine(
        repo, ApplySettings(domain_gap_seconds=0), opener, Clock(), fallback=AiFallback([agent], 10)
    )
    await ApplyWorker(engine).run_once()
    first = await repo.get_application("ap1", OWNER)
    assert first is not None and first.stage == "ready_to_submit", first.stage_reason
    assert first.review is not None and first.review["mode"] == "ai" and first.review["agent"] == "scripted"
    assert (
        agent.refusals[1] == SUBMIT_REFUSED
        and agent.refusals[2] is not None
        and not opener.drivers[0].submitted
    )
    assert opener.drivers[0].pressed == ["Control+A"] * 3  # never Enter
    (draft,) = repo.playbooks.values()
    assert draft.status == "draft" and draft.domain == "careers.beta.example"
    assert {r["key"]: r.get("id") for r in draft.spec["rules"]} == {
        "firstName": "fn",
        "lastName": "ln",
        "email": "mail",
        "resume": "cv",
    }
    assert (repo.metrics[0].mode, repo.metrics[0].cost_micro_usd, repo.metrics[0].outcome) == (
        "ai",
        1234,
        "success",
    )

    await repo.create_application(app("ap2"))
    await ApplyWorker(engine).run_once()
    second = await repo.get_application("ap2", OWNER)
    assert second is not None and second.stage == "ready_to_submit" and second.review is not None
    assert second.review["mode"] == "playbook" and repo.metrics[-1].mode == "playbook"  # the AI share falls


def test_the_recorder_needs_a_matching_value() -> None:
    rec = Recorder()
    assert rec.draft("unknown", "x.example", {"firstName": "Riya"}) is None


async def test_gemini_speaks_computer_use_and_never_presses_enter() -> None:
    sent: list[dict[str, Any]] = []
    replies = [
        {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "type_text_at", "args": {"x": 8, "y": 13, "text": "Riya", "press_enter": True}}}]}}],
         "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 10}},
        {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "report_done", "args": {}}}]}}]},
    ]  # fmt: skip

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        assert request.headers["x-goog-api-key"] == "k"
        return httpx2.Response(200, json=replies[len(sent) - 1])

    driver = AgentDriver(PAGE)
    ctx = FillContext(details={"firstName": "Riya"})
    agent = GeminiAgent("k", "gemini-cu", "https://example.invalid/v1beta", httpx2.MockTransport(handler))
    outcome = await agent.run("task", Guarded(driver, Recorder(), "https://careers.beta.example/a", ctx), 5)
    assert (
        outcome.status == "done" and outcome.steps == 2 and outcome.cost_micro_usd == 1000 * 1.25 + 10 * 10.0
    )
    assert sent[0]["tools"][0]["computer_use"]["environment"] == "ENVIRONMENT_BROWSER"
    reply = sent[1]["contents"][-1]["parts"][0]["functionResponse"]
    assert (
        reply["name"] == "type_text_at"
        and "url" in reply["response"]
        and reply["parts"][0]["inline_data"]["mime_type"] == "image/png"
    )
    assert driver.typed == ["Riya"] and "Return" not in driver.pressed and "Enter" not in driver.pressed


async def test_claude_echoes_the_toolset_and_follows_the_batch_rule() -> None:
    calls: list[dict[str, Any]] = []
    turns = [
        [{"type": "tool_use", "id": "t1", "name": "left_click", "toolset_name": "computer", "input": {"coordinate": [10, 50]}},
         {"type": "tool_use", "id": "t2", "name": "type", "toolset_name": "computer", "input": {"text": "x"}}],
        [{"type": "tool_use", "id": "t3", "name": "report_done", "input": {}}],
    ]  # fmt: skip

    class Messages:
        async def create(self, **kw: Any) -> Any:
            calls.append(kw)
            return SimpleNamespace(
                content=turns[len(calls) - 1], usage=SimpleNamespace(input_tokens=100, output_tokens=10)
            )

    driver = AgentDriver(PAGE)
    agent = ClaudeAgent(SimpleNamespace(messages=Messages()), "claude-sonnet-5-5")
    ctx = FillContext(details={}, resume=Resume("cv.pdf", b"%PDF"))
    outcome = await agent.run("task", Guarded(driver, Recorder(), "https://careers.beta.example/a", ctx), 5)
    assert outcome.status == "done" and calls[0]["tools"][0]["type"] == "computer_toolset_20260801"
    results = [m for m in calls[1]["messages"] if m["role"] == "user"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and all(
        r["toolset_name"] == "computer" for r in results
    )
    assert results[0]["is_error"] and SUBMIT_REFUSED in results[0]["content"]  # the submit click was refused
    assert results[1]["content"] == NOT_EXECUTED and driver.typed == []
