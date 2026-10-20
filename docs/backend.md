# Backend architecture and deployment

This document describes the Tayyari Python backend: a modular FastAPI application serving the React frontend. The backend is the single source of truth for AI-driven career intelligence, decision logic, persistence, and cost protection.

## Module overview

The package is importable as `backend/tayyari/`, laid out in layers with import restrictions enforced by test. Each module has one purpose.

| Module | Exports | Purpose |
|--------|---------|---------|
| `core` | Settings, Logger, errors | Configuration (env + `.env` cascade), structlog + PII redaction, custom error types, limits, UUIDs, clock, text utils, DB URL normalisation |
| `contract` | Pydantic models | Types mirroring `shared/types.ts`: AnalysisEvent, ChatEvent, Profile, skill/domain/role objects |
| `llm` | ClaudeClient, JevClient | Anthropic API client (pricing, caching, errors, usage), Jev HTTP adapter, model capability table |
| `decisions` | DecisionRunner, DecisionService | Jev decision specs, packs, runner, recorder (with mode: off/shadow/primary); defaults in version-controlled TOML |
| `analysis` | AnalysisService | Reader execution, synthesis, normalisation against golden baselines, Sonnet calls |
| `chat` | ChatService | Chat session store, tool mapping, WebSearch/WebFetch, streaming |
| `guard` | RateLimiter, SpendGuard | Per-IP fixed-window counters, daily spend ceiling, client IP hashing |
| `storage` | Repos (Profiles, Sessions, LlmCalls, Decisions, Limits) | SQLAlchemy 2 async, Alembic migrations, Postgres (Neon) + SQLite |
| `prompts` | build_reader_system, build_synth_system, build_chat_system | Prompt templates (limits interpolated from `core.limits`) |
| `ingest` | fetch and extract functions | PDF/DOCX parsing, GitHub API, HTML scraping, link classification, SSRF guard |
| `api` | create_app, routes | FastAPI app factory, request dependencies, NDJSON streaming, file uploads, error handling |
| `cli` | commands | Entry point: `dev`, `serve`, `migrate`, `smoke`, `make-sample`, `ingest-check`, `deploy`, `export`, `replay`, `eval` |
| `sample` | sample_profile, SampleLoader | Bundled Riya Kapoor persona; fixtures for testing and offline mode |
| `portfolio` | PortfolioService | Resume → Portfolio: line-cited facts, closed kit, sentence grounding, the public page |
| `auth` | AuthService, GoogleOAuth, MagicLinks, SessionService, EmailSender | Google sign-in (PKCE, ID-token checks), emailed sign-in links, server-side sessions, Resend email |
| `billing` | BillingService, EntitlementService, RazorpayGateway, StripeGateway | Products and prices, the consume order, orders, webhooks for INR (Razorpay) and USD (Stripe) |
| `match` | MatchService and its stages | Posting match: reading a posting, facts, gates, coverage, radar, questions, resume, cover note, export and lint |
| `discover` | DiscoverService, the refresher, the registry, the quick fit | The board store behind Search with AI: Tayyari's employers' own boards read into listings, plus the parsers, places, vocabulary and quick fit the search reuses; a separate service (own tables, settings and worker) |
| `jobsearch` | JobSearchService, the brief, the harvest, the board reader, the narrowing, the checks | Search with AI: Haiku 5.5 scouts find employers' boards on the web, code reads them and narrows, Haiku reads the shortlist, code checks every quote; reuses `discover`'s pure parts |
| `interview` | InterviewService, the plan, the router, the judge, the debrief, the work-sample templates | Practice the interview: plans in code from the person's evidence, a role or a template; Haiku 5.5 phrases, judges and writes the debrief; code checks every quote, name and number and decides every verdict |
| `speech` | SpeechService, SarvamEngine, the meter | Speech for practice interviews through Sarvam AI's SDK: speech-to-text of answer pieces and the interviewer's voice, metered against a soft daily and per-session budget; self-contained like `discover` |
| `prepare` | PrepareService, assemble, verify, github | Prepare ("How do I prepare for this role?") as typed blocks: the plan's numbers and evidence from the graph in code, two calls of the chat model at once (where you stand, the moves) checked line by line, a stored plan with the person's state, the repo ranking from the stored GitHub digest, a headline rewrite |

**Layering rule** (enforced by test): `core` < `contract` < `llm` < `decisions` < `analysis`/`chat`/`portfolio`/`match` < `api`. `match` sits with `portfolio`: it reuses the portfolio's line-cited fact extractor and sentence anchors rather than forking them. `auth`, `billing` and `discover` depend only on `core`, `contract` and `storage.ports`. `jobsearch` sits with the model-calling services and may also import `discover` (never the other way round). `interview` sits with them too and may import `jobsearch`, `discover` and `portfolio` (nothing but `api` and the container imports it). `prepare` sits with them and reuses the posting match's schema helpers (`match.schemas.schema_of`, `rows_of`), nothing else of `match`. `speech` is self-contained like `discover` (`core`, `contract`, `storage.ports` and the `sarvamai` SDK). `storage` depends only on `core` and `contract`. `guard` depends on `storage.ports`. Services depend on Protocols, so every unit can be tested with fakes.

## Request flows

### Analysis (`POST /api/analyze`)

1. **Ingest** (deterministic): PDF → text, GitHub repos → README + tests, links → HTML, all with SSRF guard, rate limiting, and private-IP block.
2. **Jev first read** (parallel with readers): resume → career_family, stage, depth, nouls (leads, proof, outcomes, AI, progression). ~2.5 s, live signals on screen (if confidence ≥ 0.6).
3. **Haiku readers** (6 concurrent): each source → reader output (skills, domains, role hints, facts) in structured JSON. ~5–20 s per source depending on size and cache hits.
4. **Synthesis** (Sonnet): all reader outputs + resume → skill graph (person, mastery, 4–7 domains, skills with evidence, next roles, have/gap requirements). With prompt caching (profile block shared across threads). ~10–30 s.
5. **Evidence checks** (Jev + code, post-synth): each skill's claims → anchor-token check (names, numbers, identifiers in raw text and reader output) then Jev support/contradicts/says_nothing. Claims dropped only when both signals agree. ~4 s total, fail open.
6. **Storage**: Profile saved to DB (Postgres on cloud, SQLite locally), with context (sources + readers).
7. **Spend tracking**: every LLM call and Jev decision recorded with cost, latency, and request ID. Daily total cached.

**Cost per analysis**: ~$0.30–$0.60 (readers $0.10–$0.40, synth $0.15–$0.20, evidence $0.01–$0.10 with Jev, cache reads save ~30–50%).

### Chat (`POST /api/chat`)

1. **Session resolve**: load profile and context. Unknown `sessionId` → new session + reset note.
2. **Jev web-search gate**: message + origin → needs_outside_info (noul). Tools withheld only if `p ≤ 0.15` and confidence ≥ 0.7.
3. **Cached system blocks**: tools, static coach rules, profile block (shared, cache breakpoint 1), thread origin, history (automatic tail breakpoint).
4. **Stream through Sonnet**: WebSearch and WebFetch tools available (or withheld by gate). Map tool names `web_search` → `WebSearch`.
5. **Persist**: append user message, assistant message, tools used, and `llm_call_id` in one transaction. Optimistic on `(session_id, seq)` with retry.
6. **Emit**: `done{sessionId}` at end. Truncation → append note. Refusal → error event with friendly message.

**Cost per turn**: ~$0.02–$0.10 (first turn ~$0.05 with cache write, later turns ~$0.002–$0.005 with cache read).

### Portfolio (`POST /api/portfolio`, Resume → Portfolio)

Turns a profile into a portfolio page from a closed kit (`tayyari/portfolio/`, contract `shared/portfolio.ts` ⇄
`contract/portfolio.py`). Nothing a model writes is markup; every visible fact traces to a source line.

1. **Facts** (Haiku, one call per source, concurrent): the sources are shown with numbered lines; the model cites
   line numbers and types only short fields (title, employer, dates). Code copies the cited lines verbatim and keeps a
   fact only when its title/employer are in the cited window. Lines with contact details, addresses or a date of birth
   are refused. Cached by `facts_hash` (source texts + `FACTS_VERSION`); partial extractions are not cached.
2. **Shape** (Jev `first_read`, live): career stage, family, public-proof flags feed the composer (heuristics when off).
3. **Design** (code, `compose.py`): scores every valid composition × graph variant × style from features, breaks ties
   with `person_seed(profile_id)`, stores the top 3 distinct looks with reasons. A spec can never be invalid
   (`spec_problems`). A regenerate keeps the owner's visibility, privacy and hidden domains.
4. **Draft** event: the page with verbatim fallback wording (`fallback.py`), so it paints before the copy.
5. **Copy** (Sonnet, up to 3 concurrent calls: fixed slots, `exp:*`, `proj:*`), then **grounding** (`ground.py`):
   per-sentence anchors (numbers with units, names, dates, multipliers) must be in the quotes of the facts the slot
   cites; seniority verbs, puffery, pronouns, grades and truncated quotes are refused; then Jev `check_copy` judges each
   sentence (supports / contradicts / says nothing; Claude fallback). Failed slots get one re-ask, then fallback wording.
6. **Done** event + save (`portfolios` table, no FK to profiles; `generated` slots kept for "reset").

Other routes: `GET/PATCH/DELETE /api/portfolio/{profileId}` (edits validated against the kit; user text is plain,
`origin: "user"`), `POST …/rewrite` (one slot), `GET …/public-preview` and `GET /api/p/{publicId}` (published only)
return `PublicPortfolio`: visible + public blocks only, no profile id, no skill evidence, no skills under 45, no
email/phone/grades unless opted in, PII scrubbed. `/p/*` pages send `X-Robots-Tag: noindex`.

**Cost per portfolio**: ~$0.03–0.08 (live eval over 13 fixtures: $0.54 total, 9–21 s each, 0 ungrounded slots).
Check it with `uv run python -m tayyari.cli portfolio-eval [--only riya] [--write] [--details]` (needs the API key;
`--write` refreshes `tests/data/portfolio/rendered/*.portfolio.json`, which the dev gallery `/dev/portfolios` and
`npm run check:portfolio` read).

### Sign-in (`/api/auth/*`, `/api/me`)

Analysis stays open to anonymous visitors, whose profiles are kept for as long as the retention window allows (see Retention). Keeping a profile with an account and purchases need a sign-in; so does the posting match, unless anonymous matching is on (see Posting match). Both sign-in paths end in `AuthService`: find or create the account, start a session, and hand back the cookie token and the path to send the browser to.

