/**
 * The data behind `/resume` for one profile: the list of resumes, the one in view, the blocks, and the actions that
 * change them. Every write answers with the whole resume as it now stands, and that answer replaces what is on screen:
 * the page never guesses what an operation did.
 *
 * One thing runs at a time (`busy`); a second action while one is in flight is refused, so answers can never arrive
 * out of order. A change of profile drops everything, and an answer for the profile that was left is ignored.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../lib/api';
import * as api from './api';
import type { BlocksResponse, Busy, Failure, FileKind, FinaliseReport, FinaliseRequest, LoadStatus, ResumeDetail, ResumeList, ResumeOp, ResumeSummary } from './types';
import { fileNameOf, pickResumeId } from './model';

export interface FinaliseState {
  /** The resume the report is about. */
  id?: string;
  status: 'idle' | 'running' | 'ready' | 'error';
  report?: FinaliseReport;
  error?: string;
  /** The person asked for "make default" with it. */
  makeDefault: boolean;
}

interface State {
  list: ResumeList | null;
  listStatus: LoadStatus;
  listError?: Failure;
  detail: ResumeDetail | null;
  detailStatus: LoadStatus;
  detailError?: Failure;
  blocks: BlocksResponse | null;
  blocksStatus: LoadStatus;
  blocksError?: Failure;
  busy: Busy | null;
  /** A transient sentence about the last action that failed. */
  notice?: string;
  finalise: FinaliseState;
}

const NO_FINALISE: FinaliseState = { status: 'idle', makeDefault: false };

const EMPTY: State = {
  list: null,
  listStatus: 'idle',
  detail: null,
  detailStatus: 'idle',
  blocks: null,
  blocksStatus: 'idle',
  busy: null,
  finalise: NO_FINALISE,
};

const failureOf = (err: unknown): Failure => ({
  message: err instanceof Error ? err.message : String(err),
  status: err instanceof ApiError ? err.status : 0,
});

/** The list with one resume's summary brought up to date: its row, its path slot, and the default mark moved to it. */
function mergeSummary(list: ResumeList, summary: ResumeSummary): ResumeList {
  const known = list.resumes.some((r) => r.id === summary.id);
  const resumes = (known ? list.resumes.map((r) => (r.id === summary.id ? summary : r)) : [...list.resumes, summary]).map((r) =>
    summary.isDefault && r.id !== summary.id && r.isDefault ? { ...r, isDefault: false } : r,
  );
  const paths = summary.kind === 'path' && summary.roleId ? list.paths.map((p) => (p.roleId === summary.roleId ? { ...p, resumeId: summary.id } : p)) : list.paths;
  return { ...list, resumes, paths };
}

/** What a fresh answer for a resume changes: the resume in view, and its row in the list. */
const withDetail = (s: State, detail: ResumeDetail): Partial<State> => ({
  detail,
  detailStatus: 'ready',
  detailError: undefined,
  list: s.list ? mergeSummary(s.list, detail.resume) : s.list,
});

/** One list request per profile at a time, shared: React's double effect in development never asks twice. */
const listing = new Map<string, Promise<ResumeList>>();
function listOnce(profileId: string): Promise<ResumeList> {
  const running = listing.get(profileId);
  if (running) return running;
  const next = api.listResumes(profileId).finally(() => listing.delete(profileId));
  listing.set(profileId, next);
  return next;
}

export interface Resumes extends State {
  /** The resume the page shows: the one asked for, else the default. */
  viewId: string | undefined;
  reload: () => void;
  reloadDetail: () => void;
  /** Make (or open) the resume for a next role; resolves with its id. */
  makePath: (roleId: string) => Promise<string | null>;
  /** Send one operation; resolves with the resume as it now stands, or null when it was refused. */
  op: (op: ResumeOp) => Promise<ResumeDetail | null>;
  runFinalise: (id: string, req: FinaliseRequest) => Promise<void>;
  makeDefault: (id: string) => Promise<void>;
  download: (id: string, file: FileKind) => Promise<void>;
  ensureBlocks: (force?: boolean) => void;
  clearNotice: () => void;
}

