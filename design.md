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
| No shadows, no blur, no glow | `box-shadow`, `text-shadow`, `drop-shadow()`, `backdrop-filter`, `blur()` are forbidden everywhere, except the two decisions below. |
| Hairlines only | No border thicker than 1 px. The 2 px focus ring (`outline`) is the one exception. |
| Colour lives in one file | Colour literals (`#hex`, `rgb()`, `hsl()`, `color-mix()`, literal `oklch()` in CSS) exist only in `src/theme/tokens.css`. Everything else uses tokens. |
| Motion is opacity and transform | `@keyframes` may not animate paint or layout (`filter`, `background-position`, `width`, `height`, `left`, `top`, `stroke-dashoffset`…). |
| Nothing loops | `infinite` animations are allowed only on an explicit allow-list (a typing indicator, a live dot). |
| Hue properties are never transitioned | A `transition` naming `--h`, `--h2`, `--h3`, `--h4`, `--k`, `--kw`, `--kb`, a `--c-*` chroma number or `all` is an error: it would animate the field on every step. |
| Depth from tone | Surfaces step 2–4 OKLCH lightness points; dividers are 1 px at ~8 % ink alpha; popovers are opaque surfaces with a hairline. |
| One serif line per screen | A display serif for a single headline (the h1, or the one italic line); every other heading is the sans `.title`. The brand wordmark is the logo, not a line, and does not count. |
| Zero idle frames | Between background steps the page draws nothing: no `requestAnimationFrame` loops, no CSS animations running at rest. |
| Exceptions are written down | The deliberate exceptions (§8, §9 and the two below) are bounded, documented, and still pass every check above. |

**Two exceptions to the shadow rule (2026-11-12, the user's decisions).** (1) *A short shadow on paper pieces only.* A `box-shadow` is allowed when every colour in the declaration is `var(--paper-shade)` or `var(--paper-shade-soft)`; the restraint check accepts nothing else, and the paper colours never follow the hue. (2) *One soft radial light behind the skill graph.* `--stage-light` is a single `radial-gradient` in the hue (closest-side, about 94 % of the graph's box, centred on it), faded to transparent. A gradient only: no blur, no `box-shadow`, no animation of its own; it is as dark or as light as `--c-glow-*` allows and the score at the graph's centre stays plain ink.

## 3. Colour

### 3.1 One hue, fixed lightness and chroma

Every colour in the interface is `oklch(L C var(--h))` with **L and C fixed per role** and only the hue variable. Lightness fixed means WCAG contrast is fixed across the entire hue circle. The colour lives in the field (dyed, clearly coloured from the first second) and in tinted surfaces; ink stays near-neutral so reading never depends on the hue.

Seven custom properties drive it, registered with `@property` so they are numbers and never transitioned:

| Property | Meaning |
|---|---|
| `--h` | the base hue, integer degrees |
| `--h2`, `--h3` | the side-lobe hues, ≈ `h + 35` and `h − 45`, each breathing ±10° |
| `--h4` | the fourth lobe, `h + 70` fixed: the field fans across 115° of the wheel, never complementary (no grey mud between lobes) |
| `--k` | global chroma multiplier: 1, or 0 under "Off" (neutral greys) |
| `--kw` | warm-sector easing: chroma × 0.75 at hue 85, back to ×1 fifty degrees away (yellow and orange go muddy fastest) |
| `--kb` | lobe-chroma breath, 0.85…1 |

### 3.2 The roles, light ("Dye on paper", 2026-10-20)

The field itself is dyed; panels are pale paper of the same hue laid on top, separated by a lightness step of about .1 and a 1 px line, never a shadow. Design canvas: https://claude.ai/artifact/U8LW9zvPXdYNAT5T769WfU (the "Theme playground" board scrubs every direction and hue).