1. **Google.** `GET /api/auth/google/start?next=/` puts a random state, a PKCE verifier and a nonce into a signed cookie (`tayyari_oauth`, valid ten minutes, scoped to the callback path) and redirects to Google with S256 and the nonce. `GET /api/auth/google/callback` checks the state against the cookie, exchanges the code and verifies the ID token (signature against Google's keys, issuer, audience, expiry, nonce). It finds the account by Google subject, then by email (attaching the Google identity to an account made from a link), and creates one if neither exists. `next` must be a path on this site. A declined or failed sign-in returns to `/?auth=<code>` (`google_denied`, `google_failed`, `google_unavailable`), which the sign-in sheet explains.
2. **Emailed link.** `POST /api/auth/magic` `{"email"}` always answers 202, so nothing reveals whether an address has an account. A link carries a random 32-byte token; only its sha256 is stored (`magic_links`). It works once (the spend is one conditional update) and for `TAYYARI_AUTH__MAGIC_LINK_MINUTES` (15). `TAYYARI_AUTH__MAGIC_PER_EMAIL` and `TAYYARI_AUTH__MAGIC_PER_IP` (3 each) limit requests per 15 minutes, counted in `rate_limit_hits`; the email is counted by HMAC, never as a plain hash. The link is sent through Resend's HTTP API (`RESEND_API_KEY`, `RESEND_FROM`); a send failure is logged and answered the same. A local server with no Resend key sends nothing: it logs the link and returns it as `devLink` in the 202 body. That shortcut needs `TAYYARI_ENV=local` and no key, so a server with a key never hands a link to whoever asked. `GET /api/auth/magic/verify?token=` spends the link, signs in and redirects to `/`; an unknown, used or expired link redirects to `/?auth=link_invalid`.

**Session.** The cookie `tayyari_session` (`TAYYARI_AUTH__COOKIE_NAME`) holds 32 random bytes; the table keeps only their sha256 (`auth_sessions.id`). It is `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` in production or when the public URL is https, and lasts `TAYYARI_AUTH__SESSION_DAYS` (30). The lifetime slides: a use more than an hour after the last refresh moves `last_seen_at` and the expiry forward. `GET /api/me` re-issues the cookie when a refresh has just run. An expired session is deleted when it is next seen. `POST /api/auth/logout` deletes the session and clears the cookie (204). The auth routes are shed by the in-memory bucket before they read anything; `CrossSiteGuard` covers the state-changing ones.

**Routes.**
- `GET /api/me` → `MeResponse`: `user` (absent for an anonymous visitor), `profiles` (the account's profiles: id, createdAt, goal, personName), `entitlements` (`credits`, `passUntil`, `freeMatchAvailable`) `providers` (`google`, `magic`: which sign-in methods work on this server) and `anonymousMatch` (a boolean: may a visitor with no sign-in match a posting here, see Posting match). Anonymous visitors get the same shape without a user.
- `POST /api/me/claim` `{profileId}`: attaches an unowned profile, its unowned portfolio, and the posting matches and answers made on it without an account (anonymous matching) to the account. A profile id is 128 random bits and is the only protection an unclaimed profile has, so the claim itself follows the capability model: whoever presents the id of an unclaimed profile gets it. From then on the id is no longer enough (see below). A sample run or an unknown id is 404; a profile that belongs to another account is 409 `profile_taken`; claiming your own profile again changes nothing.
- `PATCH /api/me` `{currency?, name?}`: currency is `INR` or `USD`.
- `DELETE /api/me`: deletes the account with its profiles (and their contexts and chats), matches, resumes and cover notes, what the person said in their own words, profile facts, portfolios and sessions, then clears the cookie (204). Orders are kept (see Storage).

**Who may open a profile.** One rule, `api.deps.require_profile_access`, runs in every route that takes a profile id: `GET /api/profile/{pid}`, `POST /api/chat`, and `POST /api/portfolio` with `GET`, `PATCH`, `DELETE`, `POST …/rewrite` and `GET …/public-preview` of `/api/portfolio/{profileId}` (the match routes do the same: `get_owned_for_chat` for an account, `get_open_for_chat` for a visitor with no sign-in, see Posting match). A profile nobody has claimed (`profiles.user_id` NULL) and a sample run stay open to whoever holds the id, as before. A claimed profile is served only to the session of the account that claimed it; anyone else, signed in or not, gets the 404 and the sentence the route gives an unknown id (`Profile not found`, `Portfolio not found`, the chat's `Profile not found — run the analysis again`), never a 403. The check runs right after the id shape, before the body is read and before any rate-limit counter, model call or spend reservation, so a refused request costs nothing and changes nothing; the session is looked up only for a claimed profile. Chat sessions are keyed by profile, so a conversation started before the claim is the owner's afterwards and no one else can resume it. The public page `GET /api/p/{publicId}` is separate: it answers once the owner has published it. A portfolio written for a claimed profile carries the owner's id in `portfolios.user_id` (`PortfolioService` reads it from the profile when it saves a build); a page that existed before the claim gets it from the claim.

**Leafout sign-in (L1).** The site (leafout.in) signs people in through Supabase Auth. Its server writes the Supabase access token (never the refresh token) to `leafout-access` on `.leafout.in`: HttpOnly, Secure, SameSite=Lax, 30 days. The app reads that cookie and never sets, refreshes or deletes it. Code: `auth/supabase.py` (`SupabaseVerifier`, `SupabaseIdentity`, `SupabaseError(reason)`), `auth/jwks.py` (the JWKS cache, shared with Google), `api/session_cookie.py` (`SessionCookieMiddleware`, which puts `tayyari_session` on a response that was bridged), `AuthService.leafout_sign_in`, called from `api.deps.get_auth_context`. Migration `0011_leafout_identity` adds `users.supabase_sub` (unique), `auth_sessions.source`, `supabase_session_id` and `verified_at`, and makes `users.email` nullable: a phone-only account has no email, and `/api/me` omits it.

- **Bridge.** Runs when the request has no live `tayyari_session`, carries the cookie, Leafout sign-in is configured, and the path is under `/api/` and is not a webhook. It runs at most once per request. Local checks, each with a reason code that goes to the log and never to the person: shape (`malformed`), `alg` ES256 or RS256 only (`alg`; `none` and HS* are refused before any key lookup), `kid` in the cached JWKS (`kid_unknown`, one refetch at most a minute), the signature (`signature`), `iss`, `aud` and `role` (`authenticated` only), `exp`, `iat` and the maximum age (`too_old`), `sub` a UUID, and not an anonymous user. Only when a new session is about to start: `GET /auth/v1/user` with the token (`revoked` on 401 or 403, `mismatch` when the id differs from `sub`, `upstream` on a network error or 5xx).
- **Recheck and swap.** A bridged session is `source='supabase'` with `verified_at`. After `TAYYARI_AUTH__SUPABASE_RECHECK_HOURS` (24) the next request reads the cookie again: a valid token with the same `sub` slides `verified_at` (local checks only); a missing or invalid cookie ends the session, and `/api/me` reports `reauth: true` so the web app sends the browser through the site's sign-in; a valid token with a different `sub` ends the old session and bridges a new one. Amendments in roadmap L1 §9 cover the expired-token case.
- **Accounts.** `UserRepo.sign_in_supabase` links by `supabase_sub`, then by verified email (setting `supabase_sub` on an F1 account that has none), and otherwise creates the account. An account already linked to another `sub` is refused with `email_taken`, and `/api/me` reports `signInIssue: "email_taken"`. The phone number is never stored.
- **Hold cookie.** `POST /api/auth/logout` and `DELETE /api/me` set `tayyari_bridge_hold` (host-only, HttpOnly, 600 s). It holds the first 32 hex characters of the sha256 of the site token the browser sent. While it matches the current token, the bridge is skipped, so an old token cannot recreate the session. A new sign-in brings a new token and works at once.
- **Limits.** Window `auth_bridge`, counted only after the local checks pass and before the Supabase call: 10 a minute and 30 an hour per client, 600 an hour for the service, and 50 a day per `sub` (`TAYYARI_GUARD__AUTH_BRIDGE_*`). Over a limit the request is anonymous, not a 429.
- **Settings.** `TAYYARI_AUTH__SUPABASE_URL`, `_PUBLISHABLE_KEY` (public, a plain string), `_SIGN_IN_URL`, `_SIGN_OUT_URL`, `_COOKIE` (`leafout-access`), `_VERIFY` (`auto`, `jwks` or `user_endpoint`), `_MAX_TOKEN_AGE_S` (3600) and `_RECHECK_HOURS` (24). Leafout sign-in is on when the URL, the key and the sign-in URL are set; startup logs `leafout_sign_in enabled=… verify=…`. `TAYYARI_AUTH__OWN_SIGN_IN` (default false) turns off the app's own Google and emailed-link paths; `/api/me` reports `providers.leafout` and `leafoutSignInUrl`. `TAYYARI_AUTH__REDIRECT_OTHER_HOSTS` (default false) answers page requests on other hosts with a 308 to `TAYYARI_AUTH__PUBLIC_URL`, never `/api/*`. No new secrets.
- **Anonymous match.** `auto` counts only the app's own providers, so Leafout sign-in does not turn anonymous posting match off.

### Payments (`/api/billing/*`)

**Products and prices** (`billing/products.py`). `pack3` is three posting matches with no expiry. `pass30` is unlimited postings for 30 days from purchase; a purchase made while a pass runs extends it from the end of that pass. Nothing renews by itself. Prices are integers in minor units: `TAYYARI_BILLING__PACK3_INR` and `PASS30_INR` in paise, `PACK3_USD` and `PASS30_USD` in cents; 0 means not set. A product is on sale in a currency only when its price is set, at or above the provider's minimum (100 paise, 50 cents), and that currency's provider has its secrets: INR goes to Razorpay (key id and key secret), USD to Stripe (secret key and webhook secret). A lower price counts as not on sale and is logged at start (`price_below_provider_minimum`).

**Currency.** The account's saved currency wins. Otherwise INR for an Indian time zone (`Asia/Kolkata` or `Asia/Calcutta`) or an Indian locale in `Accept-Language` (any `-IN` region, or Hindi), and USD for everyone else. `GET /api/billing/entitlements` is open to everyone (zeros when anonymous) and returns the balance, the currency to show first and the prices on sale; a product that is not on sale has no key.

**Checkout.** `POST /api/billing/checkout` `{product, currency?, timezone?}` needs a sign-in and answers 409 `not_offered` when the product is not on sale in that currency. The server writes its own order (random id, status `created`) and the provider's: for INR a Razorpay order through the official SDK (run in a worker thread), answered with `orderId`, `amount`, `currency` and `keyId` for Checkout.js; for USD a Stripe Checkout Session, answered with its `url`, returning to `/pricing?checkout=success` or `cancelled`. A provider failure is 503 `payment_provider_unavailable`.

**Settling.** Both webhooks settle through the same code. For Razorpay the browser hand-off settles too (see Browser hand-off), and the webhook, when `RAZORPAY_WEBHOOK_SECRET` is set, covers a payment whose browser never came back. Without that secret the Razorpay webhook route answers 503 `webhooks_not_configured`, and the server logs `razorpay_webhooks_off` once at start. Razorpay posts to `/api/billing/webhooks/razorpay` (`X-Razorpay-Signature`, checked over the raw body); Stripe posts to `/api/billing/webhooks/stripe` (`Stripe-Signature`, checked with `construct_event`). The raw body is read first (at most 512 KB) and its signature is checked before anything is parsed.
- Paid: Razorpay `order.paid` and `payment.captured`; Stripe `checkout.session.completed` (when the session is paid) and `checkout.session.async_payment_succeeded`.
- Failed: Razorpay `payment.failed`; Stripe `checkout.session.async_payment_failed` and `checkout.session.expired`. A failed event marks a `created` order `failed`.
- Anything else is acknowledged and ignored.

A paid event moves the order from `created` (or `failed`) to `paid` in one conditional update, which also checks the amount and currency when the provider sends them, and then grants three credits or 30 days of pass. An order that is already paid answers `already_paid` and grants nothing again, so a retried, replayed or duplicated webhook grants once. A good signature always gets 200 `{"ok": true}`, even for an order we do not know. A bad signature is 400. A webhook whose signing secret is not set here answers 503.

**Browser hand-off.** After Checkout.js, `POST /api/billing/verify` `{razorpay_order_id, razorpay_payment_id, razorpay_signature}` checks the HMAC of `order_id|payment_id` with the key secret, requires the order to belong to this account, settles it the same way and returns the new balance with `ok` (true once the order is granted or was already paid). It settles and grants at once; the webhook is the backstop when the browser never comes back.

**Consume order** (`billing/entitlements.py`; the balance is in `entitlements`, its history in `credit_ledger`). Every change starts by locking the account's entitlement row (a row lock on Postgres, the write lock on SQLite), so two requests cannot spend the same credit. Starting a posting match takes one use, in this order:
1. A use already recorded for the same match id: the same answer, nothing more taken.
2. A pass still running: a `match` row with delta 0.
3. This UTC calendar month's free posting (`TAYYARI_BILLING__FREE_MATCHES_PER_MONTH`, default 1), if not yet used: a `free_monthly` row with delta 0.
4. A credit: `credits` down by one, a `match` row with delta -1.
5. Otherwise `PaymentRequired`: 402 `{"error": "payment_required", "reason": "no_credits"}`.

Re-checks after answers, resume and cover writes, and downloads never call `consume`. A posting is paid for once, when it is first read.

**Refund on failure.** `entitlements.refund` gives back the use recorded for a match id. The posting-match stream calls it when reading fails, or when the client goes away, before the match is stored; once the match row exists the charge is kept. A credit is restored, and a `refund` row (ref `refund:<id of the use>`) takes the use out of the idempotency check and the month's free count. The ledger is append-only: a refund never edits or deletes a row.

**Webhooks and the cross-site guard.** The webhook routes are exempt from `CrossSiteGuard`: they carry no cookie, and the signature is their credential. The Content-Security-Policy allows Razorpay's Checkout.js and its frame and API hosts on every page; only the pricing page loads the script.

### Posting match (`/api/match`)

Every route needs a sign-in and answers only for the account's own profiles and matches (someone else's id gets the same 404 as an unknown one), unless anonymous matching is on.

**Anonymous matching.** `TAYYARI_MATCH__ALLOW_ANONYMOUS` is `auto` (the default), `true` or `false`. `container.match_anonymous` is the setting resolved once at startup, and `GET /api/me` reports it as `anonymousMatch`. `auto` is on exactly when the server offers no way to sign in (`auth.providers == (False, False)`: no Google credentials and no email path), because then an account is not something a person can get; `true` is always on; `false` is always off, and every route answers 401 without a sign-in. When it is on, a request with no session works like this:
- **Which profile.** A match can be started on a profile nobody has claimed (`profiles.user_id` NULL) or on a sample run, the bundled persona included; anything else is the 404 an unknown id gets. The match is stored with `matches.user_id` NULL.
- **Which match.** A match with no owner is opened by its id alone, for as long as its profile is still unclaimed or a sample run (ids are 128 random bits, like a profile's). The whole journey works the same: read, answers, resume, cover note, downloads. A signed-in person who holds such an id can open it too. A match an account owns is that account's alone, and once its profile is claimed the matches follow that rule.
- **No entitlement.** The entitlement service is never called: nothing is consumed, nothing is refunded. The meter is the client address: the per-address `match` windows, plus `TAYYARI_GUARD__ANON_MATCH_PER_DAY` (3) a day per address, counted for `POST /api/match` only. Answers, resumes, cover notes and downloads are only shed, as for accounts.
- **No list.** `GET /api/matches` answers `{"matches": []}`: there is no identity to list by, so the web app keeps its own list of the ids it was given.
- **A shared profile.** The sample is held by everyone who knows its id, so what a visitor says in answers there stays on their own match and is not kept as a profile fact that another visitor's match would read. On a profile nobody has claimed, facts are kept for the profile as usual (`user_facts.user_id` NULL).
- **Claiming.** `POST /api/me/claim` gives the matches and facts made on that profile without an account to the account (one `UPDATE` each). Account deletion also removes any such match that was still running when the profile was claimed.

A signed-in account behaves as described below whether anonymous matching is on or off: it matches only its own profiles and pays with an entitlement.

**Starting a match.** `POST /api/match` `{profileId, text?, url?}`; pasted text wins when both are given. The checks run in this order, and everything before the stream starts is answered as JSON:
1. Shed: the in-memory `match` bucket, before the body is read.
2. Sign-in, or anonymous matching allowed: 401 `unauthorized` when neither.
3. Body: 415 for the wrong content type, 413 above 128 KB, 408 for a stalled body, 400 for an invalid one.
4. Profile: one of the account's own profiles; with no sign-in, one nobody has claimed or a sample run. Else 404 `Profile not found`.
5. Counters: per client address and, for an account, per account, with `TAYYARI_GUARD__MATCH_PER_MINUTE` (0 = off), `_PER_HOUR` (10) and `_PER_DAY` (30), plus the global `TAYYARI_GUARD__MATCH_GLOBAL_PER_HOUR` (120), counted once per request. Over a limit: 429 with `Retry-After`.
6. Posting text: the pasted text, else the link. Neither is 400; text longer than `TAYYARI_MATCH__MAX_POSTING_CHARS` (20,000) is 400; a link that is not a web link is 400. A job board whose terms forbid automated reading (LinkedIn, `lnkd.in`, Naukri, Indeed, Glassdoor, foundit, Shine) is refused with 422 `posting_link_blocked` before anything is fetched. Any other link goes through the SSRF-guarded ingest (public pages only); a page that cannot be read, or has no text, is 422 `posting_link_unreadable`. Links inside a posting are never opened.
7. Allowance, with no sign-in only: `TAYYARI_GUARD__ANON_MATCH_PER_DAY` (3) per client address (0 = off), counted only now, so a posting that could not be read uses none. Over: 429 with `Retry-After`.
8. Spend: `TAYYARI_GUARD__EST_MATCH_USD` (0.06) reserved; 503 when the daily ceiling is reached.
9. Entitlement: `consume` (402 `payment_required` when nothing is left). Skipped with no sign-in: nothing is taken, so nothing is given back.
10. The stream starts.

**The stream** (NDJSON, one JSON object per line; shapes in `contract/match_api.py`), in this order:
- `posting` `{posting, facts}`: the posting as read (requirements, gates, anomalies, hash) and the person's facts, so the citations can show as rows arrive.
- `coverage` `{coverage}`: one line per batch of at most ten requirements, as each batch finishes. Batches are checked concurrently.
- `gates` `{gates, blocked}`: once gates and coverage are both done, so it arrives after the coverage lines.
- `radar` `{radar}`, then `questions` `{questions}` (at most three).
- `done` `{matchId, createdAt}`: the match is stored and the charge is kept.
- `error` `{message, code}` at any point after the stream started. `code` is `payment_required`, `no_requirements`, `export_blocked`, `rate_limited`, `unauthorized` or `failed`. An error refunds the charge before it is sent (with no sign-in there is no charge).

**The radar** (`match/radar.py`, `match/areas.py`). One axis per domain of the person's own graph, in the graph's order; "you" is the graph's mastery of that domain, "role expects" the tier weight of the requirements placed on it, scaled against the busiest axis, with `asks` naming them. The posting is read without the person, so its `domainHint`s ("Cloud infrastructure", "Agentic AI") rarely match the graph's own area names ("Backend & Cloud", "Agent & RAG Systems"). While gates and coverage are checked, one more Haiku call (`match.areas`, thinking off, about 2k tokens) places every requirement in one of the person's areas or in none, citing first the person's skill it is closest to; code keeps only a known skill's domain, a known domain id or `none`. A requirement it did not place (or every one, if the call failed) is placed by code: the hint against the domain names (the same words, one inside the other, a distinctive shared word such as "cloud", an alias group), then its named terms against the skills. Requirements in no area are listed in `outside` and named under the chart, never drawn as an axis: before 2026-10-17 they were summed into one "Other" axis that outweighed every real area. A stored radar that still has that axis is read without it and scaled again (`contract.match.Radar`). A re-check keeps the radar.

The posting is stored once per normalised text (`postings`), so a text read before is not read again, by anyone. The person's facts come from the portfolio extractor, cached per profile (`profile_facts`, keyed by the hash of the sources). The match keeps its facts (`matches.facts`, the FactBook) and its result.

**After the stream.**
- `GET /api/match/{id}` → `MatchDetail`: the posting with its text, the facts its rows cite, and the result (band, counts, confidence, coverage, gates, radar, questions). `GET /api/matches` → `MatchList`, newest first (empty with no sign-in).
- `POST /api/match/{id}/answers` `{answers: [{questionId, text}]}`: up to three answers of at most 1,500 characters. Each becomes a fact in the person's own words (`user_facts`, with its question) that later matches of the same profile can cite. Only the rows and gates the answered questions name are checked again; the result's version goes up; the response is the new `MatchDetail`. An answer to an unknown or closed question is ignored, and with no usable answer the route is 400. A save from a stale tab is 409 `match_changed`. Answers never consume.
- **Resume.** `POST /api/match/{id}/resume` streams `variant` `{variant}` then `done`. A resume already built for the match is returned without reserving anything. Otherwise the spend is reserved, the resume is composed from cited facts (relevance scores, code selection, phrasing, verification) and stored with status `needs_user` while any line waits for the person, else `ready`. `GET /api/match/{id}/resume` → `{variant}`, or 404 until one is built. `PATCH /api/match/{id}/resume` `{decisions: [...]}` (up to 40) applies each choice: keep, soften, drop, a figure for a line (`fill`, which becomes a user fact), or the posting's own term (`use_posting_term`). Nothing is applied unless every decision fits; otherwise 422 `decision_refused`. The variant is re-verified, stored and returned.
- **Downloads.** `GET /api/match/{id}/resume.pdf` and `.docx` answer 409 `export_blocked` while any line still needs the person. Otherwise they return the file with `Content-Disposition: attachment`, `X-Robots-Tag: noindex` and a name like `<person>-resume.pdf`. The checklist stored with the variant is refreshed when it changes.
- **Cover note.** `POST /api/match/{id}/cover` (no body) returns the stored note without reserving anything; otherwise it reserves the spend, writes the note from (requirement, fact) pairs with a plain sentence for each gap, 250 to 400 words, no generic openers, and stores it. It is 422 `cover_unavailable` when no covered requirement has facts to write from. `GET /api/match/{id}/cover` → `{cover}`, or 404 until one is written; `GET /api/match/{id}/cover.pdf` → the file.
- **Built once.** A stored resume or cover note is returned as it is, so answers given later do not change it. There is no rebuild control yet.

**Cost.** About half a cent per match on Haiku 5.5, as measured. The reservation for a match, and for a resume or cover note that is written, is `TAYYARI_GUARD__EST_MATCH_USD` (0.06).

### Discover's board store (`backend/tayyari/discover/`, `GET /api/discover/cities`)

Since D3 (2026-10-16) Discover is Search with AI. Two things stay in `backend/tayyari/discover/`, as a self-contained
service. The first is the store of open roles from Tayyari's own employers, which the search reads first. The second is
the pieces the search reuses: board fetching and parsing, places, the skill vocabulary, the person index and the quick
fit. The old code-matched list (groups, lenses, lanes, the companies lane, `GET /api/discover` and
`/api/discover/listings/{id}`) was removed. The deferred items of the first version are in `docs/discover-later.md`.

**Service boundary.** The package imports only `core`, `contract` and `storage.ports`; `tests/unit/test_layering.py`
enforces it. It owns:
- its tables, `discover_listings` and `discover_runs`;
- its settings, `TAYYARI_DISCOVER__*`;
- its HTTP client;
- its registry file, `discover/data/employers.json`.

No model is called, so it spends nothing. `api/routes/discover.py` serves only `GET /api/discover/cities`, the
`DiscoverCitiesResponse` for the city picker, which also starts a refresh when the store is old. `jobsearch` imports its
pure parts; `discover` never imports `jobsearch`.

**Sources.** Two public APIs that need no key:
- Greenhouse: `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`.
- Lever: `api.lever.co/v0/postings/{site}?mode=json`.

The registry lists 51 employers (38 on Greenhouse, 13 on Lever) with their Indian offices. `load_registry` checks the
whole file and lists every problem at once. Nothing else is fetched: no careers-page crawling, no portal, no aggregator,
and no link found inside a posting. A listing is kept when:
- its country is `IN`;
- its location names India or one of our cities; or
- it is a remote role that names India.

**Refresh.** How a board is read:
- with a User-Agent that names the service;
- at most `FETCH_CONCURRENCY` boards at once, with a timeout per request;
- with one retry for a timeout, a 429 or a 5xx;
- with a 64 MB cap on the answer.

A board that cannot be read keeps its listings. A listing is closed only when its employer's board was read and no
longer lists it. Single flight is decided in the database: a new run starts only when no run is `running` and started
less than 20 minutes ago.

`DiscoverService.maybe_refresh` starts a background refresh when the last good run is older than `REFRESH_HOURS`, and no
request waits for it. The cities route and every search route call it. The same refresh runs as its own process:
`uv run --directory backend python -m tayyari.cli discover-refresh` once, or `discover-worker --every HOURS` in a loop.
`jobsearch.boards` reuses its fetcher, `fetch_board` and `board_client`, for the boards the scouts find.

**What is stored.**
- `discover_listings` holds what an employer's public board says: title, place, work mode, apply link, dates, salary
  only when Lever states one, the years asked, and the posting's plain text (at most 20,000 characters).
- `discover_runs` holds each refresh's counts and note.

Neither table holds anything about a person.

**Quick fit (code only).** The search's cards carry it as `AiJob.quickFit`. A posting's title and text are matched
against the vocabulary in `discover/vocab.py` (426 skills in 20 professional sections, with aliases), and the result is
counted in asks:
- `GENERAL` skills are not counted.
- `ANY_OF` alternatives are one ask.
- `IMPLIES` credits included skills.
- The fit word follows the share met: `looks_close`, `worth_a_look`, `stretch`, or `cant_tell` when too few are named.

It was calibrated on 1,377 real listings. Recalibrate against real listings before changing a threshold.

**Apply links.** Each listing links to the employer's own posting (Greenhouse `absolute_url`, Lever `hostedUrl`), with
tracking parameters removed. A listing with no usable http(s) link is not stored.

**Tests.**
- `tests/unit/discover/`: refresh, parsing, places, the vocabulary and the quick fit, on `httpx2.MockTransport`.
- `tests/unit/storage/test_discover_contract.py`: the repo contract.
- `tests/integration/test_discover_routes.py`: the cities route, the background refresh and the absence of the old
  routes.

### Search with AI (`/api/jobsearch*`)

`backend/tayyari/jobsearch/` is the whole of Discover since D3. It was built 2026-10-16 from two baseline runs
(`docs/research/jobsearch-baseline.md`) and runs on Claude Haiku 5.5 only (`TAYYARI_JOBSEARCH__MODEL`). The specs are
in `docs/roadmap.md`, D2 and D3.

**A run, in two halves at once.**
1. **Brief, in code** (`brief.py`). The role, next roles, years and city; the strongest skills; the evidence lines `E1…`
   copied from the profile's own evidence (`evidence_lines`); and up to three angles. Without `remote` there is no
   remote angle.
2. **Near half: Tayyari's own employers.** They are read from Discover's store, narrowed in code (`narrow.py`: place,
   then kind, then level, then rank), and up to 6 go to the read. This happens while the scouts search, so the first
   cards show in a few seconds.
3. **Scouts** (`jobsearch.scout`). One `ClaudeClient.search_web` call per angle, all at once.
   - Only the basic `web_search_20250305` tool, with `allowed_domains` set to Greenhouse and Lever, `user_location` set
     to the city, and up to 3 searches each.
   - A scout never sees the name or the resume text.
   - `harvest.py` takes board tokens from the tool's result URLs and skips recruiter boards.
4. **Web half.** Up to 30 boards found on the web are read (`boards.py`, through Discover's fetcher and parser, with a
   one-hour cache). Roles already read are left out, then the rest are narrowed and up to 12 go to the read.
5. **Reads** (`jobsearch.judge`). Structured output, 4 postings per call, all at once.
6. **Checks, in code** (`judge.py`): every quote word for word, every evidence id real, years read in code, injection
   only with a checked quote. The fit word and `AiJob.asks` (met, partly, not shown: the card's pips) are worked out
   in code.

"Not for me" (`AvoidPrefs`) keeps hidden roles and waved-off employers out of both pools. `noStepUp` drops Senior titles
for someone under 4 years, and any read whose level is a step up. Each batch's jobs stream as soon as the batch is
checked. The trail sentences are written in code.

**Stored runs.** When a run finishes it is saved to `jobsearch_runs`:
- `result` holds what the person saw (`JobSearchRun`).
- `postings` holds, server-side only, the shown postings' text by job id.

`done` carries `runId`. A role's prep page and referral note are kept in `jobsearch_docs`, one per profile, role and
kind, and a rewrite replaces it. Both tables are purged after 30 days (`storage.maintenance`). A run is read back only
for its own profile; any other id is a 404.

**Routes.**
- `POST /api/jobsearch`, with `{profileId, city?, remote?, avoid?}`: NDJSON `step`, `job`, then `done` or `error`.
- `GET /api/jobsearch/latest?profileId=&city=&remote=`: the last `JobSearchRun`, or a 404 "No search yet". The page
  shows it at once. A search runs only when the person presses the button.
- `GET /api/jobsearch/runs/{runId}/jobs/{jobId}/posting?profileId=`: the `JobPosting`. "See how you fit" sends its text
  to `POST /api/match`, with the same credits as a pasted posting.
- `POST /api/jobsearch/prepare` and `POST /api/jobsearch/referral`, with `RoleRequest {profileId, runId, jobId, fresh?}`:
  a `RolePrep` or a `ReferralNote`, kept once written. `fresh` writes a new one.

**Prepare** (`prepare.py`, `jobsearch.prepare`, one Haiku call).
- The lead comes from the checked read.
- Every gap gets the nearest evidence (a checked id) and one small step. The step is advice, linted against claims about
  the person, with its anchors checked against the posting and the evidence; a code template replaces one that fails.
- 3 to 4 questions, each tied to a quote checked in the posting. Each question's story is built in code: cited evidence
  lines, plus placeholders for what only the person knows.
- A posting with text aimed at an AI gets no model call, only the fallback page.

**Referral** (`referral.py`, `jobsearch.referral`, one Haiku call). One short paragraph, a storyline in three sentences in
typed slots, citations first (changed 2026-10-17 from three separate lines, at the user's request):
- `intro`, who you are: opens "I'm" or "I", the current role (and place) and at most one cited evidence line, no quote.
- `fit`, why you fit them: the prompt gets the role's checked matches (`AiJob.met`, "what they need that you show");
  the sentence quotes a short passage of ONE matched ask and says what the person did, citing exactly the evidence line
  that met it. Up to 230 characters.
- `draw`, why they fit you: the posting's own words about the mission or the work, and why that is where the person
  wants to go; it may not claim anything the person did.
- Every quote is checked against the posting; numbers, names and seniority words must be in the cited evidence or the
  posting (`portfolio.ground` anchors; the layering allows `jobsearch` to import `portfolio`); no greeting, question
  or praise words.
- A sentence that fails falls back to a template that still reads in the paragraph ("{employer} wants “…”, and I can
  point to {the evidence line}"; evidence is never clipped mid-phrase). The web app adds "Hi {name}," (or "Hi there,"
  when the name is left empty), the ask "Would you be open to referring me?" and the employer's link. A note stored in
  the old three-line shape fails validation and is written again.
- An employer found on the web is named from its board slug without a trailing number (`appen-2` becomes "Appen").

**Findings from building on the API.**
- With `output_config.format` set alongside a server tool, Haiku 5.5 answered at once without searching. So the scouts
  run in plain mode and only the reads are structured.
- The basic search tool returns `web_search_tool_result` blocks, which `anthropic_client.searches_of` reads one query at
  a time.
- One search costs $0.01. A run is about $0.11, mostly nine searches.

**Guards.** The profile is opened the way Discover opens it (`require_profile_access`).
- **The search**, in this order:
  1. `shed`, then off (503), then the body.
  2. The profile (404), then the city (400).
  3. The `jobsearch` windows per address and per account (1/min, 4/h, 8/day), or `jobsearch_anon` without an account
     (3 a day). The service-wide cap is 40 an hour.
  4. The spend reservation (`EST_JOBSEARCH_USD`, $0.20).
- **Latest and posting:** shed in the `chat` bucket.
- **Prepare and referral:** shed in `chat` and reserve `EST_JOBSEARCH_DOC_USD` ($0.02).

No credit is charged. Every answer is `no-store`.

**Tests.**
- `tests/unit/jobsearch/` covers harvest, brief, narrowing, the checks with known-bad reads, prepare and referral (every
  check proven to fire), and the run over fake boards and scripted Haiku: two halves, stored runs, remote, Not for me,
  prep and referral caching and ownership.
- `tests/unit/llm/test_search_web.py` uses SSE fixtures.
- Also `tests/integration/test_jobsearch_routes.py`, `tests/unit/contract/test_jobsearch_parity.py` and the repo
  contract.

### Practice the interview (`/api/interview/*`)

`backend/tayyari/interview/` is I1, built 2026-10-17 from the canvas https://claude.ai/artifact/VtufBm3wga3E1SJRiHw3cd
and the research in `docs/research/mock-interview.md` (what HackerRank's Chakra and its practice interviews do, and what
we take and leave). Everything runs on Claude Haiku 5.5 (`TAYYARI_INTERVIEW__MODEL`). Speech goes through Sarvam AI when
the server has a key (`/api/speech/*`, below), else the browser's own speech; either way the interview itself only
ever sees text. It is free while we test it; the guard windows are the only meter.

**Four kinds.**
- **This role** comes from a search-with-AI role (`RoleSource {runId, jobId}`, read through `RoleLookup`, which
  `JobSearchService.role` satisfies). Its slots are 2 asks the person shows, 1 gap, 1 claim and 1 story; Spend more time
  on shifts the quotas.
  A role of the person's graph (`GraphRoleSource {roleId}`, `Profile.roles`) has no posting. Its plan is the role's
  requirements: a gap is one the person does not show or shows below the role's level (`target − current`, biggest
  first), with the nearest evidence line; a met requirement is a claim on an evidence line of its own skill (a met
  requirement whose skill has no evidence line is not asked); then a story, the role's skills first. Quotas gap 2,
  claim 2, story 1 (Spend more time on shifts them). The requirement is a source of origin `role`, prompted as
  "role needs: …" and never as a posting. The title is the role's title; there is no employer summary. An id that is
  not among the profile's roles is `GraphRoleGone` (404). Prepare's "Practice the interview for this role" opens it.
- **Defend your resume** asks about 5 of the person's evidence lines: two with a number, a "Led …" line, a line from a
  repository or page, and one more. A low-scoring skill is never asked as if the resume listed it (it is Tayyari's
  reading, not the person's words).
- **Behavioural stories** builds 4 stories from evidence lines with people or action in them.
- **Work sample** uses one of six templates (`tasks.py`: frontend, data analyst, marketing, nursing, accounts,
  teaching), picked from the role's or the person's title. The task comes first, then 3 questions on the choices. An
  optional assistant can be used along the way.

**A session.**
1. **Plan, in code** (`plan.py`, `POST /api/interview/plan`). It is deterministic and free. Slot ids are `s1…`, and every
   source is an evidence line or the posting's own words. `start` recomputes the plan and drops the slots the person
   skipped.
2. **Phrase** (`phrase.py`, `interview.phrase`, one call at start; work samples skip it). Each slot gets a question, a
   push, a floor and hidden notes on what a full answer covers.
   - Code checks each line: under 200 characters, ending with "?", and every number and name in the slot's sources or
     the posting.
   - A line that fails gets the kind's template.
   - A posting with text aimed at an AI gets templates only.
3. **Answer** (`router.py`, `judge.py`, `flow.py`).
   - The router handles these in code, with no model call: repeat, skip, "I don't know" (Couldn't defend, with the words
     quoted), a dispute (the cited line is shown, once per slot) and unclear input.
   - Otherwise `interview.judge` reads the answer. It returns quotes first, then on-topic, story parts, a verdict, why,
     and a new fact.
   - Code keeps a verdict only on a quote that is a passage of the answer; with none it is Not assessed. A floor answer
     is capped at Shaky, and a story needs situation, action and result to be Defended.
   - A personality word in `why` is replaced. A new fact is kept only on a Defended answer to an ask, gap, claim or
     story, written like a resume line (never "The person …"), with every name and number from the answer.
   - The rungs follow fixed rules. Surface: defended moves to the next slot, shaky goes to push, couldn't goes to floor.
     A story's push asks for its missing part.
   - Off-topic answers are redirected once.
   - A failed judge call answers 503 (`ModelUnavailable`) and records nothing, so the person can answer again.
4. **End** (`debrief.py`, `interview.debrief`; `checks.py`, `interview.checks` for work samples).
   - Counts, the first thing to work on and the role summary are worked out in code.
   - The model phrases Say, Avoid and Fix. Say is checked against the answer and the sources, and `[brackets]` hold
     what only the person knows. Avoid must quote the answer. Fix is clamped to 5 to 120 minutes.
   - Each work-sample check needs a quote from the work, the test note or an answer.
   - The assistant's use is described in code ("Asked the assistant 2 things · changed its version before using it").
   - A failed call still gives a debrief built from code. `end` on a finished session returns the stored debrief.

**Storage.** `interview_sessions` holds one row per session.
- `state` (JSON) holds the plan, the phrased lines and notes, the rung, the per-slot judgements, the turns, the work and
  the assistant log. `debrief` (JSON) is filled once the session is done.
- `version` makes saves compare-and-swap: a lost race is a 409 "This interview moved on in another tab."
- Rows are purged after `retention_days` (30).
- Ask again creates a one-slot session from the stored lines, with no model call.
- `set_fact` marks a new fact as kept "From you".
- History works out the best verdict per evidence line across sessions, the kept facts, and the interviews left today.

**Routes** (`api/routes/interview.py`). Every answer is `no-store`, and a session id that is not 32 hex is a 404. The
profile is opened as the job search opens it, sample profile included. The sample profile's history is always empty,
because everyone shares it: a sample session is reachable only through the id its own browser holds.
- `POST /api/interview/plan` → `InterviewPlan`.
- `POST /api/interview/sessions` → `InterviewSession`. The checks run in this order: shed `interview`, off (503), body,
  profile, the `interview` windows per address and per account, then the spend reservation (`EST_INTERVIEW_USD`).
- `GET /api/interview/sessions?profileId=` → `PracticeHistory`. `GET /api/interview/sessions/{id}?profileId=` →
  `SessionView`.
- `POST …/{id}/answer` (reserves `EST_INTERVIEW_TURN_USD`), `…/work`, `…/assistant` (reserves the turn amount),
  `…/end` (reserves `EST_INTERVIEW_END_USD`), `…/again` (its own `interview_again` window), `…/facts`, and
  `DELETE …/{id}`.
- Off, every route answers 503 except `DELETE`, so a transcript can always be removed.

**Speech, through Sarvam AI** (`backend/tayyari/speech/`, `api/routes/speech.py`, added 2026-10-17). The user added a
Sarvam key with 100 free credits; the official `sarvamai` SDK (pinned 0.1.35) is called only from the server, so the
key never reaches a browser.
- **Speech-to-text:** Saaras v4 (`en-IN`). The room cuts a spoken answer at each pause (at most 30 s a piece) and sends
  each piece to `POST /api/speech/transcribe` as the raw body (WebM/Opus, Ogg, MP4/AAC, MP3 or WAV). The interview's
  own names and terms (`InterviewService.speech_context`: employer, title, slot topics, names and tech words from the
  sources, the work material's identifiers; at most 50) go as Sarvam `keyterms`, so "Kitebird" and "LCP" come back
  right. The audio is never stored or logged; only the text reaches the interview.
- **The interviewer's voice:** Bulbul v4-flash, speaker `simran_en_customer`, MP3. `POST /api/speech/speak` reads only
  a line the interviewer said or is saying in that session (anything else is a 404), and keeps an in-process cache so a
  repeated line costs nothing. The room plays it through Web Audio (no `media-src` change to the CSP).
- **Made ahead** (added 2026-10-17). Measured on production, a question's voice took about 4.7 s to come back from Sarvam
  (0.9 s from the cache), so the server makes the lines an interview will probably say before the browser asks.
  - *One line, one call.* A line being made is one shared task, keyed like the cache (model, speaker, pace, text).
    `SpeechService.speak` for a line already being made waits for that task instead of calling Sarvam again, so a request
    for a line that is being made ahead, or two requests for one new line, cost one call and one charge. The task is
    shielded: a browser that hangs up does not stop it, and a finished line goes into the LRU as before. The service
    keeps every task it starts in a set until it is done (`drain()` waits for them, `aclose()` stops them at shutdown).
  - *`SpeechService.prefetch(session_id, texts, pace=None)`* is fire and forget: it schedules each text not cached, not
    being made and not already waiting, in order, and returns at once. It never raises (a failure is logged with the size
    and the reason, never the text or audio, and leaves nothing cached). At most `TTS_PREFETCH_CONCURRENCY` (2) lines are
    made at a time across the process (a semaphore; a person's own `/speak` never queues behind it). Each line is charged
    like `speak` charges it, but first checked under a lock: a line the day's or the session's remaining budget would not
    cover is skipped silently, so a prefetch never spends the last credits a person may be waiting for. It is off when
    `TTS_PREFETCH` is false, there is no engine, or the cache is off (`TTS_CACHE_ITEMS=0`). The pace is the one the session's
    browser last asked for (the slower voice is 0.85), else 1.0, so a person on the slower voice is not sent lines they
    will not ask for.
  - *What to make* is the interview's to say (`InterviewService.speech_upcoming`, code over the stored session, no model,
    `None` for another profile's session or a finished one). `now` is the line being said, its lead and then its text.
    `soon`, most likely first, is the current question's follow-up and easier question (only at the surface rung and only
    with `TTS_PREFETCH_FOLLOWUPS`; a story's follow-up is one of its four fixed part questions; a work sample asks each
    question once), the surface questions of the slots not asked yet in plan order, and the sentences the interviewer
    says the same way to everyone: the replies to "I don't know" and to an off-topic or too short answer, and the closing
    line. Those are one made line for every session, so they cost credits once. These texts stay on the server;
    `/api/speech/speak` still reads only a line the session has said or is saying, and by the time the browser asks, it has.
  - *When* (the api layer composes interview and speech, `api/routes/_voice.py`; only while Sarvam speech is on):
    `POST /sessions` (start), `…/work`, `…/again` (for the new session) and `GET /sessions/{id}` (a reload, which also
    warms a replica that has not made the lines) have `now` then `soon` made once the response has been sent (a Starlette
    background task, so the person is not kept waiting). `…/answer` asks first for the lines the next turn may need (the
    current slot's follow-up and easier question and the next slot's question) while the judge reads the answer, which
    takes about 2 s, and after the reply for the new `now` and `soon` (a dispute or redirect lead could not be known
    before). A problem making lines ahead never changes what a route answers.
  - *Cost.* Lines nobody hears are paid for: a full five-question interview makes about 1.1 thousand characters
    (about Rs 2.2, every question plus every slot's follow-up and easier question) against about 0.6 thousand before, so
    the day's 6,000 characters cover about five of them instead of ten. A person who turned the voice off in the browser is still charged (the server does not know). Raise
    `TTS_DAILY_CHARS`, or set `TTS_PREFETCH_FOLLOWUPS=false` to leave out the lines only some answers lead to.
  - *The browser* (`src/practice/speech.ts`, `useInterview.ts`) asks for a line's lead and question together and plays the
    lead as soon as it arrives while the question is still on its way (a question Sarvam cannot give is read by the
    browser's voice after the lead), and `warmVoice` starts the requests the moment a new line is known, before the room
    renders it. Its per-line cache and the browser-voice fallback are unchanged.
- **Budget** (soft, `speech/meter.py` on `KvRepo`, one key per UTC day): `TAYYARI_SPEECH__STT_DAILY_SECONDS` (1800) and
  `TTS_DAILY_CHARS` (6000) across the service, `STT_SESSION_SECONDS` (600) and `TTS_SESSION_CHARS` (3000) per session.
  A piece is billed at what the browser says it ran or what its size says, by the weight of its format, at most 30 s.
  Prices (2026-10): about Rs 0.50 a minute of speech-to-text and Rs 2 per 1,000 characters of voice, so the daily caps
  are about Rs 15 and Rs 12. When a budget runs out, or the key is unset, or Sarvam fails, `GET /api/speech/status`
  says so and the room falls back to the browser's speech, then typing.
- **Routes:** `GET /api/speech/status?profileId=&sessionId=` (session optional),
  `POST /api/speech/transcribe?profileId=&sessionId=&seconds=` (409 on an ended interview, 413 over 1.5 MB, 415 for a
  non-audio type, 503 with the reason when Sarvam is unavailable), `POST /api/speech/speak` returning `audio/mpeg`. The
  `speech` window allows 40 requests a minute per address.
- **Measured locally, 2026-10-17:** a 110-character question spoken in about 2 s (0.03 s from the cache); a 9-second
  answer written down in about 0.5 s, names right.
- **Sarvam's side:** its terms say identifiable content is not used for training without opt-in; retention is set per
  workspace in Sarvam's dashboard (Settings, Workspace, Data retention). Set it to the shortest period.

**Microphone.** The app's `Permissions-Policy` allows `microphone=(self)` (camera and geolocation stay off).

**Measured locally, 2026-10-17, real Haiku 5.5:**
- start (the phrasing call) about 6 s;
- each judged answer 1.5 to 4 s;
- the debrief 2 to 5 s;
- a work sample starts at once (no phrasing call).

**Tests.**
- `tests/unit/interview/`: plans, the router table, the judge, phrase, debrief and checks with known-bad fixtures, the
  assistant observation, and full `role` and `work` flows over `FakeInterviewRepo` and a scripted Haiku.
- `tests/integration/test_interview_routes.py`, `tests/unit/storage/test_interview_contract.py`,
  `tests/unit/contract/test_interview_parity.py` and `tests/unit/guard/test_ratelimit_interview.py`.

### Prepare (`/api/prepare*`)

`backend/tayyari/prepare/` is P1 (spec in `docs/roadmap.md`, canvas https://claude.ai/artifact/QgpVd4MRfnsqZRrPrLRe73),
built 2026-10-18. "How do I prepare for this role?" on the skill graph answers with four typed blocks instead of the coach's
prose: where you stand, the one move, the plan, next actions. The contract is `shared/prepare.ts` ⇄
`contract/prepare.py` (`tests/unit/contract/test_prepare_parity.py`). Everything runs on the chat model
(`TAYYARI_CHAT_MODEL`, at `TAYYARI_CHAT_EFFORT`); the `prepare.*` purposes are not in the client's "wants thinking" table,
so Haiku 5.5 runs them with thinking off and Sonnet 5.5 at the chat effort.

**Code supplies every number** (`prepare/assemble.py`, no model). For `PrepareRequest {profileId, roleId, requirement?,
fresh?}` the role is found on `Profile.roles`:
- gaps are the requirements the graph does not meet (`have` false, or `current` under `target`), ordered by
  `target - current`; exactly one is `biggest` (ties: the first); `strong` are the requirements met;
- scope `requirement` keeps only that gap (matched by name, case-insensitive); a requirement already met is 400
  `already_strong`, and so is a role with no gap at all; an unknown role or requirement is 404;
- `rank` and `roleCount` come from the roles by alignment (1 = closest), never a percentage;
- the evidence list is every `Skill.evidence` line numbered `e1…` (the skills the role's requirements point to first, by
  `skillId` else by name, then the other skills by score; at most 40), each with its source's kind and the chip's label
  (`resume` → "your resume", `github` → "your GitHub", `lovable` → "your Lovable app", `linkedin` → "your LinkedIn",
  `portfolio` → "your portfolio", else the source's own label).

**Two calls at the same time** (`prompts/prepare.py`, `prepare/schemas.py`; citations before the text in every object,
`evidenceIds` restricted to the plan's ids and the gap names to the plan's gaps in the schema itself):
- `prepare.standing` → `{headline, gaps: [{name, evidenceIds, why}]}`: one sentence over the gaps and one "why" per gap.
- `prepare.moves` → `{moves: [{have: [{evidenceIds, text}], title, body, works, phases: [{title, hours, tasks,
  deliverable}]}]}`: one to three moves, best first, distinct in size. The prompt gets the role's title, eta and why, the
  gaps with their levels, what is met, the evidence lines, the person's headline and summary, and the readers' summaries
  and notes from `StoredContext.readers` (clipped).

**Code checks** (`prepare/verify.py`; a known-bad test for each): a cited id must exist; a why or have item without a
valid citation is dropped; every digit sequence in a why, have item, title, body, task or deliverable must be in the cited
evidence (the move's have citations for its title, body and phases), the gaps' levels or the role's eta, else that sentence
is dropped and a move whose title or body fails is dropped; `works` is filtered to the plan's gap names
(case-insensitive) and a move with none left is dropped; hours outside 1–40 drop the phase; at most 5 phases of at most
4 tasks; a phase with no task or no deliverable left is dropped, and a move with no phase; lengths are clipped at a word
(titles 90, body 200, tasks 160, deliverable 120, why 180, headline 160); the surviving moves are `m1`…`m3`.

**The stream** (`POST /api/prepare`, NDJSON, `Cache-Control: no-store`): `gaps` at once with the plan (no moves, no
whys), then `standing` and `moves` as each call lands (either order), then `done` with the stored plan (version 1, state
`{hoursPerWeek: 6, moveIndex: 0, done: [], links: []}`); or `error` with code `no_moves` (the moves call failed or no
move survived; a retry is the client's) or `failed` (a model error or the spend ceiling). A standing call that fails or
runs late leaves the gaps with their numbers alone. A plan already stored for the same profile, role and requirement
streams `gaps` then `done` at once, unless `fresh` is true: no counter, no reservation, no model call. The guards, in
order, all answered as JSON before the stream starts: `shed` (in-memory `prepare` bucket, before the body is read) →
body (415 / 413 / 408 / 400) → profile (404; opened as the job search and the interview open it, the bundled sample
seeded on first use so a plan's row can refer to it) → the plan's numbers from the graph (404 / 400) → a stored plan →
the `prepare` windows per client address and per account (`TAYYARI_GUARD__PREPARE_PER_MINUTE` 2, `_PER_HOUR` 10,
`_PER_DAY` 30, `PREPARE_GLOBAL_PER_HOUR` 120; 429 with `Retry-After`) → the spend reservation (`EST_PREPARE_USD`, 0.06;
503). An account on the unlimited list skips every `shed` and counter; the reservation still applies. Nothing is charged
to an account.

**After the stream.**
- `GET /api/prepare/{planId}?profileId=` → `PreparePlan`. A plan is read back only for its profile; another profile's
  plan id, an unknown one and an id that is not 32 hexadecimal characters are the same 404.
- `PUT /api/prepare/{planId}/state` `PrepareStateUpdate {profileId, version, state}` → `PrepareStateSaved`: a
  compare-and-swap on `version` (409 `plan_changed` when stale; the new version is `version + 1`). Every done key
  (`"<moveId>:<phaseIndex>:<taskIndex>"`), `moveIndex` and link must name a move, phase and task the plan has, and a
  link's url must be `http(s)`, else 400. Sheds in the `chat` bucket; no model.
- `GET /api/prepare/{planId}/repos?profileId=` → `PrepareRepos` (`prepare/github.py`, code only, nothing fetched): the
  `### N. name` blocks of the GitHub source's digest in the stored context (`ingest.github`'s format; the sample's is
  `tayyari/sample/github.md`), ranked by the words they share with the plan's moves and gap names plus a small bonus for
  tests and CI; reasons are short code-made strings ("names evals", "has tests", "has CI", "no README yet"); at most one
  `suggested`, only when it shares a word with the plan; urls from the source url's owner; `github: false` without a
  GitHub source.
- `POST /api/prepare/{planId}/headline` `{profileId}` → `PrepareHeadline` (`prepare.headline`, one call, counted and
  reserved like a plan with `EST_PREPARE_HEADLINE_USD`, 0.02): a headline for the role citing evidence ids first, kept
  as `proposed` only when it is at most 120 characters, cites at least one valid id, and every number and every
  capitalised word after the first is in the cited evidence, the role title or the current headline (`Person.headline`);
  otherwise `proposed` is absent and `note` says why in a sentence.

**Storage.** `prepare_plans` (migration 0010, additive): `content` is the plan without `state` and `version`, written once;
`state` and `version` change with every save. The foreign key to `profiles` cascades, so a plan goes with its profile's
purge or its account's deletion and needs no retention step of its own. The newest row for a (profile, role, requirement)
is the one handed back; `fresh` adds a row and the older plan stays readable by its id.

**Tests.**
- `tests/unit/prepare/`: assembly, the verifiers with a known-bad answer per check, the digest parser and ranking over the
  sample digest, the prompts and schemas, the service's stream over fakes and a scripted model (both orders, failures,
  cancellation, the stored plan, the state's compare-and-swap, repos, headline), and the golden
  `tests/data/prepare/riya.prepare.json` (Riya's AI Product Engineer plan from the hand-written answers next to it;
  `UPDATE_PREPARE_GOLDEN=1` rewrites it).
- `tests/integration/test_prepare_routes.py` (the real service over the real app, a scripted Claude at the edge),
  `tests/unit/storage/test_prepare_contract.py`, `tests/unit/guard/test_ratelimit_prepare.py` and
  `tests/unit/contract/test_prepare_parity.py`.

### Serving the web app (`api/static.py`)

`mount_spa` is the last route and answers GET and HEAD from `backend/static` (the build), first match wins: a real file (assets cached for an hour, `.html` files `no-cache`); a page `<path>/index.html`, the generated content pages and `/pricing` when the build has its own, `no-cache`, where a trailing slash is a 308 to the address without it; one of the app's own client routes (`APP_ROUTES`: `/`, `/pricing`, `/match…`, `/discover…`, `/practice…`, `/p/…`, and `/dev/…` on a non-prod server), which gets `index.html` with 200. Anything else gets `404.html` with status 404, or the JSON 404 when the build has no 404.html, and no build at all is the JSON 404 (so is `/404.html` itself). The private routes (match, discover, practice, `/p/`, `/dev/`) send `X-Robots-Tag: noindex`; `/`, `/pricing` and the content pages do not. A path that is not a plain relative address (a leading slash, a backslash, a NUL, a `..` that leaves the build) is never a file. The `/dev/` check reads `app.state.settings`, which `create_app` sets; `/api/*` never reaches this route.

### The Leafout Weekly (`weekly/`, roadmap W1)

A personal newspaper, built per reader once a week and stored as one `weekly_editions` row (unique per user and week).

- **Compose** (`weekly/compose.py`): openings from `discover_listings` first seen in the last 7 days (14 when fewer than three), ranked by
  the code-only quick fit against the reader's newest claimed profile, "can't tell" only when nothing better came in; the lead one gets one
  judge read (`weekly.judge`, jobsearch's prompt and `verify`). Progress is code over Prepare plans, the newest practice session and the
  graph's roles. News: up to 3 `search_web` calls (`weekly.news`) restricted to `TAYYARI_WEEKLY__NEWS_DOMAINS`, one structured read
  (`weekly.news.read`), then `weekly/ground.py` drops any item whose URL was not a search result or whose skill is not on the graph. Tip and
  quote rotate from a list in code. The paid part sits inside `SpendGuard.reserve(est_weekly_usd)` and a soft weekly cap; without it the
  edition is built anyway, without news and the read.
- **News domains must be readable by Anthropic's crawler**: one blocked domain makes the API refuse the whole search
  (`BLOCKED_NEWS_DOMAINS` in `core/settings.py`, kept out by a test).
- **Schedule** (`_weekly_loop` in `container.py`, every 15 minutes, `TAYYARI_WEEKLY__ENABLED`): from 06:00 India time on the reader's day,
  due readers are claimed through `kv` rows so two replicas never both work on one; build, store, then email in batches through Resend with
  `List-Unsubscribe` one-click headers; a failed try is retried by a later tick up to `max_tries`. Accounts without an email get the stored
  edition only. Without a Resend key nothing is sent (local runs write the HTML under the data dir).
- **CLI**: `python -m tayyari.cli weekly run` (one pass now) and `weekly preview <email>` (build and store this week's edition for that
  account, print the local HTML path).

## Jev decisions framework

### Decision specifications

Each spec is a **DecisionSpec**: question (template with `bind()` for dynamic parts), `interpret()` function (argmax/thresholding in code), neutral value, and a default config.

Specs are grouped into **packs** sharing a state (batched into one Jev call to save cost). Example: all first-read questions use state `{resume, notes}` and run in one call.

### Execution modes (from `decisions/defaults.toml`)

| Mode | Behavior | Use case |
|------|----------|----------|
| `off` | Never ask Jev. Return neutral. | Disabled specs. |
| `shadow` | Call Jev, log next to incumbent (e.g., Sonnet's own output), never delay stream > 1.5 s. Return incumbent (never escalate). | Skill levels, role fit — gather data, no impact on live output. |
| `primary` | Ask Jev first. If confidence ≥ threshold, use it. Otherwise, run Claude Haiku fallback (same rubric) and return with `escalated=true`. Fail open to fallback on errors, circuit breaker, or spend trip. | Live decisions: first read (with fallback=skip for 1 s latency), triage, evidence checks, chat gate. |

### Fallback behavior

- `fallback="claude"`: Haiku structured call with the same schema (enum labels for choice, boolean + confidence for noul, integer for score). Escalation reason: `low_confidence`, `jev_error`, `circuit_open`, `jev_disabled`, or `spend`.
- `fallback="skip"`: Return neutral, e.g., first-read with no fallback because a Haiku call cannot meet 1 s latency.

### Recorder and logging

**DecisionRecorder** buffers and flushes every 1 s or 50 rows:
- `jev_calls`: request, response, latency, cost.
- `decision_states`: SHA256-deduplicated, zlib-compressed state (resume text) to save space.
- `decision_log`: decision id, source (jev/claude/default), confidence, threshold, escalation, incumbent (for shadow decisions).

Each row carries the pinned Jev model and spec version, so replays never mix versions.

**Audit sampling**: `audit_rate: 0.05` means 5% of used Jev answers also get a background Claude answer (tracked task). Agreement statistics feed the `cli eval` harness.

### Adopting a new Jev version

1. Bump `TAYYARI_JEV__MODEL=jev-1.14.0` in `.env` or cloud secrets.
2. Export the corpus: `cli export > corpus.jsonl` (join decision_log with state).
3. Replay against the new Jev: `cli replay --model jev-1.14.0 < corpus.jsonl > report.json`.
4. Review agreement by confidence bin: `cli eval report report.json`.
5. Compute new thresholds: `cli eval promote --dry-run < corpus.jsonl` (per spec, the lowest threshold meeting agreement target).
6. Commit the `defaults.toml` update.

Because the log records the model pin, old results never mix with new ones, and you can safely A/B test or roll back.

### Adding a new decision

1. Define a **DecisionSpec** with id, version, pack, question template, `interpret()`, neutral value, and default config.
2. Add it to the pack's `specs` tuple.
3. In `decisions/defaults.toml`, add a `[spec.id]` section with mode, threshold, fallback, etc.
4. Update **DecisionService** (or its implementation) to expose the decision to the caller (e.g., `AnalysisService` or `ChatService`).
5. Wire the service to the decision runner in `container.py`.
6. Write a unit test that mocks Jev and Claude.
7. Add a row to `cli eval` to track the new spec's agreement.

## Storage

### Tables

All timestamps are `timestamptz` (UTC). JSON columns use `JSON().with_variant(JSONB, "postgresql")` for Postgres efficiency.

| Table | Key | Columns | Indexes / notes |
|-------|-----|---------|-----------------|
| `profiles` | `id` PK | `created_at`, `last_accessed_at`, `owner_hash` (HMAC, nullable), `is_sample`, `schema_version`, `goal`, `profile` JSON, `user_id` (FK users, nullable; set when claimed) | `(last_accessed_at)` for retention; `(owner_hash, created_at)` for owner queries; `(user_id, created_at)` for an account's profiles |
| `profile_contexts` | `profile_id` PK/FK | `context` JSON (sources + reader outputs), `bytes` | split from profiles so `/api/profile` never reads source text |
| `chat_sessions` | `id` PK | `profile_id` FK cascade, `origin` JSON, `created_at`, `updated_at`, `turn_count`, `ip_hash` | `(profile_id)`, `(updated_at)` for old-session cleanup |
| `chat_messages` | PK `(session_id, seq)` | `role`, `content` text, `tools` JSON null, `llm_call_id` FK null, `created_at` | optimistic append guard |
| `llm_calls` | `id` UUID PK | `created_at`, `purpose` (reader/synth/chat/decision_fallback), `model`, `request_id`, `profile_id` FK, `session_id` FK, `analysis_id` FK, `ip_hash`, `attempt`, `status`, `error_kind`, `stop_reason`, `input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens`, `web_searches`, `cost_micro_usd` BIGINT, `latency_ms`, `ttft_ms` | `(created_at)`, `(profile_id)`, `(purpose, created_at)` for queries |
| `jev_calls` | `id` UUID PK | `created_at`, `pack`, `model_requested`, `model_served`, `state_hash` FK, `questions` JSON, `response` JSON null, `status`, `error_kind`, `request_id`, `input_tokens`, `output_tokens`, `cost_micro_usd`, `latency_ms`, `profile_id` FK, `analysis_id` FK, `is_sample`, `payload_purged_at` | `(pack, created_at)`, `(state_hash)` for dedup query |
| `decision_states` | `hash` CHAR(64) PK (SHA256) | `payload` BYTEA (zlib JSON), `raw_bytes` text, `created_at`, `last_ref_at`, `is_sample` | deduplicates resume text across 10–40 calls per analysis |
| `decision_log` | `id` UUID PK | `created_at`, `call_id` FK null, `spec_id`, `spec_version`, `pack`, `subject_ref` (claim or skill id), `profile_id` FK, `analysis_id` FK, `mode`, `jev_answer` JSON, `jev_confidence`, `threshold`, `claude_call_id` FK null, `claude_answer` JSON, `incumbent` JSON, `used`, `source_used` (jev/claude/default), `escalated`, `escalation_reason`, `latency_ms`, `cost_micro_usd`, `is_sample` | `(spec_id, created_at)`, `(profile_id)`, `(call_id)` |
| `rate_limit_hits` | PK `(bucket, window_start)` | `count` | `(window_start)` for cleanup; purged after 2 days |
| `spend_daily` | PK `(day, scope)` (scope: total/anthropic/jev) | `micro_usd`, `calls` | `cli spend --reconcile` sums calls per day to flag drift |
| `kv` | `key` PK | `value` JSON, `updated_at` | maintenance bookkeeping (last purge, IP hash key) |
| `portfolios` | `profile_id` PK | `public_id` (unique), `doc` JSON, `generated` JSON, `facts_hash`, `version`, `published`, `created_at`, `updated_at`, `published_at`, `last_viewed_at`, `user_id` (copied from the profile when it is claimed; no foreign key, like `profile_id`) | `(public_id)`, `(updated_at)`, `(user_id)` |
| `users` | `id` PK (32 hex characters) | `email` (lower-cased, unique), `name`, `picture_url`, `google_sub` (unique), `created_at`, `last_login_at`, `locale`, `currency` (`INR` or `USD`) | Migration 0003. Removed by `DELETE /api/me`, which removes the account's children first |
| `auth_sessions` | `id` PK (sha256 of the cookie token) | `user_id` FK cascade, `created_at`, `expires_at`, `last_seen_at`, `ip_hash`, `user_agent` (at most 200 characters) | `(user_id)`, `(expires_at)`; purged once expired |
| `magic_links` | `token_hash` PK (sha256 of the token) | `email`, `created_at`, `expires_at`, `used_at` (null until spent), `ip_hash` | `(expires_at)`; purged once expired |
| `orders` | `id` PK; `provider_order_id` unique | `user_id` FK, set to NULL when the account is deleted; `provider` (`razorpay` or `stripe`), `product`, `currency`, `amount_minor`, `status` (`created`, `paid`, `failed`), `created_at`, `paid_at`, `raw` JSON (provider ids only) | Migration 0004. `(user_id)`; never purged, so a payment record outlives its account |
| `entitlements` | `user_id` PK, FK cascade | `credits`, `pass_until` (null), `updated_at` | Migration 0004. The row is the lock for every change to one account's balance |
| `credit_ledger` | `id` PK | `user_id` FK cascade, `delta`, `reason` (`purchase`, `match`, `free_monthly`, `refund`; `admin` is reserved, nothing writes it yet), `ref`, `created_at` | Migration 0004. `(user_id, created_at)`; append-only, so the balance is the sum of the deltas |
| `postings` | `hash` PK (sha256 of the normalised text) | `text`, `parsed` JSON (the Posting without its text), `created_at` | Migration 0005. No account: shared by everyone who pasted the same text; never purged |
| `matches` | `id` PK | `user_id` FK cascade (NULL for a match made without an account, until its profile is claimed), `profile_id` FK cascade, `posting_hash` FK (no action), `facts` JSON (the FactBook), `result` JSON, `band`, `blocked`, `version`, `created_at`, `updated_at` | Migration 0005; 0006 made `user_id` nullable. `(user_id, created_at)`, `(profile_id)` |
| `user_facts` | `id` PK (hash of profile id and fact id) | `user_id` FK cascade (NULL for something said without an account), `profile_id` FK cascade, `match_id` FK (set to NULL when the match goes), `question`, `answer`, `fact` JSON, `created_at` | Migration 0005; 0006 made `user_id` nullable. `(profile_id, created_at)`, `(user_id)`; a fact outlives the match it was said in |
| `variants` | `id` PK | `match_id` FK cascade, `kind` (`resume` or `cover`), `spec` JSON, `status` (`needs_user` or `ready`), `version`, `created_at`, `updated_at` | Migration 0005. Unique `(match_id, kind)` |
| `profile_facts` | `profile_id` PK, FK cascade | `facts_hash`, `facts` JSON, `created_at` | Migration 0005. One row per profile; a new hash replaces it |
| `discover_listings` | `id` PK (stable hash of source, board and external id) | `employer_id`, `source` (`greenhouse` or `lever`), `external_id`, `title`, `title_norm`, `work_mode`, `city`, `locality`, `lat`, `lng`, `precision`, `remote_india`, `location_text`, `apply_url`, `apply_host`, `posted_at` (null), `first_seen_at`, `last_seen_at`, `status` (`open` or `closed`), `closed_at` (null), `salary_text` (null), `years_min` (null), `entry_level`, `content_hash`, `posting_text` | Migration 0007. Employers' public postings only; no column holds anything about a person. `(status)`, `(city)`, `(employer_id)`; closed rows are deleted 30 days after closing |
| `discover_runs` | `id` PK | `started_at`, `finished_at` (null), `status` (`running`, `ok` or `failed`), `employers_ok`, `employers_failed`, `listings_open`, `listings_closed`, `note` (null) | Migration 0007. `(started_at)`. The single-flight check reads it: a `running` run under 20 minutes old blocks a new one |
| `jobsearch_runs` | `id` PK (32 hex) | `profile_id`, `city` (null), `remote`, `created_at`, `result` (JSON: the `JobSearchRun` the person saw), `postings` (JSON: shown postings' text by job id, server-side only) | Migration 0008. `(profile_id, created_at)`, `(created_at)`; purged after 30 days |
| `jobsearch_docs` | `id` PK (hash of profile, job, kind) | `profile_id`, `job_id`, `kind` (`prep` or `referral`), `created_at`, `body` (JSON) | Migration 0008. `(created_at)`; replaced by a rewrite; purged after 30 days |
| `interview_sessions` | `id` PK (32 hex) | `profile_id`, `kind`, `status` (`active` or `done`), `created_at`, `updated_at`, `version` (compare-and-swap), `state` (JSON: plan, phrased lines, turns, judgements, work, assistant log), `debrief` (JSON, null until done) | Migration 0009. `(profile_id, created_at)`, `(created_at)`; purged after 30 days |
| `prepare_plans` | `id` PK (32 hex) | `profile_id` FK cascade, `role_id`, `requirement` (null for a plan over the whole role), `content` (JSON: the `PreparePlan` without its state and version), `state` (JSON: hours a week, the move shown, done task keys, links), `version` (compare-and-swap on state saves), `created_at`, `updated_at` | Migration 0010. `(profile_id, role_id, requirement)`; goes with its profile, no retention step of its own |

### Retention (run at most every 6 hours, from the lifespan; replicas claim each run through a `kv` row, under a Postgres try-lock)

- **Profiles**: anonymous profiles (no `user_id`) not opened for 60 days are deleted, with their contexts and chats. A profile saved to an account (`user_id` set) is kept until the account is deleted.
- **Sample runs** ("Try a sample profile"): deleted once idle for 60 days, except the bundled Riya Kapoor persona. Chats of sample profiles, the persona's included, are deleted on the same window, because what visitors type to them is user text.
- **Sample corpus**: decision and Jev rows flagged `is_sample` that belong to no profile (CLI and eval runs) or to the kept persona, the states they reference, and the model-call rows of the kept persona are exempt from the payload and metadata steps. Visitor text is not exempt: chat-gate decisions and chat calls follow the normal windows, even for the persona.
- **Jev payloads** (states + raw JSON): purged after 30 days. Metadata (`decision_log` rows) kept for 180 days.
- **Rate limit hits**: deleted after 2 days.
- **Sign-ins and emailed links** (`auth_sessions`, `magic_links`): deleted once their expiry has passed. A used link that has not expired yet stays until it does.
- **Discover listings**: a listing its employer's board stopped listing is deleted 30 days after it was closed. The `discover_runs` log has no retention step yet.
- **Searches with AI**: `jobsearch_runs` and `jobsearch_docs` rows are deleted 30 days after they were written.
- **Practice interviews**: `interview_sessions` rows (transcript and debrief) are deleted `TAYYARI_INTERVIEW__RETENTION_DAYS` (30) days after they started.
- **Plans** (`prepare_plans`) follow their profile through the foreign key: an anonymous profile's purge and an account's deletion take its plans along; there is no window of their own.
- **Postings** are never purged yet. Matches, resumes, cover notes, the answers a person gave and profile facts are removed only with their account (`DELETE /api/me`) or their profile. Orders are kept when an account is deleted, with `user_id` cleared.

### Migrations (Alembic)

Migrations live in `storage/migrations/` inside the package. No external `alembic.ini`.

**Fast path** (cold starts): read `alembic_version` and compare to head. If equal, do nothing.

**Upgrade path**:
1. Start a transaction.
2. On Postgres: `SET LOCAL lock_timeout='10s'`, then `SELECT pg_advisory_xact_lock(<key>)`. This works behind Neon's pgbouncer (transaction-level lock).
3. Run `upgrade head`.
4. Commit (lock released).

If migration fails, the transaction rolls back and startup crashes. Readiness checks fail, so the load balancer keeps the old version running.

Revisions 0003 (accounts, and the owner columns on `profiles` and `portfolios`), 0004 (payments), 0005 (posting match), 0006 (anonymous posting match), 0007 (Discover: two new tables, no change to an existing one), 0008 (search with AI), 0009 (practice interviews) and 0010 (plans for roles) follow this rule.

**Additive-only rule**: migrations add tables or nullable columns only. No drops, renames, or constraint tightening after revision 0001 (unless a file carries a `# contract-phase` marker). Relaxing a constraint, as 0006 does by dropping `NOT NULL` from `matches.user_id` and `user_facts.user_id`, is allowed: it only widens what the old release also writes, and the old release never reads a row with a NULL owner. 0006 spells the DDL out (`ALTER COLUMN … DROP NOT NULL` on Postgres; on SQLite an edit of the table's SQL in `sqlite_master`) instead of calling `alter_column`, because an Alembic batch rebuild of `matches` on SQLite would cascade into `variants` and delete every stored resume.

## Cost guard

### Rate limits (per-IP fixed-window counters)

Each IP has atomic counters (one `INSERT … ON CONFLICT … RETURNING count` per window):

| Endpoint | Window | Limit | Global |
|----------|--------|-------|--------|
| `/api/analyze` | 1 min | 2 | — |
| | 1 hour | 6 | 60 per hour (cross-IP) |
| | 1 day | 15 | — |
| `/api/chat` | 1 min | 20 | — |
| | 1 hour | 120 | 600 per hour (all clients) |
| | 1 day | 400 | — |
| `/api/portfolio` | 1 min | 4 | — |
| | 1 hour | 12 | 120 per hour (all clients) |
| | 1 day | 30 | — |
| `/api/match` | 1 min | 0 (off by default) | — |
| | 1 hour | 10 per client, 10 per account | 120 per hour (all clients) |
| | 1 day | 30 per client, 30 per account | — |
| `/api/match`, no sign-in | 1 day | 3 per client (`match_anon`, `POST /api/match` only), on top of the rows above | — |

Limits are set by `TAYYARI_GUARD__*_PER_{MINUTE,HOUR,DAY}` and the `*_GLOBAL_PER_HOUR` settings; 0 turns a window off. A posting match is counted for its client address and, separately, for its account (`u:<user id>`); a match started without a sign-in is counted for its client address, and once more against `TAYYARI_GUARD__ANON_MATCH_PER_DAY` (a daily counter only, no in-memory bucket, no whole-service counter: `match` already has those). Re-checks, answers and downloads are not matches and are not counted. For IPv6 clients the same windows apply again to the client's /48 at four times the limit. Magic-link requests are limited per email and per client address (3 per 15 minutes, `TAYYARI_AUTH__MAGIC_PER_EMAIL` and `TAYYARI_AUTH__MAGIC_PER_IP`) through the same `rate_limit_hits` table, not through these windows.

**In-memory bucket** (per replica): rejects hammering before it reaches the DB. If the DB errors, fail open to the in-memory bucket and log.

**Responses**: 429 JSON with `Retry-After` header.

**Unlimited accounts.** `TAYYARI_GUARD__UNLIMITED_EMAILS` (comma-separated, or a JSON list; trimmed, lower-cased, exact match) names the sign-in emails of accounts that are never limited and never charged, for the owner's own test account. Both sign-in methods prove the person owns the address (a magic link is read from the inbox; Google must say `email_verified`), so the email is a safe key. `api.deps.unlimited(request, container)` answers per request: False with no lookup when the list is empty or the request carries no session cookie, otherwise the session's account is checked against the list (the session lookup is the one the route would do anyway, cached for the request, and the answer is cached too). A route passes the answer as `exempt=` to `RateLimiter.shed` and `check`, which then return at once: no in-memory token, no per-address, per-account or /48 counter, no whole-service hourly counter, on every route (the `chat` bucket's light reads included). Posting match skips `entitlements.consume` for such an account (nothing to refund) and the practice history omits `leftToday`; the work sample's assistant has no per-session message cap and speech has no per-session share of Sarvam seconds or characters. `/api/me` and `GET /api/billing/entitlements` carry `entitlements.unlimited: true` (absent for everyone else). **Still applied:** the daily spend ceiling and every spend reservation (the ledger stays true), the magic-link limits per email and per address (exempting them would let anyone flood that inbox through our sender), process capacity (the analysis semaphore, `UploadGate`, chat `StreamSlots`), a practice session's turn cap, and Sarvam's global daily budget (the owner's free credits; when it is spent the browser's speech takes over). The addresses are never logged: the startup line reports only `unlimited_accounts: <count>`.

### Spend ceiling (soft)

Daily ledger: `spend_daily` row per UTC day (scope: total/anthropic/jev). Incremented atomically with every `llm_calls` and `jev_calls` write. Read with 5 s cache.

Default ceiling: **$20/day UTC**. Reaching it returns 503 JSON before the stream starts. Mid-analysis, the run may finish up to 125% of the ceiling (in-flight grace), then emit an error event. Spend guard fails closed if DB is down (because profiles can't be saved anyway). A posting match reserves `TAYYARI_GUARD__EST_MATCH_USD` (0.06) before its posting is read, and a resume or cover note reserves it before it is written.

Also set a **spend limit on the Anthropic workspace** as a hard backstop outside our code.

### Client IP identification

The header named by `TAYYARI_GUARD__CLIENT_IP_HEADER` is read first when present (default `cf-connecting-ip`, which Cloudflare sets in front of FastAPI Cloud and overwrites any value the client sent). Otherwise `X-Forwarded-For` is read, counting `TAYYARI_GUARD__TRUSTED_PROXY_HOPS` entries from the right (default 3, the number measured on FastAPI Cloud); then the socket peer. IPv6 clients are counted per /64 network. HMAC-SHA256 of the IP (salt generated at first startup, stored in `kv` table). Never store the raw IP.

## Telemetry (FastAPI's OpenTelemetry)

FastAPI 0.142.2 and later record OpenTelemetry for every request with no code of ours; `api/app.py` (`TELEMETRY`) only narrows it:
- **What is recorded:** a span per API request (`GET /api/interview/sessions/{session_id}`) with child spans for dependency resolution, the endpoint, response serialization and each `BackgroundTasks` task; request count, duration and in-flight metrics. Spans keep the path and query string; profile ids there are random ids, not names.
- **Left out:** the health probes and everything outside `/api/` (the web app's files), so counts and error rates are the API's; the two sign-in URLs whose query string holds a one-time secret (`/api/auth/magic/verify`, `/api/auth/google/callback`), since FastAPI redacts only cloud-storage signatures from `url.query`; and FastAPI's own logs (`logs: False`), because they carry exception messages and stack traces past `core.redact`. Unhandled errors are still logged, redacted, by `api/errors.py`.
- **Where it goes:** on FastAPI Cloud the metrics reach the Metrics dashboard (request counts, error rates, response times; Pro plans) with nothing to set. Elsewhere nothing is exported until `FASTAPI_OTEL_AUTO_CONFIGURE=true`, `OTEL_SERVICE_NAME=tayyari` and `OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP over HTTP/protobuf; `OTEL_EXPORTER_OTLP_HEADERS` for a key) are set; since 0.143.0 the endpoint alone does nothing. The optional Logfire setup (`LOGFIRE_TOKEN`, `core/logging.py`) configures the global providers, and FastAPI then uses them.
- **Not traced yet:** model calls and Sarvam calls run inside the endpoint span without spans of their own; their cost and latency are in `llm_calls` and the structlog lines.

## Testing layout

Tests use **pytest** with **respx** (mocking HTTP) and **asgi-lifespan** (FastAPI lifespan fixture).

| Directory | Purpose | Mocks |
|-----------|---------|-------|
| `tests/unit` | Fast, isolated | FakeClaudeClient, FakeJevClient, FakeRepos, in-memory limits |
| `tests/integration` | Contract and integration | Same fakes, but real composition and flows |
| `tests/golden` | Regression on Sonnet + readers | Captured NDJSON baselines; compared after a rebuild |
| `tests/fakes` | Fake implementations | dict-backed repos, scripted Claude/Jev clients |
| `tests/data` | Fixtures | sample resumes, links, reader outputs |

Postgres-only tests (`tests/unit/storage`) run only when `TEST_DATABASE_URL` is set: a direct, non-pooler URL of a throwaway database. Without it they are skipped.

**Sample corpus**: `tayyari/sample/` (Riya Kapoor persona) is the source of truth. `cli make-sample` regenerates it with a live run. Golden baseline captures it and compares on any rebuild.

**Jev contract test**: mock `jev_http` and test both `HttpxJev` and `TypeSafeSdkJev` (if the SDK is pinned) against the same scripted server, verifying normalisation (noul confidence, choice normalization by option count, score mapping).

## Verification checklist

Run these before shipping Wave 4:

**Local:**
1. `npm run test:py` — contract, decision matrix, SSRF, chunk upload 413s, limiter concurrency, migrations, golden NDJSON, cancellation.
2. `npm run lint:py` — Ruff style.
3. `npm run typecheck:py` — mypy strict on new modules.
4. `npm run smoke` — Haiku call, Sonnet chat, cache read, PDF parse, no 400 retry on effort.
5. Manual: upload sample, check Jev first-read lines appear in ~2 s, graph renders, evidence log appears.
6. Manual: chat, check tool chips read "Searched the web". Restart API, continue thread; history survives.
7. Manual: check `llm_calls`, `jev_calls`, `decision_log` have rows. `spend_daily` reconciles.
8. Manual: trip guard (3 analyses under 2/hour → 429, tiny ceiling → 503).
9. `cli ingest-check` and `cli make-sample` succeed.
10. `npm run check` (full frontend + backend typecheck).

**Cloud (FastAPI Cloud + Neon):**
1. Deploy from `backend/`. Check `/api/health` (storage: postgres), `/api/sample-profile`, live browser analysis (curl -N).
2. Logs: migrations ran once across replicas.
3. Scale to zero, then chat. History intact.
4. Trip per-IP limit via proxy. Hashed IP matches real public IP.
5. Cold start latency on Neon.
6. Optional: `cli replay` on exported corpus.

## Configuration reference

See **[backend/.env.example](backend/.env.example)** for the complete list and defaults. Key variables:

- `ANTHROPIC_API_KEY` (required for analysis/chat)
- `JEV_API_KEY` (optional; enables Jev decisions)
- `DATABASE_URL` (Neon on cloud; SQLite locally if unset)
- `TAYYARI_*` (models, efforts, limits, Jev, guard settings)

Accounts, payments and the posting match (names only; the values are never written in docs or code):
- **Sign-in secrets:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (Google sign-in shows only when both are set), `RESEND_API_KEY` (email links). `RESEND_FROM` is the sender and is not a secret.
- **Payment secrets:** `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` (both for INR), `RAZORPAY_WEBHOOK_SECRET` (optional; enables the webhook), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (both for USD).
- **`TAYYARI_AUTH__*`:** `SESSION_DAYS` (30), `MAGIC_LINK_MINUTES` (15), `MAGIC_PER_EMAIL` and `MAGIC_PER_IP` (3), `COOKIE_NAME` (`tayyari_session`), `PUBLIC_URL` (default `FASTAPI_PUBLIC_URL`, else `http://localhost:5173`).
- **`TAYYARI_BILLING__*`:** `PACK3_INR`, `PASS30_INR`, `PACK3_USD`, `PASS30_USD` in minor units (paise, cents); 0 or unset means not on sale, and so does a price below the provider's minimum (100 paise, 50 cents). `FREE_MATCHES_PER_MONTH` (1).
- **`TAYYARI_MATCH__*`:** `MODEL` (unset: the reader model), `WRITER_MODEL` (unset: the same as `MODEL`), `MAX_POSTING_CHARS` (20000), `CALL_TIMEOUT_S` (90), `ALLOW_ANONYMOUS` (`auto`, `true` or `false`; `auto` allows a posting match without a sign-in exactly when the server offers no way to sign in).
- **Unlimited accounts:** `TAYYARI_GUARD__UNLIMITED_EMAILS` (empty: none; `a@x.com,b@y.com` or `["a@x.com","b@y.com"]`): sign-in emails with no rate limits and no charges (see Cost guard); the spend ceiling, sign-in link limits, capacity guards and Sarvam's daily budget still apply. Set it in `backend/.env` locally and in the cloud with `fastapi cloud env set TAYYARI_GUARD__UNLIMITED_EMAILS` (the owner runs that; real addresses stay out of the repo).
- **Posting-match guard:** `TAYYARI_GUARD__MATCH_PER_MINUTE` (0), `MATCH_PER_HOUR` (10), `MATCH_PER_DAY` (30), `MATCH_GLOBAL_PER_HOUR` (120), `EST_MATCH_USD` (0.06), `ANON_MATCH_PER_DAY` (3: matches per client address per day without a sign-in, 0 = off).
- **`TAYYARI_JOBSEARCH__*`** (no secrets of its own; it uses the Anthropic key): `ENABLED` (true), `MODEL` (claude-haiku-5-5), `SCOUTS` (3), `SEARCHES_PER_SCOUT` (3), `MAX_BOARDS` (30), `FETCH_CONCURRENCY` (8), `MAX_JUDGED` (12), `JUDGE_BATCH` (4), `PER_EMPLOYER` (3), `POSTING_CHARS` (6000), `BOARD_CACHE_MINUTES` (60), `REQUEST_TIMEOUT_S` (12). Its limits are guard settings: `JOBSEARCH_PER_MINUTE` 1, `_PER_HOUR` 4, `_PER_DAY` 8, `JOBSEARCH_GLOBAL_PER_HOUR` 40, `ANON_JOBSEARCH_PER_DAY` 3, `EST_JOBSEARCH_USD` 0.20, `EST_JOBSEARCH_DOC_USD` 0.02 (a prep page or referral note).
- **`TAYYARI_INTERVIEW__*`** (no secrets of its own): `ENABLED` (true), `MODEL` (claude-haiku-5-5), `MAX_TURNS` (60), `ASSISTANT_MESSAGES` (12), `POSTING_CHARS` (6000), `RETENTION_DAYS` (30), `HISTORY_LIMIT` (50). Its limits are guard settings: `INTERVIEW_PER_MINUTE` 2, `_PER_HOUR` 3, `_PER_DAY` 3 (per address and per account), `INTERVIEW_GLOBAL_PER_HOUR` 60, `INTERVIEW_AGAIN_PER_DAY` 20, `EST_INTERVIEW_USD` 0.03 (start), `EST_INTERVIEW_TURN_USD` 0.005 (an answer or an assistant message), `EST_INTERVIEW_END_USD` 0.02 (the debrief).
- **Prepare** has no settings group of its own: it runs on `TAYYARI_CHAT_MODEL` at `TAYYARI_CHAT_EFFORT`. Its limits are guard settings: `PREPARE_PER_MINUTE` 2, `_PER_HOUR` 10, `_PER_DAY` 30 (per address and per account; a plan written and a headline rewritten count, a stored plan handed back does not), `PREPARE_GLOBAL_PER_HOUR` 120, `EST_PREPARE_USD` 0.06 (the two calls of a plan), `EST_PREPARE_HEADLINE_USD` 0.02.
- **`SARVAM_API_KEY`** (secret, optional) and **`TAYYARI_SPEECH__*`**: `ENABLED` (true), `STT_MODEL` (saaras:v4), `STT_LANGUAGE` (en-IN), `TTS_MODEL` (bulbul:v4-flash), `TTS_SPEAKER` (simran_en_customer), `TTS_LANGUAGE` (en-IN), `TTS_CODEC` (mp3), `TTS_SAMPLE_RATE` (22050), `MAX_SEGMENT_SECONDS` (30), `MAX_SEGMENT_BYTES` (1,500,000), `STT_DAILY_SECONDS` (1800), `TTS_DAILY_CHARS` (6000), `STT_SESSION_SECONDS` (600), `TTS_SESSION_CHARS` (3000), `REQUEST_TIMEOUT_S` (20), `TTS_CACHE_ITEMS` (256), `TTS_PREFETCH` (true: make the interviewer's next lines in the background; needs the cache), `TTS_PREFETCH_FOLLOWUPS` (true: include the current question's follow-up and easier question), `TTS_PREFETCH_CONCURRENCY` (2, 1 to 8: lines made at once across the process). Guard: `SPEECH_PER_MINUTE` 40. Without the key, practice uses the browser's speech.
- **`TAYYARI_DISCOVER__*`** (no secrets; the boards are public): `ENABLED` (true; false answers 503 `discover_disabled` and fetches nothing), `REFRESH_HOURS` (6, how old the last good run may get before a request starts one), `FETCH_CONCURRENCY` (4), `REQUEST_TIMEOUT_S` (15), `MAX_LISTINGS_PER_EMPLOYER` (300: India listings kept per employer, known cities first, then remote, then the rest, newest first within each), `REGISTRY_PATH` (unset: the registry shipped in `discover/data/employers.json`).

`backend/.env.example` lists each of these with its default or an empty value.

For deployment to FastAPI Cloud, also see **[backend/DEPLOY.md](backend/DEPLOY.md)** (linking here per task rules).
