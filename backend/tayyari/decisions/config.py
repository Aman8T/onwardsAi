"""Decision configuration: ``defaults.toml``, environment overrides and the Jev kill switch, merged per spec.

Precedence for one spec, lowest first:

    1. the spec's own ``default`` (a fallback in code)
    2. ``decisions/defaults.toml`` (version-controlled; what a new Jev promotes into)
    3. ``Settings.decision_overrides`` / ``TAYYARI_DECISION_OVERRIDES`` (JSON, wins over everything)

An override key is a spec id, a family wildcard such as ``"first_read.*"`` or ``"*"`` for everything; the
broader keys apply first, so an exact id always wins. Every value is validated when the store is built (an
override typo fails at startup, not in the middle of an analysis).

The kill switch is ``Settings.jev.enabled``: with it off the store reports ``jev_enabled=False`` and the runner
never calls Jev. Primary decisions then go to their Claude fallback with escalation reason ``jev_disabled``
(or stay neutral when the fallback is ``skip``); shadow decisions do nothing.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import fields
from importlib import resources
from typing import TYPE_CHECKING, Any

from .spec import FALLBACKS, MODES, DecisionConfig, DecisionSpec

if TYPE_CHECKING:
    from ..core.settings import Settings

DEFAULTS_RESOURCE = "defaults.toml"
_FIELD_NAMES: frozenset[str] = frozenset(f.name for f in fields(DecisionConfig))
_FALLBACK_ALIASES = {"none": "skip"}


def _number(value: Any, name: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{where}: {name} must be a number, got {value!r}")
    return float(value)


def _clean_fields(raw: Mapping[str, Any], where: str) -> dict[str, Any]:
    """Validate one table of config fields and return them with normalised types."""
    unknown = sorted(set(raw) - _FIELD_NAMES)
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {unknown}; valid fields are {sorted(_FIELD_NAMES)}")
    out: dict[str, Any] = {}
    for name, value in raw.items():
        if name == "mode":
            if value not in MODES:
                raise ValueError(f"{where}: mode must be one of {MODES}, got {value!r}")
            out[name] = value
        elif name == "fallback":
            value = _FALLBACK_ALIASES.get(value, value) if isinstance(value, str) else value
            if value not in FALLBACKS:
                raise ValueError(f"{where}: fallback must be one of {FALLBACKS}, got {value!r}")
            out[name] = value
        elif name in ("threshold", "audit_rate"):
            number = _number(value, name, where)
            if not 0.0 <= number <= 1.0:
                raise ValueError(f"{where}: {name} must be between 0 and 1, got {value!r}")
            out[name] = number
        elif name == "timeout_s":
            number = _number(value, name, where)
            if not number > 0:
                raise ValueError(f"{where}: timeout_s must be positive, got {value!r}")
            out[name] = number
        else:  # fallback_model
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{where}: fallback_model must be a non-blank string, got {value!r}")
            out[name] = value
    return out


def load_defaults(text: str | None = None) -> dict[str, dict[str, Any]]:
    """Parse ``defaults.toml`` (the packaged file, or ``text``) into ``{spec id: validated fields}``.

    Top-level scalar keys (``version``) are ignored; every table is a spec id. Raises ``ValueError`` for a
    malformed or invalid file, so a bad edit is caught by the tests and at startup.
    """
    if text is None:
        text = resources.files(__package__).joinpath(DEFAULTS_RESOURCE).read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{DEFAULTS_RESOURCE} is not valid TOML: {exc}") from exc
    out: dict[str, dict[str, Any]] = {}
    for spec_id, table in data.items():
        if not isinstance(table, dict):
            continue
        out[spec_id] = _clean_fields(table, f"{DEFAULTS_RESOURCE} [{spec_id}]")
    return out


def _matches(pattern: str, spec_id: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return spec_id.startswith(pattern[:-1])
    return pattern == spec_id


class DecisionConfigStore:
    """The effective `DecisionConfig` of every spec, plus the process-wide switches.

    ``defaults`` and ``overrides`` map a spec id (overrides also ``"family.*"`` and ``"*"``) to config fields;
    both are validated here. ``jev_enabled`` is the kill switch; ``send_pii`` says whether contact data may go to
    Jev (the runner masks it before sending, and before logging, when it is False).
    """

    def __init__(
        self,
        defaults: Mapping[str, Mapping[str, Any]] | None = None,
        overrides: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        jev_enabled: bool = True,
        send_pii: bool = False,
    ) -> None:
        self._defaults = {
            spec_id: _clean_fields(table, f"defaults [{spec_id}]")
            for spec_id, table in (defaults or {}).items()
        }
        self._overrides = {
            pattern: _clean_fields(table, f"override [{pattern}]")
            for pattern, table in (overrides or {}).items()
        }
        self.jev_enabled = jev_enabled
        self.send_pii = send_pii

    @classmethod
    def from_settings(
        cls, settings: Settings, *, defaults: Mapping[str, Mapping[str, Any]] | None = None
    ) -> DecisionConfigStore:
        """The packaged ``defaults.toml`` (or ``defaults``), ``settings.decision_overrides`` and the kill switch."""
        return cls(
            load_defaults() if defaults is None else defaults,
            settings.decision_overrides,
            jev_enabled=settings.jev.enabled,
            send_pii=settings.jev.send_pii,
        )

    def for_spec(self, spec: DecisionSpec[Any]) -> DecisionConfig:
        """The merged config of ``spec`` (see the module docstring for the order)."""
        merged: dict[str, Any] = {f.name: getattr(spec.default, f.name) for f in fields(DecisionConfig)}
        merged.update(self._defaults.get(spec.id, {}))
        # broad overrides first ("*", then "family.*" shortest to longest), the exact id last
        matching = sorted(
            (p for p in self._overrides if p != spec.id and _matches(p, spec.id)), key=lambda p: (len(p), p)
        )
        for pattern in matching:
            merged.update(self._overrides[pattern])
        merged.update(self._overrides.get(spec.id, {}))
        return DecisionConfig(**merged)

    def configured_ids(self) -> set[str]:
        """Exact spec ids named by the defaults or the overrides (wildcards excluded), for typo checks."""
        exact = {p for p in self._overrides if not p.endswith("*")}
        return set(self._defaults) | exact

    def override_patterns(self) -> list[str]:
        return sorted(self._overrides)


__all__ = ["DEFAULTS_RESOURCE", "DecisionConfigStore", "load_defaults"]
