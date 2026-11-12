/**
 * Where the two floating gap numbers sit beside a role's radar. Pure. The axis angles, the points and the label anchors
 * come from src/lib/radar.ts (the one geometry every radar shares: axis 0 at 12 o'clock, clockwise), the same method the
 * dashboard's skill graph uses (views/dashboard/radarLayout.ts, copied small here so the two screens do not import each
 * other). The number floats just outside the axis's own label, above it on the upper half and below it on the lower
 * half, kept inside the radar's box. Percentages are of the box, so it stays put as the chart scales.
 */
import { angleOf, labelAnchor, pointAt, radarGeom } from '../../lib/radar';

/** How far past the axis label (in 600-box units) the number is set, so it never lands on the label. */
const BEYOND = 50;
/** Axes within this of straight up or down (the cosine) count as vertical. */
const VERTICAL = 0.25;

export interface GapAxis {
  id: string;
  /** The person's level, 0 to 100. */
  value: number;
}

export interface GapSpot {
  id: string;
  /** The person's data point, as a share of the box (0 to 100). */
  px: number;
  py: number;
  /** The floating number's anchor, as a share of the box. */
  left: number;
  top: number;
  /** Which edge the anchor is: the number grows away from it. */
  align: 'left' | 'right' | 'centre';
}

const r1 = (n: number) => Math.round(n * 10) / 10;

/** The spot of each named axis (by index in `axes`), for a radar of `axes.length` axes. */
export function gapSpots(axes: readonly GapAxis[], pick: readonly string[]): GapSpot[] {
  const g = radarGeom();
  const n = axes.length;
  const out: GapSpot[] = [];
  axes.forEach((a, i) => {
    if (!pick.includes(a.id)) return;
    const ang = angleOf(i, n);
    const cos = Math.cos(ang);
    const sin = Math.sin(ang);
    const lab = labelAnchor(g, i, n);
    const p = pointAt(g, i, n, Math.min(100, Math.max(0, a.value)) / 100);
    const vertical = Math.abs(cos) < VERTICAL;
    const align: GapSpot['align'] = vertical ? 'centre' : cos < 0 ? 'left' : 'right';
    // sideways the number hugs the box edge on its own side; vertical axes keep their column
    const x = vertical ? lab.x : cos < 0 ? 0 : g.size;
    const y = lab.y + (sin < 0 ? -BEYOND : BEYOND);
    out.push({ id: a.id, px: r1((p.x / g.size) * 100), py: r1((p.y / g.size) * 100), left: r1((x / g.size) * 100), top: r1((Math.min(g.size, Math.max(0, y)) / g.size) * 100), align });
  });
  return out;
}
