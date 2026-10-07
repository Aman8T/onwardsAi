"""Cost and abuse protection for a public URL: who is asking, how often, and whether AI is still affordable.

    client_ip   the client's address from X-Forwarded-For (trusted hops) or a platform header; `ip_subject`
    ratelimit   RateLimiter: per-IP minute/hour/day windows and a global analyze backstop (database counters
                shared across replicas, an in-memory token bucket in front, fail open on repository errors)
    spend       SpendGuard: the daily spend ceiling (`admit` before a request, `admit_call` before each model
                call, `tripped` for synchronous callers)

`guard` depends on `core`, `llm.errors` and the `storage.ports` Protocols only, so it is tested with the fakes.
"""

from tayyari.guard.client_ip import client_ip, ip_subject
from tayyari.guard.ratelimit import InProcessBucket, RateLimiter, Route
from tayyari.guard.spend import SpendGuard

__all__ = [
    "InProcessBucket",
    "RateLimiter",
    "Route",
    "SpendGuard",
    "client_ip",
    "ip_subject",
]
