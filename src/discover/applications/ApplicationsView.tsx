/**
 * Your applications (board 10 of the "Discover with AI" canvas; the materials look of design.md 7b): the roles a person
 * saved, and where each one stands.
 *
 * It opens as one hero and a mosaic. The hero is the role that moved most recently (an interview, an application, an
 * offer before a role that is only saved; the newest change wins), with its stage as pips and the person's own note
 * stuck to it as a post-it. Beside it, on notebook paper, what to do for that role: only steps the app can open (match
 * the posting, choose the resume and cover note, practise this interview), each ticked only from a record that exists
 * (a match this browser remembers, the ticks made on the role's prep page, the stage and date the person set). Under it
 * the stage counts as small tiles (the current stage on a field) and one ink block: the application through Leafout
 * when there is one, else Prepare. The other roles follow on neutral ground, by stage: five columns on a wide screen and
 * tabs under 760 px, each with its count.
 *
 * A role moves by its own stage select, a real `<select>`; moving it to Applied for the first time writes today's date,
 * which can be changed. A note is one line. Everything is kept in this browser (see `tracker.ts`), and says so.
 * Removing a role asks first.
 *
 * A saved role that also has an application through Leafout (looked up by its listing) shows where that application
 * stands on the server, in words ("Waiting for you", "Sent 4 Nov"), and links to it ("Open the application"). The two
 * lists stay separate until the saved roles move to the server (a later decision).
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import type { ApplicationSummary } from '../../../shared/apply';
import { appliedLabel } from '../../apply/ApplyForMe';
import { applyPath } from '../../apply/route';
import { PageLine, Pips } from '../../components/materials';
import type { Pip, TickState } from '../../components/materials';
import { NARROW_QUERY, cssVars, useMediaQuery } from '../../lib/ui';
import { actions } from '../../store';
import '../../styles/discover-more.css';
import { listingMatchFor } from '../listingMatch';
import { checklistKey, parseChecklist, readJson } from '../prepare/storage';
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
const shortDay = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });

/** The stages a role passes through on its way to an offer; Closed is where it ends, so it has no pip. */
const PATH: readonly TrackStage[] = ['saved', 'applied', 'interviewing', 'offer'];

/**
 * The role that moved most recently: past Saved first (an interview, an application, an offer), the newest change wins;
 * else the newest saved role; else, when every role is closed, the newest of those.
 */
function lead(items: readonly TrackedJob[]): TrackedJob | undefined {
  const newest = (list: readonly TrackedJob[]) => [...list].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0];
  return newest(items.filter((t) => t.stage !== 'closed' && t.stage !== 'saved')) ?? newest(items.filter((t) => t.stage === 'saved')) ?? newest(items);
}

const HERO_WORDS: Record<TrackStage, string> = {
  saved: 'is saved',
  applied: 'moved to',
  interviewing: 'moved to',
  offer: 'moved to',
  closed: 'is',
};

