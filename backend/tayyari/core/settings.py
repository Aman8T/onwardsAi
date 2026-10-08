"""Typed application settings.

Sources, highest priority first: values passed to ``Settings(...)``, real environment variables, then the
``.env`` files (repo root, then ``backend/``; the later file wins). Missing files are ignored, which is the
normal case in the cloud. Nothing here reads the environment at import time: call `get_settings()` (cached)
or build ``Settings(...)`` yourself, and call ``get_settings.cache_clear()`` after changing the environment.

Variable names:
    third-party names, no prefix      ANTHROPIC_API_KEY, JEV_API_KEY (or TYPESAFE_API_KEY), GITHUB_TOKEN,
                                      DATABASE_URL, LOGFIRE_TOKEN
    our own, prefix ``TAYYARI_``      TAYYARI_SYNTH_MODEL, TAYYARI_API_PORT, TAYYARI_READER_THINKING ...
    nested groups, ``__`` delimiter   TAYYARI_GUARD__DAILY_CEILING_USD, TAYYARI_JEV__ENABLED ...
    JSON                              TAYYARI_DECISION_OVERRIDES='{"evidence.support":{"mode":"shadow"}}'

An empty (or whitespace-only) value counts as unset everywhere.
"""

from __future__ import annotations

import os
import tempfile
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Literal

import platformdirs
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, SettingsConfigDict

from .dburl import DbConfig, normalise_database_url
from .paths import BACKEND_DIR, REPO_ROOT

