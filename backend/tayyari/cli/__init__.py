"""Command-line tools for the Tayyari backend: ``uv run python -m tayyari.cli <command>``.

    dev           API with auto-reload on 127.0.0.1 (the web app's Vite proxy targets this port)
    serve         API without reload (``--host``, ``--port``)
    migrate       bring the configured database to the newest schema
    smoke         real calls: Haiku structured, Sonnet chat + prompt cache, Jev, DOCX resume parse (costs a few cents)
    make-sample   regenerate the bundled sample profile with the real pipeline (costs a few cents)
    ingest-check  deterministic ingestion checks; the github and web sections use the network, no AI
    deploy        build the web app, then ``fastapi deploy`` from backend/

This package is the one place that prints to the terminal. Importing it imports nothing heavy: every command
module is loaded only when its command runs.
"""
