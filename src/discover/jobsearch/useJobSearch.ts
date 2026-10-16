/**
 * The state of one "Search with AI" run, from the person pressing the button to the last line of the stream.
 *
 *   idle ──start──▶ running ──done──▶ done            (steps, jobs and the funnel stay on screen)
 *                      ├──error / a broken stream──▶ error   (the jobs found before it stay too)
 *                      └──stop──▶ idle (stopped)      (so do the jobs found so far)
 *
 * A search costs money and about a minute, so it is only ever started from a button press (`start`), never from a
 * render. A run is cancelled when the screen goes away and when a new run starts; whatever a cancelled run still
 * says is ignored. The screen keys the panel by profile and city, so a different person or place starts clean.
 *
 * Jobs arrive best-first within each batch the server reads, and are kept ordered by fit word (strong, good,
 * stretch) with arrival order inside each word, so a late strong role rises above an earlier stretch and nothing
 * else moves. A job that arrives twice replaces the first.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react';
import type { AiFitWord, AiJob, FunnelStep, JobSearchEvent, TrailStep } from '../../../shared/jobsearch';
import { ENDED_EARLY, failureOf, streamJobSearch } from './api';
import type { JobSearchFailure, JobSearchStream } from './api';

export type JobSearchPhase = 'idle' | 'running' | 'done' | 'error';

export interface JobSearchState {
  phase: JobSearchPhase;
  steps: TrailStep[];
  jobs: AiJob[];
  funnel: FunnelStep[];
  note?: string;
  failure?: JobSearchFailure;
  /** The person stopped this run (phase is `idle`, and what was found so far is kept). */
  stopped: boolean;
}

export const INITIAL: JobSearchState = { phase: 'idle', steps: [], jobs: [], funnel: [], stopped: false };

/** Far more than a real search sends; a stream that kept going could not grow the page without end. */
const MAX_STEPS = 60;
const MAX_JOBS = 60;

const FIT_RANK: Record<AiFitWord, number> = { strong: 0, good: 1, stretch: 2 };

/** `job` placed after every job with the same or a better fit word, so arrival order holds inside a word. */
export function insertJob(jobs: readonly AiJob[], job: AiJob): AiJob[] {
  const rest = jobs.filter((j) => j.id !== job.id);
  if (rest.length >= MAX_JOBS) return rest;
  const at = rest.findIndex((j) => FIT_RANK[j.fit] > FIT_RANK[job.fit]);
  if (at === -1) return [...rest, job];
  return [...rest.slice(0, at), job, ...rest.slice(at)];
}

type Action =
  | { type: 'start' }
  | { type: 'event'; event: JobSearchEvent }
  | { type: 'fail'; failure: JobSearchFailure }
  | { type: 'stop' };

export function reduce(state: JobSearchState, action: Action): JobSearchState {
  if (action.type === 'start') return { ...INITIAL, phase: 'running' };
  // a run that already ended (or was stopped) says nothing more
  if (state.phase !== 'running') return state;
  switch (action.type) {
    case 'stop':
      return { ...state, phase: 'idle', stopped: true };
    case 'fail':
      return { ...state, phase: 'error', failure: action.failure };
    case 'event': {
      const e = action.event;
      switch (e.type) {
        case 'step':
          return state.steps.length >= MAX_STEPS ? state : { ...state, steps: [...state.steps, e.step] };
        case 'job':
          return { ...state, jobs: insertJob(state.jobs, e.job) };
        case 'done':
          return { ...state, phase: 'done', funnel: e.funnel, note: e.note };
        case 'error':
          return { ...state, phase: 'error', failure: { message: e.message } };
      }
    }
  }
}

export interface JobSearchOptions {
  /** Where the stream comes from. The dev gallery passes a scripted one; the app uses the server's. */
  stream?: JobSearchStream;
  /** Start as soon as the panel mounts. For the dev gallery's scripted streams only: a real search is never started by a render. */
  autoStart?: boolean;
}

export interface JobSearch extends JobSearchState {
  start: () => void;
  stop: () => void;
}

export function useJobSearch(profileId: string, city: string | undefined, { stream = streamJobSearch, autoStart = false }: JobSearchOptions = {}): JobSearch {
  const [state, dispatch] = useReducer(reduce, INITIAL);
  const abort = useRef<AbortController | null>(null);
  const latest = useRef({ profileId, city, stream });
  latest.current = { profileId, city, stream };

  const start = useCallback(() => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    dispatch({ type: 'start' });
    const { profileId: id, city: where, stream: run } = latest.current;
    let ended = false;
    run(
      { profileId: id, ...(where ? { city: where } : {}) },
      (event) => {
        if (ctrl.signal.aborted) return;
        if (event.type === 'done' || event.type === 'error') ended = true;
        dispatch({ type: 'event', event });
      },
      ctrl.signal,
    ).then(
      () => {
        if (!ctrl.signal.aborted && !ended) dispatch({ type: 'fail', failure: { message: ENDED_EARLY } });
      },
      (err: unknown) => {
        if (!ctrl.signal.aborted) dispatch({ type: 'fail', failure: failureOf(err) });
      },
    );
  }, []);

  const stop = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    dispatch({ type: 'stop' });
  }, []);

  useEffect(() => () => abort.current?.abort(), []);

  useEffect(() => {
    if (autoStart) start();
  }, [autoStart, start]);

  return { ...state, start, stop };
}
