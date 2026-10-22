"""Typed application settings.

Sources, highest priority first: values passed to ``Settings(...)``, real environment variables, then the
``.env`` files (repo root, then ``backend/``; the later file wins). Missing files are ignored, which is the
normal case in the cloud. Nothing here reads the environment at import time: call `get_settings()` (cached)
or build ``Settings(...)`` yourself, and call ``get_settings.cache_clear()`` after changing the environment.

Variable names:
    third-party names, no prefix      ANTHROPIC_API_KEY, JEV_API_KEY (or TYPESAFE_API_KEY), GITHUB_TOKEN,
                                      DATABASE_URL, LOGFIRE_TOKEN, RAZORPAY_*, STRIPE_*
    our own, prefix ``TAYYARI_``      TAYYARI_SYNTH_MODEL, TAYYARI_API_PORT, TAYYARI_READER_THINKING ...
    nested groups, ``__`` delimiter   TAYYARI_GUARD__DAILY_CEILING_USD, TAYYARI_JEV__ENABLED ...
    JSON                              TAYYARI_DECISION_OVERRIDES='{"evidence.support":{"mode":"shadow"}}'

An empty (or whitespace-only) value counts as unset everywhere.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Protocol

import platformdirs
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .dburl import DbConfig, normalise_database_url
from .paths import BACKEND_DIR, REPO_ROOT

Env = Literal["local", "prod", "test"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
SynthCache = Literal["off", "system", "system+data"]
CacheTtl = Literal["5m", "1h"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
AnonymousMatch = Literal["auto", "true", "false"]
SupabaseVerify = Literal["auto", "jwks", "user_endpoint"]

_CLOUD_MARKERS = ("FASTAPICLOUD_DEPLOYMENT_ID", "FASTAPI_PUBLIC_URL")
_EMAIL_SEPARATORS = re.compile(r"[,;\s]+")
_EMAIL_DECORATION = "[]\"' "


def detect_env() -> Env:
    """``"prod"`` when FastAPI Cloud's deployment variables are present in the environment, else ``"local"``."""
    return "prod" if any(os.environ.get(name, "").strip() for name in _CLOUD_MARKERS) else "local"


@lru_cache(maxsize=16)
def parse_email_list(raw: str) -> tuple[str, ...]:
    """The addresses in ``raw``, trimmed, lower-cased, without repeats, in the order given.

    ``raw`` is a comma-separated list (``a@x.com, b@y.com``) or a JSON list of strings (``["a@x.com","b@y.com"]``);
    semicolons and line breaks separate too. A piece with no ``@`` is dropped, and so is everything when ``raw`` is
    blank. A JSON list that does not parse is read as plain text rather than failing the start-up.
    """
    text = raw.strip()
    if text.startswith("["):
        try:
            loaded = json.loads(text)
        except ValueError:
            loaded = None
        if isinstance(loaded, list):
            text = ",".join(item for item in loaded if isinstance(item, str))
    found: list[str] = []
    for piece in _EMAIL_SEPARATORS.split(text):
        email = piece.strip(_EMAIL_DECORATION).lower()
        if "@" in email and email not in found:
            found.append(email)
    return tuple(found)


def _default_data_dir(data: dict[str, Any]) -> Path:
    """Per-user data folder locally (outside the repo and OneDrive); the temp folder in prod (ephemeral)."""
    if data.get("env") == "prod":
        return Path(tempfile.gettempdir()) / "tayyari"
    return Path(platformdirs.user_data_dir("tayyari", appauthor=False))


class ModelNames(BaseModel):
    """The model id used for each role."""

    model_config = ConfigDict(frozen=True)

    reader: str
    synth: str
    chat: str


class JevSettings(BaseModel):
    """Jev (TypeSafe's decision model): `TAYYARI_JEV__*`."""

    enabled: bool = True
    model: str = "jev-1.13.0"
    """Pinned: every logged decision records it, so replays against a newer Jev never mix with old rows."""
    base_url: str = "https://api.typesafe.ai"
    timeout_display_s: float = Field(default=2.5, gt=0)
    """Budget for decisions that drive what the person sees live (first read)."""
    timeout_evidence_s: float = Field(default=4.0, gt=0)
    """Budget for the post-synthesis evidence checks."""
    max_retries: int = Field(default=1, ge=0)
    send_pii: bool = False
    """False: emails, phones and address lines are masked (`core.redact`) before any text goes to Jev."""


