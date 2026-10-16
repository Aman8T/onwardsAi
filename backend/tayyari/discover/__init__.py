"""Discover: the board store behind Search with AI, built from employers' own public job boards.

    service    `DiscoverService`: the city list, and the refresh that keeps the store current
    refresh    reading the boards (Greenhouse and Lever, public and keyless) into the store; single flight, never blocking
    registry   the curated list of employers (`data/employers.json`) and its validation
    parse      a board's JSON into India listings: place, work mode, salary (Lever only), apply link, freshness inputs
    places     the cities and localities we know (approximate centroids) and straight-line distance
    vocab      about 400 skill terms across professions and the matcher that reads a posting with them
    quickfit   the quick fit: a word and counts from the skill names a posting uses; never a score, never a percentage
    person     a `Profile` read for matching
    textutil   HTML to text, requirement lines, years asked
    normalise  titles, apply links, ids

`jobsearch` (Search with AI) is the only screen on top of it: it reads the store and the registry, parses boards with
`parse`, narrows with `person`, `vocab` and `quickfit`, and takes the city list from `places`.

A self-contained service like billing: it imports only `core`, `contract` and `storage.ports` (a test enforces it), owns its
tables, settings and HTTP client. No model is called anywhere. The city a person looks in is chosen in the browser and
sent with each request; the server never stores it.
"""

from tayyari.discover.errors import DiscoverDisabled, RegistryError
from tayyari.discover.refresh import Refresher, RefreshReport
from tayyari.discover.registry import Employer, Registry, load_registry
from tayyari.discover.service import DiscoverService

__all__ = [
    "DiscoverDisabled",
    "DiscoverService",
    "Employer",
    "RefreshReport",
    "Refresher",
    "Registry",
    "RegistryError",
    "load_registry",
]
