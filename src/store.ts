/**
 * App state: a tiny external store (useSyncExternalStore) plus the actions
 * that talk to the server. Components read with `useApp(selector)` and call
 * `actions.*` — no prop drilling, no context providers.
 */
import { useSyncExternalStore } from 'react';
import type { AnalysisEvent, ChatOrigin, Profile, ReaderState, SourceKind } from '../shared/types';
import * as api from './lib/api';

export type View = 'onboarding' | 'analysis' | 'graph' | 'chat';

export interface Signal {
  id: number;
  label: string;
  src: string;
  kind: SourceKind;
}

export interface LogLine {
  id: number;
  /** "00:07" — seconds since the analysis started. */
  t: string;
  who: string;
  text: string;
  kind?: SourceKind | 'sonnet';
}

export interface AnalysisState {
  readers: ReaderState[];
  signals: Signal[];
  logs: LogLine[];
  phase: 'idle' | 'reading' | 'synthesizing' | 'done' | 'error';
  phaseLabel: string;
  /** 0 = waiting, 1 = merging evidence, 2 = scoring domains, 3 = ranking roles. */
  synthStep: number;
  synthChars: number;
  startedAt: number;
  error?: string;
  /** True when this run uses the bundled sample persona. */
  sample: boolean;
}

export interface ChatTool {
  name: string;
  detail: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  tools: ChatTool[];
  status: 'streaming' | 'done' | 'error';
  error?: string;
}

export interface Thread {
  id: string;
  origin: ChatOrigin;
  createdAt: number;
  sessionId?: string;
  messages: ChatMessage[];
}

export interface AppState {
  view: View;
  profile: Profile | null;
  /** false = current-role graph, true = next-roles graph. */
  flipped: boolean;
  analysis: AnalysisState;
  threads: Thread[];
  activeThreadId: string | null;
}

const STORAGE_KEY = 'tayyari.v1';

const emptyAnalysis = (sample = false): AnalysisState => ({
  readers: [],
  signals: [],
  logs: [],
  phase: 'idle',
  phaseLabel: '',
  synthStep: 0,
  synthChars: 0,
  startedAt: Date.now(),
  sample,
});

function load(): AppState {
  const base: AppState = { view: 'onboarding', profile: null, flipped: false, analysis: emptyAnalysis(), threads: [], activeThreadId: null };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return base;
    const saved = JSON.parse(raw) as Partial<AppState>;
    if (!saved.profile) return base;
    const threads = (saved.threads ?? []).map((t) => ({
      ...t,
      // a reload interrupts any in-flight stream
      messages: t.messages.map((m) => (m.status === 'streaming' ? { ...m, status: 'error' as const, error: 'Interrupted by reload' } : m)),
    }));
    return { ...base, profile: saved.profile, threads, flipped: !!saved.flipped, view: 'graph', activeThreadId: saved.activeThreadId ?? null };
  } catch {
    return base;
  }
}

let state: AppState = load();
const listeners = new Set<() => void>();

function persist() {
  try {
    const { profile, threads, flipped, activeThreadId } = state;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ profile, threads, flipped, activeThreadId }));
  } catch {
    /* storage unavailable — the app still works for this session */
  }
}

let persistTimer: ReturnType<typeof setTimeout> | undefined;
function set(patch: Partial<AppState> | ((s: AppState) => Partial<AppState>)) {
  const next = typeof patch === 'function' ? patch(state) : patch;
  state = { ...state, ...next };
  listeners.forEach((l) => l());
  clearTimeout(persistTimer);
  persistTimer = setTimeout(persist, 250);
}

export function getState() {
  return state;
}

export function useApp<T>(selector: (s: AppState) => T): T {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => selector(state),
  );
}

