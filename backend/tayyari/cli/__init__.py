"""Command-line tools for the Tayyari backend: ``uv run python -m tayyari.cli <command>``.

    dev           API with auto-reload on 127.0.0.1 (the web app's Vite proxy targets this port)
    serve         API without reload (``--host``, ``--port``)
    migrate       bring the configured database to the newest schema
    smoke         real calls: Haiku structured, Sonnet chat + prompt cache, Jev, DOCX resume parse (costs a few cents)
    make-sample   regenerate the bundled sample profile with the real pipeline (costs a few cents)
    ingest-check  deterministic ingestion checks; the github and web sections use the network, no AI
    portfolio-eval  build a portfolio per fixture with the real clients and report grounding, cost and time
    deploy        build the web app, then ``fastapi deploy`` from backend/
    discover-refresh  read employers' own public job boards into the database, once (no key needed)
    discover-worker   the same in a loop, as its own process
    apply-worker      auto-apply's worker: fill queued applications, submit approved ones (needs AUTO_APPLY_ENABLED)
    apply-metrics     auto-apply's learning loop: playbook vs AI runs, success and failure reasons per ATS
    weekly            The Leafout Weekly by hand: `weekly run` (one scheduler pass), `weekly preview EMAIL` (one reader's edition)

This package is the one place that prints to the terminal. Importing it imports nothing heavy: every command
module is loaded only when its command runs.
"""
