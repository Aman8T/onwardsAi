/**
 * The tiles of the mosaic that count and list, each reading what the dashboard already loaded:
 *
 *   TodayTile     the features written as things to do, on notebook paper, in pen (todos.ts computes the items; the
 *                 person's own ticks and lines come from todoMarks.ts)
 *   NextTile      the closest role large, with its five half-ring arcs (arcs.ts); the others as compact rows
 *   AppsTile      your applications: the count, the stage counts, the role that moved
 *   DiscoverTile  the last search: how many roles it found and how many fit (an ink block)
 *   PracticeTile  claims defended, x of y, with the spark
 *   MatchTile     the newest posting match with its band as a stamp
 *
 * Every tile has an inviting empty state with one next step. Hand-touched pieces (the pen, the tick, the strike, the
 * stamps) are only for what the person wrote, did or earned; the measured ones (arcs, numerals, the stage counts) are
 * straight and plain. Nothing here shows a score going down.
 */
import { useMemo, useState } from 'react';
import type { FormEvent, MouseEvent, ReactNode } from 'react';
import type { Profile } from '../../../shared/types';
import { applyPath } from '../../apply/route';
import { applyOn, useApplications } from '../../apply/useApplications';
import { STAGE_LABEL } from '../../discover/applications/tracker';
import type { TrackedJob } from '../../discover/applications/tracker';
import { discoverPath } from '../../discover/route';
import type { DiscoverTab } from '../../discover/route';
import { roleAction } from '../../lib/prompts';
import { rankRoles, roleStanding } from '../../lib/layout';
import { plural, timeAgo } from '../../lib/ui';
import { matchPath } from '../../match/route';
import { BAND_LABEL } from '../../match/model';
import type { MatchSummary } from '../../match/types';
import { practicePath } from '../../practice/route';
import { actions } from '../../store';
import { ARC_MAX, ARC_VIEWBOX, alignmentArcs } from './arcs';
import type { PracticeTrend } from './deskModel';
import { OWN_MAX, OWN_MAX_CHARS } from './todos';
import type { TodoGo, TodoRow } from './todos';

const dPath = (tab: DiscoverTab): string => discoverPath({ remote: true, tab });

/* ───────────────────────── links that stay real links ───────────────────────── */

/** A real `<a href>`: opened in a new tab by the browser's own gestures, in the app's own navigation otherwise. */
function Go({ href, onGo, className, children, label }: { href: string; onGo: () => void; className?: string; children: ReactNode; label?: string }) {
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onGo();
  };
  return (
    <a className={className} href={href} aria-label={label} onClick={click}>
      {children}
    </a>
  );
}

/** What each place a to-do opens is called in the address bar, and what opening it does. */
export function todoLink(go: TodoGo, openPlan: (planId: string) => void): { href: string; open: () => void } {
  switch (go.kind) {
    case 'practice':
      return { href: practicePath({ screen: 'hub' }), open: () => actions.openPractice({ screen: 'hub' }) };
    case 'redrill':
      return { href: practicePath({ screen: 'redrill' }), open: () => actions.openPractice({ screen: 'redrill' }) };
    case 'role':
      return { href: '/dashboard', open: () => actions.openRole(go.roleId) };
    case 'discover':
      return { href: dPath('roles'), open: () => actions.openDiscover({ tab: 'roles' }) };
    case 'match':
      return { href: matchPath({ screen: 'paste' }), open: () => actions.openMatch() };
    case 'plan':
      return { href: '/dashboard', open: () => openPlan(go.planId) };
  }
}

/* ───────────────────────── Today ───────────────────────── */

const BOX = [
  'M4.2 4.6c5-.7 10.2-.5 15.4-.1.4 5 .3 10-.1 15.1-5 .6-10.1.5-15.2.1C3.9 14.7 3.8 9.6 4.2 4.6z',
  'M4.5 4.2c5 .3 10-.2 15 .4-.3 5 .2 10.2-.3 15.1-5.1-.2-10 .4-15-.2.5-5-.1-10.2.3-15.3z',
];
const CHECK = 'M6.5 12.5c1.8 1.6 3.2 3.4 4.4 5.4 2.2-5.6 5.6-10.4 10.6-14.6';
const HALF = 'M7 18.5C10.5 13.5 13.5 9.5 17.5 5';
const STRIKE = ['M0 5C18 2 42 7 66 3.5S92 3 100 4.5', 'M0 4C22 6 40 2 64 5S90 3 100 4'];

