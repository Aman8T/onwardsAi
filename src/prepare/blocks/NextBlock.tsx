/**
 * Next: four buttons, not prompts. Each opens a small working panel in place, and nothing leaves the page until its
 * last button: a calendar file made in the browser, the repo to start from (ranked in code from the stored GitHub
 * digest, nothing fetched), a headline rewritten only from cited evidence, and Practice.
 */
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { PrepareEvidence, PrepareHeadline, PrepareMove, PreparePlan, PrepareRepos } from '../../../shared/prepare';
import type { PrepareClient } from '../api';
import { isAbort, sentenceOf } from '../api';
import { WEEKDAYS, calendarEvents, icsText, phaseSpans, shortDate, webUrl } from '../plan';
import { SkeletonLine } from './Skeletons';

export type NextAction = 'calendar' | 'repo' | 'headline' | 'practice';

interface NextProps {
  plan: PreparePlan;
  move: PrepareMove;
  client: PrepareClient;
  evidence: Map<string, PrepareEvidence>;
  onPractice: () => void;
  narrow: boolean;
}

const TILES: { id: NextAction; label: string; sub: string; icon: ReactNode }[] = [
  {
    id: 'calendar',
    label: 'Put the plan on my calendar',
    sub: 'One block a week, as long as the plan',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
        <rect x="2.5" y="4" width="15" height="13" rx="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M2.5 8h15M6.5 2.5v3M13.5 2.5v3" fill="none" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    ),
  },
  {
    id: 'repo',
    label: 'Pick the repo to start from',
    sub: 'From your GitHub, by what the plan needs',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
        <path d="M3 5.5a2 2 0 0 1 2-2h3l2 2h5a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="none" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    ),
  },
  {
    id: 'headline',
    label: 'Rewrite my headline for this',
    sub: 'Only from facts you can show',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
        <path d="M4 16l1-4 8.5-8.5a2 2 0 0 1 3 3L8 15z" fill="none" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    ),
  },
  {
    id: 'practice',
    label: 'Practice the interview for this role',
    sub: 'Your biggest gaps come first',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
        <rect x="7" y="2.5" width="6" height="10" rx="3" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    ),
  },
];

export function NextBlock({ plan, move, client, evidence, onPractice, narrow }: NextProps) {
  const [action, setAction] = useState<NextAction | null>(null);
  const panelId = 'pp-next-panel';
  return (
    <section id="pp-next" className="pp-next" aria-labelledby="pp-next-h">
      <h2 id="pp-next-h" className="pp-h">
        Next
      </h2>
      <div className={`pp-tiles${narrow ? ' is-narrow' : ''}`}>
        {TILES.map((t) => {
          const on = action === t.id;
          return (
            <button
              key={t.id}
              type="button"
              className={`pp-tile ${t.id === 'practice' ? 'm-ink' : 'm-quiet'}${on ? ' is-on' : ''}`}
              aria-pressed={on}
              aria-controls={panelId}
              onClick={() => setAction(on ? null : t.id)}
            >
              <span className="pp-tile-ic" aria-hidden="true">
                {t.icon}
              </span>
              <span className="pp-tile-text">
                <span className="pp-tile-label">{t.label}</span>
                {!narrow && <span className="pp-tile-sub">{t.sub}</span>}
              </span>
            </button>
          );
        })}
      </div>
      <div id={panelId} hidden={!action}>
        {action === 'calendar' && <CalendarPanel plan={plan} move={move} />}
        {action === 'repo' && <RepoPanel plan={plan} client={client} />}
        {action === 'headline' && <HeadlinePanel plan={plan} client={client} evidence={evidence} />}
        {action === 'practice' && <PracticePanel onPractice={onPractice} />}
      </div>
    </section>
  );
}

/* ───────────────────────── calendar ───────────────────────── */

