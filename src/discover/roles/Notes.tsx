/**
 * The states that say what happened, plainly (board "States" of the Discover canvas): a search that did not run or did
 * not finish, the sample profile, a profile with little in it. Each says it in a sentence and offers the one next step
 * that could help; none of them takes the last results away.
 *
 * The server's sentence is shown as it was written. A 429 adds how long to wait when its sentence does not already say;
 * a 503 (paused for today, or switched off) and a 404 (the profile is not on this server) cannot be fixed by asking
 * again, so they offer no retry.
 */
import type { ReactNode } from 'react';
import { offersSignIn } from '../../auth/links';
import { signInSheet, useMe } from '../../auth/useMe';
import { plural } from '../../lib/ui';
import { canRetry } from '../jobsearch/api';
import type { JobSearchFailure } from '../jobsearch/api';
import { waitWords } from '../jobsearch/words';
import { IconAlert } from './icons';

export interface FailureNoteProps {
  failure: JobSearchFailure;
  /** Results from before (or a partial set) are on screen, and stay. */
  hasResults: boolean;
  /** Roles found before the search stopped. */
  found: number;
  onRetry: () => void;
  onAnalyse: () => void;
}

/** True when asking again right now is pointless: the service says no, or the profile is missing. */
export const isBlocked = (f: JobSearchFailure | undefined): boolean => f?.status === 503 || f?.status === 404;

export function FailureNote({ failure, hasResults, found, onRetry, onAnalyse }: FailureNoteProps) {
  const { status } = failure;
  const quiet = status === 429 || status === 503;
  // a sentence that already says how long to wait is not told twice
  const wait = status === 429 && failure.retryAfter !== undefined && !/\d/.test(failure.message) ? `You can try again in ${waitWords(failure.retryAfter)}.` : undefined;
  const stay = hasResults ? 'Your last results stay here.' : undefined;

  let sub: ReactNode;
  let action: ReactNode;
  if (status === 404) {
    sub = "We couldn't find your profile on this server. Analyse your resume again, then come back.";
    action = (
      <button type="button" className="btn btn-primary dc-btn" onClick={onAnalyse}>
        Analyse my resume
      </button>
    );
  } else if (status === 401) {
    action = <SignInAction message={failure.message} />;
  } else if (quiet) {
    sub = [wait, stay].filter(Boolean).join(' ') || undefined;
  } else {
    sub = ['Nothing was charged to you.', found > 0 ? `${plural(found, 'role')} had been found before it stopped.` : undefined].filter(Boolean).join(' ');
    action = canRetry(failure) ? (
      <button type="button" className="btn btn-primary dc-btn" onClick={onRetry}>
        Try again
      </button>
    ) : undefined;
  }

  return (
    <section className={`panel dc-note${quiet ? '' : ' is-alert'}`} role={quiet ? 'status' : 'alert'}>
      <div className="dc-note-text">
        {!quiet && <IconAlert className="dc-note-icon" />}
        <div>
          <p className="dc-strong">{failure.message}</p>
          {sub && <p className="dc-muted">{sub}</p>}
        </div>
      </div>
      {action}
    </section>
  );
}

/** A sign-in is offered only where the server has a way to sign in. */
function SignInAction({ message }: { message: string }) {
  const { providers } = useMe();
  if (!offersSignIn(providers)) return null;
  return (
    <button type="button" className="btn btn-primary dc-btn" onClick={() => signInSheet.open('account', message)}>
      Sign in
    </button>
  );
}

/** Shown above the page while the profile is the sample: whose roles these are. */
export function SampleBanner({ onAnalyse }: { onAnalyse: () => void }) {
  return (
    <section className="dc-banner" role="status" aria-label="About this profile">
      <span className="chip">Sample profile</span>
      <span>
        These are roles for the sample profile.{' '}
        <button type="button" className="dc-link dc-link-pad" onClick={onAnalyse}>
          Analyse your own resume
        </button>{' '}
        to search for you.
      </span>
    </section>
  );
}

/** Shown before the first search when the profile has little in it. */
export function ThinNote({ onAdd }: { onAdd: () => void }) {
  return (
    <section className="panel dc-note m-gap" role="status">
      <div className="dc-note-text">
        <div>
          <p className="dc-strong">Your profile is short, so the search has little to go on.</p>
          <p className="dc-muted">Add a project or a link first.</p>
        </div>
      </div>
      <div className="dc-actions">
        <button type="button" className="btn btn-primary dc-btn" onClick={onAdd}>
          Add a project
        </button>
        <button type="button" className="btn dc-btn" onClick={onAdd}>
          Add a link
        </button>
      </div>
    </section>
  );
}
