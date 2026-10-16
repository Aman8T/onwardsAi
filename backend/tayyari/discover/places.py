"""The cities and localities Discover knows, and the straight-line distance between two of them.

The names are a contract with the employer registry (`data/employers.json`) and with the web app's pickers
(``GET /api/discover/cities``): an office or a person's area uses a name from here exactly. Aliases (``Bangalore`` for
Bengaluru, ``Gurgaon`` for Delhi NCR) only help reading what an employer or a resume writes; they are never shown.

Coordinates are **approximate centroids**, taken to about three decimals. They exist only to work out a straight-line
distance in kilometres between two localities; they are not addresses, never drawn on a map, and good to a kilometre or
so. A distance is shown only when both ends are known to a locality, and always as a straight line. Nothing in this
module reads a person's data.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import cache, lru_cache
from typing import Final

EARTH_RADIUS_KM: Final = 6371.0088
"""The mean radius of the Earth."""


@dataclass(frozen=True, slots=True)
class Locality:
    name: str
    city: str
    lat: float
    lng: float
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class City:
    name: str
    lat: float
    lng: float
    aliases: tuple[str, ...]
    localities: tuple[Locality, ...] = field(default=())


def _locs(city: str, rows: list[tuple[str, float, float, tuple[str, ...]]]) -> tuple[Locality, ...]:
    return tuple(Locality(name, city, lat, lng, aliases) for name, lat, lng, aliases in rows)


CITIES: Final[tuple[City, ...]] = (
    City(
        "Bengaluru",
        12.972,
        77.595,
        ("bangalore", "bengaluru urban"),
        _locs(
            "Bengaluru",
            [
                ("Koramangala", 12.935, 77.625, ("kormangala",)),
                ("HSR Layout", 12.912, 77.647, ("hsr",)),
                ("Indiranagar", 12.978, 77.641, ("indira nagar",)),
                ("Whitefield", 12.970, 77.750, ("itpl",)),
                ("Bellandur", 12.930, 77.678, ()),
                ("Marathahalli", 12.959, 77.697, ("marathalli",)),
                ("Electronic City", 12.845, 77.660, ("electronics city",)),
                ("Jayanagar", 12.925, 77.594, ()),
                ("JP Nagar", 12.906, 77.586, ("j p nagar", "j.p. nagar")),
                ("BTM Layout", 12.917, 77.610, ("btm",)),
                ("MG Road", 12.976, 77.607, ("m g road", "mahatma gandhi road")),
                ("Hebbal", 13.036, 77.597, ()),
                ("Manyata Tech Park", 13.046, 77.621, ("manyata",)),
                ("Yeshwanthpur", 13.029, 77.539, ("yeshvantpur",)),
                ("Malleshwaram", 13.004, 77.564, ("malleswaram",)),
                ("Sarjapur Road", 12.910, 77.686, ("sarjapur",)),
                ("Domlur", 12.961, 77.639, ()),
                ("Rajajinagar", 12.992, 77.552, ("rajaji nagar",)),
            ],
        ),
    ),
    City(
        "Hyderabad",
        17.385,
        78.487,
        ("secunderabad",),
        _locs(
            "Hyderabad",
            [
                ("HITEC City", 17.444, 78.377, ("hi-tec city", "hitech city", "hi-tech city", "hitec")),
                ("Gachibowli", 17.440, 78.349, ()),
                ("Madhapur", 17.448, 78.392, ()),
                ("Kondapur", 17.460, 78.364, ()),
                ("Financial District", 17.417, 78.343, ()),
                ("Nanakramguda", 17.414, 78.344, ()),
                ("Banjara Hills", 17.416, 78.435, ()),
                ("Jubilee Hills", 17.433, 78.407, ()),
                ("Begumpet", 17.444, 78.463, ()),
                ("Kukatpally", 17.495, 78.400, ()),
                ("Secunderabad", 17.434, 78.501, ()),
            ],
        ),
    ),
    City(
        "Pune",
        18.520,
        73.857,
        (),
        _locs(
            "Pune",
            [
                ("Hinjewadi", 18.591, 73.739, ("hinjawadi",)),
                ("Kharadi", 18.552, 73.935, ()),
                ("Viman Nagar", 18.568, 73.914, ("vimannagar",)),
                ("Baner", 18.559, 73.787, ()),
                ("Koregaon Park", 18.536, 73.894, ()),
                ("Magarpatta", 18.515, 73.930, ()),
                ("Hadapsar", 18.509, 73.926, ()),
                ("Kothrud", 18.507, 73.808, ()),
                ("Wakad", 18.598, 73.761, ()),
                ("Aundh", 18.558, 73.808, ()),
                ("Yerawada", 18.553, 73.885, ("yerwada",)),
                ("Shivajinagar", 18.531, 73.848, ("shivaji nagar",)),
            ],
        ),
    ),
    City(
        "Chennai",
        13.083,
        80.271,
        ("madras",),
        _locs(
            "Chennai",
            [
                ("Sholinganallur", 12.901, 80.228, ()),
                ("Perungudi", 12.965, 80.246, ()),
                ("Thoraipakkam", 12.941, 80.237, ()),
                ("Taramani", 12.985, 80.242, ()),
                ("Guindy", 13.007, 80.221, ()),
                ("Velachery", 12.982, 80.218, ()),
                ("T. Nagar", 13.042, 80.234, ("t nagar", "t.nagar", "thyagaraya nagar")),
                ("Adyar", 13.001, 80.257, ()),
                ("Anna Nagar", 13.085, 80.210, ()),
                ("Nungambakkam", 13.057, 80.243, ()),
                ("Ambattur", 13.114, 80.155, ()),
            ],
        ),
    ),
    City(
        "Mumbai",
        19.076,
        72.878,
        ("bombay", "navi mumbai", "thane"),
        _locs(
            "Mumbai",
            [
                ("Andheri", 19.114, 72.870, ()),
                ("Bandra Kurla Complex", 19.067, 72.869, ("bkc", "bandra kurla", "bandra-kurla")),
                ("Lower Parel", 18.993, 72.830, ()),
                ("Powai", 19.118, 72.906, ()),
                ("Goregaon", 19.166, 72.853, ()),
                ("Malad", 19.187, 72.848, ()),
                ("Vikhroli", 19.110, 72.925, ()),
                ("Worli", 19.018, 72.815, ()),
                ("Nariman Point", 18.926, 72.824, ()),
                ("Fort", 18.934, 72.835, ()),
                ("Thane", 19.218, 72.978, ()),
                ("Navi Mumbai", 19.033, 73.030, ("vashi", "airoli", "belapur", "nerul")),
            ],
        ),
    ),
    City(
        "Delhi NCR",
        28.614,
        77.209,
        (
            "delhi",
            "new delhi",
            "gurgaon",
            "gurugram",
            "noida",
            "greater noida",
            "faridabad",
            "ghaziabad",
        ),
        _locs(
            "Delhi NCR",
            [
                ("Connaught Place", 28.632, 77.217, ()),
                ("Saket", 28.525, 77.207, ()),
                ("Okhla", 28.530, 77.275, ()),
                ("Nehru Place", 28.549, 77.253, ()),
                ("Aerocity", 28.556, 77.120, ()),
                ("Dwarka", 28.592, 77.046, ()),
                ("Gurugram Cyber City", 28.495, 77.089, ("cyber city", "cybercity", "cyber hub")),
                ("Gurugram Golf Course Road", 28.443, 77.101, ("golf course road",)),
                ("Gurugram Sohna Road", 28.410, 77.045, ("sohna road",)),
                ("Noida Sector 62", 28.627, 77.365, ("sector 62",)),
                ("Noida Sector 18", 28.570, 77.326, ("sector 18",)),
                ("Noida Sector 132", 28.500, 77.402, ("sector 132",)),
            ],
        ),
    ),
    City(
        "Kolkata",
        22.573,
        88.364,
        ("calcutta",),
        _locs(
            "Kolkata",
            [
                ("Salt Lake Sector V", 22.576, 88.433, ("sector v", "sector 5")),
                ("New Town", 22.580, 88.478, ("newtown",)),
                ("Park Street", 22.553, 88.351, ()),
                ("Ballygunge", 22.525, 88.365, ()),
            ],
        ),
    ),
    City(
        "Ahmedabad",
        23.023,
        72.571,
        ("gandhinagar",),
        _locs(
            "Ahmedabad",
            [
                ("SG Highway", 23.035, 72.509, ("s.g. highway", "sarkhej gandhinagar highway", "sg road")),
                ("Prahlad Nagar", 23.012, 72.509, ("prahladnagar",)),
                ("Navrangpura", 23.037, 72.560, ()),
                ("GIFT City", 23.160, 72.682, ("gift-city",)),
            ],
        ),
    ),
    City(
        "Coimbatore",
        11.017,
        76.956,
        (),
        _locs(
            "Coimbatore",
            [
                ("Peelamedu", 11.027, 77.020, ()),
                ("RS Puram", 11.007, 76.949, ("r.s. puram", "r s puram")),
                ("Saravanampatti", 11.078, 77.000, ()),
                ("Gandhipuram", 11.017, 76.966, ()),
            ],
        ),
    ),
    City(
        "Kochi",
        9.931,
        76.267,
        ("cochin",),
        _locs(
            "Kochi",
            [
                ("Kakkanad", 10.016, 76.342, ()),
                ("Edappally", 10.026, 76.308, ()),
                ("Vyttila", 9.968, 76.319, ()),
            ],
        ),
    ),
    City(
        "Thiruvananthapuram",
        8.524,
        76.937,
        ("trivandrum",),
        _locs(
            "Thiruvananthapuram",
            [
                ("Technopark", 8.557, 76.881, ()),
                ("Kazhakoottam", 8.568, 76.873, ("kazhakkoottam",)),
            ],
        ),
    ),
    City(
        "Jaipur",
        26.912,
        75.787,
        (),
        _locs(
            "Jaipur",
            [
                ("Malviya Nagar", 26.854, 75.811, ()),
                ("Sitapura", 26.791, 75.830, ()),
                ("C-Scheme", 26.907, 75.796, ("c scheme",)),
            ],
        ),
    ),
    City(
        "Chandigarh",
        30.733,
        76.779,
        ("mohali", "panchkula"),
        _locs(
            "Chandigarh",
            [
                ("IT Park", 30.726, 76.842, ("rajiv gandhi chandigarh technology park",)),
                ("Sector 17", 30.741, 76.783, ()),
                ("Mohali", 30.705, 76.718, ()),
            ],
        ),
    ),
    City(
        "Indore",
        22.720,
        75.858,
        (),
        _locs("Indore", [("Vijay Nagar", 22.753, 75.893, ())]),
    ),
)

INDIA_NAMES: Final[tuple[str, ...]] = (
    "india",
    "karnataka",
    "maharashtra",
    "telangana",
    "tamil nadu",
    "haryana",
    "uttar pradesh",
    "gujarat",
    "west bengal",
    "kerala",
    "rajasthan",
    "punjab",
    "madhya pradesh",
)
"""Words that say a place is in India: the country, and the states our cities are in."""

_STOP_CHARS: Final = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Lower case with every run of punctuation turned into one space (``Gurugram, Haryana`` -> ``gurugram haryana``)."""
    return _STOP_CHARS.sub(" ", text.lower()).strip()


