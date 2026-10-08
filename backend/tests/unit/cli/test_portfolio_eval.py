"""``portfolio-eval``: the table, the written files and the exit code, over the real fixtures.

The container is the real one on a throw-away SQLite file; only the model is scripted (or absent, for ``--offline``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tayyari.cli import portfolio_eval
from tayyari.container import Container, ContainerOverrides
from tayyari.contract.portfolio import Portfolio, spec_problems
from tayyari.core.settings import Settings
from tayyari.portfolio import service as service_module
from tayyari.portfolio.ground import SlotCheck
from tayyari.portfolio.service import PortfolioService
from tests.fakes.claude import FakeClaudeClient
from tests.unit.portfolio._fixtures_a import load_fixture
from tests.unit.portfolio._fixtures_c import good_copy, script_copy, script_facts

MakeSettings = Callable[..., Settings]


def scripted_factory(name: str = "riya") -> portfolio_eval.ContainerFactory:
    """A container whose Claude extracts the facts of fixture ``name`` and writes its copy."""
    claude = FakeClaudeClient()
    script_facts(claude, load_fixture(name).ctx)
    script_copy(claude, good_copy(name))

    async def factory(settings: Settings) -> Container:
        return await Container.build(settings, overrides=ContainerOverrides(claude=claude))

    return factory


def test_the_fixtures_are_found_by_name_and_unknown_names_are_a_usage_error() -> None:
    folder = portfolio_eval.FIXTURES_DIR
    every = portfolio_eval.fixture_paths(folder, [])
    assert {"riya", "aarav", "meera", "vikram", "sana", "stress-scanned"} <= {p.stem for p in every}
    assert [p.stem for p in portfolio_eval.fixture_paths(folder, ["sana", "riya", "sana"])] == [
        "sana",
        "riya",
    ]
    with pytest.raises(portfolio_eval.UsageError, match="unknown fixture"):
        portfolio_eval.fixture_paths(folder, ["nobody"])


def test_an_offline_run_prints_a_table_row_per_fixture_and_writes_nothing_by_default(
    make_settings: MakeSettings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "rendered"
    code = portfolio_eval.run("riya,stress-scanned", offline=True, settings=make_settings(), out=out)
    shown = capsys.readouterr().out

    assert code == 0
    assert "offline (no model calls)" in shown
    header = next(line for line in shown.splitlines() if line.startswith("name"))
    for column in ("facts k/d", "slots m/r", "re-asked", "fallbacks", "ungrounded", "cost", "seconds"):
        assert column in header
    rows = [line.split() for line in shown.splitlines() if line.startswith(("riya ", "stress-scanned "))]
    assert [r[0] for r in rows] == ["riya", "stress-scanned"]
    assert all(r[1] == "0/0" and r[5] == "0" for r in rows)  # no facts offline, nothing ungrounded
    assert "total: ungrounded 0" in shown
    assert not out.exists()


def test_write_stores_the_portfolio_json_of_each_fixture(
    make_settings: MakeSettings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "rendered"
    code = portfolio_eval.run("riya,vikram", write=True, offline=True, settings=make_settings(), out=out)
    assert code == 0 and "Wrote 2 file(s)" in capsys.readouterr().out
    assert sorted(p.name for p in out.iterdir()) == ["riya.portfolio.json", "vikram.portfolio.json"]

    raw = (out / "riya.portfolio.json").read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw
    page = Portfolio.model_validate(json.loads(raw))
    assert page.profileId == load_fixture("riya").profile.id
    assert page.content.person.name == "Riya Kapoor" and spec_problems(page.spec) == []
    assert page.looks and page.graph.domains


def test_a_scripted_online_run_reports_model_slots(
    make_settings: MakeSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    code = portfolio_eval.run(
        "riya", settings=make_settings(), container_factory=scripted_factory(), details=True
    )
    shown = capsys.readouterr().out
    assert code == 0
    row = next(line.split() for line in shown.splitlines() if line.startswith("riya "))
    assert row[1] == "20/6" and row[2] == "16/0"  # facts kept/dropped, slots model/resume
    assert "real clients" in shown
    assert "repeats an earlier fact" in shown  # --details lists what was dropped


def test_an_ungrounded_slot_fails_the_run(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = service_module.check_slot

    def strict(slot: object, facts: object, **kw: object) -> SlotCheck:
        if getattr(slot, "id", "") == "tagline":
            return SlotCheck("tagline", False, ["planted"], [])
        return real(slot, facts, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(service_module, "check_slot", strict)
    code = portfolio_eval.run("riya", settings=make_settings(), container_factory=scripted_factory())
    captured = capsys.readouterr()
    assert code == 1
    assert "1 ungrounded slot(s)" in captured.err
    assert "total: ungrounded 1" in captured.out


def test_a_build_error_is_reported_and_fails_the_run(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(self: PortfolioService, *args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(PortfolioService, "build", broken)
    code = portfolio_eval.run("riya,aarav", offline=True, settings=make_settings())
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out.count("ERROR") >= 2 and "RuntimeError: boom" in captured.out
    assert "2 build error(s)" in captured.err


def test_an_online_run_needs_the_anthropic_key(
    make_settings: MakeSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    code = portfolio_eval.run("riya", settings=make_settings())
    assert code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_an_unknown_fixture_name_exits_with_the_usage_code(
    make_settings: MakeSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert portfolio_eval.run("nobody", offline=True, settings=make_settings()) == 2
    assert "unknown fixture" in capsys.readouterr().err
