"""`SpeechService`: what the status says and why, what a piece of an answer costs and when it is refused, and the voice's cache."""

from __future__ import annotations

import pytest

from tayyari.contract.speech import SpeechStatus, TranscribeResult
from tayyari.core.errors import PayloadTooLarge
from tayyari.speech.engine import (
    REJECTED_MESSAGE,
    UNAVAILABLE_MESSAGE,
    Heard,
    SpeechRejected,
    SpeechUnavailable,
)
from tayyari.speech.service import (
    BYTES_PER_SECOND,
    DAILY_MESSAGE,
    NOTHING_TO_SAY,
    SESSION_MESSAGE,
    TOO_LARGE_MESSAGE,
)
from tests.fakes.speech import MP3, FakeSpeechEngine
from tests.unit.speech.support import OTHER_SESSION, SESSION, WEBM, make_rig

PIECE = b"\x1a\x45\xdf\xa3" + b"\x00" * 3996  # 4,000 bytes: one second by size


# ───────────────────────── status ─────────────────────────


async def test_status_with_a_key_has_both_on_and_names_the_limits() -> None:
    rig = make_rig(max_segment_seconds=25, max_segment_bytes=900_000)
    status = await rig.service.status(SESSION)
    assert status == SpeechStatus(
        stt=True, tts=True, provider="sarvam", maxSegmentSeconds=25, maxSegmentBytes=900_000
    )
    assert status.to_dict() == {
        "stt": True,
        "tts": True,
        "provider": "sarvam",
        "maxSegmentSeconds": 25,
        "maxSegmentBytes": 900_000,
    }


async def test_status_without_a_key_says_not_configured_for_both() -> None:
    rig = make_rig(silent=True)
    status = await rig.service.status(SESSION)
    assert (status.stt, status.tts, status.provider) == (False, False, None)
    assert (status.sttReason, status.ttsReason) == ("not_configured", "not_configured")
    assert rig.kv.values == {}  # nothing to meter, nothing read


async def test_status_when_switched_off_says_so_even_with_a_key() -> None:
    status = await make_rig(enabled=False).service.status(SESSION)
    assert (status.stt, status.tts, status.sttReason, status.ttsReason) == (
        False,
        False,
        "switched_off",
        "switched_off",
    )


async def test_a_cap_of_zero_switches_that_kind_off_and_leaves_the_other_on() -> None:
    status = await make_rig(tts_daily_chars=0).service.status(SESSION)
    assert (status.stt, status.tts, status.ttsReason, status.sttReason) == (True, False, "switched_off", None)
    status = await make_rig(stt_session_seconds=0).service.status(SESSION)
    assert (status.stt, status.tts, status.sttReason) == (False, True, "switched_off")


async def test_status_reports_the_daily_budget_when_the_day_is_used_up() -> None:
    rig = make_rig(stt_daily_seconds=60, tts_daily_chars=100)
    await rig.service.meter.charge(OTHER_SESSION, stt_s=60, tts_chars=100)
    status = await rig.service.status(SESSION)  # another session spent it: the day is shared
    assert (status.stt, status.tts, status.sttReason, status.ttsReason) == (
        False,
        False,
        "daily_budget",
        "daily_budget",
    )
    assert status.provider is None


async def test_status_reports_the_session_budget_and_only_for_that_session() -> None:
    rig = make_rig(stt_session_seconds=20, tts_session_chars=50)
    await rig.service.meter.charge(SESSION, stt_s=20)
    mine, theirs = await rig.service.status(SESSION), await rig.service.status(OTHER_SESSION)
    assert (mine.stt, mine.sttReason, mine.tts, mine.provider) == (False, "session_budget", True, "sarvam")
    assert (theirs.stt, theirs.tts) == (True, True)


async def test_status_without_a_session_ignores_the_session_budget() -> None:
    rig = make_rig(stt_session_seconds=20)
    await rig.service.meter.charge(SESSION, stt_s=20)
    status = await rig.service.status()
    assert status.stt is True and status.sttReason is None


async def test_the_daily_reason_wins_when_both_budgets_are_gone() -> None:
    rig = make_rig(stt_daily_seconds=10, stt_session_seconds=10)
    await rig.service.meter.charge(SESSION, stt_s=10)
    assert (await rig.service.status(SESSION)).sttReason == "daily_budget"


# ───────────────────────── billing ─────────────────────────


