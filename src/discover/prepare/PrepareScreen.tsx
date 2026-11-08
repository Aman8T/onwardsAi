/**
 * Prepare for this role (board 8 of the "Discover with AI" canvas): the page a person reads before they apply.
 *
 * It asks `POST /api/jobsearch/prepare` once on mount (a few seconds the first time, cached after) and shows, in this
 * order: the asks to lead with, the gaps with a nearest piece of evidence and a small step, the questions the posting is
 * likely to lead to with a story built from the person's own evidence, a checklist, and the interview practice that is
 * coming next. Beside it: the role in a few lines, Save and Share. With auto-apply on, the checklist's apply step offers
 * Apply for me with the tailored resume and ticks itself once the application is sent; the employer's link in the
 * heading turns quiet.
 *
 * Honesty, as the footer says: everything quoted is word for word from the posting or the profile; what is ours is
 * marked as a suggestion; a dashed box is for what only the person knows and is never filled in for them. What they type
 * and tick stays in this browser, per role. The fit is a word and the pips are counts, never a score.
 */
import { motion, useReducedMotion } from 'motion/react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ApplicationSummary } from '../../../shared/apply';
import type { AiJob, RolePrep, RoleRequest } from '../../../shared/jobsearch';
import { ApiError } from '../../lib/api';
import { EASE } from '../../lib/ui';
import '../../styles/discover-more.css';
import { useTracker } from '../applications/tracker';
import type { ApplyCtl } from '../roles/parts';
import { TROUBLE, postPrepare } from './api';
import { AskPips } from './Pips';
import { ChecklistSection, GapSection, LeadSection, PracticeSection, QuestionsSection } from './sections';
import { checklistKey, parseChecklist, parseStory, storyKey, useStoredRecord } from './storage';
import { FIT_CLASS, FIT_WORD, LEVEL_WORD, applyHref, applyText, foundLine, freshLines, placeLine, salaryLine } from './words';
import { quoted } from './parts';

export interface PrepareScreenProps {
  profileId: string;
  runId: string;
  jobId: string;
  /** The role as the search showed it, when the caller has it (else the screen finds it in the latest run). */
  job?: AiJob;
  onBack: () => void;
  onSeeMatch: (job: AiJob, runId: string) => void;
  onShare: (job: AiJob, runId: string) => void;
  /** The dev gallery passes a scripted loader; the app leaves it out (the real `postPrepare`). */
  loadPrep?: (req: RoleRequest, signal?: AbortSignal) => Promise<RolePrep>;
  /** Apply for me on this role (`applyCtlOf`); absent while auto-apply is off. */
  apply?: ApplyCtl & { note?: string };
  /** The application for this role through Leafout, if there is one. */
  application?: ApplicationSummary;
}

type Load = { status: 'loading' } | { status: 'ready'; prep: RolePrep } | { status: 'error'; message: string };

const OFFLINE = "We couldn't reach Leafout. Check your connection and try again.";

/** The sentence the person reads for a failed load: the server's own when it sent one. */
function sentenceOf(err: unknown): string {
  if (err instanceof ApiError) return err.message || TROUBLE;
  return err instanceof TypeError ? OFFLINE : TROUBLE;
}

export function PrepareScreen(props: PrepareScreenProps) {
  // a different role is a fresh page: its own load, its own boxes, its own ticks
  return <PrepareBody key={`${props.profileId}:${props.runId}:${props.jobId}`} {...props} />;
}

