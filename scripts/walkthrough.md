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
    no `canvas.light` in the DOM. `?light=reduce` (or the OS setting) and Atmosphere Off: none.
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
    when it is done, with its evidence confidence. Choose "Close the gaps", answer a question and choose "Save answers
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

Acceptance: all `npm run check` scripts green, the walkthrough clean at both sizes, and
every chat seed question and caption byte-identical to today (same `prompts.ts`).
