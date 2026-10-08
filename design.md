# The calm visual layer — a portable design system

This is the whole design idea behind Tayyari's interface, written so it can be lifted into another application. It covers the feeling we are after, the rules that produce it, every number that matters, the two places where the rules are deliberately bent, how variety is generated without risk, and the scripts that prove the result. The *why* behind each call, with sources, is in `docs/design-notes.md`; this document is the *what* and the *how*.

The system was built on 2026-10-06 → 10-09 in three rounds (a calm redesign, a resume → portfolio kit, a cursor light). Design canvases: the calm layer https://claude.ai/artifact/HLxXL14nAUeNHhLFNhozp9, the portfolio kit https://claude.ai/artifact/XH5UWJssQeNnwQiByk9fDa, the cursor light https://claude.ai/artifact/USZp9ELtqSY6sQu2gtYJCn.

---

## 1. The feeling

Opening the app should feel like exhaling. The background is a soft colour field that is always slightly different from the last time you looked, but never caught in the act of changing. Brian Eno's test for ambient music is the test for this interface: **as ignorable as it is interesting.** Someone who stays a few minutes should sense the space moving without being able to point at the moment it moved.

Everything else follows from that: no element asks for attention it has not earned, depth comes from tone rather than effects, the one serif line per screen is the only flourish, and the page produces zero frames when nothing is happening.

## 2. The rules of restraint

These are enforced by `scripts/restraint-check.ts`, not just agreed.

| Rule | Detail |
|---|---|
| No shadows, no blur, no glow | `box-shadow`, `text-shadow`, `drop-shadow()`, `backdrop-filter`, `blur()` are forbidden everywhere. |
| Hairlines only | No border thicker than 1 px. The 2 px focus ring (`outline`) is the one exception. |
| Colour lives in one file | Colour literals (`#hex`, `rgb()`, `hsl()`, `color-mix()`, literal `oklch()` in CSS) exist only in `src/theme/tokens.css`. Everything else uses tokens. |
| Motion is opacity and transform | `@keyframes` may not animate paint or layout (`filter`, `background-position`, `width`, `height`, `left`, `top`, `stroke-dashoffset`…). |
| Nothing loops | `infinite` animations are allowed only on an explicit allow-list (a typing indicator, a live dot). |
| Hue properties are never transitioned | A `transition` naming `--h`, `--h2`, `--h3`, `--k`, `--kw`, `--kb` or `all` is an error: it would animate the field on every step. |
| Depth from tone | Surfaces step 2–4 OKLCH lightness points; dividers are 1 px at ~8 % ink alpha; popovers are opaque surfaces with a hairline. |
| One serif line per screen | A display serif for a single headline; everything else is the sans. |
| Zero idle frames | Between background steps the page draws nothing: no `requestAnimationFrame` loops, no CSS animations running at rest. |
| Exceptions are written down | The two deliberate exceptions (§8, §9) are bounded, documented, and still pass every check above. |

## 3. Colour

### 3.1 One hue, fixed lightness and chroma

Every colour in the interface is `oklch(L C var(--h))` with **L and C fixed per role** and only the hue variable. Lightness fixed means WCAG contrast is fixed: dark ink at L .25 on a field at L .97 varies by a few tenths of a ratio point across the entire hue circle. Saturation, not hue, is what reads as excited, so chroma is low everywhere and the hue is free to travel.

Six custom properties drive it, registered with `@property` so they are numbers and never transitioned:

| Property | Meaning |
|---|---|
| `--h` | the base hue, integer degrees |
| `--h2`, `--h3` | the side-lobe hues, ≈ `h + 35` and `h − 45`, each breathing ±10° |
| `--k` | global chroma multiplier: 1, or 0 under "Off" (neutral greys) |
| `--kw` | warm-sector easing: chroma × 0.75 at hue 85, back to ×1 fifty degrees away (yellow and orange go muddy fastest) |
| `--kb` | lobe-chroma breath, 0.85…1 |

### 3.2 The roles

