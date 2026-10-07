/**
 * Types for ./hue.js — the plain-JS drift maths shared between the app module
 * graph and the inline boot script. Keep in step with the JSDoc in hue.js.
 */

/** Milliseconds per full hue circle: 42 minutes. */
export declare const PERIOD_MS: number;

/** Milliseconds per 1° step. */
export declare const STEP_MS: number;

/** The five custom-property values that drive every colour token. */
export interface HueValues {
  /** Base hue, integer degrees 0..359. */
  h: number;
  /** Side-lobe hue: h + 35°, breathing ±10° over 13 minutes. */
  h2: number;
  /** Side-lobe hue: h − 45°, breathing ±10° over 11 minutes. */
  h3: number;
  /** Warm-sector chroma easing, 0.75 at hue 85 back to 1 fifty degrees away. */
  kw: number;
  /** Lobe-chroma breath, 0.85..1 over 17 minutes. */
  kb: number;
}

/** The hue values at wall-clock time `now` for a browser with per-install offset `h0`. */
export declare function hueAt(now: number, h0: number): HueValues;

/**
 * Writes the five custom properties on `el.style`, skipping unchanged values.
 * Returns true when at least one property was written.
 */
export declare function applyHue(el: HTMLElement, v: HueValues): boolean;
