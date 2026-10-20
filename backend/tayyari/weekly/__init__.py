"""The Leafout Weekly: a personal old-style newspaper, built every week from the reader's own graph and emailed on their day.

    times      India time and the week an edition belongs to
    copy       the tips and quotes, written in code and rotated by issue number
    progress   the plan, closest role, last practice and nudge, all worked out in code
    jobs       the reader's top openings: the job search's narrowing, the quick fit, one checked read of the lead
    news       web searches, one slot-filling call, a verifier (`ground`)
    ground     keeps only the news items the search results and the graph back
    compose    one edition: code chooses, models only read and phrase; the paid parts sit inside one spend reservation
    render     the email (table layout, inline styles, plain text) and the unsubscribe pages
    service    subscriptions, editions and the scheduler pass (claims so replicas never double up)

Layering: this package sits with the model-calling services. It reuses the job search's narrowing, judge prompt and checks, and
Discover's quick fit, and reaches storage only through the Protocols in `storage.ports`. Nothing imports it but `api` and the
container. Contract: `shared/weekly.ts` and `contract/weekly.py`.
"""

from tayyari.weekly.service import RunReport, WeeklyService, edition_of

__all__ = ["RunReport", "WeeklyService", "edition_of"]
