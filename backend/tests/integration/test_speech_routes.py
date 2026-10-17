"""Speech for practice interviews, end to end through the real app over a real SQLite file: the profile and session each route
may open, the order of the checks, the windows and budgets, the byte caps, `Cache-Control: no-store`, and that no audio is
stored anywhere.

The interview is a stub handed in through the container override (it answers `speech_context` the way the real service does;
that is tested in tests/unit/interview/test_speech_context.py) and the engine is `FakeSpeechEngine`, so the real
`SpeechService`, meter (over the real ``kv`` table), rate limiter and routes are what run. Nothing here calls Sarvam, Claude
or the network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from tayyari.api.routes.interview import SESSION_NOT_FOUND
from tayyari.api.routes.speech import LINE_NOT_FOUND, UNSUPPORTED_AUDIO
from tayyari.contract.models import Profile
from tayyari.contract.speech import SpeechStatus, TranscribeResult
from tayyari.contract.stored import StoredContext
from tayyari.core.errors import ServiceUnavailable
from tayyari.core.settings import GuardSettings, Settings, SpeechSettings
from tayyari.interview.errors import InterviewEnded
from tayyari.interview.speech import SpeechContext
from tayyari.interview.text import fold
from tayyari.speech.engine import REJECTED_MESSAGE, UNAVAILABLE_MESSAGE, SpeechRejected, SpeechUnavailable
from tayyari.speech.service import DAILY_MESSAGE, SESSION_MESSAGE, TOO_LARGE_MESSAGE
from tayyari.storage.tables import RateLimitHitRow, metadata
from tests.fakes.discover import profile_of
from tests.fakes.speech import MP3, FakeSpeechEngine
from tests.integration.support import Env, roomy_guard, running

MakeSettings = Callable[..., Settings]
SESSION_COOKIE = "tayyari_session"
FRONTEND_ID = profile_of("frontend").id
SID = "c" * 32
ENDED = "e" * 32
OTHER_PROFILES = "d" * 32
GONE = "f" * 32
OFF = "Practice is switched off right now."
WEBM = "audio/webm;codecs=opus"
MARKER = b"UNIQUE-AUDIO-MARKER-9f3a1c"
PIECE = b"\x1a\x45\xdf\xa3" + MARKER + b"\x00" * 3000

QUESTION = "Walk me through the Kitebird checkout rebuild. What did you change first, and why?"
FOLLOW_UP = "When the cart update failed, what did the person see?"
LATER = "Which part of the checkout was yours, in one sentence?"
"""A line the interview has not said yet (it may well say it next)."""
KEYTERMS = ("Headout", "Kitebird", "LCP")


def context(*lines: str, active: bool = True, keyterms: tuple[str, ...] = KEYTERMS) -> SpeechContext:
    return SpeechContext(active=active, lines=frozenset(fold(line) for line in lines), keyterms=keyterms)


@dataclass
class StubInterview:
    """Stands in for `InterviewService` where the speech routes use it: the kill switch and `speech_context`."""

    on: bool = True
    contexts: dict[tuple[str, str], SpeechContext] = field(default_factory=dict)
    asked: list[tuple[str, str]] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return self.on

    def require_enabled(self) -> None:
        if not self.on:
            raise ServiceUnavailable(OFF)

    async def speech_context(self, profile: Profile, session_id: str) -> SpeechContext | None:
        self.asked.append((profile.id, session_id))
        return self.contexts.get((profile.id, session_id))


@dataclass
class Rig:
    env: Env
    stub: StubInterview
    engine: FakeSpeechEngine | None

    @property
    def client(self) -> httpx.AsyncClient:
        return self.env.client

    async def profile(self, *, owner: str | None = None) -> str:
        found = profile_of("frontend")
        container = self.env.container
        await container.repos.profiles.save(
            found.id, found.to_dict(), StoredContext(), goal=found.goal, owner_hash=None, is_sample=False
        )
        if owner is not None:
            await container.repos.users.claim_profile(owner, found.id)
        return found.id

    async def account(self, email: str = "ana@example.com") -> tuple[str, dict[str, str]]:
        container = self.env.container
        user = await container.repos.users.sign_in_email(email=email, now=container.clock.now())
        token, _ = await container.auth.sessions.start(user.id, ip_hash=None, user_agent=None)
        return user.id, {"cookie": f"{SESSION_COOKIE}={token}"}

    async def status(self, *, headers: dict[str, str] | None = None, **params: str) -> httpx.Response:
        return await self.client.get(
            "/api/speech/status", params={"profileId": FRONTEND_ID, **params}, headers=headers or {}
        )

    async def transcribe(
        self,
        audio: bytes = PIECE,
        *,
        session: str = SID,
        profile: str = FRONTEND_ID,
        seconds: str | None = "3.2",
        content_type: str | None = WEBM,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        params = {"profileId": profile, "sessionId": session}
        if seconds is not None:
            params["seconds"] = seconds
        sent = dict(headers or {})
        if content_type is not None:
            sent["content-type"] = content_type
        return await self.client.post("/api/speech/transcribe", params=params, content=audio, headers=sent)

    async def speak(
        self,
        text: str = QUESTION,
        *,
        session: str = SID,
        profile: str = FRONTEND_ID,
        pace: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        body: dict[str, Any] = {"profileId": profile, "sessionId": session, "text": text}
        if pace is not None:
            body["pace"] = pace
        return await self.client.post("/api/speech/speak", json=body, headers=headers or {})

    def at(self, ip: str) -> dict[str, str]:
        return {"cf-connecting-ip": ip}


@pytest.fixture
def start(build_settings: MakeSettings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def run(
        *, engine: bool = True, fake: FakeSpeechEngine | None = None, **overrides: Any
    ) -> AsyncIterator[Rig]:
        stub = StubInterview(on=overrides.pop("on", True))
        stub.contexts[(FRONTEND_ID, SID)] = context(QUESTION, FOLLOW_UP)
        stub.contexts[(FRONTEND_ID, ENDED)] = context(QUESTION, active=False)
        stub.contexts[("someone-elses-profile", OTHER_PROFILES)] = context("Another person's question.")
        overrides.setdefault("guard", roomy())
        chosen = (fake if fake is not None else FakeSpeechEngine()) if engine else None
        async with running(build_settings(**overrides), interview=stub, speech_engine=chosen) as env:
            yield Rig(env, stub, chosen)

    return run


def roomy(**overrides: Any) -> GuardSettings:
    return roomy_guard(**overrides)


def is_no_store(response: httpx.Response) -> bool:
    return response.headers.get("cache-control") == "no-store"


def files_under(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file()]


# ───────────────────────── status ─────────────────────────


async def test_status_without_a_session_is_the_days_view_and_is_never_cached(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        response = await rig.status()
        assert response.status_code == 200 and is_no_store(response)
        assert response.json() == {
            "stt": True,
            "tts": True,
            "provider": "sarvam",
            "maxSegmentSeconds": 30,
            "maxSegmentBytes": 1_500_000,
        }
        assert SpeechStatus.model_validate(response.json()).provider == "sarvam"
        assert rig.stub.asked == []  # no session named, so no session looked at


async def test_status_for_a_session_follows_its_budget(start: Any) -> None:
    async with start(speech=SpeechSettings(stt_session_seconds=4)) as rig:
        await rig.profile()
        assert (await rig.status(sessionId=SID)).json()["stt"] is True
        assert (await rig.transcribe(seconds="4")).status_code == 200
        after = (await rig.status(sessionId=SID)).json()
        assert (after["stt"], after["tts"], after["sttReason"]) == (False, True, "session_budget")
        assert "ttsReason" not in after
        other = await rig.status()  # no session: only the day counts
        assert other.json()["stt"] is True


async def test_status_says_why_when_there_is_no_key_or_it_is_switched_off(start: Any) -> None:
    async with start(engine=False) as rig:
        await rig.profile()
        body = (await rig.status(sessionId=SID)).json()
        assert (body["stt"], body["tts"], body["sttReason"], body["ttsReason"]) == (
            False,
            False,
            "not_configured",
            "not_configured",
        )
        assert "provider" not in body
    async with start(speech=SpeechSettings(enabled=False)) as rig:
        await rig.profile()
        body = (await rig.status()).json()
        assert (body["sttReason"], body["ttsReason"]) == ("switched_off", "switched_off")


async def test_status_says_the_daily_budget_is_used_up(start: Any) -> None:
    async with start(speech=SpeechSettings(stt_daily_seconds=3, tts_daily_chars=20)) as rig:
        await rig.profile()
        await rig.transcribe(seconds="3")
        await rig.speak()
        body = (await rig.status(sessionId=SID)).json()
        assert (body["stt"], body["tts"], body["sttReason"], body["ttsReason"]) == (
            False,
            False,
            "daily_budget",
            "daily_budget",
        )


async def test_status_for_a_session_that_is_not_this_profiles_is_a_404(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        for session in (GONE, OTHER_PROFILES, "not-an-id", "A" * 32, "a" * 31):
            response = await rig.status(sessionId=session)
            assert response.status_code == 404 and response.json() == {"error": SESSION_NOT_FOUND}, session


async def test_status_for_an_unknown_profile_is_a_404(start: Any) -> None:
    async with start() as rig:
        for bad in ("", "nope", "f" * 32, "x" * 200):
            response = await rig.client.get("/api/speech/status", params={"profileId": bad})
            assert response.status_code == 404 and response.json() == {"error": "Profile not found"}, bad


async def test_a_claimed_profiles_speech_is_for_its_owner_only(start: Any) -> None:
    async with start() as rig:
        owner, owner_headers = await rig.account("owner@example.com")
        _, other_headers = await rig.account("someone@example.com")
        await rig.profile(owner=owner)
        for headers in (other_headers, {}):
            assert (
                await rig.client.get("/api/speech/status", params={"profileId": FRONTEND_ID}, headers=headers)
            ).status_code == 404
            assert (await rig.transcribe(headers=headers)).status_code == 404
            assert (await rig.speak(headers=headers)).status_code == 404
        assert rig.engine is not None and rig.engine.calls == 0
        assert (await rig.transcribe(headers=owner_headers)).status_code == 200
        assert (await rig.speak(headers=owner_headers)).status_code == 200


# ───────────────────────── transcribing ─────────────────────────


async def test_a_piece_comes_back_as_text_and_the_engine_got_it_with_the_interviews_own_terms(
    start: Any,
) -> None:
    async with start() as rig:
        await rig.profile()
        response = await rig.transcribe(seconds="3.2")
        assert response.status_code == 200 and is_no_store(response)
        assert response.headers["content-type"].startswith("application/json")
        result = TranscribeResult.model_validate(response.json())
        assert result == TranscribeResult(
            text="I rebuilt the checkout and measured the gain.", seconds=4, languageCode="en-IN"
        )
        assert rig.engine is not None
        (sent,) = rig.engine.transcribed
        assert (sent.audio, sent.mime, sent.keyterms) == (PIECE, "audio/webm", KEYTERMS)
        assert rig.stub.asked == [(FRONTEND_ID, SID)]


@pytest.mark.parametrize(
    "content_type",
    [
        "audio/webm",
        "audio/webm;codecs=opus",
        "audio/ogg;codecs=opus",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "Audio/WebM",
    ],
)
async def test_every_audio_type_the_contract_names_is_taken(start: Any, content_type: str) -> None:
    async with start() as rig:
        await rig.profile()
        assert (await rig.transcribe(content_type=content_type)).status_code == 200


async def test_the_seconds_are_optional_and_a_big_body_is_billed_by_its_size(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        assert (await rig.transcribe(seconds=None)).json()["seconds"] == 1
        big = b"\x00" * 40_000
        assert (await rig.transcribe(big, seconds="1")).json()["seconds"] == 10
        assert (await rig.transcribe(seconds="500")).json()[
            "seconds"
        ] == 30  # never more than the longest piece


@pytest.mark.parametrize("seconds", ["abc", "-1", "nan", "inf", "1e999"])
async def test_a_seconds_that_is_not_a_number_of_seconds_is_a_400(start: Any, seconds: str) -> None:
    async with start() as rig:
        await rig.profile()
        response = await rig.transcribe(seconds=seconds)
        assert response.status_code == 400 and "seconds" in response.json()["error"]
        assert rig.engine is not None and rig.engine.calls == 0


@pytest.mark.parametrize(
    "content_type",
    [None, "application/json", "text/plain", "audio/flac", "video/webm", "application/octet-stream"],
)
async def test_a_body_that_is_not_an_audio_type_we_take_is_a_415_before_it_is_read(
    start: Any, content_type: str | None
) -> None:
    async with start() as rig:
        await rig.profile()
        response = await rig.transcribe(content_type=content_type)
        assert response.status_code == 415 and response.json() == {"error": UNSUPPORTED_AUDIO}
        assert rig.engine is not None and rig.engine.calls == 0
        assert rig.stub.asked == []  # judged before the profile or the session


async def test_audio_over_the_byte_cap_is_a_413_whether_it_declares_its_size_or_not(start: Any) -> None:
    async with start(speech=SpeechSettings(max_segment_bytes=20_000)) as rig:
        await rig.profile()
        assert (await rig.transcribe(b"\x00" * 20_000)).status_code == 200  # exactly the cap is fine
        assert rig.engine is not None
        rig.engine.transcribed.clear()

        declared = await rig.transcribe(b"\x00" * 20_001)
        assert declared.status_code == 413 and declared.json() == {"error": TOO_LARGE_MESSAGE}

        async def trickle() -> AsyncIterator[bytes]:  # no Content-Length: counted as it arrives
            for _ in range(5):
                yield b"\x00" * 5_000

        chunked = await rig.client.post(
            "/api/speech/transcribe",
            params={"profileId": FRONTEND_ID, "sessionId": SID, "seconds": "2"},
            content=trickle(),
            headers={"content-type": WEBM},
        )
        assert chunked.status_code == 413 and chunked.json() == {"error": TOO_LARGE_MESSAGE}
        assert rig.engine.calls == 0


async def test_an_empty_body_is_a_400_that_asks_to_say_it_again(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        response = await rig.transcribe(b"")
        assert response.status_code == 400 and response.json() == {"error": REJECTED_MESSAGE}
        assert rig.engine is not None and rig.engine.calls == 0


async def test_a_session_that_is_gone_or_not_yours_is_a_404_and_an_ended_one_a_409(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        for session in (GONE, OTHER_PROFILES, "not-an-id", "A" * 32, ""):
            response = await rig.transcribe(session=session)
            assert response.status_code == 404 and response.json() == {"error": SESSION_NOT_FOUND}, session
        ended = await rig.transcribe(session=ENDED)
        assert ended.status_code == 409 and ended.json() == {"error": InterviewEnded.user_message}
        bad_profile = await rig.transcribe(profile="f" * 32)
        assert bad_profile.status_code == 404 and bad_profile.json() == {"error": "Profile not found"}
        assert rig.engine is not None and rig.engine.calls == 0


async def test_sarvam_being_down_is_a_503_with_the_sentence_the_room_shows(start: Any) -> None:
    fake = FakeSpeechEngine(transcribe_error=SpeechUnavailable(detail="429 insufficient_quota_error"))
    async with start(fake=fake) as rig:
        await rig.profile()
        response = await rig.transcribe()
        assert response.status_code == 503
        assert response.json() == {"error": UNAVAILABLE_MESSAGE}
        assert "insufficient_quota_error" not in response.text  # the detail is for logs


async def test_a_piece_sarvam_cannot_use_is_a_400_with_its_own_sentence(start: Any) -> None:
    fake = FakeSpeechEngine(transcribe_error=SpeechRejected(detail="422"))
    async with start(fake=fake) as rig:
        await rig.profile()
        response = await rig.transcribe()
        assert response.status_code == 400 and response.json() == {"error": REJECTED_MESSAGE}


async def test_without_a_key_it_is_a_503_before_the_window_is_touched_or_the_body_read(start: Any) -> None:
    async with start(engine=False, guard=roomy(speech_per_minute=1)) as rig:
        await rig.profile()
        for _ in range(3):  # more than the window: none of them counts
            response = await rig.transcribe()
            assert response.status_code == 503 and response.json() == {"error": UNAVAILABLE_MESSAGE}
        assert await rig.env.rows(RateLimitHitRow, RateLimitHitRow.bucket.like("speech%")) == []


async def test_the_daily_and_session_budgets_stop_pieces_with_their_own_sentences(start: Any) -> None:
    async with start(speech=SpeechSettings(stt_daily_seconds=6, stt_session_seconds=4)) as rig:
        await rig.profile()
        assert (await rig.transcribe(seconds="4")).status_code == 200
        session = await rig.transcribe(seconds="2")
        assert session.status_code == 503 and session.json() == {"error": SESSION_MESSAGE}
        other = "9" * 32
        rig.stub.contexts[(FRONTEND_ID, other)] = context(QUESTION)
        assert (
            await rig.transcribe(session=other, seconds="2")
        ).status_code == 200  # a fresh session, the day's last 2
        day = await rig.transcribe(session=other, seconds="1")
        assert day.status_code == 503 and day.json() == {"error": DAILY_MESSAGE}
        assert rig.engine is not None and len(rig.engine.transcribed) == 2


async def test_pieces_are_counted_in_the_speech_window_and_the_429_says_how_long(start: Any) -> None:
    async with start(guard=roomy(speech_per_minute=2)) as rig:
        await rig.profile()
        assert (await rig.transcribe()).status_code == 200
        assert (await rig.transcribe()).status_code == 200
        refused = await rig.transcribe()
        assert refused.status_code == 429
        assert refused.json()["error"].startswith("Too many speech requests at once. Please wait ")
        assert 1 <= int(refused.headers["retry-after"]) <= 60
        assert (
            rig.engine is not None and len(rig.engine.transcribed) == 2
        )  # the refused one never reached Sarvam
        elsewhere = await rig.transcribe(headers=rig.at("198.51.100.7"))
        assert elsewhere.status_code == 200  # another address has its own window


async def test_a_piece_is_shed_in_the_chat_bucket_before_anything_is_read(start: Any) -> None:
    async with start(guard=roomy(chat_per_minute=1)) as rig:  # an in-memory bucket of two
        await rig.profile()
        assert (await rig.transcribe()).status_code == 200
        assert (await rig.transcribe()).status_code == 200
        hammered = await rig.transcribe()
        assert hammered.status_code == 429
        assert hammered.json()["error"].startswith("You're sending messages quickly.")
        assert rig.stub.asked == [(FRONTEND_ID, SID)] * 2


async def test_a_switched_off_practice_stops_pieces_and_lines_but_not_the_status(start: Any) -> None:
    async with start(on=False) as rig:
        await rig.profile()
        assert (await rig.transcribe()).json() == {"error": OFF}
        assert (await rig.speak()).json() == {"error": OFF}
        assert (await rig.status()).status_code == 200
        assert rig.engine is not None and rig.engine.calls == 0


async def test_the_audio_is_never_stored_anywhere(start: Any, build_settings: MakeSettings) -> None:
    settings = build_settings(guard=roomy())
    stub = StubInterview()
    stub.contexts[(FRONTEND_ID, SID)] = context(QUESTION)
    async with running(settings, interview=stub, speech_engine=FakeSpeechEngine()) as env:
        rig = Rig(env, stub, None)
        await rig.profile()
        assert (await rig.transcribe()).status_code == 200
        assert (await rig.speak()).status_code == 200
        await env.settle()
        # Every table of the database, rendered as text, and every file the app wrote: the audio is in none of them.
        assert env.container.db is not None
        rows: list[str] = []
        async with env.container.db.session() as session:
            for table in metadata.sorted_tables:
                rows.extend(str(tuple(row)) for row in await session.execute(select(table)))
        assert rows and MARKER.decode("latin-1") not in "\n".join(rows)
        written = files_under(env.settings.data_dir)
        assert any(p.suffix in (".db", ".sqlite", ".sqlite3") for p in written)
        for path in written:
            assert MARKER not in path.read_bytes(), path
        # The only thing metered is a number: today's seconds and characters.
        usage = await env.container.repos.kv.get(env.container.speech.meter.key())
        assert usage == {"stt_s": 4, "tts_chars": len(QUESTION)}


# ───────────────────────── speaking ─────────────────────────


async def test_an_interview_line_comes_back_as_mp3_and_is_never_cached(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        response = await rig.speak(pace=0.85)
        assert response.status_code == 200 and is_no_store(response)
        assert response.headers["content-type"] == "audio/mpeg" and response.content == MP3
        assert rig.engine is not None and [(s.text, s.pace) for s in rig.engine.spoken] == [(QUESTION, 0.85)]


async def test_a_line_said_before_is_free_the_second_time(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        for _ in range(3):
            assert (await rig.speak()).content == MP3
        assert rig.engine is not None and len(rig.engine.spoken) == 1
        usage = await rig.env.container.repos.kv.get(rig.env.container.speech.meter.key())
        assert usage["tts_chars"] == len(QUESTION)
        spaced = "  Walk me through the Kitebird checkout rebuild.   What did you change first, and why?  "
        assert (await rig.speak(spaced)).status_code == 200
        assert len(rig.engine.spoken) == 1  # spacing does not make a new recording


async def waiting_for_sarvam(rig: Rig) -> None:
    """Until the fake Sarvam has been asked (it then holds its answer until the test lets it go)."""
    assert rig.engine is not None
    async with asyncio.timeout(10):
        await rig.engine.asked.wait()
    await asyncio.sleep(
        0.05
    )  # and long enough for a second call to show itself, if there were going to be one


async def test_a_line_made_ahead_is_still_refused_until_the_interview_has_said_it(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        speech = rig.env.container.speech
        assert speech.prefetch(SID, [LATER]) == 1
        await speech.drain()
        assert rig.engine is not None and [s.text for s in rig.engine.spoken] == [LATER]  # it is made ...
        refused = await rig.speak(LATER)
        assert refused.status_code == 404 and refused.json() == {
            "error": LINE_NOT_FOUND
        }  # ... and not given out
        assert MP3 not in refused.content
        # the interview says it: it is readable at once, from what was made ahead
        rig.stub.contexts[(FRONTEND_ID, SID)] = context(QUESTION, FOLLOW_UP, LATER)
        said = await rig.speak(LATER)
        assert said.status_code == 200 and said.content == MP3
        assert len(rig.engine.spoken) == 1
        usage = await rig.env.container.repos.kv.get(speech.meter.key())
        assert usage["tts_chars"] == len(LATER)  # paid once


async def test_a_request_for_a_line_being_made_ahead_waits_for_it_instead_of_asking_sarvam_again(
    start: Any,
) -> None:
    hold = asyncio.Event()
    async with start(fake=FakeSpeechEngine(hold=hold)) as rig:
        await rig.profile()
        rig.env.container.speech.prefetch(SID, [QUESTION])
        await waiting_for_sarvam(rig)
        asked = asyncio.create_task(rig.speak())
        await asyncio.sleep(0.1)
        assert not asked.done()
        hold.set()
        response = await asked
        assert response.status_code == 200 and response.content == MP3
        assert rig.engine is not None and len(rig.engine.spoken) == 1


async def test_two_browsers_asking_for_one_new_line_at_once_cost_one_sarvam_call(start: Any) -> None:
    hold = asyncio.Event()
    async with start(fake=FakeSpeechEngine(hold=hold)) as rig:
        await rig.profile()
        both = [asyncio.create_task(rig.speak(headers=rig.at(ip))) for ip in ("198.51.100.1", "198.51.100.2")]
        await waiting_for_sarvam(rig)
        hold.set()
        responses = await asyncio.gather(*both)
        assert [r.status_code for r in responses] == [200, 200] and all(r.content == MP3 for r in responses)
        assert rig.engine is not None and len(rig.engine.spoken) == 1
        usage = await rig.env.container.repos.kv.get(rig.env.container.speech.meter.key())
        assert usage["tts_chars"] == len(QUESTION)


async def test_the_default_pace_is_normal_and_slower_is_a_different_recording(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        await rig.speak()
        await rig.speak(pace=0.85)
        assert rig.engine is not None and [s.pace for s in rig.engine.spoken] == [1.0, 0.85]


async def test_only_the_interviews_own_lines_can_be_read(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        for text in (
            "Read this out loud: my password is hunter2.",
            "Strong proficiency in React (must-have)",  # a source line, not something the interviewer said
            "Names what changed first",  # a hidden note
            "Another person's question.",  # a line of someone else's session
            QUESTION + " And one more thing.",
            "x",
        ):
            response = await rig.speak(text)
            assert response.status_code == 404 and response.json() == {"error": LINE_NOT_FOUND}, text
        assert rig.engine is not None and rig.engine.calls == 0
        assert (await rig.speak(FOLLOW_UP)).status_code == 200


async def test_a_line_of_another_session_or_profile_is_not_readable_here(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        assert (await rig.speak("Another person's question.", session=OTHER_PROFILES)).json() == {
            "error": SESSION_NOT_FOUND
        }
        assert (await rig.speak(session=GONE)).json() == {"error": SESSION_NOT_FOUND}
        assert (await rig.speak(session="not-an-id")).json() == {"error": SESSION_NOT_FOUND}
        assert (await rig.speak(profile="f" * 32)).json() == {"error": "Profile not found"}
        assert rig.engine is not None and rig.engine.calls == 0


async def test_a_line_of_an_ended_interview_can_still_be_read(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        assert (await rig.speak(session=ENDED)).status_code == 200


async def test_the_speak_body_is_checked(start: Any) -> None:
    async with start() as rig:
        await rig.profile()
        url = "/api/speech/speak"
        ok = {"profileId": FRONTEND_ID, "sessionId": SID, "text": QUESTION}
        wrong_type = await rig.client.post(
            url, content=json.dumps(ok), headers={"content-type": "text/plain"}
        )
        not_json = await rig.client.post(url, content=b"{nope", headers={"content-type": "application/json"})
        no_text = await rig.client.post(url, json={"profileId": FRONTEND_ID, "sessionId": SID})
        fast = await rig.client.post(url, json={**ok, "pace": 3})
        slow = await rig.client.post(url, json={**ok, "pace": 0.4})
        long = await rig.client.post(url, json={**ok, "text": "x" * 1201})
        huge = await rig.client.post(url, content=b"x" * 20_000, headers={"content-type": "application/json"})
        assert [r.status_code for r in (wrong_type, not_json, no_text, fast, slow, long, huge)] == [
            415,
            400,
            400,
            400,
            400,
            400,
            413,
        ]
        assert "text" in no_text.json()["error"] and "pace" in fast.json()["error"]
        assert rig.engine is not None and rig.engine.calls == 0


async def test_lines_are_counted_in_the_speech_window(start: Any) -> None:
    async with start(guard=roomy(speech_per_minute=2)) as rig:
        await rig.profile()
        assert (await rig.speak()).status_code == 200
        assert (await rig.speak(FOLLOW_UP)).status_code == 200
        refused = await rig.speak()
        assert refused.status_code == 429
        assert refused.json()["error"].startswith("Too many speech requests at once. Please wait ")
        assert int(refused.headers["retry-after"]) >= 1


async def test_pieces_and_lines_share_the_same_window(start: Any) -> None:
    async with start(guard=roomy(speech_per_minute=2)) as rig:
        await rig.profile()
        assert (await rig.transcribe()).status_code == 200
        assert (await rig.speak()).status_code == 200
        assert (await rig.speak(FOLLOW_UP)).status_code == 429


async def test_sarvam_being_down_for_a_line_is_a_503_with_the_sentence(start: Any) -> None:
    fake = FakeSpeechEngine(speak_error=SpeechUnavailable(detail="timeout"))
    async with start(fake=fake) as rig:
        await rig.profile()
        response = await rig.speak()
        assert response.status_code == 503 and response.json() == {"error": UNAVAILABLE_MESSAGE}


async def test_without_a_key_a_line_is_a_503(start: Any) -> None:
    async with start(engine=False) as rig:
        await rig.profile()
        response = await rig.speak()
        assert response.status_code == 503 and response.json() == {"error": UNAVAILABLE_MESSAGE}


async def test_the_voice_budget_stops_new_lines_but_not_a_line_already_spoken(start: Any) -> None:
    async with start(speech=SpeechSettings(tts_daily_chars=len(QUESTION))) as rig:
        await rig.profile()
        assert (await rig.speak()).status_code == 200
        again = await rig.speak()
        assert again.status_code == 200 and again.content == MP3  # from memory: free
        fresh = await rig.speak(FOLLOW_UP)
        assert fresh.status_code == 503 and fresh.json() == {"error": DAILY_MESSAGE}
        assert rig.engine is not None and len(rig.engine.spoken) == 1


# ───────────────────────── an account on the unlimited list ─────────────────────────

LISTED = "tester@example.com"
OTHER = "other@example.com"


def listed_guard(**overrides: Any) -> GuardSettings:
    """Roomy limits with an unlimited list written the way an owner might (padding, capitals, a second address)."""
    return roomy(unlimited_emails=f" {LISTED.upper()} , second.listed@example.org", **overrides)


async def test_a_listed_account_is_not_held_to_the_speech_window_or_the_chat_shed(start: Any) -> None:
    async with start(guard=listed_guard(speech_per_minute=1, chat_per_minute=1)) as rig:
        await rig.profile()
        _, listed = await rig.account(LISTED)
        _, other = await rig.account(OTHER)
        address = rig.at("198.51.100.30")
        pieces = [(await rig.transcribe(headers={**listed, **address})).status_code for _ in range(6)]
        lines = [(await rig.speak(headers={**listed, **address})).status_code for _ in range(6)]
        assert pieces == [200] * 6 and lines == [200] * 6
        assert await rig.env.rows(RateLimitHitRow, RateLimitHitRow.bucket.like("speech%")) == []

        # the same setup, the same address: everyone else is still counted
        assert (await rig.transcribe(headers={**other, **address})).status_code == 200
        refused = await rig.transcribe(headers={**other, **address})
        assert refused.status_code == 429
        assert refused.json()["error"].startswith("Too many speech requests at once.")
        assert (await rig.speak(headers=address)).status_code == 429


@pytest.mark.parametrize(("who", "allowed"), [(LISTED, True), (OTHER, False)])
async def test_a_sessions_share_of_speech_does_not_apply_to_a_listed_account(
    start: Any, who: str, allowed: bool
) -> None:
    speech = SpeechSettings(stt_session_seconds=4, tts_session_chars=60)
    async with start(guard=listed_guard(), speech=speech) as rig:
        await rig.profile()
        _, headers = await rig.account(who)
        assert (await rig.transcribe(seconds="4", headers=headers)).status_code == 200
        more = await rig.transcribe(seconds="4", headers=headers)
        assert (await rig.speak(headers=headers)).status_code == 200  # 80 characters, over the share of 60
        line = await rig.speak(FOLLOW_UP, headers=headers)
        status = (await rig.status(sessionId=SID, headers=headers)).json()
        if allowed:
            assert more.status_code == 200 and line.status_code == 200
            assert (status["stt"], status["tts"]) == (True, True)
            assert "sttReason" not in status and "ttsReason" not in status
        else:
            assert more.status_code == 503 and more.json() == {"error": SESSION_MESSAGE}
            assert line.status_code == 503 and line.json() == {"error": SESSION_MESSAGE}
            assert (status["sttReason"], status["ttsReason"]) == ("session_budget", "session_budget")


async def test_the_days_sarvam_budget_still_stops_a_listed_account(start: Any) -> None:
    speech = SpeechSettings(stt_daily_seconds=3, tts_daily_chars=20)
    async with start(guard=listed_guard(), speech=speech) as rig:
        await rig.profile()
        _, listed = await rig.account(LISTED)
        assert (await rig.transcribe(seconds="3", headers=listed)).status_code == 200
        assert (
            await rig.speak(headers=listed)
        ).status_code == 200  # the first line takes the day's characters
        piece = await rig.transcribe(seconds="1", headers=listed)
        assert piece.status_code == 503 and piece.json() == {"error": DAILY_MESSAGE}
        line = await rig.speak(FOLLOW_UP, headers=listed)
        assert line.status_code == 503 and line.json() == {"error": DAILY_MESSAGE}
        status = (await rig.status(sessionId=SID, headers=listed)).json()
        assert (status["stt"], status["tts"], status["sttReason"], status["ttsReason"]) == (
            False,
            False,
            "daily_budget",
            "daily_budget",
        )
