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
| I1 | Evidence-grounded mock interview | M2 | later | not started |
| X1 | ESCO + O*NET skill table, canonical skill ids | M2 | later | not started |
| D1 | Discover: personal, location-aware jobs with direct apply links | M2 | Opus + Sonnet | v1 built 2026-10-16 (employers' own boards, no map); rest in docs/discover-later.md |

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
5. `build_radar(profile.domains, posting.requirements, coverage)` → role expectation per existing domain from requirement weights (`domainHint` mapped to the profile's domain names by a small alias step; unmapped requirements form an "Other" axis only if ≥ 2); "you" = the profile's domain mastery. No ESCO yet (wave X1).
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
- **I1 · Grounded mock interview.** Claim ledger from facts + git-history decision signals; slot blueprint allocated by code; Surface/Push/Floor ladders; verdicts Defended/Shaky/Couldn't defend/Not assessed with verbatim-quote checks; Say/Avoid/Fix cards; readiness computed in code. Research §4.7.
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

---

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
