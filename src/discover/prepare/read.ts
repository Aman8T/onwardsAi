/**
 * Small defensive readers shared by the prepare, share and applications code. Everything that crosses a trust boundary
 * (a server answer, a value read back from localStorage) goes through these: a value of the wrong type is nothing, a
 * string that is too long is nothing (quotes are never cut, because a cut quote is no longer word for word), and a
 * list is capped at the contract's own limits.
 */
import type { Cite } from '../../../shared/discover';

export const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

export const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

export const finite = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);

export const oneOf = <T extends string>(allowed: readonly T[], v: unknown): T | undefined =>
  typeof v === 'string' && (allowed as readonly string[]).includes(v) ? (v as T) : undefined;

/** A non-empty string no longer than `max`; anything else is nothing. */
export const text = (v: unknown, max: number): string | undefined => {
  if (typeof v !== 'string') return undefined;
  const t = v.trim();
  return t !== '' && t.length <= max ? t : undefined;
};

/** An ISO time that parses. */
export const isoTime = (v: unknown): string | undefined => {
  const t = text(v, 64);
  return t !== undefined && Number.isFinite(Date.parse(t)) ? t : undefined;
};

/** `YYYY-MM-DD` that names a real day. */
export const isoDay = (v: unknown): string | undefined => {
  if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return undefined;
  const [y, m, d] = v.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? v : undefined;
};

/** A line of the person's evidence, with its source: both must be there for it to be shown as evidence. */
export function readCite(raw: unknown): Cite | undefined {
  if (!isObj(raw)) return undefined;
  const quote = text(raw.text, 1000);
  if (!quote) return undefined;
  return { source: text(raw.source, 100) ?? '', text: quote };
}
