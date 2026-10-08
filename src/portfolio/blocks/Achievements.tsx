/**
 * Achievements: the resume's own award / recognition lines, verbatim (`achievement` facts).
 * Variants: numbers (the first figure in each line set large, the line itself as the label) and list
 * (plain rows with the period on the right). A line without a figure keeps its place in the grid with
 * an ordinal instead of a numeral.
 */
import type { Fact } from '../../../shared/portfolio';
import { factsOf, formatPeriod } from '../model';
import { EmptyBlock, MoreButton, SectionShell, useLimit, usePf } from './SectionShell';
import type { BlockProps } from './SectionShell';

/* ───────────── pulling a figure out of a line ───────────── */

const WORD_UNITS = 'ms|s|billion|million|crore|lakh|thousand|hours?|minutes?|days?|weeks?|months?|years?';
/** A number with an optional currency sign and unit: "4.1s", "9.4%", "₹1,200 crore", "2.1 billion". */
const FIGURE_BODY = String.raw`([₹$€£]?\d+(?:,\d{3})*(?:\.\d+)?)(?:(%)|(ms|s|x|k|m|b)\b|\s(${WORD_UNITS})\b)?`;
const FIGURE = String.raw`(?<![\w.])` + FIGURE_BODY;
const DURATION = /^(?:hours?|minutes?|days?|weeks?|months?|years?)$/i;

interface Figure {
  value: string;
  unit: string;
}

const isYear = (digits: string, unit: string): boolean => unit === '' && /^(?:19|20)\d{2}$/.test(digits);

/**
 * The most telling figure in a line: a range ("820 ms to 140 ms" becomes "820 → 140 ms") first, then a
 * percentage, currency or measured quantity, then any other number (durations last). Bare years are skipped.
 */
export function figureOf(line: string): Figure | undefined {
  let best: { fig: Figure; score: number } | undefined;
  for (const m of line.matchAll(new RegExp(FIGURE, 'gi'))) {
    const unit = m[2] ?? m[3] ?? m[4] ?? '';
    if (isYear(m[1], unit)) continue;
    const tail = line.slice((m.index ?? 0) + m[0].length);
    const to = new RegExp(String.raw`^\s*(?:→|->|to)\s*` + FIGURE_BODY, 'i').exec(tail);
    let fig: Figure;
    let score: number;
    if (to) {
      fig = { value: `${m[1]} → ${to[1]}`, unit: (to[2] ?? to[3] ?? to[4] ?? '') || unit };
      score = 3;
    } else {
      fig = { value: m[1], unit };
      score = unit === '%' || /^[₹$€£]/.test(m[1]) || (unit !== '' && !DURATION.test(unit)) ? 2 : DURATION.test(unit) ? 0 : 1;
    }
    if (!best || score > best.score) best = { fig, score };
  }
  return best?.fig;
}

function Numeral({ fig, n }: { fig: Figure | undefined; n: number }) {
  if (!fig) return <span className="pf-num pf-num--ord">{String(n + 1).padStart(2, '0')}</span>;
  const glued = fig.unit === '%';
  return (
    <span className={fig.value.length > 7 ? 'pf-num pf-num--sm' : 'pf-num'}>
      {fig.value}
      {glued ? '%' : null}
      {fig.unit && !glued ? <span className="pf-num-unit"> {fig.unit}</span> : null}
    </span>
  );
}

/* ───────────── variants ───────────── */

function Numbers({ facts }: { facts: Fact[] }) {
  const lim = useLimit(facts);
  return (
    <>
      <ul className="pf-nums">
        {lim.visible.map((f, i) => (
          <li key={f.id} className="pf-numcell">
            <Numeral fig={figureOf(f.title)} n={i} />
            <span className="pf-num-label">{f.title}</span>
          </li>
        ))}
      </ul>
      <MoreButton {...lim} />
    </>
  );
}

function List({ facts }: { facts: Fact[] }) {
  const lim = useLimit(facts);
  return (
    <>
      <ul className="pf-ach">
        {lim.visible.map((f) => {
          const meta = [f.org, formatPeriod(f)].filter(Boolean).join(' · ');
          return (
            <li key={f.id} className="pf-ach-row">
              <span className="pf-ach-title">{f.title}</span>
              {meta ? <span className="pf-ach-meta">{meta}</span> : null}
            </li>
          );
        })}
      </ul>
      <MoreButton {...lim} />
    </>
  );
}

export function Achievements({ block, index }: BlockProps) {
  const { content } = usePf();
  const facts = factsOf(content, 'achievement');
  const label = block.variant === 'numbers' ? 'Achievements' : 'Recognition';
  if (!facts.length) return <EmptyBlock block={block} index={index} label={label} />;
  return (
    <SectionShell block={block} index={index} label={label} meta="From the resume">
      {block.variant === 'numbers' ? <Numbers facts={facts} /> : <List facts={facts} />}
    </SectionShell>
  );
}
