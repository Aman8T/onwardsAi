"""Repository Protocols and the small value types they exchange.

Services depend on these, never on SQLAlchemy, so every service is unit-tested against the dict-backed
fakes in ``tests/fakes/repos.py``. The SQLAlchemy implementations live in `repos/`.

Profiles travel as JSON documents (``dict``): storage does not import the Profile model, so a change to
the model never needs a storage change (``schema_version`` records which shape a document has).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Mapping, Sequence
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

    async def record_spend(self, at: datetime, micro_usd: int, scope: str) -> None:
        """Add one call costing ``micro_usd`` to the spend ledger on ``at``'s UTC day, in scope ``total`` and in
        ``scope`` (``browser_use``, say): spend that no LLM-call row records, so the daily ceiling sees it too."""
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


class EmailTaken(RuntimeError):
    """`UserRepo.sign_in_supabase` found the verified email on an account already linked to a different Leafout sign-in.
    ``user_id`` is that account's id (an internal id, safe to log); nothing was linked or changed."""

    def __init__(self, user_id: str) -> None:
        super().__init__("email_taken")
        self.user_id = user_id


@dataclass(frozen=True, slots=True)
class StoredUser:
    """One account. ``email`` is lower-cased, and None for an account made from a phone-only Leafout sign-in; ``currency`` is ``INR``, ``USD`` or None (not chosen yet)."""

    id: str
    email: str | None
    name: str | None
    picture_url: str | None
    google_sub: str | None
    created_at: datetime
    last_login_at: datetime
    locale: str | None
    currency: str | None
    supabase_sub: str | None = None


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
    source: str | None = None
    """``supabase`` for a session started from the Leafout site's cookie, None for the app's own sign-ins."""
    supabase_session_id: str | None = field(default=None, repr=False)
    verified_at: datetime | None = None
    """When the site's cookie last vouched for the session (None for a session that does not depend on it)."""


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

    async def sign_in_supabase(
        self, *, sub: str, email: str | None, email_verified: bool, name: str | None, now: datetime
    ) -> StoredUser:
        """Find the account for a Leafout (Supabase) sign-in and record the login, in one transaction.

        1. By ``supabase_sub``; when ``email_verified`` and the address changed on the site and no other account holds it,
           the account's email follows.
        2. Else, only when ``email`` is verified: by email. An account with no ``supabase_sub`` gets this one (it links an
           account made by Google or an emailed link); one that already has a different ``supabase_sub`` raises `EmailTaken`
           and nothing is linked.
        3. Else a new account: ``email`` only when verified (an unverified or missing address leaves it None), ``name`` as
           given. The phone number is never stored.
        """
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

    async def mark_session_verified(self, session_id: str, verified_at: datetime) -> None:
        """Record that the Leafout site's cookie vouched for a session again (``auth_sessions.verified_at``)."""
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

ConsumeSource = Literal["pass", "free_monthly", "credit", "free_apply", "hold"]
"""What paid for one use: an active pass, the month's free allowance, or one posting credit. For an auto-apply credit
hold: one of the free applications (``free_apply``), credits (``credit``), or a hold of nothing while no price is set
(``hold``)."""
HoldState = Literal["held", "charged", "released"]
"""Where an auto-apply credit hold stands: taken and still open, charged (captured), or given back (released)."""
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
        """Give back what ``consume`` or ``hold`` took for ``ref`` (the credit returns, a free posting or a free
        application counts as unused again). True when something was given back; False when ``ref`` was never paid
        for, was already given back, or is a hold that was captured (a charge is never given back here)."""
        ...

    async def hold(
        self, user_id: str, ref: str, *, now: datetime, free_allowance: int, cost: int | None
    ) -> ConsumeOutcome | None:
        """Take an auto-apply credit hold for ``ref`` (``apply:<application id>``): a free application while fewer
        than ``free_allowance`` are in use over the account's lifetime (``apply_free``, delta 0), else ``cost``
        credits (``apply_hold``, delta ``-cost``); None when the credits do not cover ``cost``. ``cost=None`` (no
        price set) holds nothing: an ``apply_hold`` row with delta 0, so capture and release still work. Idempotent on
        ``(user_id, ref)``: a live hold (captured or not) is returned as ``replayed``; after a release a new hold is
        taken."""
        ...

    async def capture(self, user_id: str, ref: str, *, now: datetime) -> bool:
        """Charge the live hold for ``ref``: an ``apply_capture`` row (delta 0, ``ref`` ``capture:<hold row id>``),
        after which `release` refuses it. True when the hold is captured (by this call or an earlier one); False
        when there is no live hold (never taken, or released)."""
        ...

    async def hold_state(self, user_id: str, ref: str) -> HoldState | None:
        """Where the latest hold for ``ref`` stands, from the ledger; None when no hold was ever taken."""
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

    async def replace(
        self, user_id: str | None, profile_id: str, fact: Fact, *, question: str, answer: str
    ) -> None:
        """One slot per ``question`` (with no match): every fact of the profile stored for ``question`` without a match
        is removed, then ``fact`` is stored. A changed answer never leaves the old, contradicting fact behind."""
        ...

    async def remove(self, profile_id: str, question: str) -> int:
        """Remove the facts of the profile stored for ``question`` without a match (an answer withdrawn); how many."""
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
class StoredInterview:
    """One practice interview (`InterviewSessionRow`). ``state`` holds everything the service needs to carry on (the
    plan, the phrased lines and hidden answer notes, the turns, the per-slot judgements, the work sample and the
    assistant log); ``debrief`` the `Debrief` once it is done. ``version`` goes up by one on every save, so two tabs that
    answer at once cannot overwrite each other (`InterviewRepo.save`)."""

    id: str
    profile_id: str
    kind: str
    status: str
    created_at: datetime
    updated_at: datetime
    version: int
    state: dict[str, Any]
    debrief: dict[str, Any] | None


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

    async def list_open_since(self, since: datetime) -> list[DiscoverListing]:
        """Every open listing first seen at or after ``since``, newest first, without its text."""
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


