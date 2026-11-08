/**
 * Share this role: "Ask for a referral" (a short note from `POST /api/jobsearch/referral`) and "Just the link"
 * (board 11 of the "Discover with AI" canvas).
 *
 * A real modal dialog (the app's shared `Sheet`): focus lands in it, Tab stays inside, Escape closes, and focus goes back
 * to whatever opened it; under 760 px it is a bottom sheet. Nothing about the person goes into a link: the link is the
 * employer's own posting, and the only other thing that can leave is the note they read and edit here.
 *
 * The referral note is one short paragraph: three sentences the server wrote from the posting, the match the search
 * already worked out and the person's own evidence (who they are, why they fit, why the employer is where they want to
 * go), each checked, then the ask, under "Hi [name],". They can name the connection (`[name]` fills in live; left empty,
 * what is copied or shared says "Hi there,"), edit every word, copy it with the link, send it through the system share
 * sheet where there is one, or ask for a new draft.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { AiJob, ReferralNote, RoleRequest } from '../../../shared/jobsearch';
import { Sheet } from '../../auth/Sheet';
import '../../styles/discover-more.css';
import { safeHref } from '../util';
import { TROUBLE, postReferral } from '../prepare/api';
import { ApiError } from '../../lib/api';
import { canShare, copyText, shareData } from './clipboard';
import { FALLBACK_NAME, cleanName, compose, countWords, edit, fill, keyLine, outgoing, withLink } from './note';

export interface ShareDialogProps {
  profileId: string;
  /** Absent: a role kept from before runs were stored; only "Just the link" is offered. */
  runId?: string;
  job: AiJob;
  onClose: () => void;
  /** The dev gallery passes a scripted loader; the app leaves it out (the real `postReferral`). */
  loadReferral?: (req: RoleRequest, signal?: AbortSignal) => Promise<ReferralNote>;
}

type Tab = 'referral' | 'link';
type Draft = { status: 'loading' } | { status: 'ready'; note: ReferralNote } | { status: 'error'; message: string };

const OFFLINE = "We couldn't reach Leafout. Check your connection and try again.";
const COPY_FAILED = "Couldn't copy. Select the text and copy it yourself.";
const SHARE_FAILED = "Couldn't open sharing. Copy the note instead.";
/** How long "Copied" stays on the page. */
const COPIED_MS = 2500;

const sentenceOf = (err: unknown): string => (err instanceof ApiError ? err.message || TROUBLE : err instanceof TypeError ? OFFLINE : TROUBLE);

export function ShareDialog(props: ShareDialogProps) {
  const titleId = useId();
  return (
    <Sheet open onClose={props.onClose} labelledBy={titleId} className="dm-share-sheet">
      <ShareBody {...props} titleId={titleId} />
    </Sheet>
  );
}

