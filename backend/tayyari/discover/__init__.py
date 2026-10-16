"""Discover: personal, location-aware job discovery over employers' own public job boards.

    service    `DiscoverService`: the screen (lenses, lanes, groups, companies, coverage), one listing, the refresh
    refresh    reading the boards (Greenhouse and Lever, public and keyless) into the store; single flight, never blocking
    registry   the curated list of employers (`data/employers.json`) and its validation
    parse      a board's JSON into India listings: place, work mode, salary (Lever only), apply link, freshness inputs
    places     the cities and localities we know (approximate centroids) and straight-line distance
    vocab      about 400 skill terms across professions and the matcher that reads a posting with them
    quickfit   the quick fit: a word and counts from the skill names a posting uses; never a score, never a percentage
    groups     why a listing fits: next roles, current work, one skill away, first steps, other
    lens       how far, how wide, and the order
    freshness  the words for how recent a listing is
    person     a `Profile` read for matching
    textutil   HTML to text, requirement lines, years asked
    normalise  titles, apply links, ids

A self-contained service like billing: it imports only `core`, `contract` and `storage.ports` (a test enforces it), owns its
tables, settings and HTTP client, and takes a `Profile` and returns contract models. No model is called anywhere. The
person's area is chosen in the browser and sent with each request; the server never stores it.
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