def _word_regex(words: list[str]) -> re.Pattern[str]:
    ordered = sorted({_norm(w) for w in words if _norm(w)}, key=lambda w: (-len(w), w))
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in ordered) + r")(?![a-z0-9])")


@lru_cache(maxsize=1)
def _city_index() -> tuple[re.Pattern[str], dict[str, City]]:
    names: dict[str, City] = {}
    for city in CITIES:
        for word in (city.name, *city.aliases):
            names[_norm(word)] = city
    return _word_regex(list(names)), names


@lru_cache(maxsize=1)
def _india_regex() -> re.Pattern[str]:
    return _word_regex(list(INDIA_NAMES))


@cache
def _locality_index(city_name: str) -> tuple[re.Pattern[str], dict[str, Locality]] | None:
    city = city_by_name(city_name)
    if city is None or not city.localities:
        return None
    names: dict[str, Locality] = {}
    for locality in city.localities:
        for word in (locality.name, *locality.aliases):
            names[_norm(word)] = locality
    return _word_regex(list(names)), names


def city_by_name(name: str | None) -> City | None:
    """The city called ``name`` (or one of its aliases), ignoring case and punctuation, or None."""
    if not name:
        return None
    wanted = _norm(name)
    return _city_index()[1].get(wanted) if wanted else None


