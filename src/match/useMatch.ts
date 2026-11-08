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
 *   401 → the sign-in sheet opens and the request is kept to resume afterwards (see pending.ts), but only where the
 *         server has a sign-in to offer. When it matches without an account (`anonymousMatch` on `/api/me`) nobody is
 *         ever asked to sign in: a 401 that still arrives is a sentence ("This server needs an account…"). Where it
 *         has no way to sign in at all, the sheet is not reachable from here either (see `MatchAccess`).
 *   402 → `paywall`: the calm "you've used your free posting" panel with a link to /pricing
 *   422 → the paste screen with the server's sentence ("We couldn't find any requirements…")
 *   409 on a download → the open decisions are re-read from the server and shown
 *   anything else → a sentence on the screen that asked.
 */
import { useSyncExternalStore } from 'react';
import type { Coverage, CoverNote, DecisionChoice, GateAnswer, MatchResult, Posting, Question, Radar, ResumeVariant } from '../../shared/match';
import { matchAccess, refresh as refreshMe, signInSheet } from '../auth/useMe';
import { plural } from '../lib/ui';
import { actions, getState } from '../store';
import * as api from './api';
import { MatchApiError } from './api';
import { changedRows } from './model';
import { rememberPending } from './pending';
import type { RecheckState } from './Questions';
import { forgetRecent, readRecent, rememberRecent } from './recent';
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
let unsavedDraft: MatchState['draft'] | null = null;
function storeDraft(draft: MatchState['draft']) {
  try {
    if (draft.text || draft.url) localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    else localStorage.removeItem(DRAFT_KEY);
  } catch {
    /* storage blocked: the draft lives for this page only */
  }
}
function writeDraft(draft: MatchState['draft']) {
  clearTimeout(draftTimer);
  unsavedDraft = draft;
  draftTimer = setTimeout(() => {
    unsavedDraft = null;
    storeDraft(draft);
  }, 300);
}
// Leaving for the sign-in page within the debounce must not lose the pasted posting: the draft is read back on return.
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => {
    if (unsavedDraft) storeDraft(unsavedDraft);
    unsavedDraft = null;
  });
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

/** The match state as it is right now, outside React (a caller that just started a match reads whether it did). */
export function getMatchState(): MatchState {
  return state;
}

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

const SIGN_IN_NOTICE = 'Sign in to match a posting. One posting a month is free.';
const NEEDS_ACCOUNT = 'This server needs an account for matching.';
const NEEDS_ACCOUNT_NO_SIGN_IN = 'This server needs an account for matching, and signing in is not set up on it yet.';
/** "This server needs an account to open this match." */
const accountTo = (what: string) => `This server needs an account to ${what}.`;

/**
 * Whether a sign-in sheet can be offered from the match flow: not when the server matches without an account
 * (nobody is asked to sign in), and not when it has no way to sign in (the sheet would have nothing to show).
 * Until `/api/me` has answered the sheet is assumed possible, as it always was.
 */
const sheetOffered = () => {
  const access = matchAccess();
  return access !== 'anonymous' && access !== 'stuck';
};

/** Why a match was refused for want of an account, when there is no sheet to ask with. */
const noAccountNotice = () => (matchAccess() === 'stuck' ? NEEDS_ACCOUNT_NO_SIGN_IN : NEEDS_ACCOUNT);

/**
 * Ask the person to sign in, where the server has a sign-in to offer. The server just said they are not signed in (a
 * session that ended, say), so the account state is read again first: otherwise the sheet would see a user it still
 * believes in and close itself at once, and a server that has since started to match without an account (or lost
 * its sign-in) is noticed. Resolves false, opening nothing, when the sheet is not offered; the caller then says why.
 */
async function askToSignIn(notice = SIGN_IN_NOTICE): Promise<boolean> {
  await refreshMe();
  const access = matchAccess();
  if (access !== 'sign-in' && access !== 'unknown') return false;
  signInSheet.open('account', notice);
  return true;
}

/**
 * A request was refused with `status`. On a 401 the sign-in sheet opens (`sheetNotice` above its form) where the
 * server can offer one and `message` is returned as it was; where it cannot, nothing opens and `plain` is returned.
 * Any other status returns `message` untouched.
 */
function refused(status: number, message: string, sheetNotice: string, plain: string): string {
  if (status !== 401) return message;
  if (!sheetOffered()) return plain;
  void askToSignIn(sheetNotice);
  return message;
}

/* ───────────────────────── the run ───────────────────────── */

let runAbort: AbortController | null = null;
let loading: string | null = null;
/** Whose side list `state.recent` holds ('' = nobody signed in). */
let recentFor: string | null = null;
/** Matches shown in the paste screen's side list. */
const RECENT_SHOWN = 5;
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
      set({ phase: 'idle', notice: statusOf(err) === 401 ? (sheetOffered() ? SIGN_IN_NOTICE : noAccountNotice()) : messageOf(err) });
      return;
    }
  }
  set({ ...applyDetail(detail), phase: 'done', live: true, step: undefined, notice: undefined, paywall: false, draft: { text: '', url: '' }, recent: null });
  writeDraft({ text: '', url: '' });
  // nobody signed in: the server's list will not hold this match, so this device keeps a line for it
  if (matchAccess() !== 'account') {
    rememberRecent({
      id: detail.id,
      title: detail.posting.title || 'Posting',
      company: detail.posting.company,
      band: detail.result.band,
      createdAt: detail.createdAt ?? new Date().toISOString(),
    });
  }
  void refreshMe(); // the posting used the free allowance or a credit: the chip in the header should say so
  if (getState().view === 'match' && getState().matchRoute.screen === 'paste') actions.openMatch({ screen: 'match', id: done.matchId }, { replace: true });
}