function Tick({ row, index, onTick }: { row: TodoRow; index: number; onTick: (on: boolean) => void }) {
  return (
    <button
      type="button"
      className="tick"
      aria-pressed={row.state === 'done' ? true : row.state === 'half' ? 'mixed' : false}
      aria-label={row.text}
      onClick={() => onTick(row.state !== 'done')}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path className="bx" d={BOX[index % 2]} />
        <path className="hf" d={HALF} />
        <path className="ck" d={CHECK} />
      </svg>
    </button>
  );
}

export function TodayTile({ rows, onTick, onAdd, onRemove, onOpenPlan }: { rows: readonly TodoRow[]; onOpenPlan: (planId: string) => void; onTick: (id: string, on: boolean) => void; onAdd: (text: string) => boolean; onRemove: (id: string) => void }) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const done = rows.filter((r) => r.state === 'done').length;
  const ownCount = rows.filter((r) => r.own).length;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (onAdd(draft)) {
      setDraft('');
      if (ownCount + 1 >= OWN_MAX) setAdding(false);
    }
  };

  return (
    <section className="t t-todo" aria-labelledby="db-todo-h">
      <div className="todo-head">
        <h2 id="db-todo-h" className="pen todo-title">
          Today
        </h2>
        <span className="fine">
          <span className="numeral">{done}</span> of <span className="numeral">{rows.length}</span> done
        </span>
      </div>
      <ul className="todo-list">
        {rows.map((row, i) => {
          const link = row.go ? todoLink(row.go, onOpenPlan) : null;
          return (
            <li key={row.id} className={`todo ruled${row.state === 'done' ? ' is-done' : row.state === 'half' ? ' is-half' : ''}`}>
              <Tick row={row} index={i} onTick={(on) => onTick(row.id, on)} />
              <span className="todo-main">
                {link ? (
                  <Go className="pen todo-t" href={link.href} onGo={link.open}>
                    {row.text}
                    <svg className="strike" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
                      <path d={STRIKE[i % 2]} vectorEffect="non-scaling-stroke" />
                    </svg>
                  </Go>
                ) : (
                  <span className="pen todo-t">
                    {row.text}
                    <svg className="strike" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
                      <path d={STRIKE[i % 2]} vectorEffect="non-scaling-stroke" />
                    </svg>
                  </span>
                )}
                <span className="cite">{row.cite}</span>
              </span>
              {row.own ? (
                <button type="button" className="todo-x" aria-label={`Remove “${row.text}”`} onClick={() => onRemove(row.id)}>
                  ×
                </button>
              ) : (
                <span className="todo-go" aria-hidden="true">
                  →
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {adding ? (
        <form className="todo-add" onSubmit={submit}>
          <label className="sr-only" htmlFor="db-todo-new">
            Add your own to-do for today
          </label>
          <input
            id="db-todo-new"
            className="pen todo-input"
            type="text"
            autoFocus
            maxLength={OWN_MAX_CHARS}
            value={draft}
            placeholder="Something of your own…"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setAdding(false);
            }}
          />
          <button type="submit" className="plink" disabled={!draft.trim()}>
            add
          </button>
        </form>
      ) : (
        ownCount < OWN_MAX && (
          <button type="button" className="pen todo-add todo-add-btn" onClick={() => setAdding(true)}>
            + add your own
          </button>
        )
      )}
    </section>
  );
}

/* ───────────────────────── Next roles ───────────────────────── */

export function NextTile({ profile }: { profile: Profile }) {
  const roles = useMemo(() => rankRoles(profile, ARC_MAX), [profile]);
  const top = roles[0];
  const arcs = useMemo(() => alignmentArcs(roles.map((r) => r.alignment)), [roles]);
  const prepareTop = () => {
    if (!top) return;
    const a = roleAction(top, roleStanding(profile, top.id));
    actions.openChat(a.origin, a.question, { roleId: top.id });
  };
  const eta = top ? (/^~/.test(top.eta) ? `about ${top.eta.slice(1).trim()}` : top.eta) : '';

  return (
    <section className="t t-next" id="db-next" aria-labelledby="db-next-h">
      <div className="card-head">
        <h2 id="db-next-h" className="kicker">
          Next · {roles.length ? `${plural(roles.length, 'path')} from here` : 'no paths yet'}
        </h2>
        <span className="kicker">alignment</span>
      </div>
      {!top ? (
        <p className="small">Next roles appear here once Leafout has enough evidence.</p>
      ) : (
        <div className="next-body">
          <div className="next-top">
            <svg className="gauge" viewBox={ARC_VIEWBOX} role="img" aria-label={`Alignment with each of the ${roles.length} roles: ${roles.map((r) => `${r.alignment}`).join(', ')} per cent`}>
              <path className="trk" d={arcs.track} />
              <path className="oth" d={arcs.others} />
              <path className="top" d={arcs.top} />
            </svg>
            <button type="button" className="next-open" onClick={() => actions.openRole(top.id)} aria-label={`Open ${top.title}: ${top.alignment}% aligned, ${top.eta}`}>
              <span className="big numeral">
                {top.alignment}
                <span className="big-unit">%</span>
              </span>
              <span className="small next-name">{top.title}</span>
            </button>
            <span className="fine">{eta} to close</span>
          </div>
          <div className="next-rows">
            {roles.slice(1).map((r) => (
              <button key={r.id} type="button" className="nrow" onClick={() => actions.openRole(r.id)} aria-label={`Open ${r.title}: ${r.alignment}% aligned, ${r.eta}`}>
                <span>{r.title}</span>
                <span className="numeral">{r.alignment}%</span>
              </button>
            ))}
            <button type="button" className="btn btn-primary btn-sm next-prep" onClick={prepareTop}>
              How do I prepare?
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/* ───────────────────────── the four small tiles ───────────────────────── */

const WAITING = new Set(['waiting_for_you', 'needs_signin', 'check_this_one']);

export function AppsTile({ items }: { items: readonly TrackedJob[] }) {
  const apply = useApplications();
  const sent = applyOn(apply.status) && apply.ready;
  const waiting = sent ? apply.applications.filter((a) => WAITING.has(a.stage)).length : 0;
  const n = (stage: TrackedJob['stage']) => items.filter((t) => t.stage === stage).length;
  // an offer is worth a place of its own; otherwise the closed ones fill it
  const fourth = n('offer') > 0 ? ('offer' as const) : ('closed' as const);
  const stages = (['saved', 'applied', 'interviewing', fourth] as const).map((stage) => ({ stage, n: n(stage) }));
  // what is moving: an interview first, else the newest application, else the newest saved role
  const moving = items.find((t) => t.stage === 'interviewing') ?? items.find((t) => t.stage === 'applied') ?? items.find((t) => t.stage === 'saved');

  return (
    <section className="t t-apps" id="db-apps" aria-labelledby="db-apps-h">
      <div className="card-head">
        <h2 id="db-apps-h" className="kicker t-ink">
          Your applications
        </h2>
        {waiting > 0 ? (
          <a className="t-go" href={applyPath({ screen: 'list' })}>
            Review the {waiting} →
          </a>
        ) : (
          <Go className="t-go" href={dPath('applications')} onGo={() => actions.openDiscover({ tab: 'applications' })}>
            All →
          </Go>
        )}
      </div>
      {items.length === 0 ? (
        <>
          <p className="small">Nothing saved yet. A role you save in Discover is kept here.</p>
          <div className="t-foot">
            <Go className="t-go" href={dPath('roles')} onGo={() => actions.openDiscover({ tab: 'roles' })}>
              Find roles →
            </Go>
          </div>
        </>
      ) : (
        <>
          <div className="t-number">
            <span className="big numeral">{items.length}</span>
            <ul className="stages" aria-label="Applications by stage">
              {stages.map((s) => (
                <li key={s.stage} className={s.stage === 'interviewing' && s.n > 0 ? 'is-on' : undefined}>
                  <b className="numeral">{s.n}</b>
                  {STAGE_LABEL[s.stage].toLowerCase()}
                </li>
              ))}
            </ul>
          </div>
          {moving && (
            <div className="t-foot">
              <span className="fine t-ink t-clip">
                <b>{moving.job.employer}</b> · {moving.job.title}
              </span>
              {moving.stage !== 'saved' && <span className="stamp t-stamp">moved</span>}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function DiscoverTile({ run, loading, now }: { run: { createdAt: string; found: number; good: number } | null; loading: boolean; now: number }) {
  const at = run ? Date.parse(run.createdAt) : NaN;
  return (
    <section className="t t-disc" id="db-disc" aria-labelledby="db-disc-h" aria-busy={loading}>
      <h2 id="db-disc-h" className="kicker">
        Discover
      </h2>
      {run ? (
        <>
          <span className="big numeral">{run.found}</span>
          <span className="fine">
            {run.found === 1 ? 'role' : 'roles'} found{Number.isFinite(at) ? ` ${timeAgo(at, now)}` : ''}
          </span>
          <Go className="t-go t-foot-end" href={dPath('roles')} onGo={() => actions.openDiscover({ tab: 'roles' })}>
            <span className="numeral">{run.good}</span> good {run.good === 1 ? 'fit' : 'fits'} →
          </Go>
        </>
      ) : (
        <>
          <span className="small">{loading ? 'Looking for your last search…' : 'Roles that fit you.'}</span>
          <Go className="t-go t-foot-end" href={dPath('roles')} onGo={() => actions.openDiscover({ tab: 'roles' })}>
            Search with AI →
          </Go>
        </>
      )}
    </section>
  );
}

function Spark({ bars }: { bars: number[] }) {
  if (bars.length < 2) return null;
  return (
    <span className="spark" role="img" aria-label={`Share of claims defended in your last ${bars.length} sessions: ${bars.map((b) => `${Math.round(b * 100)}%`).join(', ')}`}>
      {bars.map((b, i) => (
        <i key={i} className={i === bars.length - 1 ? 'last' : undefined} style={{ height: `${Math.max(8, Math.round(b * 100))}%` }} />
      ))}
    </span>
  );
}

export function PracticeTile({ trend, loading, hasSession }: { trend: PracticeTrend | null; loading: boolean; hasSession: boolean }) {
  return (
    <section className="t t-prac" id="db-prac" aria-labelledby="db-prac-h" aria-busy={loading}>
      <div className="card-head">
        <h2 id="db-prac-h" className="kicker">
          Practice
        </h2>
        {trend && <Spark bars={trend.bars} />}
      </div>
      {trend && trend.claims > 0 ? (
        <span className="t-number">
          <span className="big numeral">{trend.defended}</span>
          <span className="fine">
            of {trend.claims} claims
            <br />
            defended
          </span>
        </span>
      ) : (
        <span className="small">{loading ? 'Reading your sessions…' : 'Your own work, asked out loud.'}</span>
      )}
      <div className="t-foot">
        <Go className="t-go" href={practicePath({ screen: 'hub' })} onGo={() => actions.openPractice({ screen: 'hub' })}>
          {hasSession ? 'Practise again →' : 'Start practising →'}
        </Go>
      </div>
    </section>
  );
}

export function MatchTile({ matches, now }: { matches: readonly MatchSummary[]; now: number }) {
  const m = matches[0];
  const at = m ? Date.parse(m.createdAt) : NaN;
  return (
    <section className="t t-mtch" id="db-mtch" aria-labelledby="db-mtch-h">
      <h2 id="db-mtch-h" className="kicker t-ink">
        Posting matches
      </h2>
      {m ? (
        <Go className="t-match" href={matchPath({ screen: 'match', id: m.id })} onGo={() => actions.openMatch({ screen: 'match', id: m.id })}>
          <span className="t-match-title">{m.title}</span>
          <span className="fine t-ink">{[m.company, Number.isFinite(at) ? timeAgo(at, now) : null].filter(Boolean).join(' · ')}</span>
        </Go>
      ) : (
        <span className="small">Paste a posting, see what you can prove.</span>
      )}
      <div className="t-foot">
        {m && <span className="stamp t-stamp">{BAND_LABEL[m.band]}</span>}
        <Go className="t-go" href={matchPath({ screen: 'paste' })} onGo={() => actions.openMatch()} label="Match a posting">
          Match →
        </Go>
      </div>
    </section>
  );
}
