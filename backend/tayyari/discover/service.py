"""`DiscoverService`: personal, location-aware job discovery over employers' own public job boards.

    cities      the places we know, for the web app's pickers
    discover    one person's screen: lenses, lanes, groups, listings, the companies lane, coverage and why this order
    detail      one listing with the posting text the web app sends to the posting match
    refresh     read the boards into the store (also the CLI's ``discover-refresh`` and ``discover-worker``)

A self-contained service (like billing): it imports only `core`, `contract` and `storage.ports`, owns its tables, its
settings and its HTTP client, receives a `Profile` and returns contract models. No model is called anywhere: the quick fit,
the groups and the order are arithmetic over skill names and titles (`quickfit`, `groups`, `lens`).

**The person's area is not stored.** The city and locality arrive with each request, are used to answer it and are
forgotten. Nothing is written about a person: the quick fit of a listing for a person is computed on the fly and kept only
in a small in-memory cache keyed by a hash of their skills.

**Refresh never blocks a request.** A request that finds the last good refresh older than ``refresh_hours`` (or none) starts
one as a background task (`maybe_refresh`), guarded in process and, through the database, across replicas and the worker.
Until the first refresh has finished the answer is an empty list with a plain note.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal

import httpx2

from tayyari.contract.discover import (
    CompanyLead,
    DiscoverArea,
    DiscoverBreadth,
    DiscoverCitiesResponse,
    DiscoverCity,
    DiscoverCoverage,
    DiscoverQuery,
    DiscoverReach,
    DiscoverResponse,
    DiscoverSource,
    DiscoverWorkMode,
    Group,
    LaneCounts,
    LensStop,
    ListingCard,
    ListingDetail,
    Place,
    Salary,
)
from tayyari.contract.models import Profile
from tayyari.core.clock import Clock
from tayyari.core.errors import BadRequest
from tayyari.core.logging import get_logger
from tayyari.core.settings import DiscoverSettings
from tayyari.storage.ports import DiscoverListing, DiscoverRepo, DiscoverRun

from . import places
from .errors import DiscoverDisabled, RegistryError
from .freshness import age_from, checked_label, first_run_end, freshness_label
from .groups import Arrangement, arrange, group_reason, role_groups, static_claims
from .lens import (
    BREADTH_LABELS,
    BREADTH_ORDER,
    REACH_LABELS,
    REACH_ORDER,
    km_text,
    near_pool,
    order_candidates,
    stops,
    visible,
)
from .models import KIND_ORDER, Candidate, GroupDef
from .normalise import sentence_case, title_tokens
from .person import PersonIndex
from .quickfit import Fit, IndexCache, PostingIndex, quick_fit
from .refresh import STALE_RUN, Refresher, RefreshReport
from .registry import EMPTY, Registry, load_registry
from .vocab import vocabulary

log = get_logger(__name__)

THIN_NEAR_LANE: Final = 5
"""Fewer listings than this in the near lane and the coverage note says so."""
RETRY_AFTER_ATTEMPT: Final = timedelta(minutes=10)
"""After a refresh was started, no other is started by this process for this long (a failing network is not hammered)."""
TEXT_BATCH: Final = 150
FIT_CACHE_SIZE: Final = 40_000
MAX_COMPANIES: Final = 3
FIRST_READ_NOTE: Final = "Reading employers' job boards for the first time. This takes about a minute."
UNREADABLE_NOTE: Final = (
    "We could not read employers' job boards just now. Please try again in a little while."
)
PICK_CITY_NOTE: Final = "Pick your city to see roles near you."
THIN_NOTE: Final = (
    "Few roles like yours are shared with us yet. We read employers' own job boards, which lean toward tech and "
    "startups. Found a posting somewhere else? Paste it and we'll match it."
)
Lane = Literal["near", "remote", "city"]


@dataclass(frozen=True, slots=True)
class Area:
    """Where the person is for this one request. Never stored."""

    city: places.City | None
    city_from: Literal["resume", "you", "none"]
    resume_text: str | None
    locality: places.Locality | None
    other_city: places.City | None

    def model(self) -> DiscoverArea:
        return DiscoverArea(
            city=self.city.name if self.city else None,
            cityFrom=self.city_from,
            resumeText=self.resume_text,
            locality=self.locality.name if self.locality else None,
        )


@dataclass(slots=True)
class _Snapshot:
    marker: tuple[str, datetime | None] | None
    last_ok: DiscoverRun | None
    rows: list[DiscoverListing]
    baseline: datetime | None
    employers: int
    listings: int


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


class DiscoverService:
    def __init__(
        self,
        *,
        settings: DiscoverSettings,
        repo: DiscoverRepo,
        clock: Clock,
        registry: Registry | None = None,
        registry_path: Path | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._clock = clock
        self._registry_path = registry_path if registry_path is not None else settings.registry_path
        self._problem: str | None = None
        self._reload_registry = registry is None
        self._registry = registry if registry is not None else self._load_registry()
        self._refresher = Refresher(repo=repo, settings=settings, clock=clock, transport=transport)
        self._vocab = vocabulary()
        self._indexes = IndexCache()
        self._fits: OrderedDict[tuple[str, str, str], Fit] = OrderedDict()
        self._snapshot: _Snapshot | None = None
        self._loading = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._retry_after: datetime | None = None

    # ───────────────────────── settings and registry ─────────────────────────

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def require_enabled(self) -> None:
        if not self._settings.enabled:
            raise DiscoverDisabled()

    @property
    def registry(self) -> Registry:
        return self._registry

    def _load_registry(self) -> Registry:
        try:
            found = load_registry(self._registry_path)
        except RegistryError as exc:
            self._problem = str(exc)
            log.error("discover_registry_invalid", problem=str(exc)[:500])
            return EMPTY
        self._problem = None
        return found

    # ───────────────────────── places ─────────────────────────

    def cities(self) -> DiscoverCitiesResponse:
        return DiscoverCitiesResponse(
            cities=[
                DiscoverCity(name=city.name, localities=[locality.name for locality in city.localities])
                for city in places.CITIES
            ]
        )

    def resolve_area(
        self,
        profile: Profile,
        *,
        city: str | None,
        locality: str | None,
        other_city: str | None = None,
    ) -> Area:
        """The area for one request: the city asked for, else the resume's; a locality must be one of that city's.

        Raises `BadRequest` for a name that is not one of ours. Nothing here is stored.
        """
        resume_text = profile.person.location
        from_resume = places.find_city(resume_text)
        chosen: places.City | None = None
        origin: Literal["resume", "you", "none"] = "none"
        if city:
            chosen = places.city_by_name(city)
            if chosen is None:
                raise BadRequest("That city is not one we know. Pick one from the list.")
            origin = "resume" if from_resume is not None and chosen.name == from_resume.name else "you"
        elif from_resume is not None:
            chosen, origin = from_resume, "resume"
        spot: places.Locality | None = None
        if locality:
            if chosen is None:
                raise BadRequest("Pick a city before a locality.")
            spot = places.locality_in(chosen.name, locality)
            if spot is None:
                raise BadRequest(f"That locality is not in {chosen.name}. Pick one from the list.")
        other: places.City | None = None
        if other_city:
            other = places.city_by_name(other_city)
            if other is None:
                raise BadRequest("That city is not one we know. Pick one from the list.")
        return Area(
            chosen, origin, resume_text.strip() if resume_text and resume_text.strip() else None, spot, other
        )

    # ───────────────────────── refresh ─────────────────────────

    async def refresh(self) -> RefreshReport | None:
        """Read the employers' boards now and wait for it (the CLI, and the background task). The registry file is read
        again first, so a worker picks up an edit without a restart. None when another run is in flight."""
        if self._reload_registry:
            self._registry = self._load_registry()
        report = await self._refresher.run(self._registry, problem=self._problem)
        self._snapshot = None
        return report

    async def maybe_refresh(self) -> bool:
        """Start a refresh in the background when the last good one is old or missing. Returns whether one was started."""
        if not self._settings.enabled:
            return False
        return self._start_if_stale(await self._repo.last_ok_run())

    def _start_if_stale(self, last_ok: DiscoverRun | None) -> bool:
        if not self._settings.enabled or (self._task is not None and not self._task.done()):
            return False
        now = self._clock.now()
        if self._retry_after is not None and now < self._retry_after:
            return False
        if last_ok is not None:
            newest = last_ok.finished_at or last_ok.started_at
            if now < newest + timedelta(hours=self._settings.refresh_hours):
                return False
        self._retry_after = now + RETRY_AFTER_ATTEMPT
        # An empty context: the request that noticed the data is old must not lend its identity (request id, client hash)
        # to the background work, whose log lines are nobody's request.
        self._task = asyncio.create_task(
            self._refresh_in_background(), name="tayyari-discover-refresh", context=contextvars.Context()
        )
        return True

    async def _refresh_in_background(self) -> None:
        try:
            await self.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("discover_background_refresh_failed", exc_info=True)

    async def wait_for_refresh(self) -> None:
        """Wait for the background refresh in flight, if any (tests)."""
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def aclose(self) -> None:
        """Stop a background refresh that is still running."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # ───────────────────────── the store, as this process sees it ─────────────────────────

    async def _load(self) -> _Snapshot:
        last_ok = await self._repo.last_ok_run()
        marker = (last_ok.id, last_ok.finished_at) if last_ok is not None else None
        held = self._snapshot
        if held is not None and held.marker == marker:
            return held
        if last_ok is None:
            snapshot = _Snapshot(None, None, [], None, 0, 0)
        else:
            first = await self._repo.first_run()
            baseline = (
                first_run_end(first.started_at, first.finished_at, STALE_RUN) if first is not None else None
            )
            employers, _ = await self._repo.counts()
            rows = await self._repo.list_open()
            snapshot = _Snapshot(marker, last_ok, rows, baseline, employers, len(rows))
        self._snapshot = snapshot
        return snapshot

    async def _posting_indexes(self, rows: Sequence[DiscoverListing]) -> dict[str, PostingIndex]:
        """The read postings of ``rows``; the ones not read yet are fetched and read in batches."""
        found: dict[str, PostingIndex] = {}
        missing: list[DiscoverListing] = []
        for row in rows:
            cached = self._indexes.get(row.id, row.content_hash)
            if cached is None:
                missing.append(row)
            else:
                found[row.id] = cached
        if not missing:
            return found
        async with self._loading:
            titles = {row.id: row.title for row in missing}
            for start in range(0, len(missing), TEXT_BATCH):
                batch = [row.id for row in missing[start : start + TEXT_BATCH]]
                texts = await self._repo.texts(batch)
                built = await asyncio.to_thread(self._build_indexes, titles, texts)
                for listing_id, (content_hash, index) in built.items():
                    self._indexes.put(listing_id, content_hash, index)
                    found[listing_id] = index
        return found

    def _build_indexes(
        self, titles: dict[str, str], texts: dict[str, tuple[str, str]]
    ) -> dict[str, tuple[str, PostingIndex]]:
        return {
            listing_id: (content_hash, PostingIndex(titles[listing_id], text, self._vocab))
            for listing_id, (content_hash, text) in texts.items()
        }

    def _fit(self, person: PersonIndex, row: DiscoverListing, index: PostingIndex) -> Fit:
        key = (person.fingerprint, row.id, row.content_hash)
        held = self._fits.get(key)
        if held is not None:
            self._fits.move_to_end(key)
            return held
        fit = quick_fit(index, row.years_min, person, self._vocab)
        self._fits[key] = fit
        while len(self._fits) > FIT_CACHE_SIZE:
            self._fits.popitem(last=False)
        return fit

    async def _candidates(
        self,
        snapshot: _Snapshot,
        person: PersonIndex,
        area: Area,
        rows: Sequence[DiscoverListing] | None = None,
    ) -> list[Candidate]:
        chosen = snapshot.rows if rows is None else rows
        indexes = await self._posting_indexes(chosen)
        spot = area.locality
        out: list[Candidate] = []
        for row in chosen:
            index = indexes.get(row.id)
            if index is None:
                continue
            km: float | None = None
            if (
                spot is not None
                and area.city is not None
                and row.city == area.city.name
                and row.precision == "locality"
                and row.lat is not None
                and row.lng is not None
            ):
                km = places.distance_km(spot.lat, spot.lng, row.lat, row.lng)
            out.append(
                Candidate(
                    row=row,
                    tokens=title_tokens(row.title_norm),
                    fit=self._fit(person, row, index),
                    km=km,
                    age_from=age_from(row.posted_at, row.first_seen_at, snapshot.baseline),
                )
            )
        return out

    # ───────────────────────── cards ─────────────────────────

    def _employer_name(self, employer_id: str) -> str:
        found = self._registry.get(employer_id)
        return found.name if found is not None else employer_id.replace("-", " ").title()

    @staticmethod
    def _place(candidate: Candidate) -> Place:
        row = candidate.row
        km = None if candidate.km is None else round(candidate.km, 1)
        if row.remote_india:
            return Place(label="Remote, India", precision="country")
        if row.city and row.locality:
            return Place(
                label=f"{row.locality}, {row.city}",
                city=row.city,
                locality=row.locality,
                precision="locality",
                km=km,
            )
        if row.city:
            return Place(label=row.city, city=row.city, precision="city")
        return Place(label=row.location_text[:60] or "India", precision="country")

    def _card(
        self, candidate: Candidate, now: datetime, arrangement: Arrangement, existing_groups: set[str]
    ) -> ListingCard:
        row = candidate.row
        home = arrangement.home[row.id]
        also = [
            g.id for g in arrangement.claims.get(row.id, ()) if g.id != home.id and g.id in existing_groups
        ]
        source: DiscoverSource = "lever" if row.source == "lever" else "greenhouse"
        modes: dict[str, DiscoverWorkMode] = {"onsite": "onsite", "hybrid": "hybrid", "remote": "remote"}
        mode = modes.get(row.work_mode, "unknown")
        return ListingCard(
            id=row.id,
            title=row.title,
            employer=self._employer_name(row.employer_id),
            employerId=row.employer_id,
            source=source,
            place=self._place(candidate),
            workMode=mode,
            postedAt=row.posted_at.isoformat() if row.posted_at else None,
            firstSeenAt=row.first_seen_at.isoformat(),
            checkedAt=row.last_seen_at.isoformat(),
            freshness=freshness_label(now, candidate.age_from),
            applyUrl=row.apply_url,
            applyHost=row.apply_host,
            confidence="employer",
            sourceLabel=checked_label(now, row.last_seen_at),
            salary=Salary(text=row.salary_text) if row.salary_text else None,
            fit=candidate.fit.to_model(),
            groupId=home.id,
            alsoFits=also,
        )

    # ───────────────────────── the screen ─────────────────────────

    async def discover(self, profile: Profile, query: DiscoverQuery) -> DiscoverResponse:
        """One person's Discover screen. See the module docstring; the lens rules are in `lens`, the groups in `groups`."""
        self.require_enabled()
        area = self.resolve_area(
            profile, city=query.city, locality=query.locality, other_city=query.otherCity
        )
        snapshot = await self._load()
        self._start_if_stale(snapshot.last_ok)
        person = PersonIndex(profile, self._vocab)
        if snapshot.last_ok is None:
            return await self._without_listings(query, area, person)

        now = self._clock.now()
        cands = await self._candidates(snapshot, person, area)
        roles = role_groups(person)
        static = {c.row.id: static_claims(c, person, roles) for c in cands}
        has_role = person.current_role is not None
        reach: DiscoverReach = query.reach
        breadth: DiscoverBreadth = query.breadth
        lane: Lane = query.lane

        city_cands = (
            [c for c in cands if not c.row.remote_india and c.row.city == area.city.name] if area.city else []
        )
        pools: dict[str, list[Candidate]] = {}
        arrangements: dict[str, Arrangement] = {}

        def pool_of(key: str) -> list[Candidate]:
            if key not in pools:
                if key.startswith("near:"):
                    pools[key] = near_pool(city_cands, key.split(":", 1)[1])  # type: ignore[arg-type]
                elif key == "remote":
                    pools[key] = [c for c in cands if c.row.remote_india]
                else:
                    other = area.other_city
                    pools[key] = (
                        [
                            c
                            for c in cands
                            if other is not None
                            and not c.row.remote_india
                            and c.row.city == other.name
                            and c.fit.word == "looks_close"
                            # a move is worth it for a role at your level: an internship is not, past two years
                            and not (c.row.entry_level and person.years is not None and person.years >= 2)
                        ]
                        if other
                        else []
                    )
            return pools[key]

        def arranged(key: str) -> Arrangement:
            if key not in arrangements:
                arrangements[key] = arrange(pool_of(key), person, static)
            return arrangements[key]

        def shown(key: str, at: DiscoverBreadth) -> list[Candidate]:
            return visible(pool_of(key), arranged(key), at, has_current_role=has_role)

        lane_key = {"near": f"near:{reach}", "remote": "remote", "city": "city"}[lane]
        in_view = shown(lane_key, breadth)
        arrangement = arranged(lane_key)

        reach_stops = stops(REACH_ORDER, REACH_LABELS, lambda r: len(shown(f"near:{r}", breadth)))
        breadth_stops = stops(
            BREADTH_ORDER,
            BREADTH_LABELS,
            lambda b: len(shown(lane_key, b)),  # type: ignore[arg-type]
        )
        lane_counts = LaneCounts(
            near=len(shown(f"near:{reach}", breadth)),
            remote=len(shown("remote", breadth)),
            city=len(shown("city", breadth)),
        )

        ordered = order_candidates(in_view, arrangement, query.order)
        members: dict[str, list[Candidate]] = {}
        for c in ordered:
            members.setdefault(arrangement.home[c.row.id].id, []).append(c)
        group_defs = {arrangement.home[c.row.id].id: arrangement.home[c.row.id] for c in ordered}
        existing = set(members)
        groups = [
            Group(
                id=group.id,
                kind=group.kind,
                title=group.title,
                count=len(members[group.id]),
                reason=group_reason(group, members[group.id], person),
                listingIds=[c.row.id for c in members[group.id]],
            )
            for group in sorted(group_defs.values(), key=lambda g: (_kind_rank(g), g.rank, g.id))
        ]
        listings = [self._card(c, now, arrangement, existing) for c in ordered]

        suggestion = None
        if lane == "near" and area.city is not None:
            suggestion = self._suggestion(reach, breadth, shown, arranged)

        companies = self._companies(cands, person, static, area, in_view, shown(f"near:{reach}", "stretch"))
        coverage = DiscoverCoverage(
            employers=snapshot.employers,
            listings=snapshot.listings,
            # judged on the whole city: a small radius is empty because few employers state a neighbourhood, which is
            # not the same as few roles like yours (the suggestion says what widening adds)
            note=self._note(area, len(shown("near:city", breadth))),
        )
        return DiscoverResponse(
            area=area.model(),
            lane=lane,
            reach=reach,
            breadth=breadth,
            order=query.order,
            total=len(in_view),
            reachStops=reach_stops,
            breadthStops=breadth_stops,
            laneCounts=lane_counts,
            suggestion=suggestion,
            groups=groups,
            listings=listings,
            companies=companies,
            coverage=coverage,
            signals=self._signals(person, area, query.order),
            refreshedAt=(snapshot.last_ok.finished_at or snapshot.last_ok.started_at).isoformat(),
        )

    async def _without_listings(
        self, query: DiscoverQuery, area: Area, person: PersonIndex
    ) -> DiscoverResponse:
        """The answer while no refresh has finished: an empty list and a plain note."""
        latest = await self._repo.latest_run()
        failed = latest is not None and latest.status == "failed"
        empty_reach = [(r, REACH_LABELS[r]) for r in REACH_ORDER]
        empty_breadth = [(b, BREADTH_LABELS[b]) for b in BREADTH_ORDER]
        return DiscoverResponse(
            area=area.model(),
            lane=query.lane,
            reach=query.reach,
            breadth=query.breadth,
            order=query.order,
            total=0,
            reachStops=[LensStop(value=v, label=label, count=0) for v, label in empty_reach],
            breadthStops=[LensStop(value=v, label=label, count=0) for v, label in empty_breadth],
            laneCounts=LaneCounts(near=0, remote=0, city=0),
            groups=[],
            listings=[],
            companies=[],
            coverage=DiscoverCoverage(
                employers=0, listings=0, note=UNREADABLE_NOTE if failed else FIRST_READ_NOTE
            ),
            signals=self._signals(person, area, query.order),
        )

    def _note(self, area: Area, near_count: int) -> str | None:
        if area.city is None:
            return PICK_CITY_NOTE
        if near_count < THIN_NEAR_LANE:
            return THIN_NOTE
        return None

    @staticmethod
    def _signals(person: PersonIndex, area: Area, order: str) -> list[str]:
        if order == "posted":
            return [
                "Newest first, by the date the employer posted it or we first saw it",
                "No ranking is applied",
                "Only employers' own job boards",
            ]
        signals: list[str] = []
        if person.next_roles:
            signals.append(
                "Your next roles: " + ", ".join(sentence_case(role.title) for role in person.next_roles)
            )
        signals.append("Skills the postings name that your evidence shows")
        if person.years is not None:
            signals.append(f"Years asked against your {person.years:g}")
        if area.locality is not None:
            signals.append(f"Distance from {area.locality.name}, straight line")
        signals.append("How recently the employer listed it")
        signals.append("Only employers' own job boards")
        return signals

    def _suggestion(
        self,
        reach: DiscoverReach,
        breadth: DiscoverBreadth,
        shown: Callable[[str, DiscoverBreadth], list[Candidate]],
        arranged: Callable[[str], Arrangement],
    ) -> str | None:
        """At most one data-grounded hint: the nearest wider reach that adds roles, and where most of them fall."""
        if reach == "city":
            return None
        here = {c.row.id for c in shown(f"near:{reach}", breadth)}
        for wider in REACH_ORDER[REACH_ORDER.index(reach) + 1 :]:
            key = f"near:{wider}"
            added = [c for c in shown(key, breadth) if c.row.id not in here]
            if not added:
                continue
            arrangement = arranged(key)
            homes: dict[str, GroupDef] = {}
            counts: dict[str, int] = {}
            for c in added:
                home = arrangement.home[c.row.id]
                homes[home.id] = home
                counts[home.id] = counts.get(home.id, 0) + 1
            # The group with most of the added roles; a tie goes to the one that comes first in the order of groups.
            top = min(
                counts,
                key=lambda gid: (-counts[gid], _kind_rank(homes[gid]), homes[gid].rank, homes[gid].title),
            )
            n, m = len(added), counts[top]
            if reach == "25":
                return f"The whole city adds {n} more."
            return f"Widening to {km_text(wider)} adds {n} {_plural(n, 'role', 'roles')}, {m} in {homes[top].title}."
        return None

    def _companies(
        self,
        cands: Sequence[Candidate],
        person: PersonIndex,
        static: dict[str, tuple[GroupDef, ...]],
        area: Area,
        in_view: Sequence[Candidate],
        near_all: Sequence[Candidate],
    ) -> list[CompanyLead]:
        """Employers hiring for the person's groups elsewhere and with nothing in view or near them now."""
        if area.city is None:
            return []
        placed = [c for c in cands if not c.row.remote_india and c.row.city is not None]
        arrangement = arrange(placed, person, static)

        def eligible(c: Candidate) -> bool:
            return (
                c.fit.word in ("looks_close", "worth_a_look") and arrangement.home[c.row.id].kind != "other"
            )

        busy = {c.row.employer_id for c in in_view} | {c.row.employer_id for c in near_all if eligible(c)}
        per_employer: dict[str, dict[str, int]] = {}
        for c in placed:
            if eligible(c) and c.row.employer_id not in busy and c.row.city:
                cities = per_employer.setdefault(c.row.employer_id, {})
                cities[c.row.city] = cities.get(c.row.city, 0) + 1
        leads: list[tuple[int, str, CompanyLead]] = []
        for employer_id, cities in per_employer.items():
            employer = self._registry.get(employer_id)
            if employer is None:
                continue
            city, n = max(cities.items(), key=lambda item: (item[1], item[0]))
            leads.append(
                (
                    n,
                    employer.name.lower(),
                    CompanyLead(
                        employerId=employer_id,
                        name=employer.name,
                        city=city,
                        reason=f"Hiring {n} {_plural(n, 'role', 'roles')} like yours in {city}, nothing near you now.",
                        careersUrl=employer.careers_url,
                    ),
                )
            )
        leads.sort(key=lambda item: (-item[0], item[1]))
        return [lead for _, _, lead in leads[:MAX_COMPANIES]]

    # ───────────────────────── one listing ─────────────────────────

    async def detail(
        self, listing_id: str, profile: Profile, *, city: str | None = None, locality: str | None = None
    ) -> ListingDetail | None:
        """The listing with its posting text, or None when it is unknown or closed. The card is worked out for this person
        in the context of its own city (or the remote roles)."""
        self.require_enabled()
        row = await self._repo.get(listing_id)
        if row is None or row.status != "open":
            return None
        area = self.resolve_area(profile, city=city, locality=locality)
        snapshot = await self._load()
        person = PersonIndex(profile, self._vocab)
        rows = [r for r in snapshot.rows if r.id == row.id or self._same_context(r, row)]
        if all(r.id != row.id for r in rows):
            rows.append(row)
        cands = await self._candidates(snapshot, person, area, rows)
        roles = role_groups(person)
        static = {c.row.id: static_claims(c, person, roles) for c in cands}
        arrangement = arrange(cands, person, static)
        mine = next(c for c in cands if c.row.id == row.id)
        existing = {arrangement.home[c.row.id].id for c in cands}
        card = self._card(mine, self._clock.now(), arrangement, existing)
        return ListingDetail(card=card, postingText=row.posting_text or "")

    @staticmethod
    def _same_context(a: DiscoverListing, b: DiscoverListing) -> bool:
        if b.remote_india:
            return a.remote_india
        return not a.remote_india and a.city == b.city


def _kind_rank(group: GroupDef) -> int:
    return KIND_ORDER[group.kind]


__all__ = ["Area", "DiscoverService"]
