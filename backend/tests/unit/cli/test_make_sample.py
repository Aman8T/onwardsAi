"""``make-sample`` against a scripted container: the stream is printed, the files are written, the container closed."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tayyari.cli import make_sample
from tayyari.container import Container, ContainerOverrides
from tayyari.contract import (
    AnalyzeRequest,
    Profile,
    ReaderState,
    RequestContext,
    StoredContext,
    StoredSource,
    ev_done,
    ev_error,
    ev_log,
    ev_phase,
    ev_reader,
    ev_readers,
    ev_signal,
    ev_synth,
)
from tayyari.core.settings import Settings
from tayyari.llm.errors import ClaudeAuthError
from tayyari.sample import SAMPLE_GOAL
from tayyari.sample.provider import SampleProvider
from tests.fakes.claude import FakeClaudeClient
from tests.unit.analysis.support import (
    SAMPLE_IDS,
    reader_output,
    sample_readers,
    sample_synth,
    source_id_of,
)

NOW = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)


def scripted_profile() -> Profile:
    """The bundled sample profile with a new id and a name that needs UTF-8."""
    base = SampleProvider().sample_profile
    assert base is not None
    return base.model_copy(
        update={
            "id": "sample-0123456789abcdef",
            "person": base.person.model_copy(update={"name": "Zoë Ångström"}),
        }
    )


def stored_context() -> StoredContext:
    return StoredContext(
        sources=[
            StoredSource(id="resume", label="Resume", kind="resume", content="Zoë wrote “things” · 3 years")
        ],
        readers={"resume": {"skills": [{"name": "React"}]}},
    )


def reader(status: str, line: str) -> ReaderState:
    return ReaderState(
        sourceId="resume",
        kind="resume",
        src="resume.md",
        kindLabel="Resume",
        agent="Resume reader",
        status=status,  # type: ignore[arg-type]
        line=line,
        progress=100 if status == "done" else 40,
    )


def good_events(profile: Profile) -> list[Any]:
    return [
        ev_phase("reading", "Reading your sources"),
        ev_readers([reader("queued", "waiting")]),
        ev_reader(reader("reading", "halfway")),
        ev_signal("React", "resume", "resume"),
        ev_log("Jev", "First read: design, early career", "resume"),
        ev_reader(reader("done", "12 skills found")),
        ev_phase("synthesizing", "Building the graph"),
        ev_synth(1, 2500),
        ev_synth(1, 2600),  # less than 2,000 characters after the last printed one: not printed
        ev_synth(2, 5000),
        ev_synth(3),
        ev_done(profile),
    ]


class FakeProfiles:
    def __init__(self, stored: tuple[dict[str, Any], StoredContext] | None) -> None:
        self.stored = stored
        self.requested: list[str] = []

    async def get_for_chat(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        self.requested.append(pid)
        return self.stored


class FakeLimits:
    def __init__(self, error: BaseException | None) -> None:
        self.error = error

    async def spent_today(self, day: Any) -> int:
        if self.error is not None:
            raise self.error
        return 123_456


class FakeRepos:
    def __init__(
        self, stored: tuple[dict[str, Any], StoredContext] | None, limits_error: BaseException | None
    ) -> None:
        self.profiles = FakeProfiles(stored)
        self.limits = FakeLimits(limits_error)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeAnalysis:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.requests: list[tuple[AnalyzeRequest, RequestContext]] = []

    async def run(self, req: AnalyzeRequest, rc: RequestContext) -> AsyncIterator[Any]:
        self.requests.append((req, rc))
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


@dataclass
class Script:
    """What the fake container plays back, and what it saw. One per test."""

    events: list[Any] = field(default_factory=list)
    stored: tuple[dict[str, Any], StoredContext] | None = None
    build_error: BaseException | None = None
    limits_error: BaseException | None = None
    built_with: list[Settings] = field(default_factory=list)
    data_dir_existed: list[bool] = field(default_factory=list)
    instances: list[FakeContainer] = field(default_factory=list)


class FakeContainer:
    """A scripted container: the analysis plays the script, the repositories return the stored profile."""

    def __init__(self, script: Script) -> None:
        self.analysis = FakeAnalysis(list(script.events))
        self.repos = FakeRepos(script.stored, script.limits_error)
        self.clock = FakeClock()
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


class FakeContainerType:
    """Stands in for the `Container` class in `make_sample`: `Container.build(settings)` records and returns a fake."""

    def __init__(self, script: Script) -> None:
        self.script = script

    @staticmethod
    def _folder_exists(settings: Settings) -> bool:
        return Path(settings.data_dir).is_dir()

    async def build(self, settings: Settings) -> FakeContainer:
        self.script.built_with.append(settings)
        self.script.data_dir_existed.append(self._folder_exists(settings))
        if self.script.build_error is not None:
            raise self.script.build_error
        instance = FakeContainer(self.script)
        self.script.instances.append(instance)
        return instance


@pytest.fixture
def container(monkeypatch: pytest.MonkeyPatch) -> Script:
    """An empty script, installed as `make_sample.Container`; tests fill in the events they want."""
    script = Script()
    monkeypatch.setattr(make_sample, "Container", FakeContainerType(script))
    return script


@pytest.fixture
def scripted(container: Script) -> Script:
    """The happy path: a full stream ending in ``done``, and the profile stored with its context."""
    profile = scripted_profile()
    container.events = good_events(profile)
    container.stored = (profile.to_dict(), stored_context())
    return container


# ───────────────────────── the files ─────────────────────────


def test_writes_both_files_and_closes_the_container(
    scripted: Script, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    out = tmp_path / "out"
    assert make_sample.run(out, settings=make_settings()) == 0

    profile_path, context_path = out / "sample-profile.json", out / "sample-context.json"
    assert sorted(p.name for p in out.iterdir()) == [
        "sample-context.json",
        "sample-profile.json",
    ]  # no .tmp left over

    profile_text = profile_path.read_text(encoding="utf-8")
    profile = scripted_profile()
    assert profile_text == json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n"
    assert profile_text.startswith('{\n  "id": "sample-0123456789abcdef"')
    assert "Zoë Ångström" in profile_text  # real characters, not \u escapes
    assert Profile.from_dict(json.loads(profile_text)) == profile

    context_text = context_path.read_text(encoding="utf-8")
    assert (
        context_text
        == json.dumps(stored_context().to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    assert context_text.count("\n") == 1  # one compact line, like the bundled file
    assert "“things” · 3 years" in context_text
    assert StoredContext.model_validate(json.loads(context_text)) == stored_context()

    (instance,) = scripted.instances
    assert instance.closed == 1


def test_line_ends_are_lf_on_every_platform(
    scripted: Script, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    make_sample.run(tmp_path, settings=make_settings())
    for name in ("sample-profile.json", "sample-context.json"):
        raw = (tmp_path / name).read_bytes()
        assert raw.endswith(b"\n")
        assert b"\r" not in raw


def test_it_asks_the_pipeline_for_the_sample_with_its_goal(
    scripted: Script, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    make_sample.run(tmp_path, settings=make_settings())
    ((request, context),) = scripted.instances[0].analysis.requests
    assert request == AnalyzeRequest(sample=True, goal=SAMPLE_GOAL)
    assert request.links == () and request.resume_bytes is None
    assert context.request_id.startswith("cli-make-sample")
    assert context.ip_hash is None and context.client_ip is None
    assert context.started_at == NOW


def test_the_context_is_read_back_for_the_new_profile_id(
    scripted: Script, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    make_sample.run(tmp_path, settings=make_settings())
    assert scripted.instances[0].repos.profiles.requested == ["sample-0123456789abcdef"]


def test_existing_files_are_replaced(
    scripted: Script, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    (tmp_path / "sample-profile.json").write_text("old", encoding="utf-8")
    (tmp_path / "sample-context.json").write_text("old", encoding="utf-8")
    assert make_sample.run(tmp_path, settings=make_settings()) == 0
    assert (tmp_path / "sample-profile.json").read_text(encoding="utf-8").startswith("{")
    assert (tmp_path / "sample-context.json").read_text(encoding="utf-8").startswith("{")


def test_the_default_output_folder_is_the_bundled_sample_folder(
    scripted: Script,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
) -> None:
    from tayyari.core.paths import SAMPLE_DIR

    assert make_sample.DEFAULT_OUT == SAMPLE_DIR  # tayyari/sample/
    monkeypatch.setattr(make_sample, "DEFAULT_OUT", tmp_path)  # never write into the repo from a test
    assert make_sample.run(None, settings=make_settings()) == 0
    assert (tmp_path / "sample-profile.json").is_file()


def test_the_output_folder_is_created(
    scripted: Script, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    out = tmp_path / "a" / "b" / "c"
    assert make_sample.run(out, settings=make_settings()) == 0
    assert (out / "sample-profile.json").is_file()


# ───────────────────────── what is printed ─────────────────────────


def test_one_compact_line_per_event(
    scripted: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_sample.run(tmp_path, settings=make_settings())
    out = capsys.readouterr().out
    assert "== reading: Reading your sources" in out
    assert "readers: Resume reader (resume.md)" in out
    assert "[resume] DONE - 12 skills found" in out
    assert "halfway" not in out  # only finished readers get a line
    assert "+ React (resume)" in out
    assert "Jev      First read: design, early career" in out
    assert "== synthesizing: Building the graph" in out
    assert "synth step 1 - 2500 chars" in out
    assert "2600 chars" not in out
    assert "synth step 2 - 5000 chars" in out
    assert "synth step 3" in out
    assert "done - profile sample-0123456789abcdef" in out
    assert f"Wrote {tmp_path / 'sample-profile.json'}" in out
    assert "Zoë Ångström" in out
    assert "Spent this run: $0.1235" in out


def test_event_lines_carry_the_elapsed_time(
    scripted: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_sample.run(tmp_path, settings=make_settings())
    phase_line = next(line for line in capsys.readouterr().out.splitlines() if "== reading" in line)
    assert phase_line.split()[0].endswith("s")


# ───────────────────────── the throw-away settings ─────────────────────────


def test_the_container_gets_a_throw_away_sqlite_database(
    scripted: Script, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    base = make_settings(database_url="postgresql://user:pw@db.example.com/prod", pacing_scale=1.0)
    assert base.db.kind == "postgres"  # also fills the cached property that a plain copy would carry along

    make_sample.run(tmp_path, settings=base)
    (used,) = scripted.built_with
    assert used.database_url is None
    assert used.db.kind == "sqlite"
    assert used.data_dir != base.data_dir
    assert Path(used.data_dir).name.startswith("tayyari-sample-")
    assert str(used.data_dir).replace("\\", "/") in used.db.url.replace("%3A", ":")
    assert used.pacing_scale == 0.0
    assert used.guard.enabled is False
    assert used.log_level == "WARNING"
    # the caller's settings are untouched
    assert base.database_url is not None
    assert base.db.kind == "postgres"
    assert base.pacing_scale == 1.0
    assert base.guard.enabled is True


def test_the_temporary_folder_exists_during_the_run_and_is_gone_after(
    scripted: Script, tmp_path: Path, make_settings: Callable[..., Settings]
) -> None:
    make_sample.run(tmp_path, settings=make_settings())
    assert scripted.data_dir_existed == [True]
    assert not Path(scripted.built_with[0].data_dir).exists()


def test_sample_settings_keeps_everything_else() -> None:
    base = Settings(_env_file=None, env="test", anthropic_api_key="sk-x", reader_model="claude-haiku-4-5")
    derived = make_sample.sample_settings(base, Path("somewhere"))
    assert derived.anthropic_api_key is not None and derived.anthropic_api_key.get_secret_value() == "sk-x"
    assert derived.reader_model == "claude-haiku-4-5"
    assert derived.env == "test"
    assert derived.data_dir == Path("somewhere")


# ───────────────────────── failures ─────────────────────────


def assert_nothing_written(out: Path) -> None:
    assert not out.exists() or list(out.iterdir()) == []


def test_an_error_event_fails_the_run_and_writes_nothing(
    container: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    container.events = [ev_phase("reading", "Reading"), ev_error("Claude is busy right now.")]
    container.stored = (scripted_profile().to_dict(), stored_context())
    out = tmp_path / "out"
    (out).mkdir()
    (out / "sample-profile.json").write_text("keep me", encoding="utf-8")

    assert make_sample.run(out, settings=make_settings()) == 1

    captured = capsys.readouterr()
    assert "ERROR: Claude is busy right now." in captured.out
    assert "make-sample failed: Claude is busy right now." in captured.err
    assert (out / "sample-profile.json").read_text(encoding="utf-8") == "keep me"
    assert not (out / "sample-context.json").exists()
    assert container.instances[0].closed == 1


def test_a_stream_without_a_profile_fails(
    container: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    container.events = [ev_phase("reading", "Reading"), ev_log("Haiku", "working")]
    assert make_sample.run(tmp_path, settings=make_settings()) == 1
    assert "ended without a profile" in capsys.readouterr().err
    assert_nothing_written(tmp_path)
    assert container.instances[0].closed == 1


def test_a_profile_that_was_not_stored_fails(
    container: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    container.events = good_events(scripted_profile())
    container.stored = None
    assert make_sample.run(tmp_path, settings=make_settings()) == 1
    assert "was not stored" in capsys.readouterr().err
    assert_nothing_written(tmp_path)
    assert container.instances[0].closed == 1


def test_an_exception_in_the_pipeline_fails_and_still_closes_the_container(
    container: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    container.events = [ev_phase("reading", "Reading"), RuntimeError("synth exploded")]
    assert make_sample.run(tmp_path, settings=make_settings()) == 1
    assert "RuntimeError: synth exploded" in capsys.readouterr().err
    assert_nothing_written(tmp_path)
    assert container.instances[0].closed == 1


def test_a_container_that_cannot_be_built_fails(
    container: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    container.build_error = ConnectionError("database is down")
    assert make_sample.run(tmp_path, settings=make_settings()) == 1
    assert "ConnectionError: database is down" in capsys.readouterr().err
    assert container.instances == []
    assert_nothing_written(tmp_path)


def test_a_broken_spend_ledger_does_not_fail_the_command(
    scripted: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    scripted.limits_error = RuntimeError("no ledger")
    assert make_sample.run(tmp_path, settings=make_settings()) == 0
    assert "Spent this run" not in capsys.readouterr().out


def test_the_summary_lists_the_roles(
    scripted: Script,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_sample.run(tmp_path, settings=make_settings())
    out = capsys.readouterr().out
    profile = scripted_profile()
    assert f"{profile.person.name} - {profile.person.headline} - mastery {profile.mastery.score}" in out
    for role in profile.roles:
        assert f"{role.alignment:>3}%  {role.title} ({role.eta})" in out


# ───────────────────────── end to end: the real container, a real SQLite file, a scripted Claude ─────────────────────────


class RealContainerWithFakes:
    """The real `Container.build` (migrations, repositories, services) with only the Claude client replaced."""

    def __init__(self, claude: FakeClaudeClient) -> None:
        self.claude = claude
        self.built: list[Container] = []

    async def build(self, settings: Settings) -> Container:
        container = await Container.build(settings, overrides=ContainerOverrides(claude=self.claude))
        self.built.append(container)
        return container


def sample_claude() -> FakeClaudeClient:
    """Readers answer from the bundled context, the synthesizer with the bundled profile."""
    readers = sample_readers()
    claude = FakeClaudeClient()
    claude.script_structured(
        "reader",
        lambda call: readers.get(source_id_of(call), reader_output(source_id_of(call))),
        repeat=True,
    )
    claude.script_structured("synth", sample_synth())
    return claude


def test_the_real_pipeline_writes_a_sample_that_parses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude = sample_claude()
    real = RealContainerWithFakes(claude)
    monkeypatch.setattr(make_sample, "Container", real)
    out = tmp_path / "out"

    assert make_sample.run(out, settings=make_settings(database_url=None)) == 0

    profile = Profile.from_dict(json.loads((out / "sample-profile.json").read_text(encoding="utf-8")))
    context = StoredContext.model_validate(
        json.loads((out / "sample-context.json").read_text(encoding="utf-8"))
    )
    assert re.fullmatch(r"sample-[0-9a-f]{32}", profile.id)
    assert profile.goal == SAMPLE_GOAL
    assert len(profile.skills) >= 3 and len(profile.roles) >= 1
    assert [s.id for s in context.sources] == ["resume", "github", "lovable-1", "portfolio-1"]
    assert set(context.readers) == set(SAMPLE_IDS)
    assert context.sources[0].content.startswith("RIYA KAPOOR")

    # the model calls were the real pipeline's: four readers and one synthesis
    assert len(claude.calls_for("reader")) == 4
    assert len(claude.calls_for("synth")) == 1
    # one throw-away database, closed again
    (container,) = real.built
    assert container.settings.db.kind == "sqlite"
    assert container.settings.data_dir != make_settings().data_dir
    assert not Path(container.settings.data_dir).exists()
    assert container.maintenance is None  # aclose() ran

    out_text = capsys.readouterr().out
    assert f"done - profile {profile.id}" in out_text
    assert "Wrote" in out_text


def test_the_real_pipeline_reports_a_model_failure_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    claude = FakeClaudeClient()
    claude.script_structured("reader", ClaudeAuthError(), repeat=True)
    real = RealContainerWithFakes(claude)
    monkeypatch.setattr(make_sample, "Container", real)
    out = tmp_path / "out"

    assert make_sample.run(out, settings=make_settings()) == 1

    captured = capsys.readouterr()
    assert "ERROR:" in captured.out
    assert "make-sample failed:" in captured.err
    assert not out.exists() or list(out.iterdir()) == []
    assert real.built[0].maintenance is None
