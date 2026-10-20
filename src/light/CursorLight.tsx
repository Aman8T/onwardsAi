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
import { createLightEngine } from './engine';
import type { LightStats } from './engine';
import { LightStatsOverlay } from './Stats';
import '../styles/light.css';

/** Dev switches: `?light=off` (never mount), `?light=reduce` (as if reduced motion), `?light=stats` (overlay + harness). */
function devSwitch(): string | null {
  if (!import.meta.env.DEV) return null;
  return new URLSearchParams(window.location.search).get('light');
}

/** The field's hue, warm easing and scheme, from the root (written by boot.js and ambient.ts). */
function readField(): { h: number; kw: number; dark: boolean } {
  const root = document.documentElement;
  const h = Number.parseFloat(root.style.getPropertyValue('--h'));
  const kw = Number.parseFloat(root.style.getPropertyValue('--kw'));
  const pinned = root.dataset.scheme;
  const dark = pinned ? pinned === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  return { h: Number.isFinite(h) ? h : 250, kw: Number.isFinite(kw) ? kw : 1, dark };
}

export function CursorLight() {
  const fine = useMediaQuery('(hover: hover) and (pointer: fine)');
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)');
  const slow = useMediaQuery('(update: slow)');
  const forced = useMediaQuery('(forced-colors: active)');
  const moreContrast = useMediaQuery('(prefers-contrast: more)');
  const [, atmo] = useAtmoMode();
  const dev = devSwitch();

  const enabled =
    fine && !reduced && !slow && !forced && !moreContrast && atmo !== 'off' && dev !== 'off' && dev !== 'reduce';
  if (!enabled) return null;
  return createPortal(<LightCanvas stats={dev === 'stats'} />, document.body);
}

function LightCanvas({ stats }: { stats: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [snapshot, setSnapshot] = useState<LightStats | null>(null);

  useEffect(() => {
    const host = ref.current;
    if (!host) return;
    const engine = createLightEngine({ host, readField, onStats: stats ? setSnapshot : undefined });
    if (stats && engine) {
      window.__light = {
        sweep: (seconds, pxPerSecond) => engine.sweep(seconds, pxPerSecond),
        setScale: (scale) => engine.setScale(scale),
        snapshot: () => engine.snapshot(),
      };
    }
    return () => {
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
