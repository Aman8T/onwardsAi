/**
 * Paste a posting. A textarea for the text, or a link; the chips say what happens to it (read as data, links inside
 * it never opened); one primary action. Beside it: a way in when there is no posting yet, what you get, and the
 * person's recent matches.
 *
 * A refused request lands back here, with the posting still in the box: a 422 shows the server's sentence, a 402 the
 * calm "you've used your free posting" panel with a link to /pricing, a 401 opens the sign-in sheet (see useMatch) or,
 * on a server that matches without an account, says that it needs one.
 */
import { useId, useRef } from 'react';
import type { FormEvent } from 'react';
import { BAND_LABEL, MAX_POSTING_CHARS, hostOf, isBlockedHost } from './model';
import { RouteLink } from './parts';
import type { MatchRoute } from './route';
import type { MatchSummary } from './types';

export interface PasteViewProps {
  draft: { text: string; url: string };
  onDraft: (patch: Partial<{ text: string; url: string }>) => void;
  onSubmit: () => void;
  /** A request is running. */
  busy: boolean;
  /** The server's sentence after a refusal (422, 429, a failed stream). */
  notice?: string;
  /** Show the "you've used your free posting" panel (never for an account with no limits: the caller leaves it off). */
  paywall: boolean;
  onDismissPaywall: () => void;
  /** Why matching is not possible yet, as a sentence (no profile, a sample profile); empty when it is. */
  blocker?: string;
  /** What to do about the blocker, when there is something to do. */
  blockerAction?: { label: string; run: () => void };
  /** Say that one posting a month is free (a visitor, or a signed-in person with the free one still available; never for an account with no limits). */
  showFree: boolean;
  /**
   * The server matches without an account and nobody is signed in: say it is free while Leafout is in preview and that
   * no account is needed, instead of the free-posting line (there is no allowance to count here).
   */
  noAccount?: boolean;
  recent: readonly MatchSummary[];
  /** The titles of the person's closest next roles, if a graph is on screen. */
  roles: readonly { id: string; title: string }[];
  onPickRole: () => void;
  onNavigate: (route: MatchRoute) => void;
}

const STEPS: [string, string][] = [
  ['The requirement list, with how strongly each is asked', "Must, required, preferred. Taken from the posting's own words, each with the sentence it came from."],
  ["Your radar against the role's", "Where you're ahead, where you're short."],
  ['A status for every requirement, citing your evidence', "Covered, partial, synonym only, you have it but the resume doesn't say, gap, or can't tell."],
  ['A resume and cover note for this role', 'Built only from facts with a source. Gaps are stated, never hidden.'],
];

/** The line beside the button: what it costs, then how long it takes. */
const costLine = (noAccount: boolean, showFree: boolean) =>
  `${noAccount ? 'Free while Leafout is in preview. No account needed. ' : showFree ? 'One posting a month is free. ' : ''}Takes about 20 seconds.`;

