/**
 * The plan runs: one per profile, role and requirement, kept outside React so a stream carries on when the person
 * switches threads and is still there when they come back (the chat's answers work the same way, in the store).
 *
 * A run starts from the stored plan when the thread knows its id (`GET`), else from `POST /api/prepare`, which streams
 * the gaps at once, then the standing and the moves as each model call lands, then `done` with the stored plan. State
 * changes (hours, the move, ticks, links) apply at once and save after a short pause; a 409 (another tab saved first)
 * takes the server's version and saves ours on top, once; a second clash means theirs stands.
 */
import { useSyncExternalStore } from 'react';
import type { PrepareEvent, PreparePlan, PrepareRequest, PrepareState } from '../../shared/prepare';
import type { PrepareClient } from './api';
import { isAbort, isStatus, sentenceOf } from './api';

export type RunPhase = 'idle' | 'loading' | 'streaming' | 'ready' | 'stopped' | 'error';
export type SaveStatus = 'idle' | 'saving' | 'failed';

export interface PrepareRun {
  phase: RunPhase;
  /** From the `gaps` event on: the numbers at once, the rest as it arrives. */
  plan: PreparePlan | null;
  /** The headline and the whys have arrived (or the plan was stored). */
  standing: boolean;
  /** The moves, with their phases, have arrived. */
  moves: boolean;
  error?: string;
  save: SaveStatus;
  saveError?: string;
}

interface Entry {
  client: PrepareClient;
  req: PrepareRequest;
  onPlanId?: (planId: string) => void;
  run: PrepareRun;
  ctrl: AbortController | null;
  saveTimer: number | undefined;
  /** Saves run one after another, each with the plan's version at the time it runs. */
  saving: Promise<void>;
  /** JSON of the state the server last confirmed, so an unchanged state is never sent. */
  lastSaved: string;
}

const IDLE: PrepareRun = {
  phase: 'idle',
  plan: null,
  standing: false,
  moves: false,
  save: 'idle',
};
const SAVE_DEBOUNCE = 600;

const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();

export const runKey = (profileId: string, roleId: string, requirement?: string): string => `${profileId}|${roleId}|${requirement ?? ''}`;

const notify = () => listeners.forEach((l) => l());

function update(entry: Entry, patch: Partial<PrepareRun>) {
  entry.run = { ...entry.run, ...patch };
  notify();
}

/** Make sure a run exists for `key`, with this client and request; the callbacks are refreshed on every call. */
export function bindRun(
  key: string,
  opts: {
    client: PrepareClient;
    req: PrepareRequest;
    onPlanId?: (planId: string) => void;
  },
) {
  const have = entries.get(key);
  if (have) {
    have.client = opts.client;
    have.req = opts.req;
    have.onPlanId = opts.onPlanId;
    return;
  }
  entries.set(key, {
    ...opts,
    run: IDLE,
    ctrl: null,
    saveTimer: undefined,
    saving: Promise.resolve(),
    lastSaved: '',
  });
}

export const getRun = (key: string): PrepareRun => entries.get(key)?.run ?? IDLE;

/** Start (or restart) a run: a stored plan by id when known, else a stream; `fresh` asks for a new plan. No-op in flight. */
export function startRun(key: string, opts: { planId?: string; fresh?: boolean } = {}) {
  const entry = entries.get(key);
  if (!entry) return;
  if (entry.run.phase === 'loading' || entry.run.phase === 'streaming') return;
  void load(entry, opts);
}

/** Abort a stream; what arrived stays on screen. */
export function stopRun(key: string) {
  const entry = entries.get(key);
  if (!entry?.ctrl) return;
  entry.ctrl.abort();
  entry.ctrl = null;
  update(entry, { phase: 'stopped' });
}

async function load(entry: Entry, { planId, fresh }: { planId?: string; fresh?: boolean }) {
  const ctrl = new AbortController();
  entry.ctrl = ctrl;
  window.clearTimeout(entry.saveTimer);
  entry.run = { ...IDLE, phase: planId && !fresh ? 'loading' : 'streaming' };
  notify();

  const finish = (plan: PreparePlan) => {
    entry.lastSaved = JSON.stringify(plan.state);
    update(entry, {
      phase: 'ready',
      plan,
      standing: true,
      moves: true,
      error: undefined,
    });
    entry.onPlanId?.(plan.id);
  };

  try {
    if (planId && !fresh) {
      try {
        const plan = await entry.client.getPlan(planId, entry.req.profileId, ctrl.signal);
        if (ctrl.signal.aborted) return;
        finish(plan);
        return;
      } catch (err) {
        if (ctrl.signal.aborted || isAbort(err)) return;
        // the stored plan is gone (or is another graph's): make one
        if (!isStatus(err, 404) && !isStatus(err, 403)) throw err;
      }
    }
    update(entry, { phase: 'streaming' });
    let ended = false;
    const req: PrepareRequest = fresh ? { ...entry.req, fresh: true } : entry.req;
    await entry.client.stream(
      req,
      (e) => {
        if (ctrl.signal.aborted || ended) return;
        ended = apply(entry, e, finish);
      },
      ctrl.signal,
    );
    if (!ended && !ctrl.signal.aborted)
      update(entry, {
        phase: 'error',
        error: 'The plan ended before it was saved. Try again.',
      });
  } catch (err) {
    if (ctrl.signal.aborted || isAbort(err)) {
      if (entry.run.phase !== 'stopped') update(entry, { phase: 'stopped' });
      return;
    }
    update(entry, { phase: 'error', error: sentenceOf(err) });
  } finally {
    if (entry.ctrl === ctrl) entry.ctrl = null;
  }
}

