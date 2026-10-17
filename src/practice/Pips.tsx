/**
 * The verdict pips: one square per question, in order. Filled is Defended, half filled is Shaky, a red outline is
 * Couldn't defend, a dashed outline is Not assessed. Beside them a count line, "Defended 3 of 5 · Shaky 1 · Couldn't
 * defend 1".
 *
 * It is a picture of counts and never a score: no total to reach, no percentage, no colour scale. The squares are told
 * apart by fill and outline as well as by hue, and the same facts are always in the words. Not assessed never counts
 * against the person, so it is a dashed square and a count, never a red one.
 */
import type { Verdict, VerdictCounts } from '../../shared/interview';
import { VERDICT_LABEL } from './words';

/** More squares than this add nothing a count does not say; the line still gives every number. */
const MAX_PIPS = 30;

/** The class suffix of each verdict: `pc-pip-defended`, `pc-v-shaky`, … */
export const VERDICT_KEY: Record<Verdict, 'defended' | 'shaky' | 'couldnt' | 'na'> = {
  defended: 'defended',
  shaky: 'shaky',
  couldnt: 'couldnt',
  not_assessed: 'na',
};

export function countsOf(verdicts: readonly Verdict[]): VerdictCounts {
  const counts: VerdictCounts = { defended: 0, shaky: 0, couldnt: 0, notAssessed: 0 };
  for (const v of verdicts) {
    if (v === 'defended') counts.defended += 1;
    else if (v === 'shaky') counts.shaky += 1;
    else if (v === 'couldnt') counts.couldnt += 1;
    else counts.notAssessed += 1;
  }
  return counts;
}

export const totalOf = (c: VerdictCounts): number => c.defended + c.shaky + c.couldnt + c.notAssessed;

/** "Defended 3 of 5 · Shaky 1 · Couldn't defend 1 · Not assessed 2": counts only, a word for each. */
export function countsLine(c: VerdictCounts): string {
  const parts = [`${VERDICT_LABEL.defended} ${c.defended} of ${totalOf(c)}`];
  if (c.shaky > 0) parts.push(`${VERDICT_LABEL.shaky} ${c.shaky}`);
  if (c.couldnt > 0) parts.push(`${VERDICT_LABEL.couldnt} ${c.couldnt}`);
  if (c.notAssessed > 0) parts.push(`${VERDICT_LABEL.not_assessed} ${c.notAssessed}`);
  return parts.join(' · ');
}

/** The same for a screen reader: "5 questions: 3 defended, 1 shaky, 1 couldn't defend". */
export function countsLabel(c: VerdictCounts): string {
  const total = totalOf(c);
  const word = (v: Verdict) => VERDICT_LABEL[v].toLowerCase();
  const parts: string[] = [];
  if (c.defended > 0) parts.push(`${c.defended} ${word('defended')}`);
  if (c.shaky > 0) parts.push(`${c.shaky} ${word('shaky')}`);
  if (c.couldnt > 0) parts.push(`${c.couldnt} ${word('couldnt')}`);
  if (c.notAssessed > 0) parts.push(`${c.notAssessed} ${word('not_assessed')}`);
  return `${total} ${total === 1 ? 'question' : 'questions'}${parts.length > 0 ? `: ${parts.join(', ')}` : ''}`;
}

/** One square. Decorative: whatever carries it says the verdict in words. */
export function Pip({ verdict, size = 'sm' }: { verdict: Verdict; size?: 'sm' | 'lg' }) {
  return <span className={`pc-pip pc-pip-${VERDICT_KEY[verdict]}${size === 'lg' ? ' is-lg' : ''}`} aria-hidden="true" />;
}

/** The squares of one interview, in the order the questions were asked. */
export function VerdictPips({ verdicts, size = 'sm', label }: { verdicts: readonly Verdict[]; size?: 'sm' | 'lg'; label?: string }) {
  if (verdicts.length === 0) return null;
  return (
    <span className={`pc-pips${size === 'lg' ? ' is-lg' : ''}`} role="img" aria-label={label ?? countsLabel(countsOf(verdicts))}>
      {verdicts.slice(0, MAX_PIPS).map((v, i) => (
        <Pip key={i} verdict={v} size={size} />
      ))}
    </span>
  );
}

/** The squares and the count line together, as a past session and a role in "Coming up" show them. */
export function PipsLine({ verdicts, counts }: { verdicts: readonly Verdict[]; counts?: VerdictCounts }) {
  const c = counts ?? countsOf(verdicts);
  if (totalOf(c) === 0) return null;
  return (
    <span className="pc-pipsline">
      <VerdictPips verdicts={verdicts} label={countsLabel(c)} />
      <span className="pc-pipsline-text">{countsLine(c)}</span>
    </span>
  );
}

/** A verdict as a word in a chip, with its square: Defended, Shaky, Couldn't defend, Not assessed. */
export function VerdictChip({ verdict }: { verdict: Verdict }) {
  return (
    <span className={`pc-v pc-v-${VERDICT_KEY[verdict]}`}>
      <Pip verdict={verdict} />
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

/** What the four squares mean, for a screen that shows several. */
export function PipsKey() {
  const order: Verdict[] = ['defended', 'shaky', 'couldnt', 'not_assessed'];
  return (
    <div className="pc-pipskey" role="group" aria-label="What the squares mean">
      {order.map((v) => (
        <span key={v}>
          <Pip verdict={v} />
          {VERDICT_LABEL[v]}
        </span>
      ))}
    </div>
  );
}
