"""The places Discover knows: names are a contract with the registry and the pickers, and coordinates are sane."""

from __future__ import annotations

import pytest

from tayyari.discover import places

EXPECTED: dict[str, list[str]] = {
    "Bengaluru": [
        "Koramangala", "HSR Layout", "Indiranagar", "Whitefield", "Bellandur", "Marathahalli", "Electronic City",
        "Jayanagar", "JP Nagar", "BTM Layout", "MG Road", "Hebbal", "Manyata Tech Park", "Yeshwanthpur",
        "Malleshwaram", "Sarjapur Road", "Domlur", "Rajajinagar",
    ],
    "Hyderabad": [
        "HITEC City", "Gachibowli", "Madhapur", "Kondapur", "Financial District", "Nanakramguda", "Banjara Hills",
        "Jubilee Hills", "Begumpet", "Kukatpally", "Secunderabad",
    ],
    "Pune": [
        "Hinjewadi", "Kharadi", "Viman Nagar", "Baner", "Koregaon Park", "Magarpatta", "Hadapsar", "Kothrud",
        "Wakad", "Aundh", "Yerawada", "Shivajinagar",
    ],
    "Chennai": [
        "Sholinganallur", "Perungudi", "Thoraipakkam", "Taramani", "Guindy", "Velachery", "T. Nagar", "Adyar",
        "Anna Nagar", "Nungambakkam", "Ambattur",
    ],
    "Mumbai": [
        "Andheri", "Bandra Kurla Complex", "Lower Parel", "Powai", "Goregaon", "Malad", "Vikhroli", "Worli",
        "Nariman Point", "Fort", "Thane", "Navi Mumbai",
    ],
    "Delhi NCR": [
        "Connaught Place", "Saket", "Okhla", "Nehru Place", "Aerocity", "Dwarka", "Gurugram Cyber City",
        "Gurugram Golf Course Road", "Gurugram Sohna Road", "Noida Sector 62", "Noida Sector 18", "Noida Sector 132",
    ],
    "Kolkata": ["Salt Lake Sector V", "New Town", "Park Street", "Ballygunge"],
    "Ahmedabad": ["SG Highway", "Prahlad Nagar", "Navrangpura", "GIFT City"],
    "Coimbatore": ["Peelamedu", "RS Puram", "Saravanampatti", "Gandhipuram"],
    "Kochi": ["Kakkanad", "Edappally", "Vyttila"],
    "Thiruvananthapuram": ["Technopark", "Kazhakoottam"],
    "Jaipur": ["Malviya Nagar", "Sitapura", "C-Scheme"],
    "Chandigarh": ["IT Park", "Sector 17", "Mohali"],
    "Indore": ["Vijay Nagar"],
}  # fmt: skip


def test_the_cities_and_localities_are_exactly_the_ones_the_registry_is_written_against() -> None:
    assert {c.name: [loc.name for loc in c.localities] for c in places.CITIES} == EXPECTED


def test_every_locality_knows_its_city_and_is_near_it() -> None:
    for city in places.CITIES:
        assert 6 < city.lat < 36 and 68 < city.lng < 98  # inside India's bounding box
        for locality in city.localities:
            assert locality.city == city.name
            # A typo in a coordinate shows up as a locality tens of kilometres from its own city.
            assert places.distance_km(city.lat, city.lng, locality.lat, locality.lng) < 45, locality.name


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("Bangalore, India", "Bengaluru"),
        ("Bengaluru Urban", "Bengaluru"),
        ("Gurgaon, Haryana", "Delhi NCR"),
        ("Greater Noida", "Delhi NCR"),
        ("New Delhi", "Delhi NCR"),
        ("Navi Mumbai, Maharashtra", "Mumbai"),
        ("Thane", "Mumbai"),
        ("Madras", "Chennai"),
        ("Cochin", "Kochi"),
        ("Trivandrum, Kerala", "Thiruvananthapuram"),
        ("Mohali, Punjab", "Chandigarh"),
        ("Secunderabad", "Hyderabad"),
        ("Pune; Mumbai", "Pune"),
        ("Remote or Hyderabad", "Hyderabad"),
    ],
)
def test_a_city_is_found_by_its_name_or_an_alias(text: str, city: str) -> None:
    found = places.find_city(text)
    assert found is not None and found.name == city


@pytest.mark.parametrize("text", ["Berlin, Germany", "Remote", "", None, "Bangalorean pride", "Punekar"])
def test_other_places_are_not_found(text: str | None) -> None:
    assert places.find_city(text) is None


def test_a_locality_is_found_only_among_the_localities_of_its_city() -> None:
    found = places.find_locality("Koramangala, Bangalore", "Bengaluru")
    assert found is not None and found.name == "Koramangala"
    assert places.find_locality("Koramangala, Bangalore", "Pune") is None
    sector = places.find_locality("Sector 62, Noida", "Delhi NCR")
    assert sector is not None and sector.name == "Noida Sector 62"
    assert places.find_locality("Sector 62", "Bengaluru") is None
    assert places.find_locality("Hi-Tech City, Hyderabad", "Hyderabad").name == "HITEC City"  # type: ignore[union-attr]
    assert places.find_locality("Bengaluru", "Bengaluru") is None  # a city alone is not a locality


def test_names_are_looked_up_by_name_alias_and_case() -> None:
    assert places.city_by_name("bangalore").name == "Bengaluru"  # type: ignore[union-attr]
    assert places.city_by_name("  DELHI NCR ").name == "Delhi NCR"  # type: ignore[union-attr]
    assert places.city_by_name("Atlantis") is None and places.city_by_name(None) is None
    assert places.locality_in("Bengaluru", "hsr layout").name == "HSR Layout"  # type: ignore[union-attr]
    assert places.locality_in("Pune", "HSR Layout") is None
    assert places.locality_in(None, "HSR Layout") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Remote - India", True),
        ("Pune", True),
        ("Karnataka", True),
        ("Remote - United States", False),
        ("Remote", False),
        ("Indianapolis, IN", False),
        (None, False),
    ],
)
def test_a_text_names_india_by_the_country_a_state_or_one_of_our_cities(
    text: str | None, expected: bool
) -> None:
    assert places.names_india(text) is expected


def test_distance_is_a_straight_line_in_kilometres() -> None:
    hsr = places.locality_in("Bengaluru", "HSR Layout")
    kora = places.locality_in("Bengaluru", "Koramangala")
    assert hsr is not None and kora is not None
    assert 2.5 < places.distance_km(hsr.lat, hsr.lng, kora.lat, kora.lng) < 5
    assert places.distance_km(hsr.lat, hsr.lng, hsr.lat, hsr.lng) == 0
    blr, mum = places.city_by_name("Bengaluru"), places.city_by_name("Mumbai")
    assert blr is not None and mum is not None
    assert 800 < places.distance_km(blr.lat, blr.lng, mum.lat, mum.lng) < 900