class InterviewRepo(Protocol):
    """Practice interviews. Everything is scoped to a profile id: another profile's session id reads as None."""

    async def create(self, item: StoredInterview) -> None:
        """Store a new session (``version`` 1)."""
        ...

    async def get(self, profile_id: str, session_id: str) -> StoredInterview | None:
        """The session with this id when it belongs to this profile, else None."""
        ...

    async def save(self, item: StoredInterview) -> bool:
        """Write ``state``, ``status``, ``debrief`` and ``updated_at`` with ``version`` set to ``item.version``, only if
        the stored version is ``item.version - 1``. Returns False (and writes nothing) when it is not: someone saved first."""
        ...

    async def list(self, profile_id: str, limit: int) -> list[StoredInterview]:
        """This profile's sessions, newest first, at most ``limit``."""
        ...

    async def delete(self, profile_id: str, session_id: str) -> bool:
        """Delete one session of this profile; False when there was none."""
        ...

    async def count_since(self, profile_id: str, since: datetime) -> int:
        """Sessions this profile started at or after ``since`` (for "left today")."""
        ...

    async def purge(self, cutoff: datetime) -> int:
        """Delete sessions created before ``cutoff``; returns how many went."""
        ...


# ───────────────────────────── prepare ─────────────────────────────


@dataclass(frozen=True, slots=True)
class StoredPlan:
    """One plan for a role (`PreparePlanRow`). ``content`` is the `PreparePlan` JSON without ``state`` and ``version``;
    ``state`` is the `PrepareState` JSON the person changes; ``version`` goes up by one on every state save."""

    id: str
    profile_id: str
    role_id: str
    requirement: str | None
    content: dict[str, Any]
    state: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class PrepareRepo(Protocol):
    """Plans for roles. Everything is scoped to a profile id: another profile's plan id reads as None."""

    async def create(self, item: StoredPlan) -> None:
        """Store a new plan (``version`` 1). The profile must exist (the SQL repository enforces it)."""
        ...

    async def get(self, profile_id: str, plan_id: str) -> StoredPlan | None:
        """The plan with this id when it belongs to this profile, else None."""
        ...

    async def find(self, profile_id: str, role_id: str, requirement: str | None) -> StoredPlan | None:
        """The newest plan of this profile for this role and requirement (None: the whole role), or None."""
        ...

    async def save_state(
        self,
        profile_id: str,
        plan_id: str,
        *,
        state: dict[str, Any],
        expected_version: int,
        now: datetime,
    ) -> bool:
        """Write ``state`` and ``updated_at`` with ``version`` set to ``expected_version + 1``, only if the stored version
        is still ``expected_version``. False (and nothing written) when it is not, or the plan is not this profile's."""
        ...

    async def list_for_profile(self, profile_id: str, limit: int) -> list[StoredPlan]:
        """This profile's plans, newest first (by ``created_at``), at most ``limit``."""
        ...


# ───────────────────────────── claim marks (I2) ─────────────────────────────


@dataclass(frozen=True, slots=True)
class StoredClaimMark:
    """One judged claim of a finished practice session (`ClaimMarkRow`): an evidence line's verdict in one slot, the rung
    that decided it, the question as asked, the verdict's quote, and the slot's Fix step with where the person put it.
    ``fix_plan_id`` set = in that Prepare plan; ``fix_kept`` = kept in Practice; neither = not placed (or no Fix step)."""

    id: str
    profile_id: str
    claim_key: str
    claim_text: str
    source: str
    session_id: str
    slot_id: str
    session_kind: str
    slot_kind: str
    verdict: str
    rung: str
    question: str
    marked_on: date
    created_at: datetime
    quote: str | None = None
    story: dict[str, Any] | None = None
    fix_text: str | None = None
    fix_minutes: int | None = None
    fix_plan_id: str | None = None
    fix_kept: bool = False
    fix_added_at: datetime | None = None
    fix_done_at: datetime | None = None


