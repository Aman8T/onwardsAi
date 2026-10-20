/*
 * Hue drift maths — the single source of truth for the living gradient.
 *
 * Plain ES module with no imports and no DOM assumptions beyond `el.style`.
 * The same file is inlined into <head> by the calmBoot() plugin in vite.config.ts
 * (with the `export` keywords stripped) so the first paint already carries the
 * right hue; keep it dependency-free and free of module-only syntax.
 *
 * Everything is a pure function of wall-clock time: nothing "starts", reloads
 * never jump, and two tabs always agree.
 */

/** Milliseconds per full hue circle: 42 minutes. */
export const PERIOD_MS = 2520000;

/** Milliseconds per 1° step. */
export const STEP_MS = 7000;

/** The custom properties written on the root element, in write order. */
const HUE_PROPS = ['--h', '--h2', '--h3', '--h4', '--kw', '--kb'];

/** Last values written per element, so unchanged properties are never rewritten. */
const lastApplied = new WeakMap();

/**
 * Shortest angular distance between two hues, in degrees (0..180).
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
function circularDistance(a, b) {
  const d = Math.abs((((a - b) % 360) + 360) % 360);
  return d > 180 ? 360 - d : d;
}

/**
 * Rounds to three decimals so the CSS value only changes when it matters.
 * @param {number} x
 * @returns {number}
 */
function round3(x) {
  return Math.round(x * 1000) / 1000;
}

/**
 * @typedef {object} HueValues
 * @property {number} h  Base hue, integer degrees 0..359.
 * @property {number} h2 Side-lobe hue: h + 35°, breathing ±10° over 13 minutes.
 * @property {number} h3 Side-lobe hue: h − 45°, breathing ±10° over 11 minutes.
 * @property {number} h4 Fourth lobe: h + 70°, fixed, so the field fans across 115° of the wheel.
 * @property {number} kw Warm-sector chroma easing, 0.75 at hue 85 back to 1 fifty degrees away.
 * @property {number} kb Lobe-chroma breath, 0.85..1 over 17 minutes.
 */

/**
 * The five custom-property values at wall-clock time `now` for a browser whose
 * per-install random offset is `h0` (degrees).
 * @param {number} now Date.now()
 * @param {number} h0  Hue offset in degrees; any integer is accepted and normalised.
 * @returns {HueValues}
 */
export function hueAt(now, h0) {
  const h = ((Math.floor(h0 + 360 * ((now % PERIOD_MS) / PERIOD_MS)) % 360) + 360) % 360;
  const h2 = Math.round(h + 35 + 10 * Math.sin((2 * Math.PI * now) / 780000)) % 360;
  const h3 = Math.round(h - 45 - 10 * Math.sin((2 * Math.PI * now) / 660000) + 720) % 360;
  const h4 = (h + 70) % 360;
  const kw = round3(1 - 0.25 * Math.max(0, 1 - circularDistance(h, 85) / 50));
  const kb = round3(0.925 + 0.075 * Math.sin((2 * Math.PI * now) / 1020000));
  return { h, h2, h3, h4, kw, kb };
}

/**
 * Writes --h, --h2, --h3, --h4, --kw and --kb on `el.style`, touching only the values
 * that differ from the last write (or, on the first call, from what is already
 * on the element — the inline boot copy of this function may have run first).
 * @param {HTMLElement} el Usually document.documentElement.
 * @param {HueValues} v
 * @returns {boolean} True when at least one property was written.
 */
export function applyHue(el, v) {
  let prev = lastApplied.get(el);
  if (!prev) {
    prev = {};
    for (const name of HUE_PROPS) prev[name] = el.style.getPropertyValue(name);
    lastApplied.set(el, prev);
  }
  const next = {
    '--h': String(v.h),
    '--h2': String(v.h2),
    '--h3': String(v.h3),
    '--h4': String(v.h4),
    '--kw': String(v.kw),
    '--kb': String(v.kb),
  };
  let changed = false;
  for (const name of HUE_PROPS) {
    const value = next[name];
    if (prev[name] === value) continue;
    el.style.setProperty(name, value);
    prev[name] = value;
    changed = true;
  }
  return changed;
}