| Token | L | C (× `--k`) | Hue | Use |
|---|---|---|---|---|
| `--bg` | .97 | .012 × `--kw` | h | the field |
| `--lobe-1` | .90 | .044 × `--kw` × `--kb` | h2 | ambient lobe |
| `--lobe-2` | .90 | .044 × `--kw` × `--kb` | h3 | ambient lobe |
| `--lobe-3` | .92 | .034 × `--kw` × `--kb` | h | ambient lobe |
| `--surface` | .99 | .004 | h | cards, popovers |
| `--surface-2` | .955 | .016 | h | fields, chips, quiet fills |
| `--text` | .25 | .02 | h | ink |
| `--text-muted` | .45 | .03 | h | secondary ink (≥ 4.5:1 on every surface and lobe) |
| `--text-faint` | .55 | .03 | h | placeholders and disabled only (≥ 3:1; never carries data) |
| `--accent` | .50 | .08 | h | links, the serif's emphasised line |
| `--accent-ui` | .55 | .088 | h | focus ring, icons, strokes (≥ 3:1) |
| `--accent-strong` | .42 | .07 | h | primary button fill (surface text on it ≥ 4.5:1) |
| `--accent-soft` | .93 | .03 | h | highlighted fills |
| `--fill` | .55 | .088 / α .12 | h | chart fills |
| `--grid` | .25 | .02 / α .08 | h | chart rings, dividers |
| `--line` | .86 | .02 | h | hairlines |
| `--line-strong` | .60 | .04 | h | emphasised hairlines, dashed drop zones (≥ 3:1) |
| `--selection` | .88 | .055 | h | text selection |
| `--danger` | .48 | .12 | 25 (fixed) | errors |

### 3.3 Gamut

Browsers clip out-of-gamut OKLCH instead of mapping it, which bends lightness and hue. The sRGB limit at constant lightness is tightest around cyan (~200°) up to L .75 and around blue (~267°) above that: roughly **C ≤ 0.17·L up to L .75, then .100 at .80, .048 at .90, .014 at .97.** Keep every token at or below 0.93 × that limit, and let `palette-check` sweep all 360 hues to prove it.

### 3.4 Fallbacks, in order

1. A generated hex palette for one fixed hue, declared before the `@supports (color: oklch(0 0 0))` block, for engines without OKLCH (`npm run emit:fallback`).
2. `prefers-reduced-motion: reduce` or `(update: slow)` → the field holds Still.
3. `prefers-contrast: more` → the lobes are removed; ink goes to L .15 / .35, lines to .70, grid alpha .2.
4. `forced-colors: active` → the field is hidden; every token maps to a system colour.
5. "Off" → `--k: 0`: every chroma collapses to zero, lightness stays.

## 4. The living gradient

**Representation.** One fixed, full-viewport layer (`position: fixed; inset: 0; z-index: −1; contain: strict`) with `background-color: var(--bg)` and three radial lobes:

```
60% 55% at 15% 20%  → lobe-1 fading to its own colour at alpha 0 by 60 %
55% 50% at 85% 10%  → lobe-2, likewise
70% 45% at 50% 105% → lobe-3, likewise
```

Each lobe fades to *its own colour at alpha 0*, never to a neutral, so no hue swings in the falloff. The `<html>` behind it is a static neutral that never repaints; the view sits above at `z-index: 1`.

**Rate.** 1° every 7 s: a full circle in 42 minutes. At lobe chroma .044 one step is ≈ 0.0016 ΔE — far below the ≈ 0.02 JND and the ≈ 0.16 ΔE/s temporal detection threshold — so no single step can be seen. Over five minutes the lobes move about two JNDs: noticed only in memory.

**Why hue, never lightness or position.** Lightness change has the lowest detection threshold; movement in peripheral vision is noticed before fades. The field changes colour only. Motion *onset* captures attention while ongoing motion does not, so the hue is a pure function of wall-clock time: nothing ever starts.

**Never loops visibly.** Unrelated periods: the hue circle (42 min), the lobe offsets breathing (13 and 11 min), the lobe chroma breathing (17 min). Combined period ≈ 100 h.

**Cost.** Five custom properties written by one `setTimeout` aligned to the 7-second grid; no `requestAnimationFrame`; nothing transitioned. Hidden tabs stop the timer; on return the hue is recomputed from the clock. The maths lives in one dependency-free module (`src/theme/hue.js`):

```
h  = floor(h0 + 360 · (now mod 42 min) / 42 min) mod 360
h2 = h + 35 + 10·sin(2π·now / 13 min)
h3 = h − 45 − 10·sin(2π·now / 11 min)
kw = 1 − 0.25 · max(0, 1 − dist(h, 85) / 50)
kb = 0.925 + 0.075·sin(2π·now / 17 min)
```

`h0` is a random per-install offset kept in `localStorage`, so two tabs always agree and reloads never jump. The same module is inlined into `<head>` by a build plugin and run by a boot script before the first stylesheet, so the first paint already carries the right hue.