function CalendarPanel({ plan, move }: { plan: PreparePlan; move: PrepareMove }) {
  const h = plan.state.hoursPerWeek;
  const [weekday, setWeekday] = useState(1);
  const [time, setTime] = useState('19:00');
  const [saved, setSaved] = useState<string | null>(null);
  const spans = phaseSpans(move.phases, h);
  const events = useMemo(() => calendarEvents(move, h, weekday, time), [move, h, weekday, time]);
  const day = WEEKDAYS.find((d) => d.value === weekday) ?? WEEKDAYS[0];

  const download = () => {
    const text = icsText(events, h, `${plan.roleTitle} · ${move.title}`, `${plan.id}-${move.id}`);
    const blob = new Blob([text], { type: 'text/calendar;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'leafout-plan.ics';
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setSaved(`Saved leafout-plan.ics · ${events.length} ${events.length === 1 ? 'block' : 'blocks'}, the first on ${shortDate(events[0].start)}`);
  };

  return (
    <div className="pp-panel m-plain" aria-label="Put the plan on my calendar">
      <div className="pp-head">
        <h3 className="pp-h3">Put the plan on my calendar</h3>
        <span className="numeral pp-meta">no model call</span>
      </div>
      <div className="pp-cal-opts">
        <div role="group" aria-label="Which day" className="pp-cal-days">
          <span className="kicker">Which day</span>
          {WEEKDAYS.map((d) => (
            <button
              key={d.value}
              type="button"
              className={`pp-seg${d.value === weekday ? ' is-on' : ''}`}
              aria-pressed={d.value === weekday}
              aria-label={d.long}
              onClick={() => setWeekday(d.value)}
            >
              {d.label}
            </button>
          ))}
        </div>
        <label className="pp-cal-from">
          <span className="kicker">From</span>
          <input type="time" className="field numeral pp-time" value={time} onChange={(e) => setTime(e.target.value || '19:00')} />
          <span className="numeral pp-meta">· {h} h</span>
        </label>
      </div>
      <p className="pp-muted">
        One block every {day.long} from {shortDate(events[0]?.start ?? new Date())}, {h} h, for {events.length} {events.length === 1 ? 'week' : 'weeks'}.
      </p>
      <ul className="pp-rows" aria-label="Phases on the calendar">
        {move.phases.map((p, i) => (
          <li key={i} className="pp-row-line">
            <span className="numeral pp-meta pp-row-weeks">{spans[i].label}</span>
            <span>{p.title}</span>
          </li>
        ))}
      </ul>
      <div className="pp-actions">
        <button type="button" className="btn pp-btn" onClick={download}>
          Download the calendar file
        </button>
        <span className="pp-muted pp-small">Works with Google, Apple and Outlook calendars</span>
      </div>
      {saved && (
        <p className="kicker" role="status">
          {saved}
        </p>
      )}
    </div>
  );
}

/* ───────────────────────── repo ───────────────────────── */

type Loaded<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error'; message: string };

const REPOS_SHOWN = 3;

function RepoPanel({ plan, client }: { plan: PreparePlan; client: PrepareClient }) {
  const [state, setState] = useState<Loaded<PrepareRepos>>({
    status: 'loading',
  });
  const [attempt, setAttempt] = useState(0);
  // Three repos at first (the list is already ranked, the suggested one first): the rest on request.
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ status: 'loading' });
    client
      .getRepos(plan.id, plan.profileId, ctrl.signal)
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted || isAbort(err)) return;
        setState({ status: 'error', message: sentenceOf(err) });
      });
    return () => ctrl.abort();
  }, [client, plan.id, plan.profileId, attempt]);

  return (
    <div className="pp-panel m-graph" aria-label="Pick the repo to start from">
      <div className="pp-head">
        <h3 className="pp-h3">Pick the repo to start from</h3>
        <span className="numeral pp-meta">from your stored GitHub digest</span>
      </div>
      {state.status === 'loading' && (
        <p role="status" className="pp-wait">
          <span className="sr-only">Ranking your repos…</span>
          <SkeletonLine width="60%" />
          <SkeletonLine width="44%" />
        </p>
      )}
      {state.status === 'error' && (
        <div className="pp-error" role="alert">
          <span>{state.message}</span>
          <button type="button" className="btn btn-sm pp-btn-sm" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </div>
      )}
      {state.status === 'ready' && !state.data.github && (
        <p className="pp-muted">
          Your graph was built without a GitHub source. Add your GitHub profile on Home and run the analysis again to pick a repo here.
        </p>
      )}
      {state.status === 'ready' && state.data.github && (
        <>
          <ul className="pp-rows" aria-label="Your repos, ranked">
            {(showAll ? state.data.repos : state.data.repos.slice(0, REPOS_SHOWN)).map((r) => {
              const url = r.url ? webUrl(r.url) : null;
              return (
                <li key={r.name} className={`pp-repo${r.suggested ? ' is-suggested' : ''}`}>
                  <div className="pp-repo-head">
                    <span className="pp-repo-name">{r.name}</span>
                    {r.suggested && <span className="pp-repo-tag">start here</span>}
                    {url && (
                      <a className="pp-repo-open" href={url} target="_blank" rel="noopener noreferrer">
                        Open on GitHub
                      </a>
                    )}
                  </div>
                  {r.description && <p className="pp-muted pp-small">{r.description}</p>}
                  <div className="pp-ev-row">
                    {r.language && <span className="chip pp-ev-chip">{r.language}</span>}
                    {r.reasons.map((reason) => (
                      <span key={reason} className="chip pp-ev-chip">
                        {reason}
                      </span>
                    ))}
                  </div>
                </li>
              );
            })}
            {!showAll && state.data.repos.length > REPOS_SHOWN && (
              <li className="pp-repo">
                <button type="button" className="btn btn-sm pp-btn-sm" onClick={() => setShowAll(true)}>
                  Show {state.data.repos.length - REPOS_SHOWN} more
                </button>
              </li>
            )}
            <li className="pp-repo">
              <div className="pp-repo-head">
                <span className="pp-repo-name">Start a new repo</span>
              </div>
              <p className="pp-muted pp-small">Cleanest when the plan builds something your repos do not hold yet.</p>
            </li>
          </ul>
          <p className="pp-muted pp-small">Ranked here from the digest the graph already holds. Nothing is fetched, pushed or changed on GitHub.</p>
        </>
      )}
    </div>
  );
}

