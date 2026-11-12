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
 *
 * The neutral stage (2026-11-12): the stage never follows the hue; accents, fills, tiles and the light behind the
 * skill graph do, and their chroma is computed here, once, for both schemes: min(cap, 0.94 x the most chroma that
 * hue holds inside sRGB at that lightness). tokens.css multiplies each number by --k, so Off still collapses to grey.
 * scripts/palette-check.ts imports CHROMA_SPEC and hueAt from this file; it does not re-implement them.
 */

/** Milliseconds per full hue circle: 42 minutes. */
export const PERIOD_MS = 2520000;

/** Milliseconds per 1° step. */
export const STEP_MS = 7000;

/**
 * The chroma numbers, one per role and scheme. Key = custom property name without the leading dashes
 * (-d dark, -l light). L is the lightness tokens.css uses for that role (palette-check asserts they agree),
 * cap the most chroma the role may carry, hue which of h / h2 / h3 / h4 it is drawn at. `paper` (light tiles):
 * the cap eases down to half within 30 degrees of hue 98, so no tile competes with the post-it yellow.
 */
export const CHROMA_SPEC = {
  'c-ui-d': { L: 0.74, cap: 0.17, hue: 'h' },
  'c-ui-l': { L: 0.52, cap: 0.17, hue: 'h' },
  'c-accent-d': { L: 0.82, cap: 0.135, hue: 'h' },
  'c-accent-l': { L: 0.46, cap: 0.14, hue: 'h' },
  'c-accent2-d': { L: 0.82, cap: 0.135, hue: 'h4' },
  'c-accent2-l': { L: 0.46, cap: 0.14, hue: 'h4' },
  'c-strong-d': { L: 0.9, cap: 0.07, hue: 'h' },
  'c-strong-l': { L: 0.38, cap: 0.09, hue: 'h' },
  'c-sel-d': { L: 0.36, cap: 0.05, hue: 'h' },
  'c-sel-l': { L: 0.88, cap: 0.05, hue: 'h' },
  'c-glow-d': { L: 0.5, cap: 0.15, hue: 'h' },
  'c-glow-l': { L: 0.86, cap: 0.09, hue: 'h' },
  'c-tile1-d': { L: 0.34, cap: 0.095, hue: 'h' },
  'c-tile2-d': { L: 0.35, cap: 0.095, hue: 'h2' },
  'c-tile3-d': { L: 0.3, cap: 0.09, hue: 'h3' },
  'c-tile4-d': { L: 0.34, cap: 0.095, hue: 'h4' },
  'c-tile1-l': { L: 0.87, cap: 0.085, hue: 'h', paper: true },
  'c-tile2-l': { L: 0.89, cap: 0.085, hue: 'h2', paper: true },
  'c-tile3-l': { L: 0.885, cap: 0.08, hue: 'h3', paper: true },
  'c-tile4-l': { L: 0.9, cap: 0.08, hue: 'h4', paper: true },
};

/** The custom properties written on the root element, in write order. */
const HUE_PROPS = ['--h', '--h2', '--h3', '--h4', '--kw', '--kb'].concat(Object.keys(CHROMA_SPEC).map((k) => '--' + k));

/** The warm-paper hue the light tiles keep clear of, and the width of that keep-clear zone, in degrees. */
const PAPER_HUE = 98;
const PAPER_REACH = 30;

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
 * OKLCH to linear sRGB (Ottosson).
 * @param {number} L
 * @param {number} C
 * @param {number} hDeg
 * @returns {number[]}
 */
function oklchLinear(L, C, hDeg) {
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l = Math.pow(L + 0.3963377774 * a + 0.2158037573 * b, 3);
  const m = Math.pow(L - 0.1055613458 * a - 0.0638541728 * b, 3);
  const s = Math.pow(L - 0.0894841775 * a - 1.291485548 * b, 3);
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

/** Memo of maxChroma by lightness and integer hue: 360 hues x a handful of lightnesses. */
const maxChromaMemo = {};

/**
 * The largest chroma inside sRGB at lightness L and hue (16 bisection steps over 0..0.4).
 * @param {number} L
 * @param {number} hue
 * @returns {number}
 */
function maxChroma(L, hue) {
  const key = L + ':' + hue;
  if (maxChromaMemo[key] !== undefined) return maxChromaMemo[key];
  let lo = 0;
  let hi = 0.4;
  for (let i = 0; i < 16; i++) {
    const mid = (lo + hi) / 2;
    const rgb = oklchLinear(L, mid, hue);
    if (rgb[0] >= -0.0005 && rgb[0] <= 1.0005 && rgb[1] >= -0.0005 && rgb[1] <= 1.0005 && rgb[2] >= -0.0005 && rgb[2] <= 1.0005) lo = mid;
    else hi = mid;
  }
  maxChromaMemo[key] = lo;
  return lo;
}

/**
 * @typedef {object} HueValues
 * @property {number} h  Base hue, integer degrees 0..359.
 * @property {number} h2 Side-lobe hue: h + 35°, breathing ±10° over 13 minutes.
 * @property {number} h3 Side-lobe hue: h − 45°, breathing ±10° over 11 minutes.
 * @property {number} h4 Fourth lobe: h + 70°, fixed, so the field fans across 115° of the wheel.
 * @property {number} kw Warm-sector chroma easing, 0.75 at hue 85 back to 1 fifty degrees away.
 * @property {number} kb Lobe-chroma breath, 0.85..1 over 17 minutes.
 * Plus one number per key of CHROMA_SPEC (for example v['c-ui-d']): that role's chroma at this step, 0..cap.
 */

/**
 * The custom-property values at wall-clock time `now` for a browser whose
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
  const out = { h, h2, h3, h4, kw, kb };
  const hues = { h, h2, h3, h4 };
  for (const key of Object.keys(CHROMA_SPEC)) {
    const spec = CHROMA_SPEC[key];
    // each number comes from the hue its role is actually drawn at this step (h2 and h3 breathe)
    const hue = hues[spec.hue];
    const cap = spec.paper ? spec.cap * (1 - 0.5 * Math.max(0, 1 - circularDistance(hue, PAPER_HUE) / PAPER_REACH)) : spec.cap;
    out[key] = round3(Math.min(cap, 0.94 * maxChroma(spec.L, hue)));
  }
  return out;
}

/**
 * Writes --h, --h2, --h3, --h4, --kw, --kb and every chroma property (--c-*, both schemes) on `el.style`,
 * touching only the values that differ from the last write (or, on the first call, from what is already
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
  const next = {};
  for (const name of HUE_PROPS) next[name] = String(v[name.slice(2)]);
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
