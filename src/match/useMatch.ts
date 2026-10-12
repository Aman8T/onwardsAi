/**
 * Match state, in one module-level store (the same shape as `auth/useMe`): `useMatch()` reads it, `matchActions`
 * change it. Streams are started from event handlers and from idempotent `ensure*` calls, never from a bare effect,
 * so React's double-mounting in development cannot start a second match (which would use a second posting).
 *
 *   idle ──submit──▶ reading ──posting · gates · coverage · radar · questions──▶ ──done──▶ done
 *                       └──error──▶ idle (with a message, or the pricing panel, or the sign-in sheet)
 *
 * After `done` the stored match is read back (`GET /api/match/{id}`) so the screen always shows the server's copy:
 * the posting, the facts the rows cite, and the band, counts and confidence code computed.
 *
 * Errors are mapped once, here:
 *   401 → the sign-in sheet opens and the request is kept to resume afterwards (see pending.ts)
 *   402 → `paywall`: the calm "you've used your free posting" panel with a link to /pricing
 *   422 → the paste screen with the server's sentence ("We couldn't find any requirements…")
 *   409 on a download → the open decisions are re-read from the server and shown
 *   anything else → a sentence on the screen that asked.
 */
import { useSyncExternalStore } from 'react';
import type { Coverage, CoverNote, DecisionChoice, GateAnswer, MatchResult, Posting, Question, Radar, ResumeVariant } from '../../shared/match';
import { refresh as refreshMe, signInSheet } from '../auth/useMe';
import { plural } from '../lib/ui';
import { actions, getState } from '../store';
import * as api from './api';
import { MatchApiError } from './api';
import { changedRows } from './model';
import { rememberPending } from './pending';
import type { RecheckState } from './Questions';
import type { AnswerInput, MatchDetail, MatchEvent, MatchFact, MatchSummary, SubmitInput } from './types';

export type Phase = 'idle' | 'reading' | 'done';
export type Load = 'idle' | 'loading' | 'generating' | 'ready' | 'error';

export interface ResumeState {
  matchId?: string;
  status: Load;
  variant?: ResumeVariant;
  /** Bullet ids with a decision on its way to the server. */
  saving: ReadonlySet<string>;
  downloading?: 'docx' | 'pdf';
  /** A failed load (no variant) or a transient notice (a variant is on screen). */
  error?: string;
}

export interface CoverState {
  matchId?: string;
  status: Load;
  note?: CoverNote;
  downloading?: boolean;
  error?: string;
}

export interface MatchState {
  phase: Phase;
  matchId?: string;
  posting?: Posting;
  facts: MatchFact[];
  gates?: GateAnswer[];
  blocked: boolean;
  coverage: Coverage[];
  radar?: Radar;
  questions: Question[];
  result?: MatchResult;
  createdAt?: string;
  /** True when this match arrived live in this session (the radar draws itself once). */
  live: boolean;
  /** What is being done right now, in a calm sentence. */
  step?: string;
  /** A sentence for the paste screen after a failed or refused request. */
  notice?: string;
  /** The "used your free posting" panel. */
  paywall: boolean;
  /** Set while `GET /api/match/{id}` is in flight, and when it failed. */
  loadingId?: string;
  loadError?: { id: string; message: string };
  draft: { text: string; url: string };
  recheck: RecheckState;
  resume: ResumeState;
  cover: CoverState;
  recent: MatchSummary[] | null;
}

/* ───────────────────────── store plumbing ───────────────────────── */

const DRAFT_KEY = 'tayyari.match.draft';

function readDraft(): MatchState['draft'] {
  try {
    const raw = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null');
    return { text: typeof raw?.text === 'string' ? raw.text : '', url: typeof raw?.url === 'string' ? raw.url : '' };
  } catch {
    return { text: '', url: '' };
  }
}

let draftTimer: ReturnType<typeof setTimeout> | undefined;
function writeDraft(draft: MatchState['draft']) {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    try {
      if (draft.text || draft.url) localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
      else localStorage.removeItem(DRAFT_KEY);
    } catch {
      /* storage blocked: the draft lives for this page only */
    }
  }, 300);
}

const idleResume = (): ResumeState => ({ status: 'idle', saving: new Set() });
const idleCover = (): CoverState => ({ status: 'idle' });
const idleRecheck = (): RecheckState => ({ status: 'idle' });

/** What a run starts with: nothing read yet. */
const freshRun = (): Partial<MatchState> => ({
  matchId: undefined,
  posting: undefined,
  facts: [],
  gates: undefined,
  blocked: false,
  coverage: [],
  radar: undefined,
  questions: [],
  result: undefined,
  createdAt: undefined,
  live: false,
  loadError: undefined,
  recheck: idleRecheck(),
  resume: idleResume(),
  cover: idleCover(),
});