**Controls.** Drift / Still / Off (persisted). `?h=<deg>` pins the hue for previews and screenshots and forces Still. Reduced motion forces Still and the control reads "Still · system".

## 5. Type, space, shape, motion

**Faces.** Geist 400/500/600 for the interface, Geist Mono (tabular figures) for data numerals, Instrument Serif for one line per screen. Display tracking −0.02 em, small labels +0.01 em.

| Scale | px |
|---|---|
| `--fs-0` … `--fs-6` | 11.5, 12.5, 14, 15.5, 18, 22, 28 |
| `--fs-7` | clamp(36, 3.2vw + 24, 48) |
| `--fs-8` | clamp(44, 4.4vw + 28, 64) |

Line heights: tight 1.08 (display), snug 1.3, body 1.55. Measure 62ch.

**Space.** 4, 8, 12, 16, 24, 32, 48, 64, 96 (`--s1` … `--s9`). Treat empty space as positive space. Page wrap 1200 px; the reading column 560 px.

**Shape.** Radii 6 / 10 / 16. Hairline 1 px. A `.panel` is `--surface` + 1 px `--line` + 16 px radius; a `.chip` is `--surface-2`, 999 px radius, muted ink.

**Motion.** Durations 150 / 250 / 400 / 500 ms; one easing `cubic-bezier(0.2, 0.8, 0.2, 1)`; no bounce. Entrances are opacity plus an 8 px rise. Whole-screen crossfades 350 ms. One-time data reveals up to 900 ms. Reduced motion collapses every animation and transition to a single frame (a global `0.001ms !important` rule), and components skip their initial animation (`initial={reduced ? false : …}`).

**Primitives** (`src/styles/global.css`): `.btn`, `.btn-primary`, `.btn-quiet`, `.btn-sm`, `.field`, `.chip`, `.panel`, `.hairline`, `.kicker`, `.serif`, `.numeral`, `.sr-only`, `.wrap`, `.scroll`. Real `<button>`, `<a href>`, `<input>` + `<label>` everywhere; `aria-label` on icon-only buttons; touch targets ≥ 44 px.

## 6. Data display: radar charts

Radars are kept because the product is about a *shape of skills*, and tamed because the literature is blunt about their faults:

- 5–8 axes in a **fixed order per profile**, never sorted by value, so shapes become recognisable with use.
- At most two series: "you" as a 12 % fill with a 1.5 px stroke; a target as an unfilled hairline (benchmark thin, actual bold).
- Straight edges only; hairline rings at 25 / 50 / 75 / 100; labels outside the rim; numerals in tabular figures.
- Hover highlights one spoke. Vertices are real buttons with roving `tabindex` and arrow-key navigation; Enter opens, Escape restores focus.
- A visually hidden data table is always present and is the source of truth for parity tests.
- Animation tweens the *values along their spokes*, never the path string; axis-set changes crossfade.
- Drill-down: domains → one domain's skills; a "next roles" view overlays "you" against "role expects".

## 7. Variety from a closed kit (the portfolio)

When pages must look different for every person *and* must never break, variety comes from choosing and filling from a closed, pre-tested kit — never from free-form generation.

**Dimensions.**

| Dimension | Options |
|---|---|
| Gradient family (hue fed to `--h`) | Iris 275, Glacier 245, Lagoon 200, Sage 155, Dune 85, Clay 45, Rose 10, Orchid 320 |
| Field shape | Dawn, Horizon, Corner |
| Type pairing | Studio (Instrument Serif + Geist), Editorial (Newsreader), Technical (Geist 600 + Geist Mono), Warm (Bricolage Grotesque) |
| Corners | 2 / 10 / 18 px |
| Density | airy 112 px / compact 72 px section rhythm |
| Blocks × variants | hero (statement, split, centered, index) · proof (strip) · about (lede, prose, facts) · experience (timeline, ledger, then-now) · projects (feature+list, grid, list) · clips · achievements (numbers, list) · education (row, compact) · open-to (note, opt-in) · contact (sign-off, links) · footer |
| Compositions | Signature, Narrative, Builder, Foundations, Ledger, Pivot — each places the signature chart in or right after the hero |
| Chart variants | peaks (top − median ≥ 15), halo (balanced), ledger (chart + ranked rows; the phone fallback), stellar, atlas (≥ 24 items), spotlight (top − second ≥ 20), focus (< 4 axes or < 6 items: no chart, "areas of focus") |

