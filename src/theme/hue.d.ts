/**
 * Types for ./hue.js — the plain-JS drift maths shared between the app module
 * graph and the inline boot script. Keep in step with the JSDoc in hue.js.
 */

/** Milliseconds per full hue circle: 42 minutes. */
export declare const PERIOD_MS: number;

/** Milliseconds per 1° step. */
export declare const STEP_MS: number;

/** The hue variable a chroma number is drawn at. */
export type ChromaHue = 'h' | 'h2' | 'h3' | 'h4';

/** How one chroma number is made: lightness it is computed for, its cap, the hue it follows. */
export interface ChromaSpec {
  /** The OKLCH lightness tokens.css draws the role at (palette-check asserts they agree). */
  L: number;
  /** The most chroma the role may carry. */
  cap: number;
  /** Which hue the role is drawn at. */
  hue: ChromaHue;
  /** Light tiles: the cap eases to half within 30° of hue 98, clear of the post-it yellow. */
  paper?: boolean;
}

/**
 * The neutral stage's chroma numbers, one per role and scheme. The key is the custom property written on the root
 * without its leading dashes (-d dark, -l light), and each value is min(cap, 0.94 x the most chroma that hue holds
 * in sRGB at lightness L). tokens.css reads them as `calc(var(--c-ui-d) * var(--k))`, so Off collapses them.
 *
 * - `--c-ui-{d,l}`: accent-ui and its fills (--fill, --fill-2, --fill-strong, --rule), at --h.
 * - `--c-accent-{d,l}`: --accent, at --h.   `--c-accent2-{d,l}`: --accent-2, at --h4.
 * - `--c-strong-{d,l}`: --accent-strong, at --h.   `--c-sel-{d,l}`: --selection, at --h.
 * - `--c-glow-{d,l}`: --stage-light, the soft radial light behind the skill graph, at --h.
 * - `--c-tile1..4-{d,l}`: --tile-1..4, at --h, --h2, --h3, --h4 (light tiles ease down near hue 98).
 */
export declare const CHROMA_SPEC: {
  readonly 'c-ui-d': ChromaSpec;
  readonly 'c-ui-l': ChromaSpec;
  readonly 'c-accent-d': ChromaSpec;
  readonly 'c-accent-l': ChromaSpec;
  readonly 'c-accent2-d': ChromaSpec;
  readonly 'c-accent2-l': ChromaSpec;
  readonly 'c-strong-d': ChromaSpec;
  readonly 'c-strong-l': ChromaSpec;
  readonly 'c-sel-d': ChromaSpec;
  readonly 'c-sel-l': ChromaSpec;
  readonly 'c-glow-d': ChromaSpec;
  readonly 'c-glow-l': ChromaSpec;
  readonly 'c-tile1-d': ChromaSpec;
  readonly 'c-tile2-d': ChromaSpec;
  readonly 'c-tile3-d': ChromaSpec;
  readonly 'c-tile4-d': ChromaSpec;
  readonly 'c-tile1-l': ChromaSpec;
  readonly 'c-tile2-l': ChromaSpec;
  readonly 'c-tile3-l': ChromaSpec;
  readonly 'c-tile4-l': ChromaSpec;
};

/** The custom-property values that drive every colour token: the hues, the two multipliers and the chroma numbers. */
export interface HueValues {
  /** Base hue, integer degrees 0..359. */
  h: number;
  /** Side hue: h + 35°, breathing ±10° over 13 minutes (tile 2). */
  h2: number;
  /** Side hue: h − 45°, breathing ±10° over 11 minutes (tile 3). */
  h3: number;
  /** Fourth hue: h + 70°, fixed (tile 4, --accent-2). */
  h4: number;
  /** Warm-sector chroma easing, 0.75 at hue 85 back to 1 fifty degrees away. */
  kw: number;
  /** Lobe-chroma breath, 0.85..1 over 17 minutes. */
  kb: number;
  /** Chroma numbers, rounded to 3 decimals; see CHROMA_SPEC. Computed from the hue each role has at this step. */
  'c-ui-d': number;
  'c-ui-l': number;
  'c-accent-d': number;
  'c-accent-l': number;
  'c-accent2-d': number;
  'c-accent2-l': number;
  'c-strong-d': number;
  'c-strong-l': number;
  'c-sel-d': number;
  'c-sel-l': number;
  'c-glow-d': number;
  'c-glow-l': number;
  'c-tile1-d': number;
  'c-tile2-d': number;
  'c-tile3-d': number;
  'c-tile4-d': number;
  'c-tile1-l': number;
  'c-tile2-l': number;
  'c-tile3-l': number;
  'c-tile4-l': number;
}

/** The hue values at wall-clock time `now` for a browser with per-install offset `h0`. */
export declare function hueAt(now: number, h0: number): HueValues;

/**
 * Writes every custom property of HueValues (--h … --kb and the --c-* numbers, both schemes) on `el.style`,
 * skipping unchanged values. Returns true when at least one property was written.
 */
export declare function applyHue(el: HTMLElement, v: HueValues): boolean;
