# A1 · Auto-apply

Tayyari fills the employer's own application form for a role the person fits, shows every value and where it came from,
and submits exactly that set of values once the person approves it.

Status: allowed 2026-11-06 (`CLAUDE.md` rule 9, `project-soul.md` "What it does" 7 and "No spray-and-pray applying").
Built 2026-11-08 on `feat/auto-apply-build` (A1 W1 to W5), tested on fakes only and not deployed; how it is built is in
`docs/backend.md` ("Auto-apply"), and where the build differs from this text is in §3.3. Evidence:
`experiments/auto_apply/` (53 fill-only runs on 8 live postings, nothing submitted; `RESULTS.md`, `PLAN.md`). The build
started from branch `feat/auto-apply`; `BUILD_NOTES.md` says what changed from it.

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

The screens follow `design.md` and are designed on a canvas before any frontend code (project rule). Canvas (2026-11-06, with sample data: Apply for me, Your details, Filling, Review, Needs you and outcomes, Applications, Ready for you on a phone; and since 2026-11-07, the entry points on a match, Discover's cards, a role's page and Prepare, and the first check plus just-in-time questions): https://claude.ai/artifact/CsN9PPninRZ3Z1j5vx6Nh5. Five states:

| Screen | What it shows | Actions |
|---|---|---|
| **Apply for me** (from a match or a Discover card) | The role, the match verdict, the employer's host ("applies on boards.greenhouse.io"), what will be used: saved details, the resume (the saved one, or this match's tailored one), the price | Start, Change resume |
| **Your details** (what forms will be told) | Every answer with its source: your resume, you (the first check, or the form you answered it on, with the date) or a default. "Not asked yet" rows for what no form has needed, each answerable early but never required. A "Still true?" one-tap check for answers that go stale. Self-ID (off by default), the never-stored list, the default resume. Filters: all, from your resume, from you, defaults, to check, not asked yet | Edit, Answer, Still true |
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
- **Apply with my current resume.** The person's default resume on `/resume` when it has a finalised version (the very
  file the `/resume` download gives, §3.4); with no finalised default, the saved upload as it is. The start screen and
  the review name it ("Your default resume: <title>, finalised <date>", a `.stamp` "default", a link to `/resume`). A
  draft is never sent.

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

### 2.2 Asking less: a 30-second check, then only what a form needs (decided 2026-11-07)

