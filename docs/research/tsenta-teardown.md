# Tsenta teardown: what to copy, skip and do better for A1 (auto-apply)

Walked through on 2026-11-06 in a real, signed-in Tsenta account on the free plan (9 applications left, 21 past
applications). Sections 1 to 5 come from read-only pages. Sections 6, 8 and 9 are two live applications, each approved
by the user. The first ended unsupported and refunded, with nothing sent. The second was reviewed (three wrong answers
fixed) and submitted. Personal details from the account are deliberately left out of this note. Spec it feeds: `docs/auto-apply.md`.

## 1. The journey, end to end

1. **Landing (tsenta.com).** The pitch is volume and speed: "be the first to apply to every job that fits you. Hands
   off", 50,000+ career pages on 30+ ATSs, "hundreds of applications a week", first 100 applicants. It ships on the web,
   iMessage, WhatsApp, Claude, Codex and a browser extension.
2. **Sign up, then upload a resume.** The resume is parsed into a profile with a completeness meter ("88% complete").
3. **Profile and application defaults** (section 2 lists every question). The resume becomes a "Tsenta version" that
   is re-tailored per job, with templates, fonts, size and "fit to one page".
4. **Feed (Dashboard, Browse jobs).** Cards show a % match ring, place, age, number of openings and up to ~40 skill
   tags, with Pass and Apply. There is "Apply to all 5" and, on phones, a swipe deck ("Swipe right to apply, left to
   skip"). About 15 filters, an exclude box, "Show jobs without Auto Apply" and "Tune my feed with AI". Another way in
   is "Add your own link" (paste a URL plus an optional job description).
5. **Apply.** With the account's defaults (auto-approve on, review before submit off) a click goes straight to
   submission. With review on, Tsenta pauses before the final submit and shows everything it filled (their 2026-07-01
   changelog). Coverage claimed: Greenhouse, Workday, Ashby, Lever, Oracle, SmartRecruiters.
6. **Applications table.** Tabs: All, Submitted, In flight, Needs you, Failed, Skipped. Each row shows the resume used
   (Ready, Default), status with a reason, and "Reapply". Skip reasons seen: "Profile Incomplete", "Job Expired",
   "Screening Questions Required". There is also "Approve all".
7. **Application detail: the minimal UI** (section 3). It shows a structured form view, not a browser recording.
8. **Tracker.**
   - *Inbox:* emails to a Tsenta-provided address, auto-labelled Verification, Rejection, Interview, Recruiter,
     Assessment, Reminder, Offer and Applied. Each email is linked to its application.
   - *Pipeline:* a Sankey of outcomes over a kanban (Applied, Ghosted, Interviewing, Rejected, Offer), CSV
     import/export, "Interview feedback", "Add interview transcript" and "Add application" for ones made by hand.
9. **Networking.** A company card (size, place, links, "You applied · <role>") and "Build outreach bundle": top 3
   contacts, verified email status, email drafts and LinkedIn notes. Limited to 10 lookups a day.
10. **Auto Apply** (paid "Power" plan).
    - Applications are grouped into "lanes": a filter set, with up to 5.
    - It runs at set times, up to 4 a day. All lanes share one daily cap (10 a day by default).
    - Setup is chat-first ("What would it apply to for me?").
11. **Research.** Articles from their own data ("100k applications every 24 hours", "the biggest ATS myths").
12. **Settings.**
    - Apply settings: resume optimisation Off, Honest or Aggressive; auto-approve; review before submit.
    - "What Tsenta remembers": memory saved from chats.
    - A public portfolio and plans and billing.
    - Referrals: both people get 200 free applications.
    - Email integrations: Gmail or Outlook, used to capture OTPs and verification links, and daily recommendations at
      9 AM.
    - AI assistants, and job-board connections (Indeed, Wellfound).
    - **"Workday password":** one stored password Tsenta uses to create accounts on Workday, iCIMS and Oracle when
      applying.

An assistant chat sits on every screen ("Every change is previewed, approved by you, and undoable"). A credits chip
("9 left") is always in the header.

## 2. The questions they ask (the "Application defaults")

| Group | Questions |
|---|---|
| Contact | First and last name, email, phone and phone type, address, city, postal code, state |
| Work authorisation (per country) | Country, basis (citizen and so on), authorised to work, needs sponsorship, citizenship |
| Work preferences | Notice period (days), in-person OK, can relocate, can start immediately, has transport, needs accommodations |
| Background | Prior employee of this company, government clearance, government ties |
| Voluntary self-ID | Gender, ethnicity, veteran, disability |
| Profile | Summary, education, experience, projects, skills, certifications, custom sections |

Per-form questions seen on a submitted application include "Have you ever been employed by <company> or its
affiliates?", "Are you related to any current employees?", "Are you willing to relocate?", "Are you eligible to work in
the United States?", "Will you now or in the future require sponsorship?" and the EEO set.

## 3. The minimal application view (their best idea)

The application page never shows a browser. It shows the application as data:
- A header with role, company, "Prev 2 of 21 Next" (arrow keys work), "View job posting" and a status pill.
- Tabs: **Form, Resume, Cover, Job**.
  - *Form* lists the fields grouped as the ATS groups them, with field counts: Personal Information (9), Work
    Experience (2), Application Questions (7), Voluntary Disclosures (4).
  - Each field shows its label, then the value.
  - Choice fields show every option as "Selected" or "Not selected", or "Selected from 35 options" for long lists.
  - File fields show "Resume attached" and "Cover letter attached".
  - *Resume* and *Cover* show the tailored documents, which can be downloaded. *Job* shows the posting.
- A left rail with "Reach out at <company>" (networking), a referral nudge, and the **emails for this application**
  with labels ("Received", "Reminder").

This is the pattern for our Review screen: the person checks data, not a video of a robot.

## 4. What they get wrong (and where we win)

1. **A wrong answer sent to an employer.**
   - Their record: the saved defaults hold work authorisation for **India** only.
   - What happened: a form asked "Are you eligible to work in the United States?", and the submitted answer was
     **Yes**.
   - Why: answers are carried across countries, and auto-approve means nobody saw it.
2. **Sensitive answers filled silently.** Gender, ethnicity, disability and veteran status are filled from defaults on
   every form.
3. **A % match on every card,** with no reasons. Skill tags are dumped (40+ on one card) rather than explained.
4. **No memory of outcomes in the feed.** It recommends a role at an employer whose rejection of a similar role is
   already in the tracker, with no note.
5. **The applicant email is Tsenta's, not the person's.** The address on submitted forms is a Tsenta-hosted mailbox.
   - It powers their tracker and OTP capture.
   - But employers reply to an address the person does not own, so the person is locked in.
6. **They log in for the person.** One stored password creates accounts on Workday, iCIMS and Oracle.
7. **Volume is the product.** "Apply to all 5", Approve all, 4 runs a day, "Aggressive" resume rewriting. The
   landing page's diff even shows invented-looking metrics added to a resume line.
8. **Small bugs.** "Notice period: undefined days" on the profile; an onboarding tour that blocks the feed.

## 5. Decisions for our design (A1)

**Copy**
- **The data-first review screen** (section 3), with field groups in the ATS's order, counts per group, and
  Prev/Next across applications with arrow keys.
  - *Better:* every value carries its **source** (details, resume line, cited fact, your earlier answer, the model)
    and custom answers show their cited facts.
  - *Better:* the resume line reads "attached only when you approve".
- **Status tabs with reasons and a one-tap fix.** Needs you, Sent, Check this one, Failed (refunded), Skipped (with
  reason). A skip like "Screening questions required" opens exactly those questions; answering them requeues it.
- **Emails attached to their application,** auto-labelled, in the application's side rail.
- **Pipeline view.** Columns for Sent, Interviewing, Rejected and Offer, plus a code-computed "No reply in 3 weeks"
  column. We don't use the word "ghosted" (`docs/research/job-discovery.md` §3).
- **Application defaults grouped as they group them** (section 2), asked once.
- **A credits chip in the header,** in rupees per delivered application.

**Do better**
- **Country-scoped answers.** Work authorisation and sponsorship are stored per country. A question about a country
  with no saved answer goes to Needs you; it is never inferred. This is a hard rule in the answerer.
- **Voluntary self-ID defaults to "Decline to answer"** unless the person has set an answer and switched on "use on
  forms". Shown in its own collapsed group on Review.
- **Reasons, not percentages.** The match verdict word, two cited reasons and the biggest gap. The hard-requirement
  gates run before an application can be queued.
- **Use outcomes.** A role at an employer that rejected the person in the last 90 days carries a plain note, and
  repeat applications to the same requisition are blocked.
- **The person's own email on every form.** For tracking, give each person a forwarding address (they set one Gmail
  or Outlook filter) or let them paste emails. No proxy mailbox on applications. Decision 5 in `docs/auto-apply.md`.
