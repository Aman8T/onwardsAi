/**
 * Alignment as half rings, the closest role outermost: one concentric half ring per role, each filled to that role's
 * alignment. Pure; drawn in a 150 x 78 box with the centre at (75, 76). Measured, so plain strokes, nothing hand-drawn.
 */
export const ARC_VIEWBOX = '0 0 150 78';
export const ARC_MAX = 5;

const CX = 75;
const CY = 76;
const R0 = 70;
const STEP = 11;

const f = (n: number): string => n.toFixed(1);

/** A half ring of radius `rad`, from its left end clockwise to `pct` (0 to 1) of the way round. */
function arc(rad: number, pct: number): string {
  const p = Math.min(1, Math.max(0, pct));
  const th = Math.PI - Math.PI * p;
  return `M${CX - rad} ${CY}A${rad} ${rad} 0 0 1 ${f(CX + rad * Math.cos(th))} ${f(CY - rad * Math.sin(th))}`;
}

export interface Arcs {
  /** Every ring, full. */
  track: string;
  /** The closest role, outermost. */
  top: string;
  /** The others, inside it. */
  others: string;
}

/** `alignments` are 0 to 100, closest role first; at most ARC_MAX are drawn. */
export function alignmentArcs(alignments: readonly number[]): Arcs {
  const list = alignments.slice(0, ARC_MAX);
  const radius = (i: number) => R0 - STEP * i;
  return {
    track: list.map((_, i) => arc(radius(i), 1)).join(''),
    top: list.length ? arc(radius(0), list[0] / 100) : '',
    others: list
      .slice(1)
      .map((a, i) => arc(radius(i + 1), a / 100))
      .join(''),
  };
}
