/*
 * Ambient — the runtime side of the living gradient.
 *
 * Owns the Drift / Still / Off choice, the ?h= override, the reduced-motion and
 * slow-update fallbacks, and the 7-second stepper that writes the hue custom
 * properties. The only DOM writes anywhere in this module are the five custom
 * properties (through applyHue) and data-atmo on <html>. No requestAnimationFrame:
 * between steps the page produces zero frames.
 */
import { useSyncExternalStore } from 'react';
import { applyHue, hueAt, PERIOD_MS, STEP_MS } from './hue';

export type AtmoMode = 'drift' | 'still' | 'off';

/** localStorage key for the per-install hue offset (integer degrees). */
export const HUE0_KEY = 'tayyari.hue0';
/** localStorage key for the chosen atmosphere mode. */
export const MODE_KEY = 'tayyari.atmo';
/** Minutes per full hue circle, for copy that explains the drift. */
export const CYCLE_MINUTES = PERIOD_MS / 60_000;

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const slowUpdate = window.matchMedia('(update: slow)');

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the session still works, the value just does not persist */
  }
}

function isMode(value: string | null): value is AtmoMode {
  return value === 'drift' || value === 'still' || value === 'off';
}

const mod360 = (x: number): number => ((x % 360) + 360) % 360;

/* ----------------------------------------------------------------------------
 * State
 * ------------------------------------------------------------------------- */

let hue0: number | null = null;

/** The per-install hue offset, created once (the boot script usually did) and kept. */
export function getHue0(): number {
  if (hue0 !== null) return hue0;
  const stored = Number.parseInt(readStorage(HUE0_KEY) ?? '', 10);
  hue0 = Number.isInteger(stored) && stored >= 0 && stored < 360 ? stored : Math.floor(Math.random() * 360);
  if (hue0 !== stored) writeStorage(HUE0_KEY, String(hue0));
  return hue0;
}

/** The ?h=<deg> override, normalised to 0..359, or null when absent or not a number. */
export function forcedHue(): number | null {
  const raw = new URLSearchParams(window.location.search).get('h');
  if (raw === null) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) ? mod360(n) : null;
}

let chosen: AtmoMode = ((): AtmoMode => {
  const stored = readStorage(MODE_KEY);
  return isMode(stored) ? stored : 'drift';
})();

/** The mode the person chose (what the Atmosphere control shows as checked). */
export function getMode(): AtmoMode {
  return chosen;
}

/** The mode in force: a pinned hue is always Still, and Drift yields to reduced motion or slow screens. */
export function effectiveMode(): AtmoMode {
  if (forcedHue() !== null) return 'still';
  if (chosen === 'drift' && (reducedMotion.matches || slowUpdate.matches)) return 'still';
  return chosen;
}

const listeners = new Set<() => void>();

/** Subscribes to mode changes (chosen or effective); returns the unsubscribe. */
export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function notify(): void {
  for (const cb of listeners) cb();
}

/* ----------------------------------------------------------------------------
 * Stepper
 * ------------------------------------------------------------------------- */

let started = false;
let timer: ReturnType<typeof setTimeout> | undefined;

/** Writes the hue values for this instant: the clock's, or the clock's rebased onto the pinned hue. */
function apply(): void {
  const now = Date.now();
  const h0 = getHue0();
  const base = hueAt(now, h0);
  const forced = forcedHue();
  // Shifting the offset by the difference keeps the clock's lobe offsets and breath
  // and recomputes the warm-sector easing for the pinned hue.
  const values = forced === null ? base : hueAt(now, h0 + forced - base.h);
  applyHue(document.documentElement, values);
}

function clearTimer(): void {
  if (timer === undefined) return;
  clearTimeout(timer);
  timer = undefined;
}

/** Aligns the next write to the 7-second grid so every tab steps at the same instant. */
function schedule(): void {
  clearTimer();
  timer = setTimeout(tick, STEP_MS - (Date.now() % STEP_MS));
}

function tick(): void {
  timer = undefined;
  apply();
  schedule();
}

/** Re-evaluates mode and visibility: refreshes from the clock and (re)starts the stepper under Drift, stops it otherwise. */
function refresh(): void {
  clearTimer();
  document.documentElement.dataset.atmo = effectiveMode();
  if (effectiveMode() !== 'drift') return;
  apply();
  if (document.visibilityState === 'visible') schedule();
}

function onVisibility(): void {
  if (document.visibilityState === 'hidden') clearTimer();
  else refresh();
}

function onMediaChange(): void {
  notify();
  refresh();
}

/** Persists and applies a mode choice, then tells subscribers. */
export function setMode(mode: AtmoMode): void {
  if (mode === chosen) return;
  chosen = mode;
  writeStorage(MODE_KEY, mode);
  document.documentElement.dataset.atmo = effectiveMode();
  notify();
  if (started) refresh();
}

function stopAmbient(): void {
  if (!started) return;
  started = false;
  clearTimer();
  document.removeEventListener('visibilitychange', onVisibility);
  reducedMotion.removeEventListener('change', onMediaChange);
  slowUpdate.removeEventListener('change', onMediaChange);
}

/**
 * Starts the ambient system once: applies the current hue, then steps every 7 s
 * while the effective mode is Drift and the tab is visible. Idempotent; returns
 * the stop function. A no-op on engines without oklch (the hex fallback is static).
 */
export function startAmbient(): () => void {
  if (started) return stopAmbient;
  if (!CSS.supports('color', 'oklch(0 0 0)')) return () => {};
  started = true;
  apply();
  refresh();
  document.addEventListener('visibilitychange', onVisibility);
  reducedMotion.addEventListener('change', onMediaChange);
  slowUpdate.addEventListener('change', onMediaChange);
  return stopAmbient;
}

/* ----------------------------------------------------------------------------
 * React
 * ------------------------------------------------------------------------- */

/** [chosen, effective, set] — the two modes are strings, so the snapshots are stable. */
export function useAtmoMode(): [chosen: AtmoMode, effective: AtmoMode, set: (mode: AtmoMode) => void] {
  const current = useSyncExternalStore(subscribe, getMode);
  const effective = useSyncExternalStore(subscribe, effectiveMode);
  return [current, effective, setMode];
}
