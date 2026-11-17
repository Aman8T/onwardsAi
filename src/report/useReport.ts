/**
 * The owner sheet's state: the server's `ReportView`, what the person is choosing now (the draft: sharing on or off and
 * the choices), and the saving of one into the other. Modelled on the radar card's `useCard`.
 *
 * Saving is on change: "Make a link" and "Stop sharing" go to the server at once; a checkbox or a quote's switch waits
 * 350 ms for the next one, so three changes in a row are one request. One request is in flight at a time; a change made
 * meanwhile is sent right after it, so the server always ends on the person's last choice. Each answer replaces the
 * view (and with it the preview, which is the server's `public`, never a guess made here). When a request is refused the
 * draft goes back to what the server holds and the server's sentence is shown. Closing the sheet sends a change still
 * waiting.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { REPORT_HIDDEN_MAX } from '../../shared/report';
import type { ReportChoices, ReportView } from '../../shared/report';
import { isAbort, sentenceOf } from './api';
import type { ReportClient } from './api';

const DEBOUNCE_MS = 350;

export type ReportPhase = 'loading' | 'ready' | 'failed';

export interface ReportDraft {
  shared: boolean;
  choices: ReportChoices;
}

/** Sharing is on while the server holds a link; the view has no flag of its own. */
export const isShared = (view: ReportView): boolean => !!view.slug && !!view.pageUrl;

const draftOf = (view: ReportView): ReportDraft => ({ shared: isShared(view), choices: view.choices });

export interface ReportModel {
  phase: ReportPhase;
  /** The server's last answer: the link, `offered`, `public` and `orgsFound` come from here. */
  view: ReportView | null;
  /** What the person has chosen now (ahead of `view` while a save is under way). */
  draft: ReportDraft | null;
  /** A save is in flight or waiting to be sent. */
  saving: boolean;
  /** A new link is being made. */
  linking: boolean;
  /** The sentence for the last refused save or new link; empty when all is well. */
  problem: string;
  /** The sentence for a report that would not load. */
  loadProblem: string;
  change(patch: Partial<ReportChoices>): void;
  setShared(on: boolean): void;
  /** Hide a quote, or show it again. Never edits its text. */
  toggleQuote(id: string): void;
  makeNewLink(): Promise<void>;
  reload(): void;
}

export function useReport(profileId: string, client: ReportClient): ReportModel {
  const [view, setView] = useState<ReportView | null>(null);
  const [draft, setDraft] = useState<ReportDraft | null>(null);
  const [phase, setPhase] = useState<ReportPhase>('loading');
  const [loadProblem, setLoadProblem] = useState('');
  const [saving, setSaving] = useState(false);
  const [linking, setLinking] = useState(false);
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);

  const viewRef = useRef<ReportView | null>(null);
  const draftRef = useRef<ReportDraft | null>(null);
  const inflight = useRef(false);
  const dirty = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const dead = useRef(false);
  const live = useRef({ profileId, client });
  live.current = { profileId, client };

  const take = useCallback((next: ReportView, keepDraft: boolean) => {
    viewRef.current = next;
    setView(next);
    if (!keepDraft) {
      const d = draftOf(next);
      draftRef.current = d;
      setDraft(d);
    }
  }, []);

  const flush = useCallback(async (): Promise<void> => {
    if (inflight.current) {
      dirty.current = true;
      return;
    }
    const d = draftRef.current;
    if (!d) return;
    const { profileId: id, client: api } = live.current;
    inflight.current = true;
    if (!dead.current) {
      setSaving(true);
      setProblem('');
    }
    try {
      const next = await api.put({ profileId: id, shared: d.shared, choices: d.choices });
      if (!dead.current) take(next, dirty.current);
    } catch (err) {
      if (!dead.current) {
        setProblem(sentenceOf(err));
        // back to what the server holds, so the screen never claims what is not true
        dirty.current = false;
        const held = viewRef.current;
        if (held) {
          const back = draftOf(held);
          draftRef.current = back;
          setDraft(back);
        }
      }
    } finally {
      inflight.current = false;
      if (dirty.current && !dead.current) {
        dirty.current = false;
        void flush();
      } else {
        dirty.current = false;
        if (!dead.current) setSaving(false);
      }
    }
  }, [take]);

  useEffect(() => {
    dead.current = false;
    const ctrl = new AbortController();
    setPhase('loading');
    setLoadProblem('');
    setProblem('');
    client
      .get(profileId, ctrl.signal)
      .then((next) => {
        if (ctrl.signal.aborted) return;
        take(next, false);
        setPhase('ready');
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted || isAbort(err)) return;
        setLoadProblem(sentenceOf(err));
        setPhase('failed');
      });
    return () => {
      ctrl.abort();
      if (timer.current !== undefined) {
        // a change still waiting goes out; there is nobody left to tell about the answer
        window.clearTimeout(timer.current);
        timer.current = undefined;
        void flush();
      }
      dead.current = true;
    };
  }, [profileId, client, attempt, take, flush]);

  const queue = useCallback(
    (next: ReportDraft, immediate: boolean) => {
      draftRef.current = next;
      setDraft(next);
      setSaving(true);
      if (timer.current !== undefined) window.clearTimeout(timer.current);
      if (immediate) {
        timer.current = undefined;
        void flush();
      } else {
        timer.current = window.setTimeout(() => {
          timer.current = undefined;
          void flush();
        }, DEBOUNCE_MS);
      }
    },
    [flush],
  );

  const change = useCallback(
    (patch: Partial<ReportChoices>) => {
      const base = draftRef.current;
      if (!base || viewRef.current?.blocked) return;
      queue({ ...base, choices: { ...base.choices, ...patch } }, false);
    },
    [queue],
  );

  const setShared = useCallback(
    (on: boolean) => {
      const base = draftRef.current;
      if (!base || viewRef.current?.blocked) return;
      queue({ ...base, shared: on }, true);
    },
    [queue],
  );

  const toggleQuote = useCallback(
    (id: string) => {
      const base = draftRef.current;
      if (!base) return;
      const hidden = base.choices.hiddenQuotes;
      const has = hidden.includes(id);
      if (!has && hidden.length >= REPORT_HIDDEN_MAX) return;
      change({ hiddenQuotes: has ? hidden.filter((q) => q !== id) : [...hidden, id] });
    },
    [change],
  );

  const makeNewLink = useCallback(async () => {
    const held = viewRef.current;
    if (!held || !isShared(held)) return;
    setLinking(true);
    setProblem('');
    try {
      const next = await live.current.client.newLink({ profileId: live.current.profileId });
      if (!dead.current) take(next, inflight.current || timer.current !== undefined);
    } catch (err) {
      if (!dead.current) setProblem(sentenceOf(err));
    } finally {
      if (!dead.current) setLinking(false);
    }
  }, [take]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  return { phase, view, draft, saving, linking, problem, loadProblem, change, setShared, toggleQuote, makeNewLink, reload };
}
