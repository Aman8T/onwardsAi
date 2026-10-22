/**
 * What carries over (board 6 of the "Practice the interview" canvas), presentational: the person's practice arrives as a
 * prop, everything they can do goes out through `nav` and `onRemoveFact`.
 *
 *   Each claim, at its best   the evidence lines interviews asked about, with the best verdict each reached
 *   In your graph             a link: practice never adds a skill to the graph
 *   In Prepare                links to the prep pages of the roles that were practised
 *   Facts from you            what the person said in practice and kept, marked "From you", each with its moment
 *   Sessions                  every interview, newest first
 *
 * Counts and words only. Nothing here is shared with an employer.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { PracticeFact, PracticeHistory, RoleSource, SessionSummary } from '../../../shared/interview';
import { SessionRows } from '../hub/SessionRows';
import { ErrorNote } from '../hub/Notice';
import type { PracticeNav } from '../hub/nav';
import type { Load } from '../hub/useLoad';
import { NOTHING_SHARED, clock, dateOf, sentenceOf } from '../hub/words';
import { KEPT_LINE } from '../words';
import { ClaimsTable } from './ClaimsTable';
import '../../styles/practice.css';

export interface ProgressViewProps {
  history: Load<PracticeHistory>;
  onReload: () => void;
  nav: PracticeNav;
  /** Take a fact back; rejects with the reason when it cannot. */
  onRemoveFact: (fact: PracticeFact) => Promise<void>;
}

/** The roles that were practised, once each, newest first: the prep pages the Prepare panel points at. */
export function practisedRoles(sessions: readonly SessionSummary[]): { role: RoleSource; title: string }[] {
  const seen = new Set<string>();
  const out: { role: RoleSource; title: string }[] = [];
  for (const s of [...sessions].sort((a, b) => b.createdAt.localeCompare(a.createdAt))) {
    if (s.kind !== 'role' || !s.role || seen.has(s.role.jobId)) continue;
    seen.add(s.role.jobId);
    out.push({ role: s.role, title: s.title });
  }
  return out;
}

