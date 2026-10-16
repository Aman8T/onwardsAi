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
15. **Discover:** works on the sample profile, with a banner saying these are the sample's roles; a full match from a
    card needs an analysed resume of your own (on the sample, the card says so). Choose "Discover" in the graph header. On a server whose boards have not been read yet, the first visit shows
    an empty list with "Reading employers' job boards for the first time. This takes about a minute." and asks again
    about 20 seconds later. Once there are roles, the city reads "from your resume" with "Change". Choose "Change" and
    pick a city and a locality from the lists: the How far stops show counts, the reach moves to 10 km, and each
    distance reads "km straight line". A role whose listing names no known locality shows no distance. Pick another
    How wide stop ("Your role", "+ next steps", "+ stretch") and the lanes ("Near you", "Remote, India-eligible",
    "Worth moving for") count again. Open a row: the card is a popover on a wide screen and a bottom sheet under 760 px
    (the 390×844 preset). Its "Apply on {host} ↗" opens the employer's own page in a new tab. Choose "See full match":
    the match screen opens with "← Discover", the role, the "Apply on {host} ↗" link and "opens in a new tab";
    "← Discover" returns to the same lens. Save a role with its bookmark button: it appears under "Your applications · N",
    where its status can be changed, and the page says "Kept in this browser". Open "Why this order": it lists what the
    ranking used and what it never uses, and "Show in posted order" switches to newest first and back.

Acceptance: all `npm run check` scripts green, the walkthrough clean at both sizes, and
every chat seed question and caption byte-identical to today (same `prompts.ts`).
