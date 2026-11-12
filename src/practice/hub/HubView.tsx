/**
 * Practice, the hub (board 2 of the "Practice the interview" canvas), presentational: everything it shows arrives as
 * props, everything it does goes out through `nav`. `HubScreen` feeds it from the server and the tracker; the dev page
 * feeds it from fixtures.
 *
 * The hero (a field) is the session to pick up, else the next role to practise for, else where to start; the one ink block
 * under it starts that. The four kinds are tiles sized by what the code already knows (a role the person can practise for,
 * claims that wobbled, nothing practised yet); what wobbled is a notebook page. "Coming up" and the past sessions are rows
 * under them, the sessions on graph paper. Verdicts are words and squares and counts, never a score; nothing here is shared
 * with an employer.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ClaimBest, InterviewKind, PracticeHistory, RoleSource, SessionSummary } from '../../../shared/interview';
import { PageLine } from '../../components/materials';
import { plural } from '../../lib/ui';
import type { TrackedJob } from '../../discover/applications/tracker';
import { STAGE_LABEL } from '../../discover/applications/tracker';
import { FIT_WORD, dayLabel } from '../../discover/prepare/words';
import { CirclesLine } from '../Pips';
import { KIND_BLURB, KIND_LABEL, KEPT_LINE, REDRILL_MAX, REDRILL_MINUTES, REDRILL_SHORT, VERDICT_LABEL } from '../words';
import type { PracticeNav } from './nav';
import { DAILY_INTERVIEWS, ErrorNote, LimitNotice, PausedNotice, SampleNotice, freeLine } from './Notice';
import { SessionRows } from './SessionRows';
import type { Load } from './useLoad';
import { FIT_PC, NOTHING_SHARED, clip, dateOf, sessionSubtitle } from './words';
import '../../styles/practice.css';

export interface HubViewProps {
  /** The sample profile: practice works on it, and says whose interview it is. */
  sample: boolean;
  /** Everything in Your applications; the hub reads the roles that can be practised from it. */
  tracked: readonly TrackedJob[];
  history: Load<PracticeHistory>;
  /** The account has no limits: no daily count, nothing disabled, whatever the server's history says. */
  unlimited?: boolean;
  onReload: () => void;
  nav: PracticeNav;
}

/* ───────────────────────── the roles that can be practised ───────────────────────── */

/** A role of a search with AI that Your applications holds, and the last time it was practised. */
export interface PractiseRole {
  item: TrackedJob;
  role: RoleSource;
  last?: SessionSummary;
}

const roleOf = (t: TrackedJob): RoleSource | undefined => (t.runId ? { runId: t.runId, jobId: t.job.id } : undefined);

/** The newest finished practice of a role. */
function lastFor(sessions: readonly SessionSummary[], jobId: string): SessionSummary | undefined {
  return sessions.filter((s) => s.kind === 'role' && s.status === 'done' && s.role?.jobId === jobId).sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
}

/** Coming up: the roles the person is interviewing for, then the ones they applied to; a role with no search to read has no plan. */
export function comingUp(tracked: readonly TrackedJob[], sessions: readonly SessionSummary[]): PractiseRole[] {
  const rank: Partial<Record<TrackedJob['stage'], number>> = { interviewing: 0, applied: 1 };
  return tracked
    .filter((t) => rank[t.stage] !== undefined && roleOf(t))
    .sort((a, b) => (rank[a.stage] ?? 9) - (rank[b.stage] ?? 9) || b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, 6)
    .map((item) => ({ item, role: roleOf(item) as RoleSource, last: lastFor(sessions, item.job.id) }));
}

/** Every open role that can be practised, for the "Which role" list: the coming-up ones first. */
export function roleChoices(tracked: readonly TrackedJob[], sessions: readonly SessionSummary[]): PractiseRole[] {
  const coming = comingUp(tracked, sessions);
  const seen = new Set(coming.map((c) => c.item.job.id));
  const rest = tracked
    .filter((t) => t.stage !== 'closed' && roleOf(t) && !seen.has(t.job.id))
    .sort((a, b) => b.savedAt.localeCompare(a.savedAt))
    .map((item) => ({ item, role: roleOf(item) as RoleSource, last: lastFor(sessions, item.job.id) }));
  return [...coming, ...rest];
}

/** The claims whose best verdict is Shaky or Couldn't defend: what a re-drill can bring back (the re-drill screen asks the
 * server for the exact list, which also drops a line that is no longer on the resume). */
export const wobbled = (claims: readonly ClaimBest[]) => claims.filter((c) => c.best === 'shaky' || c.best === 'couldnt');

