# A1 · Auto-apply

Tayyari fills the employer's own application form for a role the person fits, shows every value and where it came from,
and submits exactly that set of values once the person approves it.

Status: allowed 2026-11-06 (`CLAUDE.md` rule 9, `project-soul.md` "What it does" 7 and "No spray-and-pray applying").
Designed here; not started. Evidence: `experiments/auto_apply/` (53 fill-only runs on 8 live postings, nothing
submitted; `RESULTS.md`, `PLAN.md`). Existing code to build on: branch `feat/auto-apply` (62 files, merges cleanly on
`main`; its own notes are in `BUILD_NOTES.md` on that branch).

Money is in rupees, with USD in brackets, at ₹94.48 per USD (2026-11-05).

## 1. The rules this design holds to

1. **Fit first.** An application starts only from a posting match whose hard-requirement gates did not block. Pasting
   an application link runs the posting match on that posting first. Discover's "Apply for me" does the same.
2. **The employer's own form only.** Greenhouse, Lever and Ashby at launch, then others. Never LinkedIn, Naukri,
   Indeed, Glassdoor or any board whose terms forbid automation (`apply/intake.py` keeps the manual-only list and never
   fetches them).
3. **Nothing reaches the employer before approval.** The preview fill runs behind a guard that blocks every non-GET
   request and every CAPTCHA provider. Greenhouse and Ashby upload the resume the moment it is attached, so the preview
   leaves the file input empty and shows the resume separately (measured in the experiment, `RESULTS.md` §7).
4. **The person approves every submit.** They approve a digest: sha256 over every selector, value and the resume's
   file hash. The submit run fills the same values again with no model call, attaches the resume, re-checks the
   digest, and only then clicks.
5. **A submit happens once.** A `submitting` marker is written before the click. A submit is never retried
   automatically. An unclear outcome (no confirmation page, a timeout after the click) goes to the person as "Check
   this one", with the employer's page linked.
6. **No CAPTCHA solving, no logging in for the person.** Standalone browsers are created with `solveCaptchas: false`
   and the guard blocks challenge providers. A challenge or login wall becomes `needs_you` with a hand-off link.
7. **Answers cite facts.** Custom questions are answered from the person's cited facts (rule 1 of `CLAUDE.md`), at high
   confidence only. Eligibility, salary, notice period, demographic and consent questions are never drafted; they come
   from saved details or are asked.
8. **Spend is reserved before every model or browser call** through `guard.SpendGuard`, and recorded in `llm_calls`.
   The old branch's AI fallback skipped this; it is a must-fix.
9. **Signed-in only.** No anonymous auto-apply, even while anonymous matching is on.

## 2. What the person sees

The screens follow `design.md` and are designed on a canvas before any frontend code (project rule). Five states:

| Screen | What it shows | Actions |
|---|---|---|
| **Apply for me** (from a match or a Discover card) | The role, the match verdict, the employer's host ("applies on boards.greenhouse.io"), what will be used: saved details, the resume (the saved one, or this match's tailored one), the price | Start, Change resume |
| **Your details** (once, editable later) | Name, email, phone, location, work authorisation, notice period, salary expectation (optional), links, the saved resume. Each field says which forms ask for it | Save |
| **Review** | Every field the form asks for, in the form's order: the value, its source (details, resume, a cited fact, your earlier answer, the model) and a confidence word. Custom answers show their cited facts. The resume is shown separately, marked "attached only when you approve". A screenshot of the filled form | Edit any value, Approve and send, Not now |
| **Needs you** | The questions Tayyari will not answer for them, or "this form needs you to sign in / pass a check", with the page linked | Answer, Open the form |
| **Your applications** | One list: preparing, needs you, ready to review, sent (with the confirmation screenshot and time), check this one, failed (refunded), and later stages (viewed, interviewing, offer, rejected) | Open, Mark a stage, Paste an email |

