from __future__ import annotations

import tempfile
from pathlib import Path

import platformdirs
import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsError

from tayyari.core import settings as settings_module
from tayyari.core.dburl import DbConfig
from tayyari.core.settings import (
    GuardSettings,
    JevSettings,
    ModelNames,
    Settings,
    detect_env,
    get_settings,
)


def build(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


# ───────────────────────────── defaults ─────────────────────────────


def test_defaults_match_the_spec() -> None:
    s = build()
    assert s.env == "local"
    assert (s.reader_model, s.synth_model, s.chat_model) == (
        "claude-haiku-4-5",
        "claude-sonnet-5-5",
        "claude-sonnet-5-5",
    )
    assert s.reader_thinking is False
    assert (s.synth_effort, s.chat_effort) == ("medium", "medium")
    assert (s.reader_max_tokens, s.synth_max_tokens, s.chat_max_tokens) == (8000, 32000, 8000)
    assert s.api_port == 8787
    assert s.synth_hints is False
    assert s.synth_cache == "system"
    assert s.chat_cache_ttl == "5m"
    assert s.pacing_scale == 1.0
    assert s.decision_overrides == {}
    assert s.anthropic_api_key is None
    assert s.jev_api_key is None
    assert s.github_token is None
    assert s.logfire_token is None
    assert s.database_url is None


def test_nested_defaults_match_the_spec() -> None:
    s = build()
    assert s.jev == JevSettings(
        enabled=True,
        model="jev-1.13.0",
        base_url="https://api.typesafe.ai",
        timeout_display_s=2.5,
        timeout_evidence_s=4.0,
        max_retries=1,
        send_pii=False,
    )
    assert s.guard == GuardSettings(
        analyze_per_minute=2,
        analyze_per_hour=6,
        analyze_per_day=15,
        analyze_global_per_hour=60,
        chat_per_minute=20,
        chat_per_hour=120,
        chat_per_day=400,
        daily_ceiling_usd=20.0,
        est_analyze_usd=0.40,
        est_chat_usd=0.10,
        ceiling_grace=1.25,
        trusted_proxy_hops=1,
        client_ip_header=None,
        enabled=True,
    )
    assert (s.concurrency.reader_concurrency, s.concurrency.reader_timeout_s) == (6, 90)
    assert s.concurrency.max_concurrent_analyses == 4
    assert (s.retention.profile_days, s.retention.payload_days, s.retention.metadata_days) == (60, 30, 180)


def test_models_property_is_a_small_frozen_object() -> None:
    s = build(reader_model="r", synth_model="s", chat_model="c")
    assert s.models == ModelNames(reader="r", synth="s", chat="c")
    with pytest.raises(ValidationError):
        s.models.reader = "x"  # type: ignore[misc]


# ───────────────────────────── precedence ─────────────────────────────


def test_environment_beats_the_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\nTAYYARI_SYNTH_MODEL=file-model\nGITHUB_TOKEN=gh-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    s = Settings(_env_file=env_file)
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "from-env"
    # whatever the environment does not set still comes from the file
    assert s.synth_model == "file-model"
    assert s.github_token is not None
    assert s.github_token.get_secret_value() == "gh-file"


def test_later_env_file_wins(tmp_path: Path) -> None:
    repo_env = tmp_path / "repo.env"
    backend_env = tmp_path / "backend.env"
    repo_env.write_text("TAYYARI_API_PORT=1111\nTAYYARI_SYNTH_MODEL=repo-model\n")
    backend_env.write_text("TAYYARI_API_PORT=2222\n")
    s = Settings(_env_file=(repo_env, backend_env))
    assert s.api_port == 2222
    assert s.synth_model == "repo-model"


def test_missing_env_files_are_ignored(tmp_path: Path) -> None:
    s = Settings(_env_file=(tmp_path / "nope.env", tmp_path / "also-nope.env"))
    assert s.anthropic_api_key is None


def test_init_arguments_beat_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_API_PORT", "1234")
    assert build(api_port=4321).api_port == 4321


def test_conftest_isolation_ignores_the_real_repo_env_file() -> None:
    # The repo-root .env holds live keys on a developer machine; no Settings() may see them in a test.
    s = Settings()
    assert s.anthropic_api_key is None
    assert s.jev_api_key is None


# ───────────────────────────── aliases ─────────────────────────────


def test_third_party_names_are_read_without_the_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("JEV_API_KEY", "jev-key")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp")
    monkeypatch.setenv("LOGFIRE_TOKEN", "lf")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example.com/app")
    s = build()
    assert isinstance(s.anthropic_api_key, SecretStr)
    assert s.anthropic_api_key.get_secret_value() == "sk-ant"
    assert s.jev_api_key is not None
    assert s.jev_api_key.get_secret_value() == "jev-key"
    assert s.github_token is not None
    assert s.github_token.get_secret_value() == "ghp"
    assert s.logfire_token is not None
    assert s.logfire_token.get_secret_value() == "lf"
    assert s.database_url == "postgresql://u:p@db.example.com/app"


def test_typesafe_api_key_is_an_alias_and_jev_api_key_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    s = build()
    assert s.jev_api_key is not None
    assert s.jev_api_key.get_secret_value() == "ts-key"
    monkeypatch.setenv("JEV_API_KEY", "jev-key")
    s = build()
    assert s.jev_api_key is not None
    assert s.jev_api_key.get_secret_value() == "jev-key"


def test_aliases_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows upper-cases names; a .env file may use any case
    monkeypatch.setenv("anthropic_api_key", "lower")
    s = build()
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "lower"


def test_field_names_work_as_constructor_arguments() -> None:
    # populate_by_name: tests build Settings(anthropic_api_key=...) without knowing the env alias
    s = build(anthropic_api_key="by-name", jev_api_key="jev-by-name", database_url="sqlite:///x.db")
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "by-name"
    assert s.jev_api_key is not None
    assert s.jev_api_key.get_secret_value() == "jev-by-name"
    assert s.db.kind == "sqlite"


def test_secrets_never_show_in_repr_or_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-very-secret")
    monkeypatch.setenv("JEV_API_KEY", "jev-very-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:db-very-secret@db.example.com/app")
    s = build()
    text = repr(s) + str(s) + repr(s.summary()) + repr(s.db)
    assert "very-secret" not in text
    assert s.summary()["anthropic_key"] is True
    assert s.summary()["jev_enabled"] is True


# ───────────────────────────── empty means unset ─────────────────────────────


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_credentials_count_as_unset(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", blank)
    monkeypatch.setenv("JEV_API_KEY", blank)
    monkeypatch.setenv("GITHUB_TOKEN", blank)
    monkeypatch.setenv("LOGFIRE_TOKEN", blank)
    monkeypatch.setenv("DATABASE_URL", blank)
    s = build()
    assert s.anthropic_api_key is None
    assert s.jev_api_key is None
    assert s.github_token is None
    assert s.logfire_token is None
    assert s.database_url is None
    assert s.db.kind == "sqlite"


def test_blank_tayyari_variables_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_SYNTH_MODEL", "")
    monkeypatch.setenv("TAYYARI_API_PORT", "")
    monkeypatch.setenv("TAYYARI_GUARD__DAILY_CEILING_USD", "")
    monkeypatch.setenv("TAYYARI_GUARD__CLIENT_IP_HEADER", "")
    monkeypatch.setenv("TAYYARI_DECISION_OVERRIDES", "")
    s = build()
    assert s.synth_model == "claude-sonnet-5-5"
    assert s.api_port == 8787
    assert s.guard.daily_ceiling_usd == 20.0
    assert s.guard.client_ip_header is None
    assert s.decision_overrides == {}


def test_blank_init_values_are_unset_and_keys_are_stripped() -> None:
    s = build(anthropic_api_key="  sk-x  ", jev_api_key="", database_url="   ")
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-x"
    assert s.jev_api_key is None
    assert s.database_url is None


def test_blank_secret_in_env_file_is_unset(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=\nJEV_API_KEY=\nDATABASE_URL=\n")
    s = Settings(_env_file=env_file)
    assert s.anthropic_api_key is None
    assert s.database_url is None


# ───────────────────────────── environment detection ─────────────────────────────


def test_local_is_the_default_environment() -> None:
    assert build().env == "local"
    assert detect_env() == "local"


@pytest.mark.parametrize("marker", ["FASTAPICLOUD_DEPLOYMENT_ID", "FASTAPI_PUBLIC_URL"])
def test_cloud_markers_select_prod(monkeypatch: pytest.MonkeyPatch, marker: str) -> None:
    monkeypatch.setenv(marker, "something")
    assert build().env == "prod"
    assert build().is_prod


def test_empty_cloud_marker_is_not_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTAPICLOUD_DEPLOYMENT_ID", "")
    assert build().env == "local"


def test_explicit_env_beats_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTAPICLOUD_DEPLOYMENT_ID", "abc")
    monkeypatch.setenv("TAYYARI_ENV", "test")
    assert build().env == "test"


def test_invalid_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_ENV", "staging")
    with pytest.raises(ValidationError):
        build()


def test_detection_happens_per_call_not_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build().env == "local"
    monkeypatch.setenv("FASTAPI_PUBLIC_URL", "https://x.fastapicloud.dev")
    assert build().env == "prod"


# ───────────────────────────── data dir and db ─────────────────────────────


def test_data_dir_is_the_user_data_folder_locally_and_temp_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build().data_dir == Path(platformdirs.user_data_dir("tayyari", appauthor=False))
    assert build(env="test").data_dir == Path(platformdirs.user_data_dir("tayyari", appauthor=False))
    assert build(env="prod").data_dir == Path(tempfile.gettempdir()) / "tayyari"


def test_data_dir_can_be_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TAYYARI_DATA_DIR", str(tmp_path / "d"))
    assert build(env="prod").data_dir == tmp_path / "d"


def test_data_dir_is_not_created_by_settings(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "data"
    build(data_dir=target)
    assert not target.exists()


def test_db_is_derived_and_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    s = build(data_dir=tmp_path)
    assert isinstance(s.db, DbConfig)
    assert s.db.kind == "sqlite"
    assert s.db is s.db
    monkeypatch.setenv(
        "DATABASE_URL", "postgres://u:p@ep-x-pooler.region.aws.neon.tech/neondb?sslmode=require"
    )
    pg = build()
    assert pg.db.kind == "postgres"
    assert pg.db.url.startswith("postgresql+asyncpg://")


def test_prod_without_database_url_is_ephemeral(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTAPICLOUD_DEPLOYMENT_ID", "d1")
    s = build()
    assert s.db.ephemeral is True
    assert s.summary()["storage"] == "ephemeral"


# ───────────────────────────── nested and typed values ─────────────────────────────


def test_nested_double_underscore_overrides_keep_the_other_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_GUARD__DAILY_CEILING_USD", "5.5")
    monkeypatch.setenv("TAYYARI_GUARD__ANALYZE_PER_HOUR", "2")
    monkeypatch.setenv("TAYYARI_GUARD__CLIENT_IP_HEADER", "fly-client-ip")
    monkeypatch.setenv("TAYYARI_JEV__ENABLED", "false")
    monkeypatch.setenv("TAYYARI_JEV__TIMEOUT_DISPLAY_S", "1.5")
    monkeypatch.setenv("TAYYARI_CONCURRENCY__READER_CONCURRENCY", "3")
    monkeypatch.setenv("TAYYARI_RETENTION__PROFILE_DAYS", "7")
    s = build()
    assert s.guard.daily_ceiling_usd == 5.5
    assert s.guard.analyze_per_hour == 2
    assert s.guard.analyze_per_minute == 2  # untouched default
    assert s.guard.client_ip_header == "fly-client-ip"
    assert s.jev.enabled is False
    assert s.jev.timeout_display_s == 1.5
    assert s.jev.model == "jev-1.13.0"
    assert s.concurrency.reader_concurrency == 3
    assert s.concurrency.reader_timeout_s == 90
    assert s.retention.profile_days == 7
    assert s.retention.payload_days == 30


def test_nested_overrides_from_an_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TAYYARI_GUARD__DAILY_CEILING_USD=3\nTAYYARI_JEV__SEND_PII=true\n")
    s = Settings(_env_file=env_file)
    assert s.guard.daily_ceiling_usd == 3
    assert s.jev.send_pii is True


def test_nested_objects_can_be_passed_directly() -> None:
    s = build(guard=GuardSettings(enabled=False), jev=JevSettings(enabled=False))
    assert s.guard.enabled is False
    assert s.jev.enabled is False


def test_decision_overrides_parse_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TAYYARI_DECISION_OVERRIDES",
        '{"evidence.support": {"mode": "shadow"}, "chat.web_gate": {"mode": "off", "threshold": 0.9}}',
    )
    s = build()
    assert s.decision_overrides == {
        "evidence.support": {"mode": "shadow"},
        "chat.web_gate": {"mode": "off", "threshold": 0.9},
    }
    assert s.summary()["decision_overrides"] == ["chat.web_gate", "evidence.support"]


def test_decision_overrides_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('TAYYARI_DECISION_OVERRIDES={"triage.text_quality":{"mode":"off"}}\n')
    assert Settings(_env_file=env_file).decision_overrides == {"triage.text_quality": {"mode": "off"}}


def test_invalid_decision_overrides_json_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_DECISION_OVERRIDES", "{not json")
    with pytest.raises(SettingsError):
        build()


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_legacy_reader_thinking_flag_spellings(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("TAYYARI_READER_THINKING", raw)
    assert build().reader_thinking is True


def test_legacy_model_variable_names_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_READER_MODEL", "my-reader")
    monkeypatch.setenv("TAYYARI_SYNTH_MODEL", "my-synth")
    monkeypatch.setenv("TAYYARI_CHAT_MODEL", "my-chat")
    assert build().models == ModelNames(reader="my-reader", synth="my-synth", chat="my-chat")


def test_api_port_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_API_PORT", "9100")
    assert build().api_port == 9100


def test_literals_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAYYARI_SYNTH_CACHE", "everything")
    with pytest.raises(ValidationError):
        build()
    monkeypatch.setenv("TAYYARI_SYNTH_CACHE", "system+data")
    monkeypatch.setenv("TAYYARI_CHAT_CACHE_TTL", "1h")
    s = build()
    assert (s.synth_cache, s.chat_cache_ttl) == ("system+data", "1h")


def test_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        build(guard=GuardSettings(ceiling_grace=0.5))
    with pytest.raises(ValidationError):
        build(pacing_scale=-1)
    with pytest.raises(ValidationError):
        build(api_port=0)


# ───────────────────────────── get_settings ─────────────────────────────


def test_get_settings_is_cached_until_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_settings()
    assert get_settings() is first
    monkeypatch.setenv("TAYYARI_API_PORT", "9999")
    assert get_settings().api_port == 8787  # still the cached object
    get_settings.cache_clear()
    assert get_settings().api_port == 9999


def test_module_holds_no_settings_instance_and_reads_no_env_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in vars(settings_module).values():
        assert not isinstance(value, Settings)
    # nothing is frozen at import: a variable set now is seen by the next construction
    monkeypatch.setenv("TAYYARI_SYNTH_MODEL", "late")
    assert build().synth_model == "late"


def test_summary_lists_presence_not_values() -> None:
    s = build(anthropic_api_key="k")
    summary = s.summary()
    assert summary["env"] == "local"
    assert summary["anthropic_key"] is True
    assert summary["jev_key"] is False
    assert summary["jev_enabled"] is False  # enabled in config but no key
    assert summary["models"] == {
        "reader": "claude-haiku-4-5",
        "synth": "claude-sonnet-5-5",
        "chat": "claude-sonnet-5-5",
    }