/* ───────────────────────── the screen ───────────────────────── */

type Card = { kind: InterviewKind; blurb: string; built: readonly string[]; length: string; button: string };

const CARDS: readonly Card[] = [
  {
    kind: 'role',
    blurb: 'From a role you saved in Discover: what the posting asks, what you show, and your gaps.',
    built: ['Posting asks', 'Your resume', 'Your gaps'],
    length: '5 questions · about 20 min',
    button: 'Set up',
  },
  {
    kind: 'resume',
    blurb: 'No posting needed. It picks lines from your resume and asks you to back them up.',
    built: ['Your resume', 'GitHub', 'Pages'],
    length: '5 questions · about 15 min',
    button: 'Set up',
  },
  {
    kind: 'stories',
    blurb: '“Tell me about a time…”, built from your own experience, with follow-ups that make each story complete.',
    built: ['Your evidence lines'],
    length: '4 stories · about 20 min',
    button: 'Set up',
  },
  {
    kind: 'work',
    blurb: 'A small real task for your kind of work, then questions on your choices.',
    built: ['A task template', 'Your kind of work'],
    length: 'About 10 minutes, then 3 questions · best on a laptop',
    button: 'Try a task',
  },
];

export function HubView({ sample, tracked, history, unlimited = false, onReload, nav }: HubViewProps) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const sessions = history.status === 'ready' ? history.data.sessions : [];
  const left = history.status === 'ready' && !unlimited ? history.data.leftToday : undefined;
  const limited = left === 0;
  const paused = history.status === 'error' && history.code === 503;
  const coming = useMemo(() => comingUp(tracked, sessions), [tracked, sessions]);
  const choices = useMemo(() => roleChoices(tracked, sessions), [tracked, sessions]);
  const weak = history.status === 'ready' ? wobbled(history.data.claims) : [];

  // the page opens at its title, for a reader who arrived by keyboard or with a screen reader
  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  // the hero: what to pick up, else the next role to practise for, else where to start
  const unfinished = sessions.find((s) => s.status === 'active');
  const next = coming[0];
  // tile sizes follow what the code knows: a role to practise for, claims that wobbled, nothing practised yet
  const roleBig = choices.length > 0;
  const kindSpan = (kind: InterviewKind): string => {
    if (kind === 'role') return roleBig ? 'pc-s7' : 'pc-s5';
    if (kind === 'resume') return roleBig ? 'pc-s5' : 'pc-s7';
    return weak.length > 0 ? 'pc-s4' : 'pc-s6';
  };

  return (
    <div className="pc-page pc-hub">
      {(sample || limited || paused) && (
        <div className="pc-notices">
          {sample && <SampleNotice onAnalyse={nav.analyse} />}
          {limited && <LimitNotice used={DAILY_INTERVIEWS} />}
          {paused && <PausedNotice />}
        </div>
      )}

      <div className="m-screen pc-screen">
        <div className="pc-lead">
          <header className="m-hero m-field pc-hero">
            <p className="kicker">Practice</p>
            <h1 className="serif pc-title" ref={titleRef} tabIndex={-1}>
              Practice the interview
            </h1>
            <p className="pc-lede">Questions about your own work, asked out loud. Credit only for what you can back up.</p>
            <div className="pc-pick">
              {unfinished ? (
                <>
                  <p className="kicker">Where you left off</p>
                  <p className="pc-pick-name">{KIND_LABEL[unfinished.kind]}</p>
                  <p className="pc-pick-meta">
                    Started {dateOf(unfinished.createdAt)} · not finished
                    {unfinished.kind === 'role' || unfinished.kind === 'work' ? ` · ${sessionSubtitle(unfinished)}` : ''}
                  </p>
                </>
              ) : next ? (
                <>
                  <p className="kicker">Next up · from Your applications</p>
                  <p className="pc-pick-name">
                    {next.item.job.title} at {next.item.job.employer}
                  </p>
                  <p className="pc-pick-meta">
                    {next.item.stage === 'interviewing' ? STAGE_LABEL.interviewing : next.item.appliedAt ? `Applied ${dayLabel(next.item.appliedAt)}` : STAGE_LABEL.applied} ·{' '}
                    {next.last ? `practised ${dateOf(next.last.createdAt)}` : 'not practised yet'}
                  </p>
                </>
              ) : (
                <>
                  <p className="kicker">Start here</p>
                  <p className="pc-pick-name">{KIND_LABEL.resume}</p>
                  <p className="pc-pick-meta">{KIND_BLURB.resume}</p>
                </>
              )}
            </div>
            <p className="pc-fine">{freeLine(left, unlimited)}</p>
          </header>
          {unfinished ? (
            <button type="button" className="m-tile m-ink pc-ink" onClick={() => nav.room(unfinished.id)}>
              Carry on <span aria-hidden="true">→</span>
            </button>
          ) : next ? (
            <button type="button" className="m-tile m-ink pc-ink" disabled={limited} onClick={() => nav.setup('role', next.role)}>
              {next.last ? 'Practise again for this role' : 'Practise for this role'} <span aria-hidden="true">→</span>
            </button>
          ) : (
            <button type="button" className="m-tile m-ink pc-ink" disabled={limited} onClick={() => nav.setup('resume')}>
              Defend your resume <span aria-hidden="true">→</span>
            </button>
          )}
        </div>

        <section className="m-mosaic is-12 pc-mosaic" aria-label={weak.length > 0 ? 'Four kinds of practice, and a re-drill' : 'Four kinds of practice'}>
          {CARDS.map((card) => (
            <KindTile key={card.kind} card={card} span={kindSpan(card.kind)} choices={choices} limited={limited} nav={nav} />
          ))}
          {weak.length > 0 && <RedrillTile weak={weak} limited={limited} nav={nav} />}
        </section>
      </div>

      <div className="pc-stack">
        {coming.length > 0 && <ComingUp rows={coming} limited={limited} nav={nav} />}
        <YourPractice history={history} onReload={onReload} nav={nav} paused={paused} />
      </div>

      <footer className="pc-footer">
        <p>{KEPT_LINE}</p>
        <p>{NOTHING_SHARED}</p>
      </footer>
    </div>
  );
}

