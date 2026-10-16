/**
 * The data behind the screen: one request per lens, the previous answer kept on screen while the next one loads
 * (the list dims, it does not jump), and, while the employers' boards are being read for the very first time,
 * one more request about 20 seconds later.
 *
 *   loading ──answer──▶ ready        loading ──failure──▶ error (the last answer, if any, stays in `data`)
 *
 * Requests are cancelled when the lens changes again, so a slow answer can never overwrite a newer one.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { DiscoverCity, DiscoverResponse } from '../../shared/discover';
import { getCities, getDiscover } from './api';
import { isFirstRefresh } from './model';
import { apiParams } from './route';
import type { DiscoverQuery } from './route';

/** How long to wait before asking again while the boards are read for the first time. */
export const FIRST_REFRESH_POLL_MS = 20_000;

export interface DiscoverState {
  status: 'loading' | 'ready' | 'error';
  data: DiscoverResponse | null;
  error?: string;
  /** The one poll has been made and the boards are still being read. */
  waited: boolean;
  reload: () => void;
}

const messageOf = (err: unknown) => (err instanceof Error ? err.message : 'Discover is having trouble right now. Please try again in a moment.');

export function useDiscover(profileId: string | undefined, query: DiscoverQuery, enabled = true): DiscoverState {
  const key = profileId ? apiParams(profileId, query).toString() : '';
  const [state, setState] = useState<Omit<DiscoverState, 'reload' | 'waited'>>({ status: 'loading', data: null });
  const [nonce, setNonce] = useState(0);
  const [polledKey, setPolledKey] = useState<string | null>(null);
  const latest = useRef({ profileId, query });
  latest.current = { profileId, query };

  useEffect(() => {
    if (!enabled || !key) return;
    const ctrl = new AbortController();
    setState((s) => ({ status: 'loading', data: s.data }));
    getDiscover(latest.current.profileId!, latest.current.query, ctrl.signal)
      .then((data) => {
        if (!ctrl.signal.aborted) setState({ status: 'ready', data });
      })
      .catch((err) => {
        if (!ctrl.signal.aborted) setState((s) => ({ status: 'error', data: s.data, error: messageOf(err) }));
      });
    return () => ctrl.abort();
  }, [key, nonce, enabled]);

  // the boards are being read for the first time: look again once, after about 20 s
  const first = state.status === 'ready' && state.data !== null && isFirstRefresh(state.data);
  useEffect(() => {
    if (!first || polledKey === key) return;
    const t = window.setTimeout(() => {
      setPolledKey(key);
      setNonce((n) => n + 1);
    }, FIRST_REFRESH_POLL_MS);
    return () => window.clearTimeout(t);
  }, [first, key, polledKey]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, waited: first && polledKey === key, reload };
}

/** The cities and localities we know, for the pickers. Empty until they arrive (or if the list is unavailable). */
export function useCities(): DiscoverCity[] {
  const [cities, setCities] = useState<DiscoverCity[]>([]);
  useEffect(() => {
    let live = true;
    getCities()
      .then((c) => live && setCities(c))
      .catch(() => {
        /* the pickers say the list is unavailable; the screen still works */
      });
    return () => {
      live = false;
    };
  }, []);
  return cities;
}