class ClaimMarkRepo(Protocol):
    """Claim marks. Everything is scoped to a profile id: another profile's mark reads as None and cannot be changed."""

    async def add(self, items: Sequence[StoredClaimMark]) -> int:
        """Store the marks that are not there yet, by ``(profile_id, session_id, slot_id)``; returns how many were new. A
        mark already stored is left exactly as it is, its Fix step's place and tick included. The profile must exist."""
        ...

    async def list_for_profile(self, profile_id: str, limit: int) -> list[StoredClaimMark]:
        """This profile's marks, oldest first (by ``marked_on``, then ``created_at``, then ``id``), at most ``limit``."""
        ...

    async def session_ids(self, profile_id: str) -> set[str]:
        """The sessions of this profile that have marks (so a history read can write the missing ones)."""
        ...

    async def get(self, profile_id: str, mark_id: str) -> StoredClaimMark | None:
        """The mark with this id when it is this profile's, else None."""
        ...

    async def find(self, profile_id: str, session_id: str, slot_id: str) -> StoredClaimMark | None:
        """This profile's mark for one slot of one session, or None."""
        ...

    async def place_fix(self, profile_id: str, mark_id: str, *, plan_id: str | None, now: datetime) -> bool:
        """Put the mark's Fix step in a plan (``plan_id``) or keep it in Practice (None), with ``fix_added_at`` = ``now``.
        Placing it again moves it; its tick stays. False (nothing written) when the mark is not this profile's or has no
        Fix step. The plan is not checked here: the service checks it is this profile's."""
        ...

    async def set_fix_done(self, profile_id: str, mark_id: str, done_at: datetime | None) -> bool:
        """Tick (``done_at``) or untick (None) the mark's placed Fix step. False when the mark is not this profile's or its
        Fix step is not placed."""
        ...

    async def clear_fix(self, profile_id: str, mark_id: str) -> bool:
        """Take the Fix step out of wherever it is (no plan, not kept, no tick); the step's text stays on the mark. False
        when the mark is not this profile's or its Fix step was not placed."""
        ...

    async def fixes_for_plan(self, profile_id: str, plan_id: str) -> list[StoredClaimMark]:
        """This profile's marks whose Fix step is in this plan, the oldest placed first."""
        ...

    async def kept_fixes(self, profile_id: str) -> list[StoredClaimMark]:
        """This profile's marks whose Fix step is kept in Practice, the oldest placed first."""
        ...

    async def delete_for_session(self, profile_id: str, session_id: str) -> int:
        """Delete one session's marks (the person deleted the session); returns how many went."""
        ...


# ───────────────────────────── hand-off from the site ─────────────────────────────


@dataclass(frozen=True, slots=True)
class StoredHandoff:
    """What the marketing site posted (`HandoffRow`). ``links`` are already checked http(s) addresses."""

    id: str
    created_at: datetime
    expires_at: datetime
    links: list[str]
    goal: str | None = None
    notes: str | None = None
    resume_name: str | None = None
    resume_type: str | None = None
    resume_bytes: bytes | None = field(default=None, repr=False)


class HandoffRepo(Protocol):
    """Hand-offs waiting to be taken. Nothing is ever read twice."""

    async def create(self, item: StoredHandoff) -> None:
        """Store a new hand-off."""
        ...

    async def take(self, handoff_id: str) -> StoredHandoff | None:
        """Delete the hand-off and return it, in one statement (two callers never both get it); None when there is none.
        An expired row is deleted and returned like any other: the caller judges ``expires_at``."""
        ...

    async def purge_expired(self, now: datetime) -> int:
        """Delete every hand-off whose ``expires_at`` is before ``now``; the number removed."""
        ...


# ───────────────────────────── the Leafout Weekly ─────────────────────────────


@dataclass(frozen=True, slots=True)
class StoredWeeklySubscription:
    """One account's subscription (`WeeklySubscriptionRow`). ``status`` is ``active``, ``paused`` or ``off``; ``day`` is
    ``mon`` or ``fri``; ``sections`` are the parts the reader wants; ``unsubscribe_token`` is the secret of the one-click link."""

    user_id: str
    email: str | None
    day: str
    sections: list[str]
    status: str
    paused_until: datetime | None
    unsubscribe_token: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredWeeklyEdition:
    """One edition for one reader and week (`WeeklyEditionRow`). ``payload`` is the `WeeklyEdition` JSON without ``id``,
    ``createdAt``, ``emailedAt`` and ``readAt``."""

    id: str
    user_id: str
    issue: int
    week_of: date
    payload: dict[str, Any]
    created_at: datetime
    emailed_at: datetime | None = None
    read_at: datetime | None = None


class WeeklyRepo(Protocol):
    """Subscriptions and editions of The Leafout Weekly, and the small claims that keep two replicas from working on the same
    reader's week. Everything about an edition is scoped to its reader's user id."""

    async def get_subscription(self, user_id: str) -> StoredWeeklySubscription | None:
        """The account's subscription, or None when it never subscribed."""
        ...

    async def get_subscription_by_token(self, token: str) -> StoredWeeklySubscription | None:
        """The subscription whose unsubscribe token this is, or None."""
        ...

    async def save_subscription(self, item: StoredWeeklySubscription) -> None:
        """Insert the subscription or replace the account's (every column but ``user_id`` and ``created_at``)."""
        ...

    async def unsubscribe_by_token(self, token: str, now: datetime) -> bool:
        """Set the status of the subscription with this token to ``off`` (and clear ``paused_until``); False when no
        subscription has the token. Safe to repeat."""
        ...

    async def due_subscriptions(
        self, *, day: str, week_of: date, now: datetime, limit: int
    ) -> list[StoredWeeklySubscription]:
        """Subscriptions whose reader wants ``day``, that are ``active`` or ``paused`` with ``paused_until`` at or before
        ``now``, and that have no edition for ``week_of``. At most ``limit``, in user id order."""
        ...

    async def unsent_editions(
        self, *, day: str, week_of: date, now: datetime, limit: int
    ) -> list[tuple[StoredWeeklySubscription, StoredWeeklyEdition]]:
        """The same subscriptions (with an email address) whose edition for ``week_of`` exists but has not been emailed."""
        ...

    async def create_edition(self, item: StoredWeeklyEdition) -> bool:
        """Store a new edition; False (nothing written) when the reader already has one for ``item.week_of``."""
        ...

    async def get_edition(self, user_id: str, edition_id: str) -> StoredWeeklyEdition | None:
        """The reader's edition with this id, or None (another reader's id is None too)."""
        ...

    async def edition_for_week(self, user_id: str, week_of: date) -> StoredWeeklyEdition | None:
        """The reader's edition for this week, or None."""
        ...

    async def list_editions(self, user_id: str, limit: int) -> list[StoredWeeklyEdition]:
        """The reader's editions, newest first (by ``week_of``), at most ``limit``."""
        ...

    async def next_issue(self, user_id: str) -> int:
        """1 for a reader with no edition, else the highest issue number plus one."""
        ...

    async def mark_emailed(self, user_id: str, edition_id: str, now: datetime) -> None:
        """Set ``emailed_at`` (once; a later call keeps the first time)."""
        ...

    async def mark_read(self, user_id: str, edition_id: str, now: datetime) -> None:
        """Set ``read_at`` (once; a later call keeps the first time)."""
        ...

    async def unread_count(self, user_id: str) -> int:
        """The reader's editions not yet opened in the app."""
        ...

    async def claim(
        self, key: str, week_of: date, *, now: datetime, hold: timedelta, max_tries: int
    ) -> int | None:
        """Win the right to work on ``key`` (for example ``compose:<user id>``) for this week: the attempt number (1, 2 ...)
        for exactly one caller per ``hold``, across replicas, or None when another caller still holds it or ``max_tries``
        attempts were already made for ``week_of``. Attempts of an earlier week do not count."""
        ...

    async def paid_this_week(self, week_of: date) -> int:
        """Editions of the week that were allowed to spend on a model, as counted by `add_paid`."""
        ...

    async def add_paid(self, week_of: date, now: datetime) -> None:
        """Count one more edition of the week that spends on a model (a soft counter: two callers at once may both add)."""
        ...