class GuardSettings(BaseModel):
    """Cost and abuse protection: `TAYYARI_GUARD__*`."""

    enabled: bool = True
    analyze_per_minute: int = Field(default=2, ge=0)
    analyze_per_hour: int = Field(default=6, ge=0)
    analyze_per_day: int = Field(default=15, ge=0)
    analyze_global_per_hour: int = Field(default=60, ge=0)
    chat_per_minute: int = Field(default=20, ge=0)
    chat_per_hour: int = Field(default=120, ge=0)
    chat_per_day: int = Field(default=400, ge=0)
    daily_ceiling_usd: float = Field(default=20.0, ge=0)
    """Soft ceiling on spend per UTC day; reaching it switches AI off until midnight."""
    est_analyze_usd: float = Field(default=0.40, ge=0)
    est_chat_usd: float = Field(default=0.10, ge=0)
    ceiling_grace: float = Field(default=1.25, ge=1.0)
    """A run already past the readers may finish while spend is under ceiling x grace."""
    portfolio_per_minute: int = Field(default=4, ge=0)
    portfolio_per_hour: int = Field(default=12, ge=0)
    portfolio_per_day: int = Field(default=30, ge=0)
    portfolio_global_per_hour: int = Field(default=120, ge=0)
    """Portfolio generations and section rewrites (the paid portfolio calls) across all clients per hour."""
    est_portfolio_usd: float = Field(default=0.12, ge=0)
    """Spend reserved before a portfolio generation: Haiku fact reads (one per source), three concurrent Sonnet copy
    calls (fixed slots, experience, projects), one re-ask and the Jev claim checks."""
    match_per_minute: int = Field(default=0, ge=0)
    match_per_hour: int = Field(default=10, ge=0)
    match_per_day: int = Field(default=30, ge=0)
    """Posting matches one signed-in account may start, and (separately) one client address, per window
    (0 = the window is not enforced). Re-checks, resumes and cover notes are not matches and do not count."""
    match_global_per_hour: int = Field(default=120, ge=0)
    """Backstop on posting matches per hour across all accounts (shared counter)."""
    est_match_usd: float = Field(default=0.06, ge=0)
    """Spend reserved before a posting match (and before a resume or cover note is written): the posting read,
    the facts, the gates, two coverage batches, and the phrasing calls, on Haiku."""
    anon_match_per_day: int = Field(default=3, ge=0)
    """Posting matches one client address may start per day without an account (0 = not enforced). Counted for
    `POST /api/match` only, and only for a visitor with no sign-in; the per-address windows above apply as well. With
    no account there is nothing to charge, so this is the only meter on an anonymous run."""
    jobsearch_per_minute: int = Field(default=1, ge=0)
    jobsearch_per_hour: int = Field(default=4, ge=0)
    jobsearch_per_day: int = Field(default=8, ge=0)
    """Searches with AI one signed-in account may start, and (separately) one client address, per window (0 = the window
    is not enforced)."""
    jobsearch_global_per_hour: int = Field(default=40, ge=0)
    """Backstop on searches with AI per hour across all clients (shared counter)."""
    anon_jobsearch_per_day: int = Field(default=3, ge=0)
    """Searches with AI one client address may start per day without an account (0 = not enforced)."""
    est_jobsearch_usd: float = Field(default=0.20, ge=0)
    """Spend reserved before a search with AI: up to nine web searches ($0.01 each) and the Haiku reads around them."""
    est_jobsearch_doc_usd: float = Field(default=0.02, ge=0)
    """Spend reserved before a role's prep page or referral note is written (one Haiku call; a kept one costs nothing)."""
    interview_per_minute: int = Field(default=2, ge=0)
    interview_per_hour: int = Field(default=3, ge=0)
    interview_per_day: int = Field(default=3, ge=0)
    """Practice interviews one signed-in account may start, and (separately) one client address, per window (0 = the
    window is not enforced). Free while we test it, so these are the only meter. Answers, the debrief and "ask this one
    again" are not new interviews."""
    interview_global_per_hour: int = Field(default=60, ge=0)
    """Backstop on practice interviews started per hour across all clients (shared counter)."""
    interview_again_per_day: int = Field(default=20, ge=0)
    """"Ask this one again" (one question of a finished interview) per client address and per account per day."""
    est_interview_usd: float = Field(default=0.03, ge=0)
    """Spend reserved before a practice interview starts (one Haiku call phrases every question)."""
    est_interview_turn_usd: float = Field(default=0.005, ge=0)
    """Spend reserved before an answer is judged, or the work sample's assistant answers (one small Haiku call)."""
    est_interview_end_usd: float = Field(default=0.02, ge=0)
    """Spend reserved before the debrief is written (one Haiku call, plus one for a work sample's checks)."""
    speech_per_minute: int = Field(default=40, ge=0)
    """Speech requests (a transcribed piece of an answer, or a spoken question) one client address may make per minute
    (0 = not enforced). Sarvam's own budget is metered separately (`SpeechSettings`)."""
    prepare_per_minute: int = Field(default=2, ge=0)
    prepare_per_hour: int = Field(default=10, ge=0)
    prepare_per_day: int = Field(default=30, ge=0)
    """Plans ("How do I prepare for this role?") and headline rewrites one signed-in account may ask for, and
    (separately) one client address, per window (0 = the window is not enforced). A stored plan that is returned as it is,
    reading a plan back, saving its state and the repo ranking are not counted: nothing is written by a model for them."""
    prepare_global_per_hour: int = Field(default=120, ge=0)
    """Backstop on plans and headline rewrites per hour across all clients (shared counter)."""
    est_prepare_usd: float = Field(default=0.06, ge=0)
    """Spend reserved before a plan is written: two calls of the chat model at the same time (where you stand, the moves)."""
    est_prepare_headline_usd: float = Field(default=0.02, ge=0)
    """Spend reserved before a headline is rewritten for a plan's role (one short call of the chat model)."""
    chat_global_per_hour: int = Field(default=600, ge=0)
    """Backstop on chat turns per hour across all clients (shared counter)."""
    auth_bridge_per_minute: int = Field(default=10, ge=0)
    auth_bridge_per_hour: int = Field(default=30, ge=0)
    """Times one client may ask the app to turn the Leafout site's cookie into an app session, per window (0 = not enforced).
    Counted only when the cookie's token has passed every local check and Supabase is about to be asked; over the limit the
    request is simply anonymous."""
    auth_bridge_global_per_hour: int = Field(default=600, ge=0)
    """Backstop on those sign-ins per hour across all clients (shared counter)."""
    auth_bridge_per_sub_per_day: int = Field(default=50, ge=0)
    """Sessions one Leafout account may have started per day (0 = not enforced); keyed by a keyed hash, never the id."""
    handoff_per_minute: int = Field(default=3, ge=0)
    handoff_per_hour: int = Field(default=10, ge=0)
    handoff_per_day: int = Field(default=30, ge=0)
    """Hand-offs from the marketing site (a resume and links stored for the app to take) one client address may make per window
    (0 = the window is not enforced). Nothing is paid for a hand-off: the limits keep the table and the uploads small."""
    handoff_global_per_hour: int = Field(default=600, ge=0)
    """Backstop on hand-offs per hour across all clients (shared counter)."""
    weekly_preview_per_hour: int = Field(default=3, ge=0)
    weekly_preview_per_day: int = Field(default=6, ge=0)
    """Editions of The Leafout Weekly one signed-in account (and, separately, one client address) may have built on demand
    ("Read the latest edition first") per window (0 = the window is not enforced). Returning an edition that is already built
    is not counted: nothing is written by a model for it."""
    weekly_global_per_hour: int = Field(default=120, ge=0)
    """Backstop on editions built on demand per hour across all clients (shared counter)."""
    card_per_minute: int = Field(default=120, ge=0)
    card_per_day: int = Field(default=2000, ge=0)
    """Requests for a shared radar card (``/r/{slug}.svg``, ``/og/{slug}-{version}.png``, ``/u/{slug}``) one client address may
    make per window (0 = the window is not enforced). Nothing is paid for: the limits stop a client walking through links."""
    card_global_per_hour: int = Field(default=0, ge=0)
    """Backstop on those requests per hour across all clients (0 = not enforced: the images are cached for an hour by every
    proxy in front of them, GitHub's included, and drawing one calls no model)."""
    est_weekly_usd: float = Field(default=0.06, ge=0)
    """Spend reserved around the paid part of one edition: up to three web searches (a cent each), one structured call for the
    news and one read of the lead opening."""
    chat_streams_per_ip: int = Field(default=2, ge=0)
    """Concurrent chat streams one client may hold open (per replica)."""
    chat_streams_per_process: int = Field(default=24, ge=0)
    """Concurrent chat streams one replica serves."""
    upload_inflight_bytes: int = Field(default=64 * 1024 * 1024, ge=0)
    """Upload bytes one replica buffers at once across all requests; beyond it new uploads get 503."""
    upload_read_timeout_s: float = Field(default=30.0, gt=0)
    """Deadline for reading a whole request body (slow or stalled uploads are cut off)."""
    deep_health_min_interval_s: float = Field(default=5.0, ge=0)
    """/api/health/deep touches the database at most this often; in between it answers from the last result."""
    trusted_proxy_hops: int = Field(default=3, ge=0)
    """Proxies in front of the app; the client IP is that many entries from the right of X-Forwarded-For.

    Measured on FastAPI Cloud (Wave 0c canary, 2026-10-07): client, Cloudflare edge, then two internal hops.
    """
    client_ip_header: str | None = "cf-connecting-ip"
    """Read the client IP from this header when present (Cloudflare sets it in front of FastAPI Cloud and
    overwrites any client-sent value); otherwise fall back to X-Forwarded-For, then the socket peer."""
    unlimited_emails: str = Field(default="", repr=False, exclude=True)
    """Sign-in emails of accounts that have no rate limits and are never charged: ``TAYYARI_GUARD__UNLIMITED_EMAILS=
    a@x.com,b@y.com`` (a JSON list works too). Kept as the text it came in (a list or tuple given in code is joined), read
    through `unlimited_email_list` / `is_unlimited`. Matched exactly after trimming and lower-casing; both sign-in methods
    prove the person owns the address. Still applied to these accounts: the daily spend ceiling, the limits on sign-in
    links, process capacity and Sarvam's daily budget. Never logged or dumped (``repr=False``, ``exclude=True``)."""

    @field_validator("unlimited_emails", mode="before")
    @classmethod
    def _emails_as_text(cls, v: object) -> object:
        """Blank is empty; a list, tuple or set (code, tests) is joined into the comma-separated text."""
        if v is None:
            return ""
        if isinstance(v, list | tuple | set | frozenset):
            return ",".join(str(item) for item in v)
        return v

    @property
    def unlimited_email_list(self) -> tuple[str, ...]:
        """The addresses on the unlimited list, trimmed, lower-cased and without repeats (empty for none)."""
        return parse_email_list(self.unlimited_emails)

    @property
    def has_unlimited(self) -> bool:
        """Whether any address is on the unlimited list (the quick test before a request looks anyone up)."""
        return bool(self.unlimited_emails.strip()) and bool(self.unlimited_email_list)

    def is_unlimited(self, email: str | None) -> bool:
        """Whether ``email`` (the address a signed-in account proved it owns) is on the unlimited list."""
        if not email or not self.unlimited_emails:
            return False
        return email.strip().lower() in self.unlimited_email_list

    @field_validator("client_ip_header", mode="before")
    @classmethod
    def _blank_header_is_none(cls, v: object) -> object:
        """Blank keeps the default (like every other variable); "none" or "off" disables the header."""
        if isinstance(v, str):
            if not v.strip():
                raise PydanticUseDefault()
            if v.strip().lower() in {"none", "off", "false", "0"}:
                return None
        return v