576 looks before layout. A scoped wrapper (`.pf-scope`) sets its own `--h`/`--h2`/`--h3` and the token sheet recomputes every role colour inside it, so a page's palette is its family, not the app's ambient state.

**How a page is chosen.** Code, not a model: a deterministic composer scores valid options from features (career shape, chart shape, content counts), breaks ties with a seed derived from the person, and stores the top three distinct looks with rule-written reasons. Free, instant, reproducible, and it can never produce an invalid spec. Models do only what needs language: extracting facts (by line reference, copied verbatim by code) and writing copy into typed slots with character limits; every sentence is checked against the facts it cites (numbers with units, names, dates, seniority verbs, puffery) and falls back to the fact's own wording.

**Guardrails.** Every block declares a minimum; below it the block is omitted on the public page. Slot limits are enforced in code with `text-wrap: balance / pretty` and `overflow-wrap: anywhere`. Non-Latin names switch to fallback faces. Lists collapse after six items. Only `http`, `https` and `mailto` links render. Captions over data are templates over the values, never model text. Numbers are passed through untouched and a parity script asserts the rendered values equal the source in every variant and skin.

**An alternate skin costs no content.** "Old Newspaper" re-renders the same spec: paper L .955 (hue 85), ink L .22, a blackletter nameplate and a thick-thin double rule as the two deliberate print exceptions, columns via `column-width`, and the chart as a printed index with a halftone fill and an AREA / SCORE table. Toggling is a 400 ms drain to paper; `?view=press` makes it shareable.

## 8. Exception one: the cursor light

Home only. A soft field of colour follows the pointer, as the page's way of being quietly aware of the person. It bends "no spatial motion" and "no effects", and it is bounded so that it stays one exception.

**Why it is allowed.** The motion is the person's own: the field moves only because they moved, so there is no onset that is not theirs. While they are still, only the outline morphs (isoluminant colour change below the detection threshold), and after 45 s it fades and the loop stops. It is not mounted on touch or coarse pointers, under reduced motion or slow updates, under forced colours or more contrast, or with the Atmosphere Off.

**Luminous without brightness.** The field is at L .97, so "brighter" has nowhere to go; a bright core on a light page is the spotlight everyone has seen. Light through paper does the opposite: a paler, less saturated heart (L .965, C .016) with the colour in the body around it (L .89, C .048), fading with no edge. Lightness never drops below .89, which keeps `text-muted` ≥ 5:1 and large accent text ≥ 3:1 at every hue; the proof uses the same constants the shader is built from.

**Colour.** `hue = --h + 38·sin(2πt/97 s + travel/2200 px) + 18·sin(2πt/41 s + travel/900 px)`: bounded to ±56° of the field, phase advanced by time and by distance, body 28° further round, chroma eased through yellow–orange like `--kw`.

**Rendering.** One WebGL2 fragment shader on a canvas at half the viewport (≤ 960×600 texels), fixed between the field and the view, premultiplied alpha, plain source-over (`screen` / `plus-lighter` would wash the field to white). Per pixel: OKLab colour, chroma clipped to sRGB, ±1 LSB triangular dither. Measured on an integrated Intel GPU: ~0.5 ms GPU at half scale, 0.1 ms main thread per frame.

**Follow.** Heart with a 35 ms time constant (17 ms at speed): ~30 px behind at 1200 px/s, 4 % of the body's width, invisible on an edgeless shape. The organic trailing lives in the *shape*: direction smoothed over 120 ms, stretch up to 35 % along travel relaxing over 160 ms, the halo trailing part of that stretch. The frame interval is clamped before any division. A watchdog lowers the render scale when frames run long and switches the light off for the session on a third strike.

**Boundaries.** No ring, no bright core on a darker ground, no outline you can trace, no rhythm, no banding. The cards stay opaque; the light shows around them like a sheet of paper on a lit table.

## 9. Exception two: print conventions in the newspaper skin

Only in the Old Newspaper skin: a blackletter nameplate at 56 px or larger, and the thick-thin double rule (built as a fixed-height pseudo-element with a `var()` background so the border rule still holds). Everything else in that skin is hairline and shadowless.

## 10. Accessibility, as drawn

