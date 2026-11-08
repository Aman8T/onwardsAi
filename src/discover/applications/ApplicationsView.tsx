/**
 * Your applications (board 10 of the "Discover with AI" canvas): the roles a person saved, and where each one stands.
 *
 * Five stages (Saved, Applied, Interviewing, Offer, Closed) as columns on a wide screen and as tabs under 760 px, each
 * with its count. A role moves by its own stage select, a real `<select>`; moving it to Applied for the first time writes
 * today's date, which can be changed. A note is one line. Everything is kept in this browser (see `tracker.ts`), and says
 * so. Removing a role asks first.
 *
 * A saved role that also has an application through Leafout (looked up by its listing) shows where that application
 * stands on the server, in words ("Waiting for you", "Sent 4 Nov"), and links to it ("Open the application"). The two
 * lists stay separate until the saved roles move to the server (a later decision).
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { ApplicationSummary } from '../../../shared/apply';
import { appliedLabel } from '../../apply/ApplyForMe';
import { applyPath } from '../../apply/route';
import { NARROW_QUERY, useMediaQuery } from '../../lib/ui';
import { actions } from '../../store';
import '../../styles/discover-more.css';
import { applyHref, applyText, dayLabel, FIT_CLASS, FIT_WORD, LEVEL_WORD, placeLine } from '../prepare/words';
import { MAX_NOTE, STAGES, STAGE_LABEL, useTracker } from './tracker';
import type { TrackStage, TrackedJob } from './tracker';

export interface ApplicationsViewProps {
  profileId: string;
  onPrepare: (item: TrackedJob) => void;
  onSeeMatch: (item: TrackedJob) => void;
  onShare: (item: TrackedJob) => void;
  onBack: () => void;
  /** Open the practice plan for a role; absent = Practice's plan screen for it. */
  onPractise?: (item: TrackedJob) => void;
  /** The person's applications through Leafout by Discover listing (`useApplications().byListing`); absent while auto-apply is off. */
  applications?: ReadonlyMap<string, ApplicationSummary>;
}

const roles = (n: number) => `${n} ${n === 1 ? 'role' : 'roles'}`;
const nameOf = (t: TrackedJob) => `${t.job.title} at ${t.job.employer}`;

