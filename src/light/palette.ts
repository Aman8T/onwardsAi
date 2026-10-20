/*
 * The cursor light's numbers, in one place. The shader, the engine and scripts/palette-check.ts all read
 * them, so the pixels on screen and the contrast proof cannot disagree. Plain numbers only: no DOM, no
 * colour syntax (tokens.css stays the only file with colour literals).
 *
 * The look is a "viscous wake" (design canvas, approved prototype): dye, not light. The head sits on the
 * pointer; a chain of springy followers trails it and fades into the field. Chroma carries the colour, not
 * brightness: the dye is a little darker than the light field and a little lighter than the dark one, so
 * nothing glows and there is no bright core anywhere.
 */

export const LIGHT = {
  wake: {
    /** Followers behind the head (the shader draws N + 1 blobs). */
    N: 10,
    /** Spring stiffness, s⁻²; follower 0 chases the head, follower i chases follower i−1. */
    K: 1400,
    /** Damping ratio (under-damped: a small overshoot when the pointer stops). */
    zeta: 0.72,
    /** The longest integration substep, seconds, so 60 Hz and 120 Hz screens behave the same. */
    substep: 0.004,
    /** Head radius in CSS px: a fraction of the viewport's short side, clamped. Follower i has R·(1 − taper·i/N). */
    radius: { fraction: 0.16, min: 90, max: 140, taper: 0.6 },
    /** stretch = min(speed / fullSpeed, 1) · max; blobs also lengthen by `segment` × their segment's length. */
    stretch: { fullSpeed: 1600, max: 0.9, segment: 0.6 },
    /** Where a blob's centre sits along its segment toward the tail (0.3 = 30 % back). */
    centreBack: 0.3,
    /** Dye tones, OKLCH, head to tail. Light scheme sits just under the field (.97), dark just over it (.16). */
    tone: {
      light: { head: { L: 0.81, C: 0.11 }, tail: { L: 0.86, C: 0.08 } },
      dark: { head: { L: 0.37, C: 0.085 }, tail: { L: 0.33, C: 0.07 } },
    },
    /** Peak opacity at the head; it falls along the chain as (1 − fall·u)^power, u = i/N, followers × follower. */
    alpha: { light: 0.8, dark: 0.78, fall: 0.85, power: 1.4, follower: 0.55 },
    /** Hue at the head is the field's + `head`; the tail is `tail` degrees round from the head. */
    hue: { head: 24, tail: -60 },
    /**
     * A small wander around the head hue: two sines whose phase advances with time (seconds) and with the
     * distance the head has travelled (px). Bounded by the sum of the amplitudes (MAX_WANDER).
     */
    wander: [
      { amp: 8, period: 97, travel: 2200, phase: 0 },
      { amp: 4, period: 41, travel: 900, phase: 1 },
    ],
  },
  /** Backing store: this fraction of the viewport in CSS px, never more texels than the cap. */
  render: { scale: 0.5, maxWidth: 960, maxHeight: 600 },
  /** Exponential time constants, seconds. */
  tau: {
    /** Smoothing of the head's speed and of its direction of travel. */
    speed: 0.06,
    direction: 0.1,
    /** Fade-in on wake (about 300 ms to settle). */
    wake: 0.1,
    /** Dimming when still (about 4 s). */
    dim: 1.3,
    /** Fade-out when the pointer leaves the window (about 600 ms). */
    leave: 0.2,
    /** Fade-out into sleep after a long stillness (about 6 s). */
    sleep: 2,
  },
  /** Direction updates only above this speed, px/s. */
  directionMinSpeed: 30,
  /**
   * The wake is still when the pointer has not moved for `after` ms, the head is slower than `speed` px/s and the
   * followers' summed motion and offset is under `energy` per follower: the loop stops and the last frame stays.
   */
  settle: { after: 700, speed: 4, energy: 2 },
  /** The idle schedule in seconds: dim to `dimTo` after `dimAfter`, sleep after `sleepAfter`. */
  idle: { dimAfter: 10, dimTo: 0.55, sleepAfter: 45 },
  /** The watchdog: a frame longer than `long` × the period counts; `share` of a `window`-second span is a strike. */
  watchdog: { long: 1.5, window: 2, share: 0.2, scales: [0.35, 0.25] },
} as const;

/** The largest hue offset the wander can reach, in degrees. */
export const MAX_WANDER = LIGHT.wake.wander.reduce((sum, w) => sum + w.amp, 0);

/** Shortest angular distance between two hues, in degrees (0..180). */
export function circularDistance(a: number, b: number): number {
  const d = Math.abs((((a - b) % 360) + 360) % 360);
  return d > 180 ? 360 - d : d;
}

/** The warm-sector chroma easing, the same curve as --kw in src/theme/hue.js: 0.75 at hue 85, back to 1 fifty degrees away. */
export function warmEasing(hue: number): number {
  return 1 - 0.25 * Math.max(0, 1 - circularDistance(hue, 85) / 50);
}

/** The head's wander from `field + hue.head` at time `t` (seconds) after `travel` px of movement. */
export function wanderOffset(t: number, travel: number): number {
  let off = 0;
  for (const w of LIGHT.wake.wander) off += w.amp * Math.sin((2 * Math.PI * t) / w.period + travel / w.travel + w.phase);
  return off;
}

/** The head's radius in CSS px for a viewport. */
export function wakeRadius(width: number, height: number): number {
  const { fraction, min, max } = LIGHT.wake.radius;
  return Math.min(max, Math.max(min, Math.min(width, height) * fraction));
}