class ConcurrencySettings(BaseModel):
    """Parallelism and timeouts: `TAYYARI_CONCURRENCY__*`."""

    reader_concurrency: int = Field(default=6, ge=1)
    reader_timeout_s: float = Field(default=90.0, gt=0)
    max_concurrent_analyses: int = Field(default=4, ge=1)
    """Analyses run at once per replica; more are told to retry."""


class RetentionSettings(BaseModel):
    """How long stored data is kept: `TAYYARI_RETENTION__*`."""

    profile_days: int = Field(default=60, ge=1)
    """Profiles (and their chats) not opened for this long are deleted; sample rows are kept."""
    payload_days: int = Field(default=30, ge=1)
    """Jev request/response payloads and states are purged after this long."""
    metadata_days: int = Field(default=180, ge=1)
    """Decision and call metadata (no text) is kept this long."""


def _origin(raw: str) -> str:
    """``raw`` as an origin without a trailing slash; a missing scheme means https."""
    raw = raw.strip()
    return (raw if "://" in raw else f"https://{raw}").rstrip("/")


def _default_public_url() -> str:
    """The app's public origin: ``FASTAPI_PUBLIC_URL`` on FastAPI Cloud, else the Vite dev server, which proxies
    ``/api`` to this process."""
    raw = os.environ.get("FASTAPI_PUBLIC_URL", "").strip()
    return _origin(raw) if raw else "http://localhost:5173"


class OwnProviders(Protocol):
    """What `MatchSettings.anonymous_allowed` reads of `auth.service.Providers` (core cannot import it)."""

    @property
    def google(self) -> bool: ...

    @property
    def magic(self) -> bool: ...