export function ApplicationsView({ profileId, onPrepare, onSeeMatch, onShare, onBack, onPractise, applications }: ApplicationsViewProps) {
  const tracker = useTracker(profileId);
  const narrow = useMediaQuery(NARROW_QUERY);
  const base = useId();
  const [tab, setTab] = useState<TrackStage>('saved');
  const [confirming, setConfirming] = useState<string | null>(null);
  const [announce, setAnnounce] = useState('');
  /** After a role moves, the stage select it moved with is pressed again: focus follows it to its new place. */
  const follow = useRef<string | null>(null);
  /** After a role is removed, focus goes to the column it was in. */
  const refocusColumn = useRef<TrackStage | null>(null);
  const tabRefs = useRef<Partial<Record<TrackStage, HTMLButtonElement | null>>>({});

  const selectId = useCallback((id: string) => `${base}-stage-${id}`, [base]);
  const columnId = (stage: TrackStage) => `${base}-col-${stage}`;

  const columns = useMemo(
    () =>
      STAGES.map((stage) => ({
        stage,
        // newest saved first: stable while a note is typed, unlike an order by last change
        items: tracker.items.filter((t) => t.stage === stage).sort((a, b) => b.savedAt.localeCompare(a.savedAt)),
      })),
    [tracker.items],
  );
  const open = tracker.items.filter((t) => t.stage !== 'closed').length;
  const closed = tracker.items.length - open;

  useEffect(() => {
    if (follow.current) {
      document.getElementById(selectId(follow.current))?.focus({ preventScroll: true });
      follow.current = null;
    } else if (refocusColumn.current) {
      document.getElementById(columnId(refocusColumn.current))?.focus({ preventScroll: true });
      refocusColumn.current = null;
    }
  });

  const move = (t: TrackedJob, stage: TrackStage) => {
    tracker.setStage(t.job.id, stage);
    follow.current = t.job.id;
    if (narrow) setTab(stage);
    setAnnounce(`${nameOf(t)} moved to ${STAGE_LABEL[stage]}.`);
  };

  const remove = (t: TrackedJob) => {
    refocusColumn.current = t.stage;
    setConfirming(null);
    tracker.remove(t.job.id);
    setAnnounce(`${nameOf(t)} removed from Your applications.`);
  };

  const onTabKeys = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = STAGES.indexOf(tab);
    const next =
      e.key === 'ArrowRight' || e.key === 'ArrowDown'
        ? STAGES[(at + 1) % STAGES.length]
        : e.key === 'ArrowLeft' || e.key === 'ArrowUp'
          ? STAGES[(at + STAGES.length - 1) % STAGES.length]
          : e.key === 'Home'
            ? STAGES[0]
            : e.key === 'End'
              ? STAGES[STAGES.length - 1]
              : undefined;
    if (!next) return;
    e.preventDefault();
    setTab(next);
    tabRefs.current[next]?.focus();
  };

  const shown = narrow ? columns.filter((c) => c.stage === tab) : columns;

  return (
    <div className="dm-page dm-apps">
      <p className="sr-only" role="status">
        {announce}
      </p>

      <header className="dm-head">
        <button type="button" className="dm-back" onClick={onBack}>
          <span aria-hidden="true">←</span> Discover
        </button>
        <p className="kicker">Discover · your applications</p>
        <h1 className="serif dm-title">Your applications</h1>
        <div className="dm-head-row">
          <span className="dm-muted">Kept in this browser</span>
          <span className="chip">
            {open} open · {closed} closed
          </span>
        </div>
      </header>

      {tracker.items.length === 0 && (
        <section className="panel dm-sec">
          <p className="dm-muted">Nothing saved yet. Press Save on a role and it is kept here, with its stage, the date you applied and a note.</p>
          <div className="dm-actions">
            <button type="button" className="btn btn-primary dm-btn" onClick={onBack}>
              Back to roles
            </button>
          </div>
        </section>
      )}

      {narrow && (
        <div className="dm-stagetabs" role="tablist" aria-label="Stages" onKeyDown={onTabKeys}>
          {columns.map((c) => (
            <button
              key={c.stage}
              ref={(el) => {
                tabRefs.current[c.stage] = el;
              }}
              type="button"
              role="tab"
              id={`${base}-tab-${c.stage}`}
              className="dm-tab"
              aria-selected={tab === c.stage}
              aria-controls={columnId(c.stage)}
              tabIndex={tab === c.stage ? 0 : -1}
              onClick={() => setTab(c.stage)}
            >
              {STAGE_LABEL[c.stage]} <span className="numeral dm-tabcount">{c.items.length}</span>
            </button>
          ))}
        </div>
      )}

      <section className={narrow ? 'dm-board dm-board-one' : 'dm-board'} aria-label="Applications by stage">
        {shown.map((c) => (
          <div
            key={c.stage}
            id={columnId(c.stage)}
            className="dm-column"
            tabIndex={-1}
            {...(narrow ? { role: 'tabpanel', 'aria-labelledby': `${base}-tab-${c.stage}` } : { role: 'group', 'aria-label': `${STAGE_LABEL[c.stage]}, ${roles(c.items.length)}` })}
          >
            {!narrow && (
              <div className="dm-colhead-line">
                <h2 className="title dm-h2s">{STAGE_LABEL[c.stage]}</h2>
                <span className="numeral dm-muted dm-small">{c.items.length}</span>
              </div>
            )}
            {c.items.length === 0 && <p className="dm-empty">Nothing here yet.</p>}
            <ul className="dm-applist">
              {c.items.map((t) => (
                <Item
                  key={t.job.id}
                  item={t}
                  application={applications?.get(t.job.id)}
                  selectId={selectId(t.job.id)}
                  confirming={confirming === t.job.id}
                  onAskRemove={() => setConfirming(t.job.id)}
                  onKeep={() => setConfirming(null)}
                  onRemove={() => remove(t)}
                  onStage={(stage) => move(t, stage)}
                  onNote={(note) => tracker.setNote(t.job.id, note)}
                  onDate={(day) => tracker.setAppliedAt(t.job.id, day)}
                  onPrepare={() => onPrepare(t)}
                  onPractise={() => (onPractise ? onPractise(t) : t.runId && actions.openPractice({ screen: 'setup', kind: 'role', run: t.runId, job: t.job.id }))}
                  onSeeMatch={() => onSeeMatch(t)}
                  onShare={() => onShare(t)}
                />
              ))}
            </ul>
          </div>
        ))}
      </section>

      <p className="dm-footer">Roles come from employers' own job boards. We never use LinkedIn data.</p>
    </div>
  );
}

/* ───────────────────────── one role ───────────────────────── */

interface ItemProps {
  item: TrackedJob;
  /** The application through Leafout for this role, if there is one. */
  application?: ApplicationSummary;
  selectId: string;
  confirming: boolean;
  onAskRemove: () => void;
  onKeep: () => void;
  onRemove: () => void;
  onStage: (stage: TrackStage) => void;
  onNote: (note: string) => void;
  onDate: (day: string | undefined) => void;
  onPrepare: () => void;
  onPractise: () => void;
  onSeeMatch: () => void;
  onShare: () => void;
}

