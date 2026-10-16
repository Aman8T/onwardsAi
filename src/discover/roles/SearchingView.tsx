/**
 * The search in progress (boards "Searching" and "PhoneSearching"): a status line with Stop, the live trail on the
 * left, and on the right the roles as each one is checked, with outlined skeletons for those still to come. The first
 * roles come from Tayyari's own employers about six seconds in, while the web search is still running; the rest follow
 * as it finishes. Nothing animates on its own: skeletons are still boxes, and a new card enters with a short rise.
 *
 * Roles can be saved while the search runs; Prepare, See how you fit and Share wait for the search to finish, because
 * they need the stored run. Stop keeps what has come.
 */
import type { RefObject } from 'react';
import type { AiJob, TrailStep } from '../../../shared/jobsearch';
import { Trail } from '../jobsearch/Trail';
import { IconStop } from './icons';
import { JobCard, SkeletonCard } from './JobCard';
import type { RoleCtl } from './JobCard';

export interface SearchingViewProps {
  /** "Bengaluru and remote in India". */
  where: string;
  steps: readonly TrailStep[];
  jobs: readonly AiJob[];
  now: number;
  ctlOf: (job: AiJob) => RoleCtl;
  onStop: () => void;
  stopRef: RefObject<HTMLButtonElement | null>;
  /** The phone's form: a serif "Searching…", a compact trail, compact cards. */
  compact: boolean;
}

export function SearchingView({ where, steps, jobs, now, ctlOf, onStop, stopRef, compact }: SearchingViewProps) {
  const waiting = compact ? 1 : Math.max(2, 6 - jobs.length);
  const stop = (
    <button ref={stopRef} type="button" className="btn dc-btn" aria-label="Stop the search" onClick={onStop}>
      <IconStop />
      Stop
    </button>
  );

  return (
    <>
      {compact ? (
        <section className="dc-search-head is-compact">
          <div className="dc-search-head-text" role="status" aria-live="polite">
            <div className="kicker">Discover</div>
            <h1 className="serif dc-search-title" data-dc-h1 tabIndex={-1}>
              Searching…
            </h1>
            <p className="dc-muted">{where}</p>
            <p className="dc-muted dc-small">About 20 seconds in all</p>
          </div>
          {stop}
        </section>
      ) : (
        <>
          <section className="dc-hero">
            <div className="kicker">Discover</div>
            <h1 className="serif dc-title" data-dc-h1 tabIndex={-1}>
              Searching for roles like yours
            </h1>
          </section>
          <section className="panel dc-search-head" aria-label="Search status">
            <div className="dc-search-head-text" role="status" aria-live="polite">
              <span className="dc-strong">Searching {where}…</span>
              <span className="dc-muted dc-small">About 20 seconds in all</span>
            </div>
            {stop}
          </section>
        </>
      )}

      <div className="dc-searching">
        <aside className="panel dc-trail-panel" aria-label="What the search is doing">
          <div className="kicker">What it is doing</div>
          <Trail steps={steps} running />
        </aside>

        <section className="dc-live" aria-label="Roles found so far">
          <div>
            {compact && <div className="kicker">First roles</div>}
            <p className="dc-muted dc-live-line">{compact ? "From Tayyari's employers · the web search adds more" : "First roles from Tayyari's employers · the web search adds more as it finishes"}</p>
          </div>
          <ul className="dc-grid" aria-busy="true">
            {jobs.map((job) => (
              <JobCard key={job.id} job={job} now={now} ctl={ctlOf(job)} compact={compact} />
            ))}
            {Array.from({ length: waiting }, (_, i) => (
              <SkeletonCard key={`sk${i}`} reading={i === 0} compact={compact} i={i} />
            ))}
          </ul>
        </section>
      </div>
    </>
  );
}

/** Opening the page: the last results are being fetched. Outlines only, and no motion. */
export function LoadingView() {
  return (
    <div className="dc-loading" aria-busy="true" aria-label="Loading your last results">
      <section className="dc-hero" aria-hidden="true">
        <div className="kicker">Discover</div>
        <span className="dc-sk" style={{ width: 'min(520px, 80%)', height: 44 }} />
      </section>
      <div className="panel dc-loading-bar" aria-hidden="true">
        <span className="dc-sk" style={{ width: 180, height: 14 }} />
        <span className="dc-sk" style={{ width: 120, height: 14 }} />
      </div>
      <ul className="dc-grid" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <SkeletonCard key={i} i={i} />
        ))}
      </ul>
    </div>
  );
}
