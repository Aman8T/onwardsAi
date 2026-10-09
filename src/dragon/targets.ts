/*
 * What the dragon may burn: visible things on Home from a fixed selector list, never a focused input,
 * never something it burned in the last minute. Rects are measured on demand (a few elements, every
 * decision — not every frame).
 */

/** Elements worth a dragon's attention, by their Home class names. */
const SELECTORS = [
  '.ob-h1-line',
  '.ob-sub',
  '.ob-reads',
  '.ob-hero > .kicker',
  '.ob-nav .btn',
  '.ob-brand .brand',
  '.ob-card-title',
  '.ob-card-sub',
  '.ob-drop',
  '.ob-label',
  '.ob-add-row .field',
  '.ob-cta',
  '.ob-fine',
  '.ob-how-title',
  '.ob-step h3',
  '.ob-step p',
  '.ob-sample-wrap .btn',
];

export interface Target {
  el: HTMLElement;
  rect: DOMRect;
  key: string;
}

/** Seconds a scorched element stays uninteresting. */
export const NOVELTY_SECONDS = 60;

let nextKey = 1;

function keyOf(el: HTMLElement): string {
  let key = el.dataset.dragonKey;
  if (!key) {
    key = String(nextKey++);
    el.dataset.dragonKey = key;
  }
  return key;
}

/** Visible, interactive-safe candidates inside the viewport. */
export function flammable(): Target[] {
  const out: Target[] = [];
  const W = window.innerWidth;
  const H = window.innerHeight;
  const active = document.activeElement;
  for (const el of document.querySelectorAll<HTMLElement>(SELECTORS.join(','))) {
    if (active && (el === active || el.contains(active))) continue;
    if (el.classList.contains('dragon-charred')) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 24 || rect.height < 12) continue;
    if (rect.bottom < 0 || rect.top > H || rect.right < 0 || rect.left > W) continue;
    out.push({ el, rect, key: keyOf(el) });
  }
  return out;
}

/** When each key was last burned, in seconds (performance.now() / 1000). */
export type BurnMemory = Map<string, number>;

export interface PickOptions {
  /** The pointer, CSS px, or null when it has not moved lately. */
  pointer: { x: number; y: number } | null;
  memory: BurnMemory;
  now: number;
  random?: () => number;
}

/**
 * The dragon's taste: novelty first, then size, then nearness to the pointer (it watches what you do),
 * with enough randomness that it never follows the same tour twice.
 */
export function pickTarget(targets: Target[], opts: PickOptions): Target | null {
  const rnd = opts.random ?? Math.random;
  let best: Target | null = null;
  let bestScore = 0;
  for (const t of targets) {
    const last = opts.memory.get(t.key);
    if (last !== undefined && opts.now - last < NOVELTY_SECONDS) continue;
    const area = t.rect.width * t.rect.height;
    const cx = t.rect.left + t.rect.width / 2;
    const cy = t.rect.top + t.rect.height / 2;
    let near = 0;
    if (opts.pointer) {
      const d = Math.hypot(cx - opts.pointer.x, cy - opts.pointer.y);
      near = Math.max(0, 1 - d / 500);
    }
    const score = (0.5 + Math.sqrt(area) / 400) * (1 + 0.6 * near) * (0.7 + 0.6 * rnd());
    if (score > bestScore) {
      bestScore = score;
      best = t;
    }
  }
  return best;
}

/** A broad, horizontal element the dragon can land on (the panel, the header). */
export function perches(): Target[] {
  const out: Target[] = [];
  for (const sel of ['.ob-form', '.ob-header']) {
    const el = document.querySelector<HTMLElement>(sel);
    if (!el) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 200 || rect.top < 40 || rect.top > window.innerHeight - 40) continue;
    out.push({ el, rect, key: keyOf(el) });
  }
  return out;
}