@dataclass(frozen=True, slots=True)
class StoredCardSnapshot:
    """One account's radar card (`CardSnapshotRow`). ``areas`` are ``{id, name, short, score, roleTarget?}`` in the graph's
    order, ``skills`` ``{id, name, score}`` (at most three); ``slug`` is None while the card is not shared; ``version`` is the
    first 8 hex characters of the sha256 of what the card draws, and ``updated_at`` is when that last changed (the date on
    the card)."""

    user_id: str
    profile_id: str
    slug: str | None
    shared: bool
    theme: str
    areas: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    role_id: str | None
    role_title: str | None
    version: str
    created_at: datetime
    updated_at: datetime
    line: str | None = None
    """The one line under the shape, chosen by the owner (a sentence of their read or their own words); None for none."""


class CardRepo(Protocol):
    """Radar cards, one per account. The slug is the only public key: `get_by_slug` finds a card only while it is shared."""

    async def get(self, user_id: str) -> StoredCardSnapshot | None:
        """The account's card, or None when it never had one."""
        ...

    async def get_by_slug(self, slug: str) -> StoredCardSnapshot | None:
        """The shared card with this slug, or None (an unknown slug, or a card no longer shared)."""
        ...

    async def save(self, item: StoredCardSnapshot) -> None:
        """Insert the card or replace the account's (every column but ``user_id`` and ``created_at``). A slug another card
        already holds raises (the fake a ValueError, SQL an IntegrityError); at 36^12 slugs it does not happen by chance."""
        ...

    async def list_page(self, after_user_id: str | None, limit: int) -> list[StoredCardSnapshot]:
        """At most ``limit`` cards in user id order, starting after ``after_user_id`` (the daily refresh walks them)."""
        ...


# ───────────────────────────── resumes (R2) ─────────────────────────────

ResumeKind = Literal["upload", "path", "posting"]
"""What a stored resume is: the resume as the person wrote it, one arranged for a next role, or the one tailored for a
posting that the person saved from Match (``role_key`` is then the match id)."""


@dataclass(frozen=True, slots=True)
class StoredResume:
    """One resume of a profile (`ResumeRow`). ``draft`` and ``final`` are `ResumeVariant` JSON (``final`` with its lint,
    None until the resume is finalised), ``facts`` the `match.models.FactBook` JSON the lines cite. Storage keeps all
    three opaque: the resume service validates them. ``role_key`` is ``''`` for the upload and the `Role.id` for a path.
    ``version`` counts the writes of the draft."""

    id: str
    user_id: str | None
    """The account the resume belongs to; None for a profile with no account."""
    profile_id: str
    kind: ResumeKind
    role_key: str
    title: str
    draft: dict[str, Any]
    final: dict[str, Any] | None
    facts: dict[str, Any]
    is_default: bool
    version: int
    created_at: datetime
    updated_at: datetime
    finalised_at: datetime | None = None


class ResumeRepo(Protocol):
    """Resumes, at most one per ``(profile, kind, role_key)``. Who may open one is decided above storage, from the
    resume's profile (`ProfileRepo.get_access`): the repository reads by id alone."""

    async def create(self, item: StoredResume) -> StoredResume:
        """Store a new resume and return it. When the profile already has one of this ``(kind, role_key)`` (two first
        requests at once), nothing is written and the one already stored is returned."""
        ...

    async def get(self, resume_id: str) -> StoredResume | None:
        """The resume with this id, or None."""
        ...

    async def find(self, profile_id: str, kind: ResumeKind, role_key: str) -> StoredResume | None:
        """The profile's resume of this kind (and role), or None."""
        ...

    async def list_for_profile(self, profile_id: str) -> list[StoredResume]:
        """Every resume of the profile, oldest first (the upload is made first)."""
        ...

    async def save(self, item: StoredResume, *, expected_version: int) -> bool:
        """Write ``draft``, ``final``, ``facts``, ``finalised_at``, ``updated_at`` and ``version`` of ``item``, only if
        the stored version is still ``expected_version``. False (and nothing written) when it is not, or the resume is
        gone. Never changes the owner, the profile, the kind, the title or the default."""
        ...

    async def set_default(self, profile_id: str, resume_id: str) -> bool:
        """Make ``resume_id`` the profile's one default (every other resume of the profile stops being it), in one
        transaction. False (and nothing changed) when the resume is not this profile's or has no finalised version."""
        ...

    async def adopt(self, profile_id: str, user_id: str) -> int:
        """Give the profile's resumes that have no account to ``user_id`` (the profile was claimed); returns the count."""
        ...


