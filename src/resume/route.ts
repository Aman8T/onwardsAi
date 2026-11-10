/**
 * The resume page's address. The screen lives in the query string, as Practice's does, so a view can be linked to,
 * reloaded and stepped back through:
 *
 *   /resume                          the resumes, with the default one in view
 *   /resume?r=<id>                   one resume in view
 *   /resume?r=<id>&screen=finalise   Finalise for that resume
 *   /resume?screen=blocks            your blocks (`r` is kept, so "Your resumes" returns to the same one)
 *
 * Finalise needs a resume; without one it is the resumes screen.
 *
 * Pure functions only (the store imports this file), so nothing here may touch the store or the DOM.
 */
export const RESUME_PATH = '/resume';

export type ResumeScreen = 'resume' | 'finalise' | 'blocks';

export interface ResumeQuery {
  screen: ResumeScreen;
  /** The resume in view; the default one when absent. */
  id?: string;
}

export const DEFAULT_RESUME: ResumeQuery = { screen: 'resume' };

const ID = /^[A-Za-z0-9_-]{1,64}$/;
const idOf = (value: string | null | undefined): string | undefined => (value !== null && value !== undefined && ID.test(value) ? value : undefined);

export function isResumePath(pathname: string): boolean {
  return pathname.replace(/\/+$/, '') === RESUME_PATH;
}

/** Make the query consistent: Finalise without a resume is the resumes screen. */
function settle(query: ResumeQuery): ResumeQuery {
  const id = idOf(query.id);
  const screen: ResumeScreen = query.screen === 'blocks' ? 'blocks' : query.screen === 'finalise' && id ? 'finalise' : 'resume';
  return id ? { screen, id } : { screen };
}

/** The query an address names, or null when it is not the resume address. */
export function parseResumeUrl(pathname: string, search: string): ResumeQuery | null {
  if (!isResumePath(pathname)) return null;
  const p = new URLSearchParams(search);
  const screen = p.get('screen');
  return settle({ screen: screen === 'finalise' || screen === 'blocks' ? screen : 'resume', id: idOf(p.get('r')) });
}

/** `{ ...query, ...patch }`, settled; a patch clears the resume by setting `id` to undefined. */
export function mergeResume(query: ResumeQuery, patch: Partial<ResumeQuery>): ResumeQuery {
  return settle({ screen: patch.screen ?? query.screen, id: 'id' in patch ? patch.id : query.id });
}

/** The pathname and search of a query; the resumes screen with the default in view is the bare path. */
export function resumePath(query: ResumeQuery): string {
  const q = settle(query);
  const p = new URLSearchParams();
  if (q.id) p.set('r', q.id);
  if (q.screen !== 'resume') p.set('screen', q.screen);
  const search = p.toString();
  return search ? `${RESUME_PATH}?${search}` : RESUME_PATH;
}
