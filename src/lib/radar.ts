/**
 * Radar geometry — pure functions over a fixed 600 × 600 viewBox.
 *
 * The chart is drawn in viewBox units and scaled by CSS, so every helper here
 * is deterministic and unit-testable. Axis 0 sits at 12 o'clock and the rest
 * follow clockwise in the order given (never sorted by value: a fixed order
 * keeps shapes recognisable across profiles).
 */

/** Side length of the square viewBox (`0 0 600 600`). */
export const RADAR_VIEW = 600;

export interface RadarGeom {
  /** ViewBox side length. */
  size: number;
  /** Centre of the plot in viewBox units. */
  cx: number;
  cy: number;
  /** Radius of the 100 % ring. */
  r: number;
  /** Radius at which axis labels are anchored, outside the rim. */
  labelR: number;
}

/** The one geometry every radar shares: centre 300,300, rim 200, labels at 236. */
export function radarGeom(): RadarGeom {
  return { size: RADAR_VIEW, cx: RADAR_VIEW / 2, cy: RADAR_VIEW / 2, r: 200, labelR: 236 };
}

/** Angle of axis `i` of `n` in radians, measured clockwise from 12 o'clock (`n` ≥ 1). */
export function angleOf(i: number, n: number): number {
  return -Math.PI / 2 + (i * 2 * Math.PI) / n;
}

/** Point on axis `i` of `n` at `value01` (0 = centre, 1 = rim). */
export function pointAt(g: RadarGeom, i: number, n: number, value01: number): { x: number; y: number } {
  const a = angleOf(i, n);
  return { x: g.cx + Math.cos(a) * g.r * value01, y: g.cy + Math.sin(a) * g.r * value01 };
}

/** One decimal, with `-0` normalised so paths are byte-stable. */
function fmt(v: number): string {
  const r = Math.round(v * 10) / 10;
  return String(r === 0 ? 0 : r);
}

/**
 * Closed polygon through every axis: `M x y L x y … Z`.
 * The command count depends only on `values01.length`, which is what lets a
 * motion value tween the vertices along their spokes without re-parsing.
 */
export function polygonPath(g: RadarGeom, values01: number[]): string {
  const n = values01.length;
  if (n === 0) return '';
  let d = '';
  for (let i = 0; i < n; i++) {
    const p = pointAt(g, i, n, values01[i]);
    d += `${i === 0 ? 'M' : ' L'} ${fmt(p.x)} ${fmt(p.y)}`;
  }
  return `${d} Z`;
}

/** The grid "ring" at `level01`: a polygon through all `n` axes at that level. */
export function ringPath(g: RadarGeom, n: number, level01: number): string {
  const values = new Array<number>(n).fill(level01);
  return polygonPath(g, values);
}

export type LabelSide = 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'nw';

const SIDES: readonly LabelSide[] = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'];

/**
 * Where axis `i`'s label is anchored (at `labelR`) and which compass side it
 * faces, from 45° sectors so CSS can translate the label away from the rim.
 */
export function labelAnchor(g: RadarGeom, i: number, n: number): { x: number; y: number; side: LabelSide } {
  const a = angleOf(i, n);
  // Compass degrees: 0 = north, 90 = east, clockwise.
  const deg = (((a + Math.PI / 2) * 180) / Math.PI + 360) % 360;
  const side = SIDES[Math.round(deg / 45) % 8];
  return { x: g.cx + Math.cos(a) * g.labelR, y: g.cy + Math.sin(a) * g.labelR, side };
}

/**
 * Pivot for an axis-set crossfade: the label anchor of `pivotId` in the
 * outgoing set, else in the incoming set, else the centre. Both layers scale
 * about this point so the vertex the user clicked stays put.
 */
export function pivotFor(
  prev: Array<{ id: string }>,
  next: Array<{ id: string }>,
  pivotId: string | null | undefined,
  g: RadarGeom,
): { x: number; y: number } {
  if (pivotId) {
    for (const set of [prev, next]) {
      const i = set.findIndex((a) => a.id === pivotId);
      if (i >= 0) {
        const { x, y } = labelAnchor(g, i, set.length);
        return { x, y };
      }
    }
  }
  return { x: g.cx, y: g.cy };
}

/**
 * The leaf the chart is drawn on (design canvas "Leaf radar experiments", quiet whole leaf): a palmate leaf that shares the
 * chart's geometry: the spokes are its main veins and the rings its cross-veins, so it adds no line of its own inside the
 * chart. One lobe per axis with its tip just past the rim, a sinus between neighbours; the stalk leaves through the sinus
 * nearest the bottom, between two spokes, so it never runs along one. Decoration only, and only for three axes or more.
 */
export interface RadarLeaf {
  blade: string;
  stalk: string;
}

export function radarLeaf(g: RadarGeom, n: number): RadarLeaf | null {
  if (n < 3) return null;
  const half = Math.PI / n;
  const at = (a: number, r: number) => `${fmt(g.cx + Math.cos(a) * r)} ${fmt(g.cy + Math.sin(a) * r)}`;
  const tipR = g.r * 1.12;
  const sinusR = g.r * 0.8;
  const shoulderR = g.r * 1.04;
  let blade = '';
  for (let i = 0; i < n; i++) {
    const a = angleOf(i, n);
    if (i === 0) blade = `M ${at(a - half, sinusR)}`;
    blade += ` Q ${at(a - half * 0.55, shoulderR)} ${at(a, tipR)} Q ${at(a + half * 0.55, shoulderR)} ${at(a + half, sinusR)}`;
  }
  // the sinus closest to straight down carries the stalk
  let stalkAngle = angleOf(0, n) + half;
  let bestGap = Infinity;
  for (let i = 0; i < n; i++) {
    const m = angleOf(i, n) + half;
    const gap = Math.abs(Math.atan2(Math.sin(m - Math.PI / 2), Math.cos(m - Math.PI / 2)));
    if (gap < bestGap) {
      bestGap = gap;
      stalkAngle = m;
    }
  }
  // it starts at the leaf's edge, so no line of its own crosses the chart
  const stalk = `M ${at(stalkAngle, sinusR)} Q ${at(stalkAngle - 0.08, g.r * 1.1)} ${at(stalkAngle, g.r * 1.3)}`;
  return { blade: `${blade} Z`, stalk };
}
