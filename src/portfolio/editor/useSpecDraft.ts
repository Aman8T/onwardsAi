/**
 * Optimistic layer over the portfolio's spec. The editor is controlled (`portfolio` comes from the parent
 * and every change leaves through `onPatch`), but spec patches carry whole objects (`blocks`, `style`…),
 * so two quick edits made before the first response arrives would send stale copies and undo each other.
 * This hook keeps the owner's latest, not yet confirmed values on top of the server spec, composes every
 * new edit against them, and drops a value once the server spec has caught up (or after a short expiry,
 * so a rejected patch cannot leave the page showing something that was never saved).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Portfolio, PortfolioPatch, PortfolioSpec } from '../../../shared/portfolio';

export type SpecPatch = Pick<PortfolioPatch, 'style' | 'blocks' | 'graph' | 'privacy'>;

const KEYS: (keyof SpecPatch)[] = ['style', 'blocks', 'graph', 'privacy'];
const EXPIRY_MS = 8000;

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export interface SpecDraft {
  /** The spec to show: the server's with the unconfirmed edits on top. */
  spec: PortfolioSpec;
  /** The look in force (the clicked one until the server confirms it). */
  lookIndex: number;
  /** True while a look change is in flight; the other spec controls wait for it. */
  locked: boolean;
  /** Apply an edit computed from the latest spec; returns nothing to send as `{}`. */
  update(fn: (latest: PortfolioSpec) => SpecPatch): void;
  selectLook(index: number): void;
}

export function useSpecDraft(portfolio: Portfolio, onPatch: (patch: PortfolioPatch) => void): SpecDraft {
  const [pending, setPendingState] = useState<SpecPatch>({});
  const [pendingLook, setPendingLook] = useState<number | null>(null);
  const pendingRef = useRef<SpecPatch>({});
  const latest = useRef<PortfolioSpec>(portfolio.spec);
  const server = useRef(portfolio);
  const lookSeen = useRef(portfolio.lookIndex);
  const onPatchRef = useRef(onPatch);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    onPatchRef.current = onPatch;
  });

  const setPending = useCallback((next: SpecPatch) => {
    const empty = Object.keys(next).length === 0 && Object.keys(pendingRef.current).length === 0;
    pendingRef.current = next;
    if (!empty) setPendingState(next);
  }, []);

  const disarm = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const arm = useCallback(() => {
    disarm();
    timer.current = window.setTimeout(() => {
      timer.current = null;
      pendingRef.current = {};
      latest.current = server.current.spec;
      setPendingState({});
      setPendingLook(null);
    }, EXPIRY_MS);
  }, [disarm]);

  // The server spoke: keep only the edits it has not applied yet (and none after it swapped the look).
  useEffect(() => {
    server.current = portfolio;
    const lookChanged = portfolio.lookIndex !== lookSeen.current;
    lookSeen.current = portfolio.lookIndex;
    setPendingLook((p) => (p !== null && (lookChanged || p === portfolio.lookIndex) ? null : p));
    const remaining: SpecPatch = {};
    if (!lookChanged) {
      for (const key of KEYS) {
        const mine = pendingRef.current[key];
        if (mine !== undefined && !same(mine, portfolio.spec[key])) Object.assign(remaining, { [key]: mine });
      }
    }
    latest.current = { ...portfolio.spec, ...remaining };
    setPending(remaining);
    if (Object.keys(remaining).length === 0) disarm();
  }, [portfolio, setPending, disarm]);

  useEffect(() => disarm, [disarm]);

  const update = useCallback(
    (fn: (latestSpec: PortfolioSpec) => SpecPatch) => {
      const patch = fn(latest.current);
      const keys = KEYS.filter((k) => patch[k] !== undefined);
      if (keys.length === 0) return;
      const clean: SpecPatch = {};
      for (const k of keys) Object.assign(clean, { [k]: patch[k] });
      latest.current = { ...latest.current, ...clean };
      setPending({ ...pendingRef.current, ...clean });
      arm();
      onPatchRef.current(clean);
    },
    [setPending, arm],
  );

  const selectLook = useCallback(
    (index: number) => {
      if (index === (pendingLook ?? server.current.lookIndex)) return;
      latest.current = server.current.spec;
      setPending({});
      setPendingLook(index);
      arm();
      onPatchRef.current({ lookIndex: index });
    },
    [pendingLook, setPending, arm],
  );

  const spec = useMemo(() => ({ ...portfolio.spec, ...pending }), [portfolio.spec, pending]);

  return { spec, lookIndex: pendingLook ?? portfolio.lookIndex, locked: pendingLook !== null, update, selectLook };
}
