# Browser walkthrough — calm visual layer

Run in the in-app browser at 1440×900 and 390×844 using the two launch configs in
`.claude/launch.json` (`tayyari-api` on :8787, `tayyari-web` on :5173). Use a built
`dist/` plus the API server (`npm run demo`) for the paint measurements in step 10.

1. **Onboarding:** a `.doc` is rejected; paste two links; Enter adds a link; the notes
   toggle opens and counts; the disabled-submit hint shakes (transform only); "Try a
   sample profile" starts an analysis.
2. **Analysis:** the rings advance; `data-state` reaches `done`; the graph auto-opens.
   Stop the API: the error card appears with Copy and the sample button.
3. **Now:** the radar reveals once (900 ms draw). Domain vertex → crossfade, breadcrumb
   and the level-1 header. Skill vertex → popover → CTA → chat seeded with the exact
   question and the "from the graph" caption. Back to graph lands on Now. Hovering a
   skill vertex tints the source chips that evidence it. Source chip → popover → CTA.
   Centre numeral → the "you" popover. Escape twice returns to all domains with focus
   on the domain.
4. **Keyboard:** Tab into the radar; arrows cycle the vertices; Enter opens; Escape
   restores focus. "f" flips the card; `aria-pressed` toggles on the flip control.
5. **Next:** the rail switches roles (radar crossfade); strength and gap popovers show
   the `biggestGap` copy → CTA → chat; Escape refocuses the vertex; the role CTA
   opens chat; flipping back closes the popover.
6. **Chat:** send; Stop mid-stream; Retry; Jump to latest; New question; drafts survive
   thread switches.
7. **Hues:** load `?h=0`, `?h=60`, `?h=120`, `?h=180`, `?h=240`, `?h=300` and
   screenshot every view at each. `?h=` pins the hue and the control reads "Still".
8. **Atmosphere:** choose Still, then Off (neutral greys), then Drift; each persists
   across reload (`localStorage` `tayyari.atmo`). With the OS reduced-motion setting on,
   the control reads "Still · system".
8a. **Dark:** in the Atmosphere menu's second group ("Appearance"), choose Dark: the field,
    every surface, the ink and the hairlines flip in place, the primary button becomes a light
    fill with dark text, and `<html data-scheme="dark">` is set. Reload: it is dark from the
    first paint (no light flash; `localStorage` `tayyari.scheme`). Choose System: the attribute
    goes away and the page follows the device (in the pane, Viewport › dark). Choose Light under
    a dark device: light, with `data-scheme="light"`. Load `?scheme=dark&h=120` and
    `?scheme=dark&h=300` and screenshot Home, Now and the posting match at both hues: ink and
    muted ink read on every surface and lobe, the radar's fills read against the dark field, Off
    gives neutral dark greys. On Home in dark, no `canvas.light` is in the DOM (the cursor light
    is not mounted). A published portfolio (`/p/<id>`, and `/dev/portfolios`) stays light in
    either scheme, the Old Newspaper skin too. Arrow keys move through all six items of the menu.
9. **Mobile (390×844):** every popover kind opens as a bottom sheet; `onPickSkill`
   works from the sheet; the chat rail toggles.
10. **Idle paint:** on Now, Drift mode, paste the snippet below into the console and
    leave the tab visible and untouched for 30 s. Expect `writes` 4 ± 1 (one per 7 s
    tick — a tick may carry up to five property records, counted once), `rafs` 0 and
    `longTasks` 0.

```js
(() => {
  const root = document.documentElement;
  let writes = 0;
  let rafs = 0;
  let longTasks = 0;

  // One observer callback per synchronous batch of style writes = one tick.
  new MutationObserver(() => {
    writes++;
  }).observe(root, { attributes: true, attributeFilter: ['style'] });

  const raf = window.requestAnimationFrame;
  window.requestAnimationFrame = (cb) => {
    rafs++;
    return raf.call(window, cb);
  };

  new PerformanceObserver((list) => {
    longTasks += list.getEntries().length;
  }).observe({ type: 'longtask' });

  setTimeout(() => {
    window.requestAnimationFrame = raf;
    console.log({ writes, rafs, longTasks });
  }, 30_000);
})();
```

