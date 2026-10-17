/**
 * The cursor light: a soft field of colour that follows the pointer on Home (design canvas "Cursor Light
 * Field", board Main). This component decides whether the light exists at all, mounts the canvas into
 * <body> — between the ambient field (z −1) and the view (z 1), outside the view's own transform — and
 * hands it to the engine. Everything visual is in the engine and the shader; nothing here renders per frame.
 *
 * Not mounted at all for touch or coarse pointers, under reduced motion or slow updates, under forced
 * colours or more contrast, when the Atmosphere is Off, or in the dark scheme: Home is then exactly as it
 * was. In dark the light stays off by design, not by accident: its body (L .89) is lighter than the dark
 * field (L .16), which would be the bright core on a darker ground the design forbids, and no dark-scheme
 * ink role could keep its contrast floor over it. A light for the dark field needs its own design pass
 * (palette.ts and the shader are built for the light band only; palette-check proves it there alone).
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMediaQuery } from '../lib/ui';
import { useApp } from '../store';
import { useAtmoMode, useScheme } from '../theme/ambient';
import { createLightEngine } from './engine';
import type { LightEngine, LightStats } from './engine';
import { LightStatsOverlay } from './Stats';
import '../styles/light.css';

/** Dev switches: `?light=off` (never mount), `?light=reduce` (as if reduced motion), `?light=stats` (overlay + harness). */
function devSwitch(): string | null {
  if (!import.meta.env.DEV) return null;
  return new URLSearchParams(window.location.search).get('light');
}

/** The field's hue and warm easing, from the root's inline style (written by boot.js and ambient.ts). */
function readField(): { h: number; kw: number } {
  const style = document.documentElement.style;
  const h = Number.parseFloat(style.getPropertyValue('--h'));
  const kw = Number.parseFloat(style.getPropertyValue('--kw'));
  return { h: Number.isFinite(h) ? h : 250, kw: Number.isFinite(kw) ? kw : 1 };
}

export function CursorLight() {
  const fine = useMediaQuery('(hover: hover) and (pointer: fine)');
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)');
  const slow = useMediaQuery('(update: slow)');
  const forced = useMediaQuery('(forced-colors: active)');
  const moreContrast = useMediaQuery('(prefers-contrast: more)');
  const [, atmo] = useAtmoMode();
  const [, scheme] = useScheme();
  const dev = devSwitch();

  const enabled =
    fine && !reduced && !slow && !forced && !moreContrast && atmo !== 'off' && scheme !== 'dark' && dev !== 'off' && dev !== 'reduce';
  if (!enabled) return null;
  return createPortal(<LightCanvas stats={dev === 'stats'} />, document.body);
}

function LightCanvas({ stats }: { stats: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const engineRef = useRef<LightEngine | null>(null);
  const [snapshot, setSnapshot] = useState<LightStats | null>(null);
  // Home is leaving when the view changes; AnimatePresence keeps it mounted through the crossfade.
  const leaving = useApp((s) => s.view) !== 'onboarding';

  useEffect(() => {
    const host = ref.current;
    if (!host) return;
    const engine = createLightEngine({ host, readField, onStats: stats ? setSnapshot : undefined });
    engineRef.current = engine;
    if (stats && engine) {
      window.__light = {
        sweep: (seconds, pxPerSecond) => engine.sweep(seconds, pxPerSecond),
        setScale: (scale) => engine.setScale(scale),
        snapshot: () => engine.snapshot(),
      };
    }
    return () => {
      engine?.destroy();
      engineRef.current = null;
      if (window.__light) delete window.__light;
    };
  }, [stats]);

  useEffect(() => {
    if (leaving) engineRef.current?.retire();
  }, [leaving]);

  return (
    <>
      <div ref={ref} className={leaving ? 'light-host is-leaving' : 'light-host'} aria-hidden="true" />
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