export function ProgressView({ history, onReload, nav, onRemoveFact }: ProgressViewProps) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const claimsId = useId();
  const graphId = useId();
  const prepareId = useId();
  const factsId = useId();
  const sessionsId = useId();

  // the page opens at its title, for a reader who arrived by keyboard or with a screen reader
  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  const data = history.status === 'ready' ? history.data : undefined;
  const roles = data ? practisedRoles(data.sessions) : [];

  return (
    <div className="pc-page pc-progress">
      <header className="pc-head">
        <button type="button" className="pc-back" onClick={nav.hub}>
          <span aria-hidden="true">←</span> Practice
        </button>
        <p className="kicker">Practice</p>
        <h1 className="serif pc-title" ref={titleRef} tabIndex={-1}>
          What your practice changes
        </h1>
        <p className="pc-lede">Practice feeds the rest of Leafout. {NOTHING_SHARED}</p>
      </header>

      {history.status === 'loading' && (
        <p className="pc-muted" role="status">
          Loading your practice…
        </p>
      )}
      {history.status === 'error' && <ErrorNote message={history.message} onRetry={onReload} onBack={nav.hub} />}

      {data && (
        <>
          <section className="panel pc-sec" aria-labelledby={claimsId}>
            <header className="pc-sec-head">
              <p className="kicker">Claims</p>
              <h2 className="serif pc-h2" id={claimsId}>
                Each claim, at its best
              </h2>
            </header>
            {data.claims.length === 0 ? (
              <p className="pc-muted">Nothing asked yet. Each line of your resume that an interview asks about shows up here, with the best verdict it reached.</p>
            ) : (
              <ClaimsTable history={data} onRedrill={nav.redrill} />
            )}
          </section>

          <div className="pc-pair">
            <section className="panel pc-sec" aria-labelledby={graphId}>
              <header className="pc-sec-head">
                <p className="kicker">Graph</p>
                <h2 className="serif pc-h2" id={graphId}>
                  In your graph
                </h2>
              </header>
              <p className="pc-muted pc-measure">Your graph shows what your evidence backs. Practice never adds a skill to it: it only records what you backed up out loud.</p>
              <div>
                <button type="button" className="btn btn-sm pc-btn-sm" onClick={nav.graph}>
                  Open your graph
                </button>
              </div>
            </section>

            <section className="panel pc-sec" aria-labelledby={prepareId}>
              <header className="pc-sec-head">
                <p className="kicker">Prepare</p>
                <h2 className="serif pc-h2" id={prepareId}>
                  In Prepare
                </h2>
              </header>
              {roles.length > 0 ? (
                <>
                  <p className="pc-muted pc-measure">A role's Prepare page holds its gaps and the stories to write down. Open the one you practised.</p>
                  <ul className="pc-links">
                    {roles.map((r) => (
                      <li key={r.role.jobId}>
                        <button type="button" className="btn btn-sm pc-btn-sm" onClick={() => nav.prepare(r.role)}>
                          Open Prepare · {r.title}
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <>
                  <p className="pc-muted pc-measure">Practise a role you saved in Discover, and its Prepare page is one click away.</p>
                  <div>
                    <button type="button" className="btn btn-sm pc-btn-sm" onClick={nav.applications}>
                      Open Your applications
                    </button>
                  </div>
                </>
              )}
            </section>
          </div>

          <FactsPanel headingId={factsId} facts={data.facts} onRemove={onRemoveFact} />

          <section className="panel pc-sec" aria-labelledby={sessionsId}>
            <header className="pc-sec-head">
              <p className="kicker">Past sessions</p>
              <h2 className="serif pc-h2" id={sessionsId}>
                Sessions
              </h2>
            </header>
            {data.sessions.length === 0 ? <p className="pc-muted">Nothing practised yet.</p> : <SessionRows sessions={data.sessions} nav={nav} />}
          </section>
        </>
      )}

      <footer className="pc-footer">
        <p>{KEPT_LINE}</p>
      </footer>
    </div>
  );
}

/* ───────────────────────── facts from you ───────────────────────── */

function FactsPanel({ headingId, facts, onRemove }: { headingId: string; facts: readonly PracticeFact[]; onRemove: (fact: PracticeFact) => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const keyOf = (f: PracticeFact) => `${f.sessionId}:${f.slotId}`;

  const remove = async (fact: PracticeFact) => {
    setBusy(keyOf(fact));
    setError(null);
    try {
      await onRemove(fact);
    } catch (err) {
      setError({ key: keyOf(fact), message: sentenceOf(err) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel pc-sec" aria-labelledby={headingId}>
      <header className="pc-sec-head">
        <p className="kicker">Profile</p>
        <h2 className="serif pc-h2" id={headingId}>
          Facts from you
        </h2>
      </header>
      {facts.length === 0 ? (
        <p className="pc-muted pc-measure">Nothing kept yet. When an answer says something your resume doesn't, the debrief offers to keep it here, marked From you.</p>
      ) : (
        <ul className="pc-facts">
          {facts.map((f) => (
            <li key={keyOf(f)} className="pc-factcard">
              <span className="chip pc-chip-have">From you</span>
              <p className="pc-factcard-text">“{f.text}”</p>
              <p className="pc-muted pc-small">
                Said in practice on {dateOf(f.date)} · <span className="pc-cite">You · {clock(f.at)}</span>
              </p>
              <div className="pc-factcard-foot">
                <span className="pc-muted">The match and your tailored resume can use it.</span>
                <button type="button" className="btn btn-quiet btn-sm pc-btn-sm" disabled={busy === keyOf(f)} onClick={() => void remove(f)}>
                  {busy === keyOf(f) ? 'Removing…' : 'Remove'}
                </button>
              </div>
              {error?.key === keyOf(f) && (
                <p className="pc-danger" role="alert">
                  {error.message}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