11. **Home light (fine pointer, full motion):** move the mouse — the light wakes where the
    pointer is (no sweep across the screen) and stays locked to it at any speed; stop — it keeps
    morphing, dims after 10 s and is gone by about 51 s; leave the window — it fades in 0.6 s and
    wakes again where the pointer re-enters; every button and field behaves exactly as before and
    the form panel stays opaque. With `?light=stats`, run `__light.sweep(10, 1200)` in the console:
    expect `p95` at or under one frame period, `behind` at most 40 px, `gpu` under 2 ms and
    `min L` at or above 0.885; once asleep, the step-10 snippet reports `rafs 0`. Mobile preset:
    no `canvas.light` in the DOM. `?light=reduce` (or the OS setting), Atmosphere Off and the dark
    scheme: none.
12. **Accounts:** after an analysis of your own resume (not the sample), and only when the server has a sign-in
    method set up, the "Save this to your account" card appears under the graph header once the radar has drawn; "Not now" hides it for this profile on this device.
    Choose "Save to my account" and, in local development with no Resend key, enter an email and choose "Email me a
    link". The sheet says "Check your inbox" and, because the server is local, shows the link under "open it" (the
    `devLink` of `POST /api/auth/magic`). Follow it: you land on `/` signed in, the graph is claimed by the account and
    the card is gone. The account control (the initials in the header) shows your name, your email, "Your profiles"
    (the graph is listed there) and the balance line, for example "1 free this month". "Sign out" clears a graph the
    account owns from this device; an unsaved graph stays. A link opened a second time says it has expired or was
    already used.
13. **Posting match:** needs a profile the account owns: the one from step 12 will do, since an analysis run while
    signed in is claimed too. Open "Match a posting" (the card beside the graph, or "Match" in the header), paste the
    text of `backend/tests/data/match/meera.posting.txt` and choose "Match this posting". The stream reads the posting,
    fills the requirement rows batch by batch, then the gates, the radar and the questions; the band appears in words
    when it is done, with its evidence confidence. The radar has one axis per area of the person's graph and no "Other"
    axis: "you" is the darker fill, "role expects" a lighter fill under a dashed line, and the asks that fit none of the
    areas (here the language and the MBA) are named under the chart. Selecting an area says how many asks make it up. Choose "Close the gaps", answer a question and choose "Save answers
    and re-check": the page says the answer becomes a fact marked "From you", and the rows it touches are checked again.
    Choose "Build my resume for this role". The preview lists each line that needs a decision (Keep, Soften, Drop, a
    figure for a number, or a term to confirm). While one is open, "Download PDF" and "Download DOCX" are disabled and
    the page says downloads open once the decisions are settled. Settle every line, then download both files. Open
    "Cover note": each paragraph sits beside the posting line it answers and the facts behind it, and a paragraph that
    rests on your answer carries "From you". "Download PDF" gives the cover note. Last, start a second match (each one
    calls the model): once the free posting of the month is used, the page shows "You've used your free posting this
    month." with "See plans" and "Not now", and the posting stays in the box. On the phone preset (390×844) the
    requirement table is cards and every popover opens as a bottom sheet.
