/**
 * What "Not for me" taught the search, kept in this browser only:
 *
 *   localStorage "tayyari.discover.avoid.<profileId>"  →  AvoidPrefs { jobIds, employers, noStepUp }
 *
 * Every reason hides the role (by job id) and the next search never brings it back. "Too senior" also leaves out roles
 * a step up (`noStepUp`); "Company" also skips the employer by name. The whole thing is sent with each search and
 * nothing else about the person is. The reason itself is remembered for this visit only (it labels the slim "Hidden"
 * row so Undo can take back exactly what the reason added); `AvoidPrefs` is what the server is sent, field for field.
 *
 * A small subscribable store per profile (like the tracker), so the card, the toolbar and the deck read one answer.
 * Every storage access is wrapped: with storage blocked the choices live for this page only.
 */
import { useSyncExternalStore } from 'react';
import type { AvoidPrefs } from '../../../shared/jobsearch';

/** The reasons the popover offers, in order. */
export type NoReason = 'senior' | 'stack' | 'location' | 'company' | 'applied' | 'other';

export const NO_REASONS: readonly NoReason[] = ['senior', 'stack', 'location', 'company', 'applied', 'other'];

export const NO_REASON_LABEL: Record<NoReason, string> = {
  senior: 'Too senior',
  stack: 'Wrong stack',
  location: 'Location',
  company: 'Company',
  applied: 'Already applied',
  other: 'Something else',
};

/** The server keeps at most this many of each; the newest stay. */
const MAX_ITEMS = 200;
const EMPLOYER_MAX = 200;

export const EMPTY_AVOID: AvoidPrefs = { jobIds: [], employers: [], noStepUp: false };

export const avoidKey = (profileId: string) => `tayyari.discover.avoid.${profileId}`;

const strings = (v: unknown, max: number): string[] => {
  if (!Array.isArray(v)) return [];
  const seen = new Set<string>();
  for (const x of v) if (typeof x === 'string' && x !== '' && x.length <= max) seen.add(x);
  return [...seen].slice(-MAX_ITEMS);
};

/** Whatever was stored, as clean preferences: known fields only. */
export function parseAvoid(raw: unknown): AvoidPrefs {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return EMPTY_AVOID;
  const o = raw as Record<string, unknown>;
  return { jobIds: strings(o.jobIds, 200), employers: strings(o.employers, EMPLOYER_MAX), noStepUp: o.noStepUp === true };
}

export const isEmptyAvoid = (a: AvoidPrefs) => a.jobIds.length === 0 && a.employers.length === 0 && !a.noStepUp;

const cache = new Map<string, AvoidPrefs>();
/** Why each role was hidden this visit, and whose it is (so Undo can tell when an employer is still wanted skipped). */
interface Why {
  reason: NoReason;
  employer: string;
}
const reasons = new Map<string, Map<string, Why>>();
const listeners = new Set<() => void>();

function read(profileId: string): AvoidPrefs {
  const held = cache.get(profileId);
  if (held) return held;
  let prefs = EMPTY_AVOID;
  try {
    prefs = parseAvoid(JSON.parse(localStorage.getItem(avoidKey(profileId)) ?? 'null'));
  } catch {
    prefs = EMPTY_AVOID;
  }
  cache.set(profileId, prefs);
  return prefs;
}

function write(profileId: string, prefs: AvoidPrefs): void {
  cache.set(profileId, prefs);
  try {
    if (isEmptyAvoid(prefs)) localStorage.removeItem(avoidKey(profileId));
    else localStorage.setItem(avoidKey(profileId), JSON.stringify(prefs));
  } catch {
    /* storage blocked or full: the choices live for this page only */
  }
  listeners.forEach((l) => l());
}

const reasonsOf = (profileId: string) => {
  let m = reasons.get(profileId);
  if (!m) reasons.set(profileId, (m = new Map()));
  return m;
};

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** Forget what this module holds for a profile (the dev gallery starts each state clean). */
export function resetAvoid(profileId: string, to: AvoidPrefs = EMPTY_AVOID): void {
  reasons.delete(profileId);
  write(profileId, to);
}

/** Hide roles with their reasons without a component (the dev gallery opens a state with some already hidden). */
export function seedAvoid(profileId: string, entries: readonly { job: { id: string; employer: string }; reason: NoReason }[]): void {
  reasons.delete(profileId);
  const why = reasonsOf(profileId);
  let prefs = EMPTY_AVOID;
  for (const { job, reason } of entries) {
    why.set(job.id, { reason, employer: job.employer });
    prefs = {
      jobIds: [...prefs.jobIds, job.id],
      employers: reason === 'company' ? [...prefs.employers, job.employer] : prefs.employers,
      noStepUp: prefs.noStepUp || reason === 'senior',
    };
  }
  write(profileId, prefs);
}

export interface Avoid {
  prefs: AvoidPrefs;
  /** Hidden by id, for a quick lookup. */
  hidden: ReadonlySet<string>;
  /** Why a role was hidden, when it was hidden during this visit. */
  reasonOf(id: string): NoReason | undefined;
  /** Hide a role for a reason; a second call for the same role changes the reason. */
  hide(job: { id: string; employer: string }, reason: NoReason): void;
  /** Bring a role back, and take back what its reason added. */
  undo(job: { id: string; employer: string }): void;
  /** Forget every choice: hidden roles, skipped employers, "too senior". */
  reset(): void;
}

export function useAvoid(profileId: string): Avoid {
  const prefs = useSyncExternalStore(subscribe, () => read(profileId), () => EMPTY_AVOID);
  const why = reasonsOf(profileId);

  /** `p` without what this role's reason added, unless another role hidden this visit still needs it. */
  const withoutEffect = (p: AvoidPrefs, id: string): AvoidPrefs => {
    const old = why.get(id);
    if (!old) return p;
    const others = [...why].filter(([other]) => other !== id).map(([, w]) => w);
    let next = p;
    if (old.reason === 'company' && !others.some((w) => w.reason === 'company' && w.employer === old.employer)) {
      next = { ...next, employers: next.employers.filter((e) => e !== old.employer) };
    }
    if (old.reason === 'senior' && !others.some((w) => w.reason === 'senior')) next = { ...next, noStepUp: false };
    return next;
  };

  const hide: Avoid['hide'] = (job, reason) => {
    let next = withoutEffect(read(profileId), job.id);
    why.set(job.id, { reason, employer: job.employer.slice(0, EMPLOYER_MAX) });
    const jobIds = next.jobIds.includes(job.id) ? next.jobIds : [...next.jobIds, job.id].slice(-MAX_ITEMS);
    next = { ...next, jobIds };
    if (reason === 'senior') next = { ...next, noStepUp: true };
    if (reason === 'company' && !next.employers.includes(job.employer)) {
      next = { ...next, employers: [...next.employers, job.employer.slice(0, EMPLOYER_MAX)].slice(-MAX_ITEMS) };
    }
    write(profileId, next);
  };

  const undo: Avoid['undo'] = (job) => {
    const next = withoutEffect(read(profileId), job.id);
    why.delete(job.id);
    write(profileId, { ...next, jobIds: next.jobIds.filter((id) => id !== job.id) });
  };

  const reset = () => {
    why.clear();
    write(profileId, EMPTY_AVOID);
  };

  return { prefs, hidden: new Set(prefs.jobIds), reasonOf: (id) => why.get(id)?.reason, hide, undo, reset };
}