def locality_in(city_name: str | None, name: str | None) -> Locality | None:
    """The locality called ``name`` (or one of its aliases) of the city, or None."""
    if not name:
        return None
    index = _locality_index(city_name) if city_name else None
    if index is None:
        return None
    wanted = _norm(name)
    return index[1].get(wanted) if wanted else None


def find_city(text: str | None) -> City | None:
    """The city whose name or alias appears first in ``text`` (``Pune, Maharashtra, India`` is Pune), or None."""
    if not text:
        return None
    pattern, names = _city_index()
    found = pattern.search(_norm(text))
    return names[found.group(0)] if found else None


def find_locality(text: str | None, city_name: str | None) -> Locality | None:
    """The locality of the city whose name or alias appears first in ``text``, or None.

    Only the localities of ``city_name`` are looked for, so ``Sector 62`` means Noida's in Delhi NCR and nothing elsewhere.
    """
    if not text or not city_name:
        return None
    index = _locality_index(city_name)
    if index is None:
        return None
    found = index[0].search(_norm(text))
    return index[1][found.group(0)] if found else None


def names_india(text: str | None) -> bool:
    """True when ``text`` says India: the country, a state our cities are in, or one of our cities."""
    if not text:
        return False
    return _india_regex().search(_norm(text)) is not None or find_city(text) is not None


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """The straight-line (great-circle) distance in kilometres between two points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


__all__ = [
    "CITIES",
    "INDIA_NAMES",
    "City",
    "Locality",
    "city_by_name",
    "distance_km",
    "find_city",
    "find_locality",
    "locality_in",
    "names_india",
]
