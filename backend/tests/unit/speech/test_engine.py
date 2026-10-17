"""The engine seam: which audio types a piece may be sent as (and the codec each is named for), the key terms an engine may
send, and the two errors' sentences."""

from __future__ import annotations

import pytest

from tayyari.core.errors import BadRequest, ServiceUnavailable
from tayyari.speech.engine import (
    CODECS,
    KEYTERM_MAX_CHARS,
    KEYTERM_MAX_COUNT,
    REJECTED_MESSAGE,
    UNAVAILABLE_MESSAGE,
    SpeechRejected,
    SpeechUnavailable,
    clean_keyterms,
    codec_for,
    media_type,
    require_codec,
)


@pytest.mark.parametrize(
    ("mime", "codec"),
    [
        ("audio/webm", "webm"),
        ("audio/webm;codecs=opus", "webm"),
        ("audio/webm; codecs=opus", "webm"),
        ("Audio/WebM;Codecs=Opus", "webm"),
        ("audio/ogg", "ogg"),
        ("audio/ogg;codecs=opus", "ogg"),
        ("audio/mp4", "mp4"),
        ("audio/mp4;codecs=mp4a.40.2", "mp4"),
        ("audio/x-m4a", "mp4"),
        ("audio/mpeg", "mp3"),
        ("audio/wav", "wav"),
        ("audio/x-wav", "wav"),
        ("  audio/wav  ", "wav"),
    ],
)
def test_an_audio_type_maps_to_the_codec_sarvam_is_told(mime: str, codec: str) -> None:
    assert codec_for(mime) == codec
    assert require_codec(mime) == codec


@pytest.mark.parametrize(
    "mime",
    [
        "",
        "audio/flac",
        "audio/aac",
        "audio/webm2",
        "video/webm",
        "text/plain",
        "application/json",
        "application/octet-stream",
    ],
)
def test_anything_else_is_rejected_before_any_call(mime: str) -> None:
    assert codec_for(mime) is None
    with pytest.raises(SpeechRejected) as caught:
        require_codec(mime)
    assert caught.value.status == 400 and caught.value.user_message == REJECTED_MESSAGE


def test_the_codec_table_is_exactly_the_five_types_the_contract_names() -> None:
    assert set(CODECS.values()) == {"webm", "ogg", "mp4", "mp3", "wav"}
    assert {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav"} <= set(CODECS)


def test_the_media_type_drops_its_parameters_and_case() -> None:
    assert media_type("Audio/WebM;codecs=opus") == "audio/webm"
    assert media_type(" audio/ogg ; codecs=opus") == "audio/ogg"


def test_the_two_errors_carry_the_sentences_the_room_shows() -> None:
    assert (
        UNAVAILABLE_MESSAGE
        == "Sarvam's speech isn't available right now. Your browser's speech will take over."
    )
    assert REJECTED_MESSAGE == "We couldn't make out that piece. Say it again, or type it."
    unavailable, rejected = SpeechUnavailable(), SpeechRejected()
    assert isinstance(unavailable, ServiceUnavailable) and unavailable.status == 503
    assert unavailable.to_body() == {"error": UNAVAILABLE_MESSAGE}
    assert isinstance(rejected, BadRequest) and rejected.status == 400
    assert rejected.to_body() == {"error": REJECTED_MESSAGE}


def test_a_detail_is_for_logs_and_never_the_body() -> None:
    err = SpeechUnavailable(detail="transcribe 429 insufficient_quota_error")
    assert err.detail == "transcribe 429 insufficient_quota_error"
    assert "429" not in err.to_body()["error"]


# ───────────────────────── key terms ─────────────────────────


def test_key_terms_are_trimmed_squeezed_and_deduplicated_in_order() -> None:
    assert clean_keyterms(["  Kitebird ", "LCP", "kitebird", "Kite   UI", "", "   ", "LCP"]) == [
        "Kitebird",
        "LCP",
        "Kite UI",
    ]


def test_a_key_term_is_at_most_sixty_four_characters() -> None:
    fits, too_long = "x" * KEYTERM_MAX_CHARS, "x" * (KEYTERM_MAX_CHARS + 1)
    assert clean_keyterms([too_long, fits]) == [fits]


def test_at_most_fifty_key_terms_are_kept_and_the_first_win() -> None:
    terms = [f"Term{i}" for i in range(80)]
    cleaned = clean_keyterms(terms)
    assert len(cleaned) == KEYTERM_MAX_COUNT == 50
    assert cleaned == terms[:50]


def test_there_may_be_no_key_terms_at_all() -> None:
    assert clean_keyterms([]) == []
