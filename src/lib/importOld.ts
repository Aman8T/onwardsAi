/*
 * One-time hand-off from the old address (tayyari.fastapicloud.dev) to this one.
 *
 * Browsers keep localStorage per site, so the profile, recent matches and theme a person saved on the old domain are
 * invisible here. The old domain's moving page (`/__moving.js`, served by `api/static.py`) reads those keys and
 * sends the browser here with them in the address fragment (`#leafout-import=…`), which never reaches any server.
 *
 * Imported first in `main.tsx`, so it runs before the store and the theme read localStorage. It only fills an empty
 * browser: an existing profile here is never replaced, and a key already set here is kept. Only the known keys are
 * copied, as strings. The fragment is removed from the address at once.
 */
const PREFIX = '#leafout-import=';

const KEYS = [
  'tayyari.v1',
  'tayyari.matches.recent',
  'tayyari.match.draft',
  'tayyari.practice.prefs',
  'tayyari.scheme',
  'tayyari.atmo',
  'tayyari.hue0',
] as const;

function decode(raw: string): Record<string, unknown> | null {
  try {
    const binary = atob(decodeURIComponent(raw));
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function hasProfile(): boolean {
  try {
    const saved = JSON.parse(localStorage.getItem('tayyari.v1') ?? 'null') as { profile?: unknown } | null;
    return Boolean(saved?.profile);
  } catch {
    return false;
  }
}

function importOld(): void {
  if (typeof window === 'undefined' || !window.location.hash.startsWith(PREFIX)) return;
  const raw = window.location.hash.slice(PREFIX.length);
  window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
  const data = decode(raw);
  if (!data) return;
  try {
    // Never replace work already on this address: the hand-off is for a browser that has nothing here yet.
    if (hasProfile()) return;
    for (const key of KEYS) {
      const value = data[key];
      if (typeof value === 'string' && value.length < 2_000_000 && localStorage.getItem(key) === null) {
        localStorage.setItem(key, value);
      }
    }
  } catch {
    /* storage blocked or full: start fresh, as before */
  }
}

importOld();

export {};
