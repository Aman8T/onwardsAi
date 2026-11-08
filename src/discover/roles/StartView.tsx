/**
 * The first visit (board "Start" of the Discover canvas): nothing has been searched yet. A serif line, one sentence on
 * what the search does, and a panel with the city, the remote choice and the one button that starts it. Nothing here
 * searches by itself: the search runs only when the person presses "Search with AI". Under them, how the search works
 * in three plain steps, and, when there are saved roles, a preview of Your applications so the list is reachable before
 * the first search.
 */
import type { ReactNode } from 'react';
import type { DiscoverCity } from '../../../shared/discover';
import { STAGE_LABEL } from '../applications/tracker';
import type { TrackedJob } from '../applications/tracker';
import { CityControl, RemoteToggle } from './SearchBar';
import { IconSearch } from './icons';

const STEPS = [
  { title: "Search employers' sites", body: "Claude plans a few searches from your resume and your next roles, then runs them on employers' own job sites." },
  { title: 'Read their boards, so every role is open today', body: "It reads each employer's own board, so a role the employer has taken down is left out." },
  { title: 'Check each posting against your evidence', body: 'Each ask in a posting is set against a line from your resume or your projects, and the ones you do not show are named.' },
];

const day = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '';
};

export interface StartViewProps {
  cityName?: string;
  citySource: 'resume' | 'you' | 'none';
  cities: readonly DiscoverCity[];
  picked?: string;
  resumeCity?: string;
  onPick: (city: string | undefined) => void;
  remote: boolean;
  onRemote: (on: boolean) => void;
  /** Without a city the search reads remote roles only; say so. */
  onSearch: () => void;
  blocked: boolean;
  blockedWhy?: string;
  /** Without an account the server allows three searches a day; said only when we know the person is not signed in. */
  signedIn: boolean | null;
  /** Failure or profile notes shown above the panel. */
  notes?: ReactNode;
  apps: readonly TrackedJob[];
  appsHref: string;
  onApps: () => void;
}

export function StartView(p: StartViewProps) {
  // a closed role is history, not something waiting on the person
  const open = p.apps.filter((t) => t.stage !== 'closed');
  return (
    <>
      <section className="dc-start">
        <div className="dc-start-copy">
          <div className="kicker">Discover</div>
          <h1 className="serif dc-start-title" data-dc-h1 tabIndex={-1}>
            Roles you would not find on your own
          </h1>
          <p className="dc-start-lede">Claude searches employers' own job sites for roles like yours, reads their boards so every role is open today, and checks each posting against your evidence.</p>
        </div>

        <section className="panel dc-start-panel" aria-label="Search">
          <div className="dc-start-where">
            <div className="kicker">Where</div>
            <CityControl name={p.cityName} source={p.citySource} cities={p.cities} picked={p.picked} resumeCity={p.resumeCity} onPick={p.onPick} boxed />
            {!p.cityName && <p className="dc-muted dc-small">Without a city, the search looks at remote roles open to India.</p>}
          </div>
          <div className="dc-start-remote">
            <RemoteToggle checked={p.remote} onChange={p.onRemote} large />
          </div>
          <div className="dc-start-go">
            <button type="button" className="btn btn-primary dc-btn dc-wide" aria-disabled={p.blocked || undefined} title={p.blocked ? p.blockedWhy : undefined} onClick={() => !p.blocked && p.onSearch()}>
              <IconSearch />
              Search with AI
            </button>
            <p className="dc-muted dc-small">About 20 seconds{p.signedIn === false ? ' · 3 searches a day without an account' : ''}</p>
          </div>
        </section>
      </section>

      {p.notes}

      <section className="dc-how" aria-label="How the search works">
        <div className="kicker">How the search works</div>
        <ol className="dc-how-list">
          {STEPS.map((s, i) => (
            <li key={s.title}>
              <span className="numeral dc-how-n" aria-hidden="true">
                {i + 1}
              </span>
              <div>
                <p className="dc-strong">{s.title}</p>
                <p className="dc-muted">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {open.length > 0 && (
        <section className="panel dc-apps" aria-label="Your applications">
          <div className="dc-apps-head">
            <div>
              <h2 className="title dc-apps-title">Your applications · {open.length}</h2>
              <p className="dc-muted dc-small">Kept in this browser</p>
            </div>
            <a
              className="btn btn-sm dc-btn-sm"
              href={p.appsHref}
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                e.preventDefault();
                p.onApps();
              }}
            >
              Open your applications
            </a>
          </div>
          <ul className="dc-apps-list">
            {open.slice(0, 4).map((t) => {
              const detail = [t.appliedAt ? day(t.appliedAt) : '', t.note ? `“${t.note}”` : ''].filter(Boolean).join(' · ');
              return (
                <li key={t.job.id}>
                  <div className="dc-app-item">
                    <span className="dc-strong">{t.job.title}</span>
                    <span className="dc-muted">{t.job.employer}</span>
                    <span className="dc-app-meta">
                      <span className={`chip${t.stage === 'applied' || t.stage === 'interviewing' || t.stage === 'offer' ? ' is-on' : ''}`}>{STAGE_LABEL[t.stage]}</span>
                      {detail && <span className="dc-muted dc-small">{detail}</span>}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </>
  );
}