What changes from `feat/auto-apply`'s `/apply` screen: it no longer starts from a pasted link alone, and it gains
the separate resume line, "Check this one" and the refund mark.

## 3. How a run works

```
match (gates pass) ─► queued ─► preparing ─► ready_to_review ─► approved ─► submitting ─► sent
                                    │                                           │
                                    ├─► needs_you (question, CAPTCHA, login)    ├─► check_this_one (unclear outcome)
                                    └─► failed (refunded)                       └─► failed (refunded)
```

- **Worker.** A separate process leases `applications` rows (the database is the queue, as on the old branch). It
  drives a Browser Use **standalone browser** over CDP with Playwright, so the worker needs no local Chromium. Every
  run stops the browser in a `finally` with `PATCH /api/v4/browsers/{id} {"action":"stop"}`, because dropping the
  connection does not stop billing. CDP and live-view URLs are credentials: never logged or stored.
- **The guard.** Two layers, as in the experiment: the Manifest V3 extension (`experiments/auto_apply/common/guard.py`,
  declarativeNetRequest: block every non-GET request and challenge providers, allow only named read-only operations
  per ATS) and a Playwright route guard. A probe POST must come back blocked before any fill starts. The submit run
  lifts the guard only for the form's own host, only after the digest check.
- **The fill, cheapest first.**
  1. **Playbook replay, no model.** The ATS's saved playbook (field rules by label pattern, then type, then profile key;
     how to open the form; comboboxes, typeaheads, file widgets; success and error signatures). Measured: 5 of 6
     forms, ₹0.06 ($0.0007).
  2. **Custom questions only to a model.** Haiku 5.5 with the person's facts, cited. Answers the person confirms are
     kept per question text, so the same question at the next employer costs nothing.
  3. **No playbook, or a step failed: the field-list loop.** Our scanner reads the form into a structured field list
     with the visible buttons, a model returns actions, code executes and re-scans (up to 4 rounds). Haiku 5.5 first
     (₹0.25, 4 of 8 complete in the test), then Sonnet 5.5 if a required field is still empty (₹1.60, 6 of 8).
  4. **Later (v2): Browser Use's own agent** for unknown custom career sites, capped with `maxCostUsd`, never allowed
     to submit.
- **AI shrinks over time.** A successful step 3 trace becomes a draft playbook for that ATS or employer domain. It is
  promoted after 3 clean replays (each still reviewed by its person) and retired after 2 failures. Target: under 10%
  of Greenhouse, Lever and Ashby applications need a model for anything but custom questions.
- **Limits.** Per person: 10 applications a day at launch. Per employer domain: the old branch's per-hour cap and gap.
  Browser Use: 10 concurrent browsers on the free tier (about 400 fills an hour); back off on 429.

## 4. Cost and price

Our cost per application, from measured parts (`PLAN.md` §1): about **₹0.43** at launch (70% of forms covered by a
playbook) and **₹0.25** once 90% are, **₹1.06** for a form with no playbook yet. At 10,000 applications a month,
allowing for retries and forex and GST, that is about ₹6,600 at launch or ₹3,900 when mature.

Competitors charge ₹2.08 to ₹2.99 (Tsenta, USD plans) and ₹8.7 to ₹20 (JobsEye packs) per application. The price is
decision 2 below; the pricing idea from the experiment is ₹3 to ₹5 per **delivered** application or ₹299 for 100, the
first 10 free, and a failed or unclear application refunded. No annual plans (`project-soul.md`).

## 5. Privacy and terms (before launch)

- Consent screen before the first application: what is sent to the employer, that a cloud browser (Browser Use) and
  Anthropic process the form, that screenshots are kept for 30 days.
- Privacy page and terms updated; legal review of both.
- Read each launch ATS's own terms on automated applications (Greenhouse, Lever, Ashby). Only job boards were checked
  so far.
