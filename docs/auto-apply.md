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
   "Apply for me" is offered only after a cheap check that the posting is live and that its ATS is one we can fill;
   otherwise the card says "Open on the employer's site". Nothing is charged before that check. Tsenta charged a
   credit for a posting that was already gone, then refunded it (`docs/research/tsenta-teardown.md` §6).
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
   from saved details or are asked. Work authorisation and sponsorship are stored **per country**: a question about a
   country with no saved answer goes to Needs you and is never inferred (Tsenta answered "eligible to work in the
   United States: Yes" from an India-only authorisation; `docs/research/tsenta-teardown.md` §4). Voluntary self-ID
   answers default to "Decline to answer" unless the person has switched on "use on forms".
8. **Spend is reserved before every model or browser call** through `guard.SpendGuard`, and recorded in `llm_calls`.
   The old branch's AI fallback skipped this; it is a must-fix.
9. **Signed-in only.** No anonymous auto-apply, even while anonymous matching is on.

## 2. What the person sees

The screens follow `design.md` and are designed on a canvas before any frontend code (project rule). Canvas (2026-11-06, with sample data: Apply for me, Your details, Filling, Review, Needs you and outcomes, Applications, Ready for you on a phone; and since 2026-11-07, the entry points on a match, Discover's cards, a role's page and Prepare, and the first-time questionnaire): https://claude.ai/artifact/CsN9PPninRZ3Z1j5vx6Nh5. Five states:

| Screen | What it shows | Actions |
|---|---|---|
| **Apply for me** (from a match or a Discover card) | The role, the match verdict, the employer's host ("applies on boards.greenhouse.io"), what will be used: saved details, the resume (the saved one, or this match's tailored one), the price | Start, Change resume |
| **Your details** (once, editable later) | Name, email, phone, location, work authorisation, notice period, salary expectation (optional), links, the saved resume. Each field says which forms ask for it | Save |
| **Review** | Every field the form asks for, in the form's order: the value, its source (details, resume, a cited fact, your earlier answer, the model) and a confidence word. Custom answers show their cited facts. The resume is shown separately, marked "attached only when you approve". A screenshot of the filled form | Edit any value, Approve and send, Not now |
| **Needs you** | The questions Tayyari will not answer for them, or "this form needs you to sign in / pass a check", with the page linked | Answer, Open the form |
| **Your applications** | One list: preparing, needs you, ready to review, sent (with the confirmation screenshot and time), check this one, failed (refunded), and later stages (viewed, interviewing, offer, rejected) | Open, Mark a stage, Paste an email |

What changes from `feat/auto-apply`'s `/apply` screen: it no longer starts from a pasted link alone, and it gains
the separate resume line, "Check this one" and the refund mark.

From the Tsenta teardown (`docs/research/tsenta-teardown.md` §5):
- **Review is data, not a browser.** Fields are grouped the way the ATS groups them, with a count per group. Choice
  fields show the chosen option among the others. Tabs are Form, Resume, Cover and Job, with Prev and Next across
  applications (arrow keys work). Each value carries its source, which Tsenta's view does not.
- **Two stages, then a streamed fill.** First the resume (keep or tailor), then the form fill, streamed field by field
  as a waiting state, then the form review. Status words are honest: "Filling", "Waiting for you", "Sending".
- **Review lists every value that will be sent,** locked ones included, read-only and with their source. Long answers
  get full-height text areas. Signature and attestation fields are always the person's to type. Tsenta's review hid 1
  of 15 personal fields (a typed signature among them) and let model-written free text overclaim
  (`docs/research/tsenta-teardown.md` §8–9).
- **Status tabs carry reasons and a one-tap fix.** "Screening questions required" opens exactly those questions, and
  answering them requeues the application.
- **Emails are attached to their application** in a side rail, labelled by kind (received, interview, rejection,
  offer).
- **The lean answer to Tsenta's Auto Apply lanes is a daily "Ready for you" stack.** Fills are prepared for new roles
  that pass the gates, and the person approves them one by one in a stack (swipe or keys), each with its own digest.

### 2.1 Where "Apply for me" appears (decided 2026-11-07)

Today every apply is a link to the employer's site. Wherever an employer page is reachable and its ATS is one we
can fill, **Apply for me becomes the primary action**. The employer's link stays as a quiet "or apply yourself on
<host> ↗".

The button opens one choice:
- **Fit my resume to this role, then apply.** The match's resume, the one "Build my resume for this role" makes today.
  The person checks it before the fill.
- **Apply with my current resume.** The saved upload, as it is.

