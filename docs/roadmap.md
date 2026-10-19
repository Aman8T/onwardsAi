# Roadmap and feature specs

The build plan for the paid-user phase, written so any agent can pick up a wave. Read `project-soul.md` and `CLAUDE.md` first. Decisions here were taken on 2026-10-11 with the user; the open items are marked.

Design canvases (private; ask the user to share if you can't open them):
- Posting match: https://claude.ai/artifact/6Prr8pC4H6xPxs7JQfhUjL (Paste, Main match results, Phone, Resume for this role, Cover note, Free and paid)
- Calm visual layer: https://claude.ai/artifact/HLxXL14nAUeNHhLFNhozp9 · Portfolio kit: https://claude.ai/artifact/XH5UWJssQeNnwQiByk9fDa · Cursor light: https://claude.ai/artifact/USZp9ELtqSY6sQu2gtYJCn

Research behind the specs: `docs/research/open-source-resume-tools.md` (§4 is the design this plan implements).

## Order of work

| Wave | What | Depends on | Owner model | Status |
|---|---|---|---|---|
| F1 | Accounts and per-user persistence | — | Sonnet | committed 03988c7, deployed 2026-10-12 |
| M1 | Posting-match core (pure services, contracts, prompts, tests with fakes; no routes, no tables) | — (parallel with F1) | Sonnet | committed 03988c7, deployed 2026-10-12 |
| F2 | Payments and entitlements (Razorpay INR, Stripe USD) | F1 | Sonnet | committed 03988c7, deployed 2026-10-12 |
| M2 | Posting-match persistence and routes, guard, entitlement checks | F1, M1 | Sonnet | committed 03988c7, deployed 2026-10-12 |
| M3 | Posting-match frontend (Paste, Match, Resume, Cover, Pricing, sign-in) | M1 contract (mock data), then M2 | Sonnet | committed 03988c7, deployed 2026-10-12 |
| T | Tests, docs, walkthrough steps, `.env.example`, README | each wave | Haiku | committed 03988c7, deployed 2026-10-12 |
| R1 | Shareable radar card (SVG + OG PNG) | F1 | later | not started |
| I1 | Evidence-grounded mock interview (practice) | M2, D3 | Opus + Sonnet | committed 659b7d6, deployed 2026-10-17 |
| P1 | Prepare: "help me prepare" answered as typed blocks, not text | the graph's roles | Opus + Fable | designed 2026-10-18, building |
| X1 | ESCO + O*NET skill table, canonical skill ids | M2 | later | not started |
| D1 | Discover: personal, location-aware jobs with direct apply links | M2 | Opus + Sonnet | v1 built 2026-10-16 (employers' own boards, no map); rest in docs/discover-later.md |
| L1 | Sign in to the app with the Leafout account (the site's Supabase login) | F1, app.leafout.in live | Opus + Sonnet | built 2026-10-19, committed, not yet deployed |

Shared files are edited by one wave at a time, in this order: F1 → M2 → F2 for `storage/tables.py`, `storage/ports.py`, `core/settings.py`, `container.py`, `api/routes/__init__.py`, `api/security.py`, `backend/.env.example`. M1 touches none of them. Dependencies were added by the orchestrator (`authlib`, `reportlab`, `stripe`, `razorpay`; `pypdf` and `python-docx` were already present): no wave edits `pyproject.toml`.

Each wave ends with: `npm run check:all` green, new tests for every new unit, a report listing what was verified and what was not. No commits, deploys or secrets.

---

## F1 · Accounts and per-user persistence

**Goal.** A person can sign in with Google or an emailed link, their profiles and portfolios belong to them, and anonymous use keeps working for the first analysis.

**Decisions.** Own auth in the backend (no third-party auth vendor): Google OAuth through `authlib` plus an emailed magic link; server-side sessions in Postgres with an HttpOnly cookie. Email through Resend's HTTP API (`RESEND_API_KEY`); without a key in local dev, the link is logged and returned in the response so the flow can be tested. Analysis stays open to anonymous users (rate-limited by IP, as now); saving, posting match and payments need an account.

**Files owned.** `backend/tayyari/auth/` (new: `service.py`, `google.py`, `magic.py`, `sessions.py`, `email.py`), `backend/tayyari/api/routes/auth.py`, `backend/tayyari/api/routes/me.py`, `backend/tayyari/storage/repos/users.py`, migration `0003_users.py`, `backend/tayyari/contract/auth.py`, `shared/auth.ts`, tests under `backend/tests/unit/auth`, `backend/tests/integration/test_auth_routes.py`, fakes in `backend/tests/fakes`; plus the shared files listed above (append only). Frontend: `src/auth/` (new: `SignIn.tsx`, `useMe.ts`, `AccountMenu.tsx`), `src/styles/auth.css`, small edits in `src/store.ts`, `src/App.tsx`, `src/views/graph/GraphHeader.tsx`, `src/views/Onboarding.tsx` (header account control), `src/lib/api.ts`.

**Tables (migration 0003, additive).**
- `users`: `id` (uuid hex PK), `email` (text, unique on lower(email)), `name` (text null), `picture_url` (text null), `google_sub` (text null, unique), `created_at`, `last_login_at`, `locale` (text null), `currency` (text null: `INR` | `USD`).
- `auth_sessions`: `id` (text PK: sha256 hex of the random cookie token), `user_id` FK, `created_at`, `expires_at`, `last_seen_at`, `ip_hash` (text null), `user_agent` (text null, ≤ 200 chars).
- `magic_links`: `token_hash` (text PK), `email` (text), `created_at`, `expires_at`, `used_at` (null), `ip_hash` (null).
- `profiles.user_id` (text null, FK users.id, index) and `portfolios.user_id` (text null, index): added as nullable columns.

**Session cookie.** Name `tayyari_session`; value is a 32-byte urlsafe random token; only its sha256 is stored. `HttpOnly`, `SameSite=Lax`, `Secure` in prod, `Path=/`, 30 days (`TAYYARI_AUTH__SESSION_DAYS`), sliding: `last_seen_at` refreshed at most hourly. The existing `CrossSiteGuard` stays the CSRF defence for state-changing requests.

**Routes.**
- `GET /api/auth/google/start?next=/` → 302 to Google (state in a short-lived signed cookie; `next` restricted to a same-origin path).
- `GET /api/auth/google/callback` → verifies, upserts the user by `google_sub` then by email, creates a session, sets the cookie, 302 to `next`.
- `POST /api/auth/magic` `{email}` → 202 always (no account enumeration); sends a link valid 15 minutes (`TAYYARI_AUTH__MAGIC_LINK_MINUTES`); per-email and per-IP rate limits (3 per 15 min).
- `GET /api/auth/magic/verify?token=…` → single use; creates the user if needed; sets the cookie; 302 to `/`.
- `POST /api/auth/logout` → deletes the session, clears the cookie.
- `GET /api/me` → `{user: {id, email, name, pictureUrl, currency}, profiles: [{id, createdAt, goal, personName}], entitlements: {credits, passUntil, freeMatchAvailable}}` (entitlements are zeros until F2 fills them; expose the shape now).
- `POST /api/me/claim` `{profileId}` → attaches an unclaimed profile (and its portfolio) to the signed-in user; 409 if owned by someone else.
- `PATCH /api/me` `{currency?, name?}`.

**Settings** (`core/settings.py`, group `AuthSettings`, prefix `TAYYARI_AUTH__`): `session_days=30`, `magic_link_minutes=15`, `cookie_name="tayyari_session"`, `public_url` (default from `FASTAPI_PUBLIC_URL`, else `http://localhost:5173` in local). Secrets (no prefix, `SecretStr`): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `RESEND_API_KEY`, `RESEND_FROM` (not secret). Document all in `backend/.env.example`. Google sign-in is hidden in the UI when the client id is unset; magic link works without Resend in local (link logged and returned under `TAYYARI_ENV=local` only).

**Dependency.** `api/deps.py` gains `get_current_user(request) -> User | None` and `require_user`. Routes that persist (portfolio save, match) use `require_user`; analysis does not. The user id, never the email, goes into logs.

**Frontend.** A sign-in sheet (Google button, email field, "we'll email you a link") reachable from the header's account control and shown after the first analysis as a quiet "Save this to your account" invitation (same style as the Portfolio invite in `src/views/graph/PortfolioInvite.tsx`). `useMe()` loads `/api/me` once and exposes `user`, `entitlements`, `refresh()`. After sign-in, an unclaimed profile in the store is claimed automatically. Account menu: name, "Your profiles", "Sign out". Follows `design.md`; bottom sheet under 760 px.

**Tests.** Google callback with a fake token response (respx); magic link issue/verify/expiry/single use; session expiry and sliding refresh; claim conflicts; `/api/me` shape; cookie flags in prod vs local; rate limit on `/api/auth/magic`.

**Not in F1.** Phone OTP, password login, account deletion UI (add a `DELETE /api/me` that deletes the user's rows; UI later), email change.

---

## F2 · Payments and entitlements

**Goal.** A signed-in person can buy a pack of three postings or a 30-day pass, in INR through Razorpay or in USD through Stripe, and the product knows what they are entitled to.

**Decisions.** One-time payments only in v1: `pack3` (3 posting credits, no expiry) and `pass30` (unlimited postings for 30 days from purchase; renewal is a new purchase, no auto-renew). Prices are settings, unset by default, which hides paid plans in the UI ("coming soon") and makes checkout return 409. Free allowance: one posting match per calendar month per user. Currency: the user's `currency` if set, else INR when the browser locale or timezone is India, else USD; the user can toggle.

**Files owned.** `backend/tayyari/billing/` (new: `service.py`, `razorpay.py`, `stripe_gw.py`, `entitlements.py`, `products.py`), `backend/tayyari/api/routes/billing.py`, `storage/repos/billing.py`, migration `0004_billing.py` (0005 is M2's), `contract/billing.py`, `shared/billing.ts`, tests; shared files (append). Frontend: `src/billing/` (`PricingView.tsx`, `Checkout.tsx`, `useEntitlements.ts`), `src/styles/billing.css`, route `/pricing`.

**Tables (0004).** `orders` (`id` PK, `user_id` FK, `provider` razorpay|stripe, `provider_order_id` unique, `product`, `currency`, `amount_minor` int, `status` created|paid|failed|refunded, `created_at`, `paid_at` null, `raw` JSON null), `entitlements` (`user_id` PK, `credits` int default 0, `pass_until` timestamptz null, `updated_at`), `credit_ledger` (`id` PK, `user_id`, `delta` int, `reason` purchase|match|refund|free_monthly|admin, `ref` text null, `created_at`; index `(user_id, created_at)`).

**Routes.** `POST /api/billing/checkout` `{product, currency}` → Razorpay: creates an order (REST, basic auth) and returns `{provider:"razorpay", orderId, amount, currency, keyId}` for Checkout.js; Stripe: creates a Checkout Session and returns `{provider:"stripe", url}`. `POST /api/billing/webhooks/razorpay` and `/stripe`: raw body, signature verified (HMAC with the webhook secret; Stripe's `construct_event`), idempotent on `provider_order_id`, mark paid and credit the ledger. `GET /api/billing/entitlements` → `{credits, passUntil, freeMatchAvailable, prices: {INR: {pack3, pass30}, USD: {...}}}`. `POST /api/billing/verify` `{razorpay_order_id, razorpay_payment_id, razorpay_signature}` for the client-side handoff after Checkout.js (verified server-side; the webhook remains the source of truth).

**Entitlement use.** `entitlements.consume(user_id, match_id)` in a transaction: pass active → free; else free monthly not used this month → `free_monthly`; else credits > 0 → `-1`; else `PaymentRequired` (402 JSON `{reason}`). Re-checks after gap answers and resume/cover generation for an existing match never consume.

**Settings** (`BillingSettings`, `TAYYARI_BILLING__`): `pack3_inr`, `pass30_inr`, `pack3_usd`, `pass30_usd` (minor units, 0 = unset), `free_matches_per_month=1`. Secrets: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`. CSP (`api/security.py`): `script-src` and `frame-src` gain `https://checkout.razorpay.com` and `https://api.razorpay.com` (connect) only on the pricing/checkout pages if possible, else globally with a comment. Webhook routes are exempt from `CrossSiteGuard` (no cookie, signature instead) and from the SPA fallback.

**Frontend.** Pricing page per the canvas: three columns, currency toggle, prices from `/api/billing/entitlements` (placeholders hidden when unset), Razorpay Checkout.js loaded only on that page, Stripe redirect. An entitlement chip in the match screens ("2 postings left", "Pass until 12 Nov", "1 free this month").

**Tests.** Webhook signature pass/fail; idempotent replay; ledger arithmetic; consume order (pass → free monthly → credits → 402); checkout 409 without prices; currency default rules.

**Built beyond this spec (2026-10-14).** The Razorpay calls use the official `razorpay` SDK in place of the REST calls, run in a worker thread because the SDK is synchronous. Razorpay is offered once the key id and key secret are set; the browser hand-off settles the order at once, and the webhook (when its secret is set) is the backstop for a browser that never came back. A configured price below the provider's minimum (100 paise, 50 cents) counts as not on sale and is logged at start. The pricing page handles Razorpay's `payment.failed` event: it says "The payment did not go through." while the modal stays open, and "The payment did not go through. Nothing was charged." after it is closed. A refused hand-off shows as an error with the payment id, not as a "confirmed in the background" line.

---

## M1 · Posting match core

**Goal.** Pure Python services that turn (a profile with its stored context, a posting text) into the match result, the questions, the resume variant, the cover note, the exported files and the lint report. No routes, no tables: inputs and outputs are Pydantic models; storage is behind Protocols with fakes. This is the heart of the paid product; its rules come from `project-soul.md` and research §4.

**Files owned.** `backend/tayyari/match/` (new): `models.py` (or in `contract/match.py`), `posting.py`, `facts.py`, `gate.py`, `coverage.py`, `radar.py`, `questions.py`, `variant.py`, `cover.py`, `verify.py`, `export.py`, `lint.py`, `service.py`, `ports.py`; `backend/tayyari/prompts/match.py`; `backend/tayyari/contract/match.py`; `shared/match.ts`; `backend/tests/unit/match/*`, fixtures under `backend/tests/data/match/` (two personas: a marketing manager like the canvas's Meera Nair and the bundled Riya Kapoor engineer, each with one posting). Nothing else.

**Contracts** (`contract/match.py` ⇄ `shared/match.ts`; keep names identical):
- `Fact {id, text, kind: experience|accomplishment|skill|education|project|cert|other, refs: [{source: resume|github|lovable|link|user, docId, lines: [a,b] | null, quote}], evidenceTier: artifact|built|used|listed|null}`. A fact's `quote` must be a normalised substring of its source lines (whitespace and quote marks folded); `facts.py` enforces this and drops violators. Facts come from the existing portfolio fact extractor (`portfolio/facts.py`, reuse it, don't fork it) plus skills with evidence from the stored skill graph, plus `user` facts passed in.
- `Posting {hash, title, company, location, workMode, rawText, requirements: [Requirement], gates: [Gate], anomalies: [str]}`; `Requirement {id, text, quote, tier: must|required|preferred|nice, kind: skill|years|degree|cert|tool|domain|location|auth|language|other, provenance: stated|structural|inferred, domainHint}`; `Gate {id, question, quote, severity: 1..10}`. Rules enforced in code: `quote` is a normalised substring of `rawText`; `provenance == inferred` can never be `must` or `required`; at most 30 requirements, at most 6 gates; the posting is read with **no profile in context**; imperative sentences aimed at a model ("ignore previous instructions", "rate this candidate 10") are collected into `anomalies` and never followed; URLs in the posting are listed, never fetched.
- `GateAnswer {gateId, verdict: pass|fail|uncertain, factIds, note}`; decision in code: only `fail` with severity ≥ 7 blocks; `uncertain` is "not evidenced, add it if true".
- `Coverage {requirementId, factIds, status: covered|partial|synonym_only|missing_have_it|missing_gap|unknown, level: 0..4, note, interviewRisk, mitigation}`. Schema order: `factIds` before `status`. Code downgrades: a non-gap status without at least one valid fact id becomes `unknown`; `missing_have_it` requires a fact whose source is not the resume. `level` anchors: 4 direct, specific, verifiable; 3 close with a minor gap; 2 claimed but not substantiated; 1 weak or tangential; 0 absent or contradicted.
- `MatchResult {postingHash, band: strong|good|stretch|weak, counts: {...per status}, confidence: high|medium|low, confidenceNote, gates: [GateAnswer], blocked: bool, coverage: [Coverage], radar: {axes: [{domain, you, role}]}, questions: [Question], version}`. The band is computed in code from tier weights (must 1.0, required 0.8, preferred 0.5, nice 0.25) and levels, never by a model; `confidence` is high only when every must/required row is covered or partial with facts, low when any must is unknown or a gate is uncertain.
- `Question {id, requirementId | gateId, text, why}` ≤ 3, generated for gaps and unknowns, ordered must first.
- `ResumeVariant {header, summary: Bullet, skills: [{group, items: [SkillItem]}], experience: [{entryFactId, title, org, dates, bullets: [Bullet]}], education: [...], decisions: [Decision], changes: [Change], lint: LintReport | null}`; `Bullet {id, text, factIds, verdict: exact|reframed|stretch, placeholders: [str], status: verified|needs_user|dropped}`; `Decision {bulletId, kind: stretch|number|confirm_term, prompt, options}`; `Change {kind: surfaced|reordered|reworded|left_out, text, factIds}`.
- `CoverNote {paragraphs: [{text, requirementIds, factIds, kind: match|gap|logistics|close}], wordCount, lint}`.
- `LintReport {items: [{id, ok: bool, label, detail, location}]}` for: every sentence cites a fact; every number in the output exists in a cited fact; single column; standard headings; contact as literal text; ASCII date ranges with both ends; page count; text layer round-trips every verified sentence.

**Pipeline (`service.py`).**
1. `read_posting(text)` → Haiku structured output → code checks (above) → `Posting`. Cache key: sha256 of normalised text (the caller stores it).
2. `build_facts(profile, context, user_facts)` → `[Fact]` with evidence tiers (artifact when a GitHub/Lovable source backs it, built for a project with an outcome, used for an experience bullet, listed for a bare skills-list mention).
3. `answer_gates(posting.gates, facts)` → one Haiku call; severity and consequences withheld from the prompt; verdicts validated; decision in code.
4. `assess_coverage(posting.requirements, facts)` → Haiku per batch of requirements (≤ 10 per call, concurrent), schema order enforced, code validation and downgrades, band and confidence computed.
5. `build_radar(profile, posting.requirements, placed)` → role expectation per existing domain from requirement weights; "you" = the profile's domain mastery. No ESCO yet (wave X1). **Changed 2026-10-17 (the user: "the role's graph tilts towards Other, which defies the purpose"):** a `match.areas` Haiku call, run beside gates and coverage, places each requirement in one of the person's own areas or none (citing the closest skill first); code falls back to the hint and terms for anything unplaced; requirements in no area are listed (`Radar.outside`, named under the chart) instead of forming an "Other" axis, and each axis lists its `asks`. Measured on four real AI-engineering postings against one profile: from 5–9 requirements unplaced per posting to 1–2 (a degree, a language).
6. `make_questions(coverage, gates)` ≤ 3.
7. `compose_variant(facts, posting, coverage)`: Haiku scores each fact 0–100 for relevance to the requirement table (never rewrites); code selects (summary from the top facts; up to 5 bullets per most recent entry, 3 per older; skills grouped by the posting's kinds; `missing_have_it` facts surfaced; gaps never added), then Haiku phrases each bullet from its facts with the rules: posting's exact term only when a cited fact supports it; no new numbers (use `[x]` placeholders → `needs_user`); scope verbs (led, owned, managed, built, contributed) must match the fact → otherwise `stretch`. `verify.py` checks each sentence (reuse `portfolio/ground.py` anchors: numbers with units, names, dates, multipliers; plus a scope-verb check and a skill-stuffing check). Decisions are listed; export is blocked while any `needs_user` remains.
8. `write_cover(facts, posting, coverage, user_facts)` → paragraphs from (requirement, fact) pairs; a plain gap sentence for each must/required gap; a logistics paragraph only from `user` facts; 250–400 words; no generic openers (a small banned-phrase list); the same verifier.
9. `export(variant)` → DOCX (`python-docx`: single column, standard headings, literal "•", plain date ranges) and PDF (`reportlab` platypus: one column, Helvetica, headings as plain capitals, real text) → bytes. `lint(variant, pdf_bytes, docx_bytes)` → extract text with `pypdf` / `python-docx`, run the `LintReport` checks.

**Prompts** (`prompts/match.py`). Builders for: posting reader, gate answers, coverage batch, relevance scoring, bullet phrasing, cover note. Every prompt wraps the posting and the facts in delimited untrusted-data blocks and states the evidence rules verbatim from `project-soul.md`. Keep each under ~700 words. Thinking off (extraction-type calls).

**Tests.** With `FakeClaudeClient` scripts: quote-substring enforcement drops a fabricated quote; inferred-never-must; a status without fact ids downgrades to unknown; band arithmetic; confidence rules; gate decision table; a posting with injected instructions yields anomalies and no obedience; variant: a number not in facts becomes a placeholder and blocks export; a scope-verb escalation becomes `stretch`; a gap skill never appears in skills; cover note word count and banned openers; export round-trip lint on both personas; a known-bad fixture for every lint item proving it fires.

**Cost target.** Under $0.05 per match on Haiku 5.5 for a 14-requirement posting (posting read ≈ 1 call, gates 1, coverage 2, relevance 1, phrasing ≤ 3, cover 1); record every call with purpose `match.*`.

---

## M2 · Posting match persistence, routes, guard

**Depends on F1 and M1.** Files: `storage/tables.py` (append), `storage/ports.py` (append), `storage/repos/match.py`, migration `0005_match.py`, `api/routes/match.py`, `guard` additions, `container.py` wiring, settings group `MatchSettings`, integration tests.

**Tables (0005).** `postings` (`hash` PK, `text`, `parsed` JSON, `created_at`), `matches` (`id` PK, `user_id` FK, `profile_id` FK, `posting_hash` FK, `result` JSON, `band`, `blocked`, `version` int, `created_at`, `updated_at`; index `(user_id, created_at)`), `user_facts` (`id` PK, `user_id`, `profile_id`, `match_id` null, `question`, `answer`, `fact` JSON, `created_at`), `variants` (`id` PK, `match_id` FK, `kind` resume|cover, `spec` JSON, `status`, `version`, `created_at`, `updated_at`; unique `(match_id, kind)`).

**Routes** (all `require_user`, profile must belong to the user).
- `POST /api/match` `{profileId, text?, url?}` → NDJSON stream: `posting`, `gates`, `coverage` (batches), `radar`, `questions`, `done{matchId}` or `error`. A `url` goes through the existing ingest (SSRF guard, public pages only; LinkedIn and Naukri hosts return a friendly "paste the text" error). Entitlement consumed in the same transaction as the `matches` insert (F2's `consume`; until F2 lands, a `FreeEntitlements` stub allows one per month).
- `GET /api/match/{id}` → `MatchResult` + posting summary + variant statuses. `GET /api/matches` → the user's list.
- `POST /api/match/{id}/answers` `{answers: [{questionId, text}]}` → stores `user_facts`, re-runs gates and coverage for affected rows only, bumps `version`, returns the new result. Never consumes.
- `POST /api/match/{id}/resume` → stream `variant` events then `done`; `PATCH /api/match/{id}/resume` `{decisions: [{bulletId, choice, value?}]}` → applies keep/soften/drop, numbers and term confirmations, re-verifies, returns the variant; `GET /api/match/{id}/resume.pdf` and `.docx` → 409 while decisions are pending, else the file (`Content-Disposition`, `X-Robots-Tag: noindex`).
- `POST /api/match/{id}/cover` → `CoverNote`; `GET .../cover.pdf`.

**Guard.** Per-IP and per-user limits (`match_per_hour=10`, `match_per_day=30`, global `match_global_per_hour=120`), `est_match_usd=0.06` reserved before the stream; export and answers are cheap and unreserved. Posting text capped at 20k characters; URLs fetched with the ingest limits.

**Built beyond this spec** (committed 03988c7, deployed 2026-10-12):
- `matches.facts` holds the person's `FactBook` as it stood when the match was made: the facts its rows cite, and what the resume is composed from. Facts said later go to `user_facts` and are merged in whenever the match is opened.
- The `profile_facts` table caches the portfolio fact extractor's output per profile, keyed by the hash of the sources, so a second match on the same profile does not read the sources again. An extraction that partly failed is not cached.
- The exact shapes are in `backend/tayyari/contract/match_api.py`. The `POST /api/match` stream is `posting` (with the facts), then `coverage` (one line per batch of rows), `gates`, `radar`, `questions`, and `done` `{matchId, createdAt}`, or `error` `{message, code}`. The resume stream is `variant`, then `done`. Answers take up to three `{questionId, text}` items of at most 1,500 characters; resume decisions take up to 40.
- Resume and cover note are idempotent: a stored one is returned as it is, without reserving spend or calling a model.
- Pasted boards: a link to LinkedIn (`linkedin.com` and `lnkd.in`), Naukri, Indeed, Glassdoor, foundit or Shine is refused with 422 `posting_link_blocked` before anything is fetched; the person pastes the text instead.
- Refund rule: the entitlement taken for a match is given back when the match fails before it is stored, or the client leaves first; once the match is stored the charge is kept. Re-checks, resumes, cover notes and downloads never consume an entitlement.
- Anonymous matching (committed aa02483, deployed 2026-10-12): `TAYYARI_MATCH__ALLOW_ANONYMOUS` (`auto`: on exactly when the server offers no way to sign in), migration 0006 (`matches.user_id` and `user_facts.user_id` nullable), `anonymousMatch` on `GET /api/me`. A visitor with no sign-in matches a profile nobody has claimed or a sample run, opens the match by its id, and consumes no entitlement; claiming the profile gives the matches to the account. The rules are in `docs/backend.md` (Posting match).

---

## M3 · Posting match frontend

**Depends on M1's contract (develop against a fixture first) and M2.** Files: `src/match/` (`PasteView.tsx`, `MatchView.tsx`, `RequirementTable.tsx`, `GateList.tsx`, `Questions.tsx`, `ResumeView.tsx`, `CoverView.tsx`, `EntitlementChip.tsx`, `useMatch.ts`), `src/styles/match.css`, routes in `src/store.ts` / `src/App.tsx`, a "Match" chip in `src/views/graph/GraphHeader.tsx` and an invitation on the graph screen, `src/lib/api.ts` additions, a dev fixture page `/dev/match` fed by `backend/tests/data/match/*.json` copied to `src/match/dev/`.

**Screens** follow the canvas board by board: the paste screen (text or link, source-host chip, "read as data", first posting free), the match screen (band, counts, confidence, radar overlay using `components/Radar.tsx` with two series, gate list, requirement table sorted by tier then unmet, three questions), the resume screen (preview, decisions with Keep/Soften/Drop, number input, term confirmation, "what changed and why", the checklist, downloads disabled until decisions are settled), the cover note screen (paragraph ⇄ sources), the pricing page (F2). Phone layout per the Phone board: cards instead of table rows, bottom sheets for popovers. Every popover and chip keeps the existing keyboard patterns.

**Checks.** `npm run check` (restraint, palette, build), a parity script asserting the requirement table renders every `Coverage` row and every citation, the walkthrough gains a step 12 (paste → match → answers → resume decisions → download blocked/unblocked → cover).

---

## Later waves (specs to be written when reached)

- **R1 · Radar card.** `GET /r/{slug}.svg` from a `card_snapshots` row written when a profile finishes; random revocable slug, no upstream calls, camo-compatible CSP, `/og/{slug}.png` through `resvg-py`, server-rendered `/u/{slug}` landing with OG tags. Research §3.12 has the full design.
- **I1 · Grounded mock interview** (designed 2026-10-16; see the I1 section below). Claim ledger from facts + git-history decision signals; slot blueprint allocated by code; Surface/Push/Floor ladders; verdicts Defended/Shaky/Couldn't defend/Not assessed with verbatim-quote checks; Say/Avoid/Fix cards; readiness computed in code. Research §4.7.
- **X1 · Skills table.** Seed from ESCO digital skills and O*NET software skills; aliases; `pending_review` for LLM-proposed skills; canonical ids on requirements and facts; NCO code crosswalk via ISCO-08 shown on Indian government roles.
- **Growth.** A thin Claude Code skill calling `/api/match`; `/gh/<username>` zero-upload entry; README-as-landing-page launch kit.

---

## D1 · Discover (design)

**v1 (2026-10-16).** Shipped: the list-first Discover screen at `/discover` and the `/api/discover*` routes, reading the Greenhouse and Lever boards of the 51 employers in `backend/tayyari/discover/data/employers.json`. It is a separate service in the Python backend (`backend/tayyari/discover/`): it imports only `core`, `contract` and `storage.ports`, owns `discover_listings` and `discover_runs` (migration 0007), its settings and its refresh, and calls no model. The quick fit is code over a 426-skill vocabulary, and its thresholds were calibrated on 1,377 real listings, where counting raw terms rated every role a stretch for a strong frontend profile. No map, commute time, alerts or other sources in v1. What each deferred item waits for is in `docs/discover-later.md`. Details: `docs/backend.md`, Discover.

**Goal.** Open Tayyari and see roles near the person that fit their resume and skill graph, grouped by why they matter. Each role opens into the existing match screen and has a one-click apply link to the real posting. The full design is a Claude Design canvas with placeholder data (https://claude.ai/artifact/8A5u18LpAhxB7fGfAinhmv); v1 (below) builds the parts that need no permission or legal review, and the rest waits in `docs/discover-later.md`.

**User decisions (2026-10-16).** Split list and map; on phones the list comes first, with a Map button. A separate, quiet lane for companies that fit but have nothing open. Saved roles, alerts for new fits and an application tracker are in. Opening a full match uses the same credits as today; browsing, hover cards and named gaps stay free.

**Deviation from the brief.** The brief asks for a match score on the hover card. `project-soul.md` forbids percentage scores, so the card shows a word and counts ("Looks close · names 8 skills, 5 in your profile"), computed in code and marked as read from skill names only. The full match keeps today's bands.

**Source tiers.**

| Tier | Sources | Apply link | Confidence label |
|---|---|---|---|
| A employer-direct | Greenhouse, Lever, Ashby (after written OK), later JobPosting pages on allowlisted hosts | Employer page or ATS form | "Employer's own listing, checked today" |
| B licensed aggregator | Adzuna India (after licence) | Landing page, "via Adzuna" | "From a partner, employer not confirmed" |
| C remote partners | Himalayas, Remotive | Source-mandated "via" | As B |
| D person-pasted | A pasted posting | The person's own link | "Added by you" (private) |
| E partnerships | NCS, Apna, WorkIndia, Naukri | Per agreement | Set by each agreement |
| Never | LinkedIn, scrapers, vendors built on LinkedIn data | None | None |

**Design rules.**
- **Layout.** Discover chip in the header; a context line (anchor, lenses); lane tabs Near you, Remote India-eligible, Worth moving for. Desktop: list 7/12, sticky quiet map 5/12. Phone: list first, Map button, cards as bottom sheets.
- **Clusters.** Default is why it fits; switch to By place or By company. Precedence: each top-three next role, current work, one skill away, first steps, other close roles. At 5 results a flat list; at 500, at most six clusters of three cards.
- **Area.** Anchor from the resume at city level, asked to confirm. Distances appear only after a locality is picked; never GPS or IP. Reach 3, 5, 10, 25 km or whole city, labelled "straight line". Relocation means a second city.
- **Lenses.** How far and How wide; each stop shows its count before moving. One data-grounded suggestion at a time. Nothing is filtered silently.
- **Card.** Title, employer, place, work mode, freshness, quick-fit word with counts, two cited reasons, biggest named gap, salary only if the employer states it. Actions: Apply (destination named), See full match, Save.
- **Click-through.** The existing match screen; posting side free before "Check my evidence". A breadcrumb returns to the same lens, lane, cluster and map position, kept in the URL.
- **Apply links.** Employer page, then ATS form, then aggregator labelled "via", then source-mandated "via". Never a portal search page. New tab, `noopener`, tracking parameters stripped, nothing added. No reliable link, no card.
- **Save, alerts, tracker.** Saved, Applied, Interviewing, Offer, Closed. Alerts are explicit opt-in with one-click off, and not offered to under-18s.
- **Trust.** Freshness from our first-seen date: 14 days or less plain; 15 to 45 days "Open 4 weeks"; 46 to 90 "Worth checking it's still hiring", sorted lower; over 90 hidden unless the employer feed confirmed it this week.
- **Companies lane.** Name, locality, the reason from the last 90 days, Careers page link. Never mixed with open roles.
- **States.** First run, inferred area awaiting confirmation, loading, empty, overloaded, all weak, source failing, dead apply link, thin resume.

**Data model.** Contract `shared/discovery.ts` ⇄ `backend/tayyari/contract/discovery.py`, a later wave with a parity test.
- `JobSource {id, tier, licence, applyPolicy, attribution, maxCacheHours, mayStoreDescription}`
- `Employer {id, name, domains[], atsKind, boardToken, careersUrl, localities[]}`
- `Listing {id, employerId, title, titleFamily, workMode, yearsMin?, yearsMax?, entryLevel, status, firstSeenAt, lastConfirmedAt, postedAt?, validThrough?, repostCount, salary? (employer-stated only), sourceConfidence, postingHash?, locations[], applyTargets[], sourceRefs[]}`
- `ListingLocation {label, city, region, lat, lng, precision, provider, confidence}`; `ApplyTarget {url, hostLabel, kind, checkedAt, state}`
- `HomeBase {label, city, locality?, precision, source: resume|user, confirmed}`; `Lens {reachKm|reachMinutes, breadth, lanes[], order: ranked|posted}`
- `QuickFit {word, nameCounts {matched, notFound, notStated}, reasons[] (factIds), gates[], version}`; `Cluster {facetKey, labelParams, count, listingIds[], alsoFits[]}`; `CompanyLead {employerId, reason, careersUrl}`; `TrackedRole {listingId, status, savedAt, appliedAt?, closedAt?}`
- Protocols with fakes: `ListingSource`, `Geocoder`, `QuickFitter`, `ReachIndex`. Click-through reuses the posting cache through `postingHash`.

**Before build.**
1. Ashby's written permission. Ashby stays off until it arrives.
2. Adzuna licence decision (cost and terms). Tier B stays off until decided.
3. Counsel on the map (India boundary rule, any national view) and on the regulation notes.
4. Partnerships for tier E, and a person to read Naukri's live terms. Read the unread terms (Jooble, Careerjet, Jobicy, Cutshort, Unstop, LinkUp) before any use.

Research: `docs/research/job-discovery.md` (sources, geography, matching, regulation, open questions).

## D2 · Search with AI

**Built and deployed 2026-10-16** (commit fbf7907). A "Search with AI" panel in Discover; D3 below made it the whole of Discover. Claude Haiku 5.5 scouts search employers' own job sites for the person's kind of role, code reads the boards they lead to and narrows the open roles, and Haiku reads the shortlist against the person's evidence. The trail of how it narrowed things down and the roles stream in as they come. **Haiku 5.5 only**, by the user's decision. The design follows two baseline runs by Opus and Sonnet on the sample resume (`docs/research/jobsearch-baseline.md`): search finds employers, not open jobs (about two in three job links from search were closed), so code decides what is open, near and within reach, and the model only searches and reads. Not on a design canvas: it reuses Discover's approved card language (the memory rule asks for a canvas first; a board can be added).

**How a run goes** (`backend/tayyari/jobsearch/`, ~20 s, ~$0.11):
1. **plan** (`brief.py`, code): the role, the next roles, the years, the city (Discover's, else the resume's), the strongest skills, and evidence lines `E1…` taken word for word from the profile's evidence (skills at 45 and over). Up to three angles: the role here, the next role here, the role remote in India.
2. **search** (`jobsearch.scout`, one Haiku call per angle, at once): only the basic web search tool (`web_search_20250305`), `allowed_domains` = Greenhouse and Lever, `user_location` the city, up to 3 searches each. A scout sees a title, a place, a rough level and skill names; never the name or the resume's text. Its words are not used: `harvest.py` takes board tokens from the tool's result URLs and skips recruiter boards.
3. **boards** (`boards.py`): up to 30 boards found on the web, ranked by how well their result titles read as the person's kind of role, read through Discover's fetcher and parser (`fetch_board`, `parse_board`: India listings only, the same politeness), cached for an hour in process. Boards Discover already reads come from its store, with every other stored listing.
4. **narrow** (`narrow.py`, code): place (the city or remote in India), then kind (distinctive title words of the person's role titles, or a strongest skill named in the title; seniority words never count; a word of another field such as tester, designer or IC design excludes a title unless the person's own titles use it), then level (Staff, Principal, Lead or Head need 6 years; Senior needs 2; an internship or a "0-1 years" role is out from 2 years; at most 2 years over the person's). Ranked by title fit, quick fit, then freshness; at most 3 per employer; 12 to the read.
5. **read** (`jobsearch.judge`, Haiku with structured output, 4 postings a call, at once): per posting, up to six requirements (quote, evidence ids, then status), the level quote then the level, the kind, and any text aimed at an AI, quoted.
6. **checks** (`judge.py`, code):
   - a quote must be in the posting word for word (after folding case, spaces, quote marks and dashes);
   - a met requirement must cite a real evidence id;
   - a years requirement is the level's, never evidence;
   - the level comes from the years code read (Senior with no years is a step up under 4 years), else from the reader's level with a checked quote;
   - left out: another field, far above, nothing met, or text aimed at an AI (a checked quote or a phrase code knows);
   - the word is worked out in code: strong, good or a stretch. Never a number.

**Contract.** `shared/jobsearch.ts` ⇄ `contract/jobsearch.py`, parity-tested with discover.ts. `POST /api/jobsearch` `{profileId, city?}` streams NDJSON: `step` (trail lines written in code, with the scouts' queries), `job` (`AiJob`: the role, the apply link to the employer's posting, found via the web or Tayyari's employers, the fit word, the level word and quote, up to three asks met with the evidence line, up to three gaps, Discover's quick fit, the salary only when stated), then `done` (the funnel).

**Guards.** The profile is opened the way Discover does. The order is: shed, then off (503), then body, then profile (404), then city (400), then per-address and per-account windows (`TAYYARI_GUARD__JOBSEARCH_*`: 1 a minute, 4 an hour, 8 a day). Without an account, 3 a day (`ANON_JOBSEARCH_PER_DAY`). Then 40 an hour across the service, then a spend reservation of $0.20 (`EST_JOBSEARCH_USD`). No credit is charged.

**Settings** `TAYYARI_JOBSEARCH__*`: `ENABLED`, `MODEL` (claude-haiku-5-5), `SCOUTS` 3, `SEARCHES_PER_SCOUT` 3, `MAX_BOARDS` 30, `FETCH_CONCURRENCY` 8, `MAX_JUDGED` 12, `JUDGE_BATCH` 4, `PER_EMPLOYER` 3, `POSTING_CHARS` 6000, `BOARD_CACHE_MINUTES` 60, `REQUEST_TIMEOUT_S` 12.

**Layering.** `jobsearch` sees what the other model-calling services see, plus `discover`'s pure parts (like `match` reuses `portfolio`). `discover` never imports it. The client gained `ClaudeClient.search_web` (one turn with only the search tool, read back query by query, `pause_turn` continued, one usage row per request with its $0.01 searches).

**Web app.** `src/discover/jobsearch/` (panel, trail, card, stream reader with defensive validation). Mounted above Discover's groups. "See full match" posts the posting's address to the match flow. Dev gallery states `?state=ai…`.

**Tests.** `tests/unit/jobsearch/` (harvest, brief, narrowing, the checks with known-bad reads, the run over fake boards and scripted Haiku), `tests/unit/llm/test_search_web.py` (SSE fixtures), `tests/integration/test_jobsearch_routes.py`, `tests/unit/contract/test_jobsearch_parity.py`.

**Later** (in `docs/discover-later.md`):
- Ashby (4 of Opus's 6 finds were there);
- "worldwide" remote roles read as open to India;
- keeping the employers the scouts find;
- evergreen postings ranked lower;
- a replay eval from stored search results;
- a canvas board for the panel.

## D3 · Discover is Search with AI

**Built 2026-10-16**, committed b2b2b0e and deployed the same day. The person's verdict on D2's roles ("some I never would have found"), and their
decisions:
- the AI search becomes the only Discover experience, and the old code-matched list, lenses, lanes and companies lane are
  removed;
- it runs only when pressed, and the last results are kept and shown;
- cards in a grid on desktop and a swipe deck on phone;
- every role gets Prepare (a prep page now, a mock interview next), Save (a tracker), See how you fit (the match),
  Compare, Share, and Not for me.

Share became "Ask for a referral": a short three-part note (why the company, why the person fits, why the role fits
them).

**Canvas:** https://claude.ai/artifact/X3SyjdzdYZhCjfBzTHsGYd. Its 12 boards:
- results, searching and first visit;
- the phone deck, sheet and searching;
- one role in focus, Prepare, and Compare;
- Your applications, Not for me and Share, and the states.

**Contract** (`shared/jobsearch.ts` ⇄ `contract/jobsearch.py`, parity-tested):
- `AiJob` gains `asks` (`AskCounts`: met, partly, not shown), which drives the card's pips (one square per checked
  requirement; counts, never a score), and `openLong`.
- `JobSearchRequest` gains `remote` and `avoid` (`AvoidPrefs`: hidden job ids, employers, no step up).
- `done` names the stored run (`runId`, `createdAt`).
- New: `JobSearchRun`, `JobPosting`, `RoleRequest`, `RolePrep` (with `PrepLead`, `PrepGap`, `PrepQuestion` and
  `StoryPart`), and `ReferralNote` (with `ReferralLine` and `ReferralPart`).

**Backend:**
- **The run streams in two halves at once.** Tayyari's own employers are read from Discover's store and up to 6 go to
  the read, while the scouts search. The web pool then goes to the read without the roles already read. The first cards
  show in a few seconds.
- **Stored runs.** Every run is kept in `jobsearch_runs` (migration 0008): what the person saw, plus the shown postings'
  text, kept server-side only. Prep pages and referral notes are kept in `jobsearch_docs`. Both are purged after 30
  days.
- **Routes:**
  - `GET /api/jobsearch/latest` returns the last run for the profile, city and remote choice.
  - `GET /api/jobsearch/runs/{runId}/jobs/{jobId}/posting` returns the text that "See how you fit" sends to
    `POST /api/match`.
  - `POST /api/jobsearch/prepare` and `/referral`: a run is read back only for its own profile.
- **Prepare** (`jobsearch/prepare.py`, one Haiku call):
  - the lead comes from the checked read;
  - every gap gets the nearest evidence and a small step, marked as a suggestion;
  - 3 to 4 questions, each tied to a quoted ask, with a story built in code from cited evidence plus placeholders for
    what only the person knows.
- **Referral** (`jobsearch/referral.py`, one Haiku call): three lines in typed slots.
  - Every quote is checked against the posting.
  - Every number and name is checked against the cited evidence (`portfolio.ground` anchors).
  - A line that fails falls back to a template built from facts.
- **Old list removed.** Discover's old list service and routes are gone. `discover/` is now the board store, the
  refresh, the parsers, places, vocabulary and quick fit behind the AI search, plus `GET /api/discover/cities`.

**Web app:** `src/discover/`:
- the new screen and cards, the deck, the focus panel, Not for me and the compare tray;
- `prepare/`, `share/`, `compare/` and `applications/` (the tracker, kept in this browser).

**Referral note, 2026-10-17 (the user's feedback):** one paragraph, a storyline in three sentences (`intro` who
you are, `fit` why you fit them from the role's checked matches, `draw` why them), "Hi there" when the name is
left empty; web employers named without a board number ("Appen", not "Appen 2").

**Later:**
- a mock interview grounded in the posting (I1);
- the tracker synced to an account;
- "Not for me" learning beyond ids, employers and level;
- an alert when a saved role closes.

---

## I1 · Practice the interview

**Designed 2026-10-16; built, committed (659b7d6) and deployed 2026-10-17.** The user said "build it" once the canvas was done. Four waves ran in parallel with frozen interfaces (contract, ports, settings, purposes, route and API names): B1 the service (`backend/tayyari/interview/`), B2 storage, routes, guard and wiring, F1 the room, setup and work sample (`src/practice/room|setup|work`, speech), F2 Practice, the debrief, Progress and the entry points. How it works now is in `docs/backend.md` → Practice the interview; the browser walkthrough is step 17. The research is in
`docs/research/mock-interview.md`: HackerRank's Chakra and HackerRank's practice interviews, their features and payment
models, and what we take and leave. The earlier outline is in `docs/research/open-source-resume-tools.md` §4.7.

**Canvas:** https://claude.ai/artifact/VtufBm3wga3E1SJRiHw3cd. Its 12 boards:
- the room (interactive), Practice, the plan before you start, the work sample;
- the debrief, what carries over, how each kind builds its questions, work samples for every profession;
- the phone room and phone debrief, the edges, and how it works (with the HackerRank comparison and pricing later).

**The user's decisions:**
- **Answering.** Speak by default, type any time. The browser does speech-to-text and the interviewer's voice; Chrome's
  speech service is Google's, and the app says so. No camera, and no audio kept. The transcript is kept 30 days.
- **Four kinds, all in the first version:**
  - This role, from a posting;
  - Defend your resume;
  - Behavioural stories;
  - Work sample, then defend it, with an optional assistant.
- **Free for now, price later,** under per-person daily limits and the spend cap.

**The rules the design holds to:**
- Questions come only from the person's evidence, the posting's asks and their gaps.
- Code allocates the slots: for This role, 2 asks shown, 1 gap, 1 claim and 1 story, with must-asks first. The person
  sees the plan and can skip any slot.
- Each slot has a Surface question, a Push follow-up and an easier Floor, all phrased once by the model. Rules pick
  the next rung.
- A router in code handles silence, "I don't know", skip, "say it again", a dispute and drifting off, with no model
  call. A dispute is checked against the cited line and conceded when the person is right.
- Verdicts are Defended, Shaky, Couldn't defend or Not assessed.
  - The judge cites a word-for-word quote of the answer before the verdict. Code checks the quote; a verdict without
    a valid quote becomes Not assessed.
  - Not assessed never counts against the person.
- The debrief:
  - counts in code, no number score, no hire or no-hire;
  - Say, Avoid and Fix for each answer. Say is built only from the person's words and cited evidence
    (`portfolio.ground` anchors), with `[brackets]` for what only they know;
  - a good answer about something missing from the resume can become a fact marked "From you".
- What carries over:
  - the best verdict per claim across sessions;
  - a "Practised" line on the skill in the graph, which never adds a skill;
  - Fix steps in Prepare;
  - practice dates on Your applications.
- No reading of personality: no confidence, tone, accent, face or filler-word counts.
- Nothing is shared with an employer.

**As built** (the sketch, confirmed; differences noted):
- **Contract.** `shared/interview.ts` ⇄ `contract/interview.py`:
  - `InterviewKind` (role, resume, stories, work);
  - `Slot {id, kind: ask|gap|claim|story|task, sources[], question, push, floor, covers[]}`. `covers` is hidden until
    the debrief.
  - `InterviewPlan`;
  - `Turn {slotId, speaker, rung, text, atSeconds, mode: speech|typed}`;
  - `SlotResult {slotId, quotes[], verdict, why, say, avoid?, fix?, storyParts?, newFact?}`;
  - `Debrief {counts, firstThing, results, transcript}`.
- **Tables:** one, `interview_sessions` (migration 0009): the whole state and the debrief as JSON, with a `version`
  for compare-and-swap saves. Purged after 30 days. The best verdict per claim is computed from the stored debriefs.
- **Routes:**
  - `POST /api/interview/plan`;
  - `POST /api/interview/sessions` to start, then `POST /api/interview/sessions/{id}/answer` (plain JSON, not NDJSON:
    one small judge call per answer), `/work` and `/assistant` for work samples;
  - `POST /api/interview/sessions/{id}/end`, which returns the debrief;
  - `GET /api/interview/sessions/{id}` and `GET /api/interview/sessions` (history; empty for the shared sample
    profile);
  - `POST /api/interview/sessions/{id}/again`, one slot, no model call;
  - `POST /api/interview/sessions/{id}/facts` and `DELETE /api/interview/sessions/{id}`.
- **Prompts** (`prompts/interview.py`, Haiku 5.5, structured, citations first):
  - `interview.phrase`: the questions for each slot;
  - `interview.task`: a work sample from a code-picked template, filled from the posting, never from the resume;
  - `interview.judge`: per answer;
  - `interview.debrief`: Say, Avoid and Fix;
  - `interview.assistant`: the work-sample helper.
- **Guard:** an `interview` rate window per person (3 a day on the canvas) and a spend reservation per session. The
  estimate is about $0.02 per interview, to be measured.
- **Web app:** `src/practice/` (`/practice`, `/dev/practice` for every state): Practice, Setup, the room with a speech adapter (Web Speech recognition and synthesis,
  feature-detected, falling back to typing), the work sample, the debrief and Progress. Entry points:
  - Prepare's "Practice the interview";
  - the rows of Your applications;
  - the match screen;
  - a Practice chip in the header.
- **Speech through Sarvam AI (2026-10-17, the user's request, with 100 free credits):** the official `sarvamai`
  SDK on the server (`backend/tayyari/speech/`, `/api/speech/*`, contract `shared/speech.ts`): Saaras v4
  speech-to-text of answer pieces cut at pauses, with the interview's names as key terms, and a Bulbul v4-flash
  Indian English voice for the interviewer, under a soft daily and per-session budget. The browser's speech and
  typing remain the fallbacks. Details in `docs/backend.md` → Practice the interview → Speech.
- **The voice, generated ahead (2026-10-17, the user's request after a 4.7 s wait on production):** at start the
  first question, then every surface question of the plan; while a question is being answered, its push and floor
  (`TAYYARI_SPEECH__TTS_PREFETCH_FOLLOWUPS`) and the next question; the fixed leads once per process. A line being
  generated is shared, never made twice. Measured locally: later lines 0.02 to 0.04 s instead of about 2 s. About
  1,100 voice characters per full interview instead of 600.
- **Differences from the canvas, decided while building:**
  - "Defend your resume" never asks about a skill as if the resume listed it: a low-scoring profile skill is Tayyari's
    reading, not the person's words (the live run asked "Your skills list includes RAG and retrieval", which was false).
  - A work sample's tasks are fixed templates (the six on the canvas); the posting does not fill in details yet.
  - "Add to Prepare" reads "Open Prepare": Prepare cannot take a step from the debrief yet.
  - A "Practised" badge on the graph's skills is not built; Progress lists the best verdict per evidence line instead.
  - "This role" practises roles from Discover (saved or found), and since 2026-10-18 a role of the person's graph
    (`graphRole`, opened from Prepare); a pasted posting is not a source yet.
  - A fact kept "From you" stays in practice history; it is not yet added to the posting match's facts.
  - The microphone needed `Permissions-Policy: microphone=(self)` (it was `microphone=()`).
- **Checks:**
  - a known-bad fixture for each verifier: a judge quote not in the transcript, a Say with an invented number, a
    personality word in a Why;
  - the router's phrases as table tests;
  - a parity test for the contract.

---

## P1 · Prepare (help me prepare, as blocks)

**Designed 2026-10-18** (canvas https://claude.ai/artifact/QgpVd4MRfnsqZRrPrLRe73, six boards); the user said "Looks good, build it". The ask: "Instead of text focus on interactive UI to not bombard someone with too much information", in the spirit of OpenAI's ChatKit widgets and AG-UI (typed blocks, buttons that send actions, shared state, stop and redirect). Today "How do I prepare for this role?" sends a seeded question to the coach chat, which answers with ~450 words of markdown, a table and an 8-week plan.

**What changes.** For a role (and for one gap of a role) the thread shows four blocks instead of the chat's prose; the composer stays for follow-up questions, which go to the coach chat as today.
1. **Where you stand:** the role's gaps as bars (current → target from `Profile.roles`), the biggest marked, one "why" line per gap behind a tap with an evidence chip ("From your resume"), and one quiet "Already strong" line. Drawn at once from the graph, before any model answers.
2. **The one move:** the move that closes most of it, with what the person already has (cited) and the gaps it works on (names only, never a predicted number). Up to three moves, best first, shown one at a time ("Show another move").
3. **Your plan:** phases (title, hours, two or three tasks, one deliverable); the person picks 3, 6 or 10 hours a week and the weeks are computed in code; one phase open at a time; tasks are checkboxes kept with the plan; a link to what they shipped can be added to a phase (kept; reading it into the graph is a follow-up).
4. **Next:** buttons, not prompts. Put the plan on my calendar (an .ics file made in the browser), Pick the repo to start from (ranked in code from the stored GitHub digest, nothing fetched), Rewrite my headline for this (one model call, kept only if every name and number is in the cited evidence), Practice (opens Practice; practising a role taken from the graph, rather than from a search, is a follow-up).
"Read this as text" is written in the browser from the same blocks. No percentage anywhere: the thread header says "closest of your 5 roles", and the seeded question no longer quotes "84% aligned".

**Contract.** `shared/prepare.ts` ⇄ `backend/tayyari/contract/prepare.py` (`tests/unit/contract/test_prepare_parity.py`): `PrepareRequest`, `PreparePlan` (gaps, strong, evidence, moves, state, version, rank and roleCount), `PrepareEvent` (`gaps` → `standing` and `moves` in either order → `done`, or `error`), `PrepareState` (hoursPerWeek, moveIndex, done task keys, links), `PrepareRepos`, `PrepareHeadline`.

**Routes.** `POST /api/prepare` (NDJSON; a stored plan for the same profile, role and requirement streams at once unless `fresh`), `GET /api/prepare/{id}`, `PUT /api/prepare/{id}/state` (compare-and-swap on `version`, 409 when stale), `GET /api/prepare/{id}/repos`, `POST /api/prepare/{id}/headline`. Profile access as every route (`require_profile_access`), the plan must belong to that profile; a guard window `prepare` and a spend reservation for the two paid routes; unlimited accounts skip the counters as elsewhere.

**Model calls** (purposes `prepare.standing`, `prepare.moves`, `prepare.headline`; the chat model; structured output; run `standing` and `moves` at the same time):
- `standing`: the headline sentence and one why per gap, each citing evidence ids before the text.
- `moves`: one to three moves with phases; `have` items cite evidence ids; `works` must be gap names.
- Code checks: cited ids exist; a "why" or `have` without a valid citation is dropped; any digit in model text must appear in the cited evidence or the gap numbers, else that sentence is dropped; `works` filtered to gap names and a move with none is dropped; hours 1–40, at most 5 phases and 4 tasks, lengths clipped. `standing` failing leaves the gaps alone (numbers only); `moves` failing ends the stream with an error and a retry.

**Storage.** Table `prepare_plans` (id, profile_id → profiles cascade, role_id, requirement nullable, content JSON, state JSON, version, created_at, updated_at), migration `0010`, additive.

**Not in this wave:** prepare for a skill, an area, a source or "you" (they stay chat until the blocks are extended to them, as the Anatomy board shows); reading a shipped link into the graph. (Practising a graph role was built alongside, 2026-10-18: Prepare's Practice button opens Practice · This role for the plan's role.)

**Backend built 2026-10-18** (`backend/tayyari/prepare/`, `prompts/prepare.py`, `api/routes/prepare.py`, migration 0010, the `prepare` guard window; documented in `docs/backend.md` § Prepare; golden `tests/data/prepare/riya.prepare.json`). Decisions taken while building: a role with no gap at all is `already_strong` too; `fresh` adds a row and the older plan stays readable by its id; a plan's row follows its profile (FK cascade), so there is no retention step; the `prepare` window counts a written plan and a headline rewrite, nothing else.

## L1 · Sign in to the app with the Leafout account

**Built 2026-10-19; committed, not yet deployed.** Tayyari is becoming Leafout. The marketing site (https://leafout.in, Lovable + TanStack Start, repo `Aman8T/tayyari-career-intelligence`) already signs people in through Lovable Cloud, which is Supabase Auth: email and password, emailed link, Google (managed by Lovable), phone OTP (waiting for an SMS provider) and "Sign in with Lovable". The app moves to https://app.leafout.in. **The user decided that the site's login is the app's login.** The app keeps no sign-in screen of its own. The site and the app are now same-site under `leafout.in`, so a cookie on `.leafout.in` can carry proof of the site's sign-in to the app. The app checks that proof once and then runs its own session, exactly as F1 does after Google or an emailed link.

**Shape in one paragraph.** When the site signs someone in, it writes the Supabase **access token** (never the refresh token) to a `.leafout.in` cookie. On a request that has no `tayyari_session`, the app reads that cookie and checks the token's signature against the project's public keys (JWKS). It then checks the claims and asks Supabase once whether that sign-in is still alive. If all of that passes, the app finds or creates the account and starts its usual 30-day `tayyari_session`. The app never refreshes a Supabase token and never holds a refresh token. Every 24 hours a session made this way has to see a valid site cookie again, so a sign-out on the site reaches the app within a day at most. When the site already has a session, getting a fresh cookie is a quick redirect through the site.

Checked 2026-10-19: the project's `/auth/v1/.well-known/jwks.json` publishes one `ES256` (EC P-256) signing key. Asymmetric verification works today, and no shared secret is needed.

### 1 · The shared cookie (a note for the site repo)

Lovable generates `src/integrations/supabase/client.ts` and `previewAuthStorage.ts`, and both say "do not edit". So the site does not swap the client's storage for `@supabase/ssr` cookie storage. That swap would put the whole session, refresh token included and split into chunks, on the parent domain. Instead the site **mirrors only the access token** into one cookie, from code it owns. Lovable can regenerate its files and nothing breaks.

What the site repo changes:

1. **New module `src/integrations/leafout/sharedCookie.ts`** (a new file, not a generated one):
   - `writeShared(session)` sets `leafout-access=<session.access_token>; Domain=.leafout.in; Path=/; Secure; SameSite=Lax; Max-Age=<session.expires_at − now>`.
   - `clearShared()` sets the same name, domain and path with `Max-Age=0`.
   - Both do nothing unless `location.hostname` is `leafout.in` or ends in `.leafout.in`. Lovable previews and localhost never write it.
   - If the token is longer than 3,800 bytes, nothing is written and a `shared_cookie_too_large` event is tracked. A Supabase access token is about 1 to 1.5 KB, and a cookie's limit is 4 KB.
   - The cookie cannot be HttpOnly, because browser code writes it. That is the reason only the short-lived access token goes into it.
2. **`src/lib/auth.ts`, in `startAuth`'s `onAuthStateChange`.**
   - On `INITIAL_SESSION`, `SIGNED_IN`, `TOKEN_REFRESHED` and `USER_UPDATED` with a session, call `writeShared`.
   - On `SIGNED_OUT`, call `clearShared`.
   - Drop the comment "these sessions never sign anyone in to the Tayyari app", which is no longer true.
3. **`/signin` takes `next`.**
   - `next` counts only when `new URL(next).origin === "https://app.leafout.in"` exactly: https, no port, no userinfo. Anything else is ignored and the page behaves as it does today (`/account`).
   - A valid `next` is kept in `sessionStorage` before any OAuth or emailed-link round trip, because Lovable's managed Google `redirect_uri` stays `/signin`.
   - When a session exists, whether it was there already or has just been made: `await supabase.auth.getSession()` (this refreshes an expired access token), then `writeShared`, then `location.replace(next)`.
   - So someone already signed in on the site goes straight back to the app with no click.
4. **New `/signout` route.**
   - It runs `await supabase.auth.signOut({ scope: "local" })` (scope is open question 3), then `clearShared()`.
   - With `?from=app` it then runs `location.replace("https://leafout.in/")`.
   - Without it, it runs `location.replace("https://app.leafout.in/signout?next=https://leafout.in/")`, so the app's session ends too.
   - The account page's "Sign out" button and account deletion go through `/signout` instead of calling `signOut()` directly.
5. **No other subdomain of `leafout.in` hosts third-party or user content.** Any page on a subdomain can set a `.leafout.in` cookie (see Security). Keep DNS free of dangling CNAMEs.

### 2 · Checking the token in the app

**Where.** New `backend/tayyari/auth/supabase.py` (`SupabaseVerifier`, `SupabaseIdentity`, `SupabaseError(reason)`). The JWKS cache is lifted out of `google.py` into `auth/jwks.py`, and Google uses it too. `AuthService.leafout_sign_in(token, *, ip_hash, user_agent) -> SignedIn | None` composes them like `google_finish`. `api.deps.get_auth_context` calls it.

**When.** `get_auth_context` runs the bridge when all of the following hold:
- the request has no live `tayyari_session`;
- the request carries the `leafout-access` cookie (`TAYYARI_AUTH__SUPABASE_COOKIE`);
- Leafout sign-in is configured;
- the path is under `/api/` and is not a webhook.

It runs at most once per request (the result is cached on the request, like the session). If the bridge succeeds, a session is created and the request goes on as signed in. The cookie is attached by a small pure-ASGI `SessionCookieMiddleware` (new, `api/session_cookie.py`). It adds `Set-Cookie: tayyari_session=…` to the response start when the request state holds a freshly minted token, so streaming routes need no change. If the bridge fails, the request is anonymous, exactly as with no cookie, and it never returns a 401 of its own.

**Checks, in order.** Each has a reason code that goes to the log, never to the person.
1. Shape: three base64url parts, at most 4,096 characters, and a JSON header and payload (`malformed`).
2. `alg` is `ES256` or `RS256` (`alg`). `none` and `HS*` are refused before any key is looked up, which shuts the RS/HS confusion attack.
3. `kid` is in the cached JWKS (`kid_unknown`).
   - The JWKS comes from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` through the auth service's `httpx2` client and is cached 6 hours.
   - An unknown `kid` triggers one refetch, at most every 60 seconds; this follows key rotation.
   - If the fetch fails, the reason is `upstream`: no session, nothing counted against the person, and the next request tries again.
   - A key whose `alg` differs from the header's is not used.
4. Signature (`signature`), with `joserfc`, which is already a dependency.
5. `iss` equals `<SUPABASE_URL>/auth/v1` exactly (`iss`).
6. `aud` is `authenticated`, as a string or inside a list (`aud`).
7. `role` is `authenticated` (`role`), which refuses `anon` and `service_role` tokens.
8. `exp` is present and later than now − 30 s (`exp`).
9. `iat` is present and no more than 30 s in the future (`iat`). `now − iat` is at most `TAYYARI_AUTH__SUPABASE_MAX_TOKEN_AGE_S` (default 3600, Supabase's access-token lifetime). This refuses a token minted with a long expiry even if its `exp` is still valid (`too_old`).
10. `sub` is a UUID (`sub`). `is_anonymous` is not true (`anonymous`), because Supabase anonymous users are not accounts.
11. **Only when the app is about to start a new session:** call `GET <SUPABASE_URL>/auth/v1/user` with `Authorization: Bearer <token>` and `apikey: <publishable key>`.
    - Supabase answers only while the token's session exists, which catches a global sign-out or a deleted user (`revoked` on 401 or 403).
    - The returned `id` must equal `sub` (`mismatch`).
    - The response gives `email_confirmed_at` and `phone_confirmed_at`, which the JWT does not carry reliably. `user_metadata` can be edited by the person, so the app trusts nothing in it except a display name.
    - A network error or 5xx gives `upstream`.

**If the project only signed with HS256.** It does not today: see the check above. If it ever did, the JWKS would have no usable key. The app would then rely on step 11 alone for the signature: Supabase itself validates the token, with claims checks 5–10 run on the decoded payload first as a filter. This mode is `TAYYARI_AUTH__SUPABASE_VERIFY=user_endpoint`. The default `auto` picks `jwks` when the JWKS holds an ES256 or RS256 key, else `user_endpoint`, and logs which one it chose at startup. The app never accepts an HS256 token through the JWKS path, and never takes the project's JWT secret as a setting. That secret can mint a token for any user of the project, so holding it in a second service is a risk the user-endpoint path avoids.

**Refresh and expiry.**
- The app holds no Supabase refresh token and refreshes nothing. The site's client refreshes the access token while a site tab is open and mirrors it each time (`TOKEN_REFRESHED`).
- The Supabase token is used only to *start* an app session. After that the app's `tayyari_session` carries the person: 30 days, sliding, as F1.
- **Recheck.** A session started this way is marked `source='supabase'`, `verified_at=now`. When `verified_at` is older than `TAYYARI_AUTH__SUPABASE_RECHECK_HOURS` (24), the next request also looks at the `leafout-access` cookie:
  - valid, same `sub`: `verified_at` slides, using local checks 1–10 only and no Supabase call;
  - absent or invalid: the app session ends there, the request is anonymous, `/api/me` says `reauth: true`, and the web app sends the browser through the site's `/signin?next=<this page>`. That is instant when the site still has a session, and a sign-in form when it does not;
  - valid, different `sub` (someone else signed in on the site in this browser): the old app session ends and a new one is bridged.
- The same different-`sub` rule applies whenever a valid cookie is seen next to a live session. The site is the source of truth for who is signed in in this browser.
- Sessions from the app's own Google and emailed-link paths (none in production) are left as they are until they expire.

### 3 · Accounts and linking

`UserRepo.sign_in_supabase(*, sub, email, email_verified, name, now) -> StoredUser`, in one transaction:
1. Find the account by `users.supabase_sub = sub`.
   - If found, update `last_login_at`.
   - If the verified email has changed on the site and no other account holds it, update `email`.
2. Else, find the account by verified email: `lower(email)`, and only when `email_confirmed_at` is set.
   - If found and its `supabase_sub` is NULL, set it. This links an F1 account made by Google or by link.
   - If its `supabase_sub` is already a different value, refuse with `email_taken`: no session, a log line with both account ids, nothing linked.
3. Else, create an account with `supabase_sub=sub`.
   - `email` is the verified email, or NULL for a phone-only person or an unverified email.
   - `name` comes from `user_metadata.full_name` or `name`, clipped to 200 characters. It is cosmetic, so its editability does not matter.
   - No `picture_url`: the CSP blocks avatar hosts anyway (see Follow-ups).

The Supabase phone number is **never stored**: the app has no use for it. `/api/me` shows such an account as "Signed in with your phone" (`user.email: null`).

**Migration `0011_leafout_identity`** (Alembic, additive):
- `users.supabase_sub` `String(36)` NULL, with a unique constraint `uq_users_supabase_sub`. This follows the `google_sub` pattern; an identities table is not worth it for one more provider.
- `auth_sessions.source` `String(16)` NULL (`'supabase'` or NULL for F1 sessions), `auth_sessions.supabase_session_id` `String(36)` NULL (the token's `session_id` claim, for the log and later revocation), and `auth_sessions.verified_at` timestamptz NULL.
- **`users.email` drops `NOT NULL`**, for phone-only accounts. This relaxes a constraint rather than adding a column. Old code always writes an email, so it is backward compatible, and a rollback is safe while no NULL row exists. It still needs the user's yes (open question 1).
  - Without that yes: no `DROP NOT NULL`, and a phone-only person sees "Add an email to your Leafout account to use the app", with a link to the site's account page.
  - `StoredUser.email`, `contract/auth.py` and `shared/auth.ts` (`email: string | null`) follow, and so do the places that assume an email: unlimited-account matching (`is_unlimited(None)` is false), Razorpay prefill, and the account menu.

Ports, the fake in `tests/fakes`, `UserRow` and `AuthSessionRow` gain the fields. `StoredAuthSession` gains `source`, `supabase_session_id` and `verified_at`.

### 4 · Sign-in and sign-out

**Sign in.** Every "Sign in" in the app is an ordinary link:
- The link is `<TAYYARI_AUTH__SUPABASE_SIGN_IN_URL>?next=<urlencoded https://app.leafout.in + current path>`, where the path goes through `safe_next` and the app origin is `TAYYARI_AUTH__PUBLIC_URL`.
- It replaces the sign-in sheet's Google button and email field when `providers.leafout` is true. The sheet becomes one line and one button, "Sign in with your Leafout account". This is a small board on the canvas before building (design first).
- The site returns the browser to `next` with the cookie set. The app's next `/api/me` bridges, and the web app then claims the unclaimed profile in its store, as after F1's sign-in today.

**Sign out in the app.** The account menu's "Sign out" goes to the app's new page `/signout`, a client route added to `APP_ROUTES` in `api/static.py` and marked private.
- The page sends `POST /api/auth/logout` (same-origin, so `CrossSiteGuard` passes it). That deletes the session and clears `tayyari_session`.
- It then runs `location.replace(next)`, where `next` may only be `https://leafout.in/signout?from=app` (the default) or `https://leafout.in/`. Any other value falls back to the default.
- The site's `/signout?from=app` signs out of Supabase, clears `leafout-access`, and lands on `https://leafout.in/`. Both sides have ended.

**Sign out on the site.**
- The site's `/signout` (no `from`) signs out of Supabase, clears the cookie, and goes to `https://app.leafout.in/signout?next=https://leafout.in/`, which ends the app session and returns.
- If the tab is closed halfway, the 24-hour recheck ends the app session anyway.
- A link from elsewhere can sign someone out of the app by opening `/signout`. That is a nuisance, not a takeover, and is accepted.

### 5 · The app's own Google and emailed-link paths

- New setting `TAYYARI_AUTH__OWN_SIGN_IN` (bool, default **false**). While false:
  - `GoogleOAuth` is not built even when `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set;
  - `MagicLinks.enabled` is false;
  - `GET /api/auth/google/start` and its callback redirect to `/?auth=google_unavailable`, as they do today without credentials;
  - `POST /api/auth/magic` answers 404 `{"error": "not_offered"}`, and `GET /api/auth/magic/verify` redirects to `/?auth=link_invalid`.
- The code stays, so turning the paths back on is one setting. Local development that wants to try them sets it to true. The F1 tests set it to true in their fixtures.
- `AuthService.providers` becomes a small dataclass `Providers(google, magic, leafout)`. `GET /api/me` reports `providers: {google, magic, leafout, leafoutSignInUrl}`. The URL is `null` unless Leafout sign-in is configured, and the web app builds its sign-in links from it. `MeResponse` also gains `reauth?: true` (see Recheck). The contract changes in both `contract/auth.py` and `shared/auth.ts`.
- **Anonymous matching.** `MatchSettings.anonymous_allowed` takes `Providers`, and `auto` is true exactly when none of the three is available.
  - So once the Supabase settings are set in production, `auto` turns anonymous posting match **off**: every `/api/match*` route needs a sign-in, which is now one click for anyone who has a site account.
  - Matches, answers and profiles made anonymously before are claimed on sign-in through `POST /api/me/claim`, as today.
  - Setting `TAYYARI_MATCH__ALLOW_ANONYMOUS=true` keeps anonymous matching through a launch period (open question 2).
  - The startup line `match_anonymous` gains `leafout=`.

### 6 · Security

- **Cookie scope.**
  - `leafout-access` is on `.leafout.in`, `Secure`, `SameSite=Lax`, `Path=/`, and lives only until the token's `exp`. It holds only an access token: short-lived, and useless without the project's publishable key for anything but this project's APIs, where row-level security applies.
  - `tayyari_session` **stays host-only** on `app.leafout.in`, with no `Domain` attribute: HttpOnly, Lax, Secure. It never moves to the parent domain, so the site can neither read nor set it.
  - The app never sets, refreshes or deletes `leafout-access`. Sign-out goes through the site.
- **CSRF and `CrossSiteGuard`.**
  - From the browser's point of view, `leafout.in` and `app.leafout.in` are now **same-site**. `SameSite=Lax` therefore no longer protects the app's cookies from requests started by the site's pages or any other `*.leafout.in` page, and those requests carry `Sec-Fetch-Site: same-site`, not `cross-site`.
  - The guard still refuses them, because its second rule compares the `Origin` host with the served host **exactly** (`leafout.in` ≠ `app.leafout.in`).
  - That comparison must never be loosened to the registrable domain. `same-site` must never be added to the pass list next to `same-origin`.
  - Tests pin both (see Tests).
  - The app keeps answering no CORS preflight, so no site page can read an app response.
  - GET routes stay free of side effects. The bridge is the one GET-time write, and it can only sign the browser in as the person the cookie already names.
- **Cookie tossing.** Any page on any `*.leafout.in` subdomain can set `leafout-access`. A hostile subdomain could plant its own token and sign a visitor into the attacker's app account (login CSRF), which the different-`sub` rule would even let replace a live session.
  - Defence: only the site and the app live under `leafout.in`. No user content, third-party hosting or dangling DNS goes on a subdomain.
  - Lovable previews are on `lovable.app` and are unaffected.
  - Logged as `supabase_sub_switched` so a pattern is visible.
- **Rate limits through `guard`.**
  - New window `auth_bridge`: per client 10 a minute and 30 an hour, whole service 600 an hour (`TAYYARI_GUARD__AUTH_BRIDGE_PER_MINUTE`, `_PER_HOUR`, `_GLOBAL_PER_HOUR`).
  - The window is counted only when local checks 1–10 pass and the Supabase user call (step 11) is about to run. That call is the bridge's only network call and write, so a garbage or expired cookie costs CPU and nothing else.
  - The in-memory bucket sheds first. Over the limit, the request is anonymous rather than a 429, so a page never breaks on it.
  - A per-`sub` daily counter (`u:sb:<sub hash>`, 50 a day) bounds sessions minted for one person.
  - Not exempt for unlimited accounts: they are unknown until after the bridge.
- **Logging never records tokens.**
  - Log events are `leafout_bridge` (`outcome`, `reason`, `user_id` after success), `leafout_recheck` and `supabase_jwks_refreshed` (key count, `kid`s).
  - The token, the cookie value, `sub`, the email, the phone and the Supabase `session_id` are never logged. `sub` appears only as an HMAC under the IP-hash key when needed for the per-`sub` counter.
  - `core.redact.SENSITIVE_KEYS` gains `token`, `access_token`, `refresh_token`, `cookie` and `authorization` as a backstop.
  - `SupabaseIdentity` and every dataclass holding the token use `field(repr=False)`.
  - No token ever travels in a query string: the app's sign-in link carries only `next`, and Supabase's own redirects land on the site, not the app. So `_SECRET_IN_QUERY` in `api/app.py` needs no new path, and FastAPI's telemetry spans stay clean. A test checks that the bridge's log output never contains the token, the cookie value, `sub` or the email.
- **CSP.** Unchanged.
  - The browser never talks to Supabase from the app. The JWKS fetch and the user call are server-side.
  - Sign-in and sign-out are top-level navigations to `leafout.in`, which `connect-src`, `form-action 'self'` and `frame-src` do not restrict.
  - No Supabase or Lovable script, frame or connect host is added.
- **The old domain `tayyari.fastapicloud.dev`.** The `.leafout.in` cookie is never sent there, so nobody can sign in on it. The app's sign-in links point at the site, whose `next` accepts only `app.leafout.in`, so a sign-in started there lands on the new domain.
  - New setting `TAYYARI_AUTH__REDIRECT_OTHER_HOSTS` (bool, default false; never on locally). When true, a GET or HEAD for a page (not `/api/*`, not `/api/health`) on a host other than `TAYYARI_AUTH__PUBLIC_URL`'s gets a 308 to the same path and query on the public URL. Shared `/p/` portfolio links keep working.
  - `/api/*` keeps answering on the old host, anonymously, for any script or skill that calls it.
  - `TAYYARI_AUTH__PUBLIC_URL` must be set to `https://app.leafout.in` on the cloud. `FASTAPI_PUBLIC_URL` may still name the old host.
  - A startup warning fires when Leafout sign-in is configured and the public URL's host does not share a parent domain with the sign-in URL's host (`leafout_cookie_domain_mismatch`).

### 7 · Settings

`core/settings.py`, inside `AuthSettings` (`TAYYARI_AUTH__*`):

| Field | Default | Meaning |
|---|---|---|
| `supabase_url` | None | The Supabase project URL. Unset means Leafout sign-in is off. Blank keeps the default; the trailing slash is dropped. |
| `supabase_publishable_key` | None | The project's publishable (anon) key, sent as `apikey` on the user call. It is public (it ships in the site's bundle), so it is plain `str`, not `SecretStr`. It is still a setting, never code. |
| `supabase_cookie` | `leafout-access` | The shared cookie's name; it must match the site. |
| `supabase_verify` | `auto` | `auto`, `jwks` or `user_endpoint` (see HS256 above). |
| `supabase_max_token_age_s` | 3600 | The oldest `iat` accepted. |
| `supabase_recheck_hours` | 24 | How long a bridged session goes before it must see the site cookie again. |
| `supabase_sign_in_url` | None | `https://leafout.in/signin`. |
| `supabase_sign_out_url` | None | `https://leafout.in/signout`. |
| `own_sign_in` | false | Offer the app's own Google and emailed-link sign-in. |
| `redirect_other_hosts` | false | 308 page requests on other hosts to the public URL. |

Leafout sign-in is on when `supabase_url`, `supabase_publishable_key` and `supabase_sign_in_url` are all set. It is reported at startup as `leafout_sign_in enabled=… verify=jwks|user_endpoint`. Guard: the three `AUTH_BRIDGE_*` fields above in `GuardSettings`. There are no new secrets.

`backend/.env.example` gains commented lines, with no values:

```
# Sign in with the Leafout account (the site's Supabase Auth). Unset = off.
# TAYYARI_AUTH__SUPABASE_URL=
# TAYYARI_AUTH__SUPABASE_PUBLISHABLE_KEY=
# TAYYARI_AUTH__SUPABASE_SIGN_IN_URL=
# TAYYARI_AUTH__SUPABASE_SIGN_OUT_URL=
# TAYYARI_AUTH__SUPABASE_COOKIE=leafout-access
# TAYYARI_AUTH__SUPABASE_VERIFY=auto
# TAYYARI_AUTH__SUPABASE_MAX_TOKEN_AGE_S=3600
# TAYYARI_AUTH__SUPABASE_RECHECK_HOURS=24
# TAYYARI_AUTH__OWN_SIGN_IN=false
# TAYYARI_AUTH__REDIRECT_OTHER_HOSTS=false
```

The user sets the cloud values themselves (`fastapi cloud env set NAME`). They include `TAYYARI_AUTH__PUBLIC_URL=https://app.leafout.in`.

### 8 · Tests

All without the network. `tests/fakes/supabase.py` provides `FakeSupabase`:
- an `httpx2.MockTransport` that serves the JWKS and `/auth/v1/user`;
- an EC P-256 key pair generated per test session (no key is stored in the repository);
- `mint(**claims)` for good tokens;
- switches for an empty JWKS, a rotated key, a 401 user call, a 5xx and an `id` mismatch.

- **Unit, `tests/unit/auth/test_supabase.py`.**
  - A good token gives an identity.
  - A table-driven **known-bad fixture** (`BAD_TOKENS`) with one row per check, each asserting its reason code:
    - `alg: none`;
    - HS256 signed with the public key's bytes (`alg`);
    - RS256 with an unknown `kid`, and the refetch happening once and throttled;
    - a known `kid` signed by another key;
    - a tampered payload;
    - `iss` of another project, and `iss` missing;
    - `aud` `anon`, and `aud` missing;
    - `role` `service_role`, and `role` `anon`;
    - `exp` past, and `exp` missing;
    - `iat` in the future;
    - `iat` older than the maximum age with a still-valid `exp`;
    - a `sub` that is not a UUID;
    - `is_anonymous: true`;
    - a 5,000-character token;
    - broken base64.
  - Also: the user call answering 401 (`revoked`), a different `id` (`mismatch`), and a 5xx (`upstream`, nothing counted); `user_endpoint` mode with an empty JWKS; the JWKS cache TTL.
- **Unit, `tests/unit/auth/test_leafout_sign_in.py`** (fake `UserRepo` and clock):
  - linking by `sub`; linking by verified email (sets `supabase_sub`);
  - an unverified email: never links, and the account is created without an email;
  - `email_taken`; a phone-only account (`email` NULL);
  - the email updated on change;
  - recheck: due with no cookie ends the session, due with the same `sub` slides, a different `sub` swaps;
  - `own_sign_in=false` turns Google and the link off and changes `providers`;
  - `anonymous_allowed` with `Providers` (auto, true, false);
  - the bridge's captured log lines contain no token, cookie value, `sub` or email.
- **Unit, `tests/unit/api/test_security.py` additions.**
  - `Origin: https://leafout.in` with `Sec-Fetch-Site: same-site` on `POST /api/match` gives 403.
  - `Origin: https://app.leafout.in` with Host `app.leafout.in` passes.
  - `same-site` with no `Origin` passes (unchanged behaviour, pinned).
- **Storage.** The repo against Postgres (`TEST_DATABASE_URL`, skipped without it): `uq_users_supabase_sub`, migration 0011 upgrading on top of 0010, and a NULL email allowed if the user approves that part.
- **Integration, `tests/integration/test_leafout_sign_in.py`** (the whole app with `FakeSupabase` as transport):
  - `GET /api/me` with a good `leafout-access` cookie answers with the user and sets `tayyari_session` with host-only, HttpOnly, SameSite=Lax flags, plus Secure in prod;
  - the next request with that session makes no Supabase call;
  - a bad token is anonymous with no `Set-Cookie`;
  - over the `auth_bridge` limit is anonymous;
  - `POST /api/auth/logout` gives 204 and clears the cookie;
  - `POST /api/auth/magic` gives 404 with own sign-in off;
  - a cross-subdomain `POST /api/match` gives 403;
  - anonymous match is refused with `auto` once Leafout is configured;
  - the 308 from another host with `redirect_other_hosts`, but not for `/api/*`;
  - an NDJSON route sets the cookie on its first bridged request.
- **Frontend.** `npm run check` (types for `MeResponse`). `scripts/walkthrough.md` gains a step: sign in on leafout.in, open app.leafout.in, be signed in, sign out from the app, find both ended. This step can be walked only on the real domains, because localhost cannot share a `.leafout.in` cookie. A local variant uses `*.leafout.test` hosts in the hosts file with a dev certificate, or it is left unverified and said so.

### Waves and files

- **L1a, backend.**
  - New files: `auth/supabase.py`, `auth/jwks.py`, `api/session_cookie.py`, `tests/fakes/supabase.py`, the tests above, and migration `0011_leafout_identity.py`.
  - Edited: `auth/service.py`, `auth/google.py` (uses `jwks.py`), `auth/magic.py` (the `enabled` gate), `api/deps.py`, `api/routes/auth.py`, `api/routes/me.py`, `api/static.py` (`/signout` route and the host redirect), `contract/auth.py`, `storage/repos/users.py`, `core/redact.py`.
  - Shared files, edited by this wave alone: `storage/tables.py`, `storage/ports.py`, `core/settings.py`, `container.py`, `backend/.env.example`, the guard window list.
  - `api/security.py` gets tests only, no change.
  - No new dependency: `joserfc` and `httpx2` are already in.
- **L1b, app frontend**, after the canvas board. Files: `shared/auth.ts`, `src/auth/` (the sign-in sheet becomes a link, `useMe` handles `reauth`, the account menu, a new `SignOut.tsx`), `src/lib/api.ts`, and the router entry for `/signout`.
- **L1c, site repo.** The note in §1, sent through Lovable or edited in `tayyari-career-intelligence`. It can land first, because the cookie does nothing until the app reads it.
- Docs: `docs/backend.md` § Sign-in, and `CLAUDE.md` Status, once built.

### 9 · Decisions (2026-10-19)

The user answered the open questions. Where this list and the spec above disagree, the decisions win; the full text is in the scratchpad file `L1-decisions.md`.

1. **Phone-only accounts:** allowed. `users.email` is nullable, and a phone-only person gets an account keyed by `supabase_sub`.
2. **Anonymous posting match stays on.** `auto` counts only the app's own providers (google, magic). Sign-in is needed only to save or claim, to buy, and for the account itself.
3. **Site sign-out scope:** `local`.
4. **Recheck interval:** 24 hours. An explicit sign-out on the site still reaches the app at once, through the cookie rules below.
5. **Old domain:** `TAYYARI_AUTH__REDIRECT_OTHER_HOSTS` (default false). Pages on the old host get a 308 to the public URL; `/api/*` keeps answering there.
6. **Sign-in methods:** every site method counts, including "Sign in with Lovable".
7. **Subdomains:** only the site and the app live under `leafout.in`.
8. **Design review:** no canvas. A browser review after the build.

**Amendments to §1, §2 and §4.**

- **Shared cookie.** The site sets `leafout-access` from its server, not from page script: HttpOnly, Secure, SameSite=Lax, `Domain=.leafout.in`, `Path=/`, 30 days, refreshed on every write. It still holds the access token only. Safari caps script-written cookies at 7 days, and HttpOnly keeps page scripts from reading the token.
- **Cookie absent ends the session.** For a session made from the site's cookie, a request with no cookie ends the app session at once. The response is anonymous, with no `reauth` and no redirect. The site deletes the cookie only on explicit sign-out or account deletion, so this is how a site sign-out reaches the app.
- **Expired token in the identity check.** A valid cookie with a different `sub` replaces the session only when its token is fully valid. An expired token still has its signature checked; if it has expired, the app ends the session and sets `reauth: true`.
- **Hold cookie (built).** `POST /api/auth/logout` and `DELETE /api/me` set `tayyari_bridge_hold`: host-only, HttpOnly, 600 seconds. It holds the first 32 hex characters of the sha256 of the site token the browser sent. While it matches the current site token, the bridge is skipped, so an old token cannot recreate a session. A new sign-in brings a new token, and it works at once.

## Follow-ups

Found while writing the documentation wave (2026-10-11). None blocks the landed waves; each is a change for a later wave.

- **Per-IP is the only meter for anonymous runs.** With no account there is no entitlement to charge, so a visitor's matches are limited by the client address alone (`TAYYARI_GUARD__ANON_MATCH_PER_DAY`, 3, on top of the ordinary per-address windows), and their answers, resumes and cover notes are only shed. An address that changes (a mobile network, a VPN) gets a fresh allowance; the daily spend ceiling and the global match cap are the backstop.

- **Resume and cover are not rebuilt after answers.** A stored resume or cover note stays as it was when it was written; there is no rebuild control yet.
- **Postings are never purged.** `postings` has no retention step (`storage/maintenance.py`).
- **No `shared/*.ts` mirror for `contract/match_api.py`.** The API shapes are typed by hand in `src/match/types.ts` and `src/match/api.ts`; the parity test covers `shared/match.ts` only.
- **Portfolio routes do not check the owner of a claimed profile.** `GET`, `PATCH` and `DELETE /api/portfolio/{profileId}` and the routes under it answer anyone who presents the profile id, as they did before accounts existed. A claimed profile's page should need its owner.
- **The avatar is not shown.** `users.picture_url` is stored, but the CSP `img-src 'self' data:` blocks Google's image host, and nothing renders it.
- **`pass30` auto-renew:** still undecided (v1: no renewal; a renewal is a new purchase).
- **Postgres-only tests need `TEST_DATABASE_URL`**, a direct (non-pooler) URL of a throwaway database. Without it they are skipped.
- **The per-match cost is not recorded.** The README and `docs/backend.md` quote about half a cent per match on Haiku 5.5, as measured, but the repository holds no recorded measurement; record it, with its method, before it is quoted again.
- **Account deletion has no screen.** `DELETE /api/me` exists; the account menu does not offer it yet.
- **Hand-off only without a webhook.** With `RAZORPAY_WEBHOOK_SECRET` unset, an order whose browser never returns after paying stays `created` and nothing is granted; set the secret and add the webhook on the cloud, or reconcile such orders by hand.

## Open items (the user decides)

- Prices for `pack3` and `pass30` in INR and USD.
- Whether `pass30` should auto-renew later (v1: no).
- The sender address for emails and the Google OAuth app (the user creates both and sets the secrets).
- Regenerate the Razorpay test key pair after local testing (the secret was shared in a chat), and create the live pair only when going live.