class AuthSettings(BaseModel):
    """Accounts and sign-in: `TAYYARI_AUTH__*`. The Google and Resend credentials are top-level secrets below."""

    session_days: int = Field(default=30, ge=1)
    """Lifetime of a sign-in; sliding (each use, at most hourly, pushes it out again)."""
    magic_link_minutes: int = Field(default=15, ge=1)
    """How long an emailed sign-in link works (it also works once)."""
    magic_per_email: int = Field(default=3, ge=0)
    magic_per_ip: int = Field(default=3, ge=0)
    """Sign-in links one email address, and one client, may ask for per 15 minutes (0 = not enforced)."""
    cookie_name: str = "tayyari_session"
    public_url: str = Field(default_factory=_default_public_url)
    """Where the app is reached from outside: the base of the emailed link and of Google's callback URL
    (``<public_url>/api/auth/google/callback``). Default: ``FASTAPI_PUBLIC_URL``, else ``http://localhost:5173``."""

    supabase_url: str | None = None
    """The Supabase project URL behind the Leafout site's sign-in (``https://<ref>.supabase.co``). Unset: Leafout sign-in is
    off. Blank counts as unset; a trailing slash is dropped."""
    supabase_publishable_key: str | None = None
    """The project's publishable (anon) key, sent as ``apikey`` on the call to Supabase's user endpoint. It is public (it
    ships in the site's bundle), so it is plain text and not a secret, but it is still a setting and never code."""
    supabase_cookie: str = "leafout-access"
    """The name of the cookie on ``.leafout.in`` that carries the site's access token. It must match the site."""
    supabase_verify: SupabaseVerify = "auto"
    """How a token's signature is checked: ``jwks`` (the project's public keys), ``user_endpoint`` (Supabase itself
    validates the token), or ``auto`` (``jwks`` when the project publishes an ES256 or RS256 key, else ``user_endpoint``)."""
    supabase_max_token_age_s: int = Field(default=3600, ge=60)
    """The oldest ``iat`` a token may have (Supabase's access-token lifetime). Refuses a token minted with a long expiry."""
    supabase_recheck_hours: int = Field(default=24, ge=1)
    """How long a session made from the site's cookie goes before it must see a valid site cookie again."""
    supabase_sign_in_url: str | None = None
    """The site's sign-in page (``https://leafout.in/signin``). Setting it is what lets the web app offer the link."""
    supabase_sign_out_url: str | None = None
    """The site's sign-out page (``https://leafout.in/signout``)."""
    own_sign_in: bool = False
    """Offer the app's own Google and emailed-link sign-in. Off by default: the site's sign-in is the app's sign-in."""
    redirect_other_hosts: bool = False
    """308 page requests (not ``/api/*``) on any host other than the public URL's to the same path on the public URL."""

    @field_validator("public_url", mode="before")
    @classmethod
    def _tidy_public_url(cls, v: object) -> object:
        """Blank keeps the default; a missing scheme means https; a trailing slash is dropped."""
        if isinstance(v, str):
            if not v.strip():
                raise PydanticUseDefault()
            return _origin(v)
        return v

    @field_validator(
        "supabase_url",
        "supabase_publishable_key",
        "supabase_sign_in_url",
        "supabase_sign_out_url",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """Blank is unset; a trailing slash is dropped from the project URL and the site's pages."""
        if isinstance(v, str):
            cleaned = v.strip()
            return cleaned.rstrip("/") if cleaned else None
        return v

    @field_validator("supabase_cookie", "supabase_verify", mode="before")
    @classmethod
    def _blank_keeps_the_default(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            raise PydanticUseDefault()
        return v.strip() if isinstance(v, str) else v

    @property
    def leafout_enabled(self) -> bool:
        """Sign in with the Leafout account works here: the project URL, its publishable key and the site's sign-in page are set."""
        return bool(self.supabase_url and self.supabase_publishable_key and self.supabase_sign_in_url)


class BillingSettings(BaseModel):
    """Prices and allowances: `TAYYARI_BILLING__*`. The payment providers' keys are top-level secrets below.

    Prices are integers in the currency's minor unit (paise, cents); 0 means "not set", and a product with no price
    is not offered: the pricing page shows it as coming soon and checkout answers 409. Two further things must
    hold for a product to be on sale in a currency: the provider for that currency (INR: Razorpay with its key pair,
    USD: Stripe with its secret key and webhook secret) has its secrets set, and the price is at least the provider's
    minimum (`billing.products.MIN_MINOR`: 100 paise, 50 cents). A price under it is logged at startup and not offered.
    """

    pack3_inr: int = Field(default=0, ge=0)
    """Three posting matches, no expiry, in paise (49900 = INR 499)."""
    pass30_inr: int = Field(default=0, ge=0)
    """Unlimited postings for 30 days from purchase, in paise."""
    pack3_usd: int = Field(default=0, ge=0)
    """Three posting matches, no expiry, in cents (900 = $9)."""
    pass30_usd: int = Field(default=0, ge=0)
    """Unlimited postings for 30 days from purchase, in cents."""
    free_matches_per_month: int = Field(default=1, ge=0)
    """Posting matches every account gets free each calendar month (UTC)."""

    @field_validator("*", mode="before")
    @classmethod
    def _blank_keeps_the_default(cls, v: object) -> object:
        """A blank value counts as unset (like every other variable), so a price line left empty means "not on sale"."""
        if isinstance(v, str) and not v.strip():
            raise PydanticUseDefault()
        return v


class MatchSettings(BaseModel):
    """Posting match: `TAYYARI_MATCH__*`. Which models read and write, and how long one model call may take."""

    model: str | None = None
    """The model that reads the posting and classifies (posting, facts, gates, coverage, relevance). Unset: the
    reader model (`TAYYARI_READER_MODEL`)."""
    writer_model: str | None = None
    """The model that phrases resume sentences and writes the cover note. Unset: the same as ``model``."""
    max_posting_chars: int = Field(default=20_000, ge=200, le=20_000)
    """The longest posting text accepted (pasted or fetched). The reader itself never takes more than 20,000
    characters, so a larger value would change nothing."""
    call_timeout_s: float = Field(default=90.0, gt=0)
    """The longest one classification call may take before its stage settles for the honest default (unknown,
    uncertain, the fact's own wording)."""
    allow_anonymous: AnonymousMatch = "auto"
    """Whether a visitor with no sign-in may match a posting. ``auto``: exactly when this server offers none of the
    app's OWN ways to sign in (Google, an emailed link); the Leafout account does not count, because seeing a gap is free. ``true``:
    always. ``false``: never (every `/api/match*` route answers 401 without a session). `anonymous_allowed` resolves it."""

    @field_validator("model", "writer_model", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("allow_anonymous", mode="before")
    @classmethod
    def _tidy_allow_anonymous(cls, v: object) -> object:
        """Blank keeps the default; case and padding do not matter; a real boolean means ``true`` / ``false``."""
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            if not v.strip():
                raise PydanticUseDefault()
            return v.strip().lower()
        return v

    def anonymous_allowed(self, providers: OwnProviders) -> bool:
        """The setting resolved for a server whose sign-in methods are ``providers`` (`auth.service.Providers`): ``auto`` is
        true exactly when neither of the app's OWN methods (Google, emailed link) is available. Sign-in with the Leafout
        account does not count: seeing a gap is free, and only saving, buying and the account itself need a sign-in."""
        if self.allow_anonymous == "auto":
            return not (providers.google or providers.magic)
        return self.allow_anonymous == "true"


class DiscoverSettings(BaseModel):
    """Discover, the job-discovery service: `TAYYARI_DISCOVER__*`. No secrets: it reads employers' own public job boards.

    The refresh runs in the background of the web app when the last good run is older than ``refresh_hours``, and as its
    own process (``python -m tayyari.cli discover-refresh`` once, ``discover-worker`` in a loop).
    """

    enabled: bool = True
    """False: the routes answer 503 ``discover_disabled`` and nothing is fetched."""
    refresh_hours: float = Field(default=6.0, gt=0)
    """How old the last good refresh may get before the next request starts another one in the background."""
    fetch_concurrency: int = Field(default=4, ge=1, le=32)
    """Employer boards fetched at once."""
    request_timeout_s: float = Field(default=15.0, gt=0)
    """The longest one board request may take."""
    max_listings_per_employer: int = Field(default=300, ge=1)
    """Listings kept per employer in India (the newest first); a bigger board is cut here."""
    registry_path: Path | None = None
    """The employer registry (JSON). Unset: the one shipped with the package (``discover/data/employers.json``)."""

    @field_validator("registry_path", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator(
        "enabled",
        "refresh_hours",
        "fetch_concurrency",
        "request_timeout_s",
        "max_listings_per_employer",
        mode="before",
    )
    @classmethod
    def _blank_keeps_the_default(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            raise PydanticUseDefault()
        return v


class JobSearchSettings(BaseModel):
    """Job search with AI: `TAYYARI_JOBSEARCH__*`. No secrets of its own: it uses the Anthropic key and reads employers'
    own public job boards, like Discover.

    A search sends ``scouts`` Claude Haiku calls that may each run ``searches_per_scout`` web searches restricted to
    employers' applicant tracking sites, reads at most ``max_boards`` boards they lead to, and has Haiku read at most
    ``max_judged`` postings, ``judge_batch`` to a call.
    """

    enabled: bool = True
    """False: the route answers 503 and nothing is searched."""
    model: str = "claude-haiku-5-5"
    """The model of the scouts and of the reads. Haiku 5.5 by decision (2026-10-16): the cheapest that runs web search."""
    scouts: int = Field(default=3, ge=1, le=5)
    searches_per_scout: int = Field(default=3, ge=1, le=5)
    max_boards: int = Field(default=30, ge=1, le=60)
    """Boards found on the web that one search reads (those whose results read most like the person's role first)."""
    fetch_concurrency: int = Field(default=8, ge=1, le=16)
    """Boards read at once."""
    max_judged: int = Field(default=12, ge=1, le=30)
    """Postings Haiku reads against the person's evidence."""
    judge_batch: int = Field(default=4, ge=1, le=8)
    """Postings per read call (the calls run at once)."""
    per_employer: int = Field(default=3, ge=1, le=10)
    """The most postings of one employer on the shortlist (a big board must not fill it)."""
    posting_chars: int = Field(default=6000, ge=1000, le=20_000)
    """Characters of a posting's text a read sees."""
    board_cache_minutes: float = Field(default=60.0, ge=0)
    """How long a board read for one search is reused by the next (in this process)."""
    request_timeout_s: float = Field(default=12.0, gt=0)
    """The longest one board request may take."""

    @field_validator(
        "enabled",
        "model",
        "scouts",
        "searches_per_scout",
        "max_boards",
        "fetch_concurrency",
        "max_judged",
        "judge_batch",
        "per_employer",
        "posting_chars",
        "board_cache_minutes",
        "request_timeout_s",
        mode="before",
    )
    @classmethod
    def _blank_keeps_the_default(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            raise PydanticUseDefault()
        return v


class InterviewSettings(BaseModel):
    """Practice the interview: `TAYYARI_INTERVIEW__*`. No secrets of its own: it uses the Anthropic key.

    One interview makes one Haiku call to phrase its questions, one small call per answer to judge it, one for the
    debrief (and one for a work sample's checks), plus the work sample's assistant when the person uses it. Speech is
    turned into text by the browser, so it costs nothing here.
    """

    enabled: bool = True
    """False: the routes answer 503 and nothing is asked."""
    model: str = "claude-haiku-5-5"
    """The model of every interview call. Haiku 5.5 by decision (2026-10-16, POC cost mode)."""
    max_turns: int = Field(default=60, ge=10, le=200)
    """Answers one session takes (buttons included); past it the interview ends."""
    assistant_messages: int = Field(default=12, ge=0, le=40)
    """Messages the work sample's assistant answers in one session (0 = no assistant)."""
    posting_chars: int = Field(default=6000, ge=1000, le=20_000)
    """Characters of a posting's text the phrasing call sees."""
    retention_days: int = Field(default=30, ge=1, le=365)
    """Days a session (its transcript and debrief) is kept."""
    history_limit: int = Field(default=50, ge=1, le=200)
    """Sessions the practice history reads."""

    @field_validator(
        "enabled",
        "model",
        "max_turns",
        "assistant_messages",
        "posting_chars",
        "retention_days",
        "history_limit",
        mode="before",
    )
    @classmethod
    def _blank_keeps_the_default(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            raise PydanticUseDefault()
        return v


class SpeechSettings(BaseModel):
    """Speech for practice interviews through Sarvam AI: `TAYYARI_SPEECH__*`. The key is `SARVAM_API_KEY`.

    Speech-to-text turns each piece of a spoken answer (up to ``max_segment_seconds``, cut at a pause by the browser) into
    text with ``stt_model``; text-to-speech reads the interviewer's lines with ``tts_model`` and ``tts_speaker``. Sarvam
    bills per second of audio and per character of text, so both are metered here against daily and per-session
    budgets; when one runs out, or the key is unset, the browser's own speech takes over. Tayyari keeps no audio.
    """

    enabled: bool = True
    """False: speech status says unavailable and the browser's own speech is used."""
    stt_model: str = "saaras:v4"
    stt_language: str = "en-IN"
    tts_model: str = "bulbul:v4-flash"
    tts_speaker: str = "simran_en_customer"
    tts_language: str = "en-IN"
    tts_codec: str = "mp3"
    tts_sample_rate: int = Field(default=22050, ge=8000, le=48000)
    max_segment_seconds: int = Field(default=30, ge=5, le=30)
    """The longest piece of an answer sent in one request (Sarvam's quick endpoint is for under 30 seconds)."""
    max_segment_bytes: int = Field(default=1_500_000, ge=10_000, le=5_000_000)
    stt_daily_seconds: int = Field(default=1800, ge=0)
    """Seconds of answers transcribed per day across the service (0 = no Sarvam speech-to-text)."""
    tts_daily_chars: int = Field(default=6000, ge=0)
    """Characters of questions spoken per day across the service (0 = no Sarvam voice)."""
    stt_session_seconds: int = Field(default=600, ge=0)
    tts_session_chars: int = Field(default=3000, ge=0)
    request_timeout_s: float = Field(default=20.0, gt=0)
    tts_cache_items: int = Field(default=256, ge=0, le=5000)
    """Spoken lines kept in memory, so a repeated line ("Say it again", a template) costs nothing."""
    tts_prefetch: bool = True
    """Make the interviewer's next lines ready before the browser asks for them (needs the cache: ``tts_cache_items`` > 0)."""
    tts_prefetch_followups: bool = True
    """Also make the lines only some answers lead to ready: the current question's follow-up and easier question. Off, only the
    line being said and the questions still to come are made ahead (fewer credits spent on lines nobody hears)."""
    tts_prefetch_concurrency: int = Field(default=2, ge=1, le=8)
    """Lines made ahead at the same time across the whole process (a line a person is waiting for never queues behind them)."""

    @field_validator(
        "enabled",
        "stt_model",
        "stt_language",
        "tts_model",
        "tts_speaker",
        "tts_language",
        "tts_codec",
        "tts_sample_rate",
        "max_segment_seconds",
        "max_segment_bytes",
        "stt_daily_seconds",
        "tts_daily_chars",
        "stt_session_seconds",
        "tts_session_chars",
        "request_timeout_s",
        "tts_cache_items",
        "tts_prefetch",
        "tts_prefetch_followups",
        "tts_prefetch_concurrency",
        mode="before",
    )
    @classmethod
    def _blank_keeps_the_default(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            raise PydanticUseDefault()
        return v


_SECRET_FIELDS = (
    "anthropic_api_key",
    "sarvam_api_key",
    "jev_api_key",
    "github_token",
    "logfire_token",
    "google_client_id",
    "google_client_secret",
    "resend_api_key",
    "razorpay_key_id",
    "razorpay_key_secret",
    "razorpay_webhook_secret",
    "stripe_secret_key",
    "stripe_webhook_secret",
)


class CardSettings(BaseModel):
    """The radar card (R1): ``TAYYARI_CARD__*``. No secrets."""

    report_email: str = "aman@leafout.in"
    """Where "Report this card" on a card's public page writes to (a ``mailto:`` link)."""
    png_cache_size: int = Field(default=256, ge=0)
    """LinkedIn pictures kept drawn in memory, by link and version (0 = draw every time)."""


class HandoffSettings(BaseModel):
    """The hand-off from the marketing site (a resume, links and a goal posted to ``/api/handoff``, taken once by the app):
    `TAYYARI_HANDOFF__*`. No secrets."""

    origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["https://leafout.in", "https://www.leafout.in"]
    )
    """The origins a hand-off may come from, compared with the request's ``Origin`` header (or its ``Referer`` when there is
    none). ``TAYYARI_HANDOFF__ORIGINS`` takes a JSON list or comma-separated origins (``https://leafout.in,https://www.leafout.in``).
    An empty list refuses every hand-off."""
    ttl_seconds: int = Field(default=900, ge=30, le=86_400)
    """How long a stored hand-off waits to be taken (it is deleted when read, and when it has expired, on the next hand-off)."""
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    """The largest resume file accepted (the analyze upload's limit by default)."""
    allow_localhost: bool | None = None
    """Also accept ``http://localhost:<port>`` origins (the site running on a developer's machine). None: only when the app
    itself runs locally (``env`` is ``local``); never in production unless set to true on purpose."""

    @field_validator("origins", mode="before")
    @classmethod
    def _origins_as_list(cls, v: object) -> object:
        """Comma- or whitespace-separated text (or a JSON list) becomes a list; each origin is trimmed and lower-cased with no
        trailing slash. Blank entries are dropped."""
        if v is None:
            return []
        if isinstance(v, str):
            text = v.strip()
            if text.startswith("["):
                try:
                    loaded = json.loads(text)
                except ValueError:
                    loaded = None
                v = loaded if isinstance(loaded, list) else text.strip("[]").replace('"', " ")
            if isinstance(v, str):
                v = re.split(r"[,;\s]+", v)
        if isinstance(v, list | tuple | set | frozenset):
            cleaned = (str(item).strip().strip("\"'").strip().rstrip("/").lower() for item in v)
            return list(dict.fromkeys(item for item in cleaned if item))
        return v

    def localhost_allowed(self, env: Env) -> bool:
        """Whether ``http://localhost:*`` origins are accepted: the explicit setting, else only when running locally."""
        return self.allow_localhost if self.allow_localhost is not None else env == "local"


BLOCKED_NEWS_DOMAINS: Final = frozenset(
    {"arstechnica.com", "economictimes.indiatimes.com", "ft.com", "livemint.com", "reuters.com", "thehindu.com",
     "theverge.com", "wired.com", "zdnet.com"}
)  # fmt: skip
"""News sites Anthropic's search crawler cannot read (the API's own answer, 2026-10-21): naming one in `allowed_domains`
makes the whole web search fail, so the weekly's news domains must leave them out."""


class WeeklySettings(BaseModel):
    """The Leafout Weekly, a personal newspaper built every week from the reader's own graph: `TAYYARI_WEEKLY__*`. No
    secrets of its own: it uses the Anthropic key, and the Resend key (``RESEND_API_KEY``) the sign-in emails use.

    A background loop wakes every 15 minutes; from 06:00 India time on a subscriber's day it builds that week's edition,
    stores it and emails it. The paid part of an edition (the news, and one read of the lead opening) is a few cents and
    is skipped, never failed, when the spend ceiling is reached or ``max_paid_editions_per_week`` is used up.
    """

    enabled: bool = True
    """False: no background loop (the routes still work, and `weekly preview` builds an edition by hand)."""
    sender: str | None = None
    """``The Leafout Weekly <weekly@leafout.in>``. Unset: ``RESEND_FROM``, then Resend's shared test sender. Not a secret."""
    app_url: str = "https://app.leafout.in"
    """Where the links in an email point (``<app_url>/weekly?e=<id>``, the unsubscribe address, Manage)."""
    unsubscribe_mailto: str | None = None
    """A mailbox for the ``mailto:`` half of ``List-Unsubscribe``. Unset: the header carries the https address only."""
    model: str = "claude-haiku-5-5"
    """The model of the news search, the news slots and the read of the lead opening."""
    news: bool = True
    """False: no news section is searched for, whatever the reader chose."""
    news_searches: int = Field(default=3, ge=1, le=3)
    news_items: int = Field(default=3, ge=1, le=5)
    news_domains: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "techcrunch.com", "technologyreview.com", "bloomberg.com", "cnbc.com", "hbr.org", "infoq.com",
            "thenewstack.io", "venturebeat.com", "business-standard.com", "yourstory.com", "inc42.com",
            "entrackr.com", "medianama.com",
        ]
    )  # fmt: skip
    """The sites a news search may return (reputable tech and business news). ``TAYYARI_WEEKLY__NEWS_DOMAINS`` takes a JSON
    list or comma-separated domains. Every one must be readable by Anthropic's search crawler: a single blocked domain makes
    the API refuse the whole search (checked 2026-10-21; arstechnica, economictimes, ft, livemint, reuters, thehindu, theverge,
    wired and zdnet block it). `BLOCKED_NEWS_DOMAINS` lists them and a test keeps them out."""
    max_paid_editions_per_week: int = Field(default=500, ge=0)
    """Editions per week, across all readers, that may spend on a model (0 = none: every edition is built without news and
    without the read). A soft cap: two replicas can overshoot it by a few."""
    batch_size: int = Field(default=10, ge=1, le=50)
    """Emails sent per batch, with `batch_pause_s` between batches."""
    batch_pause_s: float = Field(default=1.0, ge=0)
    max_tries: int = Field(default=3, ge=1, le=10)
    """Times one reader's edition is built or emailed in one week before the loop gives up on it."""
    concurrency: int = Field(default=4, ge=1, le=16)
    """Editions composed at once in one pass of the loop."""
    claim_hold_s: float = Field(default=600.0, gt=0)
    """How long a reader's week stays claimed by the replica working on it (a failed try is retried by a later tick)."""
    tick_seconds: float = Field(default=900.0, gt=0)
    """How often the loop wakes (15 minutes)."""
    first_tick_delay_s: float = Field(default=30.0, ge=0)

    @field_validator("news_domains", mode="before")
    @classmethod
    def _domains_as_list(cls, v: object) -> object:
        if v is None:
            return []
        if isinstance(v, str):
            text = v.strip()
            if text.startswith("["):
                try:
                    loaded = json.loads(text)
                except ValueError:
                    loaded = None
                v = loaded if isinstance(loaded, list) else text.strip("[]").replace('"', " ")
            if isinstance(v, str):
                v = re.split(r"[,;\s]+", v)
        if isinstance(v, list | tuple | set | frozenset):
            cleaned = (str(item).strip().strip("\"'").strip().lower() for item in v)
            return list(dict.fromkeys(item for item in cleaned if item))
        return v

    @field_validator("sender", "unsubscribe_mailto", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v


class Settings(BaseSettings):
    """All configuration, typed. See the module docstring for where values come from."""

    model_config = SettingsConfigDict(
        env_prefix="TAYYARI_",
        env_nested_delimiter="__",
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    env: Env = Field(default_factory=detect_env)
    """``local``, ``prod`` (detected on FastAPI Cloud) or ``test``. `TAYYARI_ENV` overrides the detection."""
    log_level: LogLevel = "INFO"

    # credentials and endpoints: third-party names, no TAYYARI_ prefix
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )
    jev_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("JEV_API_KEY", "TYPESAFE_API_KEY")
    )
    github_token: SecretStr | None = Field(default=None, validation_alias=AliasChoices("GITHUB_TOKEN"))
    sarvam_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("SARVAM_API_KEY"))
    """Sarvam AI (speech-to-text and the interviewer's voice in practice interviews). Unset: the browser's own speech."""
    logfire_token: SecretStr | None = Field(default=None, validation_alias=AliasChoices("LOGFIRE_TOKEN"))
    google_client_id: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GOOGLE_CLIENT_ID")
    )
    google_client_secret: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GOOGLE_CLIENT_SECRET")
    )
    resend_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("RESEND_API_KEY"))
    resend_from: str | None = Field(default=None, validation_alias=AliasChoices("RESEND_FROM"))
    """The sender of sign-in emails, e.g. ``Tayyari <login@example.com>``. Not a secret."""
    razorpay_key_id: SecretStr | None = Field(default=None, validation_alias=AliasChoices("RAZORPAY_KEY_ID"))
    razorpay_key_secret: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("RAZORPAY_KEY_SECRET")
    )
    razorpay_webhook_secret: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("RAZORPAY_WEBHOOK_SECRET")
    )
    stripe_secret_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("STRIPE_SECRET_KEY")
    )
    stripe_webhook_secret: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("STRIPE_WEBHOOK_SECRET")
    )
    database_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL"), repr=False, exclude=True
    )
    """Carries the password, so it is kept out of ``repr(settings)``; log `db.safe_url` instead."""

    # models (the legacy TAYYARI_READER_MODEL / _SYNTH_MODEL / _CHAT_MODEL names keep working)
    reader_model: str = "claude-haiku-4-5"
    synth_model: str = "claude-sonnet-5-5"
    chat_model: str = "claude-sonnet-5-5"
    reader_thinking: bool = False
    """Extended thinking for the Haiku readers; they extract rather than reason, so it is off."""
    synth_thinking: bool = True
    """Adaptive thinking for the synthesis. False sends ``{"type": "disabled"}`` to models that allow it (Haiku 5.5);
    a model that cannot switch thinking off (Sonnet 5.5) keeps it. Readers, portfolio calls and decision fallbacks
    never think on such models: they extract or fill short slots, and thinking only adds latency there."""
    chat_thinking: bool = True
    """Adaptive thinking for the career chat (same rule as `synth_thinking`)."""
    synth_effort: Effort = "medium"
    chat_effort: Effort = "medium"
    reader_max_tokens: int = Field(default=8000, ge=256)
    synth_max_tokens: int = Field(default=32_000, ge=256)
    """Includes Sonnet 5.5's adaptive thinking tokens, which count against max_tokens."""
    chat_max_tokens: int = Field(default=8000, ge=256)

    api_port: int = Field(default=8787, ge=1, le=65535)
    """The dev server's port (the Vite proxy targets 8787)."""

    jev: JevSettings = Field(default_factory=JevSettings)
    guard: GuardSettings = Field(default_factory=GuardSettings)
    concurrency: ConcurrencySettings = Field(default_factory=ConcurrencySettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    billing: BillingSettings = Field(default_factory=BillingSettings)
    match: MatchSettings = Field(default_factory=MatchSettings)
    discover: DiscoverSettings = Field(default_factory=DiscoverSettings)
    jobsearch: JobSearchSettings = Field(default_factory=JobSearchSettings)
    interview: InterviewSettings = Field(default_factory=InterviewSettings)
    speech: SpeechSettings = Field(default_factory=SpeechSettings)
    handoff: HandoffSettings = Field(default_factory=HandoffSettings)
    weekly: WeeklySettings = Field(default_factory=WeeklySettings)
    card: CardSettings = Field(default_factory=CardSettings)

    synth_hints: bool = False
    """Feed the first-read results to the synthesizer as hints (off: they are stored for comparison only)."""
    synth_cache: SynthCache = "system"
    chat_cache_ttl: CacheTtl = "5m"
    pacing_scale: float = Field(default=1.0, ge=0)
    """Multiplies the delays that space out UI events; 0 in tests and fast demos."""
    decision_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Emergency overrides of ``decisions/defaults.toml``, e.g. ``{"evidence.support": {"mode": "off"}}``."""

    data_dir: Path = Field(default_factory=_default_data_dir)
    """Where local files (the SQLite database) live. Created lazily by whoever writes there."""

    @field_validator(*_SECRET_FIELDS, "database_url", "resend_from", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        """Empty and whitespace-only values count as unset; surrounding whitespace is dropped."""
        if isinstance(v, SecretStr):
            v = v.get_secret_value()
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @property
    def match_model(self) -> str:
        """The model that reads and classifies for a posting match (``match.model``, else the reader model)."""
        return self.match.model or self.reader_model

    @property
    def match_writer_model(self) -> str:
        """The model that phrases and writes for a posting match (``match.writer_model``, else `match_model`)."""
        return self.match.writer_model or self.match_model

    @property
    def models(self) -> ModelNames:
        """The model ids as one small frozen object (the `models` field of /api/health and of every Profile)."""
        return ModelNames(reader=self.reader_model, synth=self.synth_model, chat=self.chat_model)

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def google_enabled(self) -> bool:
        """True when both halves of the Google OAuth client are set (the sign-in button shows only then)."""
        return self.google_client_id is not None and self.google_client_secret is not None

    @property
    def razorpay_enabled(self) -> bool:
        """True when Razorpay can take payments: both API keys are set. The Checkout.js hand-off is verified with the
        key secret, so the webhook secret is optional (see `razorpay_webhooks_enabled`)."""
        return self.razorpay_key_id is not None and self.razorpay_key_secret is not None

    @property
    def razorpay_webhooks_enabled(self) -> bool:
        """True when Razorpay's webhooks are accepted too: it is enabled and the webhook secret is set. The webhook
        covers a customer whose browser never came back after paying; without it only the hand-off settles."""
        return self.razorpay_enabled and self.razorpay_webhook_secret is not None

    @property
    def stripe_enabled(self) -> bool:
        """True when Stripe can take and reconcile payments: the secret key and the webhook secret are set."""
        return self.stripe_secret_key is not None and self.stripe_webhook_secret is not None

    @cached_property
    def db(self) -> DbConfig:
        """The database configuration derived from ``database_url`` (SQLite under ``data_dir`` when unset)."""
        return normalise_database_url(self.database_url, data_dir=self.data_dir, env=self.env)

    def summary(self) -> dict[str, Any]:
        """Non-secret facts for the startup log line: which keys are present, never their values."""
        return {
            "env": self.env,
            "models": self.models.model_dump(),
            "anthropic_key": self.anthropic_api_key is not None,
            "sarvam_key": self.sarvam_api_key is not None,
            "jev_key": self.jev_api_key is not None,
            "jev_enabled": self.jev.enabled and self.jev_api_key is not None,
            "jev_model": self.jev.model,
            "github_token": self.github_token is not None,
            "google_oauth": self.google_enabled,
            "leafout_sign_in": self.auth.leafout_enabled,
            "resend_key": self.resend_api_key is not None,
            "razorpay": self.razorpay_enabled,
            "razorpay_webhooks": self.razorpay_webhooks_enabled,
            "stripe": self.stripe_enabled,
            "database": self.db.safe_url,
            "storage": "ephemeral" if self.db.ephemeral else self.db.kind,
            "daily_ceiling_usd": self.guard.daily_ceiling_usd if self.guard.enabled else None,
            "unlimited_accounts": len(self.guard.unlimited_email_list),
            "decision_overrides": sorted(self.decision_overrides),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, built on first use. Tests call ``get_settings.cache_clear()``."""
    return Settings()


__all__ = [
    "AuthSettings",
    "BillingSettings",
    "CacheTtl",
    "ConcurrencySettings",
    "DiscoverSettings",
    "Effort",
    "Env",
    "GuardSettings",
    "JevSettings",
    "LogLevel",
    "MatchSettings",
    "ModelNames",
    "OwnProviders",
    "RetentionSettings",
    "Settings",
    "SynthCache",
    "WeeklySettings",
    "detect_env",
    "get_settings",
    "parse_email_list",
]
