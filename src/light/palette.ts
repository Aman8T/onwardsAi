/*
 * The cursor light's numbers, in one place. The shader, the engine and scripts/palette-check.ts all read
 * them, so the pixels on screen and the contrast proof cannot disagree. Plain numbers only: no DOM, no
 * colour syntax (tokens.css stays the only file with colour literals).
 *
 * The look is "light through paper" (design canvas, board Main): a pale heart, the colour in the halo,
 * no edge anywhere. Lightness lives between the body floor and the heart, both inside the page's own
 * band (the field is .97), so the light is felt through tone and hue rather than brightness.
 */

export const LIGHT = {
  /** OKLCH lightness of the pale heart. */
  heartL: 0.965,
  /** OKLCH lightness of the body: the floor no pixel goes below, so text-muted stays at or above 5:1. */
  bodyL: 0.89,
  /** OKLCH chroma at the heart and in the body (the ambient lobes use .044). */
  heartC: 0.016,
  bodyC: 0.048,
  /** Degrees from the heart's hue to the body's: analogous, like the side lobes. */
  haloHueOffset: 28,
  /**
   * The wander around the field's --h: two sines whose phase advances with time (seconds) and with the
   * distance the heart has travelled (px). Bounded by the sum of the amplitudes, so the light never leaves
   * the field's harmony window; as --h drifts, the light visits every hue.
   */
  wander: [
    { amp: 38, period: 97, travel: 2200, phase: 0 },
    { amp: 18, period: 41, travel: 900, phase: 1 },
  ],
  /** Radius of the body in CSS px: a fraction of the viewport's short side, clamped. */
  radius: { fraction: 0.34, min: 180, max: 320 },
  /** Backing store: this fraction of the viewport in CSS px, never more texels than the cap. */
  render: { scale: 0.5, maxWidth: 960, maxHeight: 600 },
  /** Exponential time constants, seconds. */
  tau: {
    /** The heart follows the pointer with this constant at rest… */
    heart: 0.035,
    /** …and this one at `speed.tight` and above (the shape, not the position, carries the trailing). */
    heartAtSpeed: 0.0175,
    speed: 0.08,
    direction: 0.12,
    stretchUp: 0.06,
    stretchDown: 0.16,
    /** Fade-in on wake (about 300 ms to settle). */
    wake: 0.1,
    /** Dimming when still (about 4 s). */
    dim: 1.3,
    /** Fade-out when the pointer leaves the window or Home is left (about 600 ms). */
    leave: 0.2,
    /** Fade-out into sleep after a long stillness (about 6 s). */
    sleep: 2,
  },
  /** Pointer speeds in px/s: where the heart is at its tightest, and where the stretch is full. */
  speed: { tight: 2500, fullStretch: 1800 },
  /** How much the field elongates along the direction of travel at full stretch (0.35 = 35 %). */
  maxStretch: 0.35,
  /** The idle schedule in seconds: dim to `dimTo` after `dimAfter`, sleep after `sleepAfter`. */
  idle: { dimAfter: 10, dimTo: 0.55, sleepAfter: 45 },
  /** Noise rates per second for the body and the halo (unrelated, so the shape never repeats) and their warp depth. */
  noise: { body: [0.045, 0.037], halo: [0.031, 0.026], warpBody: 0.3, warpHalo: 0.38 },
  /** Travel → noise phase, per px: moving stirs the shape. */
  travelPhase: 0.0008,
  /** The watchdog: a frame longer than `long` × the period counts; `share` of a `window`-second span is a strike. */
  watchdog: { long: 1.5, window: 2, share: 0.2, scales: [0.35, 0.25] },
} as const;

/** The largest hue offset the wander can reach, in degrees. */
export const MAX_HUE_OFFSET = LIGHT.wander.reduce((sum, w) => sum + w.amp, 0);

/** Shortest angular distance between two hues, in degrees (0..180). */
export function circularDistance(a: number, b: number): number {
  const d = Math.abs((((a - b) % 360) + 360) % 360);
  return d > 180 ? 360 - d : d;
}

/** The warm-sector chroma easing, the same curve as --kw in src/theme/hue.js: 0.75 at hue 85, back to 1 fifty degrees away. */
export function warmEasing(hue: number): number {
  return 1 - 0.25 * Math.max(0, 1 - circularDistance(hue, 85) / 50);
}

/** The light's hue offset from the field at time `t` (seconds) after `travel` px of movement. */
export function hueOffset(t: number, travel: number): number {
  let off = 0;
  for (const w of LIGHT.wander) off += w.amp * Math.sin((2 * Math.PI * t) / w.period + travel / w.travel + w.phase);
  return off;
}

/** The body radius in CSS px for a viewport. */
export function bodyRadius(width: number, height: number): number {
  const { fraction, min, max } = LIGHT.radius;
  return Math.min(max, Math.max(min, Math.min(width, height) * fraction));
}
