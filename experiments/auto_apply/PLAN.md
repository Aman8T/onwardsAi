# Lean production auto-apply: proposed plan

Based on the experiment in this folder ([RESULTS.md](RESULTS.md)): 45 runs, 8 real postings, nothing submitted.
Money is in USD and INR at ₹94.48 per USD (2026-11-05). Card payments from India for these USD services can add a forex
markup and 18% GST, so budget up to about 22% more in rupees than the figures shown. That multiplier is an assumption.

Before any of this is built: `project-soul.md`, `CLAUDE.md` (non-negotiable 4) and `docs/research/job-discovery.md`
("Auto-apply | Avoid") currently rule auto-apply out. Building it is decision 1 at the end.

## 1. Which architecture wins: a hybrid shaped like Arm B

**Our code drives a Browser Use cloud browser. A saved playbook fills known forms with no model at all. Claude is called
only for what is new: a form we have never seen, and job-specific questions. Browser Use's own agent is not in the main
path.**

| Setup | Measured or estimated | Full success | Cost per application |
|---|---|---|---|
| A: Browser Use agent, `bu-ultrafast` | measured | 1/8 | $0.0077 (₹0.72) |
| A: Browser Use agent, `bu-fast` | measured | 4/8 | $0.0265 (₹2.50) |
| B: Claude Sonnet 5.5 + BU browser | measured | 6/8 | $0.0169 (₹1.60) |
| C: playbook replay, no LLM | measured | 5/6 | $0.0007 (₹0.06) |
| B with Haiku 5.5 instead of Sonnet | **estimate, untested** | ? | about $0.0017 (₹0.16) |
| **Hybrid, MVP** (70% replay + Haiku for custom questions, 30% B with Sonnet) | estimate | target 75%+ | **about $0.0063 (₹0.59)** |
| **Hybrid, mature** (90% replay) | estimate | target 85%+ | **about $0.0032 (₹0.30)** |

Hybrid assumptions: a replayed application costs C's measured $0.0007 plus one Haiku call for its custom questions
(about 3k input / 1k output tokens, $0.0008, rounded to $0.001), so $0.0017 (₹0.16). Fallbacks cost B's measured
$0.0169 (₹1.60).

Why B-shaped and not A:
- **Higher quality, measured:** 6/8 vs 4/8, the resume attached on every form vs 3 of 7, 84 s vs 223 s median.
- **Fewer tokens by orders of magnitude:** B reads a structured field list once or twice (2k to 15k tokens); A re-reads the page every step (270k to 2.8M tokens). A is cheap today only because its model is cheap per token.
- **Replayable:** B's actions are selectors and values, so a successful run becomes a playbook. A's trace is JavaScript against its own element indices and cannot be replayed directly.
- **Controllable:** we own the guard, the approval digest, retries and limits, and we can verify the page instead of trusting an agent's self-report (A overclaimed in our runs).

Where Browser Use's agent still earns a place: as a **last-resort fallback for the long tail of custom career sites**
(it found the Workable form that B missed, and succeeded on bunq). Use it only after B fails, capped with `maxCostUsd`, and
never let it submit.

## 2. "AI shrinks over time"

Most of this already exists on `feat/auto-apply` (`backend/tayyari/apply/`): intake and fingerprinting, playbooks, the
digest-bound approval, the stage machine and the tables. The experiment says what to change.

1. **Fingerprint the ATS** from the URL, then the page (`apply/intake.py`): Greenhouse, Lever, Ashby, Workday, known custom hosts, unknown.
2. **Saved playbooks per ATS**, later per employer domain: field rules (label pattern, then type, then the profile key), how to open the form, how to handle comboboxes, typeaheads and file widgets, plus success and error signatures. Seed them from B traces like the ones in `runs/*-B-*/trace.json`.
3. **Replay with no LLM** (Arm C): standard fields come from the playbook and the person's saved details. Only the **custom questions** go to a model, Haiku 5.5 first, with answers cited from the person's facts as the branch already enforces. Answers the person confirms are cached per question text, so the same question at the next employer costs nothing.
4. **AI only as a fallback** when there is no playbook, a playbook step fails, or the form changed (a different field set or digest). The order: B with Haiku, then B with Sonnet, then Browser Use's agent for unknown custom sites.
5. **The loop that turns fallbacks into playbooks:** a successful fallback's trace becomes a *draft* playbook for that ATS or domain. It is promoted after 3 clean replays (each still reviewed by its person) and demoted on 2 failures. Track the share of applications that needed a model per ATS; the target is under 10% on Greenhouse, Lever and Ashby.

