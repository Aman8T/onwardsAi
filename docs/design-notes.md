# Tayyari — calm visual layer: design notes

Why the UI looks the way it does, with the evidence behind each call. Written 2026-10-06 alongside the redesign from the "Keycap Spectrum" theme to the calm, minimalist layer (living gradient + radar charts). Functionality and information architecture were not changed.

## 1. The feeling

Opening the app should feel like exhaling. Brian Eno's test for ambient music applies: "as ignorable as it is interesting". Someone who stays a few minutes should sense the space changing without being able to point at the moment it changed. Attention restoration theory calls this "soft fascination": effortless interest, like clouds drifting (Kaplan; see Terrapin's *14 Patterns of Biophilic Design*).

## 2. Colour: what actually reads as calm

- **Saturation is the lever, not hue.** Valdez & Mehrabian (1994) fitted Arousal ≈ −.31·Brightness + .60·Saturation; Wilms & Oberfeld (2018) found saturated and bright colours raise self-rated arousal and skin conductance. Reviews (Elliot 2015; healthcare colour reviews) call the "blue calms, red excites" story weak. So the field is high-lightness and low-chroma at every hue, and the hue is free to travel.
- **Nature-associated colours are liked; muddy ones are not.** Palmer & Schloss's ecological valence theory: sky and clean-water blues are liked, browns and dark yellows (olive) are disliked. Yellows and oranges therefore stay pale (L ≥ .90) and their chroma is eased down.
- **Harmony rises with hue similarity** (Schloss & Palmer 2011). The visible gradient always spans an analogous window of ~35–45°; complementary pairs never appear.
- **"Muddy" has three causes**: chroma collapse through grey when interpolating in sRGB/HSL, lightness wobble between hues in HSL/HSV, and dark warm hues reading as olive. OKLCH fixes the first two by holding chroma and lightness perceptually constant (Ottosson's OKLab); the third is handled by lightness and the warm-sector easing.

## 3. The living gradient

**Representation.** One hue number `--h` on `:root`. Every colour in the UI is `oklch(L C var(--h))` with L and C fixed per role; only hue drifts. Lightness fixed means WCAG contrast is fixed: dark ink at L .25 on a field at L .97 varies by only a few tenths of a ratio point across the whole circle. The side lobes use `--h2` (≈ h + 35°) and `--h3` (≈ h − 45°).

**Rate.** 1° every 7 s, a full circle in 42 minutes, inside Turrell's range (Akhob 24 min, Skyspace sequences ≈ 40 min, Virtually Squared 50 min). Just-noticeable difference in OKLab is ≈ 0.02 ΔE (the figure CSS Color 4 uses for gamut mapping). At lobe chroma .044 one step is ≈ 0.0016 ΔE, and the temporal detection threshold for smooth 10 s light transitions is ≈ 0.16 ΔE/s (PMC7774110), so no single step can be seen. Over five minutes the lobes move ~2 JNDs: noticed only in memory ("it was blue earlier").

**Why hue, never lightness.** Lightness change has the lowest smoothness threshold (Sekulovski, CIC 15), then hue, then chroma; isoluminant, smooth, asynchronous colour change is the hardest to detect (Macquarie change-blindness work). Motion *onset* captures attention while ongoing motion does not (Abrams & Christ 2003), so the hue is a pure function of wall-clock time: nothing ever starts.

**No spatial motion.** Peripheral vision is more sensitive to flicker and movement than the fovea; sliding and position change grab attention faster than fades (NN/g). The background changes colour only. Under WCAG 2.2.2 a colour-only drift is not "moving, blinking or scrolling", but a Drift / Still / Off control is provided anyway and `prefers-reduced-motion` forces Still.

**Never loops visibly.** Eno's *Music for Airports* uses loops of unrelated lengths so the combination never repeats. Here: the hue circle (42 min), the side-lobe offsets breathing (13 and 11 min), and a chroma breath on the lobes (17 min). Combined period ≈ 100 h.

**Warm-sector easing.** People are most sensitive to hue change in the orange region (CIC 19) and warm hues go muddy fastest, so the lobe chroma multiplier `--kw` eases to ≈ .75 through yellow–orange (oklch 60–110°) and back to 1 elsewhere. Weighting the cycle by *speed* instead would make warm change more visible, not less.

**Cost.** One fixed layer with three radial gradients; five custom properties written by one `setTimeout` every 7 s; no transition on any of them. Gradients never run on the compositor, and animating a registered custom property with keyframes would repaint the layer every frame, so stepping is the only cheap option. Between steps the page produces zero frames. Hidden tabs stop the timer; on return the hue is recomputed from the clock.

**Dark (2026-10-17, canvas "Tayyari Dark Mode").** The dark scheme keeps the whole argument and flips only lightness per role (`design.md` §3.5). Two calls follow from the research above rather than from habit: the lobes drop to chroma .034 (a saturated lobe on an L .16 field reads as a lit lamp, the arousal the system avoids), and the ink is held at L .93 rather than white, because pure white text on near-black is the halation case of the Wilms & Oberfeld data. The primary button inverts to a light fill: the same `surface` token is its text, so the swap costs no CSS. The cursor light stays off in dark; the "light through paper" idea has no dark counterpart yet (a dark-field light would have to be darker than its ground, not brighter, and that is its own design).

**Gamut.** Browsers clip out-of-gamut oklch instead of gamut-mapping it, which bends lightness and hue. The sRGB limit at constant L is tightest around cyan (≈ 200°) up to L .75 and around blue (≈ 267°) above that: roughly C ≤ 0.17·L up to L .75, then .100 at L .80, .048 at L .90, .014 at L .97. Every token is kept ≤ 0.93 × that limit; `scripts/palette-check.ts` sweeps all 360 hues to prove it.

**Fallbacks, in order.** Hex palette for one fixed hue declared before the `@supports (color: oklch(0 0 0))` block → Still under reduced motion or `(update: slow)` → stronger ink and no lobes under `prefers-contrast: more` → ambient hidden and system colours under `forced-colors: active` → greys under Off.

## 4. Depth without effects

Reference products build hierarchy from tone, not shadow: Linear ("structure should be felt, not seen"; themes generated in LCH from three inputs), Stripe (CIELAB palette on shared lightness levels), Material 3 (tone-based surfaces at 100/98/96/94/92/90 L*), iOS (separators at ~29 % alpha). Rothko's fields hover on a ground with soft edges and low value contrast; Turrell's Ganzfelds remove edges entirely.

Here: surfaces step 2–4 OKL points (field .97, surface .99, surface-2 .955), dividers are 1 px at ~8 % ink alpha, popovers are opaque surfaces with a hairline, and the only thing thicker than 1 px is the 2 px focus ring (WCAG 2.4.7 / 2.4.13).

## 5. Radar charts

The literature is blunt about radars (Scott Logic, Observable, Datawrapper, Nightingale): area grows with the square of values, axis order changes the shape, lines imply continuity, and overlapping series occlude. The design keeps what works and removes the rest:

- 5–8 axes, fixed order per profile, never sorted by value (StatsBomb's fixed templates make shapes recognisable with use).
- At most two series: "you" as a 12 % fill with a 1.5 px stroke, the target as a dashed hairline (Few's bullet-graph logic: benchmark thin, actual bold). From 2026-10-17, at the user's request, the target also gets a light 8 % fill and "you" deepens to 18 % beside it: an unfilled target was hard to tell from the grid, and the two shapes should read as darker (you) and lighter (the role) at a glance.
- Straight edges only; hairline rings at 25/50/75/100; labels outside the rim; numerals in tabular figures.
- Hover highlights one spoke; vertices are real buttons with roving tabindex and arrow-key navigation (WAI-ARIA APG); a visually hidden data table is always present (W3C WAI complex images).
- Animation tweens the values along their spokes, never the path string; axis-set changes crossfade (vertex counts differ).

## 6. Type, space, motion

- Geist 400/500/600 for UI, Geist Mono (tabular) for data numerals, Instrument Serif for one line per screen. Display tracking −0.02em, small labels +0.01em (Inter's dynamic-metrics curve as the reference).
- Spacing 4–96 px; measure 62ch; *ma* treated as positive space, not emptiness; Rams: "less, but better".
- UI motion 150–300 ms ease-out with no bounce (NN/g: 400 ms is the slow limit for feedback); whole-screen crossfades up to 500 ms (Apple's "smooth" spring); the one-time radar reveal 900 ms. Only the typing dots and a live-thread dot ever loop.

## 7. The cursor light: the one exception

Added 2026-10-09, Home only: a soft field of colour follows the pointer (design canvas "Cursor Light Field"; code in `src/light/`). It is the one deliberate exception to §3's "no spatial motion" and §4's "no effects", and it is bounded so that it stays one.

**Why it does not break the rule it bends.** The motion is the person's own: the field only ever moves because they moved, so there is no onset that is not theirs (Abrams & Christ: *onset* captures attention, self-caused motion does not). Nothing moves while they are still — the outline morphs, which is isoluminant colour change at a rate below the detection threshold in §3 — and after 45 s of stillness the light fades and the loop stops, so the page is back to zero frames. Under `prefers-reduced-motion` and `(update: slow)` it is not mounted at all, like Drift becoming Still; neither is it on touch or coarse pointers, under forced colours or more contrast, or with the Atmosphere Off.

**Luminous without brightness.** The field is at L .97, so "brighter" has nowhere to go; a bright core on a light page is the spotlight everyone has seen. Light through paper does the opposite: the heart is paler and less saturated (L .965, C .016) and the colour sits in the body around it (L .89, C .048), fading with no edge — a Gaussian falloff warped ±30 % by two octaves of simplex noise, plus a wider half-strength halo on its own slower noise. Lightness stays in the page's own band and never below .89, which keeps `text-muted` at or above 5:1 and the italic headline (`accent`, large text) above 3:1 at every hue; `scripts/palette-check.ts` proves it from the same constants the shader is built from. The blend is plain source-over: `screen` or `plus-lighter` would wash a .97 field to white. Chroma eases through yellow–orange exactly like `--kw`.

**Colour.** The hue is the field's `--h` plus a wander bounded to ±56°, two sines (97 s and 41 s) whose phase also advances with the distance travelled, so moving stirs the colour but never carries it out of the analogous window (§2, Schloss & Palmer); as `--h` drifts, the light visits every hue. The body sits 28° round from the heart, like the side lobes.

**Rendering.** One WebGL2 fragment shader on a canvas at half the viewport's CSS size (at most 960×600 texels), fixed between `.ambient` and the view. CSS blur is forbidden and slow at this size; CSS gradient layers band on a long pastel ramp and can only make unions of ellipses; SVG filters rasterise on the CPU. The shader computes each pixel in OKLab, clips chroma to sRGB per pixel, and adds ±1 LSB of triangular noise before the 8-bit output (Jimenez's interleaved-gradient-noise argument: dither in the shader where the gradient is still continuous). Measured on an Intel UHD integrated GPU: 0.36 ms at 91 k texels, 1.17 ms at 362 k, so about 1 ms for a 1440×900 window. Per frame the main thread does a few exponential smoothings and one draw call.

**Follow.** The heart tracks the pointer with a 35 ms time constant (17 ms at speed): measured 24 px behind at 700 px/s, 4 % of the body's width, invisible on an edgeless shape. The organic trailing lives in the shape instead — the direction is smoothed over 120 ms, the field stretches up to 35 % along travel and relaxes over 160 ms, and the halo lags the heart by part of that stretch. `pointermove` stores the latest sample only (1000 Hz mice cost nothing; `getCoalescedEvents` is for drawing paths, not for following), and the frame interval is clamped before any division. A watchdog lowers the render scale when frames run long and switches the light off for the session on a third strike.

**Boundaries** (canvas board "Where luminous turns tacky"): no ring, no bright core on a darker ground, no outline you can trace, no rhythm, no banding. The form panel stays opaque; the light shows around it like a sheet of paper on a lit table.

## 7a. Why the quiet look (2026-11-03)

Dark looked heavy because its panels were inverted: they sat darker than the field, so every card read as a hole cut into the page. The quiet look steps the other way in both schemes, panel lighter than field, so a card is a sheet resting on the ground and the eye is not pulled down into it. The dark field first went still (no lobes, no drift of lightness), on the argument that a large dark area with moving colour reads as a screen, not as night. Light keeps a quarter of the lobes, enough to avoid a flat sheet. It is the default; `tayyari.look = 'today'` keeps the earlier look reachable.

**Dark, second pass (2026-11-07, canvas "Leafout Dark Mode Explorations").** Four days of use said the still dark read faint and dull, and the token table explains why rather than taste: the field at C .012 is a grey, not a dye; every non-text tone sat in a band of .105 OKLab lightness (well .145 to chip .25), so a panel was one step of .045 above the field; hairlines at L .33 with no chroma read as smudge; and the accents were pastel by necessity, because at L .84 the sRGB gamut caps chroma near .08 at the blue end, so "more colour" was not available where they sat. The fix keeps every rule (one hue, fixed L and C per role, no shadows, panels lighter than the field) and moves three things at half strength each. Range: field .145, panel .205 (ΔL .06), lines .31 / .48, ink .96. Dye: the field takes C .022, the gamut ceiling at its lightness, and three lobes return at about a quarter of the first dark's strength (.175–.185 / C .026–.028): too faint to read as a lit screen, enough that the hue drift is there again, which is the Eno test from §1. Ink and accent: the accents drop to L .78 / .72 with C .10 / .108, the lightness where a real blue fits inside sRGB; links stay ≥ 9:1 and the radar stroke ≥ 8:1 on the field. The alternatives (range alone, dye alone, ink alone) are kept on the canvas as directions A to C for comparison. The lobes-equal-field assertion in `palette-check` went with the still field; the lobes are now checked like any lobe, under every text role, at all 360 hues.

## 7b. The audit pass (2026-11-08)

A checklist audit of the three surfaces (the app, its static pages, and the landing site) found the system intact where it is enforced and drifting where it is not. Nothing enforced had slipped: no colour literal outside tokens.css, no shadow, no blur, no alert box, no filler word, no dead link. What had drifted was everything a script did not check. About 240 literal font sizes (13, 14.5, 13.5 px) sat beside the eight-step scale; 801 literal spacing values beside 672 token uses; the serif, meant for one line per screen, had become the default heading face (eight lines on the Home screen); four icon sets at three stroke weights; no pressed state anywhere; hover rules that stick on touch; blank screens while lazy chunks load; one disabled-button look per screen; a 36 px control floor on desktop that only rose to 44 px on phones. The landing site had the same drift plus a copy of the token file that predated the dark-mode second pass, a skip link that never became visible, a role-shape carousel on a six-second timer (an onset, which §3 forbids), and three calls to action in its first screen.

The fix kept the system and tightened it. Sizes, spacing and radii were snapped to the tokens (nearest step, ties down; 1–2 px optical nudges and the two phone floors kept). Headings below the one serif line became a sans `.title`. Pressed feedback, hover gating, a single disabled look, frame-shaped skeletons, skip links, 44 px targets on coarse pointers and one icon stroke were added once, in the primitives. The Weekly was written into §9 as a second newspaper skin rather than dragged into §2. Where the checklist and the system disagreed (pure-black fields, text gradients, noise, shadows, a third-party icon set, removing the italic line) the system won; those are the product's thesis, not habits.

## 8. Sources

Colour and calm: Valdez & Mehrabian 1994 (PubMed 7996122); Wilms & Oberfeld 2018 (PubMed 28612080); Elliot 2015 (PMC4383146); Palmer & Schloss 2010 (PMC2889342); Schloss & Palmer 2011 (PMC3037488); Ottosson, "A perceptual color space for image processing"; Evil Martians, "OKLCH in CSS"; CSS-Tricks, "The gray dead zone of gradients"; Keith J. Grant on neutral gradient endpoints.
Perception of slow change: Simons, Franconeri & Reimer 2000; Macquarie, "smooth isoluminant color changes"; Abrams & Christ 2003 (PubMed 12930472); Sekulovski, CIC 15; CIC 19 on hue vs chroma sensitivity; PMC7774110 on 10 s light transitions; CSS Color 4 gamut mapping JND; Turrell cycle lengths (Atlas Obscura; Artlink; Rice News); Eno, *Ambient 1*.
Charts: Scott Logic, "A critique of radar charts"; Observable, "Avoid radar charts"; Datawrapper radar notes; Nightingale, "The stellar chart"; Fuchs et al. 2014; StatsBomb radars; Few, bullet graph spec; W3C WAI complex images; WAI-ARIA APG keyboard interface.
Restraint: Linear design refresh posts; Stripe, "Designing accessible color systems"; Material color utilities; iOS colour cheat sheet; Radix scale; NN/g animation duration; Apple HIG Motion and Accessibility; WCAG 2.2.2, 2.3.3, 1.4.3, 1.4.11, 2.4.7.
Browser capabilities: MDN BCD for oklch, @property, color-mix, relative colour; Chrome developers on hardware-accelerated animations; web.dev on compositor-only properties; esbuild CSS docs (colour lowering); Vite 6 build options.
Cursor light: Alex Harri, "A flowing WebGL gradient, deconstructed" (noise-mapped colour); frost.kiwi, "How to (and how not to) fix color banding" (Jimenez interleaved gradient noise, dither before quantisation); Ashima Arts / Ian McEwan, 2D simplex noise (MIT); Ottosson, OKLab matrices; Stripe's WebGL gradient mesh; Apple visionOS glass (adaptive, diffuse light); MDN `getCoalescedEvents`; caniuse `mix-blend-mode: plus-lighter`; Mozilla bug 925025 and Chromium 726143 (CSS blur cost).
