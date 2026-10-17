/**
 * "Each claim, at its best": every line of the person's evidence that an interview asked about, one column for each
 * session it was asked in, and its best verdict. The best counts, so a Shaky claim can only get better; a claim a session
 * did not ask about says "Not asked", which is not a verdict. A wide table in a scroll box that scrolls sideways on a
 * phone, with real header cells.
 */
import type { ClaimBest, PracticeHistory, Verdict } from '../../../shared/interview';
import { VerdictChip } from '../Pips';
import { KIND_LABEL } from '../words';
import { dateOf } from '../hub/words';

/** The most sessions shown as columns: the latest ones. */
const MAX_COLUMNS = 4;

/** Weakest first, so what still needs work is read first; a claim that was only ever Not assessed sits after those. */
const NEEDS_WORK: Record<Verdict, number> = { couldnt: 0, shaky: 1, not_assessed: 2, defended: 3 };

/** The best of several verdicts: Defended over Shaky over Couldn't defend over Not assessed. */
const STRENGTH: Record<Verdict, number> = { defended: 3, shaky: 2, couldnt: 1, not_assessed: 0 };

interface Column {
  sessionId: string;
  date: string;
  label: string;
}

/** The sessions the claims were asked in, oldest first, the latest few. */
export function columnsOf(history: Pick<PracticeHistory, 'sessions' | 'claims'>): { columns: Column[]; clipped: boolean } {
  const byId = new Map(history.sessions.map((s) => [s.id, s]));
  const seen = new Map<string, Column>();
  for (const claim of history.claims) {
    for (const mark of claim.marks) {
      if (seen.has(mark.sessionId)) continue;
      const session = byId.get(mark.sessionId);
      seen.set(mark.sessionId, { sessionId: mark.sessionId, date: mark.date, label: `${dateOf(mark.date)} · ${session ? KIND_LABEL[session.kind] : 'Practice'}` });
    }
  }
  const all = [...seen.values()].sort((a, b) => a.date.localeCompare(b.date));
  return { columns: all.slice(-MAX_COLUMNS), clipped: all.length > MAX_COLUMNS };
}

/** The claims in the order the table reads them: those that still need work first, then the newest best. */
export function orderClaims(claims: readonly ClaimBest[]): ClaimBest[] {
  return [...claims].sort((a, b) => NEEDS_WORK[a.best] - NEEDS_WORK[b.best] || b.on.localeCompare(a.on));
}

/** What a claim scored in one session, if it was asked there. */
function inSession(claim: ClaimBest, sessionId: string): Verdict | undefined {
  const marks = claim.marks.filter((m) => m.sessionId === sessionId);
  if (marks.length === 0) return undefined;
  return marks.reduce<Verdict>((best, m) => (STRENGTH[m.verdict] > STRENGTH[best] ? m.verdict : best), 'not_assessed');
}

export function ClaimsTable({ history }: { history: Pick<PracticeHistory, 'sessions' | 'claims'> }) {
  const { columns, clipped } = columnsOf(history);
  const claims = orderClaims(history.claims);
  return (
    <>
      <div className="pc-tablewrap scroll" role="region" aria-label="Each claim by session, scrolls sideways on a small screen" tabIndex={0}>
        <table className="pc-table">
          <thead>
            <tr>
              <th scope="col" className="kicker pc-th-claim">
                Claim
              </th>
              {columns.map((c) => (
                <th key={c.sessionId} scope="col" className="kicker">
                  {c.label}
                </th>
              ))}
              <th scope="col" className="kicker">
                Best
              </th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim, i) => (
              <tr key={`${claim.source}|${claim.text}|${i}`}>
                <th scope="row" className="pc-td-claim">
                  <span className="pc-fact">“{claim.text}”</span>
                  {claim.source && <span className="pc-cite">{claim.source}</span>}
                </th>
                {columns.map((c) => {
                  const v = inSession(claim, c.sessionId);
                  return <td key={c.sessionId}>{v ? <VerdictChip verdict={v} /> : <span className="pc-muted">Not asked</span>}</td>;
                })}
                <td>
                  <div className="pc-best">
                    <VerdictChip verdict={claim.best} />
                    {claim.best !== 'not_assessed' && claim.on && <span className="pc-muted pc-small">on {dateOf(claim.on)}</span>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="pc-muted pc-small">
        The best verdict counts. A Shaky one can only get better.{clipped ? ` Showing the latest ${MAX_COLUMNS} sessions.` : ''}
      </p>
    </>
  );
}
