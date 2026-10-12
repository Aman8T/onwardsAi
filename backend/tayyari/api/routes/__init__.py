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

Each module only validates input, applies the guards (rate limit, spend ceiling) in the documented order and
hands over to a service from the `Container`. Failures before a stream starts are JSON ``{"error": ...}``.
"""

from __future__ import annotations

from fastapi import APIRouter

from tayyari.api.routes import analyze, auth, billing, chat, health, match, me, portfolio, profile, sample

api_router = APIRouter()
for _module in (health, analyze, chat, profile, sample, portfolio, auth, me, billing, match):
    api_router.include_router(_module.router)

__all__ = ["api_router"]