| Place today | File | What changes |
|---|---|---|
| Match result: "Build my resume for this role" (primary) and "Close the gaps · N questions" | `src/match/MatchView.tsx` | The primary becomes **Apply for me ▾** with the two choices. "Build my resume for this role" moves to a quiet "Only build the resume". "Close the gaps" stays. On a pasted match (no posting address) the choice also asks for **the link to the application form**, and we check it is the same posting before filling. On a Stretch, a note suggests answering the open questions first, without blocking. |
| Match from Discover: the origin strip's "Apply on <host> ↗" | `src/discover/OriginBar.tsx` | **Apply for me** first, the host link after it. No link to ask for. |
| Discover card: "Apply on <host> ↗" (primary), Prepare, See how you fit, Save / Compare / Not for me | `src/discover/roles/JobCard.tsx`, `parts.tsx` (`ApplyLink`) | The primary becomes **Apply for me**. A card has only the quick fit, so it **runs the full posting match first** (about 20 s, shown on the card as steps), then opens the choice. |
| Discover role page, "What next": apply link, Prepare for this role, See how you fit, Save | `src/discover/roles/Focus.tsx` (`PanelActions`) | **Apply for me** on top, with the two resume choices inline. Then Prepare, See how you fit and Save as now. |
| Discover phone deck: Not for me, Details, Save, apply link | `src/discover/roles/Deck.tsx` | The apply link becomes **Apply for me**. |
| Prepare's checklist, step "Apply on <host> ↗" | `src/discover/prepare/sections.tsx` | The step offers **Apply for me** with the resume just tailored, and ticks itself when the employer confirms. |
| Compare: "Apply on <host>" per column | `src/discover/compare/CompareView.tsx` | **Apply for me** per column. |
| The Weekly paper's job links | `src/weekly/EditionPaper.tsx` | Stays a link, now to the role's page in the app, where Apply for me is. |
| "Your applications" (browser storage, Discover) | `src/discover/applications/` | Becomes the server-side Applications page (screen 6). |

**When Apply for me is not offered, the card says why:**
- **The ATS needs the person's own account** (Workday and similar): the primary is the employer's link, with a line
  saying why.
- **A hard requirement fails:** the button is shown off, with the reason. The person can still apply themselves.
- **The posting is closed:** nothing is offered, and nothing is charged.
- **Already applied to this posting:** the button opens that application.

### 2.2 The first time: a questionnaire (decided 2026-11-07)

**When it runs.** The first Apply for me opens a short questionnaire: six steps, about 5 minutes. After that the
button goes straight to the form, and the answers live in Your details (screen 2).
- Anything the resume already says is prefilled for the person to check.
- A step that doesn't apply is skipped. A fresher sees no CTC, and only a nurse sees the nursing council.
- Any question can be skipped; a form that needs the answer later asks for it then.
- Canvas screens 11 and 12.

**Where the questions come from:**
- Tsenta's application defaults (`docs/research/tsenta-teardown.md` §2).
- What Indian forms ask that a resume doesn't say: notice period and buyout, current and expected CTC, marks,
  backlogs, bonds, languages, registrations, vehicle (`docs/research/ai-launch-audit.md`, findings 1 and 2).

| Step | Questions | Rules |
|---|---|---|
| 1 · You | Full name as on ID, email, mobile, current city, cities you'd work in, languages you can work in, date of birth (optional) | Your own email only. Date of birth is sent only where a form requires it (age limits in some drives and government roles) |
| 2 · Work right now | Status (student / working / serving notice / between jobs), current company and designation, total experience (years, months; internships counted only if the person says so), notice period (immediate, 15, 30, 60, 90 days; buyout possible; last working day when serving notice), current CTC (₹ lakh a year), expected CTC (a figure / "open to discussion" / ask each time) | Current CTC is filled only into forms that require it, and never shown on a resume or portfolio. A number-only expected-CTC field with "open to discussion" saved goes to Needs you |
| 3 · Education and marks | Highest qualification, college, year of passing, graduation score, class 12 and class 10 scores, active backlogs, backlogs ever (cleared), a gap of 6 months or more | Scores are copied as given: CGPA is never converted to a percentage. A form that wants the other goes to Needs you |
| 4 · Registrations and licences | Profession-specific: nursing council registration, ICAI membership or articleship, CTET or TET, driving licence and own two-wheeler, passport | Shown when the resume or matched roles suggest them |
| 5 · Where and how you work | Relocation (yes / within my cities / ask each time), work mode, shifts (day / rotational / night), service bond (up to 1 year / no bonds / ask each time), work authorisation by country | Bond defaults to "ask each time": it is a decision per offer. Country rules as in §1 rule 7 |
| 6 · What we leave to you | Never stored, always asked on the employer's page: PAN, Aadhaar, bank or any ID number; caste or category, religion, marital status, father's name; signatures and declarations; "do you know anyone here", referrals, earlier applications to this employer. Gender: off by default ("Prefer not to say" where allowed) | A form requiring any of these goes to Needs you, and the person types it on the employer's site |

**The answers also feed the match.** They become facts "from you" in the fact book. A match then stops calling a
notice period, a registration or a licence a gap. This fixes the launch audit's finding 1 for anyone who has applied
once.

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
5. **Which email goes on applications, and how replies are tracked.** Tsenta puts its own hosted mailbox on every
   form, which powers its tracker and OTP capture but locks the person in. Proposed: the person's own email on every
   form; tracking through a per-person forwarding address (one Gmail or Outlook filter) or pasted emails, and Gmail
   read access (`gmail.readonly`, its own consent) later.