ResumeFileOrigin = Literal["analysis", "upload"]


@dataclass(frozen=True, slots=True)
class StoredResumeFile:
    """A resume file of a profile (`ResumeFileRow`) without its bytes (`ResumeFileRepo.get_data` reads those). ``facts``,
    ``merge`` and ``merged_book`` are opaque JSON (a `FactBook`, a `MergeReport`, a `FactBook`); the resume service validates
    them. ``merge`` and ``merged_book`` are None for the file a profile was analysed from."""

    id: str
    user_id: str | None
    profile_id: str
    origin: ResumeFileOrigin
    filename: str
    mime: str
    size: int
    sha256: str
    text: str
    facts: dict[str, Any]
    merge: dict[str, Any] | None
    merged_book: dict[str, Any] | None
    created_at: datetime


class ResumeFileRepo(Protocol):
    """The resume files of a profile, with their original bytes. Who may open one is decided above storage, from the
    file's profile (`ProfileRepo.get_access`)."""

    async def add(self, item: StoredResumeFile, data: bytes) -> StoredResumeFile:
        """Store a file with its bytes. A second add of the same id changes nothing and returns the stored one."""
        ...

    async def get(self, file_id: str) -> StoredResumeFile | None:
        """The file with this id (without its bytes), or None."""
        ...

    async def get_data(self, file_id: str) -> bytes | None:
        """The original bytes of the file, or None when there is no such file."""
        ...

    async def list_for_profile(self, profile_id: str) -> list[StoredResumeFile]:
        """Every file of the profile, oldest first, without bytes."""
        ...

    async def adopt(self, profile_id: str, user_id: str) -> int:
        """Give the profile's files that have no account to ``user_id`` (the profile was claimed); returns the count."""
        ...


# ───────────────────────────── auto-apply ─────────────────────────────


APPLICATION_COLUMNS: frozenset[str] = frozenset(
    {
        "profile_id", "match_id", "apply_url", "manual_url", "domain", "ats", "company", "title", "location", "job",
        "stage", "stage_reason", "needs", "review", "review_digest", "approved_at", "approved_digest", "submitted_at",
        "attempts", "lease_owner", "lease_until", "posting_key", "resume_choice", "listing_id", "submitting_at",
        "credit_ref", "credit_state", "stop_reason", "progress", "resume_copy",
    }
)  # fmt: skip
"""The columns `ApplyRepo.update_application` may change (the owner, source, job link and creation time never change; the
review and confirmation screenshots have their own calls)."""

NEVER_STORED_ANSWER_KEYS: frozenset[str] = frozenset(
    {
        "pan", "aadhaar", "passportNumber", "idNumber", "bankAccount", "ifsc", "casteCategory", "religion",
        "maritalStatus", "fatherName", "signature", "declaration", "consent", "knowsSomeoneHere", "referral",
        "previousApplication",
    }
)  # fmt: skip
"""Answer keys the answer bank never stores (mirrors ``contract.apply.NEVER_STORED_KEYS``; a test keeps them equal): ID
numbers, caste, religion, marital status, father's name, signatures, declarations and consent ticks, referrals and
earlier applications are typed on the employer's page or in Review for one form. `ApplyRepo.put_answer` refuses them
(a second check behind the service's)."""

FILLED_TERMINAL_STAGES: frozenset[str] = frozenset(
    {"sent", "failed", "closed", "cancelled", "viewed", "interviewing", "offer", "rejected"}
)
"""Stages whose filled values `ApplyRepo.purge_filled` may clear: nothing more will be filled or sent from them.
``check_this_one`` is left alone until the person resolves it."""

NOT_ON_SITE_OUTCOME = "deferred"
"""The ``run_metrics.outcome`` of a run put back for capacity (a Browser Use 429, the spend ceiling) before it reached the
site: kept for the metrics, left out of the per-site limit."""

ACCOUNT_OWNER_PREFIX = "u:"
"""``owner_key`` of an account's applications and profile is ``u:<user id>``."""


def account_owner_key(user_id: str) -> str:
    """The ``owner_key`` of an account's applications and apply profile."""
    return f"{ACCOUNT_OWNER_PREFIX}{user_id}"


def is_never_stored(question_key: str) -> bool:
    """True when the answer bank must not keep ``question_key`` (its part before any ``:`` is a never-stored key)."""
    return question_key.split(":", 1)[0] in NEVER_STORED_ANSWER_KEYS


class PostingTaken(RuntimeError):
    """A new application for a posting the person already has one for (the unique ``(user_id, posting_key)``), as
    `EmailTaken` is for an email. ``application_id`` is that application's id (an internal id, safe to log); nothing
    was created. The apply service raises it when its insert loses that race; the repositories still raise their own
    duplicate error (``IntegrityError`` in SQL, ValueError in the fake) underneath."""

    def __init__(self, application_id: str) -> None:
        super().__init__("posting_taken")
        self.application_id = application_id


