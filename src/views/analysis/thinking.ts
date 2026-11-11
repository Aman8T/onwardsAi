/** The thinking stream's logic: pure, no DOM. Content lives in thinkingLines.ts. */
import type { AnalysisState } from '../../store';
import type { ThinkingLine, ThinkingPhase, ThinkingType } from './thinkingLines';

export const ROTATE_MS = 2600;
export const FADE_MS = 240;
export const MIN_SHOW_MS = 600;

export const LABEL: Record<ThinkingType, string> = { verb: 'Now', tip: 'Tip', quote: 'Rule', stat: 'So far' };
/** The only words in braces a line may use; the run can fill each of them. */
export const PLACEHOLDERS = ['source', 'done', 'sources', 'chars'] as const;
export type ThinkingValues = Partial<Record<(typeof PLACEHOLDERS)[number], string | number>>;

export interface Picked {
  type: ThinkingType;
  text: string;
}

/** What the run is doing now, or null when it is not running (idle, done, failed). */
export function thinkingPhase(a: Pick<AnalysisState, 'phase' | 'readers' | 'synthStep'>): ThinkingPhase | null {
  if (a.phase === 'reading') return a.readers.length === 0 ? 'starting' : 'reading';
  if (a.phase === 'synthesizing') return a.synthStep >= 3 ? 'rank' : a.synthStep === 2 ? 'score' : 'merge';
  return null;
}

/** Values drawn only from this run's own state; a value that does not exist yet is left out. */
export function thinkingValues(a: Pick<AnalysisState, 'readers' | 'synthChars'>): ThinkingValues {
  const total = a.readers.length;
  const reading = a.readers.find((r) => r.status === 'reading');
  const v: ThinkingValues = {};
  if (reading?.kindLabel) v.source = reading.kindLabel;
  if (total > 0) {
    v.sources = total;
    v.done = a.readers.filter((r) => r.status === 'done').length;
  }
  if (a.synthChars > 0) v.chars = a.synthChars.toLocaleString('en-US');
  return v;
}

/** Fills the placeholders, or returns null when any value is missing. */
export function fillLine(text: string, vals: ThinkingValues): string | null {
  let ok = true;
  const out = text.replace(/\{(\w+)\}/g, (_, key: string) => {
    const v = (vals as Record<string, string | number | undefined>)[key];
    if (v === undefined || v === '') {
      ok = false;
      return '';
    }
    return String(v);
  });
  return ok ? out : null;
}

export interface Picker {
  /** The next line for this phase, or null when there is nothing to show. */
  next(phase: ThinkingPhase, vals: ThinkingValues): Picked | null;
}

/**
 * Shuffles with no immediate repeat; nothing twice until the pool for the phase is spent; a verb between any two
 * non-verbs; a verb first when the phase changes (and at the start).
 */
export function createPicker(lines: readonly ThinkingLine[], rng: () => number = Math.random): Picker {
  const used = new Map<ThinkingPhase, Set<string>>();
  let lastText = '';
  let lastType: ThinkingType | '' = '';
  let lastPhase: ThinkingPhase | '' = '';
  return {
    next(phase, vals) {
      if (!lines.length) return null;
      const pool: Picked[] = [];
      for (const l of lines) {
        if (l.when && l.when !== phase) continue;
        const text = fillLine(l.text, vals);
        if (text && text !== lastText) pool.push({ type: l.type, text });
      }
      if (!pool.length) return null;
      const wantVerb = phase !== lastPhase || lastType !== 'verb';
      const typed = pool.filter((l) => (l.type === 'verb') === wantVerb);
      const options = typed.length ? typed : pool;
      let seen = used.get(phase);
      if (!seen) used.set(phase, (seen = new Set()));
      let fresh = options.filter((l) => !seen.has(l.text));
      if (!fresh.length) {
        for (const o of options) seen.delete(o.text);
        fresh = options;
      }
      const pick = fresh[Math.min(fresh.length - 1, Math.floor(rng() * fresh.length))];
      seen.add(pick.text);
      lastText = pick.text;
      lastType = pick.type;
      lastPhase = phase;
      return pick;
    },
  };
}
