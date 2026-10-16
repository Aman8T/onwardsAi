/**
 * Small pure helpers every Discover screen shares: link safety, the apply button's words, and how long ago something
 * was checked. Nothing here touches the DOM or the store.
 *
 * Rules kept here (project-soul.md): a link from a source we do not control renders only when it is a plain web
 * address, and the apply button names where it goes.
 */

import type { DiscoverWorkMode } from '../../shared/discover';

/** A link from a source we do not control renders only when it is a plain web address. */
export function safeHref(url: string): string | undefined {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' || u.protocol === 'http:' ? url : undefined;
  } catch {
    return undefined;
  }
}

export function hostOf(url: string): string | undefined {
  try {
    return new URL(url).hostname.replace(/^www\./, '') || undefined;
  } catch {
    return undefined;
  }
}

/** How a role is worked, in a word ("hybrid"); nothing when the posting does not say. */
export const MODE_LABEL: Record<DiscoverWorkMode, string> = {
  onsite: 'on-site',
  hybrid: 'hybrid',
  remote: 'remote',
  unknown: '',
};

/** "AWS", "AWS and GraphQL", "AWS, GraphQL and Angular". */
export const joinNames = (names: readonly string[]) => (names.length <= 1 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`);

/** The button's words: it names where it goes. */
export const applyLabel = (job: { applyHost: string }) => `Apply on ${job.applyHost} ↗`;

const dayStart = (t: number) => {
  const d = new Date(t);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
};

/** "today", "yesterday", "3 days ago": how long ago the employer's board last listed the role. */
export function checkedWord(iso: string, now: number = Date.now()): string {
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return 'recently';
  const days = Math.max(0, Math.round((dayStart(now) - dayStart(at)) / 86_400_000));
  return days === 0 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`;
}
