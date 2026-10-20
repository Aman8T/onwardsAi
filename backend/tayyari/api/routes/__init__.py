"""The HTTP routes. `api_router` bundles them; `create_app` includes it before `mount_spa`.

    health    GET  /api/health, /api/health/deep
    analyze   POST /api/analyze          multipart form -> NDJSON `AnalysisEvent` stream
    chat      POST /api/chat             JSON `ChatRequestBody` -> NDJSON `ChatEvent` stream
    profile   GET  /api/profile/{pid}
    sample    GET  /api/sample-profile
    portfolio POST /api/portfolio        JSON `PortfolioRequest` -> NDJSON `PortfolioEvent` stream
              GET, PATCH /api/portfolio/{profile_id}; POST .../rewrite; GET .../public-preview; GET /api/p/{public_id}
    auth      GET /api/auth/google/start, /callback; POST /api/auth/magic; GET /api/auth/magic/verify; POST /api/auth/logout
    me        GET, PATCH, DELETE /api/me; POST /api/me/claim
    billing   GET /api/billing/entitlements; POST /api/billing/checkout, /verify; POST /api/billing/webhooks/razorpay, /stripe
    match     POST /api/match (NDJSON); GET /api/match/{id}, /api/matches; POST .../answers, .../resume (NDJSON), .../cover;
              GET, PATCH .../resume; GET .../cover, .../resume.pdf, .../resume.docx, .../cover.pdf
    discover  GET /api/discover/cities
    jobsearch POST /api/jobsearch (NDJSON); GET /api/jobsearch/latest, .../runs/{id}/jobs/{id}/posting;
              POST /api/jobsearch/prepare, /api/jobsearch/referral (search with AI)
    interview POST /api/interview/plan, /api/interview/sessions; GET /api/interview/sessions, .../sessions/{id};
              POST .../sessions/{id}/answer, .../work, .../assistant, .../end, .../again, .../facts;
              DELETE .../sessions/{id} (practice the interview)
    speech    GET /api/speech/status; POST /api/speech/transcribe (raw audio), /api/speech/speak (Sarvam AI speech for
              practice interviews)
    handoff   POST /api/handoff (multipart form from the marketing site -> 303 to /?handoff=<id> or /?handoff_error=<code>);
              GET /api/handoff/{id} (the hand-off, once)
    prepare   POST /api/prepare (NDJSON); GET /api/prepare/{id}; PUT .../state; GET .../repos; POST .../headline
              ("How do I prepare for this role?" as blocks)

Each module only validates input, applies the guards (rate limit, spend ceiling) in the documented order and
hands over to a service from the `Container`. Failures before a stream starts are JSON ``{"error": ...}``.
"""

from __future__ import annotations

from fastapi import APIRouter

from tayyari.api.routes import (
    analyze,
    auth,
    billing,
    chat,
    discover,
    handoff,
    health,
    interview,
    jobsearch,
    match,
    me,
    portfolio,
    prepare,
    profile,
    sample,
    speech,
)

api_router = APIRouter()
for _module in (
    health,
    analyze,
    chat,
    profile,
    sample,
    portfolio,
    auth,
    me,
    billing,
    match,
    discover,
    jobsearch,
    interview,
    speech,
    prepare,
    handoff,
):
    api_router.include_router(_module.router)

__all__ = ["api_router"]
