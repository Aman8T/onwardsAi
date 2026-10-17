import { ValueLine } from 'tayyari';

const box = { display: 'grid', gap: 'var(--s2)', width: 280 } as const;
const head = { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' } as const;

/** A level under its label: the line fills the width it is given (2 px tall). */
export const Level = () => (
  <div style={box}>
    <div style={head}>
      <span>React</span>
      <span className="numeral" style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-1)' }}>
        90
      </span>
    </div>
    <ValueLine value={90} label="React, 90 out of 100" />
  </div>
);

/** The bullet-graph reading: level bold, benchmark as a thin tick. */
export const WithTarget = () => (
  <div style={box}>
    <div style={head}>
      <span>Design systems</span>
      <span className="numeral" style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-1)' }}>
        87 <span style={{ color: 'var(--text-faint)' }}>· role 70</span>
      </span>
    </div>
    <ValueLine value={87} target={70} label="Design systems, 87 against a target of 70" />
  </div>
);

/** Several rows, as in a skills list. */
export const Stack = () => (
  <ul style={{ ...box, listStyle: 'none', margin: 0, padding: 0, gap: 'var(--s4)' }}>
    {[
      ['Motion & animation', 91, 60],
      ['TypeScript', 86, 80],
      ['Accessibility (WCAG)', 76, 70],
      ['LLM API integration', 52, 65],
    ].map(([name, v, t]) => (
      <li key={name as string} style={{ display: 'grid', gap: 'var(--s2)' }}>
        <div style={head}>
          <span>{name as string}</span>
          <span className="numeral" style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-1)' }}>
            {v as number}
          </span>
        </div>
        <ValueLine value={v as number} target={t as number} />
      </li>
    ))}
  </ul>
);