14. **Pricing:** open `/pricing` from the header's "Pricing" link or the "See plans" link of the paywall, signed out:
    the plans read, and the plan buttons ("Buy three postings", "Get 30 days") open the sign-in sheet ("Sign in to
    buy"). A plan with no price on the server, or whose provider has no secrets set, shows "Coming soon" with no number
    and a disabled button. Choose "₹ India" or
    "$ Everywhere else": the prices change at once, and signed in the choice is saved, so a reload keeps it. A purchase
    needs the provider's test keys: Razorpay's window opens over the page, while Stripe takes you to its hosted page and
    back to `/pricing?checkout=success` or `cancelled`, where the page says the payment is confirmed in the background
    and shows the plan when the webhook arrives. In Razorpay's test mode, pay by UPI with the id `success@razorpay` (it
    succeeds) or `failure@razorpay` (it fails), or by card `4111 1111 1111 1111` with any future expiry and any CVV (it
    succeeds). After a successful test payment the notice reads "Payment received." with "Match a posting", and the
    balance chip says "3 postings left". A failed attempt shows "The payment did not go through." under the paying line
    while the window stays open for another try; close it then and the notice reads "The payment did not go through.
    Nothing was charged." Close the window without paying and it reads "Checkout closed. Nothing was charged." If the
    server refuses the hand-off (a signature that does not verify, or an order that is not this account's), the notice
    shows the server's sentence followed by "Keep this payment id if you need help" and the payment id.
15. **Discover, with AI** (a search costs about $0.11; 3 a day per address without an account). Choose "Discover" in
    the graph header. It works on the sample profile, with a banner saying these are the sample's roles.
    - **First visit.** It shows "Roles you would not find on your own", the city chip ("Bengaluru · from your resume ·
      Change"), "Include remote in India" (on) and "Search with AI". Nothing runs until it is pressed.
    - **The search.**
      - Press it. Within about 6 seconds the first roles, from Tayyari's own employers, appear beside a live trail.
      - The trail shows a plan line, a read line, then three search lines, each with its short queries, while skeleton
        cards fill in.
      - After about 20 seconds it reads "N roles you might not have found". The "How it narrowed it down" strip counts:
        open roles on the boards read, then in the city or remote, then your kind, then within reach, then read, then
        fit. "Show the searches" opens the trail.
      - Stop keeps what has arrived.
    - **Results.**
      - Filter chips: All, Strong fit, Good fit, A stretch, Saved.
      - Sections: "Strong fits", "Good fits", "Stretches worth a try".
    - **Each card.**
      - A fit word and a level word.
      - The title, employer and place.
      - The pips: one square per checked ask (filled met, half partly, red outline not on your resume) and a count
        line. Never a number score.
      - One "Asks … / You …" pair whose two quotes are word for word from the posting and the profile.
      - How it was found and how fresh it is ("open over a year · worth asking if it's still hiring" for evergreen
        postings).
      - "Apply on {host} ↗", then Prepare, See how you fit, Save, Compare and Not for me.
    - **Focus.** The title opens the focus panel (a bottom sheet under 760 px) with every pair, the gaps, the level quote
      and Share.
    - **Coming back.** Reload: the last results show at once, with "Searched today at …", and no new search runs.
    - **Not for me.** Asks "Why not this one?" with reasons. Too senior also skips step-ups next time; Company skips
      that employer. The card becomes "Hidden · reason · Undo", and the next search leaves it out.
    - **Compare.** Pick two or three roles and the tray reads "Compare N roles · names". Compare opens them side by side.
    - **Phone.** At 390×844 the roles are a deck: "1 of N", a progress strip, Not for me / Details / Save, a swipe hint,
      and a full-width Apply. Swipe right saves; swipe left asks Not for me.
16. **One role, end to end.**
    - **Prepare** opens "Frontend Engineer at {employer}". It has these sections:
      - "Lead with these": They ask / You show, with sources.
      - "Close or explain the gap": the nearest evidence and a step marked "Suggestion".
      - "Questions they are likely to ask": each tied to a quoted ask, with a story of cited lines plus dashed boxes
        for what only you know. A Result never repeats a line already shown.
      - "Before you apply": a checklist with See how you fit, the tailored resume, the cover note, Apply and Mark as
        applied.
      - "Practice the interview": Coming next.

      The first open takes a few seconds; after that it is kept.
    - **Share** opens "Share this role" with "Ask for a referral".
      - A To field fills in `[name]`; under it, "Left empty, the note says “Hi there”." Copy note and link (and Share…)
        with the field empty gives "Hi there," while the box still shows `[name]`.
      - The note is one short paragraph, a storyline in three sentences: who you are (your role, and one line of your
        resume), why you fit them (one of the asks the search matched to your evidence, quoted, and what you did that met
        it), and why they draw you (the posting's own words about their mission or the work). Then "Would you be open to
        referring me?", a blank line and the link. About 60 to 90 words.
      - "What each sentence draws on": 1 · Who you are · your resume, 2 · Why you fit · the match: the posting and your
        resume, 3 · Why them · the posting.
      - An employer found on the web is named without its board's number ("Appen", not "Appen 2").
      - Copy note and link, Share… (where the device has it) and Rewrite.
      - "Just the link" is the plain option.
    - **See how you fit** opens the match screen on the posting the search read, with "← Discover", for your own
      analysed resume. On the sample it says a resume is needed first.
    - **Save** adds the role to "Your applications · N". There, five stages are shown as columns (tabs on a phone),
      each with a stage select, the date applied (set when the role first moves to Applied), a note, Prepare, Apply,
      Share and Remove. The page says "Kept in this browser".
    - **Without a server,** `/dev/discover?state=…` holds every state (start, searching, results, stale, focus, deck,
      sheet, not-for-me, nothing fitted, limit, paused, error, prepare, share, compare, applications).

17. **Practice the interview** (each start makes one Haiku call, each answer one small one, the debrief one more; 3
    interviews a day per address while it is free).
    - **Entry.** "Practice" in the graph header and in Discover's header opens `/practice`. Prepare's "Practise for this
      role" and the "Practise" action on a role in Your applications open the plan for that role.
    - **Practice.** Coming up (roles at Interviewing or Applied), the four kinds with what each is built from, Your
      practice with pips and counts, "Free while we test it · 3 a day". On the sample profile a banner says it is the
      sample's interview, and Your practice stays empty (the sample is shared).
    - **Setup.** The plan lists each question's kind and source quote with "Skip this one"; Spend more time on, Short or
      Full with real counts, Speak or Type, "Test my microphone" (the meter moves only with your voice), the
      interviewer's voice (on, slower, off), and the privacy line. With Sarvam set up on the server: "Your voice goes to
      Sarvam AI, an Indian speech service, to be written down. Tayyari keeps only the text…" and "Speech by Sarvam AI,
      made in India for Indian English and Hinglish." Without it (or once the day's Sarvam budget is used): the browser
      line and "In Chrome, speech-to-text runs on Google's speech service."
    - **A role from your graph.** Prepare's "Practice the interview for this role", or
      `/practice?screen=setup&kind=role&graphRole=senior-frontend-engineer` on the sample: "Senior Frontend Engineer",
      "From your skill graph · no posting", "Your biggest gaps come first." Gap rows cite "The role" ("Not shown yet:
      “Automated testing”", or "Below the role's level: …") with the nearest evidence line; claims quote a line of their own
      skill; the side summary counts requirements over pips, never a percentage. The first question is about the biggest
      gap; "Why this question" says "The role needs this, and your graph shows less of it than the role expects." The
      debrief labels it "The role needs “…” · Your skill graph", has no employer summary, and "Practise again" reopens
      the same role. A role id no longer on the graph: "That role is not on your skill graph any more…" with "Back to
      Practice". `/dev/practice?state=setup-graph` draws the setup.
    - **The room.** The question in serif, "Why this question" closed by default; with Sarvam the interviewer reads it in
      an Indian English voice ("Say it again" replays it with no new request). Speaking with Sarvam: your answer is cut
      at each pause and "Your words appear after each pause", with "Writing down what you said…" while a piece is on its
      way; with the browser: the transcript settles as you speak. Either way it can be fixed after Pause; "Done
      answering" (Space). If Sarvam stops answering mid-interview the room says so once and the browser's speech (or
      typing) takes over. Type instead works at any time. "Say it again" repeats
      the question aloud; "I don't know" answers "That's fine. I'll note it and we'll move on." and moves on; Skip moves on.
      A vague answer gets a follow-up, a stuck one an easier question, a story missing its end "What happened in the
      end?". An off-topic answer hears "Let's come back to the question." once. No verdicts are shown until the end.
      Block the microphone: the room says so and offers typing. In Firefox: typing only, with the reason.
    - **Work sample.** The task and its material, your version and how you'd test it, the assistant with its switch;
      "I'm done, ask me about it", then three questions on your choices in the room.
    - **Debrief.** Pips and counts (never a score), "Work on this first" with "Ask this one again" (free), the role
      summary, then one card per question: the deciding words marked inside your answer with their time, Why, Say (dashed
      boxes for what only you know), Avoid (your own words), Fix with minutes, story parts, "Add as a fact · From you"
      when an answer said something your resume doesn't. A work sample adds its checks and what was seen of the
      assistant's use. The transcript panel links each time; "Delete now" removes the session. Reload keeps it.
    - **Progress.** Each evidence line asked about, with its best verdict and date; the facts you kept; your sessions.
    - **Phone (390×844).** The room becomes one column with a large "Tap when done" button, Type and Say again beside it;
      the debrief opens the first thing to work on and folds the other questions.
    - **Without a server,** `/dev/practice?state=…` holds every state (hub, setup, the room's steps and edges, work,
      debrief, ended early, progress, limit, paused, sample, thin, no template, phone).

18. **Prepare** (a new plan for a role makes two Haiku calls; a stored one opens at once, with no call).
    - **From the graph.** On Next, "How do I prepare for this role?" opens a thread whose head line reads
      "Design Engineer · closest of your 5 roles · ~2 months" (never a percentage; the "Started from" card shows the
      role's rank). The question "How do I prepare for the Design Engineer role?" is followed by four blocks, not
      prose; the fixed suggestion chips are gone and the composer stays.
    - **How it arrives.** "Where you stand" draws at once from the graph: one row per gap with a bar (today filled,
      the target a tick), "the biggest gap" under one of them, "Already strong: …" under the rows. The move and the
      plan are quiet outlines ("Choosing the one move…", "Laying out the plan…") with "Stop here" under them, and
      nothing shimmers. The headline and each gap's "why" fill in, then the move and the plan appear. "Stop here"
      mid-way keeps what arrived and offers "Carry on"; a failed call shows a calm line with "Try again".
    - **Where you stand.** A row opens with + / −: the why, a chip "From your resume" (tap: the evidence line, word
      for word, with its skill) and "Help me with just this", which opens a second thread for that one gap
      ("Automated testing · Senior Frontend Engineer · gap 48→75") with its own shorter plan.
    - **The one move.** "1 of 3", the serif title, "What you already have" chips (tap: the lines they rest on),
      "Works on" chips that are gap names, "about 36 h" (the phases' hours summed in code). "Show another move"
      cycles through the moves; "Plan it" / "Hide the plan" folds the plan.
    - **Your plan.** Hours a week 3 / 6 / 10 (`aria-pressed`): the summary and every phase's weeks change at once
      ("Weeks 1–2" at 6 h becomes "Weeks 1–4" at 3 h; the total is the hours over the hours a week, rounded up).
      Phase pills carry a progress line; the open phase's tasks are checkboxes; a tick strikes the task and the
      pill's line grows; "You'll have" names the deliverable; "Add a link" keeps a web link on the phase (a non-link
      is refused). Reload: the ticks, the hours and the links are still there (saved to the plan after a short
      pause; if another tab saved first, the plan reloads and saves on top).
    - **Next.** Four buttons open a panel in place. Calendar: pick a day and a time; "Download the calendar file"
      saves `tayyari-plan.ics` with one block a week for as long as the plan, each titled with its phase. Repo: your
      repos ranked with code-made reasons, one marked "start here", "Open on GitHub"; without a GitHub source it says
      to add one. Headline: "Now", "For this role", "Rests on" chips, Copy and "Another wording"; when no wording
      passed the check, the note says so. Practice: "Practice the interview for this role" opens Practice · This
      role for the plan's role.
    - **Read this as text** at the end is written from the blocks (the same numbers, the weeks at your hours).
      "Make a new plan" asks before it replaces the plan and its ticks.
    - **Follow-ups.** The composer still sends to the coach chat; its answers show under the blocks, and "Jump to
      latest" appears only once there is a chat answer below.
    - **Phone (390×844).** A Stand / Move / Plan / Next bar sticks to the top of the thread and marks the block on
      screen; the plan is a list where the open phase expands in place; "Plan it" and "Show another move" stay at
      the bottom of the move while it is on screen.
    - **Without a server,** `/dev/prepare?state=…` holds every state (full, stream, streaming, standing, error,
      refused, thin, requirement, no-github, no-headline, conflict, phone); add `&scheme=dark` for the dark scheme.

19. **Leafout sign-in** (real domains only: localhost cannot share the `.leafout.in` cookie). Sign in on
    https://leafout.in, then open https://app.leafout.in: you are signed in, with no click.
    - **Pricing round trip.** Signed out, open `/pricing` on app.leafout.in and press a plan's Buy button: you go to
      the site's sign-in. After signing in you are back on `/pricing` with the same plan and currency selected, and
      nothing is charged. Checkout starts only when you press Buy again.
    - **Sign out.** Sign out from the app's account menu. The app ends its session and the site's sign-out follows.
      Then open app.leafout.in and leafout.in: both show you signed out.
    - **Delete.** On the site's account page, choose "Delete my account". The app asks "Also delete your Leafout app
      data?" and deletes nothing until you confirm. Afterwards the site says "Your account was deleted", and neither
      app.leafout.in nor leafout.in signs you in.

20. **Re-drill and Fix steps** (a re-drill start makes one Haiku call like any interview; placing and ticking a step
    make none). Do step 17 first, so a debrief has a Shaky or Couldn't defend claim.
    - **Practice.** A fifth card, "Re-drill what wobbled", shows only when a claim wobbled. Progress has a "Re-drill"
      link on each Shaky or Couldn't defend row.
    - **Re-drill screen** (`/practice?screen=redrill`).
      - The claims in code's order, each with its last verdict, "Asked once · last on 16 Oct · Starts at the follow-up"
        (or "the opener, in new words"), and its Fix step: done, or not yet.
      - Short ticks the top 3, All up to 6, and any claim can be unticked; the summary says "3 claims · about 9 min".
      - "Start the re-drill" counts as one of the day's interviews.
      - With nothing wobbled it says so and offers "Back to Practice".
    - **Room.**
      - Above a claim that came back: "Back from 16 Oct · Shaky" and the line.
      - A Shaky claim opens at its follow-up ("Follow-up · where it wobbled"). A Couldn't defend one is asked again in
        new words, never the last question.
      - "I'm not sure" at a follow-up start still gets the easier question.
    - **Debrief.**
      - "1 of 2 moved up", and each claim shows its previous verdict next to today's.
      - A claim that could not be defended twice may show "Or say less" with Copy.
      - A Fix step offers "Where it goes": your plans by name (the session's role first) or "Keep it in Practice". Then
        "Add to my plan", "Added to your plan · …", Open the plan and Undo.
    - **Prepare.** The plan shows "This week · week N" with the step under "From your practice". Its minutes appear in
      the summary ("+ 15 min of practice fixes"), and ticking it shows "Re-drill it". Reload: the tick is still there.
      Back on the re-drill screen, that claim is now first.
    - **Without a server,** `/dev/practice?state=redrill`, `redrill-empty`, `redrill-loading` and `debrief-redrill`, and
      `/dev/prepare?state=practice` and `practice-phone`.

21. **The radar card** (no model call; sharing needs a graph your account owns, as in step 12).
    - **The sheet.** On the graph, "Share my skill shape" in the header ("Share" on narrower screens) opens it; under
      760 px it is a bottom sheet. On the sample it says sharing needs your own graph and offers "Analyse my resume";
      on a graph with no account it offers "Sign in". Neither shows a switch.
    - **Off.** The preview is drawn in the page's colours and says nothing is public until sharing is on.
    - **On.** Turn the switch on: a link appears, and the preview becomes the real image (`/r/<slug>.svg`), exactly what
      others see. The GitHub tab shows `[![Skill shape](…/r/<slug>.svg)](…/u/<slug>)`; Copy for GitHub copies it.
    - **Settings.** Tick up to three skills (a fourth is disabled); pick a role (the card gains a dashed outline and
      "Senior Frontend Engineer expects"); pick each theme. With "Match the page" the snippet becomes a `<picture>`
      whose dark source has `?theme=dark`. Reload: every choice is still there.
    - **New link.** "Make a new link" asks first; afterwards the old `/r/…svg` shows "This card isn't shared any more".
    - **Off again.** Turn sharing off: the link is gone and the old image shows the same neutral card.
    - **The page.** Open the LinkedIn tab's link (`/u/<slug>`; served by the API, so on port 8787 locally):
      "Strongest in …", the card (dark on a dark device), the areas table, "How this was made", "See your own skill
      shape" and "Report this card". View source: `og:image` is `/og/<slug>-<version>.png`, `twitter:card` is
      `summary_large_image`, `robots` is `noindex`, and there is no `<script>`. The PNG opens at 1200 × 627; changing
      its version in the address redirects to the current one.
    - **Nothing private.** No name, email, evidence line, rank or percentage appears on the card, the picture or the
      page.
    - **Without a server,** `/dev/card` and its states (`?state=github`, `github-match`, `linkedin`, `bars`, `many`,
      `phone`, `sample`, `anonymous`, `fail-put`, `image-failed` …; `?scheme=dark`).

22. **Auto-apply** (`/apply`; needs `AUTO_APPLY_ENABLED=true` and a sign-in. Without `BROWSER_USE_API_KEY` the worker
    does not start, so a started application stays "Waiting to fill"; nothing is ever sent without your approval).
    - **Signed out.** `/apply` and "Apply for me" ask you to sign in; no route but the status answers without one.
    - **First time.** Consent first (what is sent, that Browser Use and Anthropic process the form, screenshots kept 30
      days), then the 30-second first check. "Your details" lists every saved answer with where it came from.
    - **Start.** On a finished match of a Greenhouse, Lever or Ashby posting, "Apply for me" runs the start check
      (nothing charged), then shows the resume choice and the price. A Workday, closed or manual-only posting shows the
      reason and "Open on <host>'s site" instead. On Discover, a second "Apply for me" on the same listing reuses its
      match rather than starting a new one.
    - **Fill and review** (with the key set). The row goes Waiting to fill → Filling with its sections, then questions or
      the review: every value with its source, the screenshot, and Approve, which stays off while a required question is
      open. Answer a question with "Save" ticked: other waiting applications that ask it fill again too.
    - **Send.** Approve: Sending, then Sent with the confirmation screenshot, or "Check this one" when nothing clear came
      back. "It didn’t: refund me" there gives the credit back and the row moves to Didn't go; it is never resent.
    - **Track.** Applications' tabs (Waiting, Filling, Sent, Check this one, Didn't go); a sent row with no reply for
      3 weeks (counted from when it was sent) shows "Heard back?". Paste an employer's email to move a stage forward.
    - **Without a server,** `/dev/apply` and its screens (`?screen=applications`, `details`, `first-check`, `consent`,
      `start-offer`, `run-filling`, `run-questions`, `run-review`, `run-check`, `run-sent` …).

Acceptance: all `npm run check` scripts green, the walkthrough clean at both sizes, and
every chat seed question and caption byte-identical to today (same `prompts.ts`), except the
role's, which Prepare changed on purpose (no percentage).