const uid = () => Math.random().toString(36).slice(2, 10);
const stamp = (startedAt: number) => {
  const s = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

let analysisAbort: AbortController | null = null;
const chatAborts = new Map<string, AbortController>();
let seq = 0;

function applyAnalysisEvent(e: AnalysisEvent) {
  set((s) => {
    const a = s.analysis;
    switch (e.type) {
      case 'readers':
        return { analysis: { ...a, readers: e.readers, phase: 'reading', phaseLabel: 'Readers in progress' } };
      case 'reader': {
        const exists = a.readers.some((r) => r.sourceId === e.reader.sourceId);
        const readers = exists ? a.readers.map((r) => (r.sourceId === e.reader.sourceId ? e.reader : r)) : [...a.readers, e.reader];
        return { analysis: { ...a, readers } };
      }
      case 'signal':
        return { analysis: { ...a, signals: [...a.signals, { id: ++seq, label: e.label, src: e.src, kind: e.kind }].slice(-40) } };
      case 'log':
        return { analysis: { ...a, logs: [...a.logs, { id: ++seq, t: stamp(a.startedAt), who: e.who, text: e.text, kind: e.kind }].slice(-60) } };
      case 'phase':
        return { analysis: { ...a, phase: e.phase, phaseLabel: e.label } };
      case 'synth':
        return { analysis: { ...a, synthStep: Math.max(a.synthStep, e.step), synthChars: e.chars ?? a.synthChars } };
      case 'done':
        return { profile: e.profile, threads: [], activeThreadId: null, flipped: false, analysis: { ...a, phase: 'done', phaseLabel: 'Your graph is ready', synthStep: 3 } };
      case 'error':
        return { analysis: { ...a, phase: 'error', error: e.message, phaseLabel: 'Analysis failed' } };
    }
  });
}

export const actions = {
  goto(view: View) {
    set({ view });
  },

  setFlipped(flipped: boolean) {
    set({ flipped });
  },

  /** Start a live analysis. Switches to the analysis view immediately. */
  async startAnalysis(input: api.AnalyzeInput) {
    analysisAbort?.abort();
    const ctrl = new AbortController();
    analysisAbort = ctrl;
    set({ view: 'analysis', analysis: emptyAnalysis(!!input.sample) });
    try {
      await api.analyze(input, applyAnalysisEvent, ctrl.signal);
      if (state.analysis.phase !== 'done' && state.analysis.phase !== 'error' && !ctrl.signal.aborted) {
        applyAnalysisEvent({ type: 'error', message: 'The analysis stream ended unexpectedly.' });
      }
    } catch (err) {
      if (ctrl.signal.aborted) return;
      applyAnalysisEvent({ type: 'error', message: err instanceof Error ? err.message : String(err) });
    }
  },

  cancelAnalysis() {
    analysisAbort?.abort();
    analysisAbort = null;
    set({ view: state.profile ? 'graph' : 'onboarding', analysis: emptyAnalysis() });
  },

  /** Load the pre-computed sample profile (demo fallback, no AI call). */
  async loadSampleProfile() {
    const profile = await api.sampleProfile();
    set({ profile, threads: [], activeThreadId: null, flipped: false, view: 'graph' });
  },

  showGraph() {
    set({ view: 'graph' });
  },

  /** Back to onboarding to analyse a different person (keeps nothing). */
  startOver() {
    analysisAbort?.abort();
    chatAborts.forEach((c) => c.abort());
    chatAborts.clear();
    set({ view: 'onboarding', profile: null, threads: [], activeThreadId: null, flipped: false, analysis: emptyAnalysis() });
  },

  /**
   * Open the chat from a node. Re-opens an existing thread for the same node;
   * otherwise starts a new thread and sends the seed question.
   */
  openChat(origin: ChatOrigin, question: string) {
    const existing = state.threads.find(
      // caption carries the role for requirement origins, so the same skill under two roles gets two threads
      (t) =>
        t.origin.kind === origin.kind &&
        t.origin.refId === origin.refId &&
        t.origin.title === origin.title &&
        t.origin.caption === origin.caption &&
        origin.kind !== 'free',
    );
    if (existing) {
      set({ view: 'chat', activeThreadId: existing.id });
      return;
    }
    const thread: Thread = { id: uid(), origin, createdAt: Date.now(), messages: [] };
    set((s) => ({ view: 'chat', threads: [thread, ...s.threads], activeThreadId: thread.id }));
    if (question.trim()) void actions.send(thread.id, question);
  },

  selectThread(id: string) {
    set({ activeThreadId: id, view: 'chat' });
  },

  /** Send a message in a thread and stream the answer. */
  async send(threadId: string, text: string) {
    const profile = state.profile;
    const thread = state.threads.find((t) => t.id === threadId);
    if (!profile || !thread || !text.trim()) return;
    if (thread.messages.some((m) => m.status === 'streaming')) return;

    const user: ChatMessage = { id: uid(), role: 'user', text: text.trim(), tools: [], status: 'done' };
    const reply: ChatMessage = { id: uid(), role: 'assistant', text: '', tools: [], status: 'streaming' };
    const patch = (fn: (m: ChatMessage) => ChatMessage, sessionId?: string) =>
      set((s) => ({
        threads: s.threads.map((t) =>
          t.id === threadId ? { ...t, sessionId: sessionId ?? t.sessionId, messages: t.messages.map((m) => (m.id === reply.id ? fn(m) : m)) } : t,
        ),
      }));
    set((s) => ({ threads: s.threads.map((t) => (t.id === threadId ? { ...t, messages: [...t.messages, user, reply] } : t)) }));

    const ctrl = new AbortController();
    chatAborts.set(threadId, ctrl);
    try {
      await api.chat(
        { profileId: profile.id, message: user.text, origin: thread.origin, sessionId: thread.sessionId },
        (e) => {
          if (e.type === 'delta') patch((m) => ({ ...m, text: m.text + e.text }));
          else if (e.type === 'tool') patch((m) => ({ ...m, tools: [...m.tools, { name: e.name, detail: e.detail }] }));
          else if (e.type === 'done') patch((m) => ({ ...m, status: 'done' }), e.sessionId);
          else if (e.type === 'error') patch((m) => ({ ...m, status: 'error', error: e.message }));
        },
        ctrl.signal,
      );
      patch((m) => (m.status === 'streaming' ? { ...m, status: 'done' } : m));
    } catch (err) {
      if (ctrl.signal.aborted) patch((m) => ({ ...m, status: 'done' }));
      else patch((m) => ({ ...m, status: 'error', error: err instanceof Error ? err.message : String(err) }));
    } finally {
      chatAborts.delete(threadId);
    }
  },

  stop(threadId: string) {
    chatAborts.get(threadId)?.abort();
  },
};
