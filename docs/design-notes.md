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

**Gamut.** Browsers clip out-of-gamut oklch instead of gamut-mapping it, which bends lightness and hue. The sRGB limit at constant L is tightest around cyan (≈ 200°) up to L .75 and around blue (≈ 267°) above that: roughly C ≤ 0.17·L up to L .75, then .100 at L .80, .048 at L .90, .014 at L .97. Every token is kept ≤ 0.93 × that limit; `scripts/palette-check.ts` sweeps all 360 hues to prove it.

**Fallbacks, in order.** Hex palette for one fixed hue declared before the `@supports (color: oklch(0 0 0))` block → Still under reduced motion or `(update: slow)` → stronger ink and no lobes under `prefers-contrast: more` → ambient hidden and system colours under `forced-colors: active` → greys under Off.

## 4. Depth without effects

Reference products build hierarchy from tone, not shadow: Linear ("structure should be felt, not seen"; themes generated in LCH from three inputs), Stripe (CIELAB palette on shared lightness levels), Material 3 (tone-based surfaces at 100/98/96/94/92/90 L*), iOS (separators at ~29 % alpha). Rothko's fields hover on a ground with soft edges and low value contrast; Turrell's Ganzfelds remove edges entirely.

Here: surfaces step 2–4 OKL points (field .97, surface .99, surface-2 .955), dividers are 1 px at ~8 % ink alpha, popovers are opaque surfaces with a hairline, and the only thing thicker than 1 px is the 2 px focus ring (WCAG 2.4.7 / 2.4.13).

## 5. Radar charts

The literature is blunt about radars (Scott Logic, Observable, Datawrapper, Nightingale): area grows with the square of values, axis order changes the shape, lines imply continuity, and overlapping series occlude. The design keeps what works and removes the rest:

- 5–8 axes, fixed order per profile, never sorted by value (StatsBomb's fixed templates make shapes recognisable with use).
- At most two series: "you" as a 12 % fill with a 1.5 px stroke, the target as an unfilled hairline (Few's bullet-graph logic: benchmark thin, actual bold).
- Straight edges only; hairline rings at 25/50/75/100; labels outside the rim; numerals in tabular figures.
- Hover highlights one spoke; vertices are real buttons with roving tabindex and arrow-key navigation (WAI-ARIA APG); a visually hidden data table is always present (W3C WAI complex images).
- Animation tweens the values along their spokes, never the path string; axis-set changes crossfade (vertex counts differ).

## 6. Type, space, motion

- Geist 400/500/600 for UI, Geist Mono (tabular) for data numerals, Instrument Serif for one line per screen. Display tracking −0.02em, small labels +0.01em (Inter's dynamic-metrics curve as the reference).
- Spacing 4–96 px; measure 62ch; *ma* treated as positive space, not emptiness; Rams: "less, but better".
- UI motion 150–300 ms ease-out with no bounce (NN/g: 400 ms is the slow limit for feedback); whole-screen crossfades up to 500 ms (Apple's "smooth" spring); the one-time radar reveal 900 ms. Only the typing dots and a live-thread dot ever loop.

## 7. Sources

Colour and calm: Valdez & Mehrabian 1994 (PubMed 7996122); Wilms & Oberfeld 2018 (PubMed 28612080); Elliot 2015 (PMC4383146); Palmer & Schloss 2010 (PMC2889342); Schloss & Palmer 2011 (PMC3037488); Ottosson, "A perceptual color space for image processing"; Evil Martians, "OKLCH in CSS"; CSS-Tricks, "The gray dead zone of gradients"; Keith J. Grant on neutral gradient endpoints.
Perception of slow change: Simons, Franconeri & Reimer 2000; Macquarie, "smooth isoluminant color changes"; Abrams & Christ 2003 (PubMed 12930472); Sekulovski, CIC 15; CIC 19 on hue vs chroma sensitivity; PMC7774110 on 10 s light transitions; CSS Color 4 gamut mapping JND; Turrell cycle lengths (Atlas Obscura; Artlink; Rice News); Eno, *Ambient 1*.
Charts: Scott Logic, "A critique of radar charts"; Observable, "Avoid radar charts"; Datawrapper radar notes; Nightingale, "The stellar chart"; Fuchs et al. 2014; StatsBomb radars; Few, bullet graph spec; W3C WAI complex images; WAI-ARIA APG keyboard interface.
Restraint: Linear design refresh posts; Stripe, "Designing accessible color systems"; Material color utilities; iOS colour cheat sheet; Radix scale; NN/g animation duration; Apple HIG Motion and Accessibility; WCAG 2.2.2, 2.3.3, 1.4.3, 1.4.11, 2.4.7.
Browser capabilities: MDN BCD for oklch, @property, color-mix, relative colour; Chrome developers on hardware-accelerated animations; web.dev on compositor-only properties; esbuild CSS docs (colour lowering); Vite 6 build options.