- **No stored ATS passwords.** Login-first ATSs (Workday, iCIMS, Oracle) go to the hand-off lane.

**Make leaner (instead of their bigger features)**
- **Instead of "Auto Apply lanes and run times": a daily "Ready for you" stack.** Tayyari prepares fills for new
  roles that pass the gates. The person approves them in a stack (swipe or keyboard, one digest each), so the work is
  automatic but the decision is theirs.
- **Instead of the outreach bundle: our existing referral note,** with no contact scraping. Finding emails from
  LinkedIn would break `CLAUDE.md` rule 4.
- **Skip for now:** the assistant on every screen, chat-based setup, resume templates and fonts, job-board account
  connections (Indeed, Wellfound), memory settings and the research blog.

## 6. A live apply, step by step (2026-11-06)

The user approved one application (AI Engineer, Bengaluru, listed by ClanX). Settings were changed first: "Review
before submit" on, auto-approve off. Tsenta's own note says review "works on Ashby, Greenhouse and 58 more ATSes.
Other ATSes apply normally for now".

1. **Apply on the card.** The credit chip drops at once (9 to 8), and the page jumps to
   `/applications/<id>?firstRun=1`, showing "Loading application…".
2. **About 10 s later: "Ready to review".** This is a **resume review, not a form review**.
   - *Left pane:* a chat with Tsenta's assistant, a "Fair match, 3 of 16 keywords covered" box, and keyword chips, both
     covered and missing ("+ Vision processing", "+ Audio processing" and others).
   - *Changes:* "What I changed: rewrote 1 bullet to lead with impact". The actual edit was punctuation only.
   - *Suggestion buttons:* "Weave in the missing keywords I genuinely have", "Make the summary punchier", "Fit it to
     one page".
   - *Right pane:* the tailored PDF with template, font and size controls and "1 edits · tailored". Buttons: "Use
     original resume" and **"Approve & submit"**.
   - The assistant began, unasked, to "weave the missing keywords into the bullets and skills" and named a different
     employer (the company behind the ClanX listing). It had not changed the resume by the time of approval.
