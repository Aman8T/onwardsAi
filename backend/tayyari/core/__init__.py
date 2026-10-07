"""Foundations shared by every other package: settings, logging, errors, ids, clock, limits and helpers.

`core` sits at the bottom of the layering rule (core < contract < llm < decisions < analysis/chat < api),
so nothing in here imports another `tayyari` package. Importing the package itself has no side effects
(no environment reads, no logging configuration); import the submodule you need:

    settings   Settings / get_settings()         limits    every size and count limit
    paths      repo, package and static folders   text      plural, clip, slug ... shared string helpers
    logging    configure_logging / get_logger     redact    PII masking and the log redaction processor
    context    request / analysis / profile ids   hashing   canonical JSON, sha256, hmac
    errors     AppError and friends               ids       profile, session and uuid7 ids
    clock      Clock protocol and pacing          dburl     DATABASE_URL -> async SQLAlchemy config
"""
