/**
 * Pure geometry and data helpers for the portfolio's Skill Graph variants (no React, no CSS).
 *
 * Everything is drawn on the app radar's 600 × 600 viewBox (`src/lib/radar.ts`: centre 300,300, rim 200),
 * so a portfolio radar and the app radar share their shape. Values are passed through exactly: the helpers
 * only turn the snapshot's integers into coordinates, they never rescale or re-round a score. Axes keep the
 * snapshot's fixed order (clockwise from 12 o'clock); only the *ranked lists* (ledger rows, strongest skills)
 * order by value, and they never feed a radar.
 */
import { angleOf, pointAt, polygonPath, radarGeom, ringPath, RADAR_VIEW } from '../../lib/radar';
import type { RadarGeom } from '../../lib/radar';
import type { GraphDomain, GraphSkill } from '../../../shared/portfolio';

/** The one geometry every portfolio radar shares (identical to the app radar). */
export const GEOM: RadarGeom = radarGeom();

/** Skills scoring under this are never listed by name on a public page (mirrors GRAPH_PUBLIC_MIN_SKILL). */
export const PUBLIC_MIN_SKILL = 45;

export const RING_LEVELS = [0.25, 0.5, 0.75, 1] as const;

/** Dot radii in viewBox units at full size (≈ 2.9 / 5 / 6.7 px on a 500 px plot). */
export const DOT_R = 3.5;
export const PEAK_DOT_R = 6;
export const SPOT_DOT_R = 8;

/** The ledger variant's small radar is drawn in a 440-unit window at ~200 px, so its dots scale up. */
export const LEDGER_DOT_SCALE = 1.8;

/** Half the base of a stellar spear: the base is 14 units wide at the centre. */
export const SPEAR_HALF_BASE = 7;

/** Atlas mini radars: a 520-unit window, skill numerals at this radius, bigger dots. */
export const MINI_LABEL_R = 222;
export const MINI_DOT_SCALE = 3.2;

/** The printed (newspaper) radar fills its polygon with this halftone: two dots per 12-unit cell. */
export const HALFTONE = {
  size: 12,
  dots: [
    { cx: 3, cy: 3, r: 2.1 },
    { cx: 9, cy: 9, r: 2.1 },
  ],
} as const;

/* ───────────────────────── view boxes ───────────────────────── */

export interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const FULL_VIEW: ViewBox = { x: 0, y: 0, w: RADAR_VIEW, h: RADAR_VIEW };
/** Rim-only crop for small radars with no axis labels (ledger glyph). */
export const TIGHT_VIEW: ViewBox = { x: 80, y: 80, w: 440, h: 440 };
/** Atlas mini radar crop (numerals sit just outside the rim). */
export const MINI_VIEW: ViewBox = { x: 40, y: 40, w: 520, h: 520 };

export function viewBoxAttr(v: ViewBox): string {
  return `${v.x} ${v.y} ${v.w} ${v.h}`;
}

/** One decimal, `-0` normalised, so output is byte-stable. */
export function fmt(v: number): string {
  const r = Math.round(v * 10) / 10;
  return String(r === 0 ? 0 : r);
}

/* ───────────────────────── label anchors ───────────────────────── */

/** Which side of its anchor a label sits on (`c`: centred on it). */
export type LabelSide = 'n' | 's' | 'e' | 'w' | 'c';

export interface LabelSpot {
  /** Percent of the plot box (the view box), one decimal. */
  left: number;
  top: number;
  side: LabelSide;
}

/**
 * Where axis `i` of `n` anchors its label: just outside the rim, on the east or west side unless the axis is
 * within ~20° of vertical, in which case the label sits above or below it, centred (the approved boards).
 */
export function labelSpot(
  i: number,
  n: number,
  view: ViewBox = FULL_VIEW,
  radius: number = GEOM.labelR,
  centred = false,
): LabelSpot {
  const a = angleOf(i, n);
  const c = Math.cos(a);
  const s = Math.sin(a);
  const x = GEOM.cx + c * radius;
  const y = GEOM.cy + s * radius;
  let side: LabelSide;
  if (centred) side = 'c';
  else if (Math.abs(c) < 0.35) side = s < 0 ? 'n' : 's';
  else side = c > 0 ? 'e' : 'w';
  return { left: Number(fmt(((x - view.x) / view.w) * 100)), top: Number(fmt(((y - view.y) / view.h) * 100)), side };
}

/* ───────────────────────── paths ───────────────────────── */

/** The four grid rings as one path. */
export function ringsPath(n: number): string {
  return RING_LEVELS.map((level) => ringPath(GEOM, n, level)).join(' ');
}

/** Spokes from the centre to the rim of every axis, as one path. */
export function spokesPath(n: number): string {
  let d = '';
  for (let i = 0; i < n; i++) {
    const p = pointAt(GEOM, i, n, 1);
    d += `${i === 0 ? '' : ' '}M${fmt(GEOM.cx)} ${fmt(GEOM.cy)} L${fmt(p.x)} ${fmt(p.y)}`;
  }
  return d;
}

/** One spoke (the spotlit axis). */
export function spokePath(i: number, n: number): string {
  const p = pointAt(GEOM, i, n, 1);
  return `M${fmt(GEOM.cx)} ${fmt(GEOM.cy)} L${fmt(p.x)} ${fmt(p.y)}`;
}

