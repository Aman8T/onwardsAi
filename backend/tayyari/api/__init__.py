"""The HTTP layer: request parsing, NDJSON streaming, error answers, request ids and the web app.

The modules here are infrastructure; the app factory and the routes sit next to them (written by the wiring unit).

    ndjson    NdjsonResponse: NDJSON event stream with heartbeats and disconnect handling
    uploads   parse_analyze_form (streamed multipart with hard caps), read_json_body, parse_links
    errors    install_error_handlers: AppError, HTTPException, validation, disconnect and 500 answers as JSON
    static    mount_spa: the built web app and the /api 404 (register it after every other route)
    deps      get_request_context (request id, client IP, ip hash) and RequestIdMiddleware

`api` is the top of the layering rule (core < contract < llm < decisions < analysis/chat < api). Import the
submodule you need; this package imports nothing eagerly.
"""
