/**
 * A simple word diff for a proposed wording: the words that go are `<del>`, the words that come are `<ins>`. Straight
 * and measured (a longest-common-subsequence over words), styled by tokens in resume.css. Every word is a text node.
 */
import type { ReactNode } from 'react';

export interface Seg {
  text: string;
  changed: boolean;
}

const words = (s: string) => s.split(/\s+/).filter(Boolean);

/** The two sides of a diff: `before` with what goes marked, `after` with what is new marked. */
export function diffWords(before: string, after: string): { before: Seg[]; after: Seg[] } {
  const a = words(before);
  const b = words(after);
  // lengths of the common tail from i, j
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const left: Seg[] = [];
  const right: Seg[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      left.push({ text: a[i], changed: false });
      right.push({ text: b[j], changed: false });
      i++;
      j++;
    } else if (j < b.length && (i === a.length || lcs[i][j + 1] >= lcs[i + 1][j])) {
      right.push({ text: b[j++], changed: true });
    } else {
      left.push({ text: a[i++], changed: true });
    }
  }
  return { before: left, after: right };
}

function render(segs: Seg[], tag: 'del' | 'ins'): ReactNode[] {
  const out: ReactNode[] = [];
  let run: string[] = [];
  const flush = () => {
    if (run.length === 0) return;
    out.push(tag === 'del' ? <del key={out.length}>{run.join(' ')}</del> : <ins key={out.length}>{run.join(' ')}</ins>);
    run = [];
  };
  segs.forEach((s) => {
    if (s.changed) run.push(s.text);
    else {
      flush();
      out.push(s.text);
    }
  });
  flush();
  // plain words and marked runs are separated by a space
  return out.flatMap((n, k) => (k === 0 ? [n] : [' ', n]));
}

/** The line as it was, what goes struck. */
export function Was({ before, after }: { before: string; after: string }) {
  return <>{render(diffWords(before, after).before, 'del')}</>;
}

/** The line as proposed, what is new marked. */
export function Now({ before, after }: { before: string; after: string }) {
  return <>{render(diffWords(before, after).after, 'ins')}</>;
}
