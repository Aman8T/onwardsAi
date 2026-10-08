/**
 * The four type pairings as CSS custom properties for the portfolio wrapper (`.pf-scope`).
 * Blocks never name a font: they read `--pf-display` / `--pf-body` / `--pf-mono` and the weight and
 * tracking roles below, so a pairing swap restyles the whole page. A name outside Latin script switches
 * the display face to a system / Noto sans (no tight tracking, taller lines) so combining marks and
 * conjuncts are never squeezed or mixed with a mismatched serif.
 */
import type { TypePairing } from '../../../shared/portfolio';
import { needsFallbackFaces } from '../model';

/** Noto faces for scripts the Latin web fonts do not cover; the browser picks per glyph. */
const NOTO =
  "'Noto Sans', 'Noto Sans Devanagari', 'Noto Sans Bengali', 'Noto Sans Tamil', 'Noto Sans Telugu', 'Noto Sans Gujarati', 'Noto Sans Arabic', 'Noto Sans JP', 'Noto Sans SC', 'Noto Color Emoji'";

const SANS = `'Geist', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', ${NOTO}, sans-serif`;
const MONO = `'Geist Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, ${NOTO}, monospace`;

interface Pairing {
  display: string;
  /** Weight of the name and big display lines / of card and list titles / of the tagline and ledes. */
  wName: number;
  wHead: number;
  wLede: number;
  /** Letter-spacing for the same three roles. */
  tName: string;
  tHead: string;
  tLede: string;
  /** Line height of the big name. */
  nameLh: number;
  /** `italic` for the serif pairings' captions, `normal` for the sans ones. */
  italic: 'italic' | 'normal';
}

const PAIRINGS: { [K in TypePairing]: Pairing } = {
  studio: {
    display: `'Instrument Serif', ui-serif, Georgia, 'Times New Roman', ${NOTO}, serif`,
    wName: 400,
    wHead: 400,
    wLede: 400,
    tName: '-0.02em',
    tHead: '-0.015em',
    tLede: '-0.01em',
    nameLh: 0.95,
    italic: 'italic',
  },
  editorial: {
    display: `'Newsreader', ui-serif, Georgia, 'Times New Roman', ${NOTO}, serif`,
    wName: 400,
    wHead: 400,
    wLede: 400,
    tName: '-0.035em',
    tHead: '-0.015em',
    tLede: '-0.015em',
    nameLh: 0.9,
    italic: 'italic',
  },
  technical: {
    display: SANS,
    wName: 600,
    wHead: 600,
    wLede: 500,
    tName: '-0.03em',
    tHead: '-0.025em',
    tLede: '-0.02em',
    nameLh: 0.92,
    italic: 'normal',
  },
  warm: {
    display: `'Bricolage Grotesque', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', ${NOTO}, sans-serif`,
    wName: 600,
    wHead: 500,
    wLede: 500,
    tName: '-0.035em',
    tHead: '-0.015em',
    tLede: '-0.02em',
    nameLh: 0.96,
    italic: 'normal',
  },
};

/** CSS custom properties for one pairing; `name` decides whether fallback faces are needed. */
export function typeVars(type: TypePairing, name: string): Record<string, string> {
  const p = PAIRINGS[type];
  if (needsFallbackFaces(name)) {
    return {
      '--pf-display': SANS,
      '--pf-body': SANS,
      '--pf-mono': MONO,
      '--pf-w-name': '600',
      '--pf-w-head': '600',
      '--pf-w-lede': '500',
      '--pf-t-name': '0',
      '--pf-t-head': '0',
      '--pf-t-lede': '0',
      '--pf-name-lh': '1.2',
      '--pf-italic': 'normal',
    };
  }
  return {
    '--pf-display': p.display,
    '--pf-body': SANS,
    '--pf-mono': MONO,
    '--pf-w-name': String(p.wName),
    '--pf-w-head': String(p.wHead),
    '--pf-w-lede': String(p.wLede),
    '--pf-t-name': p.tName,
    '--pf-t-head': p.tHead,
    '--pf-t-lede': p.tLede,
    '--pf-name-lh': String(p.nameLh),
    '--pf-italic': p.italic,
  };
}
