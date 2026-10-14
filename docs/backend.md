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

**Layering rule** (enforced by test): `core` < `contract` < `llm` < `decisions` < `analysis`/`chat`/`portfolio`/`match` < `api`. `match` sits with `portfolio`: it reuses the portfolio's line-cited fact extractor and sentence anchors rather than forking them. `auth` and `billing` depend only on `core`, `contract` and `storage.ports`. `storage` depends only on `core` and `contract`. `guard` depends on `storage.ports`. Services depend on Protocols, so every unit can be tested with fakes.

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

The posting is stored once per normalised text (`postings`), so a text read before is not read again, by anyone. The person's facts come from the portfolio extractor, cached per profile (`profile_facts`, keyed by the hash of the sources). The match keeps its facts (`matches.facts`, the FactBook) and its result.

**After the stream.**
- `GET /api/match/{id}` → `MatchDetail`: the posting with its text, the facts its rows cite, and the result (band, counts, confidence, coverage, gates, radar, questions). `GET /api/matches` → `MatchList`, newest first (empty with no sign-in).
- `POST /api/match/{id}/answers` `{answers: [{questionId, text}]}`: up to three answers of at most 1,500 characters. Each becomes a fact in the person's own words (`user_facts`, with its question) that later matches of the same profile can cite. Only the rows and gates the answered questions name are checked again; the result's version goes up; the response is the new `MatchDetail`. An answer to an unknown or closed question is ignored, and with no usable answer the route is 400. A save from a stale tab is 409 `match_changed`. Answers never consume.
- **Resume.** `POST /api/match/{id}/resume` streams `variant` `{variant}` then `done`. A resume already built for the match is returned without reserving anything. Otherwise the spend is reserved, the resume is composed from cited facts (relevance scores, code selection, phrasing, verification) and stored with status `needs_user` while any line waits for the person, else `ready`. `GET /api/match/{id}/resume` → `{variant}`, or 404 until one is built. `PATCH /api/match/{id}/resume` `{decisions: [...]}` (up to 40) applies each choice: keep, soften, drop, a figure for a line (`fill`, which becomes a user fact), or the posting's own term (`use_posting_term`). Nothing is applied unless every decision fits; otherwise 422 `decision_refused`. The variant is re-verified, stored and returned.
- **Downloads.** `GET /api/match/{id}/resume.pdf` and `.docx` answer 409 `export_blocked` while any line still needs the person. Otherwise they return the file with `Content-Disposition: attachment`, `X-Robots-Tag: noindex` and a name like `<person>-resume.pdf`. The checklist stored with the variant is refreshed when it changes.
- **Cover note.** `POST /api/match/{id}/cover` (no body) returns the stored note without reserving anything; otherwise it reserves the spend, writes the note from (requirement, fact) pairs with a plain sentence for each gap, 250 to 400 words, no generic openers, and stores it. It is 422 `cover_unavailable` when no covered requirement has facts to write from. `GET /api/match/{id}/cover` → `{cover}`, or 404 until one is written; `GET /api/match/{id}/cover.pdf` → the file.
- **Built once.** A stored resume or cover note is returned as it is, so answers given later do not change it. There is no rebuild control yet.

**Cost.** About half a cent per match on Haiku 5.5, as measured. The reservation for a match, and for a resume or cover note that is written, is `TAYYARI_GUARD__EST_MATCH_USD` (0.06).

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

### Retention (run at most every 6 hours, from the lifespan; replicas claim each run through a `kv` row, under a Postgres try-lock)

- **Profiles**: anonymous profiles (no `user_id`) not opened for 60 days are deleted, with their contexts and chats. A profile saved to an account (`user_id` set) is kept until the account is deleted.
- **Sample runs** ("Try a sample profile"): deleted once idle for 60 days, except the bundled Riya Kapoor persona. Chats of sample profiles, the persona's included, are deleted on the same window, because what visitors type to them is user text.
- **Sample corpus**: decision and Jev rows flagged `is_sample` that belong to no profile (CLI and eval runs) or to the kept persona, the states they reference, and the model-call rows of the kept persona are exempt from the payload and metadata steps. Visitor text is not exempt: chat-gate decisions and chat calls follow the normal windows, even for the persona.
- **Jev payloads** (states + raw JSON): purged after 30 days. Metadata (`decision_log` rows) kept for 180 days.
- **Rate limit hits**: deleted after 2 days.
- **Sign-ins and emailed links** (`auth_sessions`, `magic_links`): deleted once their expiry has passed. A used link that has not expired yet stays until it does.
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

Revisions 0003 (accounts, and the owner columns on `profiles` and `portfolios`), 0004 (payments), 0005 (posting match) and 0006 (anonymous posting match) follow this rule.

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

### Spend ceiling (soft)

Daily ledger: `spend_daily` row per UTC day (scope: total/anthropic/jev). Incremented atomically with every `llm_calls` and `jev_calls` write. Read with 5 s cache.

Default ceiling: **$20/day UTC**. Reaching it returns 503 JSON before the stream starts. Mid-analysis, the run may finish up to 125% of the ceiling (in-flight grace), then emit an error event. Spend guard fails closed if DB is down (because profiles can't be saved anyway). A posting match reserves `TAYYARI_GUARD__EST_MATCH_USD` (0.06) before its posting is read, and a resume or cover note reserves it before it is written.

Also set a **spend limit on the Anthropic workspace** as a hard backstop outside our code.

### Client IP identification

The header named by `TAYYARI_GUARD__CLIENT_IP_HEADER` is read first when present (default `cf-connecting-ip`, which Cloudflare sets in front of FastAPI Cloud and overwrites any value the client sent). Otherwise `X-Forwarded-For` is read, counting `TAYYARI_GUARD__TRUSTED_PROXY_HOPS` entries from the right (default 3, the number measured on FastAPI Cloud); then the socket peer. IPv6 clients are counted per /64 network. HMAC-SHA256 of the IP (salt generated at first startup, stored in `kv` table). Never store the raw IP.

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
- **Posting-match guard:** `TAYYARI_GUARD__MATCH_PER_MINUTE` (0), `MATCH_PER_HOUR` (10), `MATCH_PER_DAY` (30), `MATCH_GLOBAL_PER_HOUR` (120), `EST_MATCH_USD` (0.06), `ANON_MATCH_PER_DAY` (3: matches per client address per day without a sign-in, 0 = off).

`backend/.env.example` lists each of these with its default or an empty value.

For deployment to FastAPI Cloud, also see **[backend/DEPLOY.md](backend/DEPLOY.md)** (linking here per task rules).
