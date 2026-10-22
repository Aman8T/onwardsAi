/**
 * "Practised" on the graph (the I1 follow-up, board 6 of the "Practice the interview" canvas): what a person backed up in
 * practice, shown on the skill whose evidence line was asked about. It records a verdict; it never adds a skill or changes
 * a score.
 *
 * The claims come from `GET /api/interview/sessions` (each evidence line asked about, at its best). A skill's evidence
 * lines are matched to them by their folded text, the way the server keys a claim (NFKC, lower case, one kind of quote
 * mark and dash, single spaces). The sample profile is shared, so its practice is never read.
 */
import { useEffect, useState } from 'react';
import type { ClaimBest, Verdict } from '../../shared/interview';
import { isSampleId } from '../match/model';
import { fetchHistory } from './api';

/** The server's `fold`: NFKC, lower case, straight quotes, a plain dash, single spaces. */
export function foldClaim(text: string): string {
  return text
    .normalize('NFKC')
    .replace(/[‘’‚‛′]/g, "'")
    .replace(/[“”„‟″]/g, '"')
    .replace(/[‐‑‒–—―−]/g, '-')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
}

const STRENGTH: Record<Verdict, number> = { defended: 3, shaky: 2, couldnt: 1, not_assessed: 0 };

export interface Practised {
  best: Verdict;
  /** The day the best verdict was reached (ISO). */
  on: string;
}

/** The claims that were judged, by folded text (a claim only ever Not assessed was not practised). */
export function practisedIndex(claims: readonly ClaimBest[]): Map<string, Practised> {
  const out = new Map<string, Practised>();
  for (const c of claims) {
    if (c.best === 'not_assessed') continue;
    out.set(foldClaim(c.text), { best: c.best, on: c.on });
  }
  return out;
}

/** The best of a skill's practised lines; undefined when none of them was practised. */
export function practisedFor(lines: readonly string[], index: ReadonlyMap<string, Practised>): Practised | undefined {
  let best: Practised | undefined;
  for (const line of lines) {
    const p = index.get(foldClaim(line));
    if (p && (!best || STRENGTH[p.best] > STRENGTH[best.best])) best = p;
  }
  return best;
}

/** The person's practised claims, read once per profile; empty for the sample, while loading, and when the read fails. */
export function usePractised(profileId: string): ReadonlyMap<string, Practised> {
  const [index, setIndex] = useState<ReadonlyMap<string, Practised>>(() => new Map());
  useEffect(() => {
    if (isSampleId(profileId)) return;
    const ctl = new AbortController();
    fetchHistory(profileId, ctl.signal).then(
      (h) => {
        if (!ctl.signal.aborted) setIndex(practisedIndex(h.claims));
      },
      () => undefined,
    );
    return () => ctl.abort();
  }, [profileId]);
  return index;
}
