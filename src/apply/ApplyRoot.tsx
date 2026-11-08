/**
 * The auto-apply shell, for `/apply` and everything under it (route.ts): a minimal header (the wordmark, Applications,
 * the account), a signed-in gate, and the screen the address names. Auto-apply is for signed-in people only (CLAUDE.md
 * rule 9), so a visitor sees the sign-in panel and nothing else; there is no anonymous path.
 *
 *   /apply           Applications            ApplicationsView.tsx
 *   /apply/new       Apply for me            StartView.tsx (first check and consent first when due)
 *   /apply/details   Your details            DetailsView.tsx
 *   /apply/<id>      one application         ApplicationScreen.tsx (package FE2's run screens)
 *
 * The old tracker's `/apply?id=<id>` is rewritten in place to `/apply/<id>`.
 */
import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { ApplyStatus } from '../../shared/apply';
import { offersSignIn } from '../auth/links';
import { SignIn } from '../auth/SignIn';
import { signInSheet, useMe } from '../auth/useMe';
import { EntitlementChip } from '../billing/EntitlementChip';
import { Brand } from '../components/Brand';
import { initials } from '../lib/ui';
import { ApplyError, applyApi, errorText } from './api';
import { ApplicationScreen } from './ApplicationScreen';
import { ApplicationsScreen } from './ApplicationsView';
import { DetailsScreen } from './DetailsView';
import { StartScreen } from './StartView';
import { LIST_ROUTE, applyPath, canonicalApplyPath, linkTo, parseApplyRoute } from './route';
import type { ApplyRoute, Navigate } from './route';
import '../styles/apply.css';

const here = (): ApplyRoute => parseApplyRoute(window.location.pathname, window.location.search) ?? LIST_ROUTE;

export function ApplyRoot() {
  const { loaded, user, status: meStatus, refresh } = useMe();
  const [route, setRoute] = useState<ApplyRoute>(here);
  const [status, setStatus] = useState<ApplyStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  // the old `/apply?id=` link, a trailing slash or stray query keys: say the canonical address
  useEffect(() => {
    const canonical = canonicalApplyPath(window.location.pathname, window.location.search);
    if (canonical) window.history.replaceState(null, '', canonical);
  }, []);

  useEffect(() => {
    const onPop = () => setRoute(here());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate: Navigate = useCallback((next, opts) => {
    const path = applyPath(next);
    if (opts?.replace) window.history.replaceState(null, '', path);
    else window.history.pushState(null, '', path);
    setRoute(next);
    if (!opts?.replace) window.scrollTo({ top: 0 });
  }, []);

  const userId = user?.id;
  useEffect(() => {
    if (!userId) return;
    let live = true;
    applyApi
      .status()
      .then((s) => {
        if (live) {
          setStatus(s);
          setExpired(false);
        }
      })
      .catch((err) => {
        if (!live) return;
        // an expired session while the cached account still says signed in: the sign-in panel, not an error
        if (err instanceof ApplyError && err.status === 401) setExpired(true);
        else setError(errorText(err, 'Could not reach auto-apply. Try again.'));
      });
    return () => {
      live = false;
    };
  }, [userId]);

  let body: ReactNode;
  if (meStatus === 'error') {
    body = (
      <div className="ap-error" role="alert">
        <p>We could not check your sign-in.</p>
        <button type="button" className="btn" onClick={() => void refresh()}>
          Try again
        </button>
      </div>
    );
  } else if (!loaded) {
    body = null;
  } else if (!user || expired || (status && !status.signedIn)) {
    body = <SignedOut />;
  } else if (!status) {
    body = error ? (
      <p className="ap-error" role="alert">
        {error}
      </p>
    ) : (
      <p className="ap-muted" role="status">
        Loading
      </p>
    );
  } else if (!status.enabled) {
    body = <Off />;
  } else {
    body = <Screen route={route} status={status} onStatus={setStatus} navigate={navigate} />;
  }

  return (
    <div className="app-view ap-root">
      <ApplyHeader route={route} navigate={navigate} />
      <main className="ap-page">{body}</main>
      <SignIn />
    </div>
  );
}

function Screen({ route, status, onStatus, navigate }: { route: ApplyRoute; status: ApplyStatus; onStatus: (s: ApplyStatus) => void; navigate: Navigate }) {
  switch (route.screen) {
    case 'list':
      return <ApplicationsScreen navigate={navigate} />;
    case 'details':
      return <DetailsScreen navigate={navigate} />;
    case 'new':
      return <StartScreen route={route} status={status} onStatus={onStatus} navigate={navigate} />;
    case 'one':
      return <ApplicationScreen key={route.id} id={route.id} status={status} navigate={navigate} />;
  }
}

function ApplyHeader({ route, navigate }: { route: ApplyRoute; navigate: Navigate }) {
  const { user, loaded, providers, signOut } = useMe();
  const onList = route.screen === 'list';
  const name = user?.name ?? user?.email ?? '';
  return (
    <header className="ap-header">
      <Brand onClick={() => window.location.assign('/')} />
      <div className="ap-header-end">
        {user && (
          <a className="ap-navlink" aria-current={onList ? 'page' : undefined} {...linkTo(LIST_ROUTE, navigate)}>
            Applications
          </a>
        )}
        <EntitlementChip />
        {loaded && user && (
          <>
            <span className="ap-avatar" role="img" aria-label={`Signed in as ${name || 'you'}`} title={name}>
              {initials(name || 'You')}
            </span>
            {providers.leafout ? (
              <a className="btn btn-quiet" href="/signout">
                Sign out
              </a>
            ) : (
              <button type="button" className="btn btn-quiet" onClick={() => void signOut()}>
                Sign out
              </button>
            )}
          </>
        )}
        {loaded && !user && offersSignIn(providers) && (
          <button type="button" className="btn btn-quiet" onClick={() => signInSheet.open('account', undefined, applyPath(route))}>
            Sign in
          </button>
        )}
      </div>
    </header>
  );
}

function SignedOut() {
  const { providers } = useMe();
  const canSignIn = offersSignIn(providers);
  const next = `${window.location.pathname}${window.location.search}`;
  return (
    <section className="panel ap-panel ap-stack-s ap-narrow" aria-labelledby="ap-signin-title">
      <p className="kicker">Apply for me</p>
      <h1 id="ap-signin-title" className="serif ap-title">
        Sign in to apply with Leafout.
      </h1>
      <p className="ap-muted">
        Applications, the answers forms are given and the employer's confirmations stay with your account, so this page needs you
        signed in.
      </p>
      {canSignIn ? (
        <div className="ap-actions">
          <button type="button" className="btn btn-primary" onClick={() => signInSheet.open('account', undefined, next)}>
            Sign in
          </button>
        </div>
      ) : (
        <p className="ap-muted">Sign-in is not available on this server yet.</p>
      )}
    </section>
  );
}

function Off() {
  return (
    <section className="ap-stack-s ap-narrow">
      <p className="kicker">Apply for me</p>
      <h1 className="serif ap-title">Auto-apply is off on this server.</h1>
      <p className="ap-muted">It is switched on with AUTO_APPLY_ENABLED. Everything else in Leafout works as before.</p>
      <div className="ap-actions">
        <a className="btn" href="/">
          Back to Leafout
        </a>
      </div>
    </section>
  );
}
