/**
 * `/signout`: ends the app session, then hands the browser back to the site. The site's own sign-out sends people here
 * with `?next=https://leafout.in/`; the app's account menu comes with no `next`, and the default is the site's
 * sign-out, which ends the other side. Only those two `next` values are followed (see `signOutNext`).
 *
 * The request is a POST from this page (same-origin), never a bare link, so following a link cannot sign anyone out
 * of the app by itself beyond opening this page.
 */
import { useEffect, useRef } from 'react';
import * as api from '../lib/api';
import { signOutNext } from './links';
import '../styles/auth.css';

export function SignOut() {
  const started = useRef(false);
  const next = signOutNext(new URLSearchParams(window.location.search).get('next'));

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api
      .logout()
      .catch(() => {
        /* the session may already be gone; the site's sign-out is the next step either way */
      })
      .finally(() => window.location.replace(next));
  }, [next]);

  return (
    <main className="auth-page">
      <section className="panel auth-page-card" aria-live="polite">
        <p className="kicker">Your account</p>
        <h1 className="serif">Signing you out…</h1>
        <p className="auth-text">
          If nothing happens, <a href={next}>continue</a>.
        </p>
      </section>
    </main>
  );
}