@pytest.mark.parametrize(
    ("size", "claimed", "billed"),
    [
        (BYTES_PER_SECOND, 1.0, 1),
        (BYTES_PER_SECOND, 0.0, 1),  # nothing claimed: at least a second
        (10, 0.2, 1),  # a sliver: still a second
        (BYTES_PER_SECOND, 6.0, 6),  # the claim wins when the body is small for it
        (BYTES_PER_SECOND, 5.2, 6),  # fractions round up
        (BYTES_PER_SECOND * 9, 1.0, 9),  # a short claim on a big body is billed by its size
        (BYTES_PER_SECOND * 9 + 1, 1.0, 10),
        (BYTES_PER_SECOND * 400, 2.0, 30),  # never more than the longest piece
        (BYTES_PER_SECOND, 500.0, 30),
        (BYTES_PER_SECOND, -4.0, 1),
        (BYTES_PER_SECOND, float("nan"), 1),
        (BYTES_PER_SECOND, float("inf"), 1),
    ],
)
def test_billed_seconds(size: int, claimed: float, billed: int) -> None:
    assert make_rig().service.billed_seconds(size, claimed) == billed


def test_a_heavier_format_is_not_billed_several_times_over_by_its_size() -> None:
    service = make_rig().service
    # a 9-second clip: 150 KB as mp3, 288 KB as 16 kHz WAV, 36 KB as Opus in WebM
    assert service.billed_seconds(150_000, 9.0, "mp3") == 10
    assert service.billed_seconds(288_000, 9.0, "wav") == 9
    assert service.billed_seconds(36_000, 9.0, "webm") == 9
    # a short claimed length on a big Opus body is still billed by its size
    assert service.billed_seconds(80_000, 1.0, "webm") == 20


def test_the_longest_piece_follows_the_setting() -> None:
    assert make_rig(max_segment_seconds=12).service.billed_seconds(BYTES_PER_SECOND * 100, 90.0) == 12


# ───────────────────────── transcribing ─────────────────────────


async def test_a_piece_is_sent_charged_and_returned_as_tidy_text() -> None:
    engine = FakeSpeechEngine(Heard("  I cut the  load time\nfrom 4 to 1.2 seconds. ", "en-IN"))
    rig = make_rig(engine=engine)
    result = await rig.service.transcribe(SESSION, PIECE, WEBM, 3.4, ["Kitebird", "LCP"])
    assert result == TranscribeResult(
        text="I cut the load time from 4 to 1.2 seconds.", seconds=4, languageCode="en-IN"
    )
    (sent,) = engine.transcribed
    assert (sent.audio, sent.mime, sent.keyterms) == (PIECE, WEBM, ("Kitebird", "LCP"))
    assert rig.used_today() == {"stt_s": 4, "tts_chars": 0}


async def test_a_short_claimed_duration_on_a_big_body_is_billed_by_size() -> None:
    rig = make_rig()
    big = b"\x00" * (BYTES_PER_SECOND * 12)
    result = await rig.service.transcribe(SESSION, big, WEBM, 1.0, [])
    assert result.seconds == 12
    assert rig.used_today()["stt_s"] == 12
    assert (await rig.service.meter.remaining(SESSION)).stt_session == rig.settings.stt_session_seconds - 12


async def test_nothing_heard_is_empty_text_and_still_billed() -> None:
    rig = make_rig(engine=FakeSpeechEngine(Heard("", None)))
    result = await rig.service.transcribe(SESSION, PIECE, WEBM, 2.0, [])
    assert result.text == "" and result.languageCode is None and result.seconds == 2
    assert result.to_dict() == {"text": "", "seconds": 2}
    assert rig.used_today()["stt_s"] == 2


async def test_key_terms_are_cleaned_again_before_they_reach_the_engine() -> None:
    rig = make_rig()
    await rig.service.transcribe(SESSION, PIECE, WEBM, 1.0, ["  Kite  UI ", "kite ui", "x" * 65, ""])
    assert rig.engine is not None and rig.engine.transcribed[0].keyterms == ("Kite UI",)


@pytest.mark.parametrize("mime", ["audio/flac", "text/plain", "", "application/octet-stream"])
async def test_an_audio_type_we_do_not_take_is_rejected_before_any_call_or_charge(mime: str) -> None:
    rig = make_rig()
    with pytest.raises(SpeechRejected) as caught:
        await rig.service.transcribe(SESSION, PIECE, mime, 1.0, [])
    assert caught.value.user_message == REJECTED_MESSAGE
    assert rig.engine is not None and rig.engine.calls == 0 and rig.used_today() == {}


async def test_empty_audio_is_rejected_before_any_call_or_charge() -> None:
    rig = make_rig()
    with pytest.raises(SpeechRejected):
        await rig.service.transcribe(SESSION, b"", WEBM, 1.0, [])
    assert rig.engine is not None and rig.engine.calls == 0 and rig.used_today() == {}