| Token | L | C (× `--k`) | Hue | Use |
|---|---|---|---|---|
| `--bg` | .925 | .03 × `--kw` | h | the field |
| `--lobe-1`, `--lobe-2` | .845 | .073 × `--kw` × `--kb` | h2, h3 | ambient lobes |
| `--lobe-3` | .86 | .066 × `--kw` × `--kb` | h | ambient lobe |
| `--lobe-4` | .875 | .058 × `--kw` × `--kb` | h4 | ambient lobe |
| `--surface` | .972 | .012 | h | cards, popovers: tinted paper |
| `--surface-2` | .935 | .029 | h | fields, chips, quiet fills |
| `--well` | .948 | .022 | h | a recessed well inside a panel (darker than the panel in both schemes) |
| `--text` | .23 | .03 | h | ink |
| `--text-muted` | .42 | .042 | h | secondary ink (≥ 4.5:1 on every surface and lobe) |
| `--text-faint` | .50 | .045 | h | placeholders and disabled only (≥ 3:1; never carries data) |
| `--accent` | .46 | .075 | h | links, the serif's emphasised line |
| `--accent-ui` | .50 | .082 | h | focus ring, icons, strokes (≥ 3:1) |
| `--accent-strong` | .38 | .062 | h | primary button fill (surface text on it ≥ 4.5:1) |
| `--accent-soft` | .90 | .046 | h | highlighted fills |
| `--fill` | accent-ui / α .12 (.08, .18) | | h | chart fills |
| `--grid` | ink / α .08 | | h | chart rings, dividers |
| `--line` | .87 | .035 | h | hairlines |
| `--line-strong` | .58 | .06 | h | emphasised hairlines, dashed drop zones (≥ 3:1) |
| `--selection` | .86 | .066 | h | text selection |
| `--danger` | .48 | .12 | 25 (fixed) | errors |

### 3.3 Gamut

Browsers clip out-of-gamut OKLCH instead of mapping it, which bends lightness and hue. One fixed chroma per role must therefore fit **every** hue at that role's lightness: each token is held at or below 0.97 × the smallest sRGB chroma across the 360 hues at its L (cyan near 200° binds at low L, violet near 270° at high L; for example .073 at L .845, .075 at L .46, .041 at L .25). `palette-check` sweeps all 360 hues to prove it. This is why accent ink cannot be much more saturated than before: at text lightness the cyan hues cap it, so the added colour lives in the field and the surfaces. Display-P3 headroom is not used yet.

### 3.4 Fallbacks, in order

1. A generated hex palette for one fixed hue, declared before the `@supports (color: oklch(0 0 0))` block, for engines without OKLCH (`npm run emit:fallback`), one block per scheme.
2. `prefers-reduced-motion: reduce` or `(update: slow)` → the field holds Still.
3. `prefers-contrast: more` → the lobes are removed; ink goes to L .15 / .33, accent strokes .42, lines to .70, grid alpha .2 (in dark: ink .97 / .85, accent strokes .84, lines .46, grid alpha .3).
4. `forced-colors: active` → the field is hidden; every token maps to a system colour.
5. "Off" → `--k: 0`: every chroma collapses to zero, lightness stays.

### 3.5 The dark scheme ("Dye by night", 2026-10-20)

Not the light table inverted: the same structure in another mood. The field carries the dye (L .25 with real chroma, lobes at .33–.36), and the panels are deeper ink pools (.185), so text sits on the calmest, darkest tone and the colour stays in the field around it. There is no near-black, no neutral grey, no neon accent and no glow: the field is a coloured dark, ink is off-white (.92, never pure white), and secondary ink stays ≥ 4.5:1 over every lobe (worst 4.76:1). Elevation reads the other way from light (panels sit *below* the field in lightness), so separation is the 1 px line plus the tone step, never a shadow.

| Token | L | C | Light is |
|---|---|---|---|
| `--bg` | .25 | .041 × `--kw` | .925 / .03 |
| `--lobe-1`, `--lobe-2` | .36 | .059 × `--kw` × `--kb` | .845 / .073 |
| `--lobe-3` | .33 | .054 × `--kw` × `--kb` | .86 / .066 |
| `--lobe-4` | .34 | .05 × `--kw` × `--kb` | .875 / .058 |
| `--surface` | .185 | .022 | .972 / .012 |
| `--surface-2` | .235 | .035 | .935 / .029 |
| `--well` | .15 | .02 | .948 / .022 |
| `--text` | .92 | .016 | .23 / .03 |
| `--text-muted` | .75 | .03 | .42 / .042 |
| `--text-faint` | .66 | .035 | .50 / .045 |
| `--accent` | .83 | .081 | .46 / .075 |
| `--accent-ui` | .77 | .113 | .50 / .082 |
| `--accent-strong` | .85 | .071 | .38 / .062 |
| `--accent-soft` | .30 | .049 | .90 / .046 |
| `--fill` / `--fill-2` / `--fill-strong` | accent-ui at α .18 / .12 / .26 | | α .12 / .08 / .18 |
| `--grid` | ink at α .12 | | α .08 |
| `--line` | .30 | .035 | .87 / .035 |
| `--line-strong` | .56 | .05 | .58 / .06 |
| `--selection` | .40 | .066 | .86 / .066 |
| `--danger` | .72 | .12 at hue 25 | .48 / .12 |