/**
 * A match needs an account: open the sign-in sheet and keep the posting for after, or, where the server offers no
 * sheet, say why on the paste screen (the posting stays in the box either way). `otherwise` is shown when the sheet
 * was not opened for a reason that is not about accounts (the person turned out to be signed in).
 */
function needAccount(request: Pick<SubmitInput, 'text' | 'url'>, otherwise: string) {
  void askToSignIn().then((opened) => {
    if (opened) rememberPending({ text: request.text, url: request.url });
    else if (state.phase === 'idle') set({ notice: matchAccess() === 'account' ? otherwise : noAccountNotice() });
  });
}

function failRun(err: unknown, request: SubmitInput) {
  const status = statusOf(err);
  if (status === 401) {
    set({ phase: 'idle', notice: undefined });
    needAccount(request, messageOf(err));
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
   * Start a match. `signedIn: false` skips the request when the server needs an account: the sign-in sheet opens and
   * the posting is kept to resume (or, with no sheet to offer, a sentence says why). When the server matches without
   * an account the request goes straight out, signed in or not. `signedIn: null` = unknown, try the server and let a
   * 401 decide.
   */
  async submit(input: SubmitInput, opts: { signedIn: boolean | null }): Promise<void> {
    if (state.phase === 'reading') return;
    const text = input.text?.trim();
    const url = input.url?.trim();
    if (!text && !url) return;
    const request: SubmitInput = text ? { profileId: input.profileId, text } : { profileId: input.profileId, url };

    if (opts.signedIn === false && matchAccess() !== 'anonymous') {
      set({ notice: undefined, paywall: false });
      if (matchAccess() === 'stuck') set({ notice: NEEDS_ACCOUNT_NO_SIGN_IN });
      else needAccount({ text, url }, SIGN_IN_NOTICE);
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
      // the server no longer has it: it leaves this device's list of recent matches too
      if (status === 404) {
        forgetRecent(id);
        set((s) => (s.recent ? { recent: s.recent.filter((m) => m.id !== id) } : {}));
      }
      const message = refused(
        status,
        status === 401
          ? 'Sign in to open this match.'
          : status === 404
            ? `We couldn't find that match.${matchAccess() === 'anonymous' ? '' : ' It may belong to another account.'}`
            : messageOf(err),
        'Sign in to open your matches.',
        accountTo('open this match'),
      );
      set({ loadingId: undefined, loadError: { id, message } });
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
      const status = statusOf(err);
      const message = refused(
        status,
        status === 401 ? 'Sign in again to save your answers. What you typed is still here.' : messageOf(err),
        'Sign in again to save your answers.',
        `${accountTo('save your answers')} What you typed is still here.`,
      );
      set({ recheck: { status: 'error', message } });
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
      const error = refused(statusOf(err), messageOf(err), 'Sign in to build your resume.', accountTo('build a resume'));
      setResume({ status: state.resume.variant ? 'ready' : 'error', error });
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
        const error = refused(statusOf(err), messageOf(err), 'Sign in again to save your decision.', accountTo('save your decision'));
        if (state.resume.matchId === id) setResume({ error });
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
    setResume({ downloading: undefined, error: refused(res.status, res.message, 'Sign in again to download.', accountTo('download this')) });
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
      setCover({ status: 'error', error: refused(statusOf(err), messageOf(err), 'Sign in to write your cover note.', accountTo('write a cover note')) });
    }
  },

  async downloadCover(id: string): Promise<void> {
    if (state.cover.downloading) return;
    setCover({ downloading: true, error: undefined });
    const res = await api.downloadFile(api.coverFileUrl(id), 'cover-note.pdf');
    const error = res.ok ? undefined : refused(res.status, res.message, 'Sign in again to download.', accountTo('download this'));
    setCover({ downloading: false, ...(res.ok ? {} : { status: state.cover.note ? 'ready' : 'error', error }) });
  },

  /**
   * The paste screen's side list. A signed-in person gets their account's matches. Nobody signed in gets what this
   * device remembers (`recent.ts`), replaced by the server's list if it has one; the server's list is empty for a
   * visitor, so this is usually the local one. `userId` is who is asking (absent: nobody), so a sign-in or sign-out
   * reads the list again instead of showing the other person's.
   */
  async loadRecent(userId?: string): Promise<void> {
    const key = userId ?? '';
    if (state.recent !== null && recentFor === key) return;
    recentFor = key;
    set({ recent: userId ? [] : readRecent().slice(0, RECENT_SHOWN) });
    try {
      const server = await api.listMatches();
      if (recentFor !== key) return; // someone else asked meanwhile
      if (server.length > 0) set({ recent: server.slice(0, RECENT_SHOWN) });
    } catch {
      /* not signed in, or the list is unavailable: the side list stays as it is */
    }
  },
};