3. **The posting was already gone.** The job's own link returned 404 ("We couldn't find this job"). The feed still
   showed it as 3 days old and took the credit.
4. **"Approve & submit" led to "Not Supported".**
   - The message: "This job board isn't supported for auto-apply yet. Your credit has been refunded."
   - The actions: Apply manually, Download Resume, Skip and "I've Applied".
   - The headline: "Couldn't submit. Skip this one, or finish it manually and mark it applied."
   - Nothing was sent.

**What this teaches A1:**
- **Check before charging and before showing Apply.** Confirm the posting is live and that we can fill its ATS before
  a card offers "Apply for me". Otherwise the card offers "Open on the employer's site". Tsenta charges first and
  refunds after.
- **Copy the honest failure screen:** a refund stated plainly, with the manual route, the resume download, and "I've
  applied" so tracking still works.
- **Review must include the form, not only the resume.** Tsenta's review of this application covered the resume
  alone. Our Review shows every form value before anything is approved.
- **Missing keywords must never be presented as additions.** Our match states them as gaps (`project-soul.md`: no
  skill from a posting onto a resume). Tsenta's "weave in the missing keywords" is the opposite.

Still not seen: Tsenta's paused *form* review on a supported ATS (Greenhouse or Ashby).

## 7. Which ATSs an Indian feed actually uses (2026-11-06)

The account's top 20 India matches (AI roles, Bengaluru, auto-apply-able only), by the employer's posting link:

| ATS | Count |
|---|---|
| Workday (`*.myworkdayjobs.com`) | 13 |
| SmartRecruiters | 3 (two cards share one posting URL) |
| SAP SuccessFactors | 2 |
| Oracle Cloud HCM | 1 |
| Eightfold | 1 |
| **Greenhouse, Lever, Ashby** | **0** |

**Implications for A1:**
- **Launching on Greenhouse, Lever and Ashby covers startups and global tech.** It covers almost none of the large
  Indian employers in this sample. Most of those are on Workday, which needs an account per employer.
- **The A1.6 live test must sample Indian postings,** to measure the real coverage. Workday, through the hand-off lane
  (the person signs in), is the most valuable v2 target. SmartRecruiters needs no login and is the cheapest next ATS.
- **Seniority is not gated.** Roles asking 14+ and 15+ years scored 76 to 77% for a person with about 2 years. Our
  hard-requirement gates must cover years of experience.
- **The feed has duplicate cards.** Two different titles pointed to the same posting URL, so de-duplicate by posting
  URL.

## 8. A live apply on Workday, with the form review (2026-11-06)

