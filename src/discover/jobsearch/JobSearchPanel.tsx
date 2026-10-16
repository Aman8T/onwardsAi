/**
 * "Search with AI", at the top of Discover's list: one quiet panel that runs an AI job search on the server and shows
 * what comes back, in the order it comes.
 *
 *   idle      "Search further" · what happens, in two sentences · the button
 *   running   a static "Searching…" line, a Stop button, the trail as it arrives, and the roles as each batch is checked
 *   done      what was found in words, the funnel (what was left after each stage), the server's note, Search again
 *   error     the server's sentence, and the one next step that could help (try again, sign in)
 *
 * It never invents a number: the funnel is the server's counts, a role carries a word for its fit and a word for its
 * level (`JobCard`), and when nothing fitted it says so and shows the funnel, so the person sees why. Nothing moves
 * on its own: the running state is a line of text and the live trail, not an animation.
 *
 * Keyboard and screen readers: the trail is a polite live region; focus follows the person (to Stop when a search
 * starts, to the result line when it ends) only when it was in the panel, so a person reading a role is never pulled
 * away. "See full match" hands the role's posting address to Discover (`onSeeMatch`), which starts the ordinary match.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { AiJob, FunnelStep } from '../../../shared/jobsearch';
import { signInSheet, useMe } from '../../auth/useMe';
import { plural } from '../../lib/ui';
import { canRetry } from './api';
import type { JobSearchFailure } from './api';
import { JobCard } from './JobCard';
import { Trail } from './Trail';
import { useJobSearch } from './useJobSearch';
import type { JobSearchOptions } from './useJobSearch';
import { waitWords } from './words';

const LEDE =
  "Claude Haiku searches employers' own job sites for roles like yours, reads their boards so every role is open today, and checks each posting against your evidence. Takes about a minute.";

export interface JobSearchPanelProps extends JobSearchOptions {
  profileId: string;
  /** The city Discover is showing; absent = the one the person's resume names. */
  city?: string;
  /** A posting is already being read, so a second match cannot start. */
  matchLocked: boolean;
  /** Start a full match from a role's posting address; resolves with a sentence when it could not start. Absent: no "See full match". */
  onSeeMatch?: (job: AiJob) => Promise<string | undefined>;
}