/* ───────────────────────── headline ───────────────────────── */

function HeadlinePanel({ plan, client, evidence }: { plan: PreparePlan; client: PrepareClient; evidence: Map<string, PrepareEvidence> }) {
  const [state, setState] = useState<Loaded<PrepareHeadline>>({
    status: 'loading',
  });
  const [attempt, setAttempt] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setState((s) => (s.status === 'ready' ? s : { status: 'loading' }));
    setCopied(null);
    client
      .postHeadline(plan.id, { profileId: plan.profileId }, ctrl.signal)
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted || isAbort(err)) return;
        setState({ status: 'error', message: sentenceOf(err) });
      });
    return () => ctrl.abort();
  }, [client, plan.id, plan.profileId, attempt]);

  // "Another wording" keeps the last one on screen while the next is written
  const [rewriting, setRewriting] = useState(false);
  useEffect(() => {
    if (state.status !== 'loading') setRewriting(false);
  }, [state.status]);

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied('Copied');
    } catch {
      setCopied("Couldn't copy here. Select the line and copy it.");
    }
  };

  const data = state.status === 'ready' ? state.data : null;
  const cites = data ? data.evidenceIds.map((id) => evidence.get(id)).filter((e): e is PrepareEvidence => e !== undefined) : [];

  return (
    <div className="pp-panel m-plain" aria-label="Rewrite my headline for this">
      <div className="pp-head">
        <h3 className="pp-h3">Rewrite my headline for this</h3>
        <span className="numeral pp-meta">checked against your facts</span>
      </div>
      {state.status === 'loading' && (
        <p role="status" className="pp-wait">
          <span className="sr-only">Writing one from your evidence…</span>
          <span className="kicker">Writing one from your evidence…</span>
          <SkeletonLine width="70%" />
        </p>
      )}
      {state.status === 'error' && (
        <div className="pp-error" role="alert">
          <span>{state.message}</span>
          <button type="button" className="btn btn-sm pp-btn-sm" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </button>
        </div>
      )}
      {data && (
        <>
          <div className="pp-stack">
            <span className="kicker">Now</span>
            <p className="pp-muted">{data.current || '—'}</p>
          </div>
          {data.proposed ? (
            <div className="pp-proposed">
              <span className="kicker">For {plan.roleTitle}</span>
              <p className="pp-proposed-text">{data.proposed}</p>
            </div>
          ) : (
            <p className="pp-muted">{data.note ?? 'No wording passed the check this time: every draft said more than your evidence shows.'}</p>
          )}
          {data.proposed && data.note && <p className="pp-muted pp-small">{data.note}</p>}
          {cites.length > 0 && (
            <div className="pp-ev-row">
              <span className="kicker">Rests on</span>
              {cites.map((e) => (
                <span key={e.id} className="chip pp-ev-chip" title={e.text}>
                  {e.skill} · {e.sourceLabel.replace(/^your /, '')}
                </span>
              ))}
            </div>
          )}
          <div className="pp-actions">
            {data.proposed && (
              <button type="button" className="btn pp-btn" onClick={() => void copy(data.proposed ?? '')}>
                Copy
              </button>
            )}
            <button
              type="button"
              className="btn pp-btn"
              disabled={rewriting}
              onClick={() => {
                setRewriting(true);
                setState({ status: 'loading' });
                setAttempt((n) => n + 1);
              }}
            >
              Another wording
            </button>
            {copied && (
              <span className="kicker" role="status">
                {copied}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ───────────────────────── practice ───────────────────────── */

function PracticePanel({ onPractice }: { onPractice: () => void }) {
  return (
    <div className="pp-panel pp-panel-row m-quiet" aria-label="Practice the interview for this role">
      <span className="pp-stack">
        <span>Practice · This role</span>
        <span className="pp-muted pp-small">
          Built from this role on your graph: your biggest gaps first, then the claims you can defend.
        </span>
      </span>
      <button type="button" className="btn pp-btn" onClick={onPractice}>
        Start practising
      </button>
    </div>
  );
}