The user approved one application: Hitachi Energy, R&D AI Prompt & Agent Specialist, Bengaluru, on Workday. The
posting was checked live first.

1. **Apply on the card.** The credit was taken (9 to 8). The card leaves the feed and the row appears in
   Applications as "Resume: Review · Status: Needs you", with an inline **Approve** button.
2. **Stage 1, the resume.** Same layout as section 6: "Strong match, 13 of 17 keywords covered", 2 bullets
   "rewritten", 9 skills added. The bullets changed only in punctuation; the skills were padded with posting
   keywords ("AI copilots", "Knowledge management tools", "Document repositories"). The assistant left out "Power
   electronics", saying the person does not have it. The button is now **"Approve & fill form"** ("You'll check the
   filled form before it's sent").
3. **Filling, streamed live (about 2.5 minutes).** "Preparing your application · Filling My Experience… 11 fields
   filled". Fields appear as they are filled; the browser is never shown. The status pill says "Submitting" during the
   fill, which is misleading. **The resume shows "attached" on the employer's Workday during the fill**, so it reaches
   the employer before the person approves (the early-upload problem `docs/auto-apply.md` rule 3 avoids).
4. **Stage 2, the form review.**
   - "Review before submit · Waiting for your approval". The left rail says "Check what we filled in for Hitachi, and
     edit anything before we submit. Locked fields can't be changed here."
   - Every field is editable inline, grouped as Workday groups them, with counts: Personal Information 14, Work
     Experience, Education, Application Questions 8, Skills 10 (locked: "Edit in your profile"), Voluntary Disclosures
     1 (Gender, locked: "options not listed").
   - The buttons are **Submit application** and **Cancel application**.
5. **What the review caught.** Three answers were wrong and were fixed before submitting:
   - "How did you hear about this role?" was filled as **LinkedIn**. That is a guess; the person never saw the role
     on LinkedIn. It was changed to the employer's own career site.
   - The free-text "relevant experience" answer was written by their model. It **turned a team contribution into "I
     designed"** a multi-agent system, merged two different systems into one, and claimed proficiency in "several AI
     copilots" with no source. It was rewritten from the resume's own lines.
   - Long free-text answers sit in **a one-line input**, so the person can't read what they approve without
     scrolling inside the box.
   - Correct answers: not a Hitachi employee, no visa sponsorship, authorised to work in the country, able to perform
     the essential functions, Bachelor's degree, salary "Negotiable".
6. **Submit.** "Submitting your application · Sending your reviewed application to Hitachi. This usually takes under a
   minute. You can leave this page, we'll finish in the background." Section 9 records the outcome.

**What this teaches A1:**
- **The two-stage review (resume, then form) is right,** and the streamed fill is a good waiting state. Copy both, with
  honest status words ("Filling", not "Submitting").
- **Every model-written answer needs its sources on screen.** Tsenta's free text overclaimed in two places a reviewer
  would only catch by knowing the resume by heart. Ours shows the cited fact next to each sentence and drops what no
  fact supports (`CLAUDE.md` rule 1).
- **"How did you hear about us" is a fact, not a guess.** We know the source (Tayyari, or the employer's site), so we
  answer from it, or choose "Other" when no option fits.
- **Long answers get a full-height text area on Review,** whatever the ATS uses.
- **Don't let the fill attach the resume early** on ATSs that upload at once (Workday, Greenhouse, Ashby). Attach it in
  the approved submit pass.
- **Skills that come from the posting** (padding) never go into the person's profile or a form.

## 9. After submit

- **About 2.5 minutes after the click, the status was "Submitted"** (the page said "usually under a minute"). The
  corrected answers were the ones sent.
- **The submitted record has more than the review showed.** Personal Information lists 15 fields against 14 at
  review. Shown only after submit:
  - the email, which is Tsenta's hosted inbox, not the person's;
  - country code, phone number, country and state;
  - "Have you worked for this company before?: No";
  - **"Signature (full name)"**, typed in the person's name.

  The header shows "Applied using <Tsenta inbox>" and "Account password: View". A Workday candidate account was
  created for the person with Tsenta's mailbox and the stored password.
- **What this teaches A1:**
  - **Review shows every value that will be sent, locked fields included.** A field the person cannot edit still
    appears, read-only, with its source.
  - **A signature or attestation field is never filled for the person.** It is a Needs you item that they type
    themselves.
  - **Account creation on an ATS is the person's act.** Workday stays in the hand-off lane: the person signs in or
    creates the account, then we fill.

The confirmation email (if Hitachi sends one) lands in the Tsenta inbox, not the person's own mailbox.