export function ApplicationsView({ profileId, onPrepare, onSeeMatch, onShare, onBack, onPractise, applications }: ApplicationsViewProps) {
  const tracker = useTracker(profileId);
  const narrow = useMediaQuery(NARROW_QUERY);
  const base = useId();
  const [tab, setTab] = useState<TrackStage>('saved');
  const [confirming, setConfirming] = useState<string | null>(null);
  const [announce, setAnnounce] = useState('');
  /** The role the person just moved stays the hero; a note typed on another role does not steal the place. */
  const [moved, setMoved] = useState<string | null>(null);
  /** After a role moves, the stage select it moved with is pressed again: focus follows it to its new place. */
  const follow = useRef<string | null>(null);
  /** After a role is removed, focus goes to the column it was in. */
  const refocusColumn = useRef<TrackStage | null>(null);
  const tabRefs = useRef<Partial<Record<TrackStage, HTMLButtonElement | null>>>({});

  const selectId = useCallback((id: string) => `${base}-stage-${id}`, [base]);
  const columnId = (stage: TrackStage) => `${base}-col-${stage}`;

  const hero = (moved ? tracker.items.find((t) => t.job.id === moved) : undefined) ?? lead(tracker.items);
  const rest = useMemo(() => tracker.items.filter((t) => t !== hero), [tracker.items, hero]);

  const columns = useMemo(
    () =>
      STAGES.map((stage) => ({
        stage,
        // newest saved first: stable while a note is typed, unlike an order by last change
        items: rest.filter((t) => t.stage === stage).sort((a, b) => b.savedAt.localeCompare(a.savedAt)),
      })),
    [rest],
  );
  const open = tracker.items.filter((t) => t.stage !== 'closed').length;
  const closed = tracker.items.length - open;
  const countOf = (stage: TrackStage) => tracker.items.filter((t) => t.stage === stage).length;

  useEffect(() => {
    if (follow.current) {
      document.getElementById(selectId(follow.current))?.focus({ preventScroll: true });
      follow.current = null;
    } else if (refocusColumn.current) {
      document.getElementById(columnId(refocusColumn.current))?.focus({ preventScroll: true });
      refocusColumn.current = null;
    }
  });

  const moveTo = (t: TrackedJob, stage: TrackStage) => {
    tracker.setStage(t.job.id, stage);
    follow.current = t.job.id;
    setMoved(t.job.id);
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
  const handlers = (t: TrackedJob) => ({
    application: applications?.get(t.job.id),
    selectId: selectId(t.job.id),
    confirming: confirming === t.job.id,
    onAskRemove: () => setConfirming(t.job.id),
    onKeep: () => setConfirming(null),
    onRemove: () => remove(t),
    onStage: (stage: TrackStage) => moveTo(t, stage),
    onNote: (note: string) => tracker.setNote(t.job.id, note),
    onDate: (day: string | undefined) => tracker.setAppliedAt(t.job.id, day),
    onPrepare: () => onPrepare(t),
    onPractise: () => (onPractise ? onPractise(t) : t.runId && actions.openPractice({ screen: 'setup', kind: 'role', run: t.runId, job: t.job.id })),
    onSeeMatch: () => onSeeMatch(t),
    onShare: () => onShare(t),
  });

  return (
    <div className="dm-page dm-apps">
      <p className="sr-only" role="status">
        {announce}
      </p>

      {!hero ? (
        <div className="m-screen is-stack dm-appscreen">
          <section className="m-hero dm-apphero" aria-label="Your applications">
            <button type="button" className="dm-back" onClick={onBack}>
              <span aria-hidden="true">←</span> Discover
            </button>
            <p className="kicker">Discover · your applications</p>
            <h1 className="serif dm-herotitle">Your applications</h1>
            <span className="m-fine">Kept in this browser</span>
          </section>
          <div className="m-mosaic">
            <section className="m-tile m-paper m-c4 m-r2 dm-empty-page">
              <p className="pen dm-empty-pen">Your first saved role starts the pile.</p>
              <p className="m-fine">Nothing saved yet. Press Save on a role and it is kept here, with its stage, the date you applied and a note.</p>
            </section>
            <button type="button" className="m-tile m-ink m-c2 m-r2 dm-ink-row" onClick={onBack}>
              <span className="dm-ink-main">Back to roles</span>
              <span aria-hidden="true">→</span>
            </button>
          </div>
        </div>
      ) : (
        <div className="m-screen dm-appscreen">
          <Hero
            item={hero}
            profileId={profileId}
            total={tracker.items.length}
            open={open}
            closed={closed}
            onBack={onBack}
            {...handlers(hero)}
          />
          <div className="m-mosaic dm-appmosaic">
            <ForRole item={hero} profileId={profileId} {...handlers(hero)} />
            <div className="dm-stagecounts" role="group" aria-label="Applications by stage">
              {STAGES.map((stage) => (
                <div key={stage} className={`m-tile m-stage ${stage === hero.stage ? 'm-field' : 'm-graph'}`}>
                  <b className="numeral">{countOf(stage)}</b>
                  <span className="m-fine">{STAGE_LABEL[stage].toLowerCase()}</span>
                </div>
              ))}
            </div>
            <Next item={hero} application={applications?.get(hero.job.id)} onPrepare={() => onPrepare(hero)} />
          </div>
        </div>
      )}

      {tracker.items.length > 0 && (
        <>
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
                  {STAGE_LABEL[c.stage]} <span className="numeral dm-tabcount">{columns.find((x) => x.stage === c.stage)!.items.length + (hero?.stage === c.stage ? 1 : 0)}</span>
                </button>
              ))}
            </div>
          )}

          <section className={narrow ? 'dm-board dm-board-one' : 'dm-board'} aria-label="Applications by stage">
            {shown.map((c) => {
              const count = c.items.length + (hero?.stage === c.stage ? 1 : 0);
              return (
                <div
                  key={c.stage}
                  id={columnId(c.stage)}
                  className="dm-column"
                  tabIndex={-1}
                  {...(narrow ? { role: 'tabpanel', 'aria-labelledby': `${base}-tab-${c.stage}` } : { role: 'group', 'aria-label': `${STAGE_LABEL[c.stage]}, ${roles(count)}` })}
                >
                  {!narrow && (
                    <div className="dm-colhead-line">
                      <h2 className="title dm-h2s">{STAGE_LABEL[c.stage]}</h2>
                      <span className="numeral dm-muted dm-small">{count}</span>
                    </div>
                  )}
                  {c.items.length === 0 && <p className="dm-empty">{hero?.stage === c.stage ? 'Nothing else here.' : 'Nothing here yet.'}</p>}
                  <ul className="dm-applist">
                    {c.items.map((t) => (
                      <Item key={t.job.id} item={t} {...handlers(t)} />
                    ))}
                  </ul>
                </div>
              );
            })}
          </section>
        </>
      )}

      <p className="dm-footer">Roles come from employers' own job boards. We never use LinkedIn data.</p>
    </div>
  );
}