export function JobSearchPanel({ profileId, city, matchLocked, onSeeMatch, stream, autoStart }: JobSearchPanelProps) {
  const s = useJobSearch(profileId, city, { stream, autoStart });
  const labelId = useId();
  const listId = useId();
  const root = useRef<HTMLElement>(null);
  const stopBtn = useRef<HTMLButtonElement>(null);
  const lead = useRef<HTMLParagraphElement>(null);
  const [matching, setMatching] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ id: string; text: string } | null>(null);
  const now = useMemo(() => Date.now(), [s.jobs]);

  // Focus follows the person: to Stop when a search starts, to the result line when it ends, but only when focus was
  // in the panel (or nowhere), so someone reading a role further down is never pulled back up.
  const before = useRef(s.phase);
  useEffect(() => {
    const was = before.current;
    before.current = s.phase;
    if (was === s.phase) return;
    const at = document.activeElement;
    if (at && at !== document.body && !root.current?.contains(at)) return;
    if (s.phase === 'running') stopBtn.current?.focus({ preventScroll: true });
    else if (was === 'running') lead.current?.focus({ preventScroll: true });
  }, [s.phase]);

  const seeMatch = async (job: AiJob) => {
    if (!onSeeMatch) return;
    setNotice(null);
    // a second posting cannot be read at the same time: say so on the card that was pressed, not on every card
    if (matchLocked || matching !== null) {
      setNotice({ id: job.id, text: 'A posting is already being read. Try again in a moment.' });
      return;
    }
    setMatching(job.id);
    const text = await onSeeMatch(job);
    setMatching(null);
    // what this attempt said replaces any "already being read" line left on another card by a press meanwhile
    setNotice(text ? { id: job.id, text } : null);
  };

  const found = s.jobs.length;
  const running = s.phase === 'running';

  return (
    <section ref={root} className="dc-js" aria-labelledby={labelId}>
      <div className="panel dc-js-panel" aria-busy={running || undefined}>
        <p className="kicker" id={labelId}>
          Search further
        </p>

        {s.phase === 'idle' && (
          <>
            {s.stopped ? (
              <p ref={lead} tabIndex={-1} className="dc-js-title" role="status">
                {`You stopped the search.${found > 0 ? ` ${plural(found, 'role')} had been found by then.` : ''}`}
              </p>
            ) : (
              <p className="dc-js-lede">{LEDE}</p>
            )}
            <div className="dc-actions">
              <button type="button" className="btn btn-primary dc-btn" onClick={s.start}>
                {s.stopped ? 'Search again' : 'Search with AI'}
              </button>
            </div>
          </>
        )}

        {running && (
          <>
            <div className="dc-js-runhead">
              <p className="dc-js-status" role="status">
                {`Searching${city ? ` in ${city}` : ''}…`}
                {found > 0 ? ` ${plural(found, 'role')} found so far.` : ' This takes about a minute.'}
              </p>
              <button ref={stopBtn} type="button" className="btn dc-btn-sm" aria-label="Stop the search" onClick={s.stop}>
                Stop
              </button>
            </div>
            <Trail steps={s.steps} live />
          </>
        )}

        {s.phase === 'done' && (
          <div className="dc-js-done">
            <div className="dc-js-done-main">
              <p ref={lead} tabIndex={-1} className="dc-js-title" role="status">
                {found > 0
                  ? `Found ${plural(found, 'role')}, strongest fit first.`
                  : s.funnel.length > 0
                    ? 'Nothing fitted this time. Here is where the search narrowed things down.'
                    : 'Nothing fitted this time.'}
              </p>
              {s.note && <p className="dc-muted dc-js-note">{s.note}</p>}
              {s.steps.length > 0 && (
                <details className="dc-js-details">
                  <summary className="dc-js-summary">How it searched</summary>
                  <Trail steps={s.steps} live={false} />
                </details>
              )}
              <div className="dc-actions">
                <button type="button" className="btn dc-btn" onClick={s.start}>
                  Search again
                </button>
              </div>
            </div>
            {s.funnel.length > 0 && <Funnel steps={s.funnel} />}
          </div>
        )}

        {s.phase === 'error' && s.failure && <Failed failure={s.failure} found={found} lead={lead} onRetry={s.start} />}
      </div>

      {found > 0 && (
        <>
          {/* the role titles below are h3: this is the h2 they sit under */}
          <h2 className="sr-only" id={listId}>
            Roles the search found
          </h2>
          <ul className="dc-js-jobs" aria-labelledby={listId}>
            {s.jobs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                now={now}
                onSeeMatch={onSeeMatch ? () => void seeMatch(job) : undefined}
                matchBusy={matching === job.id}
                matchLocked={matchLocked || (matching !== null && matching !== job.id)}
                notice={notice?.id === job.id ? notice.text : undefined}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** What was left after each stage, as label and count. */
function Funnel({ steps }: { steps: readonly FunnelStep[] }) {
  const id = useId();
  return (
    <div className="dc-js-funnel-box">
      <p className="kicker" id={id}>
        How the search narrowed it down
      </p>
      <ol className="dc-js-funnel" aria-labelledby={id}>
        {steps.map((step, i) => (
          <li key={i}>
            <span>{step.label}</span>
            <span className="numeral dc-js-funnel-n">{step.count}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** The server's sentence as given, and the one next step that could help. */
function Failed({ failure, found, lead, onRetry }: { failure: JobSearchFailure; found: number; lead: RefObject<HTMLParagraphElement | null>; onRetry: () => void }) {
  // a sentence that already says how long to wait is not told twice
  const wait = failure.status === 429 && failure.retryAfter !== undefined && !/\d/.test(failure.message) ? `You can try again in ${waitWords(failure.retryAfter)}.` : undefined;
  return (
    <>
      <p ref={lead} tabIndex={-1} className="dc-js-title" role="alert">
        {failure.message}
      </p>
      {wait && <p className="dc-muted">{wait}</p>}
      {found > 0 && <p className="dc-muted">{`${plural(found, 'role')} had been found before it stopped.`}</p>}
      <div className="dc-actions">
        {failure.status === 401 && <SignInAction message={failure.message} />}
        {canRetry(failure) && (
          <button type="button" className="btn btn-primary dc-btn" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </>
  );
}

/** A sign-in is offered only where the server has a way to sign in. */
function SignInAction({ message }: { message: string }) {
  const { providers } = useMe();
  if (!providers.google && !providers.magic) return null;
  return (
    <button type="button" className="btn btn-primary dc-btn" onClick={() => signInSheet.open('account', message)}>
      Sign in
    </button>
  );
}
