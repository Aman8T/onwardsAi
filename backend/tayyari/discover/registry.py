"""The employer registry: which boards Discover reads, and where each employer has offices in India.

The file (``data/employers.json``, written by hand and checked against the live boards) names, for every employer, its
applicant tracking system (``greenhouse`` or ``lever``), the board token, the careers page and its offices. City and
locality names are ours (`places`) and must match exactly. `load_registry` validates all of it and lists every problem
at once, so a bad edit is one fix, not ten.

The board token goes into a URL, so only ``[A-Za-z0-9._-]`` is accepted. Nothing else is ever fetched: no careers-page
crawling, no job board that forbids automation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from . import places
from .errors import RegistryError

DEFAULT_PATH: Final = Path(__file__).resolve().parent / "data" / "employers.json"
SUPPORTED_VERSION: Final = 1
Ats = Literal["greenhouse", "lever"]
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_BOARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_ATS: Final = ("greenhouse", "lever")


@dataclass(frozen=True, slots=True)
class Office:
    city: str
    locality: str | None = None


@dataclass(frozen=True, slots=True)
class Employer:
    id: str
    name: str
    ats: Ats
    board: str
    careers_url: str
    offices: tuple[Office, ...]
    families: tuple[str, ...] = ()
    india_jobs_at_verify: int = 0

    def sole_locality(self, city: str) -> str | None:
        """The one locality the employer lists in ``city``, when it lists exactly one office there with a locality and no
        other office in that city (so a listing in ``city`` is safely in that locality). Never a guess."""
        in_city = [office for office in self.offices if office.city == city]
        localities = {office.locality for office in in_city if office.locality}
        if len(in_city) == 1 and len(localities) == 1:
            return next(iter(localities))
        return None


@dataclass(frozen=True, slots=True)
class Registry:
    version: int
    verified_at: str
    employers: tuple[Employer, ...]

    def get(self, employer_id: str) -> Employer | None:
        for employer in self.employers:
            if employer.id == employer_id:
                return employer
        return None

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(employer.id for employer in self.employers)


EMPTY: Final = Registry(SUPPORTED_VERSION, "", ())
"""What the service uses when the file cannot be read: nothing is fetched and the refresh says why."""


def _http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def parse_registry(data: Any) -> Registry:
    """Validate parsed JSON as a registry; `RegistryError` lists every problem found."""
    problems: list[str] = []
    if not isinstance(data, dict):
        raise RegistryError("the registry must be a JSON object")
    if data.get("version") != SUPPORTED_VERSION:
        problems.append(f"version must be {SUPPORTED_VERSION}")
    raw_employers = data.get("employers")
    if not isinstance(raw_employers, list):
        raise RegistryError("; ".join([*problems, "employers must be a list"]))
    employers: list[Employer] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_employers):
        where = f"employers[{index}]"
        if not isinstance(raw, dict):
            problems.append(f"{where} must be an object")
            continue
        employer_id = raw.get("id")
        if isinstance(employer_id, str):
            where = f"{where} ({employer_id})"
        if not isinstance(employer_id, str) or not _ID.fullmatch(employer_id):
            problems.append(f"{where}: id must be a lowercase slug")
            continue
        if employer_id in seen:
            problems.append(f"{where}: id is used twice")
            continue
        seen.add(employer_id)
        before = len(problems)
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{where}: name is needed")
        ats = raw.get("ats")
        if ats == "greenhouse" or ats == "lever":
            kind: Ats = ats
        else:
            kind = "greenhouse"
            problems.append(f"{where}: ats must be one of {', '.join(_ATS)}")
        board = raw.get("board")
        if not isinstance(board, str) or not _BOARD.fullmatch(board):
            problems.append(f"{where}: board must be a board token ([A-Za-z0-9._-])")
        if not _http_url(raw.get("careersUrl")):
            problems.append(f"{where}: careersUrl must be an http(s) link")
        offices = _offices(raw.get("offices"), where, problems)
        families = raw.get("families", [])
        if not isinstance(families, list) or not all(isinstance(f, str) for f in families):
            problems.append(f"{where}: families must be a list of text")
            families = []
        at_verify = raw.get("indiaJobsAtVerify", 0)
        if not isinstance(at_verify, int) or isinstance(at_verify, bool) or at_verify < 0:
            problems.append(f"{where}: indiaJobsAtVerify must be a whole number")
            at_verify = 0
        if len(problems) == before:
            employers.append(
                Employer(
                    id=employer_id,
                    name=str(name).strip(),
                    ats=kind,
                    board=str(board),
                    careers_url=str(raw["careersUrl"]),
                    offices=offices,
                    families=tuple(families),
                    india_jobs_at_verify=at_verify,
                )
            )
    if problems:
        raise RegistryError("; ".join(problems))
    verified = data.get("verifiedAt")
    return Registry(SUPPORTED_VERSION, verified if isinstance(verified, str) else "", tuple(employers))


def _offices(raw: object, where: str, problems: list[str]) -> tuple[Office, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(f"{where}: offices must be a list")
        return ()
    offices: list[Office] = []
    for number, item in enumerate(raw):
        spot = f"{where}: offices[{number}]"
        city_name = item.get("city") if isinstance(item, dict) else None
        city = places.city_by_name(city_name) if isinstance(city_name, str) else None
        if city is None or city.name != city_name:
            problems.append(f"{spot}: city {city_name!r} is not one of our cities")
            continue
        locality_name = item.get("locality")
        if locality_name is None:
            offices.append(Office(city.name))
            continue
        locality = places.locality_in(city.name, locality_name) if isinstance(locality_name, str) else None
        if locality is None or locality.name != locality_name:
            problems.append(f"{spot}: locality {locality_name!r} is not one of {city.name}'s")
            continue
        offices.append(Office(city.name, locality.name))
    return tuple(offices)


def load_registry(path: Path | None = None) -> Registry:
    """Read and validate the registry at ``path`` (default: the one shipped with the package)."""
    target = path or DEFAULT_PATH
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"cannot read the employer registry ({type(exc).__name__})") from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise RegistryError("the employer registry is not valid JSON") from None
    return parse_registry(data)


__all__ = [
    "DEFAULT_PATH",
    "EMPTY",
    "SUPPORTED_VERSION",
    "Ats",
    "Employer",
    "Office",
    "Registry",
    "load_registry",
    "parse_registry",
]
