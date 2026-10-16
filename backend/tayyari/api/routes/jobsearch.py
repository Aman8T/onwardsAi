"""Search with AI: Claude Haiku scouts search employers' own job sites for the person's kind of role, code reads the
boards they lead to and narrows the open roles, and Haiku reads the shortlist against the person's evidence.

    POST /api/jobsearch    {"profileId", "city"?} -> NDJSON `step` lines, `job` lines, then `done` (or `error`)

A profile is opened exactly as Discover opens it (`api.deps.require_profile_access`): one nobody has claimed or a sample
run is open to whoever holds its id; a claimed profile only to its owner's session. ``city`` is a name from
``/api/discover/cities`` (the city Discover is showing); without one, the city the resume names, else remote roles only.
It answers this request and is not stored.

In this order, all answered as JSON before the stream starts: ``shed`` (in-memory, before the body is read) -> switched
off (503) -> body (415 / 413 / 408 / 400) -> profile (404) -> city (400) -> the client's counters, and the account's
when signed in, or the client's daily allowance without an account (429 with ``Retry-After``) -> the spend reservation
(503). Nothing is charged to an account: a run costs about $0.10 to $0.15 (mostly the $0.01 web searches), and the
counters are the meter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Final

from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.responses import Response

from tayyari.api.deps import get_current_user, get_request_context, require_profile_access
from tayyari.api.lifespan import get_container
from tayyari.api.routes._body import read_model
from tayyari.api.routes.analyze import HeldResponse
from tayyari.api.security import subnet_hash
from tayyari.contract.jobsearch import JobSearchEvent, JobSearchRequest
from tayyari.contract.models import Profile
from tayyari.core.errors import BadRequest, NotFound
from tayyari.core.ids import is_valid_id
from tayyari.discover import places
from tayyari.llm.types import CallContext

router = APIRouter()

TAG: Final = "jobsearch"
BODY_MAX_BYTES: Final = 4 * 1024
PROFILE_NOT_FOUND: Final = "Profile not found"
UNKNOWN_CITY: Final = "That city is not one we know. Pick one from the list."


@router.post("/api/jobsearch")
async def start_jobsearch(request: Request) -> Response:
    container = get_container(request)
    rc = await get_request_context(request)
    limiter = container.rate_limiter
    ip48 = subnet_hash(container.ip_hash_key, rc.client_ip)
    limiter.shed("jobsearch", rc.ip_hash, ip_hash_48=ip48)
    service = container.jobsearch
    service.require_enabled()
    body = await read_model(request, container, JobSearchRequest, max_bytes=BODY_MAX_BYTES)
    if not is_valid_id(body.profileId):
        raise NotFound(PROFILE_NOT_FOUND)
    await require_profile_access(request, body.profileId, not_found=PROFILE_NOT_FOUND)
    found = await container.repos.profiles.get_profile(body.profileId)
    if found is None and container.sample.is_sample_profile(body.profileId):
        sample = container.sample.sample_profile
        found = sample.to_dict() if sample is not None else None
    if found is None:
        raise NotFound(PROFILE_NOT_FOUND)
    try:
        profile = Profile.from_dict(found)
    except ValidationError:
        raise NotFound(PROFILE_NOT_FOUND) from None
    city_name = (body.city or "").strip()
    if city_name:
        city = places.city_by_name(city_name)
        if city is None:
            raise BadRequest(UNKNOWN_CITY)
    else:
        city = places.find_city(profile.person.location)

    user = await get_current_user(request)
    await limiter.check("jobsearch", rc.ip_hash, ip_hash_48=ip48, memory=False)
    if user is not None:
        await limiter.check("jobsearch", f"u:{user.id}", memory=False, globally=False)
    else:
        await limiter.check("jobsearch_anon", rc.ip_hash, ip_hash_48=ip48, memory=False)

    held = AsyncExitStack()
    try:
        await held.enter_async_context(
            container.spend_guard.reserve(container.settings.guard.est_jobsearch_usd)
        )
        ctx = CallContext(
            purpose="jobsearch.scout", request_id=rc.request_id, profile_id=profile.id, ip_hash=rc.ip_hash
        )
        events: AsyncIterator[JobSearchEvent] = service.run(profile, city, ctx)
        return HeldResponse(request, events, TAG, held)
    except BaseException:
        await held.aclose()
        raise


__all__ = ["PROFILE_NOT_FOUND", "TAG", "UNKNOWN_CITY", "router"]