- Text ≥ 4.5:1 (≥ 3:1 at 24 px+ and for UI strokes) on every surface it can sit on, proved at all 360 hues. Caption greys and fills under white text fail first: darken both.
- Colours that must be told apart differ in lightness, not hue alone.
- A 2 px focus ring in `--accent-ui`, 2 px offset. Keyboard patterns from the ARIA practices: roving tabindex for charts, menus with arrow keys and Escape, bottom sheets on narrow screens.
- Reduced motion, slow updates, forced colours and more contrast each have a defined response (§3.4, §8).
- Charts carry a hidden data table; icon-only controls carry labels; live regions announce file and link changes.

## 11. The verification toolkit

| Script | Proves |
|---|---|
| `scripts/palette-check.ts` | every token inside sRGB at all 360 hues and lobe extremes; every contrast pair at its floor; the cursor light's body under every text role; `--fallback <hue>` emits the hex block |
| `scripts/restraint-check.ts` | the rules in §2, over every `.css/.ts/.tsx` file and `index.html` |
| `scripts/build-check.ts` | the built bundle still carries `@property --h`, the OKLCH tokens, the boot script before the first stylesheet, the ambient layer, and no `box-shadow` |
| `scripts/portfolio-parity.tsx` | rendered chart numbers equal the source in every variant, skin and mode (784 renders) |
| `scripts/walkthrough.md` | the browser walkthrough: every interaction, every hue, mobile, the idle-paint test (`rafs 0`, `longTasks 0` over 30 s), the cursor light's schedule and fallbacks |

`npm run check` runs typecheck, palette, restraint, build and parity; it is the gate for every change to the visual layer.

## 12. Applying this to another application

1. **Copy the colour engine:** `src/theme/tokens.css` (keep it the only file with colour literals), `src/theme/hue.js`, `src/theme/boot.js`, `src/theme/ambient.ts`, the `calmBoot()` plugin from `vite.config.ts`, and the `<div class="ambient">` in `index.html` before the app root. Keep `html` on a static neutral and the app root at `z-index: 1`.
2. **Keep the roles, change nothing but hue.** If a new role is needed, pick L and C inside the gamut rule (§3.3), add it to `tokens.css` in the `oklch(L calc(C * var(--k)) var(--h))` form, add its contrast pairs to `palette-check`, regenerate the hex fallback.
3. **Bring the primitives** (`src/styles/global.css`) and the type, space and motion tokens. Choose one display serif for the one line per screen; load fonts with `display=swap`.
4. **Install the checks** (`scripts/*`) and wire `npm run check` into CI before writing any UI. The restraint rules are what keep the system calm when many hands touch it.
5. **Motion conventions:** `EASE` from `src/lib/ui.ts`, `useReducedMotion()` to skip initial animations, `useMediaQuery` for capability gates, per-frame writes outside React state (see `Radar.tsx`).
6. **Data display:** reuse `src/lib/radar.ts` + `src/components/Radar.tsx` with the §6 rules, or apply the same rules to another chart: fixed order, two series at most, hairline rings, real buttons, a hidden table, tweened values.
7. **Variety, if needed:** build a closed kit (§7) and let code compose from it; let models only write into typed, limited, checked slots.
8. **Exceptions:** allow at most one or two, bounded in lightness and chroma, with their own proof in `palette-check` and a fallback matrix like §8. Write them down in `docs/design-notes.md`.
9. **CSP:** the hue boot script is inlined and must be hashed in `script-src`; `style-src` needs `'unsafe-inline'` for the custom-property writes; WebGL needs nothing extra; same-origin workers are fine, `blob:` workers are not.
10. **Ship with the Atmosphere control** (Drift / Still / Off) and honour reduced motion from the first paint — the system is calm only if the person can make it still.

## 13. Sources, in brief

Colour and arousal: Valdez & Mehrabian 1994; Wilms & Oberfeld 2018; Palmer & Schloss's ecological valence theory; Schloss & Palmer 2011 on hue similarity and harmony; Ottosson's OKLab. Perception of slow change: Abrams & Christ 2003 (onset captures attention); Sekulovski (lightness has the lowest smoothness threshold); CSS Color 4's 0.02 JND. Turrell's cycle lengths; Eno's *Music for Airports* (unrelated loop lengths). Depth without effects: Linear, Stripe, Material 3 tonal surfaces, iOS separators, Rothko. Charts: Scott Logic's radar critique, Few's bullet graphs, StatsBomb's fixed templates, W3C WAI complex images and ARIA practices. Cursor light: Alex Harri's WebGL gradients, frost.kiwi on dithering, Jimenez's interleaved gradient noise, Ashima simplex noise. Full citations: `docs/design-notes.md`.
