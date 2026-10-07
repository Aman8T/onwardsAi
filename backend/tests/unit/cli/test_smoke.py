"""``smoke`` end to end against injected fakes (`FakeClaudeClient`, `FakeJev`): no network, no real key."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from tayyari.cli import smoke
from tayyari.contract.records import LlmCallRecord
from tayyari.core.settings import Settings
from tayyari.llm.anthropic_client import AnthropicClaudeClient
from tayyari.llm.errors import ClaudeAuthError, ClaudeRefusal, JevError
from tayyari.llm.jev_http import HttpJevClient
from tayyari.llm.models import jev_cost_micro
from tayyari.llm.types import ChatStreamEvent, StructuredResult, TurnEnd, Usage
from tayyari.llm.usage import MemoryUsageSink, UsageSink
from tests.fakes.claude import FakeClaudeClient, fake_usage, text_turn
from tests.fakes.jev import FakeJev, noul_answer

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5-5"


def row(purpose: str, usage: Usage, status: str = "ok") -> LlmCallRecord:
    return LlmCallRecord(
        id=uuid4(),
        created_at=datetime.now(UTC),
        purpose=purpose,
        model=usage.model,
        status=status,  # type: ignore[arg-type]
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        web_searches=usage.web_searches,
        cost_micro_usd=usage.cost_micro_usd,
        latency_ms=21,
    )


class LedgerFake(FakeClaudeClient):
    """The scripted fake, reporting every request to the usage sink the way the real client does."""

    def __init__(self, sink: UsageSink, *, extra_structured_rows: int = 0) -> None:
        super().__init__()
        self.sink = sink
        self.extra_structured_rows = extra_structured_rows
        self.closed = False

    async def structured(self, **kwargs: Any) -> StructuredResult:
        for _ in range(self.extra_structured_rows):  # a request that was rejected and retried
            await self.sink.record(row("smoke", fake_usage(kwargs["model"]), status="error"))
        result = await super().structured(**kwargs)
        await self.sink.record(row(kwargs["ctx"].purpose, result.usage))
        return result

    def stream_chat(self, **kwargs: Any) -> AsyncIterator[ChatStreamEvent]:
        return self._ledger(super().stream_chat(**kwargs), kwargs["ctx"].purpose)

    async def _ledger(
        self, events: AsyncIterator[ChatStreamEvent], purpose: str
    ) -> AsyncIterator[ChatStreamEvent]:
        async for event in events:
            if isinstance(event, TurnEnd):
                await self.sink.record(row(purpose, event.usage))
            yield event

    async def aclose(self) -> None:
        self.closed = True


TURN_ONE = fake_usage(SONNET, input_tokens=31, output_tokens=12, cache_write_tokens=2_541)
TURN_TWO = fake_usage(
    SONNET, input_tokens=64, output_tokens=9, cache_write_tokens=88, cache_read_tokens=2_541
)
JEV_INPUT_TOKENS = 1_000_000  # priced at $0.042 per million: a cost that is easy to see in the total
SKILLS = {"skills": [{"name": "React", "confidence": 0.95}, {"name": "TypeScript", "confidence": 0.9}]}


def scripted_claude(sink: UsageSink, **kwargs: Any) -> LedgerFake:
    fake = LedgerFake(sink, **kwargs)
    fake.script_structured("smoke", SKILLS)
    fake.script_chat(
        text_turn("Learn to evaluate model output.", usage=TURN_ONE),
        text_turn("Evaluate model output.", usage=TURN_TWO),
    )
    return fake


class Rig:
    """One smoke run against fakes; keeps the fakes so a test can look at what they were asked."""

    def __init__(self, settings: Settings, **claude_kwargs: Any) -> None:
        self.settings = settings
        self.claude_kwargs = claude_kwargs
        self.claude: LedgerFake | None = None
        self.jev = FakeJev({smoke.JEV_QUESTION_ID: noul_answer(0.97)}, input_tokens=JEV_INPUT_TOKENS)
        self.jev_factory_calls = 0

    def claude_factory(self, settings: Settings, sink: UsageSink) -> LedgerFake:
        assert settings is self.settings
        self.claude = scripted_claude(sink, **self.claude_kwargs)
        return self.claude

    def jev_factory(self, settings: Settings) -> FakeJev:
        self.jev_factory_calls += 1
        return self.jev

    async def run(self, **options: Any) -> int:
        return await smoke.run_smoke(
            self.settings, claude_factory=self.claude_factory, jev_factory=self.jev_factory, **options
        )


@pytest.fixture
def rig(settings: Settings) -> Rig:
    return Rig(settings)


# ───────────────────────── all green ─────────────────────────


async def test_every_check_passes(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    assert await rig.run() == 0
    out = capsys.readouterr().out
    for name in ("haiku structured", "sonnet chat + cache", "jev noul", "docx resume parse"):
        assert f"[PASS] {name}" in out
    assert "FAIL" not in out.replace("0 FAIL", "")
    assert "Result: 4 PASS, 0 FAIL, 0 SKIP" in out


async def test_the_report_has_per_call_tokens_and_a_total_cost(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    await rig.run()
    out = capsys.readouterr().out
    assert "turn 1 write=2,541 read=0" in out
    assert "turn 2 write=88 read=2,541" in out
    assert "attempts=1" in out
    assert "React, TypeScript" in out
    assert "c.read" in out and "c.write" in out  # the ledger table
    assert f"{TURN_TWO.cache_read_tokens:>8,}" in out

    sink = rig.claude.sink  # type: ignore[union-attr]
    assert isinstance(sink, MemoryUsageSink)
    claude_micro = sink.total_cost_micro_usd
    jev_micro = jev_cost_micro(rig.settings.jev.model, JEV_INPUT_TOKENS)
    assert claude_micro > 0 and jev_micro > 0
    assert f"Claude: 3 request(s), {smoke.usd(claude_micro)}" in out
    assert f"Jev:    1 call(s), {smoke.usd(jev_micro)}" in out
    assert f"Total:  {smoke.usd(claude_micro + jev_micro)}" in out


async def test_the_haiku_call_is_made_the_way_the_readers_make_it(rig: Rig) -> None:
    await rig.run()
    (call,) = rig.claude.structured_calls  # type: ignore[union-attr]
    assert call.model == rig.settings.reader_model == HAIKU
    assert call.ctx.purpose == "smoke"
    assert call.schema == smoke.SKILLS_SCHEMA
    assert call.pdf is None
    # asked for effort on purpose: the real client must drop it for Haiku (that is what the check proves)
    assert call.effort == "low"
    assert "React" in call.user_text


async def test_the_chat_turns_share_one_cached_system_block(rig: Rig) -> None:
    await rig.run()
    first, second = rig.claude.chat_calls  # type: ignore[union-attr]
    cached = [b for b in first.system if b.cache]
    assert len(cached) == 1
    assert first.system[0].cache is True
    assert first.system[0].text == second.system[0].text == smoke.handbook_text()
    assert first.system == second.system
    assert 8_000 < len(first.system[0].text) < 11_000  # roughly 2,500 tokens
    assert first.model == second.model == rig.settings.chat_model == SONNET
    assert first.tools == second.tools == "none"
    assert first.cache_tail is True and second.cache_tail is True
    assert first.ctx.purpose == second.ctx.purpose == "smoke"
    # the second turn continues the conversation
    assert [m["role"] for m in second.messages] == ["user", "assistant", "user"]
    assert second.messages[0] == first.messages[0]
    assert second.messages[1]["content"] == "Learn to evaluate model output."
    assert second.messages[2]["content"] == smoke.CHAT_TURNS[1]


async def test_the_jev_question_is_a_noul_with_a_ten_second_budget(rig: Rig) -> None:
    await rig.run()
    (call,) = rig.jev.calls
    assert call.question_ids == [smoke.JEV_QUESTION_ID]
    assert call.questions[smoke.JEV_QUESTION_ID].kind == "noul"
    assert call.timeout_s == smoke.JEV_TIMEOUT_S
    assert call.ctx.purpose == "smoke"
    assert "React" in str(call.state)


async def test_clients_are_closed(rig: Rig) -> None:
    await rig.run()
    assert rig.claude.closed is True  # type: ignore[union-attr]
    assert rig.jev.closed is True


def test_the_handbook_is_deterministic_and_carries_no_name_or_date() -> None:
    text = smoke.handbook_text()
    assert text == smoke.handbook_text()
    assert "2026" not in text and "2025" not in text
    assert text.startswith("Coaching handbook.")


# ───────────────────────── skips ─────────────────────────


async def test_skip_claude_never_calls_claude(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    assert await rig.run(skip_claude=True) == 0
    out = capsys.readouterr().out
    assert rig.claude is None  # not even built
    assert "[SKIP] haiku structured - --skip-claude" in out
    assert "[SKIP] sonnet chat + cache - --skip-claude" in out
    assert "[PASS] jev noul" in out
    assert "Result: 2 PASS, 0 FAIL, 2 SKIP" in out
    assert "(no Claude request was recorded)" in out


async def test_skip_jev_never_builds_jev(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    assert await rig.run(skip_jev=True) == 0
    assert rig.jev_factory_calls == 0
    assert rig.jev.calls == []
    assert "[SKIP] jev noul - --skip-jev" in capsys.readouterr().out


async def test_without_a_jev_key_the_jev_check_is_skipped(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    sink_rig = Rig(settings)
    exit_code = await smoke.run_smoke(
        settings, claude_factory=sink_rig.claude_factory, jev_factory=lambda _settings: None
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[SKIP] jev noul - Jev is not configured" in out
    assert "FAIL" not in out.replace("0 FAIL", "")


async def test_the_default_jev_factory_skips_when_unconfigured(
    make_settings: Callable[..., Settings],
) -> None:
    assert smoke.default_jev(make_settings()) is None
    assert smoke.default_jev(make_settings(jev_api_key="k", jev={"enabled": False})) is None
    client = smoke.default_jev(make_settings(jev_api_key="k"))
    assert isinstance(client, HttpJevClient)
    assert client.enabled
    await client.aclose()


async def test_the_default_claude_factory_builds_the_real_unguarded_client(settings: Settings) -> None:
    client = smoke.default_claude(settings, MemoryUsageSink())
    assert isinstance(client, AnthropicClaudeClient)  # nothing is sent until the first call
    await client.aclose()


# ───────────────────────── failures ─────────────────────────


async def test_no_cache_read_on_turn_two_is_a_failure(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    sink_rig = Rig(settings)

    def factory(s: Settings, sink: UsageSink) -> LedgerFake:
        fake = LedgerFake(sink)
        fake.script_structured("smoke", SKILLS)
        fake.script_chat(
            text_turn("One.", usage=TURN_ONE),
            text_turn("Two.", usage=fake_usage(SONNET, input_tokens=2_600, output_tokens=5)),  # nothing read
        )
        return fake

    assert await smoke.run_smoke(settings, claude_factory=factory, jev_factory=sink_rig.jev_factory) == 1
    out = capsys.readouterr().out
    assert "[FAIL] sonnet chat + cache - turn 2 did not read the cache" in out
    assert "[PASS] haiku structured" in out  # the other checks still ran
    assert "<-- smoke failed" in out


async def test_a_retried_structured_call_is_a_failure(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    flaky = Rig(settings, extra_structured_rows=1)
    assert await flaky.run() == 1
    out = capsys.readouterr().out
    assert "[FAIL] haiku structured" in out
    assert "2 request row(s) [error, ok], expected exactly one ok row" in out


async def test_a_second_attempt_is_a_failure(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    def factory(s: Settings, sink: UsageSink) -> LedgerFake:
        fake = LedgerFake(sink)
        fake.script_structured("smoke", StructuredResult(SKILLS, fake_usage(HAIKU), "end_turn", attempts=2))
        fake.script_chat(text_turn("a", usage=TURN_ONE), text_turn("b", usage=TURN_TWO))
        return fake

    assert await smoke.run_smoke(settings, skip_jev=True, claude_factory=factory) == 1
    assert "needed 2 attempts" in capsys.readouterr().out


async def test_an_empty_skill_list_is_a_failure(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    def factory(s: Settings, sink: UsageSink) -> LedgerFake:
        fake = LedgerFake(sink)
        fake.script_structured("smoke", {"skills": []})
        fake.script_chat(text_turn("a", usage=TURN_ONE), text_turn("b", usage=TURN_TWO))
        return fake

    assert await smoke.run_smoke(settings, skip_jev=True, claude_factory=factory) == 1
    assert "the answer has no skills" in capsys.readouterr().out


async def test_a_claude_error_fails_that_check_with_its_user_message(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    def factory(s: Settings, sink: UsageSink) -> LedgerFake:
        fake = LedgerFake(sink)
        fake.script_structured("smoke", ClaudeAuthError())
        fake.script_chat(ClaudeRefusal("general_harms"))
        return fake

    assert await smoke.run_smoke(settings, skip_jev=True, claude_factory=factory) == 1
    out = capsys.readouterr().out
    assert (
        "[FAIL] haiku structured - ClaudeAuthError: The server's Anthropic API key is missing or invalid."
        in out
    )
    assert "[FAIL] sonnet chat + cache - ClaudeRefusal" in out
    assert "[PASS] docx resume parse" in out  # the offline check does not depend on Claude


async def test_a_turn_without_text_is_a_failure(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    def factory(s: Settings, sink: UsageSink) -> LedgerFake:
        fake = LedgerFake(sink)
        fake.script_structured("smoke", SKILLS)
        fake.script_chat(text_turn("", usage=TURN_ONE), text_turn("b", usage=TURN_TWO))
        return fake

    assert await smoke.run_smoke(settings, skip_jev=True, claude_factory=factory) == 1
    assert "a turn produced no text" in capsys.readouterr().out


async def test_a_stream_without_a_final_event_is_a_failure(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    from tayyari.llm.types import TextDelta

    def factory(s: Settings, sink: UsageSink) -> LedgerFake:
        fake = LedgerFake(sink)
        fake.script_structured("smoke", SKILLS)
        fake.script_chat([TextDelta("cut")])
        return fake

    assert await smoke.run_smoke(settings, skip_jev=True, claude_factory=factory) == 1
    assert "without a final TurnEnd" in capsys.readouterr().out


async def test_a_claude_factory_that_raises_fails_both_checks(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    def factory(s: Settings, sink: UsageSink) -> LedgerFake:
        raise RuntimeError("no client for you")

    assert await smoke.run_smoke(settings, skip_jev=True, claude_factory=factory) == 1
    out = capsys.readouterr().out
    assert (
        "[FAIL] haiku structured - could not build the Claude client: RuntimeError: no client for you" in out
    )
    assert "[FAIL] sonnet chat + cache - could not build" in out


async def test_a_missing_anthropic_key_fails_without_building_a_client(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_a: Any, **_k: Any) -> None:
        raise AssertionError("no client may be built without a key")

    monkeypatch.setattr(smoke, "default_claude", forbidden)
    assert await smoke.run_smoke(make_settings(), skip_jev=True) == 1
    out = capsys.readouterr().out
    assert out.count("ANTHROPIC_API_KEY is not set") == 2
    assert "[PASS] docx resume parse" in out


async def test_a_jev_error_fails_that_check(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    rig.jev.fail_with(JevError("timeout", "Jev did not answer in 10s"))
    assert await rig.run() == 1
    out = capsys.readouterr().out
    assert "[FAIL] jev noul - JevError[timeout]: Jev did not answer in 10s" in out
    assert "[PASS] sonnet chat + cache" in out


async def test_a_jev_no_to_an_obvious_yes_is_a_failure(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    rig.jev.set_answer(smoke.JEV_QUESTION_ID, noul_answer(0.1))
    assert await rig.run() == 1
    assert "answered no to an obvious yes" in capsys.readouterr().out


async def test_a_jev_factory_that_raises_fails_the_jev_check(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_settings: Settings) -> FakeJev:
        raise RuntimeError("bad jev config")

    rig = Rig(settings)
    assert await smoke.run_smoke(settings, claude_factory=rig.claude_factory, jev_factory=broken) == 1
    assert (
        "[FAIL] jev noul - could not build the Jev client: RuntimeError: bad jev config"
        in capsys.readouterr().out
    )


async def test_a_broken_docx_parse_is_a_failure(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def nothing(data: bytes, name: str, log: Callable[[str], None]) -> Any:
        from tayyari.ingest import SourceDoc

        return SourceDoc.failure(
            "resume", "resume", "Resume", "Resume", None, name, "Could not read that DOCX file."
        )

    monkeypatch.setattr(smoke, "ingest_resume", nothing)
    assert await rig.run() == 1
    assert (
        "[FAIL] docx resume parse - the DOCX was not read: Could not read that DOCX file."
        in capsys.readouterr().out
    )


# ───────────────────────── helpers ─────────────────────────


@pytest.mark.parametrize(
    ("micro", "text"),
    [(0, "$0.0000"), (1_000, "$0.0010"), (123_456, "$0.1235"), (4, "$0.000004"), (999, "$0.000999")],
)
def test_usd_formatting(micro: int, text: str) -> None:
    assert smoke.usd(micro) == text


def test_describe_never_returns_an_empty_line() -> None:
    assert smoke.describe(ValueError()) == "ValueError"
    assert smoke.describe(ValueError("a\n  b")) == "ValueError: a b"


# ───────────────────────── run() ─────────────────────────


def test_run_is_the_whole_thing_in_one_call(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    rig = Rig(settings)
    exit_code = smoke.run(settings=settings, claude_factory=rig.claude_factory, jev_factory=rig.jev_factory)
    assert exit_code == 0
    assert "Result: 4 PASS, 0 FAIL, 0 SKIP" in capsys.readouterr().out


def test_run_returns_1_when_a_check_fails(settings: Settings) -> None:
    rig = Rig(settings)
    rig.jev.fail_with(JevError("auth", "bad key"))
    assert smoke.run(settings=settings, claude_factory=rig.claude_factory, jev_factory=rig.jev_factory) == 1


async def test_a_client_that_fails_to_close_does_not_hide_the_report(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken_close() -> None:
        raise RuntimeError("socket already closed")

    monkeypatch.setattr(rig.jev, "aclose", broken_close)
    assert await rig.run() == 0
    assert "Result: 4 PASS, 0 FAIL, 0 SKIP" in capsys.readouterr().out
