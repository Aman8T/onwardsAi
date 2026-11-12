import type { MouseEvent, ReactNode } from 'react';

/**
 * The hand-drawn pieces of the materials kit (design.md 7b; classes in styles/materials.css and the end of global.css).
 * They mark what the person did or wrote, so they are used on notebook paper and nowhere near a measured value.
 * Nothing here computes anything: every state is passed in from a record.
 */

const BOX = [
  'M4.2 4.6c5-.7 10.2-.5 15.4-.1.4 5 .3 10-.1 15.1-5 .6-10.1.5-15.2.1C3.9 14.7 3.8 9.6 4.2 4.6z',
  'M4.5 4.2c5 .3 10-.2 15 .4-.3 5 .2 10.2-.3 15.1-5.1-.2-10 .4-15-.2.5-5-.1-10.2.3-15.3z',
] as const;
const CHECK = 'M6.5 12.5c1.8 1.6 3.2 3.4 4.4 5.4 2.2-5.6 5.6-10.4 10.6-14.6';
const HALF = 'M7 18.5C10.5 13.5 13.5 9.5 17.5 5';
const STRIKE = ['M0 5C18 2 42 7 66 3.5S92 3 100 4.5', 'M0 4C22 6 40 2 64 5S90 3 100 4'] as const;

export type TickState = 'todo' | 'half' | 'done';

/**
 * A tick box drawn by hand. With `onToggle` it is a button the person presses; without it, it is a mark the app set from
 * a dated record, and `label` says what it stands for. `index` picks one of two box shapes, so a list does not repeat.
 */
export function HandTick({ state, label, index = 0, onToggle }: { state: TickState; label: string; index?: number; onToggle?: (done: boolean) => void }) {
  const mark = (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path className="bx" d={BOX[index % 2]} />
      <path className="hf" d={HALF} />
      <path className="ck" d={CHECK} />
    </svg>
  );
  if (!onToggle) {
    const said = state === 'done' ? 'done' : state === 'half' ? 'partly done' : 'not done yet';
    return (
      <span className={`tick${state === 'done' ? ' is-done' : state === 'half' ? ' is-half' : ''}`} role="img" aria-label={`${label}: ${said}`}>
        {mark}
      </span>
    );
  }
  return (
    <button type="button" className="tick" aria-pressed={state === 'done' ? true : state === 'half' ? 'mixed' : false} aria-label={label} onClick={() => onToggle(state !== 'done')}>
      {mark}
    </button>
  );
}

/** The pen stroke through a finished line. Put it inside `.m-line-t`; it shows when the row is `.m-line.is-done`. */
export function PenStrike({ index = 0 }: { index?: number }) {
  return (
    <svg className="m-strike" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
      <path d={STRIKE[index % 2]} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/**
 * One ruled line of a notebook page: an optional tick, the thing in pen, the fact it rests on in small mono, and an
 * optional end piece. With `href` (and `onGo` for in-app navigation) the written line is a link; with only `onGo` it is
 * a button.
 */
export function PageLine({
  state = 'todo',
  index = 0,
  title,
  cite,
  href,
  onGo,
  onToggle,
  tick = true,
  wrap = false,
  end,
}: {
  state?: TickState;
  index?: number;
  title: string;
  cite?: ReactNode;
  href?: string;
  onGo?: () => void;
  onToggle?: (done: boolean) => void;
  tick?: boolean;
  wrap?: boolean;
  end?: ReactNode;
}) {
  const cls = `pen m-line-t${wrap ? ' is-wrap' : ''}`;
  const inner = (
    <>
      {title}
      {wrap ? null : <PenStrike index={index} />}
    </>
  );
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    if (!onGo || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onGo();
  };
  return (
    <li className={`m-line${tick ? '' : ' is-bare'}${state === 'done' ? ' is-done' : ''}`}>
      {tick ? <HandTick state={state} label={title} index={index} onToggle={onToggle} /> : null}
      <span className="m-line-main">
        {href ? (
          <a className={cls} href={href} onClick={click}>
            {inner}
          </a>
        ) : onGo ? (
          <button type="button" className={cls} onClick={onGo}>
            {inner}
          </button>
        ) : (
          <span className={cls}>{inner}</span>
        )}
        {cite ? <span className="m-cite">{cite}</span> : null}
      </span>
      {end ? <span className="m-line-end">{end}</span> : null}
    </li>
  );
}

export type Pip = 'on' | 'half' | 'no';

/**
 * A row of circles, one per stage, requirement or question: filled is held (reached, covered, defended), half is partly,
 * dashed is not yet. It is a count the code made, shown as marks instead of a percentage. `label` must say the same
 * thing in words. `now` rings one circle (the current stage); `line` draws a rail through the row, which needs a flat
 * ground (set `--m-ground` on a parent if that ground is not `--bg`).
 */
export function Pips({ pips, label, r = 8, gap = 10, now, line = false }: { pips: readonly Pip[]; label: string; r?: number; gap?: number; now?: number; line?: boolean }) {
  const pad = now === undefined ? 1 : 7;
  const step = r * 2 + gap;
  const width = pad * 2 + r * 2 + step * Math.max(0, pips.length - 1);
  const height = (r + pad) * 2;
  const cy = r + pad;
  const cx = (i: number) => pad + r + i * step;
  return (
    <svg className={`m-pips${line ? ' has-line' : ''}`} viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={label}>
      {line && pips.length > 1 ? <line x1={cx(0)} y1={cy} x2={cx(pips.length - 1)} y2={cy} /> : null}
      {pips.map((p, i) => {
        const x = cx(i);
        const big = i === now ? r + 2 : r;
        return (
          <g key={i}>
            {p === 'on' ? <circle className="on" cx={x} cy={cy} r={big} /> : p === 'half' ? <circle className="of" cx={x} cy={cy} r={big} /> : <circle className="no" cx={x} cy={cy} r={big} />}
            {p === 'half' ? <path className="hf" d={`M${x} ${cy - big}a${big} ${big} 0 0 0 0 ${big * 2}z`} /> : null}
            {i === now ? <circle className="halo" cx={x} cy={cy} r={big + 5} /> : null}
          </g>
        );
      })}
    </svg>
  );
}