let state: MatchState = {
  phase: 'idle',
  facts: [],
  blocked: false,
  coverage: [],
  questions: [],
  live: false,
  paywall: false,
  draft: readDraft(),
  recheck: idleRecheck(),
  resume: idleResume(),
  cover: idleCover(),
  recent: null,
};
const listeners = new Set<() => void>();

function set(patch: Partial<MatchState> | ((s: MatchState) => Partial<MatchState>)) {
  state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
  listeners.forEach((l) => l());
}
const setResume = (patch: Partial<ResumeState>) => set((s) => ({ resume: { ...s.resume, ...patch } }));
const setCover = (patch: Partial<CoverState>) => set((s) => ({ cover: { ...s.cover, ...patch } }));

export function useMatch(): MatchState {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => state,
  );
}

/* ───────────────────────── errors ───────────────────────── */

const messageOf = (err: unknown) => (err instanceof Error ? err.message : 'Something went wrong. Please try again.');
const statusOf = (err: unknown) => (err instanceof MatchApiError ? err.status : 0);

const SIGN_IN_NOTICE = 'Sign in to match a posting. Your first posting is free.';
/**
 * Ask the person to sign in. The server just said they are not (a session that ended, say), so the account state is
 * read again first: otherwise the sheet would see a user it still believes in and close itself at once.
 */
const openSignIn = (notice = SIGN_IN_NOTICE) => {
  void refreshMe().finally(() => signInSheet.open('account', notice));
};

/* ───────────────────────── the run ───────────────────────── */

let runAbort: AbortController | null = null;
let loading: string | null = null;
/** Decisions are sent one at a time, so their answers can never arrive out of order. */
let decisionQueue: Promise<void> = Promise.resolve();

function applyDetail(d: MatchDetail): Partial<MatchState> {
  return {
    matchId: d.id,
    posting: d.posting,
    facts: d.facts,
    result: d.result,
    coverage: d.result.coverage,
    gates: d.result.gates,
    blocked: d.result.blocked,
    radar: d.result.radar,
    questions: d.result.questions,
    createdAt: d.createdAt ?? state.createdAt,
  };
}

function stepFor(s: MatchState): string {
  if (!s.posting) return 'Reading the posting on its own…';
  const n = s.posting.requirements.length;
  return s.coverage.length === 0
    ? `Checking ${plural(n, 'requirement')} against what you have shown…`
    : `Checked ${s.coverage.length} of ${n} requirements…`;
}

function apply(e: MatchEvent): void {
  switch (e.type) {
    case 'posting':
      set((s) => ({
        posting: e.posting,
        facts: e.facts && e.facts.length > 0 ? e.facts : s.facts,
        step: stepFor({ ...s, posting: e.posting }),
      }));
      break;
    case 'gates':
      set({ gates: e.gates, blocked: e.blocked });
      break;
    case 'coverage':
      set((s) => {
        const have = new Map(s.coverage.map((c) => [c.requirementId, c]));
        for (const c of e.coverage) have.set(c.requirementId, c);
        const coverage = [...have.values()];
        return { coverage, step: stepFor({ ...s, coverage }) };
      });
      break;
    case 'radar':
      set({ radar: e.radar });
      break;
    case 'questions':
      set({ questions: e.questions });
      break;
    default:
      break;
  }
}

/** The stream said `done`: read the stored match back and move to its address. */
async function finish(done: Extract<MatchEvent, { type: 'done' }>): Promise<void> {
  set({ matchId: done.matchId, step: 'Putting the result together…' });
  let detail: MatchDetail | null = null;
  try {
    detail = await api.getMatch(done.matchId);
  } catch (err) {
    // fall back to what streamed, if the stream carried the result itself
    if (done.result && state.posting) {
      detail = { id: done.matchId, createdAt: done.createdAt, posting: state.posting, facts: state.facts, result: done.result };
    } else {
      set({ phase: 'idle', notice: statusOf(err) === 401 ? SIGN_IN_NOTICE : messageOf(err) });
      return;
    }
  }
  set({ ...applyDetail(detail), phase: 'done', live: true, step: undefined, notice: undefined, paywall: false, draft: { text: '', url: '' }, recent: null });
  writeDraft({ text: '', url: '' });
  void refreshMe(); // the posting used the free allowance or a credit: the chip in the header should say so
  if (getState().view === 'match' && getState().matchRoute.screen === 'paste') actions.openMatch({ screen: 'match', id: done.matchId }, { replace: true });
}