function Item({ item, application, selectId, confirming, onAskRemove, onKeep, onRemove, onStage, onNote, onDate, onPrepare, onPractise, onSeeMatch, onShare }: ItemProps) {
  const { job } = item;
  const ids = useId();
  const down = item.takenDownAt;
  const href = applyHref(job);
  const live = !down;
  const showDate = item.stage === 'applied' || item.stage === 'interviewing' || item.stage === 'offer' || (item.stage === 'closed' && !!item.appliedAt);
  const keepRef = useRef<HTMLButtonElement>(null);
  const askRef = useRef<HTMLButtonElement>(null);
  const wasConfirming = useRef(false);

  // the question replaces the Remove button: focus goes into it, and comes back to Remove when it is answered "Keep"
  useEffect(() => {
    if (confirming) keepRef.current?.focus();
    else if (wasConfirming.current) askRef.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);

  return (
    <li>
      <article className="dm-appcard" aria-label={nameOf(item)}>
        <div className="dm-appname">
          <h3 className={`dm-apptitle${item.stage === 'closed' ? ' is-closed' : ''}`}>{job.title}</h3>
          <p className="dm-muted dm-small">
            {job.employer} · {placeLine(job)}
          </p>
          <p className="dm-mono dm-muted dm-tiny">{job.applyHost}</p>
        </div>

        {down ? (
          <p className="dm-danger dm-small">The employer took this posting down on {dayLabel(down)}</p>
        ) : (
          <div className="dm-head-row dm-head-tight">
            <span className={FIT_CLASS[job.fit]}>{FIT_WORD[job.fit]}</span>
            <span className="dm-lvl">{LEVEL_WORD[job.level]}</span>
          </div>
        )}

        {application && (
          <div className="dm-appfield">
            <p className="dm-small">
              <span className="dm-muted">Through Leafout: </span>
              <span className="dm-strong">{appliedLabel(application)}</span>
            </p>
            <a className="btn btn-sm dm-btn-sm" href={applyPath({ screen: 'one', id: application.id })} aria-label={`Open the application for ${nameOf(item)}`}>
              Open the application
            </a>
          </div>
        )}

        <div className="dm-appfield">
          <label htmlFor={selectId} className="dm-muted dm-tiny">
            Stage
          </label>
          <select id={selectId} className="field dm-box dm-select" value={item.stage} onChange={(e) => onStage(e.target.value as TrackStage)}>
            {STAGES.map((s) => (
              <option key={s} value={s}>
                {STAGE_LABEL[s]}
              </option>
            ))}
          </select>
        </div>

        {showDate && (
          <div className="dm-appfield">
            <label htmlFor={`${ids}-date`} className="dm-muted dm-tiny">
              Date applied
            </label>
            <input id={`${ids}-date`} className="field dm-box" type="date" value={item.appliedAt ?? ''} onChange={(e) => onDate(e.target.value || undefined)} />
          </div>
        )}

        <div className="dm-appfield">
          <label htmlFor={`${ids}-note`} className="dm-muted dm-tiny">
            Note
          </label>
          <input id={`${ids}-note`} className="field dm-box" type="text" autoComplete="off" maxLength={MAX_NOTE} placeholder="Add a note" value={item.note} onChange={(e) => onNote(e.target.value)} />
        </div>

        {confirming ? (
          <div className="dm-confirm dm-confirm-card" role="group" aria-label={`Remove ${nameOf(item)}?`}>
            <p className="dm-small">Remove this role from Your applications?</p>
            <div className="dm-actions dm-actions-tight">
              <button type="button" className="btn btn-sm dm-btn-sm" onClick={onRemove}>
                Remove
              </button>
              <button ref={keepRef} type="button" className="btn btn-primary btn-sm dm-btn-sm" onClick={onKeep}>
                Keep
              </button>
            </div>
          </div>
        ) : (
          <>
            {live && (
              <div className="dm-actions dm-actions-tight">
                {item.runId && (
                  <button type="button" className="btn btn-sm dm-btn-sm" onClick={onPrepare}>
                    Prepare
                  </button>
                )}
                {item.runId && (
                  <button type="button" className="btn btn-sm dm-btn-sm" aria-label={`Practise the interview for ${nameOf(item)}`} onClick={onPractise}>
                    Practise
                  </button>
                )}
                {href ? (
                  <a className="btn btn-sm dm-btn-sm" href={href} target="_blank" rel="noopener noreferrer" aria-label={`${applyText(job)} for ${nameOf(item)}, opens in a new tab`}>
                    Apply ↗
                  </a>
                ) : null}
              </div>
            )}
            <div className="dm-actions dm-actions-tight">
              {live && item.runId && (
                <button type="button" className="btn btn-quiet btn-sm dm-btn-sm" onClick={onSeeMatch}>
                  See how you fit
                </button>
              )}
              {live && (
                <button type="button" className="btn btn-quiet btn-sm dm-btn-sm" onClick={onShare}>
                  Share
                </button>
              )}
              <button ref={askRef} type="button" className="btn btn-quiet btn-sm dm-btn-sm" aria-label={`Remove ${nameOf(item)}`} onClick={onAskRemove}>
                Remove
              </button>
            </div>
          </>
        )}
      </article>
    </li>
  );
}
