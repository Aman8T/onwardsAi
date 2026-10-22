"""Prepare: "How do I prepare for this role?" as typed blocks (P1).

    POST /api/prepare                              `PrepareRequest` -> NDJSON `gaps` (at once, code only), then `standing`
                                                   and `moves` as each model call lands (either order), then `done` with
                                                   the stored plan; or `error` (`no_moves`, `failed`). A plan already
                                                   stored for the same profile, role and requirement streams `gaps` then
                                                   `done` at once, unless ``fresh`` is true: no counter, no reservation,
                                                   no model call.
    GET  /api/prepare/{planId}?profileId=          `PreparePlan`
    PUT  /api/prepare/{planId}/state               `PrepareStateUpdate` -> `PrepareStateSaved` (409 `plan_changed` when
                                                   ``version`` is stale; 400 when a done key or link names what the plan
                                                   does not have)
    GET  /api/prepare/{planId}/repos?profileId=    `PrepareRepos`, ranked in code from the stored GitHub digest
    POST /api/prepare/{planId}/headline            `PrepareHeadlineRequest` -> `PrepareHeadline` (one model call)
    GET  /api/prepare?profileId=                   `PreparePlanSummary[]`, newest first (where a Fix step can go, I2)

A plan carries the Fix steps from practice placed in it (``practice``, I2) wherever it is read whole: `GET` by id and a stored
plan streamed again. They live on the claim marks, so they are read through the interview service here: prepare never
imports interview (the layering test).

A profile is opened exactly as the job search and the interview open it (`api.deps.require_profile_access`): one nobody
has claimed or a sample run is open to whoever holds its id; a claimed profile only to its owner's session. A plan is read
back only for the profile it belongs to; another profile's plan id is a 404, like an unknown one, and so is an id that is
not 32 hexadecimal characters. Every answer is ``Cache-Control: no-store``: a plan carries the person's evidence.

Writing a plan, in this order, all answered as JSON before the stream starts: ``shed`` (in-memory, before the body is
read) -> body (415 / 413 / 408 / 400) -> profile (404) -> the plan's numbers from the graph (404 for a role or requirement
the graph does not have, 400 ``already_strong`` for one it already meets) -> a stored plan (streamed at once) -> the
client's counters in the ``prepare`` window, and the account's when signed in (429 with ``Retry-After``) -> the spend
reservation (503). The headline rewrite counts and reserves the same way; reading a plan back, saving its state and the
repo ranking shed in the ``chat`` bucket like the other light reads and call no model. An account on the unlimited list
(``TAYYARI_GUARD__UNLIMITED_EMAILS``, `api.deps.unlimited`) skips every ``shed`` and counter; the spend reservation still
applies. Nothing is charged to an account.
"""

from __future__ import annotations

import re
from contextlib import AsyncExitStack
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from tayyari.api.deps import get_current_user, get_request_context, require_profile_access, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.ndjson import NdjsonResponse
from tayyari.api.routes._body import read_model
from tayyari.api.routes.analyze import HeldResponse
from tayyari.api.security import subnet_hash
from tayyari.container import Container
from tayyari.contract.models import Profile
from tayyari.contract.prepare import PrepareHeadlineRequest, PreparePlan, PrepareRequest, PrepareStateUpdate
from tayyari.contract.requests import RequestContext
from tayyari.contract.stored import StoredContext
from tayyari.core.errors import NotFound
from tayyari.core.ids import is_valid_id
from tayyari.llm.types import CallContext, Purpose
from tayyari.prepare.errors import PLAN_NOT_FOUND, PlanNotFound

router = APIRouter()

TAG: Final = "prepare"
PROFILE_NOT_FOUND: Final = "Profile not found"
NO_STORE: Final = {"Cache-Control": "no-store"}
BODY_MAX_BYTES: Final = 4 * 1024
"""A plan or headline request holds a profile id, a role id and a requirement name."""
STATE_BODY_MAX_BYTES: Final = 48 * 1024
"""A state holds at most 200 done keys of 24 characters and 20 links of 500 characters, at up to six bytes a character."""
_HEX_ID = re.compile(r"[0-9a-f]{32}")


def _require_hex(plan_id: str) -> None:
    if not _HEX_ID.fullmatch(plan_id):
        raise PlanNotFound()


async def _access(request: Request, container: Container, profile_id: str) -> None:
    """The id's shape, then who may open the profile: the 404 an unknown id gets, before anything is read.

    For the light routes (a plan read back, its state, its repos) the profile document itself is not needed, so only
    its owner row is looked at; an id no stored profile has (the bundled sample before it is seeded included) is the
    same 404, because no plan can exist for it either.
    """
    if not is_valid_id(profile_id):
        raise NotFound(PROFILE_NOT_FOUND)
    await require_profile_access(request, profile_id, not_found=PROFILE_NOT_FOUND)
    if await container.repos.profiles.get_access(profile_id) is None:
        raise NotFound(PROFILE_NOT_FOUND)