/** One stream event into the run; true when the stream is over (`done` or `error`). */
function apply(entry: Entry, e: PrepareEvent, finish: (plan: PreparePlan) => void): boolean {
  const plan = entry.run.plan;
  switch (e.type) {
    case 'gaps':
      update(entry, {
        plan: e.plan,
        standing: e.plan.headline !== undefined || e.plan.gaps.some((g) => g.why !== undefined),
        moves: e.plan.moves.length > 0,
      });
      return false;
    case 'standing':
      if (plan)
        update(entry, {
          plan: { ...plan, headline: e.headline, gaps: e.gaps },
          standing: true,
        });
      return false;
    case 'moves':
      if (plan) update(entry, { plan: { ...plan, moves: e.moves }, moves: true });
      return false;
    case 'done':
      finish(e.plan);
      return true;
    case 'error':
      update(entry, { phase: 'error', error: e.message });
      return true;
  }
}

/* ───────────────────────── state ───────────────────────── */

/** Change the plan's state on screen at once; it saves after a short pause (only a stored plan can be saved). */
export function updateState(key: string, fn: (s: PrepareState) => PrepareState) {
  const entry = entries.get(key);
  const plan = entry?.run.plan;
  if (!entry || !plan) return;
  const state = fn(plan.state);
  if (state === plan.state) return;
  update(entry, { plan: { ...plan, state } });
  scheduleSave(entry, SAVE_DEBOUNCE);
}

/** Save again after a failure. */
export function retrySave(key: string) {
  const entry = entries.get(key);
  if (entry) scheduleSave(entry, 0);
}

function scheduleSave(entry: Entry, delay: number) {
  if (entry.run.phase !== 'ready') return;
  window.clearTimeout(entry.saveTimer);
  entry.saveTimer = window.setTimeout(() => {
    entry.saving = entry.saving.then(() => doSave(entry));
  }, delay);
}

async function doSave(entry: Entry) {
  const plan = entry.run.plan;
  if (!plan || entry.run.phase !== 'ready') return;
  const { state } = plan;
  const json = JSON.stringify(state);
  if (json === entry.lastSaved) {
    if (entry.run.save !== 'idle') update(entry, { save: 'idle', saveError: undefined });
    return;
  }
  update(entry, { save: 'saving', saveError: undefined });
  const { client, req } = entry;
  const put = (version: number) => client.putState(plan.id, { profileId: req.profileId, version, state });
  try {
    let saved;
    try {
      saved = await put(plan.version);
    } catch (err) {
      if (!isStatus(err, 409)) throw err;
      // another tab saved first: take its version and save ours on top, once
      const fresh = await client.getPlan(plan.id, req.profileId);
      try {
        saved = await put(fresh.version);
      } catch (err2) {
        if (!isStatus(err2, 409)) throw err2;
        // a second clash: theirs stands
        entry.lastSaved = JSON.stringify(fresh.state);
        const cur = entry.run.plan;
        if (cur)
          update(entry, {
            plan: { ...cur, state: fresh.state, version: fresh.version },
            save: 'idle',
            saveError: undefined,
          });
        return;
      }
    }
    entry.lastSaved = json;
    const cur = entry.run.plan;
    // the person may have moved on while this saved: keep what they see, and the next save carries it
    if (cur)
      update(entry, {
        plan: {
          ...cur,
          version: saved.version,
          state: cur.state === state ? saved.state : cur.state,
        },
        save: 'idle',
        saveError: undefined,
      });
  } catch (err) {
    update(entry, { save: 'failed', saveError: sentenceOf(err) });
  }
}

/* ───────────────────────── hook ───────────────────────── */

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
};

export function usePrepareRun(key: string): PrepareRun {
  return useSyncExternalStore(
    subscribe,
    () => getRun(key),
    () => IDLE,
  );
}
