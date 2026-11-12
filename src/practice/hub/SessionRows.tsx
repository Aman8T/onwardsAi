/**
 * The rows of past practice, newest first: the day, what was practised, the squares with their count line, and the way
 * back to its debrief (or, for an interview that was not finished, back into the room). The hub's "Your practice" and
 * Progress's "Sessions" show the same rows.
 */
import type { SessionSummary } from '../../../shared/interview';
import { CirclesLine } from '../Pips';
import { KIND_LABEL } from '../words';
import type { PracticeNav } from './nav';
import { dateOf, sessionSubtitle } from './words';

export function SessionRows({ sessions, nav }: { sessions: readonly SessionSummary[]; nav: PracticeNav }) {
  return (
    <ul className="pc-rows">
      {sessions.map((s) => {
        const done = s.status === 'done';
        const when = dateOf(s.createdAt);
        return (
          <li key={s.id} className="pc-row">
            <span className="pc-row-date numeral">{when}</span>
            <div className="pc-row-main">
              <span className="pc-strong">{KIND_LABEL[s.kind]}</span>
              <span className="pc-muted pc-row-sub">{sessionSubtitle(s)}</span>
            </div>
            <div className="pc-row-result">{done ? <CirclesLine verdicts={s.verdicts} counts={s.counts} /> : <span className="chip">Not finished</span>}</div>
            {done ? (
              <button type="button" className="btn btn-sm pc-btn-sm" aria-label={`Open debrief, ${KIND_LABEL[s.kind]}, ${when}`} onClick={() => nav.debrief(s.id)}>
                Open debrief
              </button>
            ) : (
              <button type="button" className="btn btn-sm pc-btn-sm" aria-label={`Carry on, ${KIND_LABEL[s.kind]}, ${when}`} onClick={() => nav.room(s.id)}>
                Carry on
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
