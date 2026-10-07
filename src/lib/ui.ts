/**
 * Shared UI helpers: the house easing, media queries, relative time, small formatters
 * and the two measuring hooks (count-up tween, element size) the views build on.
 * Replaces the per-view copies that used to live in NowGraph, NextGraph, GraphHeader,
 * ChatView and chat/helpers.
 */
import { animate, useReducedMotion } from 'motion/react';
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import type { CSSProperties, RefObject } from 'react';

/** The one easing curve, `cubic-bezier(.2,.8,.2,1)`, for motion props (CSS uses `var(--ease)`). */
export const EASE = [0.2, 0.8, 0.2, 1] as const;

/** Viewport width at and below which popovers become bottom sheets and rails collapse. */
export const NARROW_QUERY = '(max-width: 760px)';

const canMatchMedia = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function';

/** Live `matchMedia` result. Renders `false` on the server and wherever matchMedia is missing. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (!canMatchMedia()) return () => {};
      const list = window.matchMedia(query);
      list.addEventListener('change', onChange);
      return () => list.removeEventListener('change', onChange);
    },
    () => canMatchMedia() && window.matchMedia(query).matches,
    () => false,
  );
}

/** `Date.now()`, refreshed every `intervalMs`; drives relative-time labels and elapsed clocks. */
export function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** "just now", "12 min ago", "3 h ago", "yesterday", "4 days ago", then "on 4 Oct". */
export function timeAgo(ts: number, now: number = Date.now()): string {
  const seconds = Math.max(0, (now - ts) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  if (days < 2) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  return `on ${new Date(ts).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}`;
}

/** Upper-cased first letters of the first two words of a name ("Aman Sharma" -> "AS"). */
export function initials(name: string, fallback = 'Y'): string {
  const letters = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => Array.from(word)[0] ?? '')
    .join('');
  return letters ? letters.toUpperCase() : fallback;
}

/** "3 skills" / "1 skill". Pass `pluralWord` for irregular plurals (`plural(2, 'category', 'categories')`). */
export function plural(n: number, word: string, pluralWord = `${word}s`): string {
  return `${n} ${n === 1 ? word : pluralWord}`;
}

export function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}

/** Typed carrier for CSS custom properties: `style={cssVars({ '--pop-w': '304px' })}`. */
export function cssVars(vars: Record<string, string | number>): CSSProperties {
  return vars as CSSProperties;
}

const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

export interface TweenOptions {
  /** Milliseconds, default 900. */
  duration?: number;
  /** Milliseconds before the count starts, default 0. */
  delay?: number;
  /** `false` skips the count and returns `target` straight away (already-revealed content). */
  enabled?: boolean;
}

/**
 * Counts an integer up from 0 to `target` (ease-out cubic). A later change of `target` counts on
 * from the value currently shown. Jumps straight to `target` when disabled or under reduced motion.
 */
export function useTween(target: number, { duration = 900, delay = 0, enabled = true }: TweenOptions = {}): number {
  const reduced = useReducedMotion();
  const instant = !enabled || !!reduced;
  const [value, setValue] = useState(instant ? target : 0);
  // The exact (unrounded) position, so an interrupted count resumes where it was.
  const position = useRef(value);

  useEffect(() => {
    if (instant) {
      position.current = target;
      return;
    }
    const controls = animate(position.current, target, {
      duration: duration / 1000,
      delay: delay / 1000,
      ease: easeOutCubic,
      onUpdate: (latest) => {
        position.current = latest;
        setValue(Math.round(latest));
      },
    });
    return () => controls.stop();
  }, [target, duration, delay, instant]);

  return instant ? target : value;
}

/**
 * Tracks an element's size (`clientWidth` / `clientHeight`, so padding counts) through ResizeObserver.
 * Attach the ref to an element that stays mounted; it measures once before first paint, then on resize.
 */
export function useElementSize<T extends HTMLElement>(): [RefObject<T | null>, { width: number; height: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const { clientWidth: width, clientHeight: height } = el;
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, size];
}
