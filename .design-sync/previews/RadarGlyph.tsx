import { RadarGlyph } from 'tayyari';

const RIYA = [81, 85, 58, 36, 55, 55];
const BALANCED = [70, 72, 68, 74, 69, 71];
const PEAKED = [92, 40, 35, 30, 45, 38];

const row = { display: 'flex', alignItems: 'center', gap: 'var(--s3)' } as const;

/** The 20 px sparkline beside a rail row: the person's shape at a glance. */
export const InARow = () => (
  <ul style={{ display: 'grid', gap: 'var(--s3)', margin: 0, padding: 0, listStyle: 'none', maxWidth: 320 }}>
    {[
      ['Riya Kapoor', RIYA],
      ['Balanced profile', BALANCED],
      ['One strong peak', PEAKED],
    ].map(([name, values]) => (
      <li key={name as string} style={{ ...row, justifyContent: 'space-between' }}>
        <span style={row}>
          <RadarGlyph values={values as number[]} />
          <span>{name as string}</span>
        </span>
        <span className="numeral" style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-1)' }}>
          {Math.round((values as number[]).reduce((a, b) => a + b, 0) / (values as number[]).length)}
        </span>
      </li>
    ))}
  </ul>
);

/** Larger glyphs for a card header or a comparison strip. */
export const Sizes = () => (
  <div style={{ ...row, gap: 'var(--s5)' }}>
    {[20, 32, 48, 64].map((size) => (
      <div key={size} style={{ display: 'grid', justifyItems: 'center', gap: 'var(--s2)' }}>
        <RadarGlyph values={RIYA} size={size} />
        <span className="numeral" style={{ fontSize: 'var(--fs-0)', color: 'var(--text-muted)' }}>
          {size}
        </span>
      </div>
    ))}
  </div>
);

/** Fewer than three values: the glyph falls back to a plain ring. */
export const TooFewAxes = () => (
  <div style={row}>
    <RadarGlyph values={[64, 40]} size={32} />
    <span className="kicker">2 areas: ring only</span>
  </div>
);