/* ───────────────────────── the hero: the role that moved ───────────────────────── */

interface RoleHandlers {
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

const showDateFor = (item: TrackedJob) => item.stage === 'applied' || item.stage === 'interviewing' || item.stage === 'offer' || (item.stage === 'closed' && !!item.appliedAt);

function Hero({ item, total, open, closed, onBack, application, selectId, confirming, onAskRemove, onKeep, onRemove, onStage, onNote, onDate, onShare }: RoleHandlers & { item: TrackedJob; profileId: string; total: number; open: number; closed: number; onBack: () => void }) {
  const { job } = item;
  const ids = useId();
  const down = item.takenDownAt;
  const href = applyHref(job);
  const live = !down;
  const at = PATH.indexOf(item.stage);
  const pips: Pip[] = PATH.map((_, i) => (i <= at ? 'on' : 'no'));
  const pipsLabel = `Stages: ${PATH.map((s, i) => `${STAGE_LABEL[s].toLowerCase()}${i === at ? ' now' : i > at ? ' still ahead' : ''}`).join(', ')}`;
  const dates = [item.appliedAt ? `applied ${dayLabel(item.appliedAt)}` : '', `last changed ${shortDay(item.updatedAt)}`].filter(Boolean).join(' · ');

  return (
    <section className="m-hero dm-apphero" aria-label={nameOf(item)}>
      <button type="button" className="dm-back" onClick={onBack}>
        <span aria-hidden="true">←</span> Discover
      </button>
      <p className="kicker">
        Discover · your applications · {total}
        <span className="dm-kicker-rest"> · {open} open · {closed} closed · kept in this browser</span>
      </p>
      <h1 className="serif dm-herotitle">
        <span className="sr-only">Your applications. </span>
        {job.employer} {HERO_WORDS[item.stage]}{' '}
        {item.stage === 'saved' ? null : <span className="hl">{STAGE_LABEL[item.stage].toLowerCase()}</span>}
        {item.stage === 'saved' ? null : '.'}
        {item.stage === 'saved' ? '.' : null}
      </h1>
      <p className="m-fine dm-herosub">
        <span className="dm-strong">{job.title}</span> · {placeLine(job)} · {dates}
      </p>
      <p className="m-fine dm-mono dm-tiny">{job.applyHost}</p>

      {down ? (
        <p className="dm-danger dm-small">The employer took this posting down on {dayLabel(down)}</p>
      ) : (
        <div className="dm-head-row dm-head-tight">
          <span className={FIT_CLASS[job.fit]}>{FIT_WORD[job.fit]}</span>
          <span className="dm-lvl">{LEVEL_WORD[job.level]}</span>
        </div>
      )}

      {item.stage !== 'closed' && (
        <div className="dm-pathrow">
          <Pips pips={pips} label={pipsLabel} now={at} line r={8} gap={22} />
          <span className="m-fine">
            Stage {at + 1} of {PATH.length}: {STAGE_LABEL[item.stage].toLowerCase()}
          </span>
        </div>
      )}

      <div className="stick is-flow m-turn-l dm-herostick">
        <div className="postit" role="note" style={cssVars({ '--pp': 'var(--paper-yellow)' })}>
          <label htmlFor={`${ids}-note`} className="sr-only">
            Note
          </label>
          <textarea
            id={`${ids}-note`}
            className="pen pnote-field dm-postit-field"
            rows={3}
            autoComplete="off"
            maxLength={MAX_NOTE}
            placeholder="Add a note"
            value={item.note}
            onChange={(e) => onNote(e.target.value.replace(/[\r\n]+/g, ' '))}
          />
          <span className="pcite pcite-end">your note on this role</span>
        </div>
      </div>

      <div className="dm-herocontrols">
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
        {showDateFor(item) && (
          <div className="dm-appfield">
            <label htmlFor={`${ids}-date`} className="dm-muted dm-tiny">
              Date applied
            </label>
            <input id={`${ids}-date`} className="field dm-box" type="date" value={item.appliedAt ?? ''} onChange={(e) => onDate(e.target.value || undefined)} />
          </div>
        )}
      </div>

      {application && (
        <div className="dm-appfield dm-leafout">
          <p className="dm-small">
            <span className="dm-muted">Through Leafout: </span>
            <span className="dm-strong">{appliedLabel(application)}</span>
          </p>
          <a className="btn btn-sm dm-btn-sm" href={applyPath({ screen: 'one', id: application.id })} aria-label={`Open the application for ${nameOf(item)}`}>
            Open the application
          </a>
        </div>
      )}

      <RemoveBar
        name={nameOf(item)}
        confirming={confirming}
        onAsk={onAskRemove}
        onKeep={onKeep}
        onRemove={onRemove}
        extra={
          <>
            {live && href ? (
              <a className="btn btn-sm dm-btn-sm" href={href} target="_blank" rel="noopener noreferrer" aria-label={`${applyText(job)} for ${nameOf(item)}, opens in a new tab`}>
                Apply ↗
              </a>
            ) : null}
            {live && (
              <button type="button" className="btn btn-quiet btn-sm dm-btn-sm" onClick={onShare}>
                Share
              </button>
            )}
          </>
        }
      />
    </section>
  );
}

/** Remove asks first; the question replaces the buttons, focus goes into it and comes back to Remove on "Keep". */
function RemoveBar({ name, confirming, onAsk, onKeep, onRemove, before, extra }: { name: string; confirming: boolean; onAsk: () => void; onKeep: () => void; onRemove: () => void; before?: ReactNode; extra?: ReactNode }) {
  const keepRef = useRef<HTMLButtonElement>(null);
  const askRef = useRef<HTMLButtonElement>(null);
  const wasConfirming = useRef(false);

  useEffect(() => {
    if (confirming) keepRef.current?.focus();
    else if (wasConfirming.current) askRef.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);

  if (confirming) {
    return (
      <div className="dm-confirm dm-confirm-card" role="group" aria-label={`Remove ${name}?`}>
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
    );
  }
  return (
    <>
      {before}
      <div className="dm-actions dm-actions-tight">
        {extra}
        <button ref={askRef} type="button" className="btn btn-quiet btn-sm dm-btn-sm" aria-label={`Remove ${name}`} onClick={onAsk}>
          Remove
        </button>
      </div>
    </>
  );
}

/* ───────────────────────── a page of the notebook: what to do for the hero role ───────────────────────── */

function ForRole({ item, profileId, onSeeMatch, onPrepare, onPractise }: RoleHandlers & { item: TrackedJob; profileId: string }) {
  const { job } = item;
  const href = applyHref(job);
  const live = !item.takenDownAt;
  if (!live) return null;

  // what already exists: a match this browser remembers, and the ticks the person made on the role's prep page
  const ticks = parseChecklist(readJson(checklistKey(profileId, job.id)));
  const matched = !!listingMatchFor(job.id, profileId);
  const writing: TickState = ticks.resume && ticks.cover ? 'done' : ticks.resume || ticks.cover ? 'half' : 'todo';
  const applied = item.stage === 'applied' || item.stage === 'interviewing' || item.stage === 'offer';

  const lines: ReactNode[] = [];
  if (item.runId) {
    lines.push(
      <PageLine
        key="match"
        index={0}
        state={matched || ticks.fit ? 'done' : 'todo'}
        title="Match the posting"
        cite={matched ? 'a posting match is kept in this browser' : ticks.fit ? 'ticked on its prep page' : 'every requirement against your evidence'}
        onGo={onSeeMatch}
      />,
      <PageLine
        key="write"
        index={1}
        state={writing}
        title="Choose the resume and cover note"
        cite={writing === 'todo' ? 'built in the match, from facts it can cite' : `ticked on its prep page: ${[ticks.resume ? 'resume' : '', ticks.cover ? 'cover note' : ''].filter(Boolean).join(' and ')}`}
        onGo={onPrepare}
      />,
      <PageLine key="practise" index={0} state="todo" title="Practise this interview" cite="a mock interview on this posting" onGo={onPractise} />,
    );
  }
  if (href) {
    lines.push(
      <PageLine
        key="apply"
        index={1}
        state={applied ? 'done' : 'todo'}
        title={`Apply on ${job.applyHost}`}
        cite={applied ? `moved to ${STAGE_LABEL[item.stage].toLowerCase()}${item.appliedAt ? ` · applied ${dayLabel(item.appliedAt)}` : ''}` : "opens the employer's own board in a new tab"}
        href={href}
        onGo={() => window.open(href, '_blank', 'noopener,noreferrer')}
      />,
    );
  }
  if (lines.length === 0) return null;
  const total = lines.length;
  return (
    <section className="m-tile m-paper m-c6 m-r4 dm-forrole" aria-label={`What to do for ${job.employer}`}>
      <div className="m-page-head">
        <h2 className="pen m-page-title">For {job.employer}</h2>
        <span className="m-fine">{total === 1 ? '1 step' : `${total} steps`}</span>
      </div>
      <ul className="m-lines">{lines}</ul>
    </section>
  );
}

/** The one ink block: the application through Leafout when there is one, else Prepare for the role. */
function Next({ item, application, onPrepare }: { item: TrackedJob; application?: ApplicationSummary; onPrepare: () => void }) {
  if (application) {
    return (
      <a className="m-tile m-ink m-c6 dm-ink-row" href={applyPath({ screen: 'one', id: application.id })} aria-label={`Open the application for ${nameOf(item)}`}>
        <span className="dm-ink-main">
          <span className="dm-ink-key">Through Leafout</span>
          <span>{appliedLabel(application)}</span>
        </span>
        <span>Open the application →</span>
      </a>
    );
  }
  if (item.runId && !item.takenDownAt) {
    return (
      <button type="button" className="m-tile m-ink m-c6 dm-ink-row" onClick={onPrepare}>
        <span className="dm-ink-main">
          <span className="dm-ink-key">Prepare</span>
          <span>{item.job.employer}: lead with your strongest ground</span>
        </span>
        <span aria-hidden="true">→</span>
      </button>
    );
  }
  return null;
}

/* ───────────────────────── one role of the rest ───────────────────────── */

function Item({ item, application, selectId, confirming, onAskRemove, onKeep, onRemove, onStage, onNote, onDate, onPrepare, onPractise, onSeeMatch, onShare }: RoleHandlers & { item: TrackedJob }) {
  const { job } = item;
  const ids = useId();
  const down = item.takenDownAt;
  const href = applyHref(job);
  const live = !down;
  const showDate = showDateFor(item);

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

        <RemoveBar
          name={nameOf(item)}
          confirming={confirming}
          onAsk={onAskRemove}
          onKeep={onKeep}
          onRemove={onRemove}
          before={
            live ? (
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
            ) : undefined
          }
          extra={
            <>
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
            </>
          }
        />
      </article>
    </li>
  );
}