async def _profile(request: Request, container: Container, profile_id: str) -> tuple[Profile, StoredContext]:
    """The profile and its context this caller may open, or the 404 an unknown id gets."""
    if not is_valid_id(profile_id):
        raise NotFound(PROFILE_NOT_FOUND)
    await require_profile_access(request, profile_id, not_found=PROFILE_NOT_FOUND)
    loaded = await container.prepare.load_profile(profile_id)
    if loaded is None:
        raise NotFound(PROFILE_NOT_FOUND)
    return loaded


def _ctx(rc: RequestContext, profile_id: str, purpose: Purpose) -> CallContext:
    return CallContext(purpose=purpose, request_id=rc.request_id, profile_id=profile_id, ip_hash=rc.ip_hash)


def _json(data: dict[str, object]) -> JSONResponse:
    return JSONResponse(data, headers=NO_STORE)


async def _with_practice(container: Container, plan: PreparePlan) -> PreparePlan:
    """The plan with the Fix steps placed in it (none: the field stays absent)."""
    practice = await container.interview.plan_fixes(plan.profileId, plan.id)
    return plan.model_copy(update={"practice": practice}) if practice else plan


async def _shed_light(request: Request, container: Container, rc: RequestContext) -> None:
    container.rate_limiter.shed(
        "chat",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        exempt=await unlimited(request, container),
    )


async def _count(request: Request, container: Container, rc: RequestContext) -> None:
    """Count one request in the ``prepare`` windows of the client, and of the account when signed in (the whole-service
    counter is only touched once)."""
    limiter = container.rate_limiter
    free = await unlimited(request, container)
    await limiter.check(
        "prepare",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        memory=False,
        exempt=free,
    )
    user = await get_current_user(request)
    if user is not None:
        await limiter.check("prepare", f"u:{user.id}", memory=False, globally=False, exempt=free)


# ───────────────────────── the plan ─────────────────────────


@router.post("/api/prepare")
async def start_prepare(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    container.rate_limiter.shed(
        "prepare",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        exempt=await unlimited(request, container),
    )
    body = await read_model(request, container, PrepareRequest, max_bytes=BODY_MAX_BYTES)
    profile, context = await _profile(request, container, body.profileId)
    service = container.prepare
    plan = service.assemble(profile, context, body)
    if body.fresh is not True:
        stored = await service.stored(profile.id, plan.roleId, plan.requirement)
        if stored is not None:
            return NdjsonResponse(request, service.replay(await _with_practice(container, stored)), TAG)
    await _count(request, container, rc)
    held = AsyncExitStack()
    try:
        await held.enter_async_context(
            container.spend_guard.reserve(container.settings.guard.est_prepare_usd)
        )
        events = service.run(profile, context, plan, _ctx(rc, profile.id, "prepare.standing"))
        return HeldResponse(request, events, TAG, held)
    except BaseException:
        await held.aclose()
        raise


@router.get("/api/prepare/{plan_id}")
async def get_plan(plan_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed_light(request, container, rc)
    _require_hex(plan_id)
    profile_id = request.query_params.get("profileId", "")
    await _access(request, container, profile_id)
    plan = await container.prepare.get(profile_id, plan_id)
    if plan is None:
        raise PlanNotFound()
    return _json((await _with_practice(container, plan)).to_dict())


@router.get("/api/prepare")
async def list_plans(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed_light(request, container, rc)
    profile_id = request.query_params.get("profileId", "")
    await _access(request, container, profile_id)
    plans = await container.prepare.summaries(profile_id)
    return JSONResponse([p.to_dict() for p in plans], headers=NO_STORE)


@router.put("/api/prepare/{plan_id}/state")
async def save_state(plan_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed_light(request, container, rc)
    _require_hex(plan_id)
    body = await read_model(request, container, PrepareStateUpdate, max_bytes=STATE_BODY_MAX_BYTES)
    await _access(request, container, body.profileId)
    saved = await container.prepare.save_state(body.profileId, plan_id, body)
    return _json(saved.to_dict())


# ───────────────────────── next: the repo and the headline ─────────────────────────


@router.get("/api/prepare/{plan_id}/repos")
async def plan_repos(plan_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed_light(request, container, rc)
    _require_hex(plan_id)
    profile, context = await _profile(request, container, request.query_params.get("profileId", ""))
    repos = await container.prepare.repos(profile.id, plan_id, context)
    if repos is None:
        raise PlanNotFound()
    return _json(repos.to_dict())


@router.post("/api/prepare/{plan_id}/headline")
async def rewrite_headline(plan_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    container.rate_limiter.shed(
        "prepare",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        exempt=await unlimited(request, container),
    )
    _require_hex(plan_id)
    body = await read_model(request, container, PrepareHeadlineRequest, max_bytes=BODY_MAX_BYTES)
    profile, _ = await _profile(request, container, body.profileId)
    plan = await container.prepare.get(profile.id, plan_id)
    if plan is None:
        raise PlanNotFound()
    await _count(request, container, rc)
    async with container.spend_guard.reserve(container.settings.guard.est_prepare_headline_usd):
        headline = await container.prepare.headline(profile, plan, _ctx(rc, profile.id, "prepare.headline"))
    return _json(headline.to_dict())


__all__ = ["BODY_MAX_BYTES", "PLAN_NOT_FOUND", "PROFILE_NOT_FOUND", "STATE_BODY_MAX_BYTES", "TAG", "router"]
