/**
 * What is inside the report's owner sheet (N1c, canvas https://claude.ai/artifact/WLF5fKswLqtgrWzRRW9Sq5, board "Before
 * sharing: what shows"): the choices of what the link shows, every quoted resume line with a switch to hide it, the near
 * misses with the way to Prepare for each, the link itself, and beside it the report exactly as a visitor sees it.
 *
 * It owns no overlay: `ReportSheet` puts it in a dialog, and the dev page puts it on a page. Every change saves on its
 * own (see `useReport`); the sheet says "Saving" and shows the server's sentence when a save is refused. The preview is
 * the server's `public` content, never a guess made here, so it is exactly what the page shows.
 *
 * The sample and an anonymous graph cannot share (the server says so with `blocked`): they get a sentence and one action,
 * never a switch. No percentages, ranks or scores anywhere on it, and nothing that says why the person is looking.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties, MouseEvent, ReactNode } from 'react';
import { REPORT_HIDDEN_MAX } from '../../shared/report';
import type { ReportBlocked, ReportChoices, ReportView } from '../../shared/report';
import type { ReportClient } from './api';
import { safeHref } from './format';
import { ReportPreview } from './ReportPreview';
import { isShared, useReport } from './useReport';
import type { ReportDraft, ReportModel } from './useReport';
import '../styles/report.css';

const COPIED_MS = 2400;
const COPY_FAILED = "Your browser wouldn't copy it. Select the link and copy it yourself.";

export interface ReportBodyProps {
  profileId: string;
  client: ReportClient;
  /** The phone layout (the dev page pins it inside a 390 px frame). */
  narrow: boolean;
  /** The id the dialog (or page) is named by: the title carries it. */
  titleId: string;
  onClose?: () => void;
  /** The sample's action: leave it for the person's own resume. */
  onAnalyse: () => void;
  /** An anonymous graph's action; absent when the server offers no way to sign in. */
  onSignIn?: () => void;
  /** A near miss's "What would show it": Prepare for that role of the graph. */
  onPrepare: (roleId: string) => void;
  /** "Open your resume once": /resume, inside the app. Without it the link is a plain page load. */
  onResume?: () => void;
  /** The dev page only: start with "Copied" showing, or with the new-link question open. */
  initial?: { copied?: boolean; confirmLink?: boolean };
}

