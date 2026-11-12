/**
 * The month as a person would rule it on a torn-off calendar page: every grid line overshoots its corner a little and
 * bows a little, a day with a dated record gets a hand-drawn cross, two or more get a scribble under it, today is ringed.
 *
 * Pure and deterministic: the wobble comes from a small seeded generator, the seed is derived from the year and month, and
 * each day draws from its own sub-seed, so one more record never moves the crosses next to it. Same inputs, same strings.
 * The days are Monday first, six rows of seven, from the Monday on or before the 1st.
 */

export const CAL_COLS = 7;
export const CAL_ROWS = 6;
export const CAL_CELL_W = 32;
export const CAL_CELL_H = 22;
export const CAL_W = CAL_CELL_W * CAL_COLS;
export const CAL_H = CAL_CELL_H * CAL_ROWS;
/** The grid's drawing box: the lines overshoot the grid by 6 on every side. */
export const CAL_VIEWBOX = `-6 -6 ${CAL_W + 12} ${CAL_H + 12}`;

export interface CalDay {
  /** Local day, YYYY-MM-DD. */
  key: string;
  n: number;
  inMonth: boolean;
  today: boolean;
  /** 0 nothing, 1 one thing, 2 two or more. */
  level: 0 | 1 | 2;
  /** The cross, an SVG path in the cell's 32 x 22 box; '' when the day has no record. */
  mark: string;
  /** The scribble under a day with two or more; '' otherwise. */
  fill: string;
  /** The ring round today; '' otherwise. */
  ring: string;
}

export interface MonthPage {
  year: number;
  /** 0 to 11. */
  month: number;
  /** The hand-ruled grid, one SVG path. */
  lines: string;
  days: CalDay[];
}

/** mulberry32: a number in [0, 1) per call, the same sequence for the same seed. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The page's seed: one number per year and month (November 2026 is 20261100). */
export const monthSeed = (year: number, month: number): number => year * 10000 + (month + 1) * 100;

const f = (n: number): string => n.toFixed(1);
const RING = 'M18 2C9 1 2.5 5 3 11.5c.4 6 7 9 15 8.5 7.5-.5 11.5-4.5 11-9.5C28.5 5 23 1.5 14.5 3';

const pad = (n: number) => String(n).padStart(2, '0');
const keyOf = (y: number, m: number, d: number) => `${y}-${pad(m + 1)}-${pad(d)}`;

/** The ruled grid for a seed. */
function ruledLines(seed: number): string {
  const r = seededRandom(seed);
  const j = (n: number) => (r() - 0.5) * 2 * n;
  let lines = '';
  const stroke = (x1: number, y1: number, x2: number, y2: number) => {
    const horiz = y1 === y2;
    const a = 2 + r() * 3.5;
    const b = 2 + r() * 3.5;
    const sx = x1 - (horiz ? a : 0) + j(0.5);
    const sy = y1 - (horiz ? 0 : a) + j(0.5);
    const ex = x2 + (horiz ? b : 0) + j(0.5);
    const ey = y2 + (horiz ? 0 : b) + j(0.5);
    const mx = (sx + ex) / 2 + (horiz ? 0 : j(1.3));
    const my = (sy + ey) / 2 + (horiz ? j(1.3) : 0);
    lines += `M${f(sx)} ${f(sy)}Q${f(mx)} ${f(my)} ${f(ex)} ${f(ey)}`;
  };
  for (let row = 0; row <= CAL_ROWS; row++) stroke(0, row * CAL_CELL_H, CAL_W, row * CAL_CELL_H);
  for (let col = 0; col <= CAL_COLS; col++) stroke(col * CAL_CELL_W, 0, col * CAL_CELL_W, CAL_H);
  return lines;
}

/** One cross and one scribble, drawn from the day's own seed. */
function marks(seed: number): { mark: string; fill: string } {
  const r = seededRandom(seed);
  const j = (n: number) => (r() - 0.5) * 2 * n;
  const mark =
    `M${f(9 + j(1.5))} ${f(4 + j(1))}Q${f(16 + j(2))} ${f(11 + j(1.5))} ${f(23 + j(1.5))} ${f(18 + j(1))}` +
    `M${f(23 + j(1.5))} ${f(4 + j(1))}Q${f(16 + j(2))} ${f(11 + j(1.5))} ${f(9 + j(1.5))} ${f(18 + j(1))}`;
  let fill = `M${f(5 + j(1))} ${f(4 + j(0.6))}`;
  for (let i = 1; i <= 7; i++) fill += `L${f((i % 2 ? 27 : 5) + j(1.4))} ${f(4 + i * 2 + j(0.6))}`;
  return { mark, fill };
}

/**
 * The page for the month holding `now`. `days` maps a local day (YYYY-MM-DD) to how many different things were done
 * that day (see deskModel.activeDays); a day absent from it has none.
 */
export function monthPage(now: number, days: ReadonlyMap<string, number>): MonthPage {
  const at = new Date(now);
  const year = at.getFullYear();
  const month = at.getMonth();
  const seed = monthSeed(year, month);
  const todayKey = keyOf(year, month, at.getDate());
  // Monday on or before the 1st
  const lead = (new Date(year, month, 1).getDay() + 6) % 7;
  const out: CalDay[] = [];
  for (let i = 0; i < CAL_COLS * CAL_ROWS; i++) {
    const d = new Date(year, month, 1 - lead + i);
    const key = keyOf(d.getFullYear(), d.getMonth(), d.getDate());
    const count = days.get(key) ?? 0;
    const level: 0 | 1 | 2 = count >= 2 ? 2 : count === 1 ? 1 : 0;
    const drawn = level > 0 ? marks(seed + 1 + d.getFullYear() * 400 + d.getMonth() * 32 + d.getDate()) : { mark: '', fill: '' };
    out.push({
      key,
      n: d.getDate(),
      inMonth: d.getMonth() === month,
      today: key === todayKey,
      level,
      mark: drawn.mark,
      fill: level === 2 ? drawn.fill : '',
      ring: key === todayKey ? RING : '',
    });
  }
  return { year, month, lines: ruledLines(seed), days: out };
}

/** "the 1st, 5th and today, the 10th": the page's words for a screen reader, from the days that carry a cross. */
export function monthSpoken(page: MonthPage): string {
  const name = new Date(page.year, page.month, 1).toLocaleDateString('en-GB', { month: 'long' });
  const ord = (n: number) => {
    const r10 = n % 10;
    const r100 = n % 100;
    return r10 === 1 && r100 !== 11 ? `${n}st` : r10 === 2 && r100 !== 12 ? `${n}nd` : r10 === 3 && r100 !== 13 ? `${n}rd` : `${n}th`;
  };
  const done = page.days.filter((d) => d.inMonth && d.level > 0);
  if (done.length === 0) return `${name}: no day with a record yet`;
  const list = done.map((d) => (d.today ? `today, the ${ord(d.n)}` : ord(d.n)));
  const last = list.pop()!;
  return `${name}: you did something here on ${list.length ? `${list.join(', ')} and ${last}` : last}`;
}