async def test_audio_over_the_size_cap_is_refused_with_a_413_before_any_call_or_charge() -> None:
    rig = make_rig(max_segment_bytes=20_000)
    assert len(PIECE) <= 20_000
    await rig.service.transcribe(SESSION, b"\x00" * 20_000, WEBM, 1.0, [])  # exactly at the cap is fine
    assert rig.engine is not None
    rig.engine.transcribed.clear()
    with pytest.raises(PayloadTooLarge) as caught:
        await rig.service.transcribe(SESSION, b"\x00" * 20_001, WEBM, 1.0, [])
    assert caught.value.status == 413 and caught.value.user_message == TOO_LARGE_MESSAGE
    assert rig.engine.calls == 0


async def test_without_an_engine_a_piece_is_unavailable_and_nothing_is_charged() -> None:
    rig = make_rig(silent=True)
    with pytest.raises(SpeechUnavailable) as caught:
        await rig.service.transcribe(SESSION, PIECE, WEBM, 1.0, [])
    assert caught.value.user_message == UNAVAILABLE_MESSAGE and caught.value.status == 503
    assert rig.used_today() == {}


async def test_when_switched_off_a_piece_is_unavailable() -> None:
    rig = make_rig(enabled=False)
    with pytest.raises(SpeechUnavailable):
        await rig.service.transcribe(SESSION, PIECE, WEBM, 1.0, [])
    assert rig.engine is not None and rig.engine.calls == 0


async def test_the_daily_budget_stops_pieces_with_its_own_sentence() -> None:
    rig = make_rig(stt_daily_seconds=5)
    await rig.service.transcribe(SESSION, PIECE, WEBM, 5.0, [])  # the last of the day's seconds
    with pytest.raises(SpeechUnavailable) as caught:
        await rig.service.transcribe(OTHER_SESSION, PIECE, WEBM, 1.0, [])
    assert caught.value.user_message == DAILY_MESSAGE
    assert rig.engine is not None and len(rig.engine.transcribed) == 1
    assert rig.used_today()["stt_s"] == 5  # the refused piece was not charged


async def test_the_session_budget_stops_that_session_only() -> None:
    rig = make_rig(stt_session_seconds=6)
    await rig.service.transcribe(SESSION, PIECE, WEBM, 6.0, [])
    with pytest.raises(SpeechUnavailable) as caught:
        await rig.service.transcribe(SESSION, PIECE, WEBM, 1.0, [])
    assert caught.value.user_message == SESSION_MESSAGE
    assert (await rig.service.transcribe(OTHER_SESSION, PIECE, WEBM, 1.0, [])).seconds == 1


async def test_a_piece_may_run_the_budget_a_little_over_because_it_is_soft() -> None:
    rig = make_rig(stt_session_seconds=10)
    await rig.service.transcribe(SESSION, PIECE, WEBM, 8.0, [])
    await rig.service.transcribe(SESSION, PIECE, WEBM, 8.0, [])  # 2 seconds were left, 8 are billed
    assert (await rig.service.status(SESSION)).sttReason == "session_budget"


async def test_an_engine_failure_propagates_and_the_charge_stands() -> None:
    engine = FakeSpeechEngine(transcribe_error=SpeechUnavailable(detail="timeout"))
    rig = make_rig(engine=engine)
    with pytest.raises(SpeechUnavailable):
        await rig.service.transcribe(SESSION, PIECE, WEBM, 2.0, [])
    assert (
        rig.used_today()["stt_s"] == 2
    )  # Sarvam may have worked on it; the soft cap errs on the careful side


async def test_a_rejected_piece_propagates_as_a_400() -> None:
    rig = make_rig(engine=FakeSpeechEngine(transcribe_error=SpeechRejected()))
    with pytest.raises(SpeechRejected) as caught:
        await rig.service.transcribe(SESSION, PIECE, WEBM, 2.0, [])
    assert caught.value.status == 400


# ───────────────────────── speaking ─────────────────────────

LINE = "Walk me through the Kitebird checkout rebuild. What did you change first, and why?"


async def test_a_line_is_charged_by_its_characters_and_returned_as_audio() -> None:
    rig = make_rig()
    audio = await rig.service.speak(SESSION, LINE, 1.0)
    assert audio == MP3
    assert rig.engine is not None and [(s.text, s.pace) for s in rig.engine.spoken] == [(LINE, 1.0)]
    assert rig.used_today() == {"stt_s": 0, "tts_chars": len(LINE)}


async def test_a_line_spoken_before_comes_from_the_cache_and_is_not_charged_twice() -> None:
    rig = make_rig()
    first = await rig.service.speak(SESSION, LINE, 1.0)
    again = await rig.service.speak(SESSION, LINE, 1.0)
    other_session = await rig.service.speak(OTHER_SESSION, LINE, 1.0)  # the cache is not per session
    assert first == again == other_session
    assert rig.engine is not None and len(rig.engine.spoken) == 1
    assert rig.used_today()["tts_chars"] == len(LINE)
    assert (await rig.service.meter.remaining(OTHER_SESSION)).tts_session == rig.settings.tts_session_chars


