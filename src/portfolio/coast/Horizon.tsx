/**
 * The Neon Coast horizon: a setting sun, long clouds, a low skyline, the sea and a few palms, all drawn here as
 * one inline SVG from the skin's own tokens (coast.css colours every shape by class). It is decoration only
 * (`aria-hidden`), carries no text, and no text is ever placed over it. Nothing in it is traced from anywhere:
 * the buildings are plain blocks and the palms are built from the crescents below.
 */
import { useId } from 'react';

const W = 1200;
const H = 300;
/** Where the sea meets the sky, in view-box units. */
const SEA = 214;

const r1 = (v: number): number => Math.round(v * 10) / 10;

/** A frond: a crescent from the crown out to `(tx, ty)`, arched upward like a leaf under its own weight. */
function frond(tx: number, ty: number): string {
  const arch = Math.hypot(tx, ty) * 0.4;
  const mx = tx / 2;
  const my = ty / 2;
  return `M0 0 Q${r1(mx)} ${r1(my - arch)} ${r1(tx)} ${r1(ty)} Q${r1(mx)} ${r1(my - arch * 0.38)} 0 0Z`;
}

/** Frond tips around a crown, for a palm 200 units tall. */
const TIPS: ReadonlyArray<readonly [number, number]> = [
  [12, -70],
  [66, -42],
  [92, -4],
  [82, 36],
  [46, 66],
  [-58, -48],
  [-94, -10],
  [-86, 32],
  [-42, 64],
];

const CROWN = TIPS.map(([x, y]) => frond(x, y)).join(' ');

interface PalmSpec {
  /** Foot of the trunk, in view-box units. */
  x: number;
  y: number;
  /** Height of the trunk. */
  h: number;
  /** How far the crown sits to the side of the foot. */
  lean: number;
}

function trunk({ h, lean }: PalmSpec): string {
  const w = Math.max(3, h * 0.028);
  return (
    `M${r1(-w)} 0 C${r1(-w + lean * 0.05)} ${r1(-h * 0.4)} ${r1(lean * 0.62 - w * 0.7)} ${r1(-h * 0.8)} ${r1(lean - w * 0.45)} ${r1(-h)}` +
    ` L${r1(lean + w * 0.45)} ${r1(-h)} C${r1(lean * 0.62 + w * 0.7)} ${r1(-h * 0.8)} ${r1(w + lean * 0.05)} ${r1(-h * 0.4)} ${r1(w)} 0Z`
  );
}

const PALMS: PalmSpec[] = [
  { x: 96, y: 300, h: 236, lean: 34 },
  { x: 188, y: 300, h: 168, lean: -22 },
  { x: 352, y: 300, h: 132, lean: 16 },
  { x: 1012, y: 300, h: 150, lean: -18 },
  { x: 1116, y: 300, h: 222, lean: -38 },
];

/** A low coast skyline: plain blocks, two stepped towers, nothing a real city would recognise as its own. */
const SKYLINE =
  `M0 ${SEA} V198 H44 V186 H92 V200 H130 V172 H150 V160 H178 V172 H196 V192 H250 V180 H306 V196 H360 V168 H372 V150 H384 V136 H398 V150 H410 V168 H424 V190` +
  ` H470 V176 H520 V194 H566 V184 H604 V198 H660 V178 H700 V190 H742 V200 H800 V188 H846 V174 H870 V196 H930 V182 H968 V164 H980 V148 H1000 V164 H1012 V186` +
  ` H1060 V196 H1110 V180 H1160 V192 H${W} V${SEA}Z`;

/** Long, flat evening clouds: x, y, width, height. */
const CLOUDS: ReadonlyArray<readonly [number, number, number, number]> = [
  [60, 58, 300, 9],
  [240, 82, 190, 6],
  [520, 44, 260, 8],
  [900, 70, 240, 8],
  [700, 108, 330, 6],
  [120, 128, 220, 5],
];

/** The sun's light on the water, widest at the horizon: y, half-width. */
const GLINTS: ReadonlyArray<readonly [number, number]> = [
  [222, 132],
  [232, 104],
  [243, 118],
  [255, 76],
  [268, 92],
  [282, 52],
];

const SUN_X = 824;
const SUN_R = 118;

export function Horizon() {
  const raw = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const sunId = `nc-sun-${raw}`;
  const clipId = `nc-sky-${raw}`;
  return (
    <div className="nc-horizon" aria-hidden="true">
      <svg className="nc-horizon-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMax slice" focusable="false">
        <defs>
          <linearGradient id={sunId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" className="nc-sun-top" />
            <stop offset="1" className="nc-sun-low" />
          </linearGradient>
          <clipPath id={clipId}>
            <rect x="0" y="0" width={W} height={SEA} />
          </clipPath>
        </defs>
        <g clipPath={`url(#${clipId})`}>
          <circle className="nc-sun" cx={SUN_X} cy={SEA - 6} r={SUN_R} fill={`url(#${sunId})`} />
        </g>
        {CLOUDS.map(([x, y, w, h]) => (
          <rect key={`${x}-${y}`} className="nc-cloud" x={x} y={y} width={w} height={h} rx={h / 2} />
        ))}
        <path className="nc-skyline" d={SKYLINE} />
        <rect className="nc-sea" x="0" y={SEA} width={W} height={H - SEA} />
        {GLINTS.map(([y, half], i) => (
          <rect key={y} className={i % 2 ? 'nc-glint nc-glint--hot' : 'nc-glint'} x={SUN_X - half} y={y} width={half * 2} height="3" rx="1.5" />
        ))}
        <path className="nc-shore" d={`M0 ${H} V286 C180 276 330 284 470 292 C560 297 640 ${H} 700 ${H}Z`} />
        <path className="nc-shore" d={`M${W} ${H} V284 C1100 278 1010 286 930 294 C900 297 880 ${H} 860 ${H}Z`} />
        {PALMS.map((p) => (
          <g key={p.x} className="nc-palm" transform={`translate(${p.x} ${p.y})`}>
            <path d={trunk(p)} />
            <path d={CROWN} transform={`translate(${p.lean} ${-p.h}) scale(${r1(p.h / 200)})`} />
          </g>
        ))}
      </svg>
    </div>
  );
}