@dataclass(frozen=True, slots=True)
class StoredApplication:
    """One application (`ApplicationRow`) without its screenshot (`ApplyRepo.get_screenshot`). JSON columns are opaque
    here: the apply service validates them against `contract.apply`."""

    id: str
    owner_key: str
    source: str
    job_url: str
    domain: str
    ats: str
    stage: str
    created_at: datetime
    updated_at: datetime
    user_id: str
    """The account (``owner_key`` is ``u:<user_id>``); auto-apply is for accounts only."""
    profile_id: str | None = None
    match_id: str | None = None
    apply_url: str | None = None
    manual_url: str | None = None
    company: str | None = None
    title: str | None = None
    location: str | None = None
    job: dict[str, Any] | None = None
    stage_reason: str | None = None
    needs: list[dict[str, Any]] | None = None
    review: dict[str, Any] | None = None
    review_digest: str | None = None
    has_screenshot: bool = False
    approved_at: datetime | None = None
    approved_digest: str | None = None
    submitted_at: datetime | None = None
    attempts: int = 0
    lease_owner: str | None = None
    lease_until: datetime | None = None
    posting_key: str | None = None
    """``<ats>:<board>:<job id>``, else the sha256 of the normalised apply URL: unique per person when set."""
    resume_choice: str | None = None
    """``tailored`` or ``saved``."""
    listing_id: str | None = None
    submitting_at: datetime | None = None
    """The marker committed just before the submit click (with stage ``submitting``)."""
    credit_ref: str | None = None
    credit_state: str | None = None
    """``held``, ``charged`` or ``released``."""
    stop_reason: str | None = None
    has_confirmation: bool = False
    """Whether a confirmation screenshot is stored (`ApplyRepo.get_confirmation`)."""
    progress: dict[str, Any] | None = None
    resume_copy: dict[str, Any] | None = None
    """The default resume as it stood when the values were prepared (`contract.apply.ResumeCopy` and its ``variant``):
    written with the review, never by a later edit of the resume or a new default."""


@dataclass(frozen=True, slots=True)
class StoredApplicationEvent:
    """One entry of an application's history (`ApplicationEventRow`). ``detail`` never holds a resume, token or email body."""

    id: str
    application_id: str
    at: datetime
    kind: str
    source: str
    from_stage: str | None = None
    to_stage: str | None = None
    detail: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class StoredPlaybook:
    """A stored ATS playbook (`AtsPlaybookRow`); ``spec`` is the `apply.playbook.Playbook` JSON."""

    id: str
    ats: str
    status: str
    origin: str
    version: int
    spec: dict[str, Any]
    successes: int
    failures: int
    created_at: datetime
    updated_at: datetime
    domain: str | None = None
    promoted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredRunMetric:
    """One engine run (`RunMetricRow`)."""

    id: str
    created_at: datetime
    run_kind: str
    ats: str
    domain: str
    mode: str
    outcome: str
    application_id: str | None = None
    playbook_id: str | None = None
    playbook_status: str | None = None
    failure_reason: str | None = None
    steps: int = 0
    duration_ms: int = 0
    cost_micro_usd: int = 0
    fields_total: int = 0
    fields_filled: int = 0
    fields_model: int = 0


@dataclass(frozen=True, slots=True)
class StoredApplyProfile:
    """A person's auto-apply settings and resume (`ApplyProfileRow`); their answers are in the answer bank."""

    owner_key: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    resume_name: str | None = None
    resume_type: str | None = None
    resume_bytes: bytes | None = field(default=None, repr=False)
    resume_sha256: str | None = None
    self_id_on_forms: bool = False
    first_check_at: datetime | None = None
    consent_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredAnswer:
    """One saved answer in a person's answer bank (`AnswerBankRow`). ``value`` is None for an "ask each time" entry with
    no answer yet; ``source`` is ``resume``, ``first_check``, ``form``, ``you`` or ``default``."""

    user_id: str
    question_key: str
    question: str
    source: str
    ask_each_time: bool
    answered_at: datetime
    created_at: datetime
    updated_at: datetime
    value: str | None = field(default=None, repr=False)
    answered_on: str | None = None
    application_id: str | None = None
    confirmed_at: datetime | None = None


