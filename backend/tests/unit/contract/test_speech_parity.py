"""tayyari.contract.speech must match shared/speech.ts exactly: the interfaces (field names, optionality, nested shapes) and the
literal unions. The TypeScript file imports nothing, so it is read on its own.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tayyari.contract import speech as sp
from tayyari.core.paths import REPO_ROOT
from tests.unit.contract.test_ts_parity import TsTypes, diff, model_shape

SPEECH_TS = REPO_ROOT / "shared" / "speech.ts"

if not SPEECH_TS.is_file():
    pytest.skip("the frontend sources are not part of this checkout", allow_module_level=True)


def _ts() -> TsTypes:
    return TsTypes(SPEECH_TS.read_text(encoding="utf-8"))


INTERFACE_MODELS: dict[str, type[BaseModel]] = {
    "SpeechStatus": sp.SpeechStatus,
    "TranscribeResult": sp.TranscribeResult,
    "SpeakRequest": sp.SpeakRequest,
}

UNIONS = {
    "SpeechProvider": sp.SpeechProvider,
    "SpeechOffReason": sp.SpeechOffReason,
}


def test_every_interface_has_a_mirror() -> None:
    assert set(_ts().interfaces) == set(INTERFACE_MODELS)


@pytest.mark.parametrize("name", sorted(INTERFACE_MODELS))
def test_interface_matches_its_model(name: str) -> None:
    assert diff(_ts().interface(name), model_shape(INTERFACE_MODELS[name])) == []


def test_the_model_class_names_follow_the_interface_names() -> None:
    for name, cls in INTERFACE_MODELS.items():
        assert cls.__name__ == name


@pytest.mark.parametrize("name", sorted(UNIONS))
def test_the_unions_match(name: str) -> None:
    assert _ts().shape(name) == ("lit", frozenset(get_args(UNIONS[name])))


def test_every_exported_literal_union_is_checked() -> None:
    assert set(_ts().aliases) == set(UNIONS)


def test_the_four_reasons_speech_can_be_off() -> None:
    assert set(get_args(sp.SpeechOffReason)) == {
        "not_configured",
        "switched_off",
        "daily_budget",
        "session_budget",
    }


def test_optional_fields_are_omitted_on_the_wire() -> None:
    status = sp.SpeechStatus(
        stt=False,
        tts=False,
        sttReason="not_configured",
        ttsReason="not_configured",
        maxSegmentSeconds=30,
        maxSegmentBytes=1_500_000,
    )
    assert status.to_dict() == {
        "stt": False,
        "tts": False,
        "sttReason": "not_configured",
        "ttsReason": "not_configured",
        "maxSegmentSeconds": 30,
        "maxSegmentBytes": 1_500_000,
    }
    assert sp.TranscribeResult(text="hello", seconds=2).to_dict() == {"text": "hello", "seconds": 2}
    assert sp.TranscribeResult(text="", seconds=1, languageCode="en-IN").to_dict() == {
        "text": "",
        "seconds": 1,
        "languageCode": "en-IN",
    }


def test_the_speak_request_refuses_overlong_text_ids_and_a_pace_outside_half_to_double() -> None:
    ok = {"profileId": "p", "sessionId": "s", "text": "x" * sp.SPEAK_TEXT_MAX_CHARS}
    assert sp.SpeakRequest.model_validate(ok).pace is None
    for bad in (
        {**ok, "text": "x" * (sp.SPEAK_TEXT_MAX_CHARS + 1)},
        {**ok, "profileId": "p" * (sp.PROFILE_ID_MAX_CHARS + 1)},
        {**ok, "sessionId": "s" * (sp.SESSION_ID_MAX_CHARS + 1)},
        {**ok, "pace": 0.49},
        {**ok, "pace": 2.01},
    ):
        with pytest.raises(ValidationError):
            sp.SpeakRequest.model_validate(bad)
    assert sp.SpeakRequest.model_validate({**ok, "pace": 0.5}).pace == 0.5
    assert sp.SpeakRequest.model_validate({**ok, "pace": 2.0}).pace == 2.0


def test_a_drifted_typescript_file_is_caught() -> None:
    """Known-bad: an optional made required, a field renamed and a union member dropped each show up as a difference."""
    source = SPEECH_TS.read_text(encoding="utf-8")
    optional = TsTypes(source.replace("pace?: number;", "pace: number;"))
    assert diff(optional.interface("SpeakRequest"), model_shape(sp.SpeakRequest)) != []
    renamed = TsTypes(source.replace("  maxSegmentBytes: number;", "  maxSegmentSize: number;"))
    assert diff(renamed.interface("SpeechStatus"), model_shape(sp.SpeechStatus)) != []
    narrowed = TsTypes(source.replace(" | 'daily_budget'", ""))
    assert narrowed.shape("SpeechOffReason") != ("lit", frozenset(get_args(sp.SpeechOffReason)))