/**
 * Stellar spears: one thin isosceles triangle per axis, base `2 × SPEAR_HALF_BASE` units across the centre,
 * tip at the score. No connecting polygon, so the shape carries no area distortion.
 */
export function spearsPath(values100: number[]): string {
  const n = values100.length;
  let d = '';
  for (let i = 0; i < n; i++) {
    const a = angleOf(i, n);
    const px = -Math.sin(a) * SPEAR_HALF_BASE;
    const py = Math.cos(a) * SPEAR_HALF_BASE;
    const tip = pointAt(GEOM, i, n, values100[i] / 100);
    d +=
      `${i === 0 ? '' : ' '}M${fmt(GEOM.cx + px)} ${fmt(GEOM.cy + py)}` +
      ` L${fmt(tip.x)} ${fmt(tip.y)}` +
      ` L${fmt(GEOM.cx - px)} ${fmt(GEOM.cy - py)} Z`;
  }
  return d;
}

/* ───────────────────────── plot model ───────────────────────── */

export interface PlotModel {
  n: number;
  view: ViewBox;
  rings: string;
  spokes: string;
  /** The "you" polygon (empty for fewer than 3 axes). */
  poly: string;
  /** Stellar spears (empty for fewer than 3 axes). */
  spears: string;
  /** Vertex of every axis at its score, in view-box units. */
  vertices: Array<{ x: number; y: number }>;
  labels: LabelSpot[];
}

export interface PlotOptions {
  view?: ViewBox;
  /** Label anchor radius (default: the app radar's 236). */
  labelRadius?: number;
  /** Centre the labels on their anchors (atlas numerals). */
  centredLabels?: boolean;
}

/** Everything a radar, stellar chart or mini radar draws, from scores 0–100 in axis order. */
export function buildPlot(values100: number[], opts: PlotOptions = {}): PlotModel {
  const n = values100.length;
  const view = opts.view ?? FULL_VIEW;
  const values01 = values100.map((v) => v / 100);
  return {
    n,
    view,
    rings: n >= 3 ? ringsPath(n) : '',
    spokes: n >= 1 ? spokesPath(n) : '',
    poly: n >= 3 ? polygonPath(GEOM, values01) : '',
    spears: n >= 3 ? spearsPath(values100) : '',
    vertices: values01.map((v, i) => pointAt(GEOM, i, n, v)),
    labels: values100.map((_, i) => labelSpot(i, n, view, opts.labelRadius, opts.centredLabels)),
  };
}

/**
 * The window for the focus variant's glyph: the rim plus the numeral labels, padded. For three axes this is
 * close to the Student board's `60 30 480 430`.
 */
export function glyphView(n: number): ViewBox {
  if (n < 3) return FULL_VIEW;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    for (const r of [GEOM.r, GEOM.labelR]) {
      const p = pointAt(GEOM, i, n, r / GEOM.r);
      minX = Math.min(minX, p.x);
      maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y);
      maxY = Math.max(maxY, p.y);
    }
  }
  const pad = 40;
  return { x: Math.floor(minX - pad), y: Math.floor(minY - pad), w: Math.ceil(maxX - minX + 2 * pad), h: Math.ceil(maxY - minY + 2 * pad) };
}

/* ───────────────────────── data helpers ───────────────────────── */

/** `n` strongest skills of a domain that may be listed publicly (score ≥ 45), strongest first, ties in order. */
export function listedSkills(domain: GraphDomain, limit = Infinity): GraphSkill[] {
  return domain.skills
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => s.score >= PUBLIC_MIN_SKILL)
    .sort((a, b) => b.s.score - a.s.score || a.i - b.i)
    .slice(0, limit)
    .map(({ s }) => s);
}

/** A domain's listable skills in the snapshot's own order (strongest first), at most `limit`. */
export function visibleSkills(domain: GraphDomain, limit = Infinity): GraphSkill[] {
  return domain.skills.filter((s) => s.score >= PUBLIC_MIN_SKILL).slice(0, limit);
}

/** Strongest listable skills across the shown domains (ties keep graph order). */
export function topSkills(domains: GraphDomain[], limit: number): GraphSkill[] {
  const all: Array<{ s: GraphSkill; i: number }> = [];
  for (const d of domains) for (const s of listedSkills(d)) all.push({ s, i: all.length });
  return all
    .sort((a, b) => b.s.score - a.s.score || a.i - b.i)
    .slice(0, limit)
    .map(({ s }) => s);
}

/** Domains ranked by score, ties in the graph's fixed order (for ranked rows only, never for axes). */
export function rankDomains(domains: GraphDomain[]): GraphDomain[] {
  return domains
    .map((d, i) => ({ d, i }))
    .sort((a, b) => b.d.score - a.d.score || a.i - b.i)
    .map(({ d }) => d);
}

/** Id of the first domain with the highest score ("▲ high" in the newspaper table). */
export function topDomainId(domains: GraphDomain[]): string | undefined {
  let top: GraphDomain | undefined;
  for (const d of domains) if (!top || d.score > top.score) top = d;
  return top?.id;
}

/** A 0–100 score as a safe CSS percentage for a value line. */
export function pct(score: number): number {
  return Math.max(0, Math.min(100, score));
}