async def test_the_cache_ignores_spacing_but_not_pace_or_words() -> None:
    rig = make_rig()
    await rig.service.speak(SESSION, "Say it  again.", 1.0)
    await rig.service.speak(SESSION, " Say it again. ", None)  # None is the normal pace
    assert rig.engine is not None and len(rig.engine.spoken) == 1
    await rig.service.speak(SESSION, "Say it again.", 0.85)  # slower: a different recording
    await rig.service.speak(SESSION, "Say it once more.", 1.0)
    assert [(s.text, s.pace) for s in rig.engine.spoken] == [
        ("Say it again.", 1.0),
        ("Say it again.", 0.85),
        ("Say it once more.", 1.0),
    ]


async def test_a_cached_line_is_still_served_after_the_budget_is_gone() -> None:
    rig = make_rig(tts_daily_chars=len(LINE))
    await rig.service.speak(SESSION, LINE, 1.0)
    assert (await rig.service.status(SESSION)).ttsReason == "daily_budget"
    assert await rig.service.speak(SESSION, LINE, 1.0) == MP3  # free, so not refused
    with pytest.raises(SpeechUnavailable) as caught:
        await rig.service.speak(SESSION, "A line nobody has heard yet.", 1.0)
    assert caught.value.user_message == DAILY_MESSAGE


async def test_the_session_budget_stops_new_lines_for_that_session() -> None:
    rig = make_rig(tts_session_chars=30)
    await rig.service.speak(
        SESSION, "A short line, thirty characters long!", 1.0
    )  # more than 30: the cap is soft
    with pytest.raises(SpeechUnavailable) as caught:
        await rig.service.speak(SESSION, "Another line.", 1.0)
    assert caught.value.user_message == SESSION_MESSAGE
    assert await rig.service.speak(OTHER_SESSION, "Another line.", 1.0) == MP3


async def test_the_cache_keeps_only_the_most_recent_lines() -> None:
    rig = make_rig(tts_cache_items=2)
    for text in ("One.", "Two.", "Three."):
        await rig.service.speak(SESSION, text, 1.0)
    assert rig.engine is not None
    rig.engine.spoken.clear()
    await rig.service.speak(SESSION, "Three.", 1.0)
    await rig.service.speak(SESSION, "Two.", 1.0)
    assert rig.engine.spoken == []  # both still remembered
    await rig.service.speak(SESSION, "One.", 1.0)  # the oldest was dropped
    assert [s.text for s in rig.engine.spoken] == ["One."]


async def test_with_no_cache_every_line_is_spoken_and_charged() -> None:
    rig = make_rig(tts_cache_items=0)
    await rig.service.speak(SESSION, "Again.", 1.0)
    await rig.service.speak(SESSION, "Again.", 1.0)
    assert rig.engine is not None and len(rig.engine.spoken) == 2
    assert rig.used_today()["tts_chars"] == 2 * len("Again.")


async def test_pace_is_kept_within_half_and_double_speed() -> None:
    rig = make_rig()
    await rig.service.speak(SESSION, "Slow.", 0.1)
    await rig.service.speak(SESSION, "Fast.", 9.0)
    assert rig.engine is not None and [s.pace for s in rig.engine.spoken] == [0.5, 2.0]


async def test_nothing_to_say_is_rejected() -> None:
    rig = make_rig()
    with pytest.raises(SpeechRejected) as caught:
        await rig.service.speak(SESSION, "  \n ", 1.0)
    assert caught.value.user_message == NOTHING_TO_SAY
    assert rig.engine is not None and rig.engine.calls == 0


async def test_without_an_engine_a_line_is_unavailable() -> None:
    rig = make_rig(silent=True)
    with pytest.raises(SpeechUnavailable) as caught:
        await rig.service.speak(SESSION, LINE, 1.0)
    assert caught.value.user_message == UNAVAILABLE_MESSAGE
    assert rig.used_today() == {}


async def test_a_failed_line_is_not_cached_and_the_next_try_calls_the_engine_again() -> None:
    engine = FakeSpeechEngine(speak_error=SpeechUnavailable(detail="503"))
    rig = make_rig(engine=engine)
    with pytest.raises(SpeechUnavailable):
        await rig.service.speak(SESSION, LINE, 1.0)
    engine.speak_error = None
    assert await rig.service.speak(SESSION, LINE, 1.0) == MP3
    assert len(engine.spoken) == 2


async def test_ensure_says_why_before_anything_is_read() -> None:
    rig = make_rig(stt_daily_seconds=3)
    await rig.service.ensure("stt", SESSION)
    await rig.service.meter.charge(SESSION, stt_s=3)
    with pytest.raises(SpeechUnavailable) as caught:
        await rig.service.ensure("stt", SESSION)
    assert caught.value.user_message == DAILY_MESSAGE
    await rig.service.ensure("tts", SESSION)  # the voice has its own budget
