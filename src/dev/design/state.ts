/**
 * Playground state and its query string. The contract (all optional, shareable, reload-safe):
 *
 *   /dev/design?look=quiet&scheme=dark&h=250&screen=graph&compare=1
 *
 *   look     today | quiet            absent = the default (quiet); today shows the earlier look
 *   scheme   light | dark             absent = System. ambient.ts reads this key itself, so it pins the scheme on load
 *   h        0..359                   absent = the clock's hue (ambient.ts pins it on load, and then holds Still)
 *   screen   home | dashboard | graph | match | weekly
 *   compare  1                        absent = single column
 *   light    off                      the cursor light is not mounted (needs a reload, so the bar reloads)
 */
import { applyHue, hueAt } from '../../theme/hue';
import { effectiveMode, forcedHue, getHue0, getLook, getMode, setMode, setScheme } from '../../theme/ambient';
import type { Look, SchemeChoice } from '../../theme/ambient';

export type { Look };
export type ScreenId = 'home' | 'dashboard' | 'graph' | 'match' | 'weekly';

export const SCREENS: readonly (readonly [ScreenId, string])[] = [
  ['home', 'Empty dashboard'],
  ['dashboard', 'Dashboard'],
  ['graph', 'Next role'],
  ['match', 'Match'],
  ['weekly', 'Weekly'],
];

export interface PlaygroundState {
  look: Look;
  scheme: SchemeChoice;
  hue: number | null;
  screen: ScreenId;
  compare: boolean;
}

const isScreen = (s: string | null): s is ScreenId => SCREENS.some(([id]) => id === s);

export function readState(fallbackScheme: SchemeChoice): PlaygroundState {
  const p = new URLSearchParams(window.location.search);
  const scheme = p.get('scheme');
  const screen = p.get('screen');
  return {
    look: p.get('look') === 'today' ? 'today' : 'quiet',
    scheme: scheme === 'light' || scheme === 'dark' ? scheme : fallbackScheme,
    hue: forcedHue(),
    screen: isScreen(screen) ? screen : 'home',
    compare: p.get('compare') === '1',
  };
}

/** Mirrors the state into the address (replaceState), keeping any other keys such as `light`. */
export function writeUrl(s: PlaygroundState): void {
  const p = new URLSearchParams(window.location.search);
  const put = (k: string, v: string | null) => {
    if (v === null) p.delete(k);
    else p.set(k, v);
  };
  put('look', s.look === 'today' ? 'today' : null);
  put('scheme', s.scheme === 'system' ? null : s.scheme);
  put('h', s.hue === null ? null : String(s.hue));
  put('screen', s.screen);
  put('compare', s.compare ? '1' : null);
  const q = p.toString();
  window.history.replaceState(null, '', `${window.location.pathname}${q ? `?${q}` : ''}`);
}

/** Sets the attribute directly (not setLook): the playground previews a look without storing it as the person's choice. */
export function applyLook(look: Look): void {
  if (look === 'quiet') document.documentElement.dataset.look = 'quiet';
  else delete document.documentElement.dataset.look;
}

/** Leaving the playground: back to the person's own look (quiet unless they chose today). */
export function restoreLook(): void {
  applyLook(getLook());
}

export function applyScheme(scheme: SchemeChoice): void {
  setScheme(scheme);
  // setScheme returns early when the stored choice already matches, so the attribute is set here as well
  if (scheme === 'system') delete document.documentElement.dataset.scheme;
  else document.documentElement.dataset.scheme = scheme;
}

/** Call after `h` is in the address: writes the hue properties now (ambient.ts would wait for its next 7 s step). */
export function applyHuePin(hue: number | null): void {
  const now = Date.now();
  const h0 = getHue0();
  const base = hueAt(now, h0);
  applyHue(document.documentElement, hue === null ? base : hueAt(now, h0 + hue - base.h));
  document.documentElement.dataset.atmo = effectiveMode();
  if (hue === null && getMode() === 'drift') {
    // restart the stepper that a pinned hue stopped: a change of mode and back runs ambient's own refresh
    setMode('still');
    setMode('drift');
  }
}

/** The cursor light is mounted by App (not here); `?light=off` is read when it renders, so changing it reloads. */
export function cursorIsOff(): boolean {
  return new URLSearchParams(window.location.search).get('light') === 'off';
}

export function reloadWithCursor(on: boolean): void {
  const p = new URLSearchParams(window.location.search);
  if (on) p.delete('light');
  else p.set('light', 'off');
  const q = p.toString();
  window.location.assign(`${window.location.pathname}${q ? `?${q}` : ''}`);
}