function ShareBody({ profileId, runId, job, onClose, loadReferral, titleId }: ShareDialogProps & { titleId: string }) {
  const hasNote = runId !== undefined && runId !== '';
  const [tab, setTab] = useState<Tab>(hasNote ? 'referral' : 'link');
  const ids = useId();
  const tabId = (t: Tab) => `${ids}-tab-${t}`;
  const panelId = (t: Tab) => `${ids}-panel-${t}`;
  const tabs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});

  const [status, setStatus] = useState('');
  const timer = useRef<number | undefined>(undefined);
  const say = useCallback((message: string) => {
    setStatus(message);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setStatus(''), COPIED_MS);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const onTabKeys = (e: KeyboardEvent<HTMLDivElement>) => {
    const order: Tab[] = ['referral', 'link'];
    const at = order.indexOf(tab);
    const next = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? order[(at + 1) % order.length] : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? order[(at + order.length - 1) % order.length] : e.key === 'Home' ? order[0] : e.key === 'End' ? order[order.length - 1] : undefined;
    if (!next) return;
    e.preventDefault();
    setTab(next);
    tabs.current[next]?.focus();
  };

  return (
    <div className="dm-share">
      <div className="dm-share-head">
        <div>
          <h2 className="title dm-share-title" id={titleId}>
            Share this role
          </h2>
          <p className="dm-muted dm-small">
            {job.title} · {job.employer}
          </p>
        </div>
        <button type="button" className="dm-iconbtn" aria-label="Close" onClick={onClose}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
      </div>

      {hasNote && (
        <div className="dm-tabs" role="tablist" aria-label="How to share" onKeyDown={onTabKeys}>
          {(
            [
              ['referral', 'Ask for a referral'],
              ['link', 'Just the link'],
            ] as const
          ).map(([t, label]) => (
            <button
              key={t}
              ref={(el) => {
                tabs.current[t] = el;
              }}
              type="button"
              role="tab"
              id={tabId(t)}
              className="dm-tab"
              aria-selected={tab === t}
              aria-controls={panelId(t)}
              tabIndex={tab === t ? 0 : -1}
              onClick={() => setTab(t)}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {hasNote && (
        <div role="tabpanel" id={panelId('referral')} aria-labelledby={tabId('referral')} hidden={tab !== 'referral'} className="dm-panel">
          <ReferralPanel profileId={profileId} runId={runId} job={job} loadReferral={loadReferral} say={say} status={status} />
        </div>
      )}
      <div role={hasNote ? 'tabpanel' : undefined} id={panelId('link')} aria-labelledby={hasNote ? tabId('link') : undefined} hidden={hasNote && tab !== 'link'} className="dm-panel">
        <LinkPanel job={job} say={say} status={status} />
      </div>
    </div>
  );
}

/* ───────────────────────── ask for a referral ───────────────────────── */

function ReferralPanel({ profileId, runId, job, loadReferral, say, status }: { profileId: string; runId: string; job: AiJob; loadReferral: ShareDialogProps['loadReferral']; say: (m: string) => void; status: string }) {
  const [draft, setDraft] = useState<Draft>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  /** The note with `[name]` where the name goes, as edited; and the draft's own text, to tell whether it was edited. */
  const [template, setTemplate] = useState('');
  const [baseline, setBaseline] = useState('');
  const [to, setTo] = useState('');
  const [rewriting, setRewriting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [rewriteError, setRewriteError] = useState('');
  const loader = useRef(loadReferral);
  loader.current = loadReferral;
  const ids = useId();
  const keepRef = useRef<HTMLButtonElement>(null);
  const rewriteRef = useRef<HTMLButtonElement>(null);
  const wasConfirming = useRef(false);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  // the question replaces the Rewrite button: focus moves into it, and comes back to Rewrite when it is answered
  useEffect(() => {
    if (confirming) keepRef.current?.focus();
    else if (wasConfirming.current) rewriteRef.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);

  const adopt = useCallback((note: ReferralNote) => {
    const text = compose(note);
    setTemplate(text);
    setBaseline(text);
    setDraft({ status: 'ready', note });
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    setDraft((prev) => (prev.status === 'loading' ? prev : { status: 'loading' }));
    (loader.current ?? postReferral)({ profileId, runId, jobId: job.id }, ctl.signal).then(
      (note) => {
        if (!ctl.signal.aborted) adopt(note);
      },
      (err: unknown) => {
        if (!ctl.signal.aborted) setDraft({ status: 'error', message: sentenceOf(err) });
      },
    );
    return () => ctl.abort();
  }, [profileId, runId, job.id, attempt, adopt]);

  const name = cleanName(to);
  const shown = fill(template, name);
  const edited = template !== baseline;

  // the note grows with its words, so there is one scroll (the dialog's) rather than a scroll inside a scroll
  const fitNote = useCallback(() => {
    const el = noteRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, []);
  useLayoutEffect(fitNote, [fitNote, shown, draft.status]);
  // the dialog's own scrollbar appears once the note is as tall as its words, which narrows the note and wraps one more line
  useEffect(() => {
    const el = noteRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    let width = el.clientWidth;
    const watch = new ResizeObserver(() => {
      if (el.clientWidth === width) return;
      width = el.clientWidth;
      fitNote();
    });
    watch.observe(el);
    return () => watch.disconnect();
  }, [fitNote, draft.status]);

  const rewrite = async () => {
    setConfirming(false);
    setRewriteError('');
    setRewriting(true);
    try {
      adopt(await (loader.current ?? postReferral)({ profileId, runId, jobId: job.id, fresh: true }));
      say('A new draft is ready.');
    } catch (err) {
      setRewriteError(sentenceOf(err));
    } finally {
      setRewriting(false);
    }
  };

  if (draft.status === 'loading') {
    return (
      <div className="dm-draft-loading" aria-busy="true">
        <p className="dm-muted" role="status">
          Drafting a short note from the posting and your resume. It takes a few seconds.
        </p>
        <div className="dm-skel" aria-hidden="true">
          <span className="dm-skel-bar" />
          <span className="dm-skel-bar" />
          <span className="dm-skel-bar dm-skel-bar-short" />
        </div>
      </div>
    );
  }

  if (draft.status === 'error') {
    return (
      <div className="dm-draft-error">
        <p className="dm-error-text" role="alert">
          {draft.message}
        </p>
        <div className="dm-actions">
          <button type="button" className="btn btn-primary dm-btn" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  const { note } = draft;
  // what leaves the app never has a `[name]` in it: an empty To field makes the greeting "Hi there,"
  const going = outgoing(template, name);
  const send = withLink(going, note.link);
  const ready = { title: `${job.title} at ${job.employer}`, text: going, url: note.link };

  return (
    <div className="dm-referral">
      <div className="dm-tofield">
        <div className="dm-to">
          <label htmlFor={`${ids}-to`} className="kicker">
            To<span className="sr-only"> (their first name)</span>
          </label>
          <input id={`${ids}-to`} className="field dm-box" type="text" autoComplete="off" maxLength={60} placeholder="Their first name" value={to} aria-describedby={`${ids}-to-hint`} onChange={(e) => setTo(e.target.value)} />
        </div>
        <p id={`${ids}-to-hint`} className="dm-muted dm-small dm-tohint">
          Left empty, the note says “Hi {FALLBACK_NAME}”.
        </p>
      </div>

      <div className="dm-notebox" aria-busy={rewriting}>
        <div className="dm-notehead">
          <label htmlFor={`${ids}-note`} className="dm-muted dm-small">
            Your note
          </label>
          <span className="dm-muted dm-small">{countWords(shown)}</span>
        </div>
        <div className="dm-notefield">
          <textarea ref={noteRef} id={`${ids}-note`} className="dm-notetext" rows={6} value={shown} readOnly={rewriting} onChange={(e) => setTemplate(edit(template, name, e.target.value))} />
          <p className="dm-linkline">{note.link}</p>
        </div>
      </div>

      <div className="dm-key">
        <p className="kicker">What each sentence draws on</p>
        <ol className="dm-keylist">
          {note.lines.map((line, i) => (
            <li key={i} className="dm-cite">
              {keyLine(line, i)}
            </li>
          ))}
        </ol>
      </div>

      <div className="dm-actions">
        <button
          type="button"
          className="btn btn-primary dm-btn"
          onClick={async () => say((await copyText(send)) ? 'Copied' : COPY_FAILED)}
        >
          Copy note and link
        </button>
        {canShare(ready) && (
          <button
            type="button"
            className="btn dm-btn"
            onClick={async () => {
              const result = await shareData(ready);
              if (result === 'failed') say(SHARE_FAILED);
            }}
          >
            Share…
          </button>
        )}
        {confirming ? (
          <span className="dm-confirm" role="group" aria-label="Replace your edits with a new draft">
            <span className="dm-muted dm-small">A new draft replaces your edits.</span>
            <button type="button" className="btn btn-sm dm-btn-sm" onClick={rewrite}>
              Rewrite
            </button>
            <button ref={keepRef} type="button" className="btn btn-quiet btn-sm dm-btn-sm" onClick={() => setConfirming(false)}>
              Keep my note
            </button>
          </span>
        ) : (
          <button ref={rewriteRef} type="button" className="btn btn-quiet dm-btn" aria-disabled={rewriting || undefined} onClick={() => !rewriting && (edited ? setConfirming(true) : rewrite())}>
            {rewriting ? 'Rewriting…' : 'Rewrite'}
          </button>
        )}
        <span className="dm-copied" role="status" aria-live="polite">
          {status}
        </span>
      </div>
      {rewriteError && (
        <p className="dm-error-text" role="alert">
          {rewriteError}
        </p>
      )}

      <p className="dm-muted dm-small">Every quote is checked against the posting and your profile. The note uses lines from your own resume, so read it before you send it.</p>
    </div>
  );
}

/* ───────────────────────── just the link ───────────────────────── */

function LinkPanel({ job, say, status }: { job: AiJob; say: (m: string) => void; status: string }) {
  const ids = useId();
  const [line, setLine] = useState('');
  const link = safeHref(job.applyUrl);
  if (!link) {
    return <p className="dm-muted">This role has no posting link we can share.</p>;
  }
  const note = line.trim();
  const send = note ? `${note}\n\n${link}` : link;
  const data = { title: `${job.title} at ${job.employer}`, ...(note ? { text: note } : {}), url: link };
  return (
    <div className="dm-linkpanel">
      <div className="dm-field">
        <p className="dm-muted dm-small">Link to the posting</p>
        <p className="dm-linkbox">{link}</p>
      </div>
      <div className="dm-field">
        <label htmlFor={`${ids}-line`} className="dm-muted dm-small">
          One line to go with it (optional)
        </label>
        <input id={`${ids}-line`} className="field dm-box" type="text" autoComplete="off" maxLength={200} placeholder="Add a line, if you like" value={line} onChange={(e) => setLine(e.target.value)} />
      </div>
      <div className="dm-actions">
        <button type="button" className="btn btn-primary dm-btn" onClick={async () => say((await copyText(send)) ? 'Copied' : COPY_FAILED)}>
          {note ? 'Copy note and link' : 'Copy link'}
        </button>
        {canShare(data) && (
          <button
            type="button"
            className="btn dm-btn"
            onClick={async () => {
              if ((await shareData(data)) === 'failed') say(SHARE_FAILED);
            }}
          >
            Share…
          </button>
        )}
        <span className="dm-copied" role="status" aria-live="polite">
          {status}
        </span>
      </div>
      <p className="dm-muted dm-small">Only the employer's posting and your note are shared.</p>
    </div>
  );
}
