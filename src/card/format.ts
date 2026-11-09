/**
 * Small pure helpers for the share sheet: which areas the card draws, the address of the real image the preview shows,
 * and the words around it. Nothing here touches the DOM or the network.
 */
import { CARD_AREAS_MAX, CARD_AREAS_MIN_RADAR } from '../../shared/card';
import type { CardArea, CardSettings, CardTheme, CardView } from '../../shared/card';

export const THEME_OPTIONS: ReadonlyArray<{ value: CardTheme; label: string }> = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'transparent', label: 'Transparent' },
  { value: 'match', label: 'Match the page' },
];

/**
 * The areas the card draws, in the graph's own order. The server already keeps at most eight; if more ever arrive the
 * eight highest-scoring stay, still in the graph's order (the card's own rule).
 */
export function shownAreas(areas: CardArea[]): CardArea[] {
  if (areas.length <= CARD_AREAS_MAX) return areas;
  const keep = new Set(
    areas
      .map((a, i) => ({ i, score: a.score }))
      .sort((a, b) => b.score - a.score || a.i - b.i)
      .slice(0, CARD_AREAS_MAX)
      .map((x) => x.i),
  );
  return areas.filter((_, i) => keep.has(i));
}

/** True when the areas make a shape; fewer draw bars and no leaf. */
export const hasShape = (areas: CardArea[]): boolean => areas.length >= CARD_AREAS_MIN_RADAR;

/** The highest-scoring area; a tie goes to the first in the graph's order. */
export function strongest(areas: CardArea[]): CardArea | undefined {
  let best: CardArea | undefined;
  for (const a of areas) if (!best || a.score > best.score) best = a;
  return best;
}

/** Screen-reader text for the card: the shape in words, never a rank. */
export function cardLabel(areas: CardArea[]): string {
  const top = strongest(areas);
  if (!top) return 'Skill shape.';
  const n = areas.length;
  return `Skill shape across ${n} ${n === 1 ? 'area' : 'areas'}, strongest ${top.name} at ${Math.round(top.score)} of 100.`;
}

/** "22 Oct 2026" for an ISO time; today when there is none (or it does not parse). */
export function dayLabel(iso?: string): string {
  const t = iso ? Date.parse(iso) : NaN;
  const d = Number.isFinite(t) ? new Date(t) : new Date();
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * The real card, same origin (the CSP allows `img-src 'self'`). It follows what the server holds, not the unsaved
 * draft, so the preview is exactly what others see. `v` is only a cache key for this browser: the image is cached for
 * an hour, and a changed setting has to show at once. `match` is drawn light (the dark card is for dark visitors).
 */
export function imageSrc(view: CardView, narrow: boolean): string | undefined {
  const { slug, settings } = view;
  if (!settings.shared || !slug) return undefined;
  const q: string[] = [];
  if (settings.theme === 'dark' || settings.theme === 'transparent') q.push(`theme=${settings.theme}`);
  q.push(`size=${narrow ? 'sm' : 'md'}`);
  if (settings.roleId && !narrow) q.push(`compare=${encodeURIComponent(settings.roleId)}`);
  if (view.updatedAt) q.push(`v=${encodeURIComponent(view.updatedAt)}`);
  return `/r/${encodeURIComponent(slug)}.svg?${q.join('&')}`;
}

/** The same settings, for deciding whether a response still matches the draft. */
export function sameSettings(a: CardSettings, b: CardSettings): boolean {
  return (
    a.shared === b.shared &&
    a.theme === b.theme &&
    (a.roleId ?? '') === (b.roleId ?? '') &&
    (a.line ?? '') === (b.line ?? '') &&
    a.skills.length === b.skills.length &&
    a.skills.every((s, i) => s === b.skills[i])
  );
}
