"""``defaults.toml``, override precedence, validation, the kill switch, and the registry / spec checks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tayyari.core.settings import JevSettings, Settings
from tayyari.decisions.config import DecisionConfigStore, load_defaults
from tayyari.decisions.registry import DecisionRegistry
from tayyari.decisions.spec import (
    DecisionConfig,
    DecisionPack,
    DecisionSpec,
    PackResult,
    question_key,
    split_key,
)
from tayyari.llm.jev import JevQuestion
from tests.unit.decisions.support import bound_spec, choice_spec, make_pack, noul_spec

FIRST_READ = [
    "career_family",
    "career_stage",
    "depth",
    "leads_or_mentors",
    "public_proof",
    "measured_outcomes",
    "ai_experience",
    "progression_promoted",
    "progression_scope",
    "progression_switch",
    "resume_like",
]
EXPECTED_IDS = {
    *(f"first_read.{name}" for name in FIRST_READ),
    "triage.text_quality",
    "triage.about_person_work",
    "triage.page_type",
    "triage.mainly_english",
    "evidence.support",
    "chat.web_gate",
    "skill.level",
    "requirement.have",
    "role.closest",
    "role.fit",
}


# ───────────────────────── the packaged defaults.toml ─────────────────────────


def test_defaults_toml_lists_every_spec_of_this_build() -> None:
    assert set(load_defaults()) == EXPECTED_IDS


def test_first_read_is_primary_with_no_claude_fallback() -> None:
    defaults = load_defaults()
    for name in FIRST_READ:
        entry = defaults[f"first_read.{name}"]
        assert entry["mode"] == "primary" and entry["fallback"] == "skip", name
        assert entry["timeout_s"] == 2.5
    assert defaults["first_read.career_family"]["threshold"] == 0.45
    assert defaults["first_read.public_proof"]["threshold"] == 0.6


def test_triage_evidence_and_gate_defaults() -> None:
    d = load_defaults()
    for spec_id in ("triage.text_quality", "triage.about_person_work", "triage.page_type"):
        assert d[spec_id]["mode"] == "primary" and d[spec_id]["fallback"] == "claude"
    assert d["triage.mainly_english"]["fallback"] == "skip"  # "none" in the design table
    assert d["evidence.support"] == {
        "mode": "primary",
        "threshold": 0.8,
        "fallback": "claude",
        "audit_rate": 0.05,
        "timeout_s": 4.0,
    }
    assert d["chat.web_gate"]["mode"] == "primary" and d["chat.web_gate"]["fallback"] == "claude"
    assert d["chat.web_gate"]["audit_rate"] == 0.05 and d["chat.web_gate"]["threshold"] == 0.7


def test_shadow_specs_are_shadow_and_cannot_be_promoted_by_accident() -> None:
    d = load_defaults()
    for spec_id in ("skill.level", "requirement.have", "role.closest", "role.fit"):
        assert d[spec_id]["mode"] == "shadow" and d[spec_id]["threshold"] == 1.0, spec_id


def test_the_packaged_file_builds_a_store_for_real_settings(settings: Settings) -> None:
    store = DecisionConfigStore.from_settings(settings)
    assert store.jev_enabled and not store.send_pii
    assert store.configured_ids() == EXPECTED_IDS


def test_a_malformed_defaults_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="not valid TOML"):
        load_defaults("[[broken")
    with pytest.raises(ValueError, match="unknown field"):
        load_defaults('["a.b"]\nmodee = "off"\n')
    with pytest.raises(ValueError, match="threshold"):
        load_defaults('["a.b"]\nthreshold = 2\n')


def test_top_level_scalars_are_ignored_and_every_table_is_a_spec_id() -> None:
    assert load_defaults('version = 1\n["x.y"]\nmode = "off"\n') == {"x.y": {"mode": "off"}}


# ───────────────────────── precedence ─────────────────────────


def test_the_spec_default_applies_when_nothing_else_does() -> None:
    spec = noul_spec(threshold=0.55, fallback="skip", audit_rate=0.1, timeout_s=3.0)
    assert DecisionConfigStore().for_spec(spec) == spec.default


def test_defaults_toml_beats_the_spec_default_field_by_field() -> None:
    spec = noul_spec(threshold=0.55, audit_rate=0.1)
    store = DecisionConfigStore({"t.flag": {"threshold": 0.9, "fallback": "skip"}})
    cfg = store.for_spec(spec)
    assert (cfg.threshold, cfg.fallback) == (0.9, "skip")
    assert (cfg.mode, cfg.audit_rate, cfg.timeout_s) == (
        "primary",
        0.1,
        2.5,
    )  # untouched fields keep the spec's


def test_env_overrides_beat_defaults_toml() -> None:
    spec = noul_spec()
    store = DecisionConfigStore(
        {"t.flag": {"mode": "primary", "threshold": 0.9}}, {"t.flag": {"mode": "shadow", "audit_rate": 0.5}}
    )
    cfg = store.for_spec(spec)
    assert (cfg.mode, cfg.threshold, cfg.audit_rate) == ("shadow", 0.9, 0.5)


def test_wildcards_apply_broad_to_narrow_and_the_exact_id_wins() -> None:
    store = DecisionConfigStore(
        None,
        {
            "t.flag": {"threshold": 0.11},
            "*": {"mode": "off", "threshold": 0.99, "timeout_s": 9},
            "t.*": {"mode": "shadow", "threshold": 0.5},
        },
    )
    flag = store.for_spec(noul_spec("t.flag"))
    other = store.for_spec(noul_spec("t.other"))
    elsewhere = store.for_spec(noul_spec("u.other", pack="u"))
    assert (flag.mode, flag.threshold, flag.timeout_s) == ("shadow", 0.11, 9.0)
    assert (other.mode, other.threshold) == ("shadow", 0.5)
    assert (elsewhere.mode, elsewhere.threshold) == ("off", 0.99)


def test_a_family_wildcard_matches_the_dot_boundary_only() -> None:
    store = DecisionConfigStore(None, {"first_read.*": {"mode": "off"}})
    assert store.for_spec(noul_spec("first_read.depth", pack="first_read")).mode == "off"
    assert store.for_spec(noul_spec("first_reader.depth", pack="x")).mode == "primary"


def test_fallback_none_is_an_alias_for_skip() -> None:
    cfg = DecisionConfigStore({"t.flag": {"fallback": "none"}}).for_spec(noul_spec())
    assert cfg.fallback == "skip"


def test_fallback_model_can_be_set_and_cleared() -> None:
    assert (
        DecisionConfigStore({"t.flag": {"fallback_model": "claude-sonnet-5-5"}})
        .for_spec(noul_spec())
        .fallback_model
        == "claude-sonnet-5-5"
    )
    assert (
        DecisionConfigStore(None, {"t.flag": {"fallback_model": None}}).for_spec(noul_spec()).fallback_model
        is None
    )


@pytest.mark.parametrize(
    "table",
    [
        {"mode": "maybe"},
        {"mode": 1},
        {"threshold": 1.5},
        {"threshold": -0.1},
        {"threshold": "high"},
        {"threshold": True},
        {"audit_rate": 2},
        {"fallback": "gpt"},
        {"timeout_s": 0},
        {"timeout_s": "slow"},
        {"fallback_model": ""},
        {"fallback_model": 5},
        {"colour": "red"},
    ],
)
def test_invalid_values_fail_when_the_store_is_built_not_in_the_middle_of_a_run(
    table: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        DecisionConfigStore(None, {"t.flag": table})
    with pytest.raises(ValueError):
        DecisionConfigStore({"t.flag": table})


def test_unknown_ids_are_reported_by_the_registry() -> None:
    registry = DecisionRegistry([make_pack(noul_spec(), choice_spec())])
    store = DecisionConfigStore(
        {"t.flag": {"mode": "off"}, "stale.entry": {"mode": "off"}},
        {"t.pick": {}, "typo.evidence": {}, "t.*": {}},
    )
    assert registry.unknown_config_ids(store) == ["stale.entry", "typo.evidence"]
    assert store.override_patterns() == ["t.*", "t.pick", "typo.evidence"]


# ───────────────────────── kill switch and settings ─────────────────────────


def test_the_kill_switch_comes_from_the_jev_settings(make_settings: Callable[..., Settings]) -> None:
    assert DecisionConfigStore.from_settings(make_settings()).jev_enabled is True
    off = make_settings(jev=JevSettings(enabled=False))
    assert DecisionConfigStore.from_settings(off).jev_enabled is False
    assert DecisionConfigStore.from_settings(make_settings(jev=JevSettings(send_pii=True))).send_pii is True


def test_settings_overrides_win_over_defaults_toml(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(decision_overrides={"evidence.support": {"mode": "shadow", "threshold": 0.95}})
    store = DecisionConfigStore.from_settings(settings)
    spec = noul_spec("evidence.support", pack="evidence")
    cfg = store.for_spec(spec)
    assert (cfg.mode, cfg.threshold) == ("shadow", 0.95)
    assert (cfg.fallback, cfg.audit_rate, cfg.timeout_s) == ("claude", 0.05, 4.0)  # still from defaults.toml


def test_the_env_json_override_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> None:
    monkeypatch.setenv(
        "TAYYARI_DECISION_OVERRIDES", '{"evidence.support": {"mode": "off"}, "first_read.*": {"mode": "off"}}'
    )
    monkeypatch.setenv("TAYYARI_JEV__ENABLED", "false")
    store = DecisionConfigStore.from_settings(make_settings())
    assert store.for_spec(noul_spec("evidence.support", pack="evidence")).mode == "off"
    assert store.for_spec(noul_spec("first_read.depth", pack="first_read")).mode == "off"
    assert store.for_spec(noul_spec("chat.web_gate", pack="chat")).mode == "primary"
    assert store.jev_enabled is False


def test_a_bad_env_override_fails_at_startup(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValueError):
        DecisionConfigStore.from_settings(
            make_settings(decision_overrides={"evidence.support": {"mode": "of"}})
        )


# ───────────────────────── DecisionConfig, DecisionSpec, DecisionPack, registry ─────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "x", "threshold": 0.5},
        {"mode": "off", "threshold": 1.1},
        {"mode": "off", "threshold": 0.5, "audit_rate": -1},
        {"mode": "off", "threshold": 0.5, "fallback": "gpt"},
        {"mode": "off", "threshold": 0.5, "timeout_s": 0},
        {"mode": "off", "threshold": 0.5, "fallback_model": " "},
    ],
)
def test_decision_config_validates_itself(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        DecisionConfig(**kwargs)


def test_decision_config_defaults() -> None:
    cfg = DecisionConfig(mode="primary", threshold=0.8)
    assert (cfg.audit_rate, cfg.fallback, cfg.timeout_s, cfg.fallback_model) == (0.0, "claude", 2.5, None)


def test_a_spec_needs_a_clean_id_a_version_and_a_pack() -> None:
    good = noul_spec()
    kwargs: dict[str, Any] = {
        "id": "a.b",
        "version": "1",
        "pack": "a",
        "question": JevQuestion.noul("q"),
        "interpret": lambda a: True,
        "neutral": False,
        "default": good.default,
    }
    DecisionSpec(**kwargs)
    for bad in ({"id": ""}, {"id": "a#b"}, {"version": " "}, {"pack": ""}):
        with pytest.raises(ValueError):
            DecisionSpec(**{**kwargs, **bad})
    with pytest.raises(TypeError):
        DecisionSpec("a.b", "1", "a", JevQuestion.noul("q"), None, lambda a: True, False, good.default)  # type: ignore[misc]


def test_a_pack_checks_its_specs() -> None:
    a, b = noul_spec("t.a"), noul_spec("t.b")
    pack: DecisionPack[str] = DecisionPack(id="t", specs=(a, b), build_state=lambda s: s)
    assert pack.spec("t.b") is b
    with pytest.raises(KeyError):
        pack.spec("t.zzz")
    with pytest.raises(ValueError, match="twice"):
        DecisionPack(id="t", specs=(a, a), build_state=lambda s: s)
    with pytest.raises(ValueError, match="belongs to pack"):
        DecisionPack(id="other", specs=(a,), build_state=lambda s: s)
    with pytest.raises(ValueError):
        DecisionPack(id=" ", specs=(), build_state=lambda s: s)


def test_the_registry_keeps_ids_unique_and_looks_them_up() -> None:
    registry = DecisionRegistry()
    first = make_pack(noul_spec("t.a"), choice_spec("t.b"))
    registry.register(first)
    assert registry.pack("t") is first and registry.spec("t.b").id == "t.b"
    assert "t.a" in registry and "t.zzz" not in registry
    assert [s.id for s in registry.specs()] == ["t.a", "t.b"] and registry.packs() == [first]
    with pytest.raises(ValueError, match="already registered"):
        registry.register(make_pack(noul_spec("t.c")))  # same pack id
    with pytest.raises(ValueError, match="already registered"):
        registry.register(make_pack(noul_spec("t.a", pack="u"), pack_id="u"))  # same spec id
    with pytest.raises(KeyError, match="unknown decision spec"):
        registry.spec("nope")
    with pytest.raises(KeyError, match="unknown decision pack"):
        registry.pack("nope")


def test_question_keys_round_trip() -> None:
    assert question_key("skill.level") == "skill.level"
    assert question_key("skill.level", "python") == "skill.level#python"
    assert split_key("skill.level#python") == ("skill.level", "python")
    assert split_key("requirement.have#role-1#2") == ("requirement.have", "role-1#2")
    assert split_key("evidence.support") == ("evidence.support", None)


def test_pack_result_helpers_on_an_empty_result() -> None:
    empty = PackResult("t")
    assert len(empty) == 0 and list(empty) == [] and empty.values() == [] and empty.get("x") is None
    assert empty.used_value("x", 3) == 3 and empty.instances("x") == {}


def test_bound_spec_helper_builds_questions_from_bindings() -> None:
    spec = bound_spec()
    assert spec.bind is not None
    assert spec.bind({"name": "sql"}).instructions == {"question": "Is it present?", "name": "sql"}
