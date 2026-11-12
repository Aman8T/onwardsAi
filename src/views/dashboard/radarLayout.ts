/**
 * Geometry of the dashboard's skill graph, the biggest thing on the screen: the radar in a 600 x 430 box with a callout
 * floating beside every axis. Pure. Axis angles and points come from src/lib/radar.ts (the one geometry every radar shares:
 * axis 0 at 12 o'clock, clockwise); only the box and the rim are this screen's own. Positions are computed from each
 * axis's angle for any number of axes, so six domains, four or nine all lay out the same way.
 *
 * Each callout sits at the end of a thin leader line that runs from the data point out past the rim, with a ring round the
 * data point. A callout on the left half of the chart is right-aligned (it ends at the leader), one on the right half
 * starts at it. Percentages are of the box, so the labels stay put as the chart scales.
 */
import { angleOf, pointAt, polygonPath, ringPath } from '../../lib/radar';
import type { RadarGeom } from '../../lib/radar';

export const HERO_W = 600;
export const HERO_H = 430;

/** The hero's own box: centre (300, 215), rim 140, labels beyond. */
export const HERO_GEOM: RadarGeom = { size: HERO_W, cx: 300, cy: 215, r: 140, labelR: 170 };

/** How far past the rim the leader reaches, in box units. */
const REACH = 30;
/** A top or bottom axis leans its callout to the right by this much, so the leader is not a vertical line. */
const LEAN = 16;
/** Axes within this of straight up or down (the cosine) count as vertical. */
const VERTICAL = 0.2;

export interface HeroAxis {
  id: string;
  value: number;
}

export interface HeroCallout {
  id: string;
  /** The data point, in box units. */
  px: number;
  py: number;
  /** The end of the leader, in box units. */
  ex: number;
  ey: number;
  /** The callout's anchor as a share of the box, 0 to 100. */
  left: number;
  top: number;
  /** Right-aligned and ending at the anchor (the left half of the chart). */
  right: boolean;
}

export interface HeroLayout {
  rings: string[];
  spokes: Array<{ x: number; y: number }>;
  /** The data polygon. */
  shape: string;
  callouts: HeroCallout[];
  /** The leader lines as one path. */
  leaders: string;
}

const r1 = (n: number) => Math.round(n * 10) / 10;

export function heroLayout(axes: readonly HeroAxis[], geom: RadarGeom = HERO_GEOM): HeroLayout {
  const n = axes.length;
  const g = geom;
  const callouts: HeroCallout[] = axes.map((a, i) => {
    const p = pointAt(g, i, n, Math.min(100, Math.max(0, a.value)) / 100);
    const ang = angleOf(i, n);
    const cos = Math.cos(ang);
    const sin = Math.sin(ang);
    const vertical = Math.abs(cos) < VERTICAL;
    const R = g.r + REACH;
    // a vertical axis leans right; the rest go straight out along their own angle
    const ex = vertical ? g.cx + LEAN : g.cx + cos * R;
    const ey = g.cy + sin * R;
    return { id: a.id, px: r1(p.x), py: r1(p.y), ex: r1(ex), ey: r1(ey), left: r1((ex / HERO_W) * 100), top: r1((ey / HERO_H) * 100), right: !vertical && cos < 0 };
  });
  return {
    rings: [0.25, 0.5, 0.75, 1].map((l) => ringPath(g, n, l)),
    spokes: axes.map((_, i) => {
      const p = pointAt(g, i, n, 1);
      return { x: r1(p.x), y: r1(p.y) };
    }),
    shape: polygonPath(
      g,
      axes.map((a) => Math.min(100, Math.max(0, a.value)) / 100),
    ),
    callouts,
    leaders: callouts.map((c) => `M${c.px} ${c.py}L${c.ex} ${c.ey}`).join(''),
  };
}
