import { Ring } from 'tayyari';

const row = { display: 'flex', alignItems: 'center', gap: 'var(--s4)' } as const;
const label = { fontSize: 'var(--fs-1)', color: 'var(--text-muted)' } as const;

/** A reading beside its numeral: the default 28 px ring. */
export const Reading = () => (
  <div style={row}>
    <Ring value={62} role="img" label="Match 62 out of 100" />
    <span>
      <span className="numeral">62</span>
      <span style={label}> / 100</span>
    </span>
  </div>
);

/** The three sizes the app uses; stroke stays hairline-thin as the ring grows. */
export const Sizes = () => (
  <div style={{ ...row, gap: 'var(--s6)' }}>
    {[
      [28, 1.5],
      [44, 1.5],
      [64, 2],
    ].map(([size, stroke]) => (
      <div key={size} style={{ display: 'grid', justifyItems: 'center', gap: 'var(--s2)' }}>
        <Ring value={74} size={size} stroke={stroke} />
        <span className="numeral" style={label}>
          {size}
        </span>
      </div>
    ))}
  </div>
);

/** Empty hides the arc entirely (no stray dot); full closes the circle. */
export const EmptyAndFull = () => (
  <div style={{ ...row, gap: 'var(--s6)' }}>
    {[0, 25, 50, 100].map((v) => (
      <div key={v} style={{ display: 'grid', justifyItems: 'center', gap: 'var(--s2)' }}>
        <Ring value={v} size={36} />
        <span className="numeral" style={label}>
          {v}
        </span>
      </div>
    ))}
  </div>
);

/** Live progress: `role="progressbar"` announces the value as it changes. */
export const Progress = () => (
  <div style={row}>
    <Ring value={40} size={36} role="progressbar" label="Reading your resume" />
    <span>
      Reading your resume
      <span className="kicker" style={{ display: 'block' }}>
        2 of 5 sources
      </span>
    </span>
  </div>
);
