/**
 * The sign-in sheet: Google, or an emailed link. Opened from the account control and from the "Save this to your
 * account" invitation; it is a dialog on wide screens and a bottom sheet under 760 px (see Sheet.tsx).
 *
 * The Google button is a plain link to `/api/auth/google/start`: the whole OAuth flow is a redirect, and the page
 * comes back to `/` signed in. It is hidden when the server has no Google credentials. The email form is hidden when
 * the server cannot send mail. Asking for a link never says whether the address has an account; a local server with
 * no mail key also hands back the link (`devLink`), which is shown so the flow can be tried without email.
 *
 * When the server offers sign-in through the Leafout account (`providers.leafout`) the sheet is one line and one link to it,
 * and the Google button and email field are not shown. A sign-in that did not complete comes back as `/?auth=<code>`; that code is read once here, removed from the
 * address bar, and shown as a sentence above the form.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, requestMagicLink } from '../lib/api';
import { currentPath, signInHref } from './links';
import { Sheet } from './Sheet';
import { signInSheet, useMe, useSignInSheet } from './useMe';
import type { SignInReason } from './useMe';

const LINK_MINUTES = 15;

const NOTICES: Record<string, string> = {
  link_invalid: 'That link has expired or was already used. Ask for a new one below.',
  google_failed: 'Google sign-in did not complete. Please try again.',
  google_denied: 'Google sign-in was cancelled. Nothing was saved.',
  google_unavailable: 'Google sign-in is not available right now.',
};

const COPY: Record<SignInReason, { title: string; text: string }> = {
  save: {
    title: 'Keep this graph',
    text: 'Sign in to save your graph and portfolio to your account, and open them again from any device.',
  },
  account: {
    title: 'Sign in to Leafout',
    text: 'Your graph and portfolio stay with your account. Reading your sources stays open to everyone.',
  },
  buy: {
    title: 'Sign in to buy',
    text: 'Postings are kept on your account, so they follow you to any device. Prices and plans stay open to everyone.',
  },
};

/** Where a Google sign-in lands afterwards: back on the page that asked for it when that page is the pricing page. */
const NEXT: Record<SignInReason, string> = { save: '/', account: '/', buy: '/pricing' };

/** Reads `?auth=<code>` once, takes it out of the address bar and returns its sentence. */
function takeAuthNotice(): string | undefined {
  try {
    const url = new URL(window.location.href);
    const code = url.searchParams.get('auth');
    if (code === null) return undefined;
    url.searchParams.delete('auth');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    return NOTICES[code];
  } catch {
    return undefined;
  }
}

export function SignIn() {
  const ui = useSignInSheet();
  const { me, providers, user, loaded, stuck, issue } = useMe();
  const titleId = useId();

  // Back from a sign-in that did not complete: open the sheet with the reason.
  useEffect(() => {
    const notice = takeAuthNotice();
    if (notice) signInSheet.open('account', notice);
  }, []);

  // Signed in (the link was followed in another tab, say): nothing left to ask.
  useEffect(() => {
    if (user && ui.open) signInSheet.close();
  }, [user, ui.open]);

  const copy = COPY[ui.reason];
  // the Leafout link brings the person back to the page they were on (or the one the opener named)
  const leafoutHref = ui.open ? signInHref(me, ui.next ?? currentPath()) : null;
  return (
    <>
      {!user && (stuck || issue) && <SignInTrouble href={signInHref(me, currentPath())} issue={issue} />}
    <Sheet open={ui.open && !user} onClose={signInSheet.close} labelledBy={titleId}>
      <Form
        // a fresh form each time the sheet opens
        key={ui.opened}
        titleId={titleId}
        title={copy.title}
        text={copy.text}
        notice={ui.notice}
        google={!providers.leafout && providers.google}
        magic={!providers.leafout && providers.magic}
        loaded={loaded}
        next={NEXT[ui.reason]}
        leafoutHref={providers.leafout ? leafoutHref : null}
        leafout={providers.leafout}
      />
    </Sheet>
    </>
  );
}

/** Shown when the site sign-in could not be confirmed (the loop guard stopped the redirect) or a bridge was refused. */
function SignInTrouble({ href, issue }: { href: string | null; issue?: string }) {
  return (
    <aside className="panel auth-trouble" role="status">
      <p>{issue ?? "We couldn't confirm your sign-in."}</p>
      {href && (
        <a className="btn btn-sm" href={href}>
          Sign in again
        </a>
      )}
    </aside>
  );
}