A questionnaire answered all at once is too heavy (the user's call, 2026-11-07). Questions come in three layers, and
none is asked twice.

1. **Nothing to answer: read from the resume.** Name, email, mobile, city, current role, full-time experience (from
   the dates), education (degree, college, year, score when the resume has it). The person only checks them.
2. **The first check, about 30 seconds** (canvas screen 11). The first Apply for me shows the resume facts, each with
   Edit, and asks just two things almost every Indian form asks:
   - the notice period (immediate, 15, 30, 60 or 90 days, or serving notice);
   - whether they can work in India without sponsorship.

   Safe defaults are set and shown, each changeable:
   - expected CTC "open to discussion";
   - self-ID "prefer not to say";
   - relocation and bonds asked per offer.

   "Skip, ask me on the form" is always there.
3. **Just in time** (canvas screen 12). While filling, a field we have no answer for becomes a question on that
   application, with the employer named and the reason ("required by Hollis Health").
   - "Save it" is on by default, so no form asks again.
   - **One answer covers every waiting form that asks the same thing** ("Also asked by 2 more forms waiting").
   - Per-offer questions (bond, relocation for this role) are not remembered unless the person ticks it.
   - Never-stored items are left empty for the person to type on the employer's page.

**Keeping answers true without re-asking.** Answers that go stale (notice period, current CTC, city) get a one-tap
"Still true?" check after 90 days, never the whole form. Your details (screen 2) shows what came from the resume, the
first check and forms, and what has not been asked yet.

**The question bank.** This is what can be asked, each item only in layer 3 unless it is listed in layers 1 or 2.
Where the questions come from:
- Tsenta's application defaults (`docs/research/tsenta-teardown.md` §2).
- What Indian forms ask that a resume doesn't say: notice period and buyout, current and expected CTC, marks,
  backlogs, bonds, languages, registrations, vehicle (`docs/research/ai-launch-audit.md`, findings 1 and 2).

| Group | Questions | Rules |
|---|---|---|
| You | Full name as on ID, email, mobile, current city, cities you'd work in, languages you can work in, date of birth (optional) | Your own email only. Date of birth is sent only where a form requires it (age limits in some drives and government roles) |
| Work right now | Status (student / working / serving notice / between jobs), current company and designation, total experience (years, months; internships counted only if the person says so), notice period (immediate, 15, 30, 60, 90 days; buyout possible; last working day when serving notice), current CTC (₹ lakh a year), expected CTC (a figure / "open to discussion" / ask each time) | Current CTC is filled only into forms that require it, and never shown on a resume or portfolio. A number-only expected-CTC field with "open to discussion" saved goes to Needs you |
| Education and marks | Highest qualification, college, year of passing, graduation score, class 12 and class 10 scores, active backlogs, backlogs ever (cleared), a gap of 6 months or more | Scores are copied as given: CGPA is never converted to a percentage. A form that wants the other goes to Needs you |
| Registrations and licences | Profession-specific: nursing council registration, ICAI membership or articleship, CTET or TET, driving licence and own two-wheeler, passport | Shown when the resume or matched roles suggest them |
| Where and how you work | Relocation (yes / within my cities / ask each time), work mode, shifts (day / rotational / night), service bond (up to 1 year / no bonds / ask each time), work authorisation by country | Bond defaults to "ask each time": it is a decision per offer. Country rules as in §1 rule 7 |
| What we leave to you | Never stored, always asked on the employer's page: PAN, Aadhaar, bank or any ID number; caste or category, religion, marital status, father's name; signatures and declarations; "do you know anyone here", referrals, earlier applications to this employer. Gender: off by default ("Prefer not to say" where allowed) | A form requiring any of these goes to Needs you, and the person types it on the employer's site |

**The answers also feed the match.** They become facts "from you" in the fact book. A match then stops calling a
notice period, a registration or a licence a gap. This fixes the launch audit's finding 1 for anyone who has applied
once.

## 3. How a run works

```
start checks pass ─► queued ─► filling ─► waiting_for_you ⇄ (answers) ─► approved ─► submitting ─► sent
                                  │                                         │            │
                                  ├─► needs_signin (login-first ATS)        │            ├─► check_this_one (no confirmation seen)
                                  ├─► closed (posting gone)                 │            └─► failed (refunded)
                                  └─► failed (refunded)                     └─► waiting_for_you (form changed)
any state before submitting ─► cancelled (by the person)
```

### 3.1 The end-to-end flow (agreed 2026-11-07)

The web requests only record decisions; the worker (inside the app, §8 decision 4) does all browser work; Postgres
holds the state.

1. **Start**: `POST /api/apply/applications {matchId | listingId, resume: "tailored" | "saved"}`. Code runs every
   check before any browser or model:
   - signed in, and a credit is free;
   - the posting is live (one fetch through the SSRF-guarded ingest);
   - its ATS is one we fill;
   - the match's hard requirements didn't block;
   - no earlier application to the same posting URL.

   If any check fails, nothing is created or charged. If they all pass, the row is created `queued` with a credit
   **hold**, not a charge.
2. **Fill pass (worker)**:
   - Lease the row and open a Browser Use browser with the guard; the probe must come back blocked.
   - Read the form into a field list and fill each field from the cheapest source: Your details and the answer bank
     (code), then resume facts, then the playbook, and only for custom questions Haiku then Sonnet, citing facts.
   - Whatever is still unknown becomes a question; the resume input stays empty.
   - Store the review (every field's selector, label, value, source and citation, the questions, a screenshot) and its
     digest. The row moves to `waiting_for_you`.
   - Stop the browser in `finally`.
3. **Answers**: `POST /applications/{id}/answers {questionKey, value, save}`.
   - A saved answer goes into the answer bank under a normalised question key.
   - Every other waiting application that asks the same key is updated and re-queued.
   - ID numbers are refused.
4. **Approve**: `POST /applications/{id}/approve {digest}`. It must equal the stored digest. The row moves to
   `approved`.
5. **Submit pass (worker)**:
   - Open a new browser and re-fill the same values, with no model call.
   - Attach the resume and recompute the digest. If it differs, the row goes back to `waiting_for_you`.
   - Commit the `submitting` marker, lift the guard for the form's own host only, and click.
   - The outcome:
     - the confirmation signature is seen → `sent`, and the hold is charged;
     - no clear outcome → `check_this_one`, never retried;
     - an error before the click → `failed`, and the hold is released.
6. **After**: replies come in by paste, by forwarding (§3.2) or, in v2, by mailbox access. A classifier moves the stage
   forward only. No reply in 3 weeks gives a one-tap "Heard back?" check.

**Reuse.** Tables come from the old branch's migration `0016`: `applications` (state, lease, review JSON, digest,
hold), `application_events`, `apply_profiles`, `ats_playbooks` and `run_metrics`. A new `answer_bank` maps a question
key to its value, source, date and "ask each time" flag.

**Changes from `feat/auto-apply`:**
- Local Chromium becomes Browser Use browsers.
- The computer-use agent becomes the field-list loop.
- New: the start checks, the credit hold, the `submitting` marker, the shared answer bank, and sign-in for all of it.

**Settled:**
- Re-fill at submit rather than keeping a browser open between review and approval: one extra session, about ₹0.1, and
  no idle billing.
- The MVP leaves the Ready-for-you stack, the playbook learning loop and Gmail to v2.

### 3.2 Confirmations and replies without a proxy mailbox (agreed 2026-11-07)

- **The application went through.** The submit pass reads the ATS's success signature (Greenhouse's thank-you page,
  Lever's `/thanks`, Ashby's success message, kept in the playbook). It stores a screenshot and the time, then marks
  `sent`. No email is needed; this is the proof on the Sent screen.
