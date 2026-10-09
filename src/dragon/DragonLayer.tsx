/**
 * The dragon's layer on Home: decides whether the creature exists at all (desktop and tablet, full
 * motion, no forced colours, awake in its menu), mounts a transparent host above the page, and loads the
 * 3D chunk (three.js, three.quarks, the engine) only after Home has painted and the thread is idle.
 * Everything per frame lives in the engine; nothing here renders per frame.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMediaQuery } from '../lib/ui';
import { useApp } from '../store';
import type { DragonHandle, DragonStats } from './engine';
import { useDragon } from './state';
import { DragonStatsOverlay } from './Stats';
import '../styles/dragon.css';

export interface DevSwitches {
  off: boolean;
  stats: boolean;
  egg: boolean;
  hunt: boolean;
  /** Drive the loop from a timer: for browser panes that throttle requestAnimationFrame. */
  timer: boolean;
  age?: number;
}

/** `?dragon=stats,age:600,hunt` in dev builds. */
export function devSwitches(): DevSwitches {
  const out: DevSwitches = { off: false, stats: false, egg: false, hunt: false, timer: false };
  if (!import.meta.env.DEV) return out;
  const raw = new URLSearchParams(window.location.search).get('dragon');
  if (!raw) return out;
  for (const part of raw.split(',')) {
    const [k, v] = part.split(':');
    if (k === 'off') out.off = true;
    else if (k === 'stats') out.stats = true;
    else if (k === 'egg') out.egg = true;
    else if (k === 'hunt') out.hunt = true;
    else if (k === 'timer') out.timer = true;
    else if (k === 'age') out.age = Number(v);
  }
  return out;
}

export function DragonLayer() {
  const wide = useMediaQuery('(min-width: 760px)');
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)');
  const forced = useMediaQuery('(forced-colors: active)');
  const moreContrast = useMediaQuery('(prefers-contrast: more)');
  const snap = useDragon();
  const dev = devSwitches();
  const enabled = wide && !reduced && !forced && !moreContrast && !dev.off && snap.mode === 'awake';
  if (!enabled) return null;
  return createPortal(<DragonHost dev={dev} />, document.body);
}

function DragonHost({ dev }: { dev: DevSwitches }) {
  const ref = useRef<HTMLDivElement>(null);
  const handleRef = useRef<DragonHandle | null>(null);
  const [stats, setStats] = useState<DragonStats | null>(null);
  // Home is leaving when the view changes; AnimatePresence keeps it mounted through the crossfade.
  const leaving = useApp((s) => s.view) !== 'onboarding';

  useEffect(() => {
    const host = ref.current;
    if (!host) return;
    let cancelled = false;
    const start = () => {
      void import('./engine').then(async (m) => {
        if (cancelled) return;
        const handle = await m.startDragon({
          host,
          startAge: dev.egg ? 0 : dev.age,
          huntNow: dev.hunt,
          timerLoop: dev.timer,
          onStats: dev.stats ? setStats : undefined,
        });
        if (cancelled) handle?.destroy();
        else handleRef.current = handle;
      });
    };
    // after the first paint, when the main thread is free (Home's own entrance comes first)
    const hasIdle = typeof window.requestIdleCallback === 'function';
    const idle = hasIdle ? window.requestIdleCallback(start, { timeout: 2500 }) : window.setTimeout(start, 700);
    return () => {
      cancelled = true;
      if (hasIdle) window.cancelIdleCallback(idle);
      else window.clearTimeout(idle);
      handleRef.current?.destroy();
      handleRef.current = null;
    };
    // the dev switches are read once per mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (leaving) handleRef.current?.retire();
  }, [leaving]);

  return (
    <>
      <div ref={ref} className={leaving ? 'dragon-layer is-leaving' : 'dragon-layer'} aria-hidden="true" />
      {dev.stats && stats && <DragonStatsOverlay stats={stats} />}
    </>
  );
}
