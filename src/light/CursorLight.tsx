/**
 * The cursor light, the "viscous wake": a head locked to the pointer and a chain of springy, fading dye behind
 * it. This component decides whether the light exists at all, mounts the canvas into <body> — between the
 * ambient field (z -1) and the view (z 1), outside the view's own transform — and hands it to the engine.
 * It is mounted once, in App, for every screen. Everything visual is in the engine and the shader; nothing
 * here renders per frame.
 *
 * Not mounted at all for touch or coarse pointers, under reduced motion or slow updates, under forced
 * colours or more contrast, or when the Atmosphere is Off: the page is then exactly as it was. The dye has a
 * tone per scheme (`LIGHT.wake.tone` in palette.ts), and a switch of scheme retunes it within a second.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMediaQuery } from '../lib/ui';
import { useAtmoMode } from '../theme/ambient';
import type { LightEngine, LightStats } from './engine';
import { LightStatsOverlay } from './Stats';
import '../styles/light.css';

/** Dev switches: `?light=off` (never mount), `?light=reduce` (as if reduced motion), `?light=stats` (overlay + harness). */
function devSwitch(): string | null {
  if (!import.meta.env.DEV) return null;
  return new URLSearchParams(window.location.search).get('light');
}

/** The opt-in look on the root (`html[data-look='quiet']`); anything else is today's. */
export type Look = 'today' | 'quiet';

/** The effective colour scheme, decided one way for the cursor light and the spotlight: the pinned one, else the device's. */
export function readDark(): boolean {
  const pinned = document.documentElement.dataset.scheme;
  return pinned ? pinned === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function readLook(): Look {
  return document.documentElement.dataset.look === 'quiet' ? 'quiet' : 'today';
}

/** The field's hue, warm easing, scheme and look, from the root (written by boot.js and ambient.ts). */
function readField(): { h: number; kw: number; dark: boolean; look: Look } {
  const root = document.documentElement;
  const h = Number.parseFloat(root.style.getPropertyValue('--h'));
  const kw = Number.parseFloat(root.style.getPropertyValue('--kw'));
  return { h: Number.isFinite(h) ? h : 250, kw: Number.isFinite(kw) ? kw : 1, dark: readDark(), look: readLook() };
}

/**
 * Whether the pointer layers (the wake and the spotlight) may exist at all: fine pointer, no reduced motion,
 * slow updates, forced colours or more contrast, and the Atmosphere not Off (nor the dev `?light=off|reduce`).
 */
export function usePointerLayersAllowed(): boolean {
  const fine = useMediaQuery('(hover: hover) and (pointer: fine)');
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)');
  const slow = useMediaQuery('(update: slow)');
  const forced = useMediaQuery('(forced-colors: active)');
  const moreContrast = useMediaQuery('(prefers-contrast: more)');
  const [, atmo] = useAtmoMode();
  const dev = devSwitch();
  return fine && !reduced && !slow && !forced && !moreContrast && atmo !== 'off' && dev !== 'off' && dev !== 'reduce';
}

export function CursorLight() {
  const enabled = usePointerLayersAllowed();
  if (!enabled) return null;
  return createPortal(<LightCanvas stats={devSwitch() === 'stats'} />, document.body);
}

/** Runs `fn` after the first paint, when the browser is idle (a short timeout where requestIdleCallback is missing). */
function afterFirstPaint(fn: () => void): () => void {
  if (typeof window.requestIdleCallback === 'function') {
    const id = window.requestIdleCallback(fn, { timeout: 1500 });
    return () => window.cancelIdleCallback(id);
  }
  const id = window.setTimeout(fn, 200);
  return () => window.clearTimeout(id);
}

function LightCanvas({ stats }: { stats: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [snapshot, setSnapshot] = useState<LightStats | null>(null);

  useEffect(() => {
    const host = ref.current;
    if (!host) return;
    // The WebGL code (engine, gl, shader) is its own chunk, fetched only now: after the first paint and idle.
    let cancelled = false;
    let engine: LightEngine | null = null;
    const cancelWait = afterFirstPaint(() => {
      void import('./engine').then(({ createLightEngine }) => {
        if (cancelled) return;
        engine = createLightEngine({ host, readField, onStats: stats ? setSnapshot : undefined });
        const e = engine;
        if (stats && e) {
          window.__light = {
            sweep: (seconds, pxPerSecond) => e.sweep(seconds, pxPerSecond),
            setScale: (scale) => e.setScale(scale),
            snapshot: () => e.snapshot(),
          };
        }
      });
    });
    return () => {
      cancelled = true;
      cancelWait();
      engine?.destroy();
      if (window.__light) delete window.__light;
    };
  }, [stats]);

  return (
    <>
      <div ref={ref} className="light-host" aria-hidden="true" />
      {stats && snapshot && <LightStatsOverlay stats={snapshot} />}
    </>
  );
}

declare global {
  interface Window {
    /** Dev harness, present with `?light=stats`: `__light.sweep(10, 1200)` drives the pointer; `__light.setScale(1)` renders at full size. */
    __light?: {
      sweep: (seconds: number, pxPerSecond: number) => void;
      setScale: (scale: number) => void;
      snapshot: () => LightStats;
    };
  }
}