- **Replies.** The person's own email is on the form, so replies land in their inbox. Three ways in:
  1. **Paste or forward one email** to the application (MVP).
  2. **A personal receive-only forwarding address** (`apply+<token>@<inbound domain>`) with a ready Gmail or Outlook
     filter on ATS senders and "your application" subjects. Only forwarded mail reaches us, and only mail matching an
     application is kept. Gmail's forwarding-verification code is shown in the app.
  3. **Gmail or Outlook API, read-only, under its own consent** (v2; Gmail's read scope needs Google's security
     assessment).

  Opt-in plus-addressing (`name+leafout@gmail.com`) makes the filter one line; forms that reject `+` get the plain
  address. Matching is by employer, role and sent date. A classifier quotes the email, and when more than one
  application could match, the person is asked.
- **Verification codes.** A form that emails a code during submit pauses on that field and becomes `needs_you`
  ("Type the code Kestrel Pay emailed you"). The browser stays open about 10 minutes; a forwarded code fills it
  automatically. With no code, the row becomes `check_this_one`.

- **Worker.** A background task inside the app (or, with `TAYYARI_APPLY__WORKER_IN_APP=false`, its own process)
  leases `applications` rows (the database is the queue, as on the old branch). It creates a Browser Use **standalone
  browser** through Browser Use's REST API and drives it over CDP with Playwright, so the server installs no browser. Every
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

### 3.3 As built (A1, 2026-11-08)

The build follows §1 to §3.2 with the differences below. The first four change agreed wording, so they are written here
as proposals and the text above is left as agreed until the user says yes.

1. **The sign-in and challenge stage** (§1 rule 6): a login wall, a CAPTCHA or an emailed code is the stage
   `needs_signin` (the name in the §3 diagram) with a `stopReason` of `signin`, `challenge` or `verification_code`;
   `waiting_for_you` stays for questions and the review. Proposed: "becomes `needs_signin` with a hand-off link" in
   rule 6, and the same for the verification code in §3.2.
2. **The submit order** (§3.1 step 5): Greenhouse and Ashby upload the resume the moment it is attached, and the guard
   would block that upload, so the resume is attached after the marker. The built order: open the submit browser
   with the guard on; replay the review's fill script with no model; read the form back; recompute the digest with
   the sha256 of the resume that will be attached (a different digest goes back to `waiting_for_you`); check the
   submit control is there (missing is `failed`, before any click); commit `submitting`; lift the guard only for the
   playbook's submit and upload hosts; attach the resume and wait for the widget to show its name; click once. The
   success signature is `sent`; anything else after the click (an error, a timeout, error text, a CAPTCHA, a code
   prompt) is `check_this_one`.
3. **The start body** (§3.1 step 1): `{matchId, resume: "tailored" | "saved", listingId?, runId?, applyUrl?}`. The
   match is always required, because Discover and a pasted link run the posting match first (§1 rule 1); a listing
   gives the form's address server-side, and a pasted `applyUrl` must be the same posting as the match.
4. **The check endpoint**: `POST /api/apply/check` runs the start checks read-only and uncharged, before the match on
   Discover and before Start; the start runs them again.
5. **Worker and browser**: the worker runs inside the app (§8 decision 4); Browser Use is called over its REST API
   (v4) with no SDK, and nothing is installed for a local browser. A local Chromium (`TAYYARI_TEST_BROWSER=1`) is for
   offline development and tests only.
6. **Not in A1**: retention for applications abandoned before they finished, "I applied there" on a failed row, the
   Weekly's in-app role links, the price (§4), and the live fill-only test (A1.6).
