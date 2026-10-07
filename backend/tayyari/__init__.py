"""Tayyari backend: resume and link analysis, skill-graph synthesis, Jev decisions and career chat.

Serves the HTTP contract and NDJSON event streams in shared/types.ts, so the built web app in
../static runs unmodified. See docs/backend.md for the module map.
"""

from .core.logging import reconfigure_streams

__version__ = "0.2.0"

# Logs carry model and page text. A legacy console (Windows cp1252) cannot encode all of it, and a failed
# print inside an error handler would hide the real error, so unencodable characters are escaped instead.
reconfigure_streams()