function PrepareBody({ profileId, runId, jobId, job: given, onBack, onSeeMatch, onShare, loadPrep, apply, application }: PrepareScreenProps) {
  const reduced = useReducedMotion() ?? false;
  const ids = useId();
  const h = { lead: `${ids}-lead`, gap: `${ids}-gap`, questions: `${ids}-questions`, before: `${ids}-before`, practice: `${ids}-practice` };
  const tracker = useTracker(profileId);
  const tracked = tracker.get(jobId);
  // a role opened from Your applications is found there when the caller did not pass it
  const job = given ?? tracked?.job;

  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [announce, setAnnounce] = useState('');
  const loader = useRef(loadPrep);
  loader.current = loadPrep;
  const titleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const ctl = new AbortController();
    setLoad((prev) => (prev.status === 'loading' ? prev : { status: 'loading' }));
    (loader.current ?? postPrepare)({ profileId, runId, jobId }, ctl.signal).then(
      (prep) => {
        if (ctl.signal.aborted) return;
        setLoad({ status: 'ready', prep });
        setAnnounce('Your prep page is ready.');
      },
      (err: unknown) => {
        if (ctl.signal.aborted) return;
        setLoad({ status: 'error', message: sentenceOf(err) });
      },
    );
    return () => ctl.abort();
  }, [profileId, runId, jobId, attempt]);

  // the page opens at its title, for a reader who arrived by keyboard or with a screen reader
  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  const [story, setStory] = useStoredRecord<string>(storyKey(profileId, jobId), parseStory);
  const [ticks, setTick] = useStoredRecord<true>(checklistKey(profileId, jobId), parseChecklist);
  const slots = { get: (slot: string) => story[slot] ?? '', set: (slot: string, value: string) => setStory(slot, value) };

  const prep = load.status === 'ready' ? load.prep : undefined;
  const name = job ? job.title : prep?.title;
  const employer = job ? job.employer : prep?.employer;
  const heading = name && employer ? `${name} at ${employer}` : name || 'Prepare for this role';
  const href = job ? applyHref(job) : undefined;
  const id = job?.id ?? jobId;
  // Apply for me is the action on this page (the checklist): the employer's link in the heading turns quiet
  const applyLeads = !!apply?.apply && apply.apply.kind !== 'own_account';

  const markApplied = (on: boolean) => {
    if (!job) return;
    if (on) {
      if (!tracker.isSaved(id)) tracker.save(job, runId);
      tracker.setStage(id, 'applied');
      setAnnounce('Moved to Applied in Your applications.');
    } else if (tracked?.stage === 'applied') {
      tracker.setStage(id, 'saved');
      tracker.setAppliedAt(id, undefined);
      setAnnounce('Moved back to Saved.');
    }
  };

  const jump = useCallback(
    (target: string) => {
      const el = document.getElementById(target);
      if (!el) return;
      el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
      el.focus({ preventScroll: true });
    },
    [reduced],
  );

  return (
    <div className="dm-page dm-prep" aria-busy={load.status === 'loading'}>
      <p className="sr-only" role="status">
        {announce}
      </p>

      <header className="dm-head">
        <button type="button" className="dm-back" onClick={onBack}>
          <span aria-hidden="true">←</span> Discover
        </button>
        <p className="kicker">Prepare</p>
        <h1 className="serif dm-title" ref={titleRef} tabIndex={-1}>
          {heading}
        </h1>
        {job && (
          <div className="dm-head-row">
            <span className={FIT_CLASS[job.fit]}>{FIT_WORD[job.fit]}</span>
            <span className="dm-lvl">{LEVEL_WORD[job.level]}</span>
            <span className="dm-muted">{placeLine(job)}</span>
            {href && (
              <a className={`btn ${applyLeads ? 'btn-quiet' : 'btn-primary'} btn-sm dm-btn-sm`} href={href} target="_blank" rel="noopener noreferrer" aria-label={`${applyText(job)}, opens in a new tab`}>
                {applyText(job)}
              </a>
            )}
          </div>
        )}
      </header>

      <div className="dm-cols">
        <div className="dm-main">
          {load.status === 'loading' && (
            <>
              <p className="dm-muted dm-loading-line" role="status">
                Reading the posting against your evidence. It takes a few seconds the first time.
              </p>
              <Skeleton />
            </>
          )}

          {load.status === 'error' && (
            <section className="panel dm-sec dm-error">
              <p className="dm-error-text" role="alert">
                {load.message}
              </p>
              <div className="dm-actions">
                <button type="button" className="btn btn-primary dm-btn" onClick={() => setAttempt((n) => n + 1)}>
                  Try again
                </button>
                <button type="button" className="btn btn-quiet dm-btn" onClick={onBack}>
                  Back to roles
                </button>
              </div>
            </section>
          )}

          {prep && (
            <motion.div className="dm-main-in" initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25, ease: EASE }}>
              <LeadSection headingId={h.lead} lead={prep.lead} hasGaps={prep.gaps.length > 0} job={job} onSeeMatch={job ? () => onSeeMatch(job, runId) : undefined} />
              <GapSection headingId={h.gap} gaps={prep.gaps} slots={slots} />
              <QuestionsSection headingId={h.questions} questions={prep.questions} gaps={prep.gaps} slots={slots} />
              <ChecklistSection
                headingId={h.before}
                job={job}
                ticks={ticks}
                onTick={(item, on) => setTick(item, on ? true : undefined)}
                onSeeMatch={job ? () => onSeeMatch(job, runId) : undefined}
                applyHref={href}
                stage={tracked?.stage}
                appliedAt={tracked?.appliedAt}
                onApplied={markApplied}
                {...(apply ? { apply } : {})}
                {...(application ? { application } : {})}
              />
              <PracticeSection headingId={h.practice} runId={runId} jobId={jobId} />
            </motion.div>
          )}
        </div>

        <aside className="dm-side" aria-label="About this role">
          <section className="panel dm-summary" aria-label="This role">
            <p className="kicker">This role</p>
            {job ? (
              <>
                <div className="dm-summary-fit">
                  <span className={FIT_CLASS[job.fit]}>{FIT_WORD[job.fit]}</span>
                  <span className="dm-lvl">{LEVEL_WORD[job.level]}</span>
                </div>
                {job.levelQuote && (
                  <p className="dm-muted dm-small">
                    The posting says: <span className="dm-ink">{quoted(job.levelQuote)}</span>
                  </p>
                )}
                <AskPips asks={job.asks} />
                <div className="dm-meta">
                  <p className="dm-place">{placeLine(job)}</p>
                  <p className="dm-muted dm-small">{foundLine(job)}</p>
                  {freshLines(job).map((l) => (
                    <p key={l} className="dm-muted dm-small">
                      {l}
                    </p>
                  ))}
                  <p className="dm-muted dm-small">{salaryLine(job)}</p>
                </div>
                <div className="dm-side-actions">
                  <button type="button" className="btn dm-btn dm-toggle" aria-pressed={tracker.isSaved(id)} onClick={() => tracker.toggle(job, runId)}>
                    <svg width="16" height="16" viewBox="0 0 16 16" fill={tracker.isSaved(id) ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true">
                      <path d="M4 2h8v12l-4-3-4 3z" />
                    </svg>
                    {tracker.isSaved(id) ? 'Saved · in Your applications' : 'Save'}
                  </button>
                  <button type="button" className="btn dm-btn" onClick={() => onShare(job, runId)}>
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M8 10V2.5M5 5l3-3 3 3M3 9v4.5h10V9" />
                    </svg>
                    Share
                  </button>
                </div>
              </>
            ) : (
              <p className="dm-muted dm-small">This role's details are not on this page. Open it from the list to save or share it.</p>
            )}
          </section>

          {prep && (
            <nav className="panel dm-toc" aria-label="On this page">
              <p className="kicker">On this page</p>
              {(
                [
                  [h.lead, 'Lead with these'],
                  [h.gap, 'Close or explain the gap'],
                  [h.questions, 'Questions they are likely to ask'],
                  [h.before, 'Before you apply'],
                  [h.practice, 'Practice the interview'],
                ] as const
              ).map(([target, label]) => (
                <button key={target} type="button" className="dm-toc-item" onClick={() => jump(target)}>
                  {label}
                </button>
              ))}
            </nav>
          )}
        </aside>
      </div>

      <p className="dm-footer">Everything quoted comes word for word from the posting or your profile. Suggestions are ours, marked as suggestions.</p>
    </div>
  );
}

/** The page while it is being written: outlined boxes with faint bars, nothing that moves. */
function Skeleton() {
  return (
    <div className="dm-skel" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div className="panel dm-sec" key={i}>
          <span className="dm-skel-bar dm-skel-bar-title" />
          <span className="dm-skel-bar" />
          <span className="dm-skel-bar dm-skel-bar-short" />
        </div>
      ))}
    </div>
  );
}