## 3. What it takes to scale

- **Concurrency:** the free Browser Use tier allows 10 concurrent browsers. At about 90 s per fill that is about 400 fills an hour, far above 10,000 a month (about 14 an hour on average). A $100 one-time top-up raises the limit to 50 if peaks need it. No subscription.
- **Cost caps:** reserve spend before every model call through the existing `guard.SpendGuard` and daily ceiling. The branch's AI fallback skipped this, which is a known gap. Set `maxCostUsd` on any Browser Use agent run, cap model rounds per application (B used 1 to 4), and keep a per-person daily cap.
- **CAPTCHAs and logins:** never solve or bypass them. Create standalone browsers with `solveCaptchas: false`, block challenge providers in the guard, and when a challenge or login wall appears, hand the person a link (ideally a live-view session) to finish it themselves. Workday and other login-first ATSs go in this hand-off lane.
- **Rate limits:** keep the branch's per-site politeness (runs per domain per hour, a gap between runs), plus Browser Use's API limits (25 requests/s per project, back off on 429).
- **Failure handling:** retries with backoff on every external call (our runs hit a connection reset and a billing-endpoint error). Retry fills freely, but **never retry a submit automatically**: write a "submitting" marker before the click and send any unclear outcome to a person to check. This also fixes the double-submit risk found in the branch review.
- **Human approval before every submit:** the person sees every value and its source and approves a digest of exactly those values. Because **Greenhouse and Ashby upload the resume to the employer as soon as it is attached**, the preview must be built with the file input left empty and the resume shown separately. The approved run then re-fills, attaches the resume and submits in one pass, checking the digest just before the click.
- **The guard in production:** keep the "no non-GET request until approved" rule during preview fills. Grow the read-only allowlist per ATS by inspection, as was done for Ashby.
- **Tracking and stage updates:** the branch's stage machine (applied, viewed, interviewing, offer, rejected), fed by pasted emails now and Gmail (`gmail.readonly`, separate consent) later. Plus: confirmation-page screenshots as proof, and a daily digest.
- **Privacy and terms before launch:** sign-in required (no anonymous auto-apply), privacy page and consent updated (screenshots, data sent to employers, model processors), retention purges wired, and file-type checks on resumes. These are carried over from the branch review.

## 4. Beating Tsenta and JobsEye on value

What they charge (from their own pages, seen 2026-11-05; several claims unverified, see the research notes in RESULTS.md):

| Product | Plans | Price per application |
|---|---|---|
| Tsenta (US) | $19 / $39 / $99 a month (₹1,795 / ₹3,685 / ₹9,354) for 600 / 1,500 / 4,500 applications; top tier auto-applies without review | $0.022 to $0.032 (₹2.08 to ₹2.99) |
| JobsEye (jobseye.in, India) | ₹399 / ₹699 / ₹1,299 one-time packs for 20 / 50 / 150 applications; pay only for delivered applications | ₹8.7 to ₹20 |
| **Onwards hybrid (our cost)** | | **₹0.30 to ₹0.59** (₹1.60 when a model fills the whole form) |

