/**
 * The state of one "Search with AI" run, from the person pressing the button to the last line of the stream.
 *
 *   idle ──start──▶ running ──done──▶ done            (steps, jobs, the funnel and the run's id stay on screen)
 *                      ├──error / a broken stream──▶ error   (the jobs found before it stay too)
 *                      └──stop──▶ idle (stopped)      (so do the jobs found so far)
 *
 * A search costs money and about twenty seconds, so it is only ever started from a button press (`start`), never from a
 * render. A run is cancelled when the screen goes away and when a new run starts; whatever a cancelled run still
 * says is ignored. The screen keys the hook's owner by profile, city and remote choice, so a different person or place
 * starts clean.
 *
 * Jobs arrive best-first within each batch the server reads (Tayyari's own employers first, the web search's after),
 * and are kept ordered by fit word (strong, good, stretch) with arrival order inside each word, so a late strong role
 * rises above an earlier stretch and nothing else moves. A job that arrives twice replaces the first.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react';
import type { AiJob, AvoidPrefs, FunnelStep, JobSearchEvent, TrailStep } from '../../../shared/jobsearch';
import { ENDED_EARLY, failureOf, streamJobSearch } from './api';
import type { JobSearchFailure, JobSearchStream } from './api';
import { FIT_RANK } from './words';

export type JobSearchPhase = 'idle' | 'running' | 'done' | 'error';

export interface JobSearchState {
  phase: JobSearchPhase;
  steps: TrailStep[];
  jobs: AiJob[];
  funnel: FunnelStep[];
  note?: string;
  /** The stored run (`done` names it); absent until the search finishes. */
  runId?: string;
  /** When the search finished, or ended early (ISO). */
  at?: string;
  failure?: JobSearchFailure;
  /** The person stopped this run (phase is `idle`, and what was found so far is kept). */
  stopped: boolean;
}

export const INITIAL: JobSearchState = { phase: 'idle', steps: [], jobs: [], funnel: [], stopped: false };

/** Far more than a real search sends; a stream that kept going could not grow the page without end. */
const MAX_STEPS = 60;
const MAX_JOBS = 60;

/** The jobs in the order they are shown: strong, then good, then a stretch, arrival order inside each word. */
export function sortJobs(jobs: readonly AiJob[]): AiJob[] {
  return jobs
    .map((job, i) => ({ job, i }))
    .sort((a, b) => FIT_RANK[a.job.fit] - FIT_RANK[b.job.fit] || a.i - b.i)
    .map((x) => x.job);
}

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
  | { type: 'fail'; failure: JobSearchFailure; at: string }
  | { type: 'stop'; at: string };

export function reduce(state: JobSearchState, action: Action): JobSearchState {
  if (action.type === 'start') return { ...INITIAL, phase: 'running' };
  // a run that already ended (or was stopped) says nothing more
  if (state.phase !== 'running') return state;
  switch (action.type) {
    case 'stop':
      return { ...state, phase: 'idle', stopped: true, at: action.at };
    case 'fail':
      return { ...state, phase: 'error', failure: action.failure, at: action.at };
    case 'event': {
      const e = action.event;
      switch (e.type) {
        case 'step':
          return state.steps.length >= MAX_STEPS ? state : { ...state, steps: [...state.steps, e.step] };
        case 'job':
          return { ...state, jobs: insertJob(state.jobs, e.job) };
        case 'done':
          return { ...state, phase: 'done', funnel: e.funnel, note: e.note, runId: e.runId || undefined, at: e.createdAt };
        case 'error':
          return { ...state, phase: 'error', failure: { message: e.message }, at: new Date().toISOString() };
      }
    }
  }
}

export interface JobSearchOptions {
  /** Where the stream comes from. The dev gallery passes a scripted one; the app uses the server's. */
  stream?: JobSearchStream;
  /** Start as soon as the owner mounts. For the dev gallery's scripted streams only: a real search is never started by a render. */
  autoStart?: boolean;
}

/** What a search is asked for: the place, whether remote counts, and what "Not for me" taught. */
export interface JobSearchParams {
  city?: string;
  remote: boolean;
  avoid: AvoidPrefs;
}

export interface JobSearch extends JobSearchState {
  start: () => void;
  stop: () => void;
}

export function useJobSearch(profileId: string, params: JobSearchParams, { stream = streamJobSearch, autoStart = false }: JobSearchOptions = {}): JobSearch {
  const [state, dispatch] = useReducer(reduce, INITIAL);
  const abort = useRef<AbortController | null>(null);
  const latest = useRef({ profileId, params, stream });
  latest.current = { profileId, params, stream };

  const start = useCallback(() => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    dispatch({ type: 'start' });
    const { profileId: id, params: p, stream: run } = latest.current;
    let ended = false;
    run(
      { profileId: id, ...(p.city ? { city: p.city } : {}), remote: p.remote, avoid: p.avoid },
      (event) => {
        if (ctrl.signal.aborted) return;
        if (event.type === 'done' || event.type === 'error') ended = true;
        dispatch({ type: 'event', event });
      },
      ctrl.signal,
    ).then(
      () => {
        if (!ctrl.signal.aborted && !ended) dispatch({ type: 'fail', failure: { message: ENDED_EARLY }, at: new Date().toISOString() });
      },
      (err: unknown) => {
        if (!ctrl.signal.aborted) dispatch({ type: 'fail', failure: failureOf(err), at: new Date().toISOString() });
      },
    );
  }, []);

  const stop = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    dispatch({ type: 'stop', at: new Date().toISOString() });
  }, []);

  useEffect(() => () => abort.current?.abort(), []);

  useEffect(() => {
    if (autoStart) start();
  }, [autoStart, start]);

  return { ...state, start, stop };
}
