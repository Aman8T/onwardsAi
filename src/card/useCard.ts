/**
 * The share sheet's state: the owner's `CardView`, the settings as the person is changing them (the draft), and the
 * saving of one into the other.
 *
 * Saving is on change: the switch goes to the server at once, a checkbox or a menu waits 350 ms for the next tick so
 * three skills picked in a row are one request. One request is in flight at a time; a change made meanwhile is sent
 * right after it, so the server always ends on the person's last choice. When a request is refused the draft goes
 * back to what the server holds and the server's sentence is shown. Closing the sheet sends a change still waiting.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { CARD_SKILLS_MAX } from '../../shared/card';
import type { CardSettings, CardView } from '../../shared/card';
import { isAbort, sentenceOf } from './api';
import type { CardClient } from './api';

const DEBOUNCE_MS = 350;

export type CardPhase = 'loading' | 'ready' | 'failed';

export interface CardModel {
  phase: CardPhase;
  /** The server's last answer; the link fields and the areas come from here. */
  view: CardView | null;
  /** The settings as the person has them now (ahead of `view` while a save is under way). */
  draft: CardSettings | null;
  /** A save is in flight or waiting to be sent. */
  saving: boolean;
  /** A new link is being made. */
  linking: boolean;
  /** The sentence for the last refused save or new link; empty when all is well. */
  problem: string;
  /** The sentence for a card that would not load. */
  loadProblem: string;
  change(patch: Partial<CardSettings>, immediate?: boolean): void;
  toggleSkill(id: string): void;
  makeNewLink(): Promise<void>;
  reload(): void;
}

export function useCard(profileId: string, client: CardClient): CardModel {
  const [view, setView] = useState<CardView | null>(null);
  const [draft, setDraft] = useState<CardSettings | null>(null);
  const [phase, setPhase] = useState<CardPhase>('loading');
  const [loadProblem, setLoadProblem] = useState('');
  const [saving, setSaving] = useState(false);
  const [linking, setLinking] = useState(false);
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);

  const viewRef = useRef<CardView | null>(null);
  const draftRef = useRef<CardSettings | null>(null);
  const inflight = useRef(false);
  const dirty = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const dead = useRef(false);
  const live = useRef({ profileId, client });
  live.current = { profileId, client };

  const take = useCallback((next: CardView, keepDraft: boolean) => {
    viewRef.current = next;
    setView(next);
    if (!keepDraft) {
      draftRef.current = next.settings;
      setDraft(next.settings);
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
      const next = await api.put({ profileId: id, shared: d.shared, skills: d.skills, roleId: d.roleId, theme: d.theme, line: d.line });
      if (!dead.current) take(next, dirty.current);
    } catch (err) {
      if (!dead.current) {
        setProblem(sentenceOf(err));
        // back to what the server holds, so the screen never claims what is not true
        dirty.current = false;
        const held = viewRef.current;
        if (held) {
          draftRef.current = held.settings;
          setDraft(held.settings);
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

  const change = useCallback(
    (patch: Partial<CardSettings>, immediate = false) => {
      const base = draftRef.current;
      if (!base || viewRef.current?.blocked) return;
      const next: CardSettings = { ...base, ...patch };
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

  const toggleSkill = useCallback(
    (id: string) => {
      const base = draftRef.current;
      if (!base) return;
      const has = base.skills.includes(id);
      if (!has && base.skills.length >= CARD_SKILLS_MAX) return;
      change({ skills: has ? base.skills.filter((s) => s !== id) : [...base.skills, id] });
    },
    [change],
  );

  const makeNewLink = useCallback(async () => {
    if (!viewRef.current?.settings.shared) return;
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

  return { phase, view, draft, saving, linking, problem, loadProblem, change, toggleSkill, makeNewLink, reload };
}