export function ReportBody({ profileId, client, narrow, titleId, onClose, onAnalyse, onSignIn, onPrepare, onResume, initial }: ReportBodyProps) {
  const report = useReport(profileId, client);
  return (
    <div className="rp-body" data-narrow={narrow ? 'true' : 'false'}>
      <header className="rp-head">
        <div className="rp-head-text">
          <p className="kicker">Where can I go next · beta · only you see this sheet</p>
          <h2 id={titleId} className="serif rp-title">
            Choose what the link shows.
          </h2>
        </div>
        {onClose && (
          <button type="button" className="btn btn-quiet rp-close-btn" aria-label="Close" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true">
              <path d="M2 2l10 10M12 2L2 12" />
            </svg>
          </button>
        )}
      </header>

      {report.phase === 'loading' && (
        <div className="rp-stack" role="status">
          <p className="rp-note">Opening your report…</p>
          <div className="rp-skeleton" aria-hidden="true" />
        </div>
      )}
      {report.phase === 'failed' && (
        <div className="rp-stack">
          <p className="rp-error" role="alert">
            {report.loadProblem}
          </p>
          <div>
            <button type="button" className="btn rp-action" onClick={report.reload}>
              Try again
            </button>
          </div>
        </div>
      )}
      {report.phase === 'ready' &&
        report.view &&
        report.draft &&
        (report.view.blocked ? (
          <Blocked kind={report.view.blocked} onAnalyse={onAnalyse} onSignIn={onSignIn} />
        ) : (
          <Panel report={report} view={report.view} draft={report.draft} narrow={narrow} onPrepare={onPrepare} onResume={onResume} initial={initial} />
        ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */

function Blocked({ kind, onAnalyse, onSignIn }: { kind: ReportBlocked; onAnalyse: () => void; onSignIn?: () => void }) {
  if (kind === 'sample') {
    return (
      <div className="rp-blocked">
        <p>A report needs your own resume. This graph is the sample everyone sees.</p>
        <button type="button" className="btn btn-primary rp-action" onClick={onAnalyse}>
          Analyse my resume
        </button>
      </div>
    );
  }
  return (
    <div className="rp-blocked">
      <p>Sharing needs an account, so you can turn the link off later. Sign in and this graph is saved to it.</p>
      {onSignIn && (
        <button type="button" className="btn btn-primary rp-action" onClick={onSignIn}>
          Sign in
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

interface ChoiceDef {
  key: Exclude<keyof ReportChoices, 'hiddenQuotes'>;
  label: string;
  hint: (view: ReportView) => string;
}

const CHOICES: readonly ChoiceDef[] = [
  {
    key: 'showName',
    label: 'Your first name and initial',
    hint: (v) => (v.offered.person.name ? `The page says “${v.offered.person.name}”; off, it says “This person”.` : 'Off, the page says “This person”.'),
  },
  {
    key: 'showOrgs',
    label: 'Employer and client names',
    hint: () => 'Off by default: they read “their employer” in the quotes. A client named inside a sentence can slip through, so read every line.',
  },
  {
    key: 'showQuotes',
    label: 'The resume lines behind each role',
    hint: () => 'Without them the report is just the role titles and their counts.',
  },
  {
    key: 'openToWork',
    label: 'Open to work',
    hint: () => "Off by default. The report never says why you're looking.",
  },
  {
    key: 'showNearMisses',
    label: 'Roles that came close',
    hint: () => 'Off by default. You always see them here; on, visitors see them too.',
  },
];

interface PanelProps {
  report: ReportModel;
  view: ReportView;
  draft: ReportDraft;
  narrow: boolean;
  onPrepare: (roleId: string) => void;
  onResume?: () => void;
  initial: ReportBodyProps['initial'];
}

function Panel({ report, view, draft, narrow, onPrepare, onResume, initial }: PanelProps) {
  const base = useId();
  const { choices } = draft;
  const linked = isShared(view);
  const noOrgs = !choices.showOrgs && view.orgsFound === 0;

  const status = draft.shared && !linked ? 'Making your link…' : !draft.shared && linked ? 'Turning the link off…' : linked ? 'Sharing is on' : 'Sharing is off';
  const statusSub = linked ? 'Anyone with the link can see the page.' : 'Nothing is public until you make a link.';

  return (
    <div className="rp-grid">
      <div className="rp-side">
        <div className="rp-status">
          <span className="rp-status-text">
            <span className="rp-status-line">{status}</span>
            <span className="rp-fine">{statusSub}</span>
          </span>
          <span className="rp-saving" role="status">
            {report.saving ? 'Saving…' : ''}
          </span>
        </div>

        {report.problem && (
          <p className="rp-error" role="alert">
            {report.problem}
          </p>
        )}

        {noOrgs && <NoOrgsNote onResume={onResume} />}

        <fieldset className="rp-choices">
          <legend className="sr-only">What the link shows</legend>
          {CHOICES.map((c) => (
            <label key={c.key} className="rp-choice">
              <input
                type="checkbox"
                checked={choices[c.key]}
                aria-describedby={`${base}-${c.key}`}
                onChange={(e) => report.change({ [c.key]: e.currentTarget.checked } as Partial<ReportChoices>)}
              />
              <span className="rp-choice-text">
                <span className="rp-choice-label">{c.label}</span>
                <span id={`${base}-${c.key}`} className="rp-hint">
                  {c.hint(view)}
                </span>
              </span>
            </label>
          ))}
        </fieldset>

        <Actions report={report} view={view} draft={draft} initial={initial} />
      </div>

      <section className="rp-view m-graph" aria-labelledby={`${base}-view`}>
        <h3 id={`${base}-view`} className="kicker">
          What a visitor sees
        </h3>
        <ReportPreview content={view.public} offerUrl={view.offerUrl} narrow={narrow} />
        <p className="rp-fine">
          {report.saving ? 'Updating after your change…' : linked ? 'This is the page at your link now.' : 'A preview. Nothing is public until you make a link.'}
        </p>
        <div className="stick is-flow m-turn-l rp-stick">
          <p className="postit pen rp-postit" style={{ '--pp': 'var(--paper-yellow)' } as CSSProperties}>
            Laid off is nobody's business. Your work is the story.
          </p>
        </div>
      </section>

      <div className="rp-side rp-side-end">
        <Quotes report={report} view={view} draft={draft} />
        <NearMisses view={view} shown={choices.showNearMisses} onPrepare={onPrepare} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function NoOrgsNote({ onResume }: { onResume?: () => void }) {
  const go = (e: MouseEvent<HTMLAnchorElement>) => {
    if (!onResume || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onResume();
  };
  return (
    <p className="rp-warn" role="note">
      We couldn't find your employers' names, so no resume lines are shared yet.{' '}
      <a href="/resume" onClick={go}>
        Open your resume once
      </a>{' '}
      so we can hide them, or turn on employer names after reading every line.
    </p>
  );
}

/* ------------------------------------------------------------------ */

function Quotes({ report, view, draft }: { report: ReportModel; view: ReportView; draft: ReportDraft }) {
  const roles = view.offered.roles.filter((r) => r.quotes.length > 0);
  const hidden = draft.choices.hiddenQuotes;
  const full = hidden.length >= REPORT_HIDDEN_MAX;
  const headId = useId();

  let body: ReactNode;
  if (!draft.choices.showQuotes) {
    body = <p className="rp-note">Resume lines are off, so the link quotes none of them.</p>;
  } else if (roles.length === 0) {
    body = <p className="rp-note">No resume line backs a listed role yet.</p>;
  } else {
    body = (
      <>
        {roles.map((r) => (
          <div key={r.id} className="rp-qgroup">
            <h4 className="rp-qrole">{r.title}</h4>
            <ul className="rp-qlist">
              {r.quotes.map((q) => {
                const isHidden = hidden.includes(q.id);
                return (
                  <li key={q.id} className="rp-qrow" data-hidden={isHidden ? 'true' : 'false'}>
                    <Switch on={!isHidden} disabled={!isHidden && full} label={`Show this line: ${q.text}`} onToggle={() => report.toggleQuote(q.id)} />
                    <span className="rp-qtext">
                      <span>“{q.text}”</span>
                      <span className="m-cite">
                        {q.source}
                        {isHidden ? ' · hidden' : ''}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
        {full && <p className="rp-fine">You can hide up to {REPORT_HIDDEN_MAX} lines. Show one again to hide another.</p>}
      </>
    );
  }

  return (
    <section className="rp-section" aria-labelledby={headId}>
      <div className="rp-section-head">
        <h3 id={headId} className="title rp-section-title">
          The lines it quotes
        </h3>
        <p className="rp-fine">Read each one. Hide any line; hiding never changes its words.</p>
      </div>
      {body}
    </section>
  );
}

function Switch({ on, disabled, label, onToggle }: { on: boolean; disabled: boolean; label: string; onToggle: () => void }) {
  return (
    <button type="button" role="switch" className="rp-switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={onToggle}>
      <span className="rp-track" aria-hidden="true">
        <span className="rp-knob" />
      </span>
    </button>
  );
}

/* ------------------------------------------------------------------ */

function NearMisses({ view, shown, onPrepare }: { view: ReportView; shown: boolean; onPrepare: (roleId: string) => void }) {
  const misses = view.offered.nearMisses;
  const headId = useId();
  if (misses.length === 0) return null;
  return (
    <section className="rp-section" aria-labelledby={headId}>
      <div className="rp-section-head">
        <h3 id={headId} className="title rp-section-title">
          Close, not yet
        </h3>
        <p className="rp-fine">{shown ? 'Visitors see these too.' : 'Only you see these.'}</p>
      </div>
      <ul className="rp-misses">
        {misses.map((m) => (
          <li key={m.id} className="rp-miss">
            <span className="rp-miss-title">{m.title}</span>
            {m.missing.length > 0 && <span className="rp-hint">Not shown yet: {m.missing.join(' · ')}</span>}
            <button type="button" className="rp-textbtn" onClick={() => onPrepare(m.id)}>
              What would show it
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------ */

function Actions({ report, view, draft, initial }: { report: ReportModel; view: ReportView; draft: ReportDraft; initial: ReportBodyProps['initial'] }) {
  const base = useId();
  const linked = isShared(view);
  const [confirming, setConfirming] = useState(initial?.confirmLink ?? false);
  const [copied, setCopied] = useState(initial?.copied ?? false);
  const [copyProblem, setCopyProblem] = useState('');
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const pageUrl = view.pageUrl ?? '';
  const pageHref = safeHref(view.pageUrl);
  const busy = report.linking || draft.shared !== linked;

  const copy = async () => {
    window.clearTimeout(timer.current);
    try {
      if (!navigator.clipboard) throw new Error('no clipboard');
      await navigator.clipboard.writeText(pageUrl);
      setCopyProblem('');
      setCopied(true);
      timer.current = window.setTimeout(() => setCopied(false), COPIED_MS);
    } catch {
      setCopied(false);
      setCopyProblem(COPY_FAILED);
    }
  };

  if (!linked) {
    return (
      <div className="rp-actions">
        <div className="rp-row">
          <button type="button" className="btn btn-primary rp-action" disabled={busy} onClick={() => report.setShared(true)}>
            {draft.shared ? 'Making your link…' : 'Make a link'}
          </button>
        </div>
        <p className="rp-fine">The link works only while it's shared. A new link turns the old one off.</p>
      </div>
    );
  }

  return (
    <div className="rp-actions">
      <label className="kicker" htmlFor={`${base}-link`}>
        Your link
      </label>
      <div className="rp-linkrow">
        <input id={`${base}-link`} className="rp-input" type="text" readOnly value={pageUrl} onFocus={(e) => e.currentTarget.select()} />
        <button type="button" className="btn btn-primary rp-action" onClick={() => void copy()}>
          {copied ? 'Copied' : 'Copy the link'}
        </button>
      </div>
      <span className="sr-only" role="status">
        {copied ? 'Copied to your clipboard.' : ''}
      </span>
      {copyProblem && (
        <p className="rp-error" role="alert">
          {copyProblem}
        </p>
      )}

      {confirming ? (
        <div className="rp-confirm" role="group" aria-label="Make a new link">
          <p>The old link stops working at once. Anyone who has it sees a page that is gone.</p>
          <div className="rp-row">
            <button
              type="button"
              className="btn btn-primary rp-action"
              disabled={busy}
              onClick={() => {
                void report.makeNewLink().then(() => setConfirming(false));
              }}
            >
              {report.linking ? 'Making it…' : 'Make a new link'}
            </button>
            <button type="button" className="btn btn-quiet rp-action" disabled={report.linking} onClick={() => setConfirming(false)}>
              Keep this one
            </button>
          </div>
        </div>
      ) : (
        <div className="rp-row">
          {pageHref && (
            <a className="btn rp-action" href={pageHref} target="_blank" rel="noopener noreferrer">
              Preview as a visitor
            </a>
          )}
          <button type="button" className="btn rp-action" disabled={busy} onClick={() => setConfirming(true)}>
            Make a new link
          </button>
          <button type="button" className="btn btn-quiet rp-action" disabled={busy} onClick={() => report.setShared(false)}>
            Stop sharing
          </button>
        </div>
      )}
      <p className="rp-fine">The link works only while it's shared. A new link stops the old one at once. Stop sharing and the page is gone.</p>
    </div>
  );
}
