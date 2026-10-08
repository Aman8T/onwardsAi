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

Acceptance: all `npm run check` scripts green, the walkthrough clean at both sizes, and
every chat seed question and caption byte-identical to today (same `prompts.ts`).
