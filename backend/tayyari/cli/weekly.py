"""``weekly run`` and ``weekly preview``: The Leafout Weekly by hand.

``weekly run`` does one pass of the scheduler now, with the same service the background loop calls: it composes and emails the
editions that are due, but only on a reader's day and from 06:00 India time, so on any other day it says there is nothing to
do. ``weekly preview EMAIL`` builds this week's edition for one account (the stored one when it is already built), prints its id,
and writes its email as an HTML file under the data folder so the layout can be opened in a browser. A build is one paid run (a few
cents: up to three web searches, one slot-filling call and one read of the lead opening) when ANTHROPIC_API_KEY is set; without a
key the paid parts are skipped and the edition is built from the graph alone. Nothing is emailed by ``preview``.

Both build a full `Container` on the configured database (``DATABASE_URL``; SQLite under the user data folder when unset), with
the background loop switched off for this process.
"""

from __future__ import annotations

import asyncio

from tayyari.container import Container
from tayyari.core.settings import Settings, get_settings


def _settings() -> Settings:
    settings = get_settings()
    copy = settings.model_copy(update={"weekly": settings.weekly.model_copy(update={"enabled": False})})
    copy.__dict__.pop("db", None)
    return copy


async def _run_pass(settings: Settings) -> int:
    container = await Container.build(settings)
    try:
        report = await container.weekly.run_once()
        if not report.days:
            print(
                "Not a sending day (editions go out on a reader's day from 06:00 India time); nothing was done."
            )
            return 0
        print(
            f"Weekly pass for {', '.join(report.days)}: composed {report.composed}, emailed {report.sent}, "
            f"failed {report.failed}, held by another worker or out of tries {report.skipped}."
        )
        if report.no_mailer:
            print(
                f"{report.no_mailer} built edition(s) were not emailed: no mail provider is set (RESEND_API_KEY). "
                "Their emails were written to the data folder."
            )
        return 1 if report.failed else 0
    finally:
        await container.aclose()


async def _preview(settings: Settings, email: str) -> int:
    container = await Container.build(settings)
    try:
        user = await container.repos.users.get_by_email(email.strip().lower())
        if user is None:
            print("No account has that email address.")
            return 1
        row = await container.weekly.build(user.id)
        if row is None:
            print("No account has that email address.")
            return 1
        path = await container.weekly.local_copy(user.id, row)
        print(f"Edition {row.id} (issue {row.issue}, week of {row.week_of.isoformat()}) is stored.")
        print(f"Email HTML: {path}" if path is not None else "The email's HTML could not be written.")
        return 0
    finally:
        await container.aclose()


def run_pass() -> int:
    """One scheduler pass now. Returns 1 when a reader's edition failed."""
    return asyncio.run(_run_pass(_settings()))


def run_preview(email: str) -> int:
    """Build and store this week's edition for the account with this email; print its id and the local HTML path."""
    return asyncio.run(_preview(_settings(), email))