Env = Literal["local", "prod", "test"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
SynthCache = Literal["off", "system", "system+data"]
CacheTtl = Literal["5m", "1h"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

_CLOUD_MARKERS = ("FASTAPICLOUD_DEPLOYMENT_ID", "FASTAPI_PUBLIC_URL")


def detect_env() -> Env:
    """``"prod"`` when FastAPI Cloud's deployment variables are present in the environment, else ``"local"``."""
    return "prod" if any(os.environ.get(name, "").strip() for name in _CLOUD_MARKERS) else "local"


def _default_data_dir(data: dict[str, Any]) -> Path:
    """Per-user data folder locally (outside the repo and OneDrive); the temp folder in prod (ephemeral)."""
    if data.get("env") == "prod":
        return Path(tempfile.gettempdir()) / "tayyari"
    return Path(platformdirs.user_data_dir("tayyari", appauthor=False))


class ModelNames(BaseModel):
    """The model id used for each role."""

    model_config = ConfigDict(frozen=True)

    reader: str
    synth: str
    chat: str


class JevSettings(BaseModel):
    """Jev (TypeSafe's decision model): `TAYYARI_JEV__*`."""

    enabled: bool = True
    model: str = "jev-1.13.0"
    """Pinned: every logged decision records it, so replays against a newer Jev never mix with old rows."""
    base_url: str = "https://api.typesafe.ai"
    timeout_display_s: float = Field(default=2.5, gt=0)
    """Budget for decisions that drive what the person sees live (first read)."""
    timeout_evidence_s: float = Field(default=4.0, gt=0)
    """Budget for the post-synthesis evidence checks."""
    max_retries: int = Field(default=1, ge=0)
    send_pii: bool = False
    """False: emails, phones and address lines are masked (`core.redact`) before any text goes to Jev."""


class GuardSettings(BaseModel):
    """Cost and abuse protection: `TAYYARI_GUARD__*`."""

    enabled: bool = True
    analyze_per_minute: int = Field(default=2, ge=0)
    analyze_per_hour: int = Field(default=6, ge=0)
    analyze_per_day: int = Field(default=15, ge=0)
    analyze_global_per_hour: int = Field(default=60, ge=0)
    chat_per_minute: int = Field(default=20, ge=0)
    chat_per_hour: int = Field(default=120, ge=0)
    chat_per_day: int = Field(default=400, ge=0)
    daily_ceiling_usd: float = Field(default=20.0, ge=0)
    """Soft ceiling on spend per UTC day; reaching it switches AI off until midnight."""
    est_analyze_usd: float = Field(default=0.40, ge=0)
    est_chat_usd: float = Field(default=0.10, ge=0)
    ceiling_grace: float = Field(default=1.25, ge=1.0)
    """A run already past the readers may finish while spend is under ceiling x grace."""
    portfolio_per_minute: int = Field(default=4, ge=0)
    portfolio_per_hour: int = Field(default=12, ge=0)
    portfolio_per_day: int = Field(default=30, ge=0)
    portfolio_global_per_hour: int = Field(default=120, ge=0)
    """Portfolio generations and section rewrites (the paid portfolio calls) across all clients per hour."""
    est_portfolio_usd: float = Field(default=0.12, ge=0)
    """Spend reserved before a portfolio generation: Haiku fact reads (one per source), three concurrent Sonnet copy
    calls (fixed slots, experience, projects), one re-ask and the Jev claim checks."""
    chat_global_per_hour: int = Field(default=600, ge=0)
    """Backstop on chat turns per hour across all clients (shared counter)."""
    chat_streams_per_ip: int = Field(default=2, ge=0)
    """Concurrent chat streams one client may hold open (per replica)."""
    chat_streams_per_process: int = Field(default=24, ge=0)
    """Concurrent chat streams one replica serves."""
    upload_inflight_bytes: int = Field(default=64 * 1024 * 1024, ge=0)
    """Upload bytes one replica buffers at once across all requests; beyond it new uploads get 503."""
    upload_read_timeout_s: float = Field(default=30.0, gt=0)
    """Deadline for reading a whole request body (slow or stalled uploads are cut off)."""
    deep_health_min_interval_s: float = Field(default=5.0, ge=0)
    """/api/health/deep touches the database at most this often; in between it answers from the last result."""
    trusted_proxy_hops: int = Field(default=3, ge=0)
    """Proxies in front of the app; the client IP is that many entries from the right of X-Forwarded-For.

    Measured on FastAPI Cloud (Wave 0c canary, 2026-10-07): client, Cloudflare edge, then two internal hops.
    """
    client_ip_header: str | None = "cf-connecting-ip"
    """Read the client IP from this header when present (Cloudflare sets it in front of FastAPI Cloud and
    overwrites any client-sent value); otherwise fall back to X-Forwarded-For, then the socket peer."""

    @field_validator("client_ip_header", mode="before")
    @classmethod
    def _blank_header_is_none(cls, v: object) -> object:
        """Blank keeps the default (like every other variable); "none" or "off" disables the header."""
        if isinstance(v, str):
            if not v.strip():
                raise PydanticUseDefault()
            if v.strip().lower() in {"none", "off", "false", "0"}:
                return None
        return v


class ConcurrencySettings(BaseModel):
    """Parallelism and timeouts: `TAYYARI_CONCURRENCY__*`."""

    reader_concurrency: int = Field(default=6, ge=1)
    reader_timeout_s: float = Field(default=90.0, gt=0)
    max_concurrent_analyses: int = Field(default=4, ge=1)
    """Analyses run at once per replica; more are told to retry."""


class RetentionSettings(BaseModel):
    """How long stored data is kept: `TAYYARI_RETENTION__*`."""

    profile_days: int = Field(default=60, ge=1)
    """Profiles (and their chats) not opened for this long are deleted; sample rows are kept."""
    payload_days: int = Field(default=30, ge=1)
    """Jev request/response payloads and states are purged after this long."""
    metadata_days: int = Field(default=180, ge=1)
    """Decision and call metadata (no text) is kept this long."""


_SECRET_FIELDS = ("anthropic_api_key", "jev_api_key", "github_token", "logfire_token")


class Settings(BaseSettings):
    """All configuration, typed. See the module docstring for where values come from."""

    model_config = SettingsConfigDict(
        env_prefix="TAYYARI_",
        env_nested_delimiter="__",
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    env: Env = Field(default_factory=detect_env)
    """``local``, ``prod`` (detected on FastAPI Cloud) or ``test``. `TAYYARI_ENV` overrides the detection."""
    log_level: LogLevel = "INFO"

    # credentials and endpoints: third-party names, no TAYYARI_ prefix
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )
    jev_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("JEV_API_KEY", "TYPESAFE_API_KEY")
    )
    github_token: SecretStr | None = Field(default=None, validation_alias=AliasChoices("GITHUB_TOKEN"))
    logfire_token: SecretStr | None = Field(default=None, validation_alias=AliasChoices("LOGFIRE_TOKEN"))
    database_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL"), repr=False, exclude=True
    )
    """Carries the password, so it is kept out of ``repr(settings)``; log `db.safe_url` instead."""

    # models (the legacy TAYYARI_READER_MODEL / _SYNTH_MODEL / _CHAT_MODEL names keep working)
    reader_model: str = "claude-haiku-4-5"
    synth_model: str = "claude-sonnet-5-5"
    chat_model: str = "claude-sonnet-5-5"
    reader_thinking: bool = False
    """Extended thinking for the Haiku readers; they extract rather than reason, so it is off."""
    synth_thinking: bool = True
    """Adaptive thinking for the synthesis. False sends ``{"type": "disabled"}`` to models that allow it (Haiku 5.5);
    a model that cannot switch thinking off (Sonnet 5.5) keeps it. Readers, portfolio calls and decision fallbacks
    never think on such models: they extract or fill short slots, and thinking only adds latency there."""
    chat_thinking: bool = True
    """Adaptive thinking for the career chat (same rule as `synth_thinking`)."""
    synth_effort: Effort = "medium"
    chat_effort: Effort = "medium"
    reader_max_tokens: int = Field(default=8000, ge=256)
    synth_max_tokens: int = Field(default=32_000, ge=256)
    """Includes Sonnet 5.5's adaptive thinking tokens, which count against max_tokens."""
    chat_max_tokens: int = Field(default=8000, ge=256)

    api_port: int = Field(default=8787, ge=1, le=65535)
    """The dev server's port (the Vite proxy targets 8787)."""

    jev: JevSettings = Field(default_factory=JevSettings)
    guard: GuardSettings = Field(default_factory=GuardSettings)
    concurrency: ConcurrencySettings = Field(default_factory=ConcurrencySettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)

    synth_hints: bool = False
    """Feed the first-read results to the synthesizer as hints (off: they are stored for comparison only)."""
    synth_cache: SynthCache = "system"
    chat_cache_ttl: CacheTtl = "5m"
    pacing_scale: float = Field(default=1.0, ge=0)
    """Multiplies the delays that space out UI events; 0 in tests and fast demos."""
    decision_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Emergency overrides of ``decisions/defaults.toml``, e.g. ``{"evidence.support": {"mode": "off"}}``."""

    data_dir: Path = Field(default_factory=_default_data_dir)
    """Where local files (the SQLite database) live. Created lazily by whoever writes there."""

    @field_validator(*_SECRET_FIELDS, "database_url", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        """Empty and whitespace-only values count as unset; surrounding whitespace is dropped."""
        if isinstance(v, SecretStr):
            v = v.get_secret_value()
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @property
    def models(self) -> ModelNames:
        """The model ids as one small frozen object (the `models` field of /api/health and of every Profile)."""
        return ModelNames(reader=self.reader_model, synth=self.synth_model, chat=self.chat_model)

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @cached_property
    def db(self) -> DbConfig:
        """The database configuration derived from ``database_url`` (SQLite under ``data_dir`` when unset)."""
        return normalise_database_url(self.database_url, data_dir=self.data_dir, env=self.env)

    def summary(self) -> dict[str, Any]:
        """Non-secret facts for the startup log line: which keys are present, never their values."""
        return {
            "env": self.env,
            "models": self.models.model_dump(),
            "anthropic_key": self.anthropic_api_key is not None,
            "jev_key": self.jev_api_key is not None,
            "jev_enabled": self.jev.enabled and self.jev_api_key is not None,
            "jev_model": self.jev.model,
            "github_token": self.github_token is not None,
            "database": self.db.safe_url,
            "storage": "ephemeral" if self.db.ephemeral else self.db.kind,
            "daily_ceiling_usd": self.guard.daily_ceiling_usd if self.guard.enabled else None,
            "decision_overrides": sorted(self.decision_overrides),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, built on first use. Tests call ``get_settings.cache_clear()``."""
    return Settings()


__all__ = [
    "CacheTtl",
    "ConcurrencySettings",
    "Effort",
    "Env",
    "GuardSettings",
    "JevSettings",
    "LogLevel",
    "ModelNames",
    "RetentionSettings",
    "Settings",
    "SynthCache",
    "detect_env",
    "get_settings",
]
