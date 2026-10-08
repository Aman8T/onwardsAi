/**
 * A 100x54 thumbnail of a candidate look, drawn from its spec (hero variant, proof strip, project grid)
 * the way the Editor board draws Signature, Narrative and Builder. Shapes only; colours come from the
 * classes in portfolio-editor.css, which read the tokens of the surrounding `.pf-scope`.
 */
import type { PortfolioSpec } from '../../../shared/portfolio';

const VALUES = [0.84, 0.92, 0.62, 0.4, 0.58, 0.58];

const f = (n: number) => String(Math.round(n * 10) / 10);

/** Hexagonal radar: concentric rings, the data polygon, as SVG path data. */
function radar(cx: number, cy: number, r: number) {
  const point = (i: number, scale: number) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / VALUES.length;
    return `${f(cx + Math.cos(a) * r * scale)} ${f(cy + Math.sin(a) * r * scale)}`;
  };
  const poly = (scaleOf: (i: number) => number) => `${VALUES.map((_, i) => `${i ? 'L' : 'M'}${point(i, scaleOf(i))}`).join(' ')} Z`;
  return {
    rings: `${poly(() => 0.5)} ${poly(() => 1)}`,
    data: poly((i) => VALUES[i]),
  };
}

type Box = [x: number, y: number, w: number, h: number];

function Bar({ box, tone, r = 1 }: { box: Box; tone: 'd' | 'l' | 'a' | 'o' | 't'; r?: number }) {
  return <rect className={`pe-m-${tone}`} x={box[0]} y={box[1]} width={box[2]} height={box[3]} rx={r} />;
}

function Radar({ cx, cy, r }: { cx: number; cy: number; r: number }) {
  const g = radar(cx, cy, r);
  return (
    <>
      <path className="pe-m-r" d={g.rings} />
      <path className="pe-m-p" d={g.data} />
    </>
  );
}

export function LookThumb({ spec }: { spec: PortfolioSpec }) {
  const shown = spec.blocks.filter((b) => b.visible);
  const hero = shown.find((b) => b.kind === 'hero')?.variant ?? 'split';
  const hasProof = shown.some((b) => b.kind === 'proof');
  const cards = shown.some((b) => b.kind === 'projects' && b.variant === 'grid');

  return (
    <svg className="pe-mini-svg" viewBox="0 0 100 54" aria-hidden="true" focusable="false">
      {hero === 'statement' && (
        <>
          <Bar box={[8, 7, 60, 8]} tone="d" />
          <Bar box={[8, 18, 40, 2.2]} tone="l" />
          <Radar cx={50} cy={36} r={11.3} />
          <Bar box={[8, 30, 18, 2]} tone="d" r={0.8} />
          <Bar box={[8, 35, 14, 1.6]} tone="l" r={0.8} />
          <Bar box={[8, 40, 16, 1.6]} tone="l" r={0.8} />
          <Bar box={[72, 30, 20, 2]} tone="d" r={0.8} />
          <Bar box={[72, 35, 16, 1.6]} tone="l" r={0.8} />
          <Bar box={[72, 40, 18, 1.6]} tone="l" r={0.8} />
        </>
      )}
      {hero === 'centered' && (
        <>
          <Bar box={[26, 8, 48, 6]} tone="d" />
          <Bar box={[30, 18, 40, 2.2]} tone="l" />
          <Radar cx={50} cy={36} r={11.3} />
        </>
      )}
      {hero === 'index' && (
        <>
          <Bar box={[8, 7, 14, 2.5]} tone="d" r={0.8} />
          <Bar box={[8, 13, 58, 5]} tone="d" />
          <Bar box={[8, 21, 44, 5]} tone="d" />
          <Radar cx={84} cy={16} r={8.7} />
          {cards ? (
            <>
              <Bar box={[8, 33, 40, 14]} tone="t" r={2} />
              <Bar box={[11, 37, 20, 2]} tone="l" r={0.8} />
              <Bar box={[11, 41, 14, 1.6]} tone="l" r={0.8} />
              <Bar box={[52, 33, 40, 14]} tone="t" r={2} />
              <Bar box={[55, 37, 20, 2]} tone="l" r={0.8} />
              <Bar box={[55, 41, 14, 1.6]} tone="l" r={0.8} />
            </>
          ) : (
            <>
              <Bar box={[8, 33, 40, 2.2]} tone="l" />
              <Bar box={[8, 38, 34, 2.2]} tone="l" />
              <Bar box={[8, 43, 28, 2.2]} tone="l" />
            </>
          )}
        </>
      )}
      {hero === 'split' && (
        <>
          <Bar box={[8, 10, 34, 6]} tone="d" />
          <Bar box={[8, 21, 28, 2.2]} tone="l" />
          <Bar box={[8, 26, 22, 2.2]} tone="l" />
          <Bar box={[8, 32, 14, 5]} tone="a" r={1.5} />
          <Bar box={[25, 32, 12, 5]} tone="o" r={1.5} />
          <Radar cx={72} cy={24} r={13} />
          {hasProof ? (
            [8, 30, 52, 74].map((x) => (
              <g key={x}>
                <Bar box={[x, 44, 9, 2.6]} tone="d" r={0.8} />
                <Bar box={[x, 48.5, 15, 1.4]} tone="l" r={0.7} />
              </g>
            ))
          ) : (
            <>
              <Bar box={[8, 44, 40, 2]} tone="l" />
              <Bar box={[8, 48, 30, 2]} tone="l" />
            </>
          )}
        </>
      )}
    </svg>
  );
}