**Switching.** The dark table applies under `prefers-color-scheme: dark` unless `<html data-scheme="light">`, and always under `<html data-scheme="dark">`; the Atmosphere menu's second group (System / Light / Dark) writes the attribute, persisted in `localStorage` (`tayyari.scheme`) and applied by the boot script before the first paint. `?scheme=light|dark` pins it for screenshots, like `?h=`. The `<html>` static background and `color-scheme` flip with it. Where the engine has view transitions and motion is welcome, a manual switch crossfades the two schemes (opacity, `--d-base`); elsewhere it is instant. Every scheme selector is wrapped in `:where()` so each has the specificity of `:root` and the file order decides; the dark table exists twice in the sheet (the media query and the attribute) and `palette-check` asserts the copies agree and that dark redefines every light role.

**What stays light.** A portfolio page (`.pf-scope`) keeps the earlier, paler light table (bg .97, lobes .90 / .044, surface .99) recomputed from its own hues, so public pages and the Old Newspaper skin keep the palettes they were designed against in either scheme; the portfolio editor's chrome follows the app. The cursor's wake runs in both schemes with its own dye tones (§8).

## 4. The living gradient

**Representation.** One fixed, full-viewport layer (`position: fixed; inset: 0; z-index: −1; contain: strict`) with `background-color: var(--bg)` and four radial lobes that together cover most of the page, mixed `in oklab` where supported (a plain declaration first is the fallback):

```
78% 72% at 8% 12%   → lobe-1 fading to its own colour at alpha 0 by 74 %
72% 66% at 94% 4%   → lobe-2, likewise
95% 62% at 42% 112% → lobe-3, likewise
46% 42% at 74% 62%  → lobe-4, by 70 %
```

Each lobe fades to *its own colour at alpha 0*, never to a neutral, so no hue swings in the falloff. The `<html>` behind it is a static neutral that never repaints; the view sits above at `z-index: 1`.

**Rate.** 1° every 7 s: a full circle in 42 minutes. At lobe chroma .073 one step is ≈ 0.0026 ΔE — far below the ≈ 0.02 JND and the ≈ 0.16 ΔE/s temporal detection threshold — so no single step can be seen. Over five minutes the lobes move about two JNDs: noticed only in memory.

**Why hue, never lightness or position.** Lightness change has the lowest detection threshold; movement in peripheral vision is noticed before fades. The field changes colour only. Motion *onset* captures attention while ongoing motion does not, so the hue is a pure function of wall-clock time: nothing ever starts.

**Never loops visibly.** Unrelated periods: the hue circle (42 min), the lobe offsets breathing (13 and 11 min), the lobe chroma breathing (17 min). Combined period ≈ 100 h.

**Cost.** Six custom properties written by one `setTimeout` aligned to the 7-second grid; no `requestAnimationFrame`; nothing transitioned. Hidden tabs stop the timer; on return the hue is recomputed from the clock. The maths lives in one dependency-free module (`src/theme/hue.js`):

```
h  = floor(h0 + 360 · (now mod 42 min) / 42 min) mod 360
h2 = h + 35 + 10·sin(2π·now / 13 min)
h3 = h − 45 − 10·sin(2π·now / 11 min)
h4 = h + 70
kw = 1 − 0.25 · max(0, 1 − dist(h, 85) / 50)
kb = 0.925 + 0.075·sin(2π·now / 17 min)
```

`h0` is a random per-install offset kept in `localStorage`, so two tabs always agree and reloads never jump. The same module is inlined into `<head>` by a build plugin and run by a boot script before the first stylesheet, so the first paint already carries the right hue.

