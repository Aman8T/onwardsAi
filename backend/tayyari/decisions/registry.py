"""The registry of packs: one place that knows every spec of the build, so ids stay unique and config typos are caught.

Packs register themselves into a registry the container builds (there is no module-level global, so tests
use their own). The registry checks the ids; `unknown_config_ids` compares them with a `DecisionConfigStore`
so a misspelled override or a stale ``defaults.toml`` entry shows up in the startup log instead of being
silently ignored.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .config import DecisionConfigStore
from .spec import DecisionPack, DecisionSpec


class DecisionRegistry:
    def __init__(self, packs: Iterable[DecisionPack[Any]] = ()) -> None:
        self._packs: dict[str, DecisionPack[Any]] = {}
        self._specs: dict[str, DecisionSpec[Any]] = {}
        for pack in packs:
            self.register(pack)

    def register(self, pack: DecisionPack[Any]) -> None:
        """Add a pack. Raises ``ValueError`` for a duplicate pack id or a spec id that is already registered."""
        if pack.id in self._packs:
            raise ValueError(f"pack {pack.id!r} is already registered")
        clash = sorted(spec.id for spec in pack.specs if spec.id in self._specs)
        if clash:
            raise ValueError(f"spec id(s) already registered by another pack: {clash}")
        self._packs[pack.id] = pack
        for spec in pack.specs:
            self._specs[spec.id] = spec

    def pack(self, pack_id: str) -> DecisionPack[Any]:
        try:
            return self._packs[pack_id]
        except KeyError:
            raise KeyError(f"unknown decision pack {pack_id!r}; known: {sorted(self._packs)}") from None

    def spec(self, spec_id: str) -> DecisionSpec[Any]:
        try:
            return self._specs[spec_id]
        except KeyError:
            raise KeyError(f"unknown decision spec {spec_id!r}; known: {sorted(self._specs)}") from None

    def packs(self) -> list[DecisionPack[Any]]:
        return list(self._packs.values())

    def specs(self) -> list[DecisionSpec[Any]]:
        return list(self._specs.values())

    def __contains__(self, spec_id: object) -> bool:
        return spec_id in self._specs

    def unknown_config_ids(self, store: DecisionConfigStore) -> list[str]:
        """Spec ids that ``store`` configures (defaults or overrides) but no registered spec has, sorted."""
        return sorted(store.configured_ids() - set(self._specs))


__all__ = ["DecisionRegistry"]