class ApplyRepo(Protocol):
    """Auto-apply's applications (with their history and screenshots), playbooks, run metrics and saved profiles.

    Reads that take ``owner_key`` answer another owner's id exactly like an unknown one; ``owner_key=None`` is the worker's
    unscoped read. `claim_next` hands one runnable application to one worker at a time (a lease), on SQLite and Postgres.
    """

    async def create_application(self, item: StoredApplication) -> None:
        """Insert a new application. Raises when the person already has an application with the same ``posting_key``
        (``sqlalchemy.exc.IntegrityError`` in SQL, ValueError in the fake); NULL posting keys may repeat."""
        ...

    async def find_by_posting(self, user_id: str, posting_key: str) -> StoredApplication | None:
        """The person's application for the posting ``posting_key``, or None."""
        ...

    async def get_application(self, application_id: str, owner_key: str | None) -> StoredApplication | None:
        """The application (scoped to ``owner_key`` unless None), or None."""
        ...

    async def list_applications(self, owner_key: str, limit: int = 200) -> list[StoredApplication]:
        """The owner's applications, newest first."""
        ...

    async def update_application(
        self,
        application_id: str,
        changes: Mapping[str, Any],
        *,
        now: datetime,
        expected_stage: str | None = None,
    ) -> StoredApplication | None:
        """Apply ``changes`` (keys from `APPLICATION_COLUMNS`) and stamp ``updated_at``; None when the application does
        not exist or, with ``expected_stage``, is no longer in that stage (nothing is changed then). A key outside
        `APPLICATION_COLUMNS` raises ValueError; a ``posting_key`` the person already uses raises like
        `create_application`."""
        ...

    async def delete_application(
        self, application_id: str, owner_key: str, *, expected_stage: str | None = None
    ) -> bool:
        """Delete the owner's application with its history; False when there was none or, with ``expected_stage``, it
        is no longer in that stage (nothing is deleted then: a compare-and-set against the worker's marker)."""
        ...

    async def count_created_since(self, owner_key: str, since: datetime) -> int:
        """How many applications the owner created at or after ``since``."""
        ...

    async def claim_next(
        self, *, now: datetime, lease_owner: str, lease_until: datetime, exclude_domains: Collection[str] = ()
    ) -> StoredApplication | None:
        """Take the oldest runnable application whose lease is free and whose domain is not in ``exclude_domains``: one in
        ``queued`` (to fill) or ``approved`` (to submit). A row in ``submitting`` is never taken, even with an expired
        lease: the click may have happened. The lease is written in the same statement that checks it, so two workers
        never hold one application."""
        ...

    async def list_expired(
        self, stages: Collection[str], *, now: datetime, limit: int = 50
    ) -> list[StoredApplication]:
        """Applications in one of ``stages`` that no worker holds (no lease, or a lease that ended before ``now``),
        least recently updated first: the worker's sweep (``submitting`` -> ``check_this_one``, ``filling`` ->
        ``queued``)."""
        ...

    async def list_waiting(
        self, user_id: str, stages: Collection[str] = ("waiting_for_you",)
    ) -> list[StoredApplication]:
        """The person's applications in one of ``stages``, oldest first (an answer saved on one re-queues the others
        that ask the same question)."""
        ...

    async def release(self, application_id: str, lease_owner: str) -> None:
        """Clear the lease if ``lease_owner`` still holds it."""
        ...

    async def get_screenshot(self, application_id: str, owner_key: str | None) -> bytes | None:
        """The review screenshot (scoped like `get_application`), or None."""
        ...

    async def put_screenshot(self, application_id: str, data: bytes | None) -> None:
        """Replace (or with None, remove) the review screenshot."""
        ...

    async def get_confirmation(self, application_id: str, owner_key: str | None) -> bytes | None:
        """The confirmation screenshot (scoped like `get_application`), or None."""
        ...

    async def put_confirmation(self, application_id: str, data: bytes | None) -> None:
        """Replace (or with None, remove) the confirmation screenshot."""
        ...

    async def purge_filled(self, before: datetime) -> int:
        """Clear what was filled into the form on applications in a `FILLED_TERMINAL_STAGES` stage last updated before
        ``before``: both screenshots, the review, the needs and ``job["typed"]``. The stage, digests, times and history
        stay, and ``updated_at`` is not touched. How many applications changed."""
        ...

    async def add_event(self, event: StoredApplicationEvent) -> None:
        """Append an entry to an application's history."""
        ...

    async def list_events(self, application_id: str, limit: int = 200) -> list[StoredApplicationEvent]:
        """The application's history, oldest first."""
        ...

    async def list_playbooks(
        self, *, ats: str | None = None, domain: str | None = None, statuses: Collection[str] | None = None
    ) -> list[StoredPlaybook]:
        """Playbooks, filtered by ATS key, domain and status (None: any), newest first."""
        ...

    async def get_playbook(self, playbook_id: str) -> StoredPlaybook | None: ...

    async def save_playbook(self, item: StoredPlaybook) -> None:
        """Insert the playbook, or replace the one with the same id."""
        ...

    async def record_playbook_result(
        self, playbook_id: str, *, success: bool, now: datetime
    ) -> StoredPlaybook | None:
        """Count a success or a failure for the playbook (in one statement); the playbook after it, or None."""
        ...

    async def set_playbook_status(
        self, playbook_id: str, status: str, *, now: datetime
    ) -> StoredPlaybook | None:
        """Change a playbook's status (``promoted_at`` is stamped when it becomes active); None when there is none."""
        ...

    async def add_run_metric(self, item: StoredRunMetric) -> None: ...

    async def list_run_metrics(self, since: datetime, limit: int = 20000) -> list[StoredRunMetric]:
        """Runs at or after ``since``, newest first."""
        ...

    async def count_domain_runs_since(self, domain: str, since: datetime) -> int:
        """Engine runs against ``domain`` at or after ``since`` (the per-site limit). A run put back for capacity
        (outcome `NOT_ON_SITE_OUTCOME`) never reached the site and is not counted."""
        ...

    async def last_domain_run_at(self, domain: str) -> datetime | None:
        """When the latest run against ``domain`` started (a deferred one left out, like `count_domain_runs_since`),
        or None."""
        ...

    async def get_profile(self, owner_key: str) -> StoredApplyProfile | None: ...

    async def put_resume(
        self,
        owner_key: str,
        *,
        user_id: str,
        name: str | None,
        media_type: str | None,
        data: bytes | None,
        now: datetime,
        sha256: str | None = None,
    ) -> StoredApplyProfile:
        """Create the profile if needed and replace (or with ``data=None``, remove) its resume file and its sha256."""
        ...

    async def set_self_id(self, user_id: str, on: bool, *, now: datetime) -> StoredApplyProfile:
        """Create the account's profile if needed and switch self-identification answers on forms on or off."""
        ...

    async def mark_first_check(self, user_id: str, *, now: datetime) -> StoredApplyProfile:
        """Create the account's profile if needed and record that the first 30-second check was done at ``now``."""
        ...

    async def set_consent(self, user_id: str, *, now: datetime) -> StoredApplyProfile:
        """Create the account's profile if needed and record that the person agreed to the auto-apply terms at ``now``."""
        ...

    async def list_answers(self, user_id: str) -> list[StoredAnswer]:
        """The person's answer bank, in question key order."""
        ...

    async def get_answers(self, user_id: str, keys: Collection[str]) -> dict[str, StoredAnswer]:
        """The person's saved answers for ``keys`` (question key -> answer); keys with no answer are left out."""
        ...

    async def put_answer(self, item: StoredAnswer) -> StoredAnswer:
        """Insert or replace the answer for ``(user_id, question_key)`` (``created_at`` of an existing row is kept); the
        stored answer. Raises ValueError for a never-stored key (`is_never_stored`)."""
        ...

    async def confirm_answer(self, user_id: str, question_key: str, *, now: datetime) -> StoredAnswer | None:
        """Mark the answer as still true at ``now`` ("Still true?"); None when there is none."""
        ...

    async def delete_answer(self, user_id: str, question_key: str) -> bool:
        """Remove the answer; False when there was none."""
        ...