- Retention: screenshots and filled values purged on the same schedule as matches (`storage.maintenance`); the old
  branch defines `SCREENSHOT_DAYS` but never wires the purge.
- Resume uploads: type and size checked (PDF or DOCX, at most 5 MB) by content, not only extension.

**Public copy to change at launch, not before:** `src/site/pages/JobSearch.tsx` ("It will not apply for you") and the
landing page's "no mass-apply" line (still true, but should mention the approved auto-apply). Nothing public changes
until the feature is live.

## 6. Build order

One engineer who knows the repo; days are rough. Each step ends with `npm run check:all` green and new tests.

| Step | What | Files (mostly from `feat/auto-apply`) | Days |
|---|---|---|---|
| A1.1 | Merge `feat/auto-apply` in. Fix its review must-fixes: sign-in required, the `submitting` marker and no automatic submit retry, resume type check, retention purge, spend reservation for every model call | `apply/service.py`, `apply/engine.py`, `api/routes/apply.py`, `storage/repos/apply.py`, `storage.maintenance` | 4 to 5 |
| A1.2 | Browser Use standalone browsers in the worker: create with the guard extension and `solveCaptchas: false`, probe, stop in `finally`, cost into `run_metrics` and the ledger | `apply/browser.py` (new BU opener), `core/settings.py` (`BROWSER_USE_API_KEY`), `backend/.env.example` | 2 |
| A1.3 | Replace the computer-use fallback with the field-list loop (scanner, actions, verify), Haiku then Sonnet, through `ClaudeClient.structured` | `apply/agent/` (Gemini and computer-use adapters removed or parked), `apply/forms.py` (scanner from the experiment) | 3 |
| A1.4 | Built-in playbooks for Greenhouse, Lever and Ashby from recorded traces; comboboxes, typeaheads, the attach-at-submit resume rule; fixtures captured from live pages | `apply/playbook.py`, `apply/runner.py`, `tests/data/apply/` | 3 to 4 |
| A1.5 | Screens from the agreed canvas: Apply for me, Your details, Review, Needs you, Your applications; "Apply for me" on the match and on Discover cards | `src/apply/`, `src/match/MatchView.tsx`, `src/discover/`, `shared/apply.ts` | 3 to 4 |
| A1.6 | Fill-only test on 20 to 30 live postings with the test identity, never approved; then a closed beta with real approvals | `experiments/auto_apply/` harness | 2 to 3 |
| A1.7 | Price and entitlements (refund on failure), consent screen, privacy and terms text | `billing/`, `src/apply/` | 2, plus legal review |

v2, after the beta: the playbook learning loop and per-ATS metrics, the cached answer bank, Browser Use's agent for
custom sites, a hand-off lane with a live-view session for CAPTCHA and login-first ATSs (Workday), Gmail stage
tracking under its own consent, the match's tailored resume attached by default, more ATSs (SmartRecruiters, Workable,
Recruitee, iCIMS).

## 7. Checks and tests

- Unit tests with fakes for the stage machine, the digest, the `submitting` marker (a crash after the marker never
  resubmits), the guard rules, the scanner over saved HTML, and the field-list loop over a scripted model.
- A guard test that proves no non-GET request leaves the preview fill and that the resume input stays empty.
- The layering test keeps `apply` away from `match` internals.
- Before beta: the fill-only live test (A1.6) published with honest numbers: completed, needed the person, failed.

## 8. Decisions for the user

1. **Approval mode.** This design approves every submit one by one (rule 4). A batch "approve these 5" screen is
   possible later, still one digest per application. Auto-submit without review is not in this design.
2. **Price.** Per delivered application in rupees, packs, or inside the ₹399 applying month; how many free.
3. **Spend for the live test.** A Browser Use top-up (from $5, about ₹472) for the 20 to 30 posting fill-only run.
4. **Where the worker runs.** FastAPI Cloud may run it beside the app now that it needs no local browser; otherwise a
   small separate host. To be confirmed in A1.2.