/**
 * `profileId` undefined means "nothing to load" (the sample profile, or nobody who may ask). `wantedId` is the resume
 * the address names.
 */
export function useResumes(profileId: string | undefined, wantedId: string | undefined): Resumes {
  const [s, setS] = useState<State>(EMPTY);
  const [attempt, setAttempt] = useState(0);
  const [detailAttempt, setDetailAttempt] = useState(0);
  const latest = useRef(s);
  latest.current = s;
  /** Bumped when the profile changes: an answer that belongs to an older profile is dropped. */
  const gen = useRef(0);
  /** The same flag as `s.busy`, readable before the next render. */
  const working = useRef(false);
  const blocksAsked = useRef(false);
  /** The id of a resume `makePath` just put on screen, until the address names it. */
  const made = useRef<string | null>(null);

  const patch = useCallback((p: Partial<State> | ((prev: State) => Partial<State>)) => {
    setS((prev) => ({ ...prev, ...(typeof p === 'function' ? p(prev) : p) }));
  }, []);

  /* the list, once per profile (and again on "Try again") */
  useEffect(() => {
    const mine = ++gen.current;
    working.current = false;
    blocksAsked.current = false;
    made.current = null;
    setS({ ...EMPTY, listStatus: profileId ? 'loading' : 'idle' });
    if (!profileId) return;
    listOnce(profileId)
      .then((list) => {
        if (mine === gen.current) patch({ list, listStatus: 'ready', listError: undefined });
      })
      .catch((err: unknown) => {
        if (mine === gen.current) patch({ listStatus: 'error', listError: failureOf(err) });
      });
  }, [profileId, attempt, patch]);

  /* the resume in view */
  const viewId = wantedId ?? pickResumeId(s.list);
  const listReady = s.listStatus === 'ready';
  const shownId = s.detail?.resume.id;
  useEffect(() => {
    // a resume that was just made is already on screen; the address is about to follow it, so the old one is not fetched again
    if (made.current) {
      if (made.current !== viewId) return;
      made.current = null;
    }
    if (!profileId || !listReady || !viewId || shownId === viewId) return;
    const mine = gen.current;
    const ctrl = new AbortController();
    patch({ detailStatus: 'loading', detailError: undefined, notice: undefined });
    api
      .getResume(viewId, ctrl.signal)
      .then((detail) => {
        if (mine === gen.current && !ctrl.signal.aborted) patch((prev) => withDetail(prev, detail));
      })
      .catch((err: unknown) => {
        if (mine === gen.current && !ctrl.signal.aborted) patch({ detailStatus: 'error', detailError: failureOf(err) });
      });
    return () => ctrl.abort();
  }, [profileId, listReady, viewId, shownId, detailAttempt, patch]);

  /** Run one action at a time; `false` when another is in flight. */
  const begin = useCallback(
    (busy: Busy): number | null => {
      if (working.current) return null;
      working.current = true;
      patch({ busy, notice: undefined });
      return gen.current;
    },
    [patch],
  );
  const end = useCallback(
    (mine: number, p: Partial<State> | ((prev: State) => Partial<State>) = {}) => {
      if (mine !== gen.current) return;
      working.current = false;
      patch((prev) => ({ ...(typeof p === 'function' ? p(prev) : p), busy: null }));
    },
    [patch],
  );

  const makePath = useCallback(
    async (roleId: string): Promise<string | null> => {
      if (!profileId) return null;
      const mine = begin({ kind: 'make', roleId });
      if (mine === null) return null;
      try {
        const detail = await api.makePath(profileId, roleId);
        if (mine === gen.current) made.current = detail.resume.id;
        end(mine, (prev) => ({ ...withDetail(prev, detail), blocks: null, blocksStatus: 'idle' }));
        blocksAsked.current = false;
        return mine === gen.current ? detail.resume.id : null;
      } catch (err) {
        end(mine, { notice: failureOf(err).message });
        return null;
      }
    },
    [profileId, begin, end],
  );

  const op = useCallback(
    async (operation: ResumeOp): Promise<ResumeDetail | null> => {
      const id = latest.current.detail?.resume.id;
      if (!id) return null;
      const mine = begin({ kind: 'op', op: operation.op, target: operation.bulletId ?? operation.factId ?? '' });
      if (mine === null) return null;
      try {
        const detail = await api.patchResume(id, [operation]);
        // the draft changed: an earlier report is about another text, and "in N of M resumes" may have moved
        end(mine, (prev) => ({ ...withDetail(prev, detail), finalise: NO_FINALISE, blocks: null, blocksStatus: 'idle' }));
        blocksAsked.current = false;
        return mine === gen.current ? detail : null;
      } catch (err) {
        end(mine, { notice: failureOf(err).message });
        return null;
      }
    },
    [begin, end],
  );

  const runFinalise = useCallback(
    async (id: string, req: FinaliseRequest): Promise<void> => {
      const mine = begin({ kind: 'finalise' });
      if (mine === null) return;
      const makeDefault = req.makeDefault === true;
      patch((prev) => ({ finalise: { id, status: 'running', makeDefault, report: prev.finalise.id === id ? prev.finalise.report : undefined } }));
      try {
        const res = await api.finalise(id, req);
        end(mine, (prev) => ({
          ...withDetail(prev, res.detail),
          finalise: { id, status: 'ready', report: res.report, makeDefault },
          blocks: null,
          blocksStatus: 'idle',
        }));
        blocksAsked.current = false;
        // a line kept as the person's own words is a new block: the count on the page comes from the list
        if (res.report.done && profileId && mine === gen.current) {
          void listOnce(profileId)
            .then((list) => {
              if (mine === gen.current) patch({ list });
            })
            .catch(() => undefined);
        }
      } catch (err) {
        end(mine, (prev) => ({ finalise: { ...prev.finalise, id, status: 'error', error: failureOf(err).message, makeDefault } }));
      }
    },
    [profileId, begin, end, patch],
  );

  const makeDefault = useCallback(
    async (id: string): Promise<void> => {
      const mine = begin({ kind: 'default' });
      if (mine === null) return;
      try {
        const detail = await api.makeDefault(id);
        end(mine, (prev) => withDetail(prev, detail));
      } catch (err) {
        end(mine, { notice: failureOf(err).message });
      }
    },
    [begin, end],
  );

  const download = useCallback(
    async (id: string, file: FileKind): Promise<void> => {
      const resume = latest.current.detail?.resume;
      const mine = begin({ kind: 'download', file });
      if (mine === null) return;
      const result = await api.downloadResume(id, file, resume && resume.id === id ? fileNameOf(resume, file) : `resume.${file}`);
      end(mine, result.ok ? {} : { notice: result.message });
    },
    [begin, end],
  );

  const ensureBlocks = useCallback(
    (force = false) => {
      if (!profileId || (blocksAsked.current && !force)) return;
      blocksAsked.current = true;
      const mine = gen.current;
      patch({ blocksStatus: 'loading', blocksError: undefined });
      api
        .getBlocks(profileId)
        .then((blocks) => {
          if (mine === gen.current) patch({ blocks, blocksStatus: 'ready' });
        })
        .catch((err: unknown) => {
          if (mine === gen.current) patch({ blocksStatus: 'error', blocksError: failureOf(err) });
        });
    },
    [profileId, patch],
  );

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  const reloadDetail = useCallback(() => setDetailAttempt((n) => n + 1), []);
  const clearNotice = useCallback(() => patch({ notice: undefined }), [patch]);

  return { ...s, viewId, reload, reloadDetail, makePath, op, runFinalise, makeDefault, download, ensureBlocks, clearNotice };
}
