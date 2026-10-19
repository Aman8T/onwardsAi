/**
 * `/account/delete`: the second half of "Delete my account" started on the site. The site sends people here with
 * `?next=https://leafout.in/account/delete-finish`. Nothing is deleted on load, because a link alone must not delete
 * anything: the person confirms with a click, then the app's data goes (`DELETE /api/me`) and the browser returns to
 * the site's finish page. Cancel goes back to the site's account page. Signed out, the page asks to sign in first.
 */
import { useState } from 'react';
import * as api from '../lib/api';
import { SITE_ACCOUNT_URL, SITE_DELETE_FINISH_URL } from './links';
import { SignInLink } from './SignInLink';
import { useMe } from './useMe';
import '../styles/auth.css';

export function AccountDelete() {
  const { user, loaded } = useMe();
  const [phase, setPhase] = useState<'ask' | 'working' | 'failed'>('ask');

  const confirm = async () => {
    setPhase('working');
    try {
      await api.deleteMe();
      window.location.replace(SITE_DELETE_FINISH_URL);
    } catch {
      setPhase('failed');
    }
  };

  return (
    <main className="auth-page">
      <section className="panel auth-page-card" aria-busy={!loaded || undefined}>
        <p className="kicker">Your account</p>
        {!loaded ? (
          <p className="auth-text">One moment…</p>
        ) : !user ? (
          <>
            <h1 className="serif">Sign in to continue</h1>
            <p className="auth-text">Sign in first, and we will ask whether to delete your Tayyari app data.</p>
            <div className="auth-page-actions">
              <SignInLink className="btn btn-primary">Sign in</SignInLink>
              <a className="btn btn-quiet" href={SITE_ACCOUNT_URL}>
                Cancel
              </a>
            </div>
          </>
        ) : (
          <>
            <h1 className="serif">Also delete your Leafout app data?</h1>
            <p className="auth-text">
              This removes your Tayyari profiles, portfolios, saved postings and sign-ins from the app. It cannot be undone. Your Leafout account is deleted on the next step.
            </p>
            <p className="auth-error" role="alert">
              {phase === 'failed' ? 'Your data could not be deleted. Please try again.' : ''}
            </p>
            <div className="auth-page-actions">
              <button type="button" className="btn btn-primary" disabled={phase === 'working'} onClick={() => void confirm()}>
                {phase === 'working' ? 'Deleting…' : 'Delete my app data'}
              </button>
              <a className="btn btn-quiet" href={SITE_ACCOUNT_URL}>
                Cancel
              </a>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
