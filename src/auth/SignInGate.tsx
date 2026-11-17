/**
 * What a signed-out visitor sees in place of any feature when the server requires a sign-in (`gate === 'blocked'` in
 * `useMe`, applied once in App). On Home it is the whole welcome: the headline, one line on what Leafout does, the
 * sign-in button, and links to the two pages anyone may read (How it works, Pricing). On any other screen (a posting
 * match, Discover, Practice, the Weekly, a graph or portfolio left over from this browser) it is the same page with
 * one more line naming what asked for the account. Nothing here talks to the server except through `SignInLink`.
 *
 * The button is the existing one: a link to the Leafout site's sign-in that returns to this page, or, on a server
 * that offers Google or an emailed link instead, the sign-in sheet (rendered once by App).
 */
import { Brand } from '../components/Brand';
import { SchemeToggle } from '../components/SchemeToggle';
import { SiteFooter } from '../components/SiteFooter';
import { isDiscoverPath } from '../discover/route';
import { isPracticePath } from '../practice/route';
import { isResumePath } from '../resume/route';
import type { View } from '../store';
import { NARROW_QUERY, useMediaQuery } from '../lib/ui';
import { SignInLink } from './SignInLink';
import '../styles/onboarding.css';
import '../styles/auth.css';

/** What the visitor was trying to open, for the one line above the headline. */
const WANTED: Partial<Record<View, string>> = {
  match: 'Posting match',
  discover: 'Search with AI',
  practice: 'Practice the interview',
  weekly: 'The Leafout Weekly',
  resume: 'The resume page',
  portfolio: 'Your portfolio',
  chat: 'The coach',
  graph: 'Your skill graph',
  analysis: 'Your skill graph',
};

function wantedFor(view: View): string | null {
  const byView = WANTED[view];
  if (byView) return byView;
  try {
    const path = window.location.pathname;
    if (isPracticePath(path)) return WANTED.practice ?? null;
    if (isDiscoverPath(path)) return WANTED.discover ?? null;
    if (isResumePath(path)) return WANTED.resume ?? null;
  } catch {
    /* no address bar */
  }
  return null;
}

export function SignInGate({ view }: { view: View }) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const wanted = wantedFor(view);
  return (
    <div className="ob-root" id="top">
      <div className="wrap ob-wrap">
        <header className="ob-header">
          <span className="ob-brand">
            <Brand tagline={!narrow} />
          </span>
          <nav className="ob-nav" aria-label="Primary">
            <SchemeToggle />
          </nav>
        </header>

        <main className="ob-main gate-main">
          {wanted && <p className="kicker gate-wanted">{wanted} needs a free Leafout account.</p>}
          <h1 className="ob-h1 serif">
            <span className="ob-h1-line">See where you stand.</span> <span className="ob-h1-line em">Then where you could go.</span>
          </h1>
          <p className="gate-lead">
            Leafout reads your resume and public links and shows your skills as a graph where every skill cites its source. Then it checks job postings
            against that evidence and helps you practise the interview. Sign in with your free Leafout account to start.
          </p>
          <div className="gate-actions">
            <SignInLink className="btn btn-primary gate-cta">Sign in</SignInLink>
            <a className="btn btn-quiet" href="/how-it-works">
              How it works
            </a>
            <a className="btn btn-quiet" href="/pricing">
              Pricing
            </a>
          </div>
        </main>

        <SiteFooter />
      </div>
    </div>
  );
}