interface FormProps {
  titleId: string;
  title: string;
  text: string;
  notice?: string;
  google: boolean;
  magic: boolean;
  loaded: boolean;
  next: string;
  /** Sign-in through the Leafout account is on; `leafoutHref` is its link (null until the server has given the URL). */
  leafout: boolean;
  leafoutHref: string | null;
}

function Form({ titleId, title, text, notice, google, magic, loaded, next, leafout, leafoutHref }: FormProps) {
  const emailId = useId();
  const hintId = useId();
  const input = useRef<HTMLInputElement>(null);
  const sent = useRef<HTMLDivElement>(null);
  const [email, setEmail] = useState('');
  const [phase, setPhase] = useState<'form' | 'sending' | 'sent'>('form');
  const [error, setError] = useState<string | null>(null);
  const [devLink, setDevLink] = useState<string | undefined>();
  const nothing = loaded && !google && !magic && !leafout;

  // The form is replaced by the confirmation: keep the keyboard inside the dialog.
  useEffect(() => {
    if (phase === 'sent') sent.current?.focus();
  }, [phase]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = email.trim();
    if (!value) {
      setError('Enter your email address.');
      input.current?.focus();
      return;
    }
    setPhase('sending');
    setError(null);
    try {
      const res = await requestMagicLink(value);
      setDevLink(res.devLink);
      setPhase('sent');
    } catch (err) {
      setPhase('form');
      setError(err instanceof ApiError && err.status === 400 ? 'That does not look like an email address.' : err instanceof Error ? err.message : 'Something went wrong. Please try again.');
      input.current?.focus();
    }
  };

  return (
    <div className="auth-body">
      <button type="button" className="auth-close" aria-label="Close" onClick={signInSheet.close}>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true">
          <path d="M2 2l8 8M10 2l-8 8" />
        </svg>
      </button>

      <p className="kicker">Your account</p>
      <h2 id={titleId} className="auth-title">
        {title}
      </h2>
      <p className="auth-text">{text}</p>

      {notice && (
        <p className="auth-notice" role="status">
          {notice}
        </p>
      )}

      {nothing && <p className="auth-text">Signing in is not set up on this server yet.</p>}

      {leafout && leafoutHref && (
        <a className="btn btn-primary auth-google" href={leafoutHref}>
          Sign in with your Leafout account
        </a>
      )}

      {google && (
        <a className="btn auth-google" href={`/api/auth/google/start?next=${encodeURIComponent(next)}`}>
          Continue with Google
        </a>
      )}

      {google && magic && (
        <p className="auth-or" aria-hidden="true">
          <span>or</span>
        </p>
      )}

      {magic && phase !== 'sent' && (
        <form className="auth-form" onSubmit={submit} noValidate>
          <label htmlFor={emailId} className="kicker">
            Email
          </label>
          <input
            ref={input}
            id={emailId}
            className="field"
            type="email"
            inputMode="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="you@example.com"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              if (error) setError(null);
            }}
            aria-describedby={hintId}
            aria-invalid={error ? true : undefined}
            // read-only, not disabled: a disabled field drops focus, and an error must put the cursor back in it
            readOnly={phase === 'sending'}
          />
          <p id={hintId} className="auth-hint">
            We will email you a link. It works once and expires in {LINK_MINUTES} minutes.
          </p>
          <p className="auth-error" role="alert">
            {error}
          </p>
          <button type="submit" className="btn btn-primary auth-submit" disabled={phase === 'sending'}>
            {phase === 'sending' ? 'Sending…' : 'Email me a link'}
          </button>
        </form>
      )}

      {magic && phase === 'sent' && (
        <div ref={sent} className="auth-sent" role="status" tabIndex={-1}>
          <p className="auth-sent-title">Check your inbox</p>
          <p className="auth-text">
            We sent a link to {email.trim()}. It works once and expires in {LINK_MINUTES} minutes. If nothing arrives, check your spam folder or try again in a few minutes.
          </p>
          {devLink && (
            <p className="auth-text">
              This server is running locally with no email provider, so here is the link: <a href={devLink}>open it</a>.
            </p>
          )}
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => {
              setPhase('form');
              setDevLink(undefined);
              requestAnimationFrame(() => input.current?.focus());
            }}
          >
            Use a different email
          </button>
        </div>
      )}
    </div>
  );
}