**Controls.** Drift / Still / Off and System / Light / Dark, both persisted, in one Atmosphere menu. `?h=<deg>` pins the hue for previews and screenshots and forces Still; `?scheme=light|dark` pins the scheme. Reduced motion forces Still and the control reads "Still · system".

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

**Primitives** (`src/styles/global.css`): `.btn`, `.btn-primary`, `.btn-quiet`, `.btn-sm`, `.field`, `.chip`, `.panel`, `.hairline`, `.kicker`, `.serif`, `.title`, `.numeral`, `.sr-only`, `.skip`, `.wrap`, `.scroll`. Real `<button>`, `<a href>`, `<input>` + `<label>` everywhere; `aria-label` on icon-only buttons; touch targets ≥ 44 px (36 px controls grow to 44 under `(pointer: coarse)`).

**States** (audit pass, 2026-11-08). A button presses: `.btn:active` moves 1 px down over `--d-fast`. Hover rules sit inside `@media (hover: hover)` so they never stick on touch. The primary button has one disabled look (`--surface-2` fill, `--text-faint` ink), not one per screen. Every lazy screen shows a frame-shaped static skeleton while its chunk loads, never a blank. Every screen offers a `.skip` link to its main region. Errors name what failed and the next step, in the active voice, without exclamation marks.

**Token discipline.** Every `font-size`, spacing, radius and duration is a token. The only literal pixel values are 1–2 px optical nudges, element dimensions (widths, heights, chart sizes), and two phone floors inside `(max-width: 760px)`: 12 px for kickers, 13.5 px for text people read or act on. `.btn` padding is 8 / 12 px, `.btn-sm` 4 / 8 px.

## 6. Data display: radar charts

Radars are kept because the product is about a *shape of skills*, and tamed because the literature is blunt about their faults:

- 5–8 axes in a **fixed order per profile**, never sorted by value, so shapes become recognisable with use.
- At most two series: "you" as a 12 % fill with a 1.5 px stroke; beside a target, "you" fills at 18 % and the target is an 8 % fill under a dashed hairline (`--fill-strong`, `--fill-2`), so the person reads darker and the role lighter (benchmark thin, actual bold).
- Every axis is one of the person's own areas. Whatever fits none of them is named beside the chart, never summed into an "Other" axis (a catch-all outweighs every real area and bends the shape towards a corner that means nothing).
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

## 7a. The quiet look (default since 2026-11-03; a neutral stage since 2026-11-12)

`html[data-look='quiet']` (tokens.css block c1b) is the look the app and the static site ship with; the colour scheme follows the device unless the person picks one (no `data-scheme` means System). Panels are lighter than the field in both schemes: that one rule is the look.

