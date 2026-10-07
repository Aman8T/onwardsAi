"""Tayyari API on FastAPI: resume and link analysis, skill-graph synthesis and career chat.

A Python port of the Node/Express server in ../../server. It speaks the same HTTP contract and
NDJSON event stream, so the built web app in ../static runs unmodified.
"""

import sys

__version__ = "0.1.0"

# Logs carry model and page text. A legacy console (Windows cp1252) cannot encode all of it, and a failed
# print inside an error handler would hide the real error, so unencodable characters are escaped instead.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # replaced by a test harness or already closed
        pass
