"""Cost and abuse protection for a public URL: who is asking, how often, and whether AI is still affordable.

    client_ip   the client's address from X-Forwarded-For (trusted hops) or a platform header; `ip_subject` and
                `ip_subjects` (the /64 and, for IPv6, the wider /48 a limit counts)
    ratelimit   RateLimiter: per-IP minute/hour/day windows (IPv6 also per /48 at a higher limit) and the global
                analyze and chat backstops (database counters shared across replicas, an in-memory token
                bucket in front, fail open on repository errors)
    slots       StreamSlots: concurrent long-lived streams per client and per process, refused without waiting
    spend       SpendGuard: the daily spend ceiling (`reserve` around a request, `admit` / `admit_call` checks,
                `tripped` for synchronous callers)

`guard` depends on `core`, `llm.errors` and the `storage.ports` Protocols only, so it is tested with the fakes.
"""

from tayyari.guard.client_ip import client_ip, ip_subject, ip_subjects
from tayyari.guard.ratelimit import InProcessBucket, RateLimiter, Route
from tayyari.guard.slots import StreamSlots
from tayyari.guard.spend import SpendGuard

__all__ = [
    "InProcessBucket",
    "RateLimiter",
    "Route",
    "SpendGuard",
    "StreamSlots",
    "client_ip",
    "ip_subject",
    "ip_subjects",
]