@dataclass(frozen=True, slots=True)
class StoredDeskNote:
    """One desk slip of the dashboard (`DeskNoteRow`); ``at`` is the browser's timestamp in milliseconds."""

    id: str
    kind: str
    text: str
    at: int
    label: str | None = None
    by: str | None = None


class DeskNoteRepo(Protocol):
    """The dashboard's notes of an account's profile. The caller has checked that the profile is the account's own;
    every call is scoped by both ids, so one account's notes never read as another's."""

    async def list(self, user_id: str, profile_id: str) -> list[StoredDeskNote]:
        """The notes, newest ``at`` first (ties by id)."""
        ...

    async def replace(
        self, user_id: str, profile_id: str, notes: Sequence[StoredDeskNote], now: datetime
    ) -> None:
        """Make ``notes`` the whole list: rows not in it are deleted, the others written, in one transaction."""
        ...


@dataclass(frozen=True, slots=True)
class CarriedRows:
    """How many rows `ProfileCarryRepo.carry` moved to the new profile."""

    resumes: int = 0
    resume_files: int = 0
    user_facts: int = 0
    desk_notes: int = 0


class ProfileCarryRepo(Protocol):
    """Moves what a person keeps on a profile (resumes, resume files, the lines they said in their own words, the
    dashboard's notes) to the profile a re-read of their graph made. Applications, matches, practice sessions and plans
    stay with the profile they were made for."""

    async def carry(self, old_profile_id: str, new_profile_id: str, user_id: str | None) -> CarriedRows:
        """Move the rows of ``old_profile_id`` to ``new_profile_id`` in ONE transaction: all of them, or none (an error
        leaves both profiles as they were). Allowed only when ``user_id`` may open the old profile (the account that owns
        it; with None, a profile nobody has claimed) and the new one is that account's or unclaimed, and neither is a
        sample run; otherwise `PermissionError` and nothing changes. A signed-in ``user_id`` also claims the new profile.
        The new profile's own clashing rows (same resume kind and role, same note, same own-words line) are replaced by
        the moved ones, and the moved default resume stays the one default."""
        ...


__all__ = [
    "ACCOUNT_OWNER_PREFIX",
    "APPLICATION_COLUMNS",
    "FILLED_TERMINAL_STAGES",
    "NEVER_STORED_ANSWER_KEYS",
    "NOT_ON_SITE_OUTCOME",
    "ApplyRepo",
    "BillingRepo",
    "CardRepo",
    "CarriedRows",
    "ChatMessage",
    "ChatSession",
    "ClaimMarkRepo",
    "ConsumeOutcome",
    "ConsumeSource",
    "DecisionLogRepo",
    "DeskNoteRepo",
    "DiscoverListing",
    "DiscoverRepo",
    "DiscoverRun",
    "EmailTaken",
    "HandoffRepo",
    "HoldState",
    "InterviewRepo",
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
    "PostingTaken",
    "PrepareRepo",
    "ProfileAccess",
    "ProfileCarryRepo",
    "ProfileFactsRepo",
    "ProfileNotClaimable",
    "ProfileOwnedByAnother",
    "ProfileRepo",
    "ResumeFileOrigin",
    "ResumeFileRepo",
    "ResumeKind",
    "ResumeRepo",
    "RunStatus",
    "SessionConflict",
    "SessionNotFound",
    "SessionRepo",
    "SettleResult",
    "SettleStatus",
    "StoredAnswer",
    "StoredApplication",
    "StoredApplicationEvent",
    "StoredApplyProfile",
    "StoredAuthSession",
    "StoredCardSnapshot",
    "StoredClaimMark",
    "StoredDeskNote",
    "StoredEntitlements",
    "StoredHandoff",
    "StoredInterview",
    "StoredMagicLink",
    "StoredMatch",
    "StoredOrder",
    "StoredPlan",
    "StoredPlaybook",
    "StoredPortfolio",
    "StoredResume",
    "StoredResumeFile",
    "StoredRunMetric",
    "StoredSearchDoc",
    "StoredSearchRun",
    "StoredUser",
    "StoredVariant",
    "StoredWeeklyEdition",
    "StoredWeeklySubscription",
    "ToolEvent",
    "UpsertCounts",
    "UserFactRepo",
    "UserRepo",
    "VariantKind",
    "WeeklyRepo",
    "account_owner_key",
    "is_never_stored",
]
