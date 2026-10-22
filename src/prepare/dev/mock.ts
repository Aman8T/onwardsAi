/**
 * A scripted `PrepareClient` for the dev page: the stream plays the fixture back the way the server would (gaps at
 * once, then the standing and the moves on timers, then done), or stops short, or fails, as the mode says. Saves,
 * repos and headlines answer after a short wait. Nothing leaves the browser.
 */
import type { PrepareEvent, PrepareHeadline, PreparePlan, PrepareRepos, PrepareState } from '../../../shared/prepare';
import { ApiError } from '../../lib/api';
import type { PrepareClient } from '../api';

export type MockMode =
  /** A stored plan: gaps, then done, at once. */
  | 'stored'
  /** A new plan: gaps at once, standing after a moment, then the moves, then done. */
  | 'stream'
  /** The gaps and nothing more (the outlines stay). */
  | 'hang'
  /** The gaps and the standing, then nothing more. */
  | 'hang-moves'
  /** The gaps, then an error. */
  | 'error'
  /** The POST itself is refused (a 429). */
  | 'refused'
  /** The first save is a 409: another tab saved first. */
  | 'conflict';

export interface MockOptions {
  plan: PreparePlan;
  repos: PrepareRepos;
  headlines: PrepareHeadline[];
  mode: MockMode;
  /** The timers, scaled (1 = the server's own pace). */
  pace?: number;
}

const abortError = () => new DOMException('The user aborted a request.', 'AbortError');

/** A copy of the plan as the `gaps` event carries it: numbers only, no headline, no whys, no moves. */
function gapsOnly(plan: PreparePlan): PreparePlan {
  return {
    ...plan,
    headline: undefined,
    gaps: plan.gaps.map((g) => ({
      name: g.name,
      skillId: g.skillId,
      current: g.current,
      target: g.target,
      biggest: g.biggest,
    })),
    moves: [],
    version: 0,
  };
}

export function mockClient(o: MockOptions): PrepareClient {
  const pace = o.pace ?? 1;
  let plan: PreparePlan = JSON.parse(JSON.stringify(o.plan)) as PreparePlan;
  let saves = 0;
  let headlineCalls = 0;

  const wait = (ms: number, signal?: AbortSignal) =>
    new Promise<void>((resolve, reject) => {
      const t = window.setTimeout(resolve, ms * pace);
      signal?.addEventListener('abort', () => {
        window.clearTimeout(t);
        reject(abortError());
      });
    });

  return {
    async stream(req, onEvent, signal) {
      await wait(120, signal);
      if (o.mode === 'refused') throw new ApiError("You've made a few plans today. Please wait a while and try again.", 429);
      if (o.mode === 'stored' && !req.fresh) {
        onEvent({ type: 'gaps', plan: gapsOnly(plan) });
        onEvent({ type: 'done', plan });
        return;
      }
      const fresh = req.fresh
        ? {
            ...plan,
            createdAt: new Date().toISOString(),
            state: {
              hoursPerWeek: 6,
              moveIndex: 0,
              done: [],
              links: [],
            } as PrepareState,
            version: 1,
          }
        : plan;
      onEvent({ type: 'gaps', plan: gapsOnly(fresh) });
      if (o.mode === 'hang') return new Promise<void>((_, reject) => signal?.addEventListener('abort', () => reject(abortError())));
      if (o.mode === 'error') {
        await wait(1400, signal);
        onEvent({
          type: 'error',
          message: 'The moves could not be written from your evidence this time.',
          code: 'moves_failed',
        });
        return;
      }
      await wait(1100, signal);
      onEvent({ type: 'standing', headline: fresh.headline, gaps: fresh.gaps });
      if (o.mode === 'hang-moves') return new Promise<void>((_, reject) => signal?.addEventListener('abort', () => reject(abortError())));
      await wait(1600, signal);
      onEvent({ type: 'moves', moves: fresh.moves });
      await wait(500, signal);
      plan = fresh;
      onEvent({ type: 'done', plan });
    },

    async getPlan(planId, _profileId, signal) {
      await wait(250, signal);
      if (planId !== plan.id) throw new ApiError('This plan is not here any more.', 404);
      return plan;
    },

    async putState(_planId, update, signal) {
      await wait(300, signal);
      saves++;
      if (o.mode === 'conflict' && saves === 1) {
        // another tab saved first: the stored plan moved on
        plan = { ...plan, version: plan.version + 1 };
        throw new ApiError('This plan changed in another tab.', 409);
      }
      if (update.version !== plan.version) throw new ApiError('This plan changed in another tab.', 409);
      plan = { ...plan, state: update.state, version: plan.version + 1 };
      return { state: plan.state, version: plan.version };
    },

    async getRepos(_planId, _profileId, signal) {
      await wait(500, signal);
      return o.repos;
    },

    async postHeadline(_planId, _req, signal) {
      await wait(900, signal);
      const h = o.headlines[headlineCalls % o.headlines.length];
      headlineCalls++;
      return h;
    },

    async tickFix(fixId, update, signal) {
      await wait(300, signal);
      const task = plan.practice?.find((t) => t.id === fixId);
      if (!task) throw new ApiError("That Fix step isn't here any more. Open the debrief again to see its steps.", 404);
      const { doneAt: _was, ...rest } = task;
      const next = update.done ? { ...rest, doneAt: new Date().toISOString() } : rest;
      plan = { ...plan, practice: plan.practice?.map((t) => (t.id === fixId ? next : t)) };
      return next;
    },
  };
}
