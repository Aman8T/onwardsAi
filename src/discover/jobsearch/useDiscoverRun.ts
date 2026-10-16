/**
 * What Discover shows for one profile, city and remote choice: the last finished search, fetched once when the page
 * opens, and the search the person starts by pressing the button.
 *
 *   opening   GET /api/jobsearch/latest  ──▶ results at once (or the Start state when there is none, a 404)
 *   searching the stream, roles arriving one by one; the old results are set aside, not thrown away
 *   finished  the stream's `done` replaces them (and is what the next visit finds)
 *   stopped / failed  what came so far is shown if any came; otherwise the results from before stay
 *
 * A search is never started from here: only `start()` does, and only a button calls it. The owner is keyed by
 * profile, city and remote choice, so changing any of them starts clean (and cancels a search in flight).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AiJob, FunnelStep, JobSearchRun, TrailStep } from '../../../shared/jobsearch';
import { failureOf, getLatestRun } from './api';
import type { JobSearchFailure, LatestLoader } from './api';
import { sortJobs, useJobSearch } from './useJobSearch';
import type { JobSearchOptions, JobSearchParams } from './useJobSearch';

/** A set of results on screen: a finished search (it has a run id) or what a stopped or failed one had found. */
export interface RunData {
  /** The stored search; absent for a partial run, which nothing can be fetched for. */
  runId?: string;
  /** When the search finished (ISO); for a partial run, when it ended. */
  createdAt: string;
  jobs: AiJob[];
  funnel: FunnelStep[];
  trail: TrailStep[];
  note?: string;
  /** The city the search read, as the server names it. */
  city?: string;
  remote: boolean;
  /** The search ended early: the person stopped it, or it failed. */
  partial?: 'stopped' | 'failed';
}

export interface DiscoverRunOptions extends JobSearchOptions {
  /** How the last results are fetched. The dev gallery passes a scripted one. */
  loadLatest?: LatestLoader;
}

export interface DiscoverRun {
  /** The last-results fetch is still in flight, and nothing else is known yet. */
  loading: boolean;
  running: boolean;
  /** The trail so far, and the roles found so far (while running). */
  steps: TrailStep[];
  liveJobs: AiJob[];
  /** The results to show when no search is running; null = nothing yet (the Start state). */
  shown: RunData | null;
  /** The last search that did not finish, or a problem with the profile (404) or the service (503). */
  failure?: JobSearchFailure;
  start: () => void;
  stop: () => void;
}

const fromRun = (run: JobSearchRun): RunData => ({
  runId: run.runId,
  createdAt: run.createdAt,
  jobs: sortJobs(run.jobs),
  funnel: run.funnel,
  trail: run.trail,
  note: run.note,
  city: run.city,
  remote: run.remote,
});

export function useDiscoverRun(profileId: string, params: JobSearchParams, { loadLatest = getLatestRun, stream, autoStart }: DiscoverRunOptions = {}): DiscoverRun {
  const search = useJobSearch(profileId, params, { stream, autoStart });
  const [saved, setSaved] = useState<RunData | null>(null);
  const [lookup, setLookup] = useState<{ status: 'loading' | 'ready'; failure?: JobSearchFailure }>({ status: 'loading' });
  const searched = useRef(false);

  // the last finished search, once, when this owner mounts; a failed look is not shown as a failed search
  useEffect(() => {
    const ctrl = new AbortController();
    loadLatest(profileId, params.city, params.remote, ctrl.signal).then(
      (run) => {
        if (ctrl.signal.aborted) return;
        if (run && !searched.current) setSaved(fromRun(run));
        setLookup({ status: 'ready' });
      },
      (err: unknown) => {
        if (ctrl.signal.aborted) return;
        const failure = failureOf(err);
        // the profile or the service is the problem: say so; anything else (a dropped connection) just means no saved results
        setLookup({ status: 'ready', ...(failure.status === 404 || failure.status === 503 ? { failure } : {}) });
      },
    );
    return () => ctrl.abort();
    // keyed by the owner: profile, city and remote never change while this hook lives
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // a finished search is what the next visit finds, so it replaces the older results
  const finished = useMemo<RunData | null>(
    () =>
      search.phase === 'done'
        ? {
            runId: search.runId,
            createdAt: search.at ?? new Date().toISOString(),
            jobs: search.jobs,
            funnel: search.funnel,
            trail: search.steps,
            note: search.note,
            remote: params.remote,
            ...(params.city ? { city: params.city } : {}),
          }
        : null,
    // the owner is keyed by place, so only the search itself changes the answer
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [search.phase, search.runId, search.at, search.jobs, search.funnel, search.steps, search.note],
  );
  useEffect(() => {
    if (finished) setSaved(finished);
  }, [finished]);

  const start = useCallback(() => {
    searched.current = true;
    search.start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search.start]);

  const running = search.phase === 'running';
  const ended = search.phase === 'error' || (search.phase === 'idle' && search.stopped);
  const partial: RunData | null =
    ended && search.jobs.length > 0
      ? {
          createdAt: search.at ?? new Date().toISOString(),
          jobs: search.jobs,
          funnel: search.funnel,
          trail: search.steps,
          remote: params.remote,
          ...(params.city ? { city: params.city } : {}),
          partial: search.phase === 'error' ? 'failed' : 'stopped',
        }
      : null;

  return {
    loading: lookup.status === 'loading' && !searched.current && saved === null,
    running,
    steps: search.steps,
    liveJobs: search.jobs,
    shown: running ? null : (finished ?? partial ?? saved),
    failure: search.phase === 'error' ? search.failure : lookup.failure,
    start,
    stop: search.stop,
  };
}