- **Copy:** JobsEye's "pay only for delivered applications, refund failures" and one-time packs (no annual lock-in fits `project-soul.md`); Tsenta's diff view of every change to the resume; a confirmation email or screenshot as proof of each application; broad Greenhouse/Lever/Ashby coverage first.
- **Skip:** volume as the product (Tsenta's 4,500 a month), applying without review, an "Aggressive" resume mode, LinkedIn Easy Apply and other boards whose terms forbid automation, and any CAPTCHA solving.
- **Do differently, using matching that is already stronger:**
  - **Apply only where the match says the person fits:** the posting match's hard-requirement gates run first, so applications are fewer and better.
  - **Every answer is cited to the person's own facts, and the person approves every submit.**
  - **Price in rupees per delivered application, well under JobsEye.** For example ₹3 to ₹5 an application, or ₹299 for 100, with the first 10 free. This is a pricing idea for decision 3, not a recommendation backed by data.
  - **Publish honest numbers:** how many applications needed a person, and how many failed.

## 5. Phased build

Rough effort in engineer-days, for one engineer who knows the repo; real numbers depend on how much of
`feat/auto-apply` is reused.

**MVP (about 12 to 18 days)**
1. Start from `feat/auto-apply` (schema, approval digest, stages, tracker UI) and fix the review's must-fixes: the submit "in progress" marker, required sign-in, resume file-type check, wired retention purge, spend reservation. (4 to 5 days)
2. Swap the worker's local Chromium for Browser Use standalone browsers with the guard extension and `solveCaptchas: false`. The worker needs only the Playwright Python package, not a local browser, which may let it run beside the app; confirm on FastAPI Cloud. (2 days)
3. Replace the AI fallback with the Arm B loop: structured field list, a model returns actions, verify on the page. Haiku first, Sonnet on failure. (3 days)
4. Built-in playbooks for Greenhouse, Lever and Ashby from recorded traces, with comboboxes, typeaheads and the attach-at-submit resume rule. (3 to 4 days)
5. Fill-only tests on 20 to 30 live postings with a test identity, then a closed beta with approvals by real users. (2 to 3 days)
6. Privacy page, terms and consent text (with legal review). (outside engineering time)

**v2 (about 15 to 25 days)**
- The playbook learning loop (draft, promote, demote) and per-ATS metrics; the cached-answer bank per question text.
- Browser Use agent fallback for unknown custom sites, capped and submit-proof.
- Gmail stage tracking; tailored resume per match attached instead of the saved one.
- Hand-off lane for CAPTCHA and login-first ATSs (live-view session for the person); Workday.
- More ATSs (SmartRecruiters, Workable, Recruitee, iCIMS) from fallback traces; the concurrency top-up if needed.

## 6. Monthly running cost

Variable cost only (model + browser + traffic), from the per-application figures in section 1. Add about 25% for retries
and failed fills; fixed costs are small (no Browser Use subscription; an optional $100 one-time concurrency top-up).

| Applications per month | A `bu-fast` | B (Sonnet) | Hybrid MVP | Hybrid mature | Replay only |
|---|---|---|---|---|---|
| 100 | $2.65 (₹250) | $1.69 (₹160) | $0.63 (₹60) | $0.32 (₹30) | $0.07 (₹7) |
| 1,000 | $26.50 (₹2,504) | $16.90 (₹1,597) | $6.30 (₹595) | $3.20 (₹302) | $0.70 (₹66) |
| 10,000 | $265 (₹25,037) | $169 (₹15,967) | $63 (₹5,952) | $32 (₹3,023) | $7 (₹661) |

With 25% retry overhead and up to 22% forex and GST, the hybrid at 10,000 applications a month stays at about ₹9,100
(MVP mix) or about ₹4,600 (mature mix). The running cost of auto-apply is small next to its engineering and support cost.

## 7. Open risks

- Small sample (8 jobs, one run each): the success rates need a 20 to 30 posting fill-only test before launch.
- ATS vendors' own terms on automated applications have not been checked (only job boards were, by the branch).
- Arm B with Haiku is untested; if quality drops, the hybrid's model cost moves from about ₹0.16 toward ₹1.60 for affected applications.
- Workable's form defeated B for reasons not yet known; custom sites will keep needing fallbacks or hand-offs.
- Live pages change; playbooks need drift detection (it is built into the digest check) and demotion.

## 8. Decisions for you

1. **Go or no-go on auto-apply itself.** `project-soul.md` and the research doc say "Avoid". If it is a go, those docs, the site's claims and an ATS-terms check need updating, and every submit stays human-approved.
2. **Architecture:** build the hybrid on top of `feat/auto-apply`, with a Haiku 5.5 test of Arm B as the first step, or start a new branch.
3. **Pricing and spend:** a per-delivered-application price in rupees (or packs), and approval to top up Browser Use credits (from $5) for the 20 to 30 posting fill-only test.
