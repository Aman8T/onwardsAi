/**
 * The pips: one 10 px square per requirement the read checked, in order. Filled is met, half filled is partly met, a
 * red outline is not on the resume. Beside them a count line, "3 asks checked · 2 met · 1 not on your resume".
 *
 * It is a picture of counts and never a score: there is no total to reach, no percentage and no colour scale. The
 * squares are told apart by fill and outline as well as by hue, and the same facts are always in the text.
 */
import type { AskCounts } from '../../../shared/jobsearch';
import '../../styles/discover-more.css';
import { asksLabel, asksLine, asksTotal, cleanAsks } from './words';

/** More squares than this add nothing a count does not say; the line still gives every number. */
const MAX_PIPS = 30;

export function Pips({ asks: raw }: { asks: AskCounts | undefined }) {
  const asks = cleanAsks(raw);
  if (asksTotal(asks) === 0) return null;
  const many = (n: number, kind: 'met' | 'part' | 'gap') => Array.from({ length: n }, () => kind);
  const kinds = [...many(asks.met, 'met'), ...many(asks.partly, 'part'), ...many(asks.notShown, 'gap')].slice(0, MAX_PIPS);
  return (
    <span className="dm-pips" role="img" aria-label={asksLabel(asks)}>
      {kinds.map((k, i) => (
        <span key={i} className={`dm-pip dm-pip-${k}`} aria-hidden="true" />
      ))}
    </span>
  );
}

/** The squares and the count line together, as the role summary and the compare table show them. */
export function AskPips({ asks }: { asks: AskCounts | undefined }) {
  return (
    <div className="dm-askpips">
      <Pips asks={asks} />
      <span className="dm-askpips-line">{asksLine(asks)}</span>
    </div>
  );
}

/** What the three squares mean, for a table of roles. */
export function PipsKey() {
  return (
    <div className="dm-pipskey" role="group" aria-label="What the squares mean">
      <span>
        <span className="dm-pip dm-pip-met" aria-hidden="true" />
        met
      </span>
      <span>
        <span className="dm-pip dm-pip-part" aria-hidden="true" />
        partly
      </span>
      <span>
        <span className="dm-pip dm-pip-gap" aria-hidden="true" />
        not on your resume
      </span>
    </div>
  );
}
