/**
 * Small parts the prepare page is built from. Every string that came from a posting, a profile or a model is a text
 * node (never markup), a quote is always in curly quotes with its source beside it, and anything that is ours rather
 * than quoted wears the dashed "Suggestion" chip.
 */
import type { ReactNode } from 'react';
import type { Cite } from '../../../shared/discover';
import '../../styles/discover-more.css';

/** A quoted line of the posting or the profile, in curly quotes. */
export const quoted = (s: string) => `“${s}”`;

/** "Suggestion" marks what is ours: advice, never a claim about the person. */
export function Suggest({ children }: { children: ReactNode }) {
  return (
    <div className="dm-suggest">
      <span className="chip dm-chip-suggest">Suggestion</span>
      <span className="dm-suggest-text">{children}</span>
    </div>
  );
}

/** A line of the person's evidence with its source, e.g. “Checkout LCP 4.1s to 1.9s at Kitebird” · Resume. */
export function Evidence({ cite }: { cite: Cite }) {
  return (
    <div className="dm-evidence">
      <p className="dm-fact">{quoted(cite.text)}</p>
      {cite.source && <p className="dm-cite">{cite.source}</p>}
    </div>
  );
}
