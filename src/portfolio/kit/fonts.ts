/**
 * On-demand web fonts for the portfolio kit. Studio's faces (Instrument Serif, Geist, Geist Mono) are
 * already loaded by index.html, and Technical needs nothing extra, so only Editorial, Warm and the
 * Old Newspaper skin append a Google Fonts stylesheet, once each. Safe to call on the server (no-op).
 */
import type { TypePairing } from '../../../shared/portfolio';

export type FontSet = TypePairing | 'press';

const GOOGLE = 'https://fonts.googleapis.com/css2';

/** Stylesheet per set; `null` = nothing to add beyond index.html. */
const SHEETS: { [K in FontSet]: string | null } = {
  studio: null,
  editorial: `${GOOGLE}?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&display=swap`,
  technical: null,
  warm: `${GOOGLE}?family=Bricolage+Grotesque:opsz,wght@12..96,500..600&display=swap`,
  press: `${GOOGLE}?family=UnifrakturMaguntia&family=Playfair+Display:ital,wght@0,700;0,900;1,400&family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400&display=swap`,
};

const requested = new Set<FontSet>();

/** The stylesheet a font set needs, if any (exported for tests and the dev gallery). */
export function fontSheet(set: FontSet): string | null {
  return SHEETS[set];
}

/** Append the set's `<link rel="stylesheet">` to `<head>` once. */
export function ensureFonts(set: FontSet): void {
  if (typeof document === 'undefined') return;
  const href = SHEETS[set];
  if (!href || requested.has(set)) return;
  requested.add(set);
  if (document.head.querySelector(`link[data-pf-fonts="${set}"]`)) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = href;
  link.dataset.pfFonts = set;
  document.head.appendChild(link);
}