function failRun(err: unknown, request: SubmitInput) {
  const status = statusOf(err);
  if (status === 401) {
    rememberPending({ text: request.text, url: request.url });
    set({ phase: 'idle', notice: undefined });
    openSignIn();
    return;
  }
  if (status === 402) {
    set({ phase: 'idle', paywall: true, notice: undefined });
    return;
  }
  set({ phase: 'idle', notice: messageOf(err) });
}

export const matchActions = {
  setDraft(patch: Partial<MatchState['draft']>) {
    const draft = { ...state.draft, ...patch };
    set({ draft });
    writeDraft(draft);
  },

  /** Back to an empty paste screen. */
  clearNotice() {
    set({ notice: undefined, paywall: false });
  },

  /**
   * Start a match. `signedIn: false` skips the request: the sign-in sheet opens and the posting is kept to resume
   * (`null` = unknown, try the server and let a 401 decide).
   */
  async submit(input: SubmitInput, opts: { signedIn: boolean | null }): Promise<void> {
    if (state.phase === 'reading') return;
    const text = input.text?.trim();
    const url = input.url?.trim();
    if (!text && !url) return;
    const request: SubmitInput = text ? { profileId: input.profileId, text } : { profileId: input.profileId, url };

    if (opts.signedIn === false) {
      rememberPending({ text, url });
      set({ notice: undefined, paywall: false });
      openSignIn();
      return;
    }

    runAbort?.abort();
    const ctrl = new AbortController();
    runAbort = ctrl;
    set({ ...freshRun(), phase: 'reading', step: 'Reading the posting on its own…', notice: undefined, paywall: false });

    let ended = false;
    try {
      await api.streamMatch(
        request,
        (e) => {
          if (ctrl.signal.aborted) return;
          if (e.type === 'done') {
            ended = true;
            void finish(e);
          } else if (e.type === 'error') {
            ended = true;
            failRun(api.eventError(e.message, e.code), request);
          } else apply(e);
        },
        ctrl.signal,
      );
      if (!ended && !ctrl.signal.aborted) failRun(new Error('The match stream ended unexpectedly. Nothing was lost: your posting is still here.'), request);
    } catch (err) {
      if (!ctrl.signal.aborted) failRun(err, request);
    }
  },

  /** Read a stored match (the address `/match/:id`). A match already on screen is not read again. */
  async load(id: string): Promise<void> {
    if (state.matchId === id && (state.result || state.phase === 'reading')) return;
    if (loading === id) return;
    loading = id;
    set({ loadingId: id, loadError: undefined });
    try {
      const detail = await api.getMatch(id);
      // a resume or cover already being fetched for this very match (the screens ask for both at once) is kept
      set((s) => ({
        ...freshRun(),
        ...applyDetail(detail),
        resume: s.resume.matchId === id ? s.resume : idleResume(),
        cover: s.cover.matchId === id ? s.cover : idleCover(),
        phase: 'done',
        loadingId: undefined,
      }));
    } catch (err) {
      const status = statusOf(err);
      if (status === 401) openSignIn('Sign in to open your matches.');
      set({
        loadingId: undefined,
        loadError: {
          id,
          message: status === 401 ? 'Sign in to open this match.' : status === 404 ? "We couldn't find that match. It may belong to another account." : messageOf(err),
        },
      });
    } finally {
      loading = null;
    }
  },

  /** Save the answers to the questions; the server re-checks the rows they touch. Never uses a posting. */
  async answer(id: string, answers: AnswerInput[]): Promise<void> {
    if (state.recheck.status === 'saving' || answers.length === 0) return;
    const before = state.result?.coverage ?? [];
    set({ recheck: { status: 'saving' } });
    try {
      await api.postAnswers(id, answers);
      const detail = await api.getMatch(id);
      const changed = changedRows(before, detail.result.coverage);
      set({
        ...applyDetail(detail),
        // the resume and cover were built before these answers: read them again when next opened
        resume: idleResume(),
        cover: idleCover(),
        recheck: {
          status: 'done',
          message: changed === 0 ? 'Saved and re-checked. No status changed.' : `Saved and re-checked. ${plural(changed, 'requirement')} changed status.`,
        },
      });
    } catch (err) {
      if (statusOf(err) === 401) openSignIn('Sign in again to save your answers.');
      set({ recheck: { status: 'error', message: statusOf(err) === 401 ? 'Sign in again to save your answers. What you typed is still here.' : messageOf(err) } });
    }
  },

  /** The resume for a match: the stored one, or built now. Safe to call from an effect; it runs once per match. */
  async ensureResume(id: string, retry = false): Promise<void> {
    const r = state.resume;
    if (r.matchId === id && r.status !== 'idle' && !(retry && r.status === 'error')) return;
    set({ resume: { ...idleResume(), matchId: id, status: 'loading' } });
    const live = () => state.resume.matchId === id;
    try {
      let variant = await api.getResume(id);
      if (!live()) return;
      if (!variant) {
        setResume({ status: 'generating' });
        await api.streamResume(id, (e) => {
          if (!live()) return;
          if (e.type === 'error') throw api.eventError(e.message, e.code);
          if (e.variant) {
            variant = e.variant;
            setResume({ variant: e.variant });
          }
        });
        if (!variant) throw new Error('The resume stream ended unexpectedly. Please try again.');
      }
      if (live()) setResume({ status: 'ready', variant, error: undefined });
    } catch (err) {
      if (!live()) return;
      if (statusOf(err) === 401) openSignIn('Sign in to build your resume.');
      setResume({ status: state.resume.variant ? 'ready' : 'error', error: messageOf(err) });
    }
  },

  /** One decision, sent in order behind any earlier ones; the server re-verifies and returns the variant. */
  decide(id: string, choice: DecisionChoice): Promise<void> {
    setResume({ saving: new Set([...state.resume.saving, choice.bulletId]), error: undefined });
    decisionQueue = decisionQueue.then(async () => {
      try {
        const variant = await api.patchResume(id, [choice]);
        if (state.resume.matchId === id) setResume({ variant });
      } catch (err) {
        if (statusOf(err) === 401) openSignIn('Sign in again to save your decision.');
        if (state.resume.matchId === id) setResume({ error: messageOf(err) });
      } finally {
        const saving = new Set(state.resume.saving);
        saving.delete(choice.bulletId);
        if (state.resume.matchId === id) setResume({ saving });
      }
    });
    return decisionQueue;
  },

  /** Download the resume. A 409 (decisions still open) re-reads the variant so the open decisions show. */
  async downloadResume(id: string, kind: 'docx' | 'pdf'): Promise<void> {
    if (state.resume.downloading) return;
    setResume({ downloading: kind, error: undefined });
    const res = await api.downloadFile(api.resumeFileUrl(id, kind), `resume.${kind}`);
    if (res.ok) {
      // the export attaches its checks to the stored resume: read them back
      const variant = await api.getResume(id).catch(() => null);
      setResume({ downloading: undefined, ...(variant ? { variant } : {}) });
      return;
    }
    if (res.status === 409) {
      const variant = await api.getResume(id).catch(() => null);
      setResume({ downloading: undefined, ...(variant ? { variant } : {}), error: 'A few lines still need a decision before the files can be made. They are listed on the right.' });
      return;
    }
    if (res.status === 401) openSignIn('Sign in again to download.');
    setResume({ downloading: undefined, error: res.message });
  },

  async ensureCover(id: string, retry = false): Promise<void> {
    const c = state.cover;
    if (c.matchId === id && c.status !== 'idle' && !(retry && c.status === 'error')) return;
    set({ cover: { ...idleCover(), matchId: id, status: 'loading' } });
    const live = () => state.cover.matchId === id;
    try {
      let note = await api.getCover(id);
      if (!live()) return;
      if (!note) {
        setCover({ status: 'generating' });
        note = await api.postCover(id);
      }
      if (live()) setCover({ status: 'ready', note, error: undefined });
    } catch (err) {
      if (!live()) return;
      if (statusOf(err) === 401) openSignIn('Sign in to write your cover note.');
      setCover({ status: 'error', error: messageOf(err) });
    }
  },

  async downloadCover(id: string): Promise<void> {
    if (state.cover.downloading) return;
    setCover({ downloading: true, error: undefined });
    const res = await api.downloadFile(api.coverFileUrl(id), 'cover-note.pdf');
    if (!res.ok && res.status === 401) openSignIn('Sign in again to download.');
    setCover({ downloading: false, ...(res.ok ? {} : { status: state.cover.note ? 'ready' : 'error', error: res.message }) });
  },

  /** The person's recent matches for the paste screen's side list. Signed-out visitors simply have none. */
  async loadRecent(): Promise<void> {
    if (state.recent !== null) return;
    set({ recent: [] });
    try {
      const recent = await api.listMatches();
      set({ recent: recent.slice(0, 5) });
    } catch {
      /* not signed in, or the list is unavailable: the side list stays empty */
    }
  },
};