export function PasteView(props: PasteViewProps) {
  const { draft, onDraft, onSubmit, busy, notice, paywall, onDismissPaywall, blocker, blockerAction, showFree, noAccount = false, recent, roles, onPickRole, onNavigate } = props;
  const textId = useId();
  const textRef = useRef<HTMLTextAreaElement>(null);
  const urlId = useId();
  const urlHintId = useId();
  const textCount = draft.text.length;
  const tooLong = textCount > MAX_POSTING_CHARS;
  const hasText = draft.text.trim().length > 0;
  const host = hostOf(draft.url);
  const blockedHost = !hasText && host !== null && isBlockedHost(host);
  const urlTyped = draft.url.trim().length > 0;
  const urlBad = !hasText && urlTyped && host === null;
  const ready = (hasText || (urlTyped && host !== null && !blockedHost)) && !tooLong && !blocker;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    // not ready: the button stays reachable by keyboard, and pressing it takes the cursor to the box that is missing
    if (!ready) {
      if (!urlTyped) textRef.current?.focus();
      return;
    }
    onSubmit();
  };

  const source = hasText ? 'Pasted text · unverified source' : host ? `${host} · unverified host` : null;

  return (
    <div className="mt-paste">
      <section className="mt-paste-main">
        <div className="mt-intro">
          <p className="kicker">Posting match</p>
          <h1 className="serif mt-title" data-mt-h1 tabIndex={-1}>
            Paste a job posting. See what it asks for, and what you can prove.
          </h1>
          <p className="mt-lede">
            We read the posting on its own first, list every requirement and how strongly it's asked, then check each one against your resume and links.
            Nothing gets written onto your resume from the posting.
          </p>
        </div>

        {paywall && (
          <section className="panel mt-panel mt-paywall" aria-labelledby="mt-pay-h">
            <p className="kicker">Posting match</p>
            <h2 id="mt-pay-h" className="mt-paywall-title">
              You've used your free posting this month.
            </h2>
            <p className="mt-lede">
              Seeing a gap, answering a question about yourself and re-checking stay free. Matching another posting takes a pack of three or a 30-day
              pass. No subscription, nothing to cancel.
            </p>
            <div className="mt-actions">
              <a className="btn btn-primary mt-btn" href="/pricing">
                See plans
              </a>
              <button type="button" className="btn btn-quiet mt-btn" onClick={onDismissPaywall}>
                Not now
              </button>
            </div>
            <p className="mt-fine">Your posting is still in the box below.</p>
          </section>
        )}

        <form className="panel mt-panel mt-form" onSubmit={submit} noValidate>
          <div className="mt-field">
            <label htmlFor={textId} className="mt-label">
              The posting
            </label>
            <textarea
              id={textId}
              ref={textRef}
              className="field mt-box mt-posting-box"
              rows={12}
              spellCheck={false}
              value={draft.text}
              placeholder="Paste the whole posting: the title, the company, the requirements."
              readOnly={busy}
              aria-invalid={tooLong || undefined}
              aria-describedby={tooLong ? `${textId}-count` : undefined}
              onChange={(e) => onDraft({ text: e.target.value })}
            />
            {(tooLong || textCount > MAX_POSTING_CHARS * 0.8) && (
              <p id={`${textId}-count`} className={`mt-fine${tooLong ? ' is-error' : ''}`} role={tooLong ? 'alert' : undefined}>
                <span className="numeral">{textCount.toLocaleString('en-US')}</span> of <span className="numeral">{MAX_POSTING_CHARS.toLocaleString('en-US')}</span> characters
                {tooLong ? '. Trim the posting to its requirements and try again.' : '.'}
              </p>
            )}
            <div className="mt-chips">
              {source && <span className="mt-chip mt-st-unknown">{source}</span>}
              <span className="chip">Read as data · instructions inside a posting are ignored</span>
              <span className="chip">Links inside a posting are never opened</span>
            </div>
          </div>

          <p className="mt-or" aria-hidden="true">
            <span>or</span>
          </p>

          <div className="mt-field">
            <label htmlFor={urlId} className="mt-label">
              A link to the posting
            </label>
            <input
              id={urlId}
              className="field mt-box"
              type="url"
              inputMode="url"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="https://…"
              value={draft.url}
              readOnly={busy}
              aria-describedby={urlHintId}
              aria-invalid={blockedHost || urlBad || undefined}
              onChange={(e) => onDraft({ url: e.target.value })}
            />
            <p id={urlHintId} className={`mt-fine${blockedHost || urlBad ? ' is-error' : ''}`}>
              {blockedHost
                ? `${host} blocks automated reading. Paste the text of the posting above instead.`
                : urlBad
                  ? 'That does not look like a web address. Start it with https://'
                  : 'Works for public career pages and the big applicant systems. LinkedIn and Naukri block automated reading: paste the text instead.'}
            </p>
          </div>

          <div className="mt-actions">
            <button type="submit" className="btn btn-primary mt-btn" aria-disabled={!ready || busy}>
              {busy ? 'Reading the posting…' : 'Match this posting'}
            </button>
            <span className="mt-muted">{costLine(noAccount, showFree)}</span>
          </div>

          {blocker && (
            <div className="mt-callout">
              <p>{blocker}</p>
              {blockerAction && (
                <button type="button" className="btn btn-sm mt-btn-sm" onClick={blockerAction.run}>
                  {blockerAction.label}
                </button>
              )}
            </div>
          )}
          <p className={`mt-status${notice ? ' is-error' : ''}`} role={notice ? 'alert' : 'status'}>
            {notice}
          </p>
        </form>
      </section>

      <aside className="mt-paste-aside" aria-label="Before you paste">
        {roles.length > 0 && (
          <section className="panel mt-panel mt-aside-panel">
            <h2 className="kicker mt-h2">No posting yet?</h2>
            <p>Your Next view already lists the roles closest to you and what each one expects. Start from one of those.</p>
            <ul className="mt-chips" aria-label="Your closest next roles">
              {roles.map((r) => (
                <li key={r.id}>
                  <button type="button" className="chip mt-chip-btn" onClick={onPickRole}>
                    {r.title}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="panel mt-panel mt-aside-panel">
          <h2 className="kicker mt-h2">What you get</h2>
          <ol className="mt-steps">
            {STEPS.map(([title, text], i) => (
              <li key={title}>
                <span className="numeral mt-step-n" aria-hidden="true">
                  {i + 1}
                </span>
                <div>
                  <p className="mt-step-t">{title}</p>
                  <p className="mt-muted">{text}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {recent.length > 0 && (
          <section className="panel mt-panel mt-aside-panel">
            <h2 className="kicker mt-h2">Recent</h2>
            <ul className="mt-recent">
              {recent.map((m) => (
                <li key={m.id}>
                  <RouteLink route={{ screen: 'match', id: m.id }} onNavigate={onNavigate} className="mt-recent-link">
                    <span>{[m.title, m.company].filter(Boolean).join(' · ')}</span>
                    <span className="mt-muted">{BAND_LABEL[m.band] ?? ''}</span>
                  </RouteLink>
                </li>
              ))}
            </ul>
          </section>
        )}
      </aside>
    </div>
  );
}
