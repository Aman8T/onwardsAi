"""Repository Protocols and the small value types they exchange.

Services depend on these, never on SQLAlchemy, so every service is unit-tested against the dict-backed
fakes in ``tests/fakes/repos.py``. The SQLAlchemy implementations live in `repos/`.

Profiles travel as JSON documents (``dict``): storage does not import the Profile model, so a change to
the model never needs a storage change (``schema_version`` records which shape a document has).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from tayyari.contract.match import Fact, Posting
from tayyari.contract.portfolio import Fact as PortfolioFact
from tayyari.contract.records import DecisionRecord, JevCallRecord, LlmCallRecord, StateBlob
from tayyari.contract.stored import StoredContext


class SessionConflict(RuntimeError):
    """`SessionRepo.append_turn` lost a ``(session_id, seq)`` race twice in a row."""


class SessionNotFound(LookupError):
    """`SessionRepo.append_turn` was asked to append to a session that does not exist."""


@dataclass(frozen=True, slots=True)
class ToolEvent:
    """One tool use shown as a chip under an assistant message (``WebSearch`` or ``WebFetch``)."""

    name: str
    detail: str


@dataclass(frozen=True, slots=True)
class ChatMessage:
    seq: int
    role: str  # "user" | "assistant"
    content: str
    tools: list[ToolEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChatSession:
    id: str
    profile_id: str
    origin: dict[str, Any] | None
    turn_count: int
    messages: list[ChatMessage] = field(default_factory=list)
    """Oldest first; at most the last ``limit`` messages."""


@dataclass(frozen=True, slots=True)
class ProfileAccess:
    """Who a profile belongs to: ``user_id`` is the account that claimed it (None while it is anonymous), and
    ``is_sample`` marks a "Try a sample profile" run, which is public whatever its ``user_id`` says."""

    user_id: str | None
    is_sample: bool


@dataclass(frozen=True, slots=True)
class OpenProfile:
    """A profile anyone holding its id may use: nobody has claimed it, or it is a sample run. ``is_sample`` tells the
    two apart (a sample run is shared by everyone who holds its id; an unclaimed profile is one person's)."""

    profile: dict[str, Any]
    context: StoredContext
    is_sample: bool


class ProfileRepo(Protocol):
    async def save(
        self,
        profile_id: str,
        profile: dict[str, Any],
        context: StoredContext,
        *,
        goal: str | None,
        owner_hash: str | None,
        is_sample: bool,
        schema_version: int = 1,
    ) -> None:
        """Insert or replace a profile and its context in one transaction (``created_at`` is kept)."""
        ...

    async def get_profile(self, pid: str) -> dict[str, Any] | None:
        """The profile document, or None. Touches ``last_accessed_at`` at most once an hour."""
        ...

    async def get_for_chat(self, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        """Profile and context in one round trip (also touches ``last_accessed_at``), or None."""
        ...

    async def get_owned_for_chat(self, user_id: str, pid: str) -> tuple[dict[str, Any], StoredContext] | None:
        """Profile and context like `get_for_chat`, but only when the profile belongs to ``user_id``. None for an
        unknown id, an anonymous profile, a sample run and another account's profile alike (so a caller cannot tell
        which one it was)."""
        ...

    async def get_open_for_chat(self, pid: str) -> OpenProfile | None:
        """Profile and context like `get_for_chat`, but only when nobody has claimed the profile (``user_id`` NULL) or it
        is a sample run. None for an unknown id and for a profile an account owns, alike. This is what a visitor with
        no sign-in may match a posting against."""
        ...

    async def get_access(self, pid: str) -> ProfileAccess | None:
        """The owner and sample flag of a profile, or None when no such row exists. A single-column lookup: it does
        not read the document and does not touch ``last_accessed_at``."""
        ...

    async def purge_expired(self, cutoff: datetime) -> int:
        """Delete anonymous, non-sample profiles last accessed before ``cutoff`` (their chats go too); returns the
        count. A profile that belongs to an account (``user_id`` set) is kept: saving it was the point."""
        ...


class SessionRepo(Protocol):
    async def load(self, sid: str, profile_id: str, limit: int = 40) -> ChatSession | None:
        """The session with its last ``limit`` messages (oldest first).

        None when ``sid`` is unknown *or belongs to another profile*: callers treat both as a fresh start.
        """
        ...

    async def create(
        self, profile_id: str, origin: dict[str, Any] | None, ip_hash: str | None
    ) -> ChatSession:
        """A new empty session with a fresh 128-bit id. The profile must exist (the SQL repository enforces it)."""
        ...

    async def append_turn(
        self,
        sid: str,
        *,
        user: str,
        assistant: str | None,
        tools: list[ToolEvent],
        llm_call_id: UUID | None,
    ) -> None:
        """Append the user message and the assistant reply with the next ``seq`` numbers.

        ``assistant=None`` stores only the user message (a turn with no visible text). ``tools`` and
        ``llm_call_id`` belong to the assistant message (to the user message when there is none).
        Optimistic: on a ``(session_id, seq)`` conflict it reloads and retries once, then raises
        `SessionConflict`. Raises `SessionNotFound` for an unknown session. Increments ``turn_count``.
        """
        ...


class LlmCallRepo(Protocol):
    async def record(self, rec: LlmCallRecord) -> None:
        """Insert the call and add its cost to ``spend_daily`` (``total`` and ``anthropic``), atomically.

        Idempotent per ``rec.id``: recording the same call again changes nothing.
        """
        ...


class DecisionLogRepo(Protocol):
    async def record_batch(
        self,
        states: list[StateBlob],
        calls: list[JevCallRecord],
        rows: list[DecisionRecord],
    ) -> None:
        """Write one flush of the decision log in one transaction.

        States are inserted or touched (``last_ref_at``), calls and rows are inserted (idempotent per id),
        and the cost of newly inserted Jev calls is added to ``spend_daily`` (``total`` and ``jev``).
        """
        ...

    def export(
        self, since: datetime, until: datetime | None = None, limit: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Decision rows in ``[since, until)``, oldest first, joined with their Jev call and decompressed state.

        Each item is JSON-ready: the decision columns at the top level, ``"call"`` (the Jev call, or None),
        and ``"state"`` (the decoded state, or None once its payload has been purged).
        """
        ...


class LimitRepo(Protocol):
    async def hit(self, bucket: str, window_s: int, now: datetime) -> int:
        """Count one hit in the fixed window containing ``now`` and return the new count (atomic).

        A counter is keyed by ``(bucket, window start)``: put the window size in the bucket name
        (``"analyze:min:<ip_hash>"``), or two sizes starting at the same instant would share a counter.
        """
        ...

    async def spent_today(self, day: date) -> int:
        """Micro-USD recorded for ``day`` in scope ``total`` (0 when there is none)."""
        ...

    async def purge_hits(self, before: datetime) -> int:
        """Delete counters whose window started before ``before``; returns the count."""
        ...


class KvRepo(Protocol):
    async def get(self, key: str) -> Any | None:
        """The stored JSON value, or None when the key is missing (or holds null)."""
        ...

    async def set(self, key: str, value: Any) -> None:
        """Insert or replace a value."""
        ...

    async def get_or_create_secret(self, key: str, nbytes: int = 32) -> bytes:
        """The secret stored under ``key``, creating ``nbytes`` of randomness on first use (race-safe).

        Concurrent callers, on this or another replica, all get the same bytes. An existing secret keeps
        its length whatever ``nbytes`` says.
        """
        ...


class PortfolioConflict(RuntimeError):
    """A portfolio save lost an optimistic-concurrency race (someone saved a newer version first)."""


@dataclass(frozen=True, slots=True)
class StoredPortfolio:
    """One portfolio row. ``doc`` is the `Portfolio` JSON (camelCase, tayyari.contract.portfolio shape)."""

    profile_id: str
    public_id: str
    doc: dict[str, Any]
    facts_hash: str | None
    """sha256 of the source texts the facts were extracted from (reuse the facts when unchanged)."""
    generated: list[dict[str, Any]]
    """The copy slots as last generated (model or fallback wording), so "reset to generated wording" can
    restore a slot the owner edited. Kept out of the `Portfolio` document the client sees."""
    version: int
    published: bool
    created_at: datetime
    updated_at: datetime
    user_id: str | None = None
    """The account that owns the page: the owner of its profile when the page was written. A write that carries None
    keeps the owner the row already has (a claim sets it on a page that existed before)."""


class PortfolioRepo(Protocol):
    """Portfolios live apart from profiles: no foreign key, so the 60-day profile purge never deletes a page."""

    async def get(self, profile_id: str) -> StoredPortfolio | None:
        """The owner's portfolio (one per profile), or None."""
        ...

    async def get_published(self, public_id: str) -> StoredPortfolio | None:
        """A published portfolio by its public id (None when unknown or unpublished); records the view time."""
        ...

    async def put(self, rec: StoredPortfolio, *, expected_version: int | None) -> StoredPortfolio:
        """Insert or update; returns the stored row with its new ``version`` and ``updated_at``.

        ``expected_version=None`` creates the row, or replaces it outright when one exists (a regenerate).
        Otherwise the update applies only when the stored version equals ``expected_version`` and raises
        `PortfolioConflict` when it does not. ``public_id`` and ``created_at`` of an existing row are kept, and so is
        its ``user_id`` unless ``rec.user_id`` names an owner.
        """
        ...

    async def delete(self, profile_id: str) -> bool:
        """Remove the owner's portfolio; True when a row was deleted."""
        ...

    async def purge_orphan_drafts(self, cutoff: datetime) -> int:
        """Delete unpublished portfolios last updated before ``cutoff`` whose profile no longer exists."""
        ...


# ───────────────────────────── accounts ─────────────────────────────


class ProfileNotClaimable(LookupError):
    """`UserRepo.claim_profile` was asked for a profile that does not exist or may not be owned (a sample run)."""


class ProfileOwnedByAnother(RuntimeError):
    """`UserRepo.claim_profile` was asked for a profile that already belongs to a different account."""


@dataclass(frozen=True, slots=True)
class StoredUser:
    """One account. ``email`` is lower-cased; ``currency`` is ``INR``, ``USD`` or None (not chosen yet)."""

    id: str
    email: str
    name: str | None
    picture_url: str | None
    google_sub: str | None
    created_at: datetime
    last_login_at: datetime
    locale: str | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class StoredAuthSession:
    """One sign-in. ``id`` is the sha256 hex of the cookie token (the token itself is never stored)."""

    id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    ip_hash: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class StoredMagicLink:
    """An emailed sign-in link, keyed by the sha256 hex of its token."""

    token_hash: str
    email: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    ip_hash: str | None


@dataclass(frozen=True, slots=True)
class OwnedProfile:
    """What the account page lists about a profile the user owns."""

    id: str
    created_at: datetime
    goal: str | None
    person_name: str | None


class UserRepo(Protocol):
    """Accounts, their sign-ins and sign-in links, and which profiles an account owns."""

    async def get(self, user_id: str) -> StoredUser | None:
        """The account, or None."""
        ...

    async def get_by_email(self, email: str) -> StoredUser | None:
        """The account with this (lower-cased) email, or None."""
        ...

    async def sign_in_google(
        self,
        *,
        sub: str,
        email: str,
        name: str | None,
        picture_url: str | None,
        locale: str | None,
        now: datetime,
    ) -> StoredUser:
        """Find the account for a verified Google identity and record the login; create it when there is none.

        Looked up by ``google_sub``, then by ``email`` (which links the Google identity to an account that
        was made by emailed link); a new account is made otherwise. The name is only filled in when the
        account has none (the person may have changed it), the picture is always refreshed.
        """
        ...

    async def sign_in_email(self, *, email: str, now: datetime) -> StoredUser:
        """Find the account for an email address whose owner just proved it, or create it; records the login."""
        ...

    async def update(
        self, user_id: str, *, name: str | None = None, currency: str | None = None
    ) -> StoredUser | None:
        """Change the name and/or currency (None leaves a field as it is); the account, or None when unknown."""
        ...

    async def delete(self, user_id: str) -> bool:
        """Delete the account with everything it owns: its profiles (and their chats), portfolios, matches (with
        their resumes and cover notes, including any made on those profiles without an account), the facts it gave in
        its own words, sign-ins and pending links. Postings
        are shared by everyone who pasted the same text and stay. True when the account existed."""
        ...

    async def create_session(self, rec: StoredAuthSession) -> None:
        """Store a new sign-in."""
        ...

    async def get_session(self, session_id: str) -> tuple[StoredAuthSession, StoredUser] | None:
        """The sign-in and its account in one round trip (expiry is the caller's decision), or None."""
        ...

    async def touch_session(self, session_id: str, *, last_seen_at: datetime, expires_at: datetime) -> None:
        """Record use of a sign-in and push its expiry out (sliding)."""
        ...

    async def delete_session(self, session_id: str) -> bool:
        """End a sign-in; True when it existed."""
        ...

    async def add_magic_link(self, rec: StoredMagicLink) -> None:
        """Store a freshly issued link."""
        ...

    async def consume_magic_link(self, token_hash: str, now: datetime) -> StoredMagicLink | None:
        """Spend a link: atomically mark it used and return it, or None when it is unknown, already used or
        expired. Two concurrent calls for the same link never both succeed."""
        ...

    async def list_profiles(self, user_id: str, limit: int = 20) -> list[OwnedProfile]:
        """The user's profiles, newest first."""
        ...

    async def claim_profile(self, user_id: str, profile_id: str) -> None:
        """Make an unowned profile the user's, with its portfolio, the posting matches made on it without an account
        and the answers given there (all of those get the user's id). Claiming one's own profile again is a no-op.
        Raises `ProfileNotClaimable` (unknown id, or a sample run) and `ProfileOwnedByAnother`."""
        ...

    async def purge_expired(self, now: datetime) -> int:
        """Delete sign-ins and links whose expiry has passed; returns the number of rows removed."""
        ...


# ───────────────────────────── payments and entitlements ─────────────────────────────

ConsumeSource = Literal["pass", "free_monthly", "credit"]
"""What paid for one use: an active pass, the month's free allowance, or one posting credit."""
SettleStatus = Literal["granted", "already_paid", "unknown_order", "mismatch", "orphaned"]
"""``granted``: this call marked the order paid and applied the grant. ``already_paid``: an earlier call did.
``unknown_order``: no such order. ``mismatch``: the amount or currency paid is not what the order asked for (nothing
is granted, the order is left as it was). ``orphaned``: the account is gone (nothing to grant to)."""


@dataclass(frozen=True, slots=True)
class StoredOrder:
    """One purchase attempt. ``status``: ``created``, ``paid``, ``failed`` or ``refunded``. ``raw`` holds provider
    ids only (never an email address or a card detail)."""

    id: str
    user_id: str | None
    provider: str
    provider_order_id: str
    product: str
    currency: str
    amount_minor: int
    status: str
    created_at: datetime
    paid_at: datetime | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SettleResult:
    """The outcome of `BillingRepo.settle_order`; ``order`` is the row as it was before this call (None if unknown)."""

    status: SettleStatus
    order: StoredOrder | None


@dataclass(frozen=True, slots=True)
class StoredEntitlements:
    """An account's balance. ``free_used`` counts the free monthly postings used since ``month_start`` and not given
    back; a ``pass_until`` in the past means no pass."""

    user_id: str
    credits: int
    pass_until: datetime | None
    free_used: int


@dataclass(frozen=True, slots=True)
class ConsumeOutcome:
    """One use of an entitlement. ``credits`` and ``pass_until`` are the balance afterwards; ``replayed`` is True when
    the same ``ref`` had already been paid for (nothing more was taken)."""

    source: ConsumeSource
    credits: int
    pass_until: datetime | None
    replayed: bool = False


class BillingRepo(Protocol):
    """Orders, balances and the ledger. Every method that changes a balance runs in one transaction and takes the
    account's ``entitlements`` row as a lock first, so concurrent calls for one account are serialised (a row lock on
    Postgres, the write lock on SQLite) and the balance always equals the sum of the ledger's deltas."""

    async def add_order(self, rec: StoredOrder) -> None:
        """Store a new order (``provider_order_id`` is unique)."""
        ...

    async def get_order(self, order_id: str) -> StoredOrder | None:
        """The order with this internal id, or None."""
        ...

    async def get_order_by_provider_id(self, provider_order_id: str) -> StoredOrder | None:
        """The order the provider knows as ``provider_order_id``, or None."""
        ...

    async def settle_order(
        self,
        provider_order_id: str,
        *,
        now: datetime,
        credits: int,
        pass_days: int,
        raw: dict[str, Any] | None,
        amount_minor: int | None = None,
        currency: str | None = None,
    ) -> SettleResult:
        """Mark the order paid and apply its grant (``credits`` and/or ``pass_days``) in one transaction.

        Idempotent on the order: a second call (a replayed webhook, the browser hand-off after the webhook) changes
        nothing and says ``already_paid``. When ``amount_minor`` / ``currency`` are given they must equal the
        order's, else nothing happens (``mismatch``). A failed order can still be settled: a customer may retry a
        payment on the same Razorpay order.
        """
        ...

    async def fail_order(self, provider_order_id: str) -> bool:
        """``created`` -> ``failed``; True when a row changed. A paid order is never touched."""
        ...

    async def entitlements(self, user_id: str, *, month_start: datetime) -> StoredEntitlements:
        """The balance (zeros when the account has none yet) and the free postings used since ``month_start``."""
        ...

    async def grant(self, user_id: str, *, credits: int, pass_days: int, ref: str, now: datetime) -> bool:
        """Add ``credits`` and/or extend the pass by ``pass_days`` (from the later of now and its current end),
        with a ``purchase`` ledger row keyed by ``ref``. Idempotent on ``(user_id, ref)``: False, and nothing
        changes, when ``ref`` was already granted."""
        ...

    async def consume(
        self, user_id: str, ref: str, *, now: datetime, month_start: datetime, free_allowance: int
    ) -> ConsumeOutcome | None:
        """Pay for one use: an active pass, else a free monthly posting (fewer than ``free_allowance`` used since
        ``month_start``), else one credit; None when there is nothing left. Idempotent on ``(user_id, ref)``: a
        ``ref`` already paid for (and not given back) is returned as ``replayed`` without taking anything more."""
        ...

    async def release(self, user_id: str, ref: str, *, now: datetime) -> bool:
        """Give back what ``consume`` took for ``ref`` (the credit returns, a free posting counts as unused again).
        True when something was given back; False when ``ref`` was never paid for or was already given back."""
        ...


# ───────────────────────────── posting match ─────────────────────────────

VariantKind = Literal["resume", "cover"]
"""What a stored variant of a match is: the resume built for the posting, or its cover note."""


@dataclass(frozen=True, slots=True)
class StoredMatch:
    """One posting matched against one profile. ``facts`` is the `match.models.FactBook` as JSON (the facts the
    result cites and the structure the resume is built from); ``result`` is the `MatchResult` as JSON. Storage keeps
    both opaque: the API layer validates them."""

    id: str
    user_id: str | None
    """The account the match belongs to; None for a match made without one (until its profile is claimed)."""
    profile_id: str
    posting_hash: str
    facts: dict[str, Any]
    result: dict[str, Any]
    band: str
    blocked: bool
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MatchListing:
    """A row of the person's list of matches: the posting's title and company, the band and when it was made."""

    id: str
    title: str
    company: str | None
    band: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredVariant:
    """The resume or cover note of a match as JSON (`ResumeVariant` / `CoverNote`). ``status`` is ``needs_user``
    while a resume still has open questions, else ``ready``; ``version`` counts the saves."""

    id: str
    match_id: str
    kind: VariantKind
    spec: dict[str, Any]
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class PostingRepo(Protocol):
    """Read postings, shared by everyone who pasted the same text (`match.ports.PostingCache` is this)."""

    async def get(self, posting_hash: str) -> Posting | None:
        """The stored reading of the posting with this hash, or None."""
        ...

    async def put(self, posting: Posting) -> None:
        """Remember a reading (idempotent on ``posting.hash``; the first one stays)."""
        ...


class ProfileFactsRepo(Protocol):
    """The portfolio fact extractor's output per profile (`match.ports.FactsCache` is this)."""

    async def get(self, profile_id: str, facts_hash: str) -> list[PortfolioFact] | None:
        """The facts extracted from the profile's sources when they hashed to ``facts_hash``, else None."""
        ...

    async def put(self, profile_id: str, facts_hash: str, facts: list[PortfolioFact]) -> None:
        """Remember an extraction, replacing an earlier one for the profile."""
        ...


class UserFactRepo(Protocol):
    """What a person said in their own words, kept per profile (`match.ports.UserFactStore` plus the owner)."""

    async def list_for_profile(self, profile_id: str) -> list[Fact]:
        """Every user fact for the profile, oldest first."""
        ...

    async def add(
        self,
        user_id: str | None,
        profile_id: str,
        fact: Fact,
        *,
        question: str,
        answer: str,
        match_id: str | None,
    ) -> bool:
        """Store one fact with the question it answered; ``user_id`` None for something said without an account.
        Idempotent on ``(profile_id, fact.id)``: False, and nothing changes, when it is already stored."""
        ...


class MatchRepo(Protocol):
    """Matches and their variants. Every match read and write is scoped by ``user_id``: another account's id is
    answered exactly like an unknown one. ``user_id=None`` scopes to the matches made without an account (their
    ``user_id`` is NULL), which the routes open only while their profile is still unclaimed or a sample run."""

    async def create(
        self,
        *,
        match_id: str,
        user_id: str | None,
        profile_id: str,
        posting_hash: str,
        facts: dict[str, Any],
        result: dict[str, Any],
        band: str,
        blocked: bool,
        version: int,
        now: datetime,
    ) -> StoredMatch:
        """Insert a match (the posting must already be stored)."""
        ...

    async def get(self, match_id: str, user_id: str | None) -> StoredMatch | None:
        """The user's match (``user_id=None``: the anonymous match with this id), or None."""
        ...

    async def list_for_user(self, user_id: str, limit: int = 50) -> list[MatchListing]:
        """The user's matches, newest first."""
        ...

    async def save_result(
        self,
        match_id: str,
        user_id: str | None,
        *,
        expected_version: int,
        facts: dict[str, Any],
        result: dict[str, Any],
        band: str,
        blocked: bool,
        version: int,
        now: datetime,
    ) -> bool:
        """Replace the result (and the facts it cites) when the stored version is still ``expected_version``.
        False when the match is not the user's, or moved on in the meantime."""
        ...

    async def save_facts(
        self, match_id: str, user_id: str | None, facts: dict[str, Any], now: datetime
    ) -> bool:
        """Replace the facts alone (a figure the person gave while settling a resume); False when not the user's."""
        ...

    async def get_variant(self, match_id: str, kind: VariantKind) -> StoredVariant | None:
        """The stored resume or cover note of a match, or None. The caller has read the match with `get` first."""
        ...

    async def put_variant(
        self, match_id: str, kind: VariantKind, *, spec: dict[str, Any], status: str, now: datetime
    ) -> StoredVariant:
        """Insert the variant, or replace it and count one more version."""
        ...


# ───────────────────────────── discover ─────────────────────────────

ListingStatus = Literal["open", "closed"]
RunStatus = Literal["running", "ok", "failed"]


@dataclass(frozen=True, slots=True)
class DiscoverListing:
    """One job on an employer's own board, as stored (`DiscoverListingRow`). ``posting_text`` is the plain text of the
    posting; the list queries leave it ``None`` (a screen of listings must not read hundreds of descriptions)."""

    id: str
    employer_id: str
    source: str
    external_id: str
    title: str
    title_norm: str
    work_mode: str
    city: str | None
    locality: str | None
    lat: float | None
    lng: float | None
    precision: str
    remote_india: bool
    location_text: str
    apply_url: str
    apply_host: str
    posted_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    status: ListingStatus
    closed_at: datetime | None
    salary_text: str | None
    years_min: int | None
    entry_level: bool
    content_hash: str
    posting_text: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoverRun:
    """One refresh of the employers' boards (`DiscoverRunRow`)."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus
    employers_ok: int
    employers_failed: int
    listings_open: int
    listings_closed: int
    note: str | None


@dataclass(frozen=True, slots=True)
class StoredSearchRun:
    """One finished search with AI (`JobSearchRunRow`). ``result`` holds the `JobSearchRun` fields the person saw;
    ``postings`` the shown postings' text by job id, never sent to a browser."""

    id: str
    profile_id: str
    city: str | None
    remote: bool
    created_at: datetime
    result: dict[str, Any]
    postings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StoredSearchDoc:
    """A role's prep page or referral note (`JobSearchDocRow`)."""

    profile_id: str
    job_id: str
    kind: str
    created_at: datetime
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    """What `DiscoverRepo.upsert_employer` did: rows added, rows whose content changed, rows that only got seen again."""

    inserted: int
    updated: int
    unchanged: int


class DiscoverRepo(Protocol):
    """Listings read from employers' own boards, and the log of the refreshes that read them. Nothing here is about a
    person. Every write by a refresh is scoped to one employer, so a board that failed to load leaves the rows of
    every other employer (and its own) untouched."""

    async def upsert_employer(
        self, employer_id: str, listings: Sequence[DiscoverListing], *, now: datetime
    ) -> UpsertCounts:
        """Store what the employer's board lists now. A new id is inserted with ``first_seen_at = now``. A known id is
        marked open and seen at ``now`` (``first_seen_at`` is kept, ``closed_at`` cleared, a closed listing that came
        back is open again), and its content columns are replaced only when ``content_hash`` differs. In one
        transaction."""
        ...

    async def close_missing(self, employer_id: str, seen_ids: Collection[str], *, now: datetime) -> int:
        """Close the employer's open listings whose id is not in ``seen_ids`` (``status = closed``, ``closed_at = now``);
        returns how many. Call it only after the employer's board was read successfully."""
        ...

    async def close_employers_except(self, employer_ids: Collection[str], *, now: datetime) -> int:
        """Close the open listings of every employer not in ``employer_ids`` (one that left the registry)."""
        ...

    async def list_open(self) -> list[DiscoverListing]:
        """Every open listing, without its text."""
        ...

    async def get(self, listing_id: str) -> DiscoverListing | None:
        """The listing with its text, open or closed, or None."""
        ...

    async def texts(self, listing_ids: Sequence[str]) -> dict[str, tuple[str, str]]:
        """``{id: (content_hash, posting_text)}`` for the open listings among ``listing_ids``."""
        ...

    async def counts(self) -> tuple[int, int]:
        """``(employers with an open listing, open listings)``."""
        ...

    async def start_run(self, *, now: datetime, stale_after: timedelta) -> DiscoverRun | None:
        """Single flight. Record a new ``running`` run and return it, unless a ``running`` run started less than
        ``stale_after`` ago exists (then None, and nothing is written). Safe against two callers at once, also on two
        replicas."""
        ...

    async def finish_run(
        self,
        run_id: str,
        *,
        now: datetime,
        status: RunStatus,
        employers_ok: int,
        employers_failed: int,
        listings_open: int,
        listings_closed: int,
        note: str | None,
    ) -> None:
        """Close a run with its outcome."""
        ...

    async def last_ok_run(self) -> DiscoverRun | None:
        """The newest run with status ``ok``, or None."""
        ...

    async def first_run(self) -> DiscoverRun | None:
        """The oldest run of any status, or None while there has never been one."""
        ...

    async def latest_run(self) -> DiscoverRun | None:
        """The newest run of any status, or None while there has never been one."""
        ...

    async def purge_closed(self, cutoff: datetime) -> int:
        """Delete listings closed before ``cutoff``; returns how many."""
        ...


class JobSearchRepo(Protocol):
    """Searches with AI and what was written for their roles. Everything is scoped to a profile id."""

    async def save_run(self, run: StoredSearchRun) -> None:
        """Store a finished search."""
        ...

    async def latest_run(self, profile_id: str, city: str | None, remote: bool) -> StoredSearchRun | None:
        """The newest search of this profile for this city (None: no city) and remote choice, or None."""
        ...

    async def get_run(self, run_id: str) -> StoredSearchRun | None:
        """The search with this id, or None (the caller checks it is the asker's profile)."""
        ...

    async def get_doc(self, profile_id: str, job_id: str, kind: str) -> StoredSearchDoc | None:
        """The stored prep page or referral note for this profile and role, or None."""
        ...

    async def save_doc(self, doc: StoredSearchDoc) -> None:
        """Store a prep page or referral note, replacing the one for the same profile, role and kind."""
        ...

    async def purge(self, cutoff: datetime) -> int:
        """Delete searches and documents created before ``cutoff``; returns how many rows went."""
        ...


__all__ = [
    "BillingRepo",
    "ChatMessage",
    "ChatSession",
    "ConsumeOutcome",
    "ConsumeSource",
    "DecisionLogRepo",
    "DiscoverListing",
    "DiscoverRepo",
    "DiscoverRun",
    "JobSearchRepo",
    "KvRepo",
    "LimitRepo",
    "ListingStatus",
    "LlmCallRepo",
    "MatchListing",
    "MatchRepo",
    "OpenProfile",
    "OwnedProfile",
    "PortfolioConflict",
    "PortfolioRepo",
    "PostingRepo",
    "ProfileAccess",
    "ProfileFactsRepo",
    "ProfileNotClaimable",
    "ProfileOwnedByAnother",
    "ProfileRepo",
    "RunStatus",
    "SessionConflict",
    "SessionNotFound",
    "SessionRepo",
    "SettleResult",
    "SettleStatus",
    "StoredAuthSession",
    "StoredEntitlements",
    "StoredMagicLink",
    "StoredMatch",
    "StoredOrder",
    "StoredPortfolio",
    "StoredSearchDoc",
    "StoredSearchRun",
    "StoredUser",
    "StoredVariant",
    "ToolEvent",
    "UpsertCounts",
    "UserFactRepo",
    "UserRepo",
    "VariantKind",
]