7. **The employer domain** (§3.2 limits): on Greenhouse, Lever and Ashby, whose hosts every employer shares, the
   employer domain is the ATS host plus the employer's board (`jobs.lever.co/acme`); elsewhere it is the host. The
   per-hour cap and the gap count runs by it, and site playbooks are keyed by it.
8. **Resume facts** (§2.2 layer 1, §3.1 step 2): read in code, with no model, when Your details or the first check
   first loads and only until the first check is done: the name, current role, city and years of experience from the
   person's newest profile, the mobile from their newest match's fact book, and the email they sign in with. Education
   is not read (the profile holds no structured education); a form asks it and the answer is kept. A resume value
   never replaces an answer the person gave.
9. **"Fill it again" for a file**: a form waiting only for a file (the resume added in Your details, or the tailored
   resume finished on the match) is queued again by the person through the retry route; no upload or match edit
   re-queues it by itself.
10. **The idle worker**: while the queue is empty the wait between passes doubles from 3 s up to 2 minutes, so the
    database can scale to zero between uses; a start, an answer, an approval or a retry in the same process wakes the
    worker within one poll.

### 3.4 The default resume and its frozen copy (2026-11-11)

- **Which file.** `container.resume_of_application` is the one place that decides it, for the fill and again for the
  submit pass: `tailored` is the match's resume; anything else ("my current resume") is the profile's default resume
  (`resumes.is_default`) when it has a finalised version (`final` and `finalised_at`). An uploaded resume
  with no edit, move, put-back or leave-out (`resume.marks`; the export's plain-character folding is not an edit) goes as
  the person's ORIGINAL saved file, name and type as uploaded; if no saved bytes exist, or the default is an edited
  upload, a `path` or a `posting` resume, it is the PDF from `ResumeService.files` (the `/resume` download, attached as
  `<First>-<Last>-Resume.pdf`). A row marked
  default with no finalised version is passed over (a draft is never the file). With no finalised default the saved
  upload goes as before. If the default's file cannot be made, no file goes out and the form asks for one: the saved
  upload is never sent under the default's name.
- **Named before anything starts.** `POST /api/apply/check` with a match carries `defaultResume` (`ResumeRef`: id, title,
  finalised date, file name) when there is one, so the start screen names it.
- **Frozen copy.** When a fill finishes (and when a changed form is re-prepared) the review is stored together with
  `applications.resume_copy` (migration 0020): the resume's id and title, the finalised date, the variant as it stood,
  and the sha256 of the file in the digest. It is written only when its hash is the one the digest signs, and it is
  cleared when the application is prepared with another resume. A later edit, a new finalised version or a new default
  never changes a copy already stored; a sent application keeps it. The application's page says "Sent with: <title>, as
  finalised <date>" and `GET /api/apply/applications/{id}/resume-copy` returns the PDF exported from the frozen variant
  (the digest's hash, except for an unedited upload, whose original bytes are not kept with the application: its copy
  is marked ``source: upload`` and the download is our rendering).
- **The digest covers it.** The resume's sha256 is part of the digest (§1 rule 4). The submit pass asks
  `resume_of_application` again, so a default that changed after preparation (another resume made the default, or the
  same one finalised again) gives another sha256, the re-check fails closed, nothing is sent, and the row goes back to
  `waiting_for_you` ("form changed") with the new resume named and a new copy frozen for the person to review.
- **Kept.** The copy holds the person's finalised resume and is not purged with the filled values (`purge_filled`): it is
  the record of what went out. Deleting the application deletes it.

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
4. **Where the worker runs.** Decided 2026-11-08: inside the app on FastAPI Cloud, started by the lifespan.
   - It needs no local browser, since Browser Use's cloud browsers are driven over CDP.
   - It leases `applications` rows in Postgres, so replicas never double-run a row.
   - It starts only when `AUTO_APPLY_ENABLED` is on and `BROWSER_USE_API_KEY` is set (the secret is set as of
     2026-11-08).

   **Open, to figure out later:** an idle app may be paused by FastAPI Cloud, and then queued fills wait for the next
   request. The user plans to move to FastAPI Cloud's $20 plan, which should keep the app running. Until then, check
   whether the current plan pauses idle apps; if it does, add a scheduled ping or accept the wait. Revisit if the fill
   queue grows beyond what one replica's worker handles.
5. **Which email goes on applications, and how replies are tracked.** Decided 2026-11-07: the person's own email on
   every form, confirmation from the ATS's success page, and replies by paste or forward, then by a forwarding address
   with a ready filter, then by mailbox access in v2 (§3.2).