/* ───────────────────────── coming up ───────────────────────── */

function ComingUp({ rows, limited, nav }: { rows: readonly PractiseRole[]; limited: boolean; nav: PracticeNav }) {
  const headingId = useId();
  return (
    <section className="m-tile m-quiet pc-sec" aria-labelledby={headingId}>
      <header className="pc-sec-head">
        <p className="kicker">From Your applications</p>
        <h2 className="title pc-h2" id={headingId}>
          Coming up
        </h2>
      </header>
      <ul className="pc-rows">
        {rows.map(({ item, role, last }) => {
          const { job } = item;
          const meta =
            item.stage === 'interviewing'
              ? [STAGE_LABEL.interviewing, item.note ? clip(item.note, 60) : ''].filter(Boolean).join(' · ')
              : [item.appliedAt ? `Applied ${dayLabel(item.appliedAt)}` : STAGE_LABEL.applied, last ? `practised ${dateOf(last.createdAt)}` : ''].filter(Boolean).join(' · ');
          return (
            <li key={job.id} className="pc-row pc-row-role">
              <div className="pc-row-main">
                <div className="pc-row-name">
                  <span className="pc-strong pc-name-employer">{job.employer}</span>
                  <span className="pc-name-title">{job.title}</span>
                </div>
                <div className="pc-row-meta">
                  <span className="pc-muted">{meta}</span>
                  <span className={FIT_PC[job.fit]}>{FIT_WORD[job.fit]}</span>
                  {last ? <CirclesLine verdicts={last.verdicts} counts={last.counts} /> : <span className="pc-muted pc-small">Not practised yet</span>}
                </div>
              </div>
              <div className="pc-row-actions">
                {last && (
                  <button type="button" className="btn btn-quiet btn-sm pc-btn-sm" aria-label={`Open debrief, ${job.title} at ${job.employer}`} onClick={() => nav.debrief(last.id)}>
                    Open debrief
                  </button>
                )}
                <button
                  type="button"
                  className="btn pc-btn"
                  disabled={limited}
                  aria-label={`${last ? 'Practise again' : 'Practise for this role'}, ${job.title} at ${job.employer}`}
                  onClick={() => nav.setup('role', role)}
                >
                  {last ? 'Practise again' : 'Practise for this role'}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ───────────────────────── the four kinds ───────────────────────── */

function KindTile({ card, span, choices, limited, nav }: { card: Card; span: string; choices: readonly PractiseRole[]; limited: boolean; nav: PracticeNav }) {
  const ids = useId();
  const [picked, setPicked] = useState<string>('');
  const chosen = choices.find((c) => c.item.job.id === picked) ?? choices[0];
  const isRole = card.kind === 'role';

  return (
    <article className={`m-tile m-plain pc-kind-tile ${span}`} aria-labelledby={`${ids}-h`}>
      <h2 className="title pc-h3" id={`${ids}-h`}>
        {KIND_LABEL[card.kind]}
      </h2>
      <p className="m-fine pc-card-blurb">{card.blurb}</p>
      <p className="m-cite">
        Built from {card.built.join(' · ')}
        <br />
        {card.length}
      </p>

      <div className="pc-card-go">
        {isRole && choices.length === 0 && (
          <>
            <p className="m-fine">Roles you save from a search in Discover show up here, so you can practise for one.</p>
            <button type="button" className="btn pc-btn" onClick={nav.discover}>
              Find roles in Discover
            </button>
          </>
        )}
        {isRole && chosen && (
          <>
            <label htmlFor={`${ids}-role`} className="kicker">
              Which role
            </label>
            <div className="pc-card-pick">
              <select id={`${ids}-role`} className="field pc-box pc-select" value={chosen.item.job.id} onChange={(e) => setPicked(e.target.value)}>
                {choices.map((c) => (
                  <option key={c.item.job.id} value={c.item.job.id}>
                    {c.item.job.employer} · {c.item.job.title}
                  </option>
                ))}
              </select>
              <button type="button" className="btn pc-btn" disabled={limited} onClick={() => nav.setup('role', chosen.role)}>
                {card.button}
              </button>
            </div>
          </>
        )}
        {!isRole && (
          <button type="button" className="btn pc-btn" disabled={limited} onClick={() => nav.setup(card.kind)}>
            {card.button}
          </button>
        )}
      </div>
    </article>
  );
}

/** Only when something wobbled: a page of the claims to bring back, in pen, each opening the re-drill. */
function RedrillTile({ weak, limited, nav }: { weak: readonly ClaimBest[]; limited: boolean; nav: PracticeNav }) {
  const ids = useId();
  const n = Math.min(weak.length, REDRILL_SHORT);
  return (
    <article className="m-tile m-paper pc-s4 pc-redrill-tile" aria-labelledby={`${ids}-h`}>
      <div className="m-page-head">
        <h2 className="m-page-title pen" id={`${ids}-h`}>
          {KIND_LABEL.redrill}
        </h2>
      </div>
      <ul className="m-lines">
        {weak.slice(0, 3).map((c, i) => (
          <PageLine key={`${c.source}|${c.text}`} tick={false} index={i} title={clip(c.text, 64)} cite={`${c.source} · ${VERDICT_LABEL[c.best]}`} onGo={nav.redrill} />
        ))}
      </ul>
      <div className="m-foot">
        <p className="m-fine">
          {plural(weak.length, 'claim')} wobbled · the top {n} take about {n * REDRILL_MINUTES} min
          {weak.length > REDRILL_SHORT ? ` · up to ${Math.min(weak.length, REDRILL_MAX)} in one` : ''}
        </p>
        <button type="button" className="btn pc-btn" disabled={limited} onClick={nav.redrill}>
          Re-drill
        </button>
      </div>
    </article>
  );
}

/* ───────────────────────── your practice ───────────────────────── */

function YourPractice({ history, onReload, nav, paused }: { history: Load<PracticeHistory>; onReload: () => void; nav: PracticeNav; paused: boolean }) {
  const headingId = useId();
  return (
    <section className="m-tile m-graph pc-sec" aria-labelledby={headingId} aria-busy={history.status === 'loading'}>
      <header className="pc-sec-head pc-sec-head-row">
        <div className="pc-sec-head">
          <p className="kicker">Past sessions</p>
          <h2 className="title pc-h2" id={headingId}>
            Your practice
          </h2>
        </div>
        <button type="button" className="pc-link" onClick={nav.progress}>
          What carries over
        </button>
      </header>

      {history.status === 'loading' && (
        <p className="pc-muted" role="status">
          Loading your practice…
        </p>
      )}
      {history.status === 'error' && !paused && <ErrorNote message={history.message} onRetry={onReload} />}
      {history.status === 'error' && paused && (
        <p className="pc-muted">
          Your past sessions show here once practice is back.{' '}
          <button type="button" className="pc-link" onClick={onReload}>
            Try again
          </button>
        </p>
      )}
      {history.status === 'ready' && history.data.sessions.length === 0 && (
        <>
          <p className="pen">Your first one starts the pile.</p>
          <p className="pc-muted">Nothing practised yet. Pick a kind above, and your debriefs are kept here for 30 days.</p>
        </>
      )}
      {history.status === 'ready' && history.data.sessions.length > 0 && <SessionRows sessions={history.data.sessions} nav={nav} />}
    </section>
  );
}
