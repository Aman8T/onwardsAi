"""Alembic environment: runs migrations on the connection `tayyari.storage.migrate` provides.

Never run by the ``alembic`` command line (there is no ini file) and never offline: the connection comes
from ``config.attributes["connection"]``, so the advisory lock and the migration share one transaction.
"""

from __future__ import annotations

from alembic import context

from tayyari.storage.tables import metadata

config = context.config


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("migrations must be run through tayyari.storage.migrate (no connection given)")
    context.configure(
        connection=connection,
        target_metadata=metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
run_migrations_online()
