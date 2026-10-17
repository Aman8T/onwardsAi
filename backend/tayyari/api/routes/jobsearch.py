"""Search with AI: the search, its last results, and what is built on a role it found.

    POST /api/jobsearch                                   {"profileId", "city"?, "remote"?, "avoid"?} -> NDJSON `step`
                                                          lines, `job` lines, then `done` {runId} (or `error`)
    GET  /api/jobsearch/latest?profileId=&city=&remote=   `JobSearchRun`: the last finished search for this profile, city
                                                          and remote choice (404 when there is none)
    GET  /api/jobsearch/runs/{runId}/jobs/{jobId}/posting?profileId=
                                                          `JobPosting`: the posting's text as the search read it (for
                                                          "See how you fit", which sends it to POST /api/match)
    POST /api/jobsearch/prepare                           `RoleRequest` -> `RolePrep` (kept once written)
    POST /api/jobsearch/referral                          `RoleRequest` -> `ReferralNote` (kept; ``fresh`` writes anew)

A profile is opened exactly as Discover opens it (`api.deps.require_profile_access`): one nobody has claimed or a sample
run is open to whoever holds its id; a claimed profile only to its owner's session. A run is read back only for the
profile it belongs to; another profile's run id is a 404, like an unknown one. ``city`` is a name from
``/api/discover/cities``; without one, the city the resume names, else remote roles only. It is not stored apart from the
run it labels.

The search, in this order, all answered as JSON before the stream starts: ``shed`` (in-memory, before the body is read)
-> switched off (503) -> body (415 / 413 / 408 / 400) -> profile (404) -> city (400) -> the client's counters, and the
account's when signed in, or the client's daily allowance without an account (429 with ``Retry-After``) -> the spend
reservation (503). Nothing is charged to an account: a run costs about $0.10 to $0.15 (mostly the $0.01 web searches).
Every request here also starts a background refresh of Discover's store when it is old (never waited for). An account on
the unlimited list (``TAYYARI_GUARD__UNLIMITED_EMAILS``) skips every ``shed`` and counter; the spend reservation still applies.

The reads (latest, posting) shed in the ``chat`` bucket like other light reads. Prepare and referral shed in ``chat`` and
reserve a small spend, because writing one calls a model (a kept one does not).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.responses import Response

from tayyari.api.deps import get_current_user, get_request_context, require_profile_access, unlimited
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.routes.analyze import HeldResponse
from tayyari.api.security import subnet_hash
from tayyari.container import Container
from tayyari.contract.jobsearch import JobSearchEvent, JobSearchRequest, RoleRequest
from tayyari.contract.models import Profile
from tayyari.contract.requests import RequestContext
from tayyari.core.errors import BadRequest, NotFound
from tayyari.core.ids import is_valid_id
from tayyari.discover import places
from tayyari.discover.places import City
from tayyari.llm.types import CallContext, Purpose

router = APIRouter()

TAG: Final = "jobsearch"
BODY_MAX_BYTES: Final = 16 * 1024
"""A search body holds the "Not for me" choices (at most 200 ids and 200 employer names)."""
ROLE_BODY_MAX_BYTES: Final = 2 * 1024
PROFILE_NOT_FOUND: Final = "Profile not found"
NO_RUN: Final = "No search yet"
ROLE_NOT_FOUND: Final = "That role is not in this search any more. Search again to see it."
UNKNOWN_CITY: Final = "That city is not one we know. Pick one from the list."
NO_STORE: Final = {"Cache-Control": "no-store"}
_HEX_ID = re.compile(r"[0-9a-f]{32}")


def _require_hex(value: str, message: str) -> None:
    if not _HEX_ID.fullmatch(value):
        raise NotFound(message)


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


def _city(name: str | None, profile: Profile) -> City | None:
    chosen = (name or "").strip()
    if chosen:
        city = places.city_by_name(chosen)
        if city is None:
            raise BadRequest(UNKNOWN_CITY)
        return city
    return places.find_city(profile.person.location)


def _remote(value: str | None) -> bool:
    return (value or "1").strip().lower() not in {"0", "false", "no", "off"}


def _ctx(rc: RequestContext, profile_id: str, purpose: Purpose) -> CallContext:
    return CallContext(purpose=purpose, request_id=rc.request_id, profile_id=profile_id, ip_hash=rc.ip_hash)


async def _shed_light(request: Request, container: Container, rc: RequestContext) -> None:
    container.rate_limiter.shed(
        "chat",
        rc.ip_hash,
        ip_hash_48=subnet_hash(container.ip_hash_key, rc.client_ip),
        exempt=await unlimited(request, container),
    )


async def _refresh_store(container: Container) -> None:
    await container.discover.maybe_refresh()


# ───────────────────────── the search ─────────────────────────


@router.post("/api/jobsearch")
async def start_jobsearch(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    limiter = container.rate_limiter
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    free = await unlimited(request, container)
    limiter.shed("jobsearch", rc.ip_hash, ip_hash_48=ip48, exempt=free)
    service = container.jobsearch
    service.require_enabled()
    body = await read_model(request, container, JobSearchRequest, max_bytes=BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    city = _city(body.city, profile)
    await _refresh_store(container)

    user = await get_current_user(request)
    await limiter.check("jobsearch", rc.ip_hash, ip_hash_48=ip48, memory=False, exempt=free)
    if user is not None:
        await limiter.check("jobsearch", f"u:{user.id}", memory=False, globally=False, exempt=free)
    else:
        await limiter.check("jobsearch_anon", rc.ip_hash, ip_hash_48=ip48, memory=False)

    held = AsyncExitStack()
    try:
        await held.enter_async_context(
            container.spend_guard.reserve(container.settings.guard.est_jobsearch_usd)
        )
        events: AsyncIterator[JobSearchEvent] = service.run(
            profile,
            city,
            _ctx(rc, profile.id, "jobsearch.scout"),
            remote=body.remote is not False,
            avoid=body.avoid,
        )
        return HeldResponse(request, events, TAG, held)
    except BaseException:
        await held.aclose()
        raise


# ───────────────────────── reading back ─────────────────────────


@router.get("/api/jobsearch/latest")
async def latest_jobsearch(request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed_light(request, container, rc)
    container.jobsearch.require_enabled()
    params = request.query_params
    profile = await _profile(request, container, params.get("profileId", ""))
    city = _city(params.get("city"), profile)
    await _refresh_store(container)
    run = await container.jobsearch.latest(profile.id, city, _remote(params.get("remote")))
    if run is None:
        raise NotFound(NO_RUN)
    return JSONResponse(run.to_dict(), headers=NO_STORE)


@router.get("/api/jobsearch/runs/{run_id}/jobs/{job_id}/posting")
async def job_posting(run_id: str, job_id: str, request: Request) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed_light(request, container, rc)
    container.jobsearch.require_enabled()
    _require_hex(run_id, ROLE_NOT_FOUND)
    _require_hex(job_id, ROLE_NOT_FOUND)
    profile = await _profile(request, container, request.query_params.get("profileId", ""))
    posting = await container.jobsearch.posting(profile.id, run_id, job_id)
    if posting is None:
        raise NotFound(ROLE_NOT_FOUND)
    return JSONResponse(posting.to_dict(), headers=NO_STORE)


# ───────────────────────── a role's prep page and referral note ─────────────────────────


async def _role_doc(request: Request, kind: Purpose) -> JSONResponse:
    container = get_container(request)
    rc = await get_request_context(request)
    await _shed_light(request, container, rc)
    service = container.jobsearch
    service.require_enabled()
    body = await read_model(request, container, RoleRequest, max_bytes=ROLE_BODY_MAX_BYTES)
    profile = await _profile(request, container, body.profileId)
    _require_hex(body.runId, ROLE_NOT_FOUND)
    _require_hex(body.jobId, ROLE_NOT_FOUND)
    async with container.spend_guard.reserve(container.settings.guard.est_jobsearch_doc_usd):
        ctx = _ctx(rc, profile.id, kind)
        fresh = body.fresh is True
        if kind == "jobsearch.prepare":
            prep = await service.prepare(profile, body.runId, body.jobId, ctx, fresh=fresh)
            answer = None if prep is None else prep.to_dict()
        else:
            note = await service.referral(profile, body.runId, body.jobId, ctx, fresh=fresh)
            answer = None if note is None else note.to_dict()
    if answer is None:
        raise NotFound(ROLE_NOT_FOUND)
    return JSONResponse(answer, headers=NO_STORE)


@router.post("/api/jobsearch/prepare")
async def prepare_role(request: Request) -> JSONResponse:
    return await _role_doc(request, "jobsearch.prepare")


@router.post("/api/jobsearch/referral")
async def referral_note(request: Request) -> JSONResponse:
    return await _role_doc(request, "jobsearch.referral")


__all__ = ["NO_RUN", "PROFILE_NOT_FOUND", "ROLE_NOT_FOUND", "TAG", "UNKNOWN_CITY", "router"]
