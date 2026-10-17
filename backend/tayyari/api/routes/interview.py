"""Practice the interview: plan one, run it, read its debrief, and look back over what was practised.

    POST   /api/interview/plan                       `PlanRequest` -> `InterviewPlan` (no model call)
    POST   /api/interview/sessions                   `StartRequest` -> `InterviewSession` (one Haiku call phrases the questions)
    GET    /api/interview/sessions?profileId=        `PracticeHistory` (sessions, claims, facts, how many are left today)
    GET    /api/interview/sessions/{id}?profileId=   `SessionView`: the session, and its debrief once it is done (404 if gone)
    POST   /api/interview/sessions/{id}/answer       `AnswerRequest` -> `InterviewSession` (the next line, or done)
    POST   /api/interview/sessions/{id}/work         `WorkSubmit` -> `InterviewSession` (a work sample's work, handed in)
    POST   /api/interview/sessions/{id}/assistant    `AssistantRequest` -> `AssistantReply` (a work sample's assistant)
    POST   /api/interview/sessions/{id}/end          `SessionRequest` -> `Debrief` (a done session returns the one it has)
    POST   /api/interview/sessions/{id}/again        `AgainRequest` -> `InterviewSession` (one question of a done interview)
    POST   /api/interview/sessions/{id}/facts        `FactRequest` -> `Debrief` (add what the answer said to the profile, or not)
    DELETE /api/interview/sessions/{id}?profileId=   204; 404 when there was nothing to delete

A profile is opened exactly as the job search opens it (`api.deps.require_profile_access`): one nobody has claimed or a
sample run is open to whoever holds its id; a claimed profile only to its owner's session. A session is read back only
for the profile it belongs to; another profile's session id is a 404, like an unknown one, and so is an id that is not 32
hexadecimal characters. Every answer is ``Cache-Control: no-store``: it holds what the person said.

Starting an interview, in this order, all answered as JSON: ``shed`` (in-memory, before the body is read) -> switched off
(503) -> body (415 / 413 / 408 / 400) -> profile (404) -> the client's counters, and the account's when signed in (429
with ``Retry-After``) -> the spend reservation (503) -> the service (400 / 404 for a plan that cannot be made). Answering,
ending and the assistant shed in the ``chat`` bucket and reserve a small spend (one small Haiku call each); handing in
work, a repeat and adding a fact call no model. "Ask this one again" has a daily window of its own. Nothing is charged to
an account: practice is free while it is tested, and the windows are the only meter.

Switched off (``TAYYARI_INTERVIEW__ENABLED=false``) every route answers 503 except the delete: a person can always take
their transcript away.

Voice made ahead (`api.routes._voice`). When Sarvam speech is on, starting, reloading (the view), handing in work and asking
again have the interviewer's next lines made in the background once the answer has been sent, and an answer has the lines the
next turn may need made while the judge reads it, then the new line and what follows once the reply has been sent. It never
changes what a route answers: no Sarvam, a used-up budget or any failure means nothing is made ahead.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.responses import Response

from tayyari.api.deps import get_current_user, get_request_context, require_profile_access
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.routes._voice import prefetch_voice, voice_after
from tayyari.api.security import subnet_hash
from tayyari.container import Container
from tayyari.contract.interview import (
    AgainRequest,
    AnswerRequest,
    AssistantRequest,
    FactRequest,
    PlanRequest,
    PracticeHistory,
    SessionRequest,
    StartRequest,
    WorkSubmit,
)
from tayyari.contract.models import Profile
from tayyari.contract.requests import RequestContext
from tayyari.core.errors import NotFound
from tayyari.core.ids import is_valid_id
from tayyari.llm.types import CallContext, Purpose

router = APIRouter()

PROFILE_NOT_FOUND: Final = "Profile not found"
SESSION_NOT_FOUND: Final = (
    "This practice interview isn't here any more. Practice interviews are kept for 30 days."
)
"""The sentence of the 404 for a session that is gone, is another profile's, or has an id of the wrong shape (the same
as `tayyari.interview.errors.InterviewNotFound`, which the service raises for an id it cannot find)."""
NO_STORE: Final = {"Cache-Control": "no-store"}
PLAN_BODY_MAX_BYTES: Final = 4 * 1024
"""A plan or start body holds a profile id, a role (two ids) and at most twelve skipped slot ids."""
SMALL_BODY_MAX_BYTES: Final = 2 * 1024
"""A session, repeat or fact request holds a profile id and a slot id."""
ANSWER_BODY_MAX_BYTES: Final = 48 * 1024
"""An answer is at most 6,000 characters; JSON may spell one as up to six bytes (``\\u00e9``), plus the envelope."""
WORK_BODY_MAX_BYTES: Final = 144 * 1024
"""Work (20,000 characters) and its test note (2,000), or an assistant message with the work (1,000 and 20,000), at up
to six bytes a character."""
_HEX_ID = re.compile(r"[0-9a-f]{32}")


def _require_hex(session_id: str) -> None:
    if not _HEX_ID.fullmatch(session_id):
        raise NotFound(SESSION_NOT_FOUND)


async def _profile(request: Request, container: Container, profile_id: str) -> Profile:
    """The profile this caller may open, or the 404 an unknown id gets."""
    if not is_valid_id(profile_id):
        raise NotFound(PROFILE_NOT_FOUND)
    await require_profile_access(request, profile_id, not_found=PROFILE_NOT_FOUND)
    found = await container.repos.profiles.get_profile(profile_id)
    if found is None and container.sample.is_sample_profile(profile_id):
        sample = container.sample.sample_profile
        found = sample.to_dict() if sample is not None else None
    if found is None:
        raise NotFound(PROFILE_NOT_FOUND)
    try:
        return Profile.from_dict(found)
    except ValidationError:
        raise NotFound(PROFILE_NOT_FOUND) from None


def _ctx(rc: RequestContext, profile_id: str, purpose: Purpose) -> CallContext:
    return CallContext(purpose=purpose, request_id=rc.request_id, profile_id=profile_id, ip_hash=rc.ip_hash)


def _shed_light(container: Container, rc: RequestContext) -> None:
    container.rate_limiter.shed(
        "chat", rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )


async def _begin(request: Request, session_id: str | None = None) -> tuple[Container, RequestContext]:
    """The first steps of every route but the delete: the container, who is asking, hammering shed, the kill switch, and
    the shape of the session id (before a body is read for a session that cannot be there)."""
    container = get_container(request)
    rc = await get_request_context(request)
    _shed_light(container, rc)
    container.interview.require_enabled()
    if session_id is not None:
        _require_hex(session_id)
    return container, rc


def _json(data: dict[str, object], background: BackgroundTask | None = None) -> JSONResponse:
    return JSONResponse(data, headers=NO_STORE, background=background)


async def _count(
    request: Request, container: Container, rc: RequestContext, route: Literal["interview", "interview_again"]
) -> None:
    """Count one request against the client's windows for ``route``, and the account's when signed in (the whole-service
    counter is only touched once)."""
    limiter = container.rate_limiter
    await limiter.check(
        route, rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip), memory=False
    )
    user = await get_current_user(request)
    if user is not None:
        await limiter.check(route, f"u:{user.id}", memory=False, globally=False)


# ───────────────────────── planning and starting ─────────────────────────


@router.post("/api/interview/plan")
async def plan_interview(request: Request) -> JSONResponse:
    container, _ = await _begin(request)
    body = await read_model(request, container, PlanRequest, max_bytes=PLAN_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    plan = await container.interview.plan(profile, body)
    return _json(plan.to_dict())


@router.post("/api/interview/sessions")
async def start_interview(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    container.rate_limiter.shed(
        "interview", rc.ip_hash, ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip)
    )
    service = container.interview
    service.require_enabled()
    body = await read_model(request, container, StartRequest, max_bytes=PLAN_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    await _count(request, container, rc, "interview")
    async with container.spend_guard.reserve(container.settings.guard.est_interview_usd):
        session = await service.start(profile, body, _ctx(rc, profile.id, "interview.phrase"))
    return _json(session.to_dict(), voice_after(container, profile, session.id))


# ───────────────────────── reading back ─────────────────────────


@router.get("/api/interview/sessions")
async def practice_history(request: Request) -> JSONResponse:
    container, _ = await _begin(request)
    profile = await _profile(request, container, request.query_params.get("profileId", ""))
    per_day = container.settings.guard.interview_per_day
    if container.sample.is_sample_profile(profile.id):
        # Everyone shares the sample profile, so its practice is never listed: a session is reachable only through
        # the id its own browser holds. Another visitor's transcript and kept facts must not show up here.
        return _json(PracticeHistory(sessions=[], claims=[], facts=[]).to_dict())
    history = await container.interview.history(profile, per_day=per_day if per_day > 0 else None)
    return _json(history.to_dict())


@router.get("/api/interview/sessions/{session_id}")
async def interview_view(session_id: str, request: Request) -> JSONResponse:
    container, _ = await _begin(request, session_id)
    profile = await _profile(request, container, request.query_params.get("profileId", ""))
    view = await container.interview.view(profile, session_id)
    if view is None:
        raise NotFound(SESSION_NOT_FOUND)
    # a reload: this replica may not have the lines yet (its memory is its own)
    active = view.session.status == "active"
    return _json(view.to_dict(), voice_after(container, profile, session_id) if active else None)


# ───────────────────────── inside an interview ─────────────────────────


@router.post("/api/interview/sessions/{session_id}/answer")
async def answer_interview(session_id: str, request: Request) -> JSONResponse:
    container, rc = await _begin(request, session_id)
    body = await read_model(request, container, AnswerRequest, max_bytes=ANSWER_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    # the judge takes about 2 s: meanwhile the lines the next turn may need are made (which one it is depends on the judge)
    await prefetch_voice(container, profile, session_id, next_turn=True)
    async with container.spend_guard.reserve(container.settings.guard.est_interview_turn_usd):
        session = await container.interview.answer(
            profile, session_id, body, _ctx(rc, profile.id, "interview.judge")
        )
    # the new line, and what follows it (a reply to a dispute or an off-topic answer could not be known before)
    return _json(session.to_dict(), voice_after(container, profile, session_id))


@router.post("/api/interview/sessions/{session_id}/work")
async def submit_interview_work(session_id: str, request: Request) -> JSONResponse:
    container, _ = await _begin(request, session_id)
    body = await read_model(request, container, WorkSubmit, max_bytes=WORK_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    session = await container.interview.submit_work(profile, session_id, body)
    return _json(session.to_dict(), voice_after(container, profile, session_id))


@router.post("/api/interview/sessions/{session_id}/assistant")
async def ask_interview_assistant(session_id: str, request: Request) -> JSONResponse:
    container, rc = await _begin(request, session_id)
    body = await read_model(request, container, AssistantRequest, max_bytes=WORK_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    async with container.spend_guard.reserve(container.settings.guard.est_interview_turn_usd):
        reply = await container.interview.assistant(
            profile, session_id, body, _ctx(rc, profile.id, "interview.assistant")
        )
    return _json(reply.to_dict())


@router.post("/api/interview/sessions/{session_id}/end")
async def end_interview(session_id: str, request: Request) -> JSONResponse:
    container, rc = await _begin(request, session_id)
    body = await read_model(request, container, SessionRequest, max_bytes=SMALL_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    async with container.spend_guard.reserve(container.settings.guard.est_interview_end_usd):
        debrief = await container.interview.end(
            profile, session_id, _ctx(rc, profile.id, "interview.debrief")
        )
    return _json(debrief.to_dict())


# ───────────────────────── after an interview ─────────────────────────


@router.post("/api/interview/sessions/{session_id}/again")
async def ask_again(session_id: str, request: Request) -> JSONResponse:
    container, rc = await _begin(request, session_id)
    body = await read_model(request, container, AgainRequest, max_bytes=SMALL_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    await _count(request, container, rc, "interview_again")
    session = await container.interview.again(profile, session_id, body.slotId)
    return _json(session.to_dict(), voice_after(container, profile, session.id))


@router.post("/api/interview/sessions/{session_id}/facts")
async def set_interview_fact(session_id: str, request: Request) -> JSONResponse:
    container, _ = await _begin(request, session_id)
    body = await read_model(request, container, FactRequest, max_bytes=SMALL_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    debrief = await container.interview.set_fact(profile, session_id, body)
    if debrief is None:
        raise NotFound(SESSION_NOT_FOUND)
    return _json(debrief.to_dict())


@router.delete("/api/interview/sessions/{session_id}")
async def delete_interview(session_id: str, request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    _shed_light(container, rc)
    _require_hex(session_id)
    profile = await _profile(request, container, request.query_params.get("profileId", ""))
    if not await container.interview.delete(profile, session_id):
        raise NotFound(SESSION_NOT_FOUND)
    return Response(status_code=204, headers=NO_STORE)


__all__ = ["PROFILE_NOT_FOUND", "SESSION_NOT_FOUND", "router"]