**The neutral stage (2026-11-12, the user's decision).** The stage never follows the hue. The field, panels, wells, ink, hairlines and grid are fixed neutrals (hue 264, chroma .002–.005, collapsing to zero under Off), so every hour has the same quiet ground and the same contrast. The hue feeds only the colour that is doing a job: the accents, the fills and rule, the selection, the dashboard's tile fields (`--tile-1` … `--tile-4`, at `--h`, `--h2`, `--h3`, `--h4`) and one soft light behind the skill graph (§2). There are no dyed lobes, so the ambient layer is a flat fill. The paper pieces (`--paper-*`, `--tape`, ink, pen) keep their own fixed colours in every look and never take the hue.

| | Light | Dark |
|---|---|---|
| Field | L .955 | L .145 |
| Panel, chip, well | L .985 / .972 / .94 | L .19 / .225 / .115 |
| Ink, muted, faint | L .20 / .42 / .52 | L .955 / .76 / .62 |
| Line, strong line | L .86 / .66 | L .30 / .45 |
| Accent-ui (marks, bars, radar), fills | L .52, cap .17 | L .74, cap .17 |
| Accent (links) | L .46, cap .14 | L .82, cap .135 |
| Primary fill | L .38, cap .09 | L .90, cap .07, panel-coloured text |
| Selection | L .88, cap .05 | L .36, cap .05 |
| Tiles | L .87 / .89 / .885 / .90, cap .085 (.08 for tiles 3, 4) | L .34 / .35 / .30 / .34, cap .095 (.09 for tile 3) |
| Cursor dye | L .80, C .11 at .75 | L .62, C .10 at .30 |

**The chroma rule.** An accent takes `min(cap, 0.94 × the most chroma that hue holds inside sRGB at that lightness)`. A fixed low chroma is what made green and yellow hours olive; this keeps every hour as alive as it can be and still inside the gamut. The numbers are computed once, in `src/theme/hue.js` (`CHROMA_SPEC`, `hueAt`), from the hue each role is drawn at that step (`--h2` and `--h3` breathe), written beside the hues as `--c-*-d` and `--c-*-l` (dark and light, both every step, so the file stays pure), and used in tokens.css as `calc(var(--c-ui-d) * var(--k))`. By day the tiles' cap eases to half within 30° of hue 98, so no tile competes with the post-it yellow. `@property` initial values are hue 250's, so a first paint before the script is sane. `palette-check` imports the same numbers and proves them at all 360 hues and every breath phase.

**The fixed hues (the `today` look and `.pf-scope` are unchanged).** In `today` the tiles use a fixed chroma, held under the sRGB ceiling at their lightness; paper is the same in both looks.

**Why the dark table moved (2026-11-07, canvas "Leafout Dark Mode Explorations", direction D), kept for the reasoning.** The first quiet dark read faint and dull: the field had no dye, every non-text tone sat between L .145 and .25, hairlines were grey on grey, the accents were pastel by force (at L .84 the gamut caps chroma near .08 at the blue end) and the ink stopped at .93. The fixes that remain: the field at .145 with a panel step of .045 or more, ink near .96 (off-white, so no halation), and accents at the lightness where a real blue fits inside sRGB. The neutral stage keeps all of that and takes the dye out of the ground, leaving it in the accents.

In dark, the spotlight (`src/light/Spot.tsx`) is white at 10 % under the wake. Escape hatch: `localStorage tayyari.look = 'today'` or `?look=today` brings back the earlier look; `?look=quiet` pins quiet. `boot.js` writes the attribute before first paint; `setLook` / `useLook` in `theme/ambient.ts` change it at runtime. Compare the two, scheme by scheme and screen by screen, at `/dev/design`.

## 7b. The hand-touched kit and the density scale (2026-11-11)

**Hand-touched means yours, clean means measured.** A small kit in `src/styles/global.css` marks what the person wrote, did or earned, and gives an empty state a human nudge. Everything the code or a model computed (scores, evidence, requirement tables, analysis, forms) stays straight and precise. That contrast is the point: the page tells you at a glance which parts are you and which parts are the measurement.

| Class | What it is | Use it for |
| --- | --- | --- |
| `.slip` (+ `.slip-pin`, `.slip-tape`, `.slip-fold`) | a slip of paper on `--accent-soft` with a 1px `--line-strong` edge, held by a pin, a strip of tape, or with a folded corner | the person's own note, their card, a nudge about their resume, the coach's nudge |
| `.tilt-l`, `.tilt-r` | a turn of 1.5 or 1.2 degrees; a pressable slip straightens on hover and focus | slips only |
| `.hand`, `.hand-margin` | Instrument Serif italic; the margin form is turned 2 degrees | the person's own words; one line in an empty state |
| `.stamp` (+ `.stamp-ok`, `.stamp-quiet`) | a small mono rubber stamp, turned 2 degrees | a stage reached, a verdict earned, "new", "done", "default" |
| `.hl` | a highlighter swipe behind a phrase | the one freshest win in a list |
| `.scribble` | a ring or underline drawn by hand, an inline SVG path with a 1.25 stroke | one number the person earned (weeks in a row) |
| `.stack` | one or two offset edges behind a sheet | a pile of the person's own things (notes, left-out blocks) |

Rules: at most three turned pieces in view; nothing turned holds a field, a table or a long list; no shadow (a slip is lifted by its tone and its turn); under 760px and under reduced motion nothing is turned. The kit handles both, so screens never add their own rotation.

**Materials (2026-11-12, first on the dashboard).** A screen opens as one screen: one hero, then a mosaic of tiles sized by what matters now, edge to edge with a 2px seam of `--bg`, no radius and no border. A tile is a material, and a material means the same thing everywhere:

| Material | Built from | Means |
| --- | --- | --- |
| Notebook paper, in pen | `--paper-sheet`, `.ruled` rows on `--rule`, `.pen` (`--font-pen`), `.tick` | what the person does or wrote: to-dos, a plan's week, what to re-drill |
| A field of colour | `--tile-1..4`, flat or a two-stop radial of two of them | where they stand right now: a stage, a verdict, where they left off |
| Graph paper | dots of `--grid` over `--surface-2` | what was measured or cited; never turned, never in pen |
| A post-it | `.postit` on `--paper-yellow`, `--paper-pink` or `--paper-blue`, ink `--paper-ink`, lifted by `--paper-shade` | their own note, stuck to the thing it is about |
| An ink block | `--accent-strong` behind `--surface` text | the one thing to press next; one a screen |

On the sheet and the calendar page the ink is `--text` and `--text-muted` (the sheet is dark at night); `--paper-ink` is only for coloured paper and the yellow highlighter (`.hl-paper`). A post-it may lap over a neighbouring tile; nothing measured ever does. The app sets a tick only from a dated record; the person may tick by hand.

**The kit for every other screen (2026-11-12).** `src/styles/materials.css` (loaded once from `main.tsx`, prefix `m-`) holds the frame and the materials as classes, and `src/components/materials.tsx` holds the hand-drawn pieces. The dashboard keeps its own `os-` and `t-` classes from before the kit; new work uses the kit.

| Piece | What it is |
| --- | --- |
| `.m-screen` (+ `.is-fit`, `.is-wide-hero`, `.is-stack`), `.m-hero`, `.m-mosaic` (+ `.is-rows`, `.is-12`) | the frame: one hero beside a six-column mosaic with 2px seams; `.is-fit` makes it one screen tall (viewport minus `--m-top`); under 1100px the hero stacks above, under 760px every tile is one full row |
| `.m-tile` (+ `.m-c1`…`.m-c6`, `.m-r2`…`.m-r5`, `.is-row`, `.is-clip`), `.m-foot` | a tile and its span |
| `.m-paper` (+ `.is-loose`), `.m-field`, `.m-field-1`…`-4`, `.m-graph`, `.m-ink`, `.m-gap`, `.m-plain`, `.m-quiet`, `.m-desk` | the materials and the neutral grounds |
| `.m-page-head`, `.m-page-title`, `.m-lines`, `.m-line`, `.m-line-t`, `.m-cite`, `PageLine`, `HandTick`, `PenStrike` | a notebook page: ruled lines in pen, each with the fact it rests on; a tick is a button only when the person may tick it, otherwise a mark set from a record |
| `Pips`, `.m-pips` | a row of circles for a count that is the point (stages reached, requirements covered, questions defended): filled, half, dashed; always with the same count in words |
| `.m-big`, `.m-count`, `.m-co`, `.m-stage`, `.m-fine`, `.m-strong`, `.m-clip` | one large counted number, a floating number beside a chart, a count per stage, small type |
| `.stick.is-flow`, `.m-turn-l`, `.m-turn-r`, `.m-margin` | a post-it in the flow of a tile and the only turns; all go straight on a phone and under reduced motion |

Ink on a material: `--text` and `--text-muted` on paper, graph paper, neutral ground and on a field (links on a field are `--text`, underlined, never `--accent`); `--surface` on an ink block; `--paper-ink` on a post-it. One ink block a screen, at most two fields, at most three turned pieces in view. Forms, tables, documents and long reading stay on neutral ground.

**Density.** Gap between page sections `--s4`; card padding `--s4` top and bottom, `--s5` left and right; row padding `--s2`; headers 64px; one hero type size per screen and everything else a step down. Controls stay at least 36px tall on desktop and 44px on touch and under 760px.

**Contrast (quiet look).** Since 2026-11-12 a light card is one step lighter than the field (`--surface` .985 over `--bg` .955), hairlines are `--line` .86 light and .30 dark, and small text is `--text-muted` .42 / .76 and `--text-faint` .52 / .62 (all neutral). Prominence comes from these, never from a shadow.

## 8. Exception one: the liquid cursor

Every screen (2026-10-20; canvas https://claude.ai/artifact/U8LW9zvPXdYNAT5T769WfU, "The cursor as liquid"). A soft drop of dye follows the pointer like a hand moving through coloured liquid: it leans the way you move, stretches and thins with speed, trails a short wake, and settles with at most a small overshoot when you stop. It bends "no spatial motion" and "no effects", and it is bounded so that it stays one exception.

**Why it is allowed.** The motion is the person's own: it moves only because they moved, so there is no onset that is not theirs. Still, it draws nothing: the loop stops and the last frame stays, waking only for the dim at 10 s and the fade to sleep at 45 s. It is not mounted on touch or coarse pointers, under reduced motion or slow updates, under forced colours or more contrast, with the Atmosphere Off, or without WebGL2. It lives under the view (z 0), so opaque panels hide it and it shows in the field around them.

**Dye, not light.** Chroma carries it, never brightness, which is what separates it from a spotlight or an "AI glow": in light the head is a touch deeper than the field (L .81, C .11 → tail .86, .08), in dark a touch above it (L .37, C .085 → tail .33, .07), never near white. Overlapping blobs combine as a capped union whose peak is exactly the head's alpha (.8 light, .78 dark), so overlaps cannot brighten. `palette-check` composites the dye at peak alpha over the field and every lobe at all 360 hues, in both schemes, and proves `text` and `text-muted` ≥ 4.5:1 and the accents ≥ 3:1 (worst: muted 4.62 light, 4.59 dark).

**Colour.** Head hue = `--h` + 24° plus a slow wander of at most ±12°; the hue turns −60° from head to tail, blended along the short arc, chroma eased through yellow–orange like `--kw` and fitted to sRGB in the shader the same way the proof does.

**Shape and motion** (`LIGHT.wake` in `src/light/palette.ts`). The head is the latest coalesced pointer sample: no smoothing, no input lag. Ten followers are springs in a chain (K 1400 s⁻², damping ratio .72), integrated in ≤ 4 ms substeps so 60 Hz and 120 Hz behave alike. Radius 0.16 × the short side, clamped 90–140 px, tapering 60 % to the tail. Stretch is min(speed / 1600, 1) × .9 along each segment, with thinning across that keeps the area.

**Rendering.** One WebGL2 pass on a half-resolution canvas (≤ 960×600 texels): 11 elliptical gaussians per pixel, scissored to the wake's bounding box, premultiplied alpha, source-over, ±1 LSB triangular dither. Measured in the preview: about 0.2 ms GPU and 0.2 ms main thread per frame. The watchdog lowers the render scale when frames run long and switches the cursor off for the session on a third strike.

**Boundaries.** No ring, no bright core, no outline you can trace, no rhythm, no banding, no additive blending.

## 9. Exception two: print conventions in the newspaper skins

Two surfaces are newspapers and follow print rather than §2's type rules: the portfolio's Old Newspaper skin (`.pf-scope`, `press.css`) and the Leafout Weekly (`src/weekly`, `weekly.css`; the landing site's Weekly section is the same paper). Inside them, and only there: a blackletter nameplate at 56 px or larger, italic display lines, 700 and 900 weights, uppercase kickers with wide tracking, the fixed cream-and-ink press palette, and the thick-thin double rule (built as a fixed-height pseudo-element with a `var()` background so the border rule still holds). Everything else in those skins is hairline and shadowless, and nothing outside them borrows any of it.

## 10. Accessibility, as drawn

- Text ≥ 4.5:1 (≥ 3:1 at 24 px+ and for UI strokes) on every surface it can sit on, proved at all 360 hues. Caption greys and fills under white text fail first: darken both.
- Colours that must be told apart differ in lightness, not hue alone.
- A 2 px focus ring in `--accent-ui`, 2 px offset. Keyboard patterns from the ARIA practices: roving tabindex for charts, menus with arrow keys and Escape, bottom sheets on narrow screens.
- Reduced motion, slow updates, forced colours and more contrast each have a defined response (§3.4, §8).
- Charts carry a hidden data table; icon-only controls carry labels; live regions announce file and link changes.

## 11. The verification toolkit

| Script | Proves |
|---|---|
| `scripts/palette-check.ts` | every token inside sRGB at all 360 hues and every breath phase (the chroma numbers are imported from `src/theme/hue.js`, not re-implemented), in both looks and both schemes; `--text` and `--text-muted` on every tile; `--paper-ink` on every paper colour at 7:1 and `--paper-ink-2` at 4.5:1; `--surface` on `--accent-strong`; accent text on `--surface` and `--bg`; every hue-following chroma multiplied by `--k`, so Off collapses it; every contrast pair at its floor, alpha tokens composited over each scheme's field; the dark table's two copies agree and cover every role; the cursor's dye, composited at peak alpha over the field and every lobe, under every text role (both schemes); `--fallback <hue>` emits the hex blocks for both schemes |
| `scripts/restraint-check.ts` | the rules in §2, over every `.css/.ts/.tsx` file and `index.html`; a `box-shadow` passes only with `--paper-shade` colours |
| `scripts/build-check.ts` | the built bundle still carries `@property --h`, the OKLCH tokens, the dark table under its media query and attribute, the `@property` registration of every chroma number, the stage tokens, the boot script (hue, chroma and scheme) before the first stylesheet, the flat ambient layer, and no `box-shadow` but the paper one |
| `scripts/portfolio-parity.tsx` | rendered chart numbers equal the source in every variant, skin and mode (784 renders) |
| `scripts/walkthrough.md` | the browser walkthrough: every interaction, every hue, mobile, the idle-paint test (`rafs 0`, `longTasks 0` over 30 s), the cursor light's schedule and fallbacks |

`npm run check` runs typecheck, palette, restraint, build and parity; it is the gate for every change to the visual layer.

## 12. Applying this to another application

1. **Copy the colour engine:** `src/theme/tokens.css` (keep it the only file with colour literals), `src/theme/hue.js`, `src/theme/boot.js`, `src/theme/ambient.ts`, the `calmBoot()` plugin from `vite.config.ts`, and the `<div class="ambient">` in `index.html` before the app root. Keep `html` on a static neutral and the app root at `z-index: 1`.
2. **Keep the roles, change nothing but hue.** If a new role is needed, pick L and C inside the gamut rule (§3.3), add it to `tokens.css` in the `oklch(L calc(C * var(--k)) var(--h))` form in the light table and in both copies of the dark table (§3.5), add its contrast pairs to `palette-check`, regenerate the hex fallbacks.
3. **Bring the primitives** (`src/styles/global.css`) and the type, space and motion tokens. Choose one display serif for the one line per screen; load fonts with `display=swap`.
4. **Install the checks** (`scripts/*`) and wire `npm run check` into CI before writing any UI. The restraint rules are what keep the system calm when many hands touch it.
5. **Motion conventions:** `EASE` from `src/lib/ui.ts`, `useReducedMotion()` to skip initial animations, `useMediaQuery` for capability gates, per-frame writes outside React state (see `Radar.tsx`).
6. **Data display:** reuse `src/lib/radar.ts` + `src/components/Radar.tsx` with the §6 rules, or apply the same rules to another chart: fixed order, two series at most, hairline rings, real buttons, a hidden table, tweened values.
7. **Variety, if needed:** build a closed kit (§7) and let code compose from it; let models only write into typed, limited, checked slots.
8. **Exceptions:** allow at most one or two, bounded in lightness and chroma, with their own proof in `palette-check` and a fallback matrix like §8. Write them down in `docs/design-notes.md`.
9. **CSP:** the hue boot script is inlined and must be hashed in `script-src`; `style-src` needs `'unsafe-inline'` for the custom-property writes; WebGL needs nothing extra; same-origin workers are fine, `blob:` workers are not.
10. **Ship with the Atmosphere control** (Drift / Still / Off, System / Light / Dark) and honour reduced motion and the colour-scheme preference from the first paint — the system is calm only if the person can make it still, and only if it meets them in the scheme their device is in.

## 13. Sources, in brief

Colour and arousal: Valdez & Mehrabian 1994; Wilms & Oberfeld 2018; Palmer & Schloss's ecological valence theory; Schloss & Palmer 2011 on hue similarity and harmony; Ottosson's OKLab. Perception of slow change: Abrams & Christ 2003 (onset captures attention); Sekulovski (lightness has the lowest smoothness threshold); CSS Color 4's 0.02 JND. Turrell's cycle lengths; Eno's *Music for Airports* (unrelated loop lengths). Depth without effects: Linear, Stripe, Material 3 tonal surfaces, iOS separators, Rothko. Charts: Scott Logic's radar critique, Few's bullet graphs, StatsBomb's fixed templates, W3C WAI complex images and ARIA practices. Cursor light: Alex Harri's WebGL gradients, frost.kiwi on dithering, Jimenez's interleaved gradient noise, Ashima simplex noise. Full citations: `docs/design-notes.md`.
